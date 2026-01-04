#!/usr/bin/env python3
"""
Merge FlatGeobuf (.fgb) files per region.

This script reads existing per-comune FGB files and merges them into
per-region FGB files for better performance:
- Fewer files to manage
- Single spatial index per region
- Better HTTP range request support
- Faster map tile rendering

Usage:
    python scripts/merge_fgb_per_region.py /path/to/ITALIA --output-dir /data/cadastral_fgb

Example:
    python scripts/merge_fgb_per_region.py /mnt/mobile/data/catasto/ITALIA --output-dir /data/aecs4u.it/land-registry/fgb
"""

import sys
import logging
from pathlib import Path
from collections import defaultdict
from typing import Optional, Annotated
from enum import Enum

import typer
from tqdm import tqdm

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

try:
    import geopandas as gpd
    import pandas as pd
    # Use pyogrio as the I/O engine (faster and more stable)
    gpd.options.io_engine = "pyogrio"
    HAS_GEOPANDAS = True
except ImportError:
    HAS_GEOPANDAS = False
    logger.error("geopandas is required. Install with: pip install geopandas")


def get_layer_type(file_path: Path) -> str | None:
    """Determine layer type from filename."""
    filename = file_path.stem.lower()
    if '_map' in filename or filename.endswith('_map'):
        return 'map'
    elif '_ple' in filename or filename.endswith('_ple'):
        return 'ple'
    return None


def parse_file_info(file_path: Path, source_dir: Path) -> dict | None:
    """Parse file path to extract region, province, comune info."""
    try:
        parts = file_path.relative_to(source_dir).parts
    except ValueError:
        logger.warning(f"File not under source_dir: {file_path}")
        return None

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


