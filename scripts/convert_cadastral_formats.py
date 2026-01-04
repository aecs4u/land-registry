#!/usr/bin/env python3
"""
Convert Italian cadastral GML files to multiple geospatial formats.

Converts INSPIRE-compliant WFS GML files to:
- GeoPackage (.gpkg) - SQLite-based, excellent for data preservation
- FlatGeobuf (.fgb) - Fast web streaming, cloud-optimized
- GeoJSON (.geojson) - Universal JSON format
- Shapefile (.shp) - Legacy format for compatibility

All metadata from the original GML files is preserved in the output formats.

Usage:
    python scripts/convert_cadastral_formats.py /path/to/source [/path/to/output] [--formats gpkg,fgb,geojson,shp]

Example:
    python scripts/convert_cadastral_formats.py /mnt/mobile/data/catasto/original/ITALIA --formats gpkg,fgb
"""

import logging
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Optional, Annotated
from enum import Enum
import json
from datetime import datetime

import typer
from tqdm import tqdm

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Try to import geopandas (uses pyogrio as default backend in modern versions)
try:
    import geopandas as gpd
    HAS_GEOPANDAS = True
except ImportError:
    HAS_GEOPANDAS = False
    logger.error("geopandas is required. Install with: pip install geopandas")

# Supported output formats with their drivers and extensions
OUTPUT_FORMATS = {
    'gpkg': {
        'driver': 'GPKG',
        'extension': '.gpkg',
        'description': 'GeoPackage - SQLite-based, preserves all metadata',
        'supports_multiple_layers': True,
    },
    'fgb': {
        'driver': 'FlatGeobuf',
        'extension': '.fgb',
        'description': 'FlatGeobuf - Fast streaming, cloud-optimized',
        'supports_multiple_layers': False,
    },
    'geojson': {
        'driver': 'GeoJSON',
        'extension': '.geojson',
        'description': 'GeoJSON - Universal JSON format',
        'supports_multiple_layers': False,
    },
    'shp': {
        'driver': 'ESRI Shapefile',
        'extension': '.shp',
        'description': 'Shapefile - Legacy format (10-char field name limit)',
        'supports_multiple_layers': False,
        'field_name_limit': 10,
    },
}

# Column name mapping for Shapefile (10-char limit)
SHAPEFILE_COLUMN_MAP = {
    'INSPIREID_LOCALID': 'INSP_LOCID',
    'INSPIREID_NAMESPACE': 'INSP_NS',
    'NATIONALCADASTRALZONINGREFERENCE': 'NAT_CAD_RF',
    'BEGINLIFESPANVERSION': 'BEGIN_LIFE',
    'ENDLIFESPANVERSION': 'END_LIFE',
    'LEVELNAME': 'LEVEL_NAME',
    'LEVELNAME_LOCALE': 'LEVNM_LOCL',
    'ORIGINALMAPSCALEDENOMINATOR': 'ORIG_SCALE',
    'ADMINISTRATIVEUNIT': 'ADMIN_UNIT',
}


