#!/usr/bin/env python3
"""
Import Italian cadastral GML/GPKG files into SpatiaLite databases.

Creates TWO separate databases:
- *_map.sqlite: Contains MAP files (fogli - sheet boundaries)
- *_ple.sqlite: Contains PLE files (particelle - individual parcels)

Recursively processes the cadastral directory structure:
ITALIA/REGIONE/PROVINCIA/COMUNE/*.gml

The script prefers GPKG files from _converted directory if available,
falling back to GML files if not.

Usage:
    python scripts/import_cadastral_to_db.py /path/to/ITALIA [--db-prefix data/cadastral]

Example:
    python scripts/import_cadastral_to_db.py /mnt/mobile/data/catasto/original/ITALIA
    # Creates: data/cadastral_map.sqlite and data/cadastral_ple.<region>.sqlite per region

    # Use converted GPKG files (faster, more reliable)
    python scripts/import_cadastral_to_db.py /mnt/mobile/data/catasto/original/ITALIA --prefer-gpkg

    # Force recreation of existing databases
    python scripts/import_cadastral_to_db.py /mnt/mobile/data/catasto/original/ITALIA --force
"""

import os
import sys
import argparse
import logging
import signal
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from tqdm import tqdm

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from land_registry.cadastral_db import CadastralDatabase

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

try:
    import geopandas as gpd
    # Use pyogrio as the I/O engine (faster and more stable than fiona)
    gpd.options.io_engine = "pyogrio"
    HAS_GEOPANDAS = True
except ImportError:
    HAS_GEOPANDAS = False
    logger.error("geopandas is required. Install with: pip install geopandas")


def validate_file_in_subprocess(file_path: Path, timeout: int = 30) -> tuple[bool, str]:
    """
    Validate a geospatial file by attempting to read it in a subprocess.
    This protects against segfaults crashing the main process.

    Args:
        file_path: Path to the file to validate
        timeout: Maximum seconds to wait for validation

    Returns:
        Tuple of (is_valid, error_message)
    """
    # Use raw string to avoid f-string evaluation issues
    validation_script = '''
import sys
try:
    import geopandas as gpd
    gpd.options.io_engine = "pyogrio"
    gdf = gpd.read_file("{file_path}")
    print("OK:" + str(len(gdf)))
    sys.exit(0)
except Exception as e:
    print("ERROR:" + str(e))
    sys.exit(1)
'''.format(file_path=str(file_path))

    # Inherit current environment to ensure same Python packages are available
    env = os.environ.copy()

    try:
        result = subprocess.run(
            [sys.executable, "-c", validation_script],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env
        )

        if result.returncode == 0 and result.stdout.startswith("OK:"):
            return True, ""
        elif result.returncode == -signal.SIGSEGV:
            return False, "Segmentation fault (corrupted file)"
        elif result.returncode == -signal.SIGABRT:
            return False, "Aborted (corrupted file)"
        else:
            error = result.stdout.strip() or result.stderr.strip()
            return False, error

    except subprocess.TimeoutExpired:
        return False, f"Timeout after {timeout}s (file too large or corrupted)"
    except Exception as e:
        return False, str(e)


def safe_read_file(file_path: Path, validate_first: bool = True) -> tuple["gpd.GeoDataFrame | None", str]:
    """
    Safely read a geospatial file, optionally validating in subprocess first.

    Args:
        file_path: Path to the file
        validate_first: If True, validate in subprocess before reading

    Returns:
        Tuple of (GeoDataFrame or None, error_message)
    """
    if validate_first:
        is_valid, error = validate_file_in_subprocess(file_path)
        if not is_valid:
            return None, error

    # Now read in main process (safe since subprocess validation passed)
    try:
        gdf = gpd.read_file(str(file_path))
        return gdf, ""
    except Exception as e:
        return None, str(e)


