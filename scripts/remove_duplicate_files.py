#!/usr/bin/env python3
"""
Compare files between two directories and remove duplicates.

Compares files in the 'original' directory with files in the 'main' directory.
If files are identical (same content), removes the copy from 'original'.

Usage:
    python scripts/remove_duplicate_files.py /path/to/main /path/to/original [--dry-run]

Example:
    # Check what would be deleted (dry run)
    python scripts/remove_duplicate_files.py /mnt/mobile/data/catasto/ITALIA /mnt/mobile/data/catasto/original/ITALIA --dry-run

    # Actually delete duplicates
    python scripts/remove_duplicate_files.py /mnt/mobile/data/catasto/ITALIA /mnt/mobile/data/catasto/original/ITALIA
"""

import os
import sys
import argparse
import hashlib
import logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_file_hash(file_path: Path, chunk_size: int = 8192) -> str | None:
    """
    Calculate MD5 hash of a file.

    Args:
        file_path: Path to the file
        chunk_size: Size of chunks to read

    Returns:
        MD5 hash string, or None if file can't be read
    """
    try:
        hasher = hashlib.md5()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(chunk_size), b''):
                hasher.update(chunk)
        return hasher.hexdigest()
    except (IOError, OSError) as e:
        logger.warning(f"Cannot read file {file_path}: {e}")
        return None


def files_are_identical(file1: Path, file2: Path) -> bool:
    """
    Check if two files have identical content.

    First compares file sizes (fast), then compares hashes if sizes match.

    Args:
        file1: First file path
        file2: Second file path

    Returns:
        True if files are identical
    """
    try:
        # Quick size check first
        size1 = file1.stat().st_size
        size2 = file2.stat().st_size

        if size1 != size2:
            return False

        # Compare hashes
        hash1 = get_file_hash(file1)
        hash2 = get_file_hash(file2)

        if hash1 is None or hash2 is None:
            return False

        return hash1 == hash2

    except (IOError, OSError) as e:
        logger.warning(f"Error comparing files: {e}")
        return False


def find_duplicates(main_dir: Path, original_dir: Path, workers: int = 4) -> list[tuple[Path, Path]]:
    """
    Find files in original_dir that are duplicates of files in main_dir.

    Args:
        main_dir: Main directory (files to keep)
        original_dir: Original directory (duplicates to remove)
        workers: Number of parallel workers

    Returns:
        List of tuples (main_file, original_file) for duplicates
    """
    # Get all files from original directory
    original_files = []
    for ext in ['*.gpkg', '*.GPKG', '*.gml', '*.GML', '*.fgb', '*.FGB']:
        original_files.extend(original_dir.rglob(ext))

    logger.info(f"Found {len(original_files)} files in original directory")

    duplicates = []
    not_found = []
    different = []

    def check_file(orig_file: Path) -> tuple[str, Path, Path | None]:
        """Check if file has a duplicate in main directory."""
        # Calculate corresponding path in main directory
        try:
            rel_path = orig_file.relative_to(original_dir)
            main_file = main_dir / rel_path

            if not main_file.exists():
                return ('not_found', orig_file, None)

            if files_are_identical(main_file, orig_file):
                return ('duplicate', orig_file, main_file)
            else:
                return ('different', orig_file, main_file)

        except Exception as e:
            logger.error(f"Error checking {orig_file}: {e}")
            return ('error', orig_file, None)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_file = {
            executor.submit(check_file, f): f
            for f in original_files
        }

        for i, future in enumerate(as_completed(future_to_file), 1):
            try:
                status, orig_file, main_file = future.result()

                if status == 'duplicate':
                    duplicates.append((main_file, orig_file))
                elif status == 'not_found':
                    not_found.append(orig_file)
                elif status == 'different':
                    different.append((main_file, orig_file))

            except Exception as e:
                logger.error(f"Error processing file: {e}")

            if i % 500 == 0:
                logger.info(f"Progress: {i}/{len(original_files)} files checked")

    logger.info(f"Results: {len(duplicates)} duplicates, {len(not_found)} not in main, {len(different)} different")

    return duplicates, not_found, different