class CadastralConverter:
    """Convert cadastral GML files to multiple geospatial formats."""

    def __init__(
        self,
        output_dir: Path,
        formats: list[str] = None,
        parallel_workers: int = 4,
        overwrite: bool = False,
        in_place: bool = False
    ):
        """
        Initialize the converter.

        Args:
            output_dir: Directory to write converted files (ignored if in_place=True)
            formats: List of output formats (gpkg, fgb, geojson, shp)
            parallel_workers: Number of parallel conversion workers
            overwrite: Whether to overwrite existing files
            in_place: If True, write converted files next to source files
        """
        self.output_dir = Path(output_dir)
        self.formats = formats or ['gpkg']
        self.parallel_workers = parallel_workers
        self.overwrite = overwrite
        self.in_place = in_place
        self.stats = {
            'files_found': 0,
            'files_converted': 0,
            'files_skipped': 0,
            'errors': 0,
            'formats_created': {fmt: 0 for fmt in self.formats}
        }

        # Validate formats
        for fmt in self.formats:
            if fmt not in OUTPUT_FORMATS:
                raise ValueError(f"Unsupported format: {fmt}. Supported: {list(OUTPUT_FORMATS.keys())}")

    def convert_gml_file(
        self,
        gml_path: Path,
        relative_path: Path
    ) -> dict:
        """
        Convert a single GML file to all requested formats.

        Args:
            gml_path: Path to source GML file
            relative_path: Relative path for output directory structure

        Returns:
            Statistics dict for this conversion
        """
        result = {
            'source': str(gml_path),
            'success': False,
            'formats_created': [],
            'error': None
        }

        try:
            # Read GML file with geopandas
            logger.debug(f"Reading {gml_path.name}...")
            gdf = gpd.read_file(gml_path)

            if gdf.empty:
                logger.warning(f"Empty GML file: {gml_path}")
                result['error'] = 'Empty file'
                return result

            # Ensure CRS is set (INSPIRE uses EPSG:6706 - ETRS89-GRS80)
            if gdf.crs is None:
                gdf.set_crs(epsg=6706, inplace=True)
                logger.debug(f"Set CRS to EPSG:6706 for {gml_path.name}")

            # Determine output directory
            if self.in_place:
                # Write next to source file
                output_subdir = gml_path.parent
            else:
                # Create output directory structure
                output_subdir = self.output_dir / relative_path.parent
                output_subdir.mkdir(parents=True, exist_ok=True)

            # Base name without extension
            base_name = gml_path.stem

            # Convert to each requested format
            for fmt in self.formats:
                fmt_info = OUTPUT_FORMATS[fmt]
                output_path = output_subdir / f"{base_name}{fmt_info['extension']}"

                # Skip if exists and not overwriting
                if output_path.exists() and not self.overwrite:
                    logger.debug(f"Skipping existing: {output_path}")
                    continue

                try:
                    self._write_format(gdf, output_path, fmt)
                    result['formats_created'].append(fmt)
                    logger.debug(f"Created {fmt}: {output_path.name}")
                except Exception as e:
                    logger.error(f"Error writing {fmt} for {gml_path.name}: {e}")

            result['success'] = len(result['formats_created']) > 0

        except Exception as e:
            result['error'] = str(e)
            logger.error(f"Error converting {gml_path}: {e}")

        return result

    def _write_format(self, gdf: 'gpd.GeoDataFrame', output_path: Path, fmt: str) -> None:
        """
        Write GeoDataFrame to a specific format with metadata preservation.

        Args:
            gdf: GeoDataFrame to write
            output_path: Output file path
            fmt: Format key (gpkg, fgb, geojson, shp)
        """
        fmt_info = OUTPUT_FORMATS[fmt]
        driver = fmt_info['driver']

        # Make a copy to avoid modifying original
        gdf_out = gdf.copy()

        # Handle Shapefile column name limitations
        if fmt == 'shp':
            gdf_out = self._prepare_for_shapefile(gdf_out)

        # Write with appropriate options
        if fmt == 'gpkg':
            # GeoPackage: Include metadata as layer options
            gdf_out.to_file(
                output_path,
                driver=driver,
                layer=output_path.stem,
                # GeoPackage preserves all field types natively
            )

        elif fmt == 'fgb':
            # FlatGeobuf: Fast binary format
            gdf_out.to_file(
                output_path,
                driver=driver
            )

        elif fmt == 'geojson':
            # GeoJSON: Include CRS and full precision
            gdf_out.to_file(
                output_path,
                driver=driver,
                # GeoJSON spec uses WGS84, but we keep the original CRS in properties
            )

            # Add metadata as a sidecar file
            self._write_geojson_metadata(gdf_out, output_path)

        elif fmt == 'shp':
            # Shapefile: Handle field name truncation
            gdf_out.to_file(
                output_path,
                driver=driver,
                encoding='utf-8'
            )

            # Write field mapping as sidecar file
            self._write_shapefile_metadata(output_path)

    def _prepare_for_shapefile(self, gdf: 'gpd.GeoDataFrame') -> 'gpd.GeoDataFrame':
        """
        Prepare GeoDataFrame for Shapefile format.
        Handles 10-character field name limit.

        Args:
            gdf: Input GeoDataFrame

        Returns:
            GeoDataFrame with renamed columns
        """
        # Rename columns that exceed 10 characters
        rename_map = {}
        for col in gdf.columns:
            if col == 'geometry':
                continue

            if col in SHAPEFILE_COLUMN_MAP:
                rename_map[col] = SHAPEFILE_COLUMN_MAP[col]
            elif len(col) > 10:
                # Truncate to 10 characters
                new_name = col[:10]
                # Handle conflicts
                counter = 1
                while new_name in rename_map.values() or new_name in gdf.columns:
                    new_name = col[:8] + str(counter).zfill(2)
                    counter += 1
                rename_map[col] = new_name

        if rename_map:
            gdf = gdf.rename(columns=rename_map)

        return gdf

    def _write_geojson_metadata(self, gdf: 'gpd.GeoDataFrame', output_path: Path) -> None:
        """Write metadata sidecar file for GeoJSON."""
        metadata = {
            'source_format': 'INSPIRE WFS GML',
            'crs': str(gdf.crs) if gdf.crs else None,
            'feature_count': len(gdf),
            'columns': list(gdf.columns),
            'converted_at': datetime.now().isoformat(),
            'converter': 'convert_cadastral_formats.py'
        }

        metadata_path = output_path.with_suffix('.metadata.json')
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)

    def _write_shapefile_metadata(self, output_path: Path) -> None:
        """Write field mapping sidecar file for Shapefile."""
        mapping_path = output_path.with_suffix('.field_mapping.json')

        # Inverse mapping for documentation
        inverse_map = {v: k for k, v in SHAPEFILE_COLUMN_MAP.items()}

        with open(mapping_path, 'w', encoding='utf-8') as f:
            json.dump({
                'description': 'Shapefile field name mapping (original -> shortened)',
                'mapping': SHAPEFILE_COLUMN_MAP,
                'inverse_mapping': inverse_map
            }, f, indent=2, ensure_ascii=False)

    def run(self, source_dir: Path) -> dict:
        """
        Run the conversion process on all GML files in source directory.

        Args:
            source_dir: Directory containing GML files (recursively searched)

        Returns:
            Statistics dictionary
        """
        source_dir = Path(source_dir)

        if not source_dir.exists():
            logger.error(f"Source directory does not exist: {source_dir}")
            return {'error': 'Source directory not found'}

        # Find all GML files
        gml_files = list(source_dir.rglob("*.gml")) + list(source_dir.rglob("*.GML"))
        self.stats['files_found'] = len(gml_files)

        if not gml_files:
            logger.warning(f"No GML files found in {source_dir}")
            return self.stats

        logger.info(f"Found {len(gml_files)} GML files to convert")
        logger.info(f"Output formats: {', '.join(self.formats)}")
        logger.info(f"Output directory: {self.output_dir}")

        # Process files (parallel or sequential based on count)
        if len(gml_files) > 10 and self.parallel_workers > 1:
            self._process_parallel(gml_files, source_dir)
        else:
            self._process_sequential(gml_files, source_dir)

        return self.stats

    def _process_sequential(self, gml_files: list[Path], source_dir: Path) -> None:
        """Process files sequentially."""
        for gml_path in tqdm(gml_files, desc="Converting", unit="file"):
            relative_path = gml_path.relative_to(source_dir)
            result = self.convert_gml_file(gml_path, relative_path)
            self._update_stats(result)

    def _process_parallel(self, gml_files: list[Path], source_dir: Path) -> None:
        """Process files in parallel."""
        logger.info(f"Processing {len(gml_files)} files with {self.parallel_workers} workers...")

        with ProcessPoolExecutor(max_workers=self.parallel_workers) as executor:
            futures = {}
            for gml_path in gml_files:
                relative_path = gml_path.relative_to(source_dir)
                future = executor.submit(
                    self.convert_gml_file,
                    gml_path,
                    relative_path
                )
                futures[future] = gml_path

            with tqdm(total=len(gml_files), desc="Converting", unit="file") as pbar:
                for future in as_completed(futures):
                    gml_path = futures[future]
                    try:
                        result = future.result()
                        self._update_stats(result)
                    except Exception as e:
                        logger.error(f"Error processing {gml_path}: {e}")
                        self.stats['errors'] += 1
                    pbar.update(1)

    def _update_stats(self, result: dict) -> None:
        """Update statistics from a conversion result."""
        if result['success']:
            self.stats['files_converted'] += 1
            for fmt in result['formats_created']:
                self.stats['formats_created'][fmt] += 1
        elif result['error']:
            self.stats['errors'] += 1
        else:
            self.stats['files_skipped'] += 1