class CadastralImporter:
    """Import cadastral files into separate SpatiaLite databases for MAP and PLE sequentially."""

    def __init__(self, db_prefix: Path, workers: int = 4, prefer_gpkg: bool = False, converted_dir: Path = None, force: bool = False, safe_mode: bool = True):
        """
        Initialize importer with two separate databases.

        Args:
            db_prefix: Path prefix for databases (e.g., 'data/cadastral' creates 'data/cadastral_map.sqlite' and 'data/cadastral_ple.<region>.sqlite')
            workers: Number of parallel workers for reading files
            prefer_gpkg: If True, prefer GPKG files from converted directory
            converted_dir: Path to converted files directory (default: source_dir + "_converted")
            force: If True, recreate databases even if they already exist
            safe_mode: If True, validate files in subprocess before reading (prevents segfaults)
        """
        # Store paths - databases will be created sequentially
        db_prefix = Path(db_prefix)
        self.db_prefix = db_prefix
        self.db_map_path = db_prefix.parent / f"{db_prefix.stem}_map.sqlite"
        # PLE databases will be created per region: <prefix>_ple.<REGIONE>.sqlite
        self.db_ple_dir = db_prefix.parent

        # Don't create databases yet - they will be created sequentially during import
        self.db_map = None
        self.db_ple_by_region: dict[str, CadastralDatabase] = {}

        self.workers = workers
        self.prefer_gpkg = prefer_gpkg
        self.converted_dir = converted_dir
        self.force = force
        self.safe_mode = safe_mode
        self.stats = {
            "files_found": 0,
            "map_files_found": 0,
            "ple_files_found": 0,
            "files_imported": 0,
            "parcels_imported": 0,
            "map_files_imported": 0,
            "ple_files_imported": 0,
            "map_parcels_imported": 0,
            "ple_parcels_imported": 0,
            "errors": 0,
            "skipped_corrupted": 0,
        }

    def _get_best_file(self, file_path: Path, source_dir: Path) -> Path:
        """
        Get the best available file to import (prefer GPKG if available).

        Args:
            file_path: Original file path (GML)
            source_dir: Root source directory

        Returns:
            Best available file path (GPKG from converted dir if available, else original)
        """
        if not self.prefer_gpkg:
            return file_path

        # Calculate converted path
        if self.converted_dir:
            converted_base = self.converted_dir
        else:
            converted_base = Path(str(source_dir) + "_converted")

        if not converted_base.exists():
            return file_path

        # Get relative path and construct GPKG path
        try:
            rel_path = file_path.relative_to(source_dir)
            gpkg_path = converted_base / rel_path.with_suffix('.gpkg')

            if gpkg_path.exists():
                return gpkg_path
        except ValueError:
            pass

        return file_path

    def import_file(
        self,
        file_path: Path,
        regione: str,
        provincia: str,
        comune_code: str,
        comune_name: str,
        db: CadastralDatabase,
        layer_type: str,
        source_dir: Path = None
    ) -> int:
        """
        Import a single GML/GPKG file to the specified database.

        Args:
            file_path: Path to the file
            regione: Region name
            provincia: Province code
            comune_code: Municipality code
            comune_name: Municipality name
            db: Database to import into
            layer_type: 'map' or 'ple'
            source_dir: Root source directory (for finding converted files)

        Returns:
            Number of parcels imported
        """
        try:
            # Get best available file (prefer GPKG if available)
            actual_file = self._get_best_file(file_path, source_dir) if source_dir else file_path

            # Use safe mode to validate files in subprocess first (prevents segfaults)
            if self.safe_mode:
                gdf, error = safe_read_file(actual_file, validate_first=True)
                if gdf is None:
                    # If primary file failed, try alternate
                    if actual_file != file_path:
                        logger.warning(f"GPKG failed ({error}), trying GML: {file_path}")
                        gdf, error2 = safe_read_file(file_path, validate_first=True)
                        if gdf is None:
                            logger.error(f"Both files failed. GPKG: {error}, GML: {error2}")
                            self.stats["skipped_corrupted"] += 1
                            return 0
                    elif source_dir and self.prefer_gpkg:
                        gpkg_path = self._get_best_file(file_path, source_dir)
                        if gpkg_path != file_path and gpkg_path.exists():
                            logger.warning(f"GML failed ({error}), trying GPKG: {gpkg_path}")
                            gdf, error2 = safe_read_file(gpkg_path, validate_first=True)
                            if gdf is None:
                                logger.error(f"Both files failed. GML: {error}, GPKG: {error2}")
                                self.stats["skipped_corrupted"] += 1
                                return 0
                        else:
                            logger.error(f"File failed: {error}")
                            self.stats["skipped_corrupted"] += 1
                            return 0
                    else:
                        logger.error(f"File failed: {error}")
                        self.stats["skipped_corrupted"] += 1
                        return 0
            else:
                # Original direct reading (may segfault on corrupted files)
                try:
                    gdf = gpd.read_file(actual_file)
                except OSError as e:
                    if "Input/output error" in str(e) or "not recognized" in str(e):
                        # File is corrupted, try alternate source
                        if actual_file != file_path:
                            logger.warning(f"GPKG corrupted, trying GML: {file_path}")
                            gdf = gpd.read_file(file_path)
                        elif source_dir and self.prefer_gpkg:
                            gpkg_path = self._get_best_file(file_path, source_dir)
                            if gpkg_path != file_path and gpkg_path.exists():
                                logger.warning(f"GML corrupted, trying GPKG: {gpkg_path}")
                                gdf = gpd.read_file(gpkg_path)
                            else:
                                raise
                        else:
                            raise
                    else:
                        raise

            if gdf.empty:
                logger.warning(f"Empty file: {actual_file}")
                return 0

            # Import to database
            count = db.import_geopandas(
                gdf=gdf,
                regione=regione,
                provincia=provincia,
                comune_code=comune_code,
                comune_name=comune_name,
                layer_type=layer_type,
                source_file=str(actual_file)
            )

            return count

        except OSError as e:
            if "Input/output error" in str(e):
                logger.error(f"Corrupted file (I/O error), skipping: {file_path}")
                self.stats["skipped_corrupted"] += 1
            else:
                logger.error(f"Error importing {file_path}: {e}")
                self.stats["errors"] += 1
            return 0
        except Exception as e:
            error_msg = str(e)
            if "not recognized as being in a supported file format" in error_msg:
                logger.error(f"Unreadable file format, skipping: {file_path}")
                self.stats["skipped_corrupted"] += 1
            else:
                logger.error(f"Error importing {file_path}: {e}")
                self.stats["errors"] += 1
            return 0

    @staticmethod
    def _get_layer_type(file_path: Path) -> str | None:
        """Determine layer type from filename."""
        filename = file_path.stem.lower()
        if '_map' in filename or filename.endswith('_map'):
            return 'map'
        elif '_ple' in filename or filename.endswith('_ple'):
            return 'ple'
        return None

    def _get_ple_db_path(self, regione: str) -> Path:
        """Get the PLE database path for a specific region."""
        # Normalize region name for filename (lowercase, replace spaces/special chars)
        region_slug = regione.lower().replace(' ', '_').replace('-', '_')
        return self.db_ple_dir / f"{self.db_prefix.stem}_ple.{region_slug}.sqlite"

    def _get_or_create_ple_db(self, regione: str) -> CadastralDatabase:
        """Get or create a PLE database for a specific region."""
        if regione not in self.db_ple_by_region:
            db_path = self._get_ple_db_path(regione)
            self.db_ple_by_region[regione] = CadastralDatabase(db_path)
        return self.db_ple_by_region[regione]

    def _parse_file_info(self, file_path: Path, source_dir: Path) -> dict | None:
        """Parse file path to extract region, province, comune info."""
        parts = file_path.relative_to(source_dir).parts

        if len(parts) < 3:
            logger.warning(f"Unexpected path structure: {file_path}")
            return None

        regione = parts[0]
        provincia = parts[1]
        comune_folder = parts[2]

        # Extract comune code from folder name (e.g., "I056_SAN NICOLA LA STRADA")
        if "_" in comune_folder:
            comune_code = comune_folder.split("_")[0]
            comune_name = "_".join(comune_folder.split("_")[1:])
        else:
            comune_code = comune_folder
            comune_name = comune_folder

        return {
            "regione": regione,
            "provincia": provincia,
            "comune_code": comune_code,
            "comune_name": comune_name,
        }

    def import_directory(self, source_dir: Path) -> dict:
        """
        Import all cadastral files from directory structure SEQUENTIALLY.

        First imports all MAP files (fogli), then all PLE files (particelle).
        This ensures each database is fully created before starting the next.

        Expected structure:
        ITALIA/
            REGIONE/
                PROVINCIA/
                    COMUNE_NAME/
                        CODICE_*.gml

        Args:
            source_dir: Root directory (ITALIA level)

        Returns:
            Statistics dictionary
        """
        source_dir = Path(source_dir)

        if not source_dir.exists():
            logger.error(f"Source directory does not exist: {source_dir}")
            return {"error": "Source directory not found"}

        # Find all GML and GPKG files
        logger.info("Scanning for cadastral files...")
        gml_files = list(source_dir.rglob("*.gml")) + list(source_dir.rglob("*.GML"))
        gpkg_files = list(source_dir.rglob("*.gpkg")) + list(source_dir.rglob("*.GPKG"))

        # Prefer GPKG files - skip GML if corresponding GPKG exists
        gpkg_stems = {f.parent / f.stem for f in gpkg_files}
        filtered_gml = [f for f in gml_files if (f.parent / f.stem) not in gpkg_stems]

        all_files = gpkg_files + filtered_gml

        # Separate files by type
        map_files = [f for f in all_files if self._get_layer_type(f) == 'map']
        ple_files = [f for f in all_files if self._get_layer_type(f) == 'ple']
        unknown_files = [f for f in all_files if self._get_layer_type(f) is None]

        self.stats["files_found"] = len(all_files)
        self.stats["map_files_found"] = len(map_files)
        self.stats["ple_files_found"] = len(ple_files)

        logger.info(f"Found {len(all_files)} cadastral files:")
        logger.info(f"  MAP files (fogli):     {len(map_files)}")
        logger.info(f"  PLE files (particelle): {len(ple_files)}")
        if unknown_files:
            logger.warning(f"  Unknown type (skipped): {len(unknown_files)}")

        if not all_files:
            return self.stats

        # ============================================================
        # PHASE 1: Import MAP files (fogli)
        # ============================================================
        if map_files:
            print("\n" + "=" * 60)
            print("PHASE 1: Importing MAP files (fogli)")
            print("=" * 60)

            # Check if MAP database already exists
            if self.db_map_path.exists() and not self.force:
                print(f"MAP database already exists: {self.db_map_path}")
                print("Skipping MAP import (use --force to recreate)")
                map_files = []  # Skip MAP processing
            else:
                if self.db_map_path.exists() and self.force:
                    print(f"Removing existing MAP database (--force): {self.db_map_path}")
                    self.db_map_path.unlink()
                print(f"Creating MAP database: {self.db_map_path}")
                self.db_map = CadastralDatabase(self.db_map_path)

            with tqdm(map_files, desc="MAP", unit="file", dynamic_ncols=True) as pbar:
                for file_path in pbar:
                    file_info = self._parse_file_info(file_path, source_dir)
                    if not file_info:
                        continue

                    count = self.import_file(
                        file_path=file_path,
                        regione=file_info["regione"],
                        provincia=file_info["provincia"],
                        comune_code=file_info["comune_code"],
                        comune_name=file_info["comune_name"],
                        db=self.db_map,
                        layer_type='map',
                        source_dir=source_dir
                    )

                    if count > 0:
                        self.stats["files_imported"] += 1
                        self.stats["parcels_imported"] += count
                        self.stats["map_files_imported"] += 1
                        self.stats["map_parcels_imported"] += count

                    # Update description with region, province, comune info and total records (fixed width)
                    region_display = file_info["regione"][:12].ljust(12)
                    provincia_display = file_info["provincia"][:2].ljust(2)
                    code_display = file_info["comune_code"].ljust(5)
                    comune_display = file_info["comune_name"][:24].ljust(24)
                    records_display = f"{self.stats['map_parcels_imported']:>12,}"
                    pbar.set_description(f"MAP | {region_display} {provincia_display} | {code_display} {comune_display} | {records_display} rec")

            # Show MAP phase summary
            print(f"\nMAP Phase Complete:")
            print(f"  Files imported: {self.stats['map_files_imported']:,}")
            print(f"  Records imported: {self.stats['map_parcels_imported']:,}")

        # ============================================================
        # PHASE 2: Import PLE files (particelle) - one database per region
        # ============================================================
        if ple_files:
            print("\n" + "=" * 60)
            print("PHASE 2: Importing PLE files (particelle) - per region")
            print("=" * 60)

            # Group PLE files by region
            ple_by_region: dict[str, list[Path]] = {}
            for file_path in ple_files:
                file_info = self._parse_file_info(file_path, source_dir)
                if file_info:
                    regione = file_info["regione"]
                    if regione not in ple_by_region:
                        ple_by_region[regione] = []
                    ple_by_region[regione].append(file_path)

            # Sort regions alphabetically
            sorted_regions = sorted(ple_by_region.keys())
            print(f"Found {len(sorted_regions)} regions to process")

            # Track per-region stats
            self.stats["ple_by_region"] = {}

            # Process each region sequentially
            for region_idx, regione in enumerate(sorted_regions, 1):
                region_files = ple_by_region[regione]
                db_path = self._get_ple_db_path(regione)

                print(f"\n--- Region {region_idx}/{len(sorted_regions)}: {regione} ({len(region_files)} files) ---")
                print(f"Database: {db_path}")

                # Check if PLE database for this region already exists
                if db_path.exists() and not self.force:
                    print(f"PLE database already exists: {db_path}")
                    print("Skipping region (use --force to recreate)")
                    continue

                if db_path.exists() and self.force:
                    print(f"Removing existing PLE database (--force): {db_path}")
                    db_path.unlink()

                # Get or create database for this region
                db_ple = self._get_or_create_ple_db(regione)
                region_records = 0

                with tqdm(region_files, desc=f"PLE-{regione[:10]}", unit="file", dynamic_ncols=True) as pbar:
                    for file_path in pbar:
                        file_info = self._parse_file_info(file_path, source_dir)
                        if not file_info:
                            continue

                        count = self.import_file(
                            file_path=file_path,
                            regione=file_info["regione"],
                            provincia=file_info["provincia"],
                            comune_code=file_info["comune_code"],
                            comune_name=file_info["comune_name"],
                            db=db_ple,
                            layer_type='ple',
                            source_dir=source_dir
                        )

                        if count > 0:
                            self.stats["files_imported"] += 1
                            self.stats["parcels_imported"] += count
                            self.stats["ple_files_imported"] += 1
                            self.stats["ple_parcels_imported"] += count
                            region_records += count

                        # Update description with region, province, comune info and total records (fixed width)
                        region_display = file_info["regione"][:12].ljust(12)
                        provincia_display = file_info["provincia"][:2].ljust(2)
                        code_display = file_info["comune_code"].ljust(5)
                        comune_display = file_info["comune_name"][:24].ljust(24)
                        records_display = f"{region_records:>12,}"
                        pbar.set_description(f"PLE | {region_display} {provincia_display} | {code_display} {comune_display} | {records_display} rec")

                # Store region stats
                self.stats["ple_by_region"][regione] = region_records
                print(f"  {regione}: {region_records:,} records imported")

            # Show PLE phase summary
            print(f"\nPLE Phase Complete:")
            print(f"  Files imported: {self.stats['ple_files_imported']:,}")
            print(f"  Records imported: {self.stats['ple_parcels_imported']:,}")
            print(f"  Databases created: {len(self.db_ple_by_region)}")

        return self.stats

    def import_single_comune(
        self,
        folder_path: Path,
        regione: str,
        provincia: str
    ) -> dict:
        """
        Import all files from a single comune folder sequentially.

        Args:
            folder_path: Path to comune folder
            regione: Region name
            provincia: Province code

        Returns:
            Statistics dictionary
        """
        folder_path = Path(folder_path)

        if not folder_path.exists():
            logger.error(f"Folder does not exist: {folder_path}")
            return {"error": "Folder not found"}

        # Extract comune info from folder name
        folder_name = folder_path.name
        if "_" in folder_name:
            comune_code = folder_name.split("_")[0]
            comune_name = "_".join(folder_name.split("_")[1:])
        else:
            comune_code = folder_name
            comune_name = folder_name

        # Find files - prefer GPKG over GML to avoid driver conflicts
        gpkg_files = list(folder_path.glob("*.gpkg"))
        gml_files = list(folder_path.glob("*.gml"))

        # Use GPKG if available, otherwise fall back to GML
        files = []
        gpkg_stems = {f.stem for f in gpkg_files}
        for gpkg in gpkg_files:
            files.append(gpkg)
        for gml in gml_files:
            if gml.stem not in gpkg_stems:  # Only add GML if no corresponding GPKG
                files.append(gml)

        map_files = [f for f in files if self._get_layer_type(f) == 'map']
        ple_files = [f for f in files if self._get_layer_type(f) == 'ple']

        # Import MAP files first
        if map_files:
            self.db_map = CadastralDatabase(self.db_map_path)
            for file_path in map_files:
                count = self.import_file(
                    file_path=file_path,
                    regione=regione,
                    provincia=provincia,
                    comune_code=comune_code,
                    comune_name=comune_name,
                    db=self.db_map,
                    layer_type='map'
                )
                if count > 0:
                    self.stats["files_imported"] += 1
                    self.stats["parcels_imported"] += count
                    self.stats["map_files_imported"] += 1
                    self.stats["map_parcels_imported"] += count

        # Import PLE files second (use per-region database)
        if ple_files:
            db_ple = self._get_or_create_ple_db(regione)
            for file_path in ple_files:
                count = self.import_file(
                    file_path=file_path,
                    regione=regione,
                    provincia=provincia,
                    comune_code=comune_code,
                    comune_name=comune_name,
                    db=db_ple,
                    layer_type='ple'
                )
                if count > 0:
                    self.stats["files_imported"] += 1
                    self.stats["parcels_imported"] += count
                    self.stats["ple_files_imported"] += 1
                    self.stats["ple_parcels_imported"] += count

        return self.stats