def merge_fgb_files(
    source_dir: Path,
    output_dir: Path,
    force: bool = False,
    layer_type: str = None,
    region_filter: str = None
) -> dict:
    """
    Merge FGB files per region.

    Args:
        source_dir: Root directory (ITALIA level)
        output_dir: Output directory for merged FGB files
        force: If True, overwrite existing output files
        layer_type: Filter by layer type ('map', 'ple', or None for both)
        region_filter: Only process this specific region (e.g., 'VALLE-AOSTA')

    Returns:
        Statistics dictionary
    """
    source_dir = Path(source_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    stats = {
        "files_found": 0,
        "files_processed": 0,
        "files_skipped": 0,
        "regions_processed": 0,
        "output_files_created": 0,
        "total_features": 0,
        "errors": 0,
    }

    # Find all FGB files
    logger.info("Scanning for FGB files...")
    fgb_files = list(source_dir.rglob("*.fgb")) + list(source_dir.rglob("*.FGB"))
    stats["files_found"] = len(fgb_files)
    logger.info(f"Found {len(fgb_files)} FGB files")

    # Group files by region and layer type
    files_by_region: dict[str, dict[str, list[Path]]] = defaultdict(lambda: defaultdict(list))

    for file_path in fgb_files:
        file_info = parse_file_info(file_path, source_dir)
        if not file_info:
            continue

        file_layer_type = get_layer_type(file_path)
        if not file_layer_type:
            continue

        # Filter by layer type if specified
        if layer_type and file_layer_type != layer_type:
            continue

        regione = file_info["regione"]

        # Filter by region if specified
        if region_filter and regione.upper() != region_filter.upper():
            continue

        files_by_region[regione][file_layer_type].append(file_path)

    # Process each region
    sorted_regions = sorted(files_by_region.keys())
    logger.info(f"Processing {len(sorted_regions)} regions")

    for region_idx, regione in enumerate(sorted_regions, 1):
        region_files = files_by_region[regione]
        region_slug = regione.lower().replace(' ', '_').replace('-', '_')

        print(f"\n{'=' * 60}")
        print(f"Region {region_idx}/{len(sorted_regions)}: {regione}")
        print(f"{'=' * 60}")

        for ltype in ['map', 'ple']:
            if ltype not in region_files:
                continue

            files = region_files[ltype]
            output_path = output_dir / f"cadastral_{ltype}.{region_slug}.fgb"

            print(f"\n  {ltype.upper()}: {len(files)} files -> {output_path.name}")

            # Check if output already exists
            if output_path.exists() and not force:
                print(f"    Skipping (already exists, use --force to overwrite)")
                stats["files_skipped"] += len(files)
                continue

            # Read and merge all files for this region/type
            gdfs = []
            with tqdm(files, desc=f"  Reading {ltype.upper()}", unit="file", leave=False) as pbar:
                for file_path in pbar:
                    try:
                        gdf = gpd.read_file(str(file_path))
                        if not gdf.empty:
                            # Add source info columns
                            file_info = parse_file_info(file_path, source_dir)
                            if file_info:
                                gdf['_regione'] = file_info['regione']
                                gdf['_provincia'] = file_info['provincia']
                                gdf['_comune_code'] = file_info['comune_code']
                                gdf['_comune_name'] = file_info['comune_name']
                            gdfs.append(gdf)
                            stats["files_processed"] += 1
                    except Exception as e:
                        logger.warning(f"Error reading {file_path}: {e}")
                        stats["errors"] += 1

            if not gdfs:
                print(f"    No valid data found, skipping")
                continue

            # Concatenate all GeoDataFrames
            print(f"    Merging {len(gdfs)} dataframes...")
            merged_gdf = pd.concat(gdfs, ignore_index=True)
            merged_gdf = gpd.GeoDataFrame(merged_gdf, crs=gdfs[0].crs)

            feature_count = len(merged_gdf)
            stats["total_features"] += feature_count
            print(f"    Total features: {feature_count:,}")

            # Write merged FGB file
            print(f"    Writing {output_path.name}...")
            try:
                merged_gdf.to_file(str(output_path), driver="FlatGeobuf")
                stats["output_files_created"] += 1
                file_size_mb = output_path.stat().st_size / (1024 * 1024)
                print(f"    Created: {output_path.name} ({file_size_mb:.1f} MB, {feature_count:,} features)")
            except Exception as e:
                logger.error(f"Error writing {output_path}: {e}")
                stats["errors"] += 1

        stats["regions_processed"] += 1

    return stats


class LayerType(str, Enum):
    """Layer type for filtering."""
    map = "map"
    ple = "ple"


app = typer.Typer(
    help="Merge FlatGeobuf files per region for better performance.",
    no_args_is_help=True,
)


@app.command()
def merge(
    source: Annotated[
        Path,
        typer.Argument(help="Source directory (ITALIA level containing FGB files)")
    ],
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", "-o", help="Output directory for merged FGB files")
    ],
    layer_type: Annotated[
        Optional[LayerType],
        typer.Option("--layer-type", "-l", help="Only process specific layer type (default: both)")
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Overwrite existing output files")
    ] = False,
    region: Annotated[
        Optional[str],
        typer.Option("--region", "-r", help="Only process a specific region (e.g., 'VALLE-AOSTA')")
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Verbose output")
    ] = False,
):
    """
    Merge per-comune FGB files into per-region FGB files.

    Examples:
      # Merge all FGB files per region
      python merge_fgb_per_region.py /mnt/mobile/data/catasto/ITALIA --output-dir /data/cadastral_fgb

      # Merge only MAP files
      python merge_fgb_per_region.py /mnt/mobile/data/catasto/ITALIA --output-dir /data/cadastral_fgb --layer-type map

      # Force overwrite existing files
      python merge_fgb_per_region.py /mnt/mobile/data/catasto/ITALIA --output-dir /data/cadastral_fgb --force

      # Process a single region
      python merge_fgb_per_region.py /mnt/mobile/data/catasto/ITALIA --output-dir /data/cadastral_fgb --region VALLE-AOSTA
    """
    if not HAS_GEOPANDAS:
        logger.error("geopandas is required. Install with: pip install geopandas")
        raise typer.Exit(code=1)

    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if not source.exists():
        logger.error(f"Source directory does not exist: {source}")
        raise typer.Exit(code=1)

    # Convert layer_type enum to string if provided
    layer_type_str = layer_type.value if layer_type else None

    stats = merge_fgb_files(
        source_dir=source,
        output_dir=output_dir,
        force=force,
        layer_type=layer_type_str,
        region_filter=region
    )

    # Print summary
    print("\n" + "=" * 60)
    print("Merge Complete!")
    print("=" * 60)
    print(f"FGB files found:      {stats['files_found']:,}")
    print(f"Files processed:      {stats['files_processed']:,}")
    print(f"Files skipped:        {stats['files_skipped']:,}")
    print(f"Regions processed:    {stats['regions_processed']:,}")
    print(f"Output files created: {stats['output_files_created']:,}")
    print(f"Total features:       {stats['total_features']:,}")
    print(f"Errors:               {stats['errors']:,}")
    print(f"\nOutput directory: {output_dir}")

    # List created files
    if output_dir.exists():
        fgb_outputs = sorted(output_dir.glob("*.fgb"))
        if fgb_outputs:
            print(f"\nCreated files ({len(fgb_outputs)}):")
            for f in fgb_outputs:
                size_mb = f.stat().st_size / (1024 * 1024)
                print(f"  {f.name}: {size_mb:.1f} MB")


if __name__ == "__main__":
    app()