app = typer.Typer(
    help="Convert Italian cadastral GML files to multiple geospatial formats.",
    no_args_is_help=True,
)


@app.command()
def convert(
    source: Annotated[
        Path,
        typer.Argument(help="Source directory containing GML files")
    ],
    output: Annotated[
        Optional[Path],
        typer.Argument(help="Output directory (defaults to source with _converted suffix)")
    ] = None,
    formats: Annotated[
        str,
        typer.Option(
            "--formats", "-f",
            help="Comma-separated list of output formats (gpkg,fgb,geojson,shp)"
        )
    ] = "gpkg",
    workers: Annotated[
        int,
        typer.Option("--workers", "-w", help="Number of parallel workers")
    ] = 4,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="Overwrite existing output files")
    ] = False,
    in_place: Annotated[
        bool,
        typer.Option("--in-place", help="Write converted files next to source GML files")
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Verbose output")
    ] = False,
):
    """
    Convert GML files to multiple geospatial formats.

    Supported formats:
      - gpkg: GeoPackage (recommended, preserves all metadata)
      - fgb: FlatGeobuf (fast streaming, cloud-optimized)
      - geojson: GeoJSON (universal JSON format)
      - shp: Shapefile (legacy, 10-char field name limit)

    Examples:
      # Convert to GeoPackage only (default)
      python convert_cadastral_formats.py /path/to/ITALIA

      # Convert to multiple formats
      python convert_cadastral_formats.py /path/to/ITALIA --formats gpkg,fgb,geojson

      # Specify output directory
      python convert_cadastral_formats.py /path/to/ITALIA /path/to/output --formats gpkg,fgb
    """
    if not HAS_GEOPANDAS:
        logger.error("Required dependencies not found.")
        logger.error("Install with: pip install geopandas pyproj")
        raise typer.Exit(code=1)

    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Parse formats
    format_list = [f.strip().lower() for f in formats.split(',')]

    # Set up paths
    if in_place:
        output_dir = source  # Not used when in_place=True, but needed for constructor
    else:
        output_dir = output if output else source.parent / f"{source.name}_converted"

    # Create converter and run
    converter = CadastralConverter(
        output_dir=output_dir,
        formats=format_list,
        parallel_workers=workers,
        overwrite=overwrite,
        in_place=in_place
    )

    stats = converter.run(source)

    # Print summary
    print("\n" + "=" * 60)
    print("Conversion Complete!")
    print("=" * 60)
    print(f"GML files found:     {stats.get('files_found', 0)}")
    print(f"Files converted:     {stats.get('files_converted', 0)}")
    print(f"Files skipped:       {stats.get('files_skipped', 0)}")
    if stats.get('errors'):
        print(f"Errors:              {stats['errors']}")
    print()
    print("Files created per format:")
    for fmt, count in stats.get('formats_created', {}).items():
        print(f"  {fmt.upper():10} {count}")
    print("=" * 60)
    if in_place:
        print("\nFiles written in-place next to source GML files")
    else:
        print(f"\nOutput directory: {output_dir}")


if __name__ == "__main__":
    app()
