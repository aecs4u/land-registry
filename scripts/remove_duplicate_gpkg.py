#!/usr/bin/env python3
"""
Compare GPKG files by their geospatial content (not file hash).

GPKG files converted from the same source at different times will have
different file hashes due to SQLite metadata, but identical geospatial data.

This script compares the actual geometry and attribute content.

Usage:
    python scripts/remove_duplicate_gpkg.py /path/to/main /path/to/original [--dry-run]

Example:
    python scripts/remove_duplicate_gpkg.py /mnt/mobile/data/catasto/ITALIA /mnt/mobile/data/catasto/original/ITALIA --dry-run
"""

import sys
import argparse
import logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

try:
    import geopandas as gpd
    HAS_GEOPANDAS = True
except ImportError:
    HAS_GEOPANDAS = False
    logger.error("geopandas is required")


def compare_gpkg_content(file1: Path, file2: Path) -> tuple[bool, str]:
    """
    Compare two GPKG files by their geospatial content.

    Args:
        file1: First GPKG file
        file2: Second GPKG file

    Returns:
        Tuple of (are_identical, reason)
    """
    try:
        gdf1 = gpd.read_file(file1)
        gdf2 = gpd.read_file(file2)

        # Compare row counts
        if len(gdf1) != len(gdf2):
            return False, f"Row count differs: {len(gdf1)} vs {len(gdf2)}"

        # Compare columns
        cols1 = set(gdf1.columns)
        cols2 = set(gdf2.columns)
        if cols1 != cols2:
            return False, f"Columns differ: {cols1 - cols2} vs {cols2 - cols1}"

        # Compare CRS
        if gdf1.crs != gdf2.crs:
            return False, f"CRS differs: {gdf1.crs} vs {gdf2.crs}"

        # For empty dataframes, they're identical
        if len(gdf1) == 0:
            return True, "Both empty"

        # Compare geometry bounds (quick check)
        if gdf1.total_bounds.tolist() != gdf2.total_bounds.tolist():
            return False, "Geometry bounds differ"

        # Compare geometry WKT (thorough check)
        # Sort by a stable column to ensure same order
        sort_cols = [c for c in ['LABEL', 'gml_id', 'fid'] if c in gdf1.columns]
        if sort_cols:
            gdf1 = gdf1.sort_values(sort_cols[0]).reset_index(drop=True)
            gdf2 = gdf2.sort_values(sort_cols[0]).reset_index(drop=True)

        # Compare geometries
        geom1 = gdf1.geometry.to_wkt().tolist()
        geom2 = gdf2.geometry.to_wkt().tolist()
        if geom1 != geom2:
            return False, "Geometries differ"

        # Compare non-geometry columns
        non_geom_cols = [c for c in gdf1.columns if c != 'geometry']
        for col in non_geom_cols:
            if not gdf1[col].equals(gdf2[col]):
                # Check if it's just NaN differences
                if gdf1[col].isna().all() and gdf2[col].isna().all():
                    continue
                return False, f"Column '{col}' differs"

        return True, "Identical content"

    except Exception as e:
        return False, f"Error comparing: {e}"


def find_content_duplicates(main_dir: Path, original_dir: Path, workers: int = 4) -> tuple[list, list, list]:
    """
    Find GPKG files with identical content.

    Args:
        main_dir: Main directory (files to keep)
        original_dir: Original directory (duplicates to remove)
        workers: Number of parallel workers

    Returns:
        Tuple of (duplicates, not_found, different)
    """
    # Get all GPKG files from original directory
    original_files = list(original_dir.rglob("*.gpkg")) + list(original_dir.rglob("*.GPKG"))
    logger.info(f"Found {len(original_files)} GPKG files in original directory")

    duplicates = []
    not_found = []
    different = []

    def check_file(orig_file: Path) -> tuple[str, Path, Path | None, str]:
        """Check if file has identical content in main directory."""
        try:
            rel_path = orig_file.relative_to(original_dir)
            main_file = main_dir / rel_path

            if not main_file.exists():
                return ('not_found', orig_file, None, "Not in main")

            is_identical, reason = compare_gpkg_content(main_file, orig_file)

            if is_identical:
                return ('duplicate', orig_file, main_file, reason)
            else:
                return ('different', orig_file, main_file, reason)

        except Exception as e:
            return ('error', orig_file, None, str(e))

    # Process files (not parallel for geopandas - it's not thread-safe)
    for i, orig_file in enumerate(original_files, 1):
        status, orig, main, reason = check_file(orig_file)

        if status == 'duplicate':
            duplicates.append((main, orig))
            logger.debug(f"Duplicate: {orig.name}")
        elif status == 'not_found':
            not_found.append(orig)
        elif status == 'different':
            different.append((main, orig, reason))
            logger.debug(f"Different: {orig.name} - {reason}")

        if i % 100 == 0:
            logger.info(f"Progress: {i}/{len(original_files)} - {len(duplicates)} duplicates found")

    return duplicates, not_found, different