def main():
    parser = argparse.ArgumentParser(
        description="Import Italian cadastral files into SpatiaLite database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Import entire ITALIA directory
  python import_cadastral_to_db.py /mnt/mobile/data/catasto/original/ITALIA

  # Import to specific database
  python import_cadastral_to_db.py /path/to/ITALIA --db cadastral.sqlite

  # Import a single comune
  python import_cadastral_to_db.py /path/to/ITALIA/VENETO/VI/L840_VICENZA --single-comune VENETO VI
        """
    )
    parser.add_argument(
        "source",
        help="Source directory (ITALIA level or single comune folder)"
    )
    parser.add_argument(
        "--db-prefix",
        default="data/cadastral",
        help="Database path prefix (default: data/cadastral). Creates <prefix>_map.sqlite and <prefix>_ple.sqlite"
    )
    parser.add_argument(
        "--single-comune",
        nargs=2,
        metavar=("REGIONE", "PROVINCIA"),
        help="Import single comune folder with specified region and province"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of parallel workers (default: 4)"
    )
    parser.add_argument(
        "--prefer-gpkg",
        action="store_true",
        help="Prefer GPKG files from _converted directory (faster, handles corrupted GML)"
    )
    parser.add_argument(
        "--converted-dir",
        help="Path to converted files directory (default: source_dir + '_converted')"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force recreation of databases even if they already exist"
    )
    parser.add_argument(
        "--no-safe-mode",
        action="store_true",
        help="Disable safe mode (faster but may crash on corrupted files)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose output"
    )

    args = parser.parse_args()

    if not HAS_GEOPANDAS:
        logger.error("geopandas is required. Install with: pip install geopandas")
        sys.exit(1)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    source = Path(args.source)
    db_prefix = Path(args.db_prefix)
    converted_dir = Path(args.converted_dir) if args.converted_dir else None

    # Ensure data directory exists
    db_prefix.parent.mkdir(parents=True, exist_ok=True)

    safe_mode = not args.no_safe_mode

    if safe_mode:
        print("Safe mode enabled: validating files in subprocess before reading")
        print("(use --no-safe-mode for faster but less safe processing)")

    importer = CadastralImporter(
        db_prefix=db_prefix,
        workers=args.workers,
        prefer_gpkg=args.prefer_gpkg,
        converted_dir=converted_dir,
        force=args.force,
        safe_mode=safe_mode
    )

    if args.single_comune:
        regione, provincia = args.single_comune
        stats = importer.import_single_comune(source, regione, provincia)
    else:
        stats = importer.import_directory(source)

    # Print summary
    print("\n" + "=" * 60)
    print("Import Complete!")
    print("=" * 60)
    print(f"Files found:           {stats.get('files_found', 0)}")
    print(f"Files imported:        {stats.get('files_imported', 0)}")
    print(f"  MAP files:           {stats.get('map_files_imported', 0)}")
    print(f"  PLE files:           {stats.get('ple_files_imported', 0)}")
    print(f"Total records:         {stats.get('parcels_imported', 0)}")
    print(f"  MAP records (fogli): {stats.get('map_parcels_imported', 0)}")
    print(f"  PLE records (parcelle): {stats.get('ple_parcels_imported', 0)}")
    if stats.get('skipped_corrupted'):
        print(f"Skipped (corrupted):   {stats['skipped_corrupted']}")
    if stats.get('errors'):
        print(f"Errors:                {stats['errors']}")
    print("=" * 60)

    # Show MAP database statistics
    if importer.db_map:
        print(f"\nMAP Database: {importer.db_map_path}")
        map_stats = importer.db_map.get_statistics()
        print(f"  Total records: {map_stats['total_parcels']:,}")
        print(f"  SpatiaLite:    {'Available' if map_stats['spatialite_available'] else 'Not available'}")
        if map_stats['by_region']:
            print(f"  By Region:")
            for region, count in sorted(map_stats['by_region'].items()):
                print(f"    {region}: {count:,}")

    # Show PLE database statistics (per region)
    if importer.db_ple_by_region:
        print(f"\nPLE Databases ({len(importer.db_ple_by_region)} regions):")
        total_ple = 0
        for regione in sorted(importer.db_ple_by_region.keys()):
            db = importer.db_ple_by_region[regione]
            db_path = importer._get_ple_db_path(regione)
            ple_stats = db.get_statistics()
            total_ple += ple_stats['total_parcels']
            print(f"  {regione}:")
            print(f"    Database: {db_path.name}")
            print(f"    Records:  {ple_stats['total_parcels']:,}")
        print(f"\n  Total PLE records: {total_ple:,}")


if __name__ == "__main__":
    main()