def remove_duplicates(duplicates: list[tuple[Path, Path]], dry_run: bool = True) -> dict:
    """
    Remove duplicate files from original directory.

    Args:
        duplicates: List of (main_file, original_file) tuples
        dry_run: If True, only print what would be deleted

    Returns:
        Statistics dictionary
    """
    stats = {
        'removed': 0,
        'failed': 0,
        'bytes_freed': 0,
    }

    for main_file, orig_file in duplicates:
        try:
            size = orig_file.stat().st_size

            if dry_run:
                logger.info(f"Would remove: {orig_file}")
            else:
                orig_file.unlink()
                logger.info(f"Removed: {orig_file}")

            stats['removed'] += 1
            stats['bytes_freed'] += size

        except Exception as e:
            logger.error(f"Failed to remove {orig_file}: {e}")
            stats['failed'] += 1

    return stats


def cleanup_empty_dirs(directory: Path, dry_run: bool = True) -> int:
    """
    Remove empty directories recursively.

    Args:
        directory: Root directory to clean
        dry_run: If True, only print what would be deleted

    Returns:
        Number of directories removed
    """
    removed = 0

    # Walk bottom-up to remove nested empty directories
    for dirpath in sorted(directory.rglob('*'), key=lambda p: len(p.parts), reverse=True):
        if dirpath.is_dir():
            try:
                # Check if directory is empty
                if not any(dirpath.iterdir()):
                    if dry_run:
                        logger.info(f"Would remove empty dir: {dirpath}")
                    else:
                        dirpath.rmdir()
                        logger.info(f"Removed empty dir: {dirpath}")
                    removed += 1
            except Exception as e:
                logger.warning(f"Cannot remove directory {dirpath}: {e}")

    return removed


def format_bytes(size: int) -> str:
    """Format bytes as human-readable string."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def main():
    parser = argparse.ArgumentParser(
        description="Find and remove duplicate files between directories",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry run - see what would be deleted
  python remove_duplicate_files.py /mnt/mobile/data/catasto/ITALIA /mnt/mobile/data/catasto/original/ITALIA --dry-run

  # Actually remove duplicates
  python remove_duplicate_files.py /mnt/mobile/data/catasto/ITALIA /mnt/mobile/data/catasto/original/ITALIA

  # Also remove empty directories after cleanup
  python remove_duplicate_files.py /mnt/mobile/data/catasto/ITALIA /mnt/mobile/data/catasto/original/ITALIA --cleanup-empty
        """
    )
    parser.add_argument(
        "main_dir",
        help="Main directory (files to keep)"
    )
    parser.add_argument(
        "original_dir",
        help="Original directory (duplicates to remove)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only show what would be deleted, don't actually delete"
    )
    parser.add_argument(
        "--cleanup-empty",
        action="store_true",
        help="Remove empty directories after cleanup"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of parallel workers (default: 4)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose output"
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    main_dir = Path(args.main_dir)
    original_dir = Path(args.original_dir)

    if not main_dir.exists():
        logger.error(f"Main directory does not exist: {main_dir}")
        sys.exit(1)

    if not original_dir.exists():
        logger.error(f"Original directory does not exist: {original_dir}")
        sys.exit(1)

    # Find duplicates
    print(f"\nComparing files...")
    print(f"  Main:     {main_dir}")
    print(f"  Original: {original_dir}")
    print()

    duplicates, not_found, different = find_duplicates(main_dir, original_dir, args.workers)

    # Summary
    print("\n" + "=" * 60)
    print("Comparison Results")
    print("=" * 60)
    print(f"Identical (duplicates):     {len(duplicates)}")
    print(f"Only in original:           {len(not_found)}")
    print(f"Different content:          {len(different)}")
    print("=" * 60)

    if not duplicates:
        print("\nNo duplicates found.")
        return

    # Calculate space that would be freed
    total_size = sum(orig.stat().st_size for _, orig in duplicates)
    print(f"\nSpace to be freed: {format_bytes(total_size)}")

    if args.dry_run:
        print("\n[DRY RUN] Would remove the following files:")
        for main_file, orig_file in duplicates[:20]:
            print(f"  {orig_file}")
        if len(duplicates) > 20:
            print(f"  ... and {len(duplicates) - 20} more")
    else:
        print(f"\nRemoving {len(duplicates)} duplicate files...")
        stats = remove_duplicates(duplicates, dry_run=False)

        print("\n" + "=" * 60)
        print("Removal Summary")
        print("=" * 60)
        print(f"Files removed:    {stats['removed']}")
        print(f"Failed:           {stats['failed']}")
        print(f"Space freed:      {format_bytes(stats['bytes_freed'])}")
        print("=" * 60)

        if args.cleanup_empty:
            print("\nCleaning up empty directories...")
            removed_dirs = cleanup_empty_dirs(original_dir, dry_run=False)
            print(f"Removed {removed_dirs} empty directories")


if __name__ == "__main__":
    main()