def remove_files(files_to_remove: list[Path], dry_run: bool = True) -> dict:
    """Remove files."""
    stats = {'removed': 0, 'failed': 0, 'bytes_freed': 0}

    for file_path in files_to_remove:
        try:
            size = file_path.stat().st_size
            if dry_run:
                logger.info(f"Would remove: {file_path}")
            else:
                file_path.unlink()
                logger.debug(f"Removed: {file_path}")
            stats['removed'] += 1
            stats['bytes_freed'] += size
        except Exception as e:
            logger.error(f"Failed to remove {file_path}: {e}")
            stats['failed'] += 1

    return stats


def cleanup_empty_dirs(directory: Path, dry_run: bool = True) -> int:
    """Remove empty directories recursively."""
    removed = 0
    for dirpath in sorted(directory.rglob('*'), key=lambda p: len(p.parts), reverse=True):
        if dirpath.is_dir():
            try:
                if not any(dirpath.iterdir()):
                    if dry_run:
                        logger.info(f"Would remove empty dir: {dirpath}")
                    else:
                        dirpath.rmdir()
                    removed += 1
            except Exception:
                pass
    return removed


def format_bytes(size: int) -> str:
    """Format bytes as human-readable string."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def main():
    parser = argparse.ArgumentParser(
        description="Compare GPKG files by content and remove duplicates"
    )
    parser.add_argument("main_dir", help="Main directory (files to keep)")
    parser.add_argument("original_dir", help="Original directory (duplicates to remove)")
    parser.add_argument("--dry-run", action="store_true", help="Only show what would be deleted")
    parser.add_argument("--cleanup-empty", action="store_true", help="Remove empty directories")
    parser.add_argument("--show-different", action="store_true", help="Show why files are different")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")

    args = parser.parse_args()

    if not HAS_GEOPANDAS:
        logger.error("geopandas is required")
        sys.exit(1)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    main_dir = Path(args.main_dir)
    original_dir = Path(args.original_dir)

    if not main_dir.exists() or not original_dir.exists():
        logger.error("Directories must exist")
        sys.exit(1)

    print(f"\nComparing GPKG content...")
    print(f"  Main:     {main_dir}")
    print(f"  Original: {original_dir}")
    print()

    duplicates, not_found, different = find_content_duplicates(main_dir, original_dir)

    print("\n" + "=" * 60)
    print("Content Comparison Results")
    print("=" * 60)
    print(f"Identical content (duplicates): {len(duplicates)}")
    print(f"Only in original:               {len(not_found)}")
    print(f"Different content:              {len(different)}")
    print("=" * 60)

    if args.show_different and different:
        print("\nFiles with different content:")
        for main_f, orig_f, reason in different[:20]:
            print(f"  {orig_f.name}: {reason}")
        if len(different) > 20:
            print(f"  ... and {len(different) - 20} more")

    if not duplicates:
        print("\nNo duplicates found.")
        return

    # Calculate space to be freed
    files_to_remove = [orig for _, orig in duplicates]
    total_size = sum(f.stat().st_size for f in files_to_remove)
    print(f"\nSpace to be freed: {format_bytes(total_size)}")

    if args.dry_run:
        print(f"\n[DRY RUN] Would remove {len(duplicates)} files")
        for _, orig_file in duplicates[:10]:
            print(f"  {orig_file}")
        if len(duplicates) > 10:
            print(f"  ... and {len(duplicates) - 10} more")
    else:
        print(f"\nRemoving {len(duplicates)} duplicate files...")
        stats = remove_files(files_to_remove, dry_run=False)

        print("\n" + "=" * 60)
        print("Removal Summary")
        print("=" * 60)
        print(f"Files removed:  {stats['removed']}")
        print(f"Failed:         {stats['failed']}")
        print(f"Space freed:    {format_bytes(stats['bytes_freed'])}")
        print("=" * 60)

        if args.cleanup_empty:
            print("\nCleaning up empty directories...")
            removed_dirs = cleanup_empty_dirs(original_dir, dry_run=False)
            print(f"Removed {removed_dirs} empty directories")


if __name__ == "__main__":
    main()
