#!/usr/bin/env python3
"""
Identify corrupted cadastral files and re-extract them from archives.

This script:
1. Scans GML files to identify corrupted ones (I/O errors, unreadable)
2. Finds corresponding ZIP archives
3. Re-extracts the corrupted files

Usage:
    python scripts/repair_corrupted_files.py /path/to/ITALIA --archive-dir /path/to/archives

Example:
    python scripts/repair_corrupted_files.py /mnt/mobile/data/catasto/original/ITALIA
"""

import os
import sys
import argparse
import logging
import zipfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def check_file_readable(file_path: Path) -> tuple[bool, str]:
    """
    Check if a file is readable (not corrupted).

    Returns:
        Tuple of (is_readable, error_message)
    """
    try:
        # Try to read first few KB to detect I/O errors
        with open(file_path, 'rb') as f:
            f.read(8192)

        # For GML files, also try to parse as XML
        if file_path.suffix.lower() == '.gml':
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read(1000)
                if not content.strip().startswith('<?xml') and not content.strip().startswith('<'):
                    return False, "Not valid XML/GML content"

        return True, ""
    except IOError as e:
        return False, f"I/O error: {e}"
    except UnicodeDecodeError as e:
        return False, f"Encoding error: {e}"
    except Exception as e:
        return False, f"Error: {e}"


def find_corrupted_files(source_dir: Path, workers: int = 4) -> list[Path]:
    """
    Scan directory for corrupted GML files.

    Args:
        source_dir: Directory to scan
        workers: Number of parallel workers

    Returns:
        List of corrupted file paths
    """
    gml_files = list(source_dir.rglob("*.gml")) + list(source_dir.rglob("*.GML"))
    logger.info(f"Scanning {len(gml_files)} GML files for corruption...")

    corrupted = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_file = {
            executor.submit(check_file_readable, f): f
            for f in gml_files
        }

        for i, future in enumerate(as_completed(future_to_file), 1):
            file_path = future_to_file[future]
            try:
                is_readable, error = future.result()
                if not is_readable:
                    logger.warning(f"Corrupted: {file_path.name} - {error}")
                    corrupted.append(file_path)
            except Exception as e:
                logger.error(f"Error checking {file_path}: {e}")
                corrupted.append(file_path)

            if i % 1000 == 0:
                logger.info(f"Progress: {i}/{len(gml_files)} files checked, {len(corrupted)} corrupted")

    return corrupted


def find_archive_for_file(file_path: Path, archive_dirs: list[Path]) -> Path | None:
    """
    Find the ZIP archive that contains the given file.

    Args:
        file_path: Path to the corrupted file
        archive_dirs: List of directories to search for archives

    Returns:
        Path to the archive, or None if not found
    """
    # Extract comune code from path (e.g., E045_GIOVE)
    comune_folder = file_path.parent.name
    comune_code = comune_folder.split("_")[0] if "_" in comune_folder else comune_folder

    # Try different archive naming patterns
    patterns = [
        f"{comune_code}*.zip",
        f"{comune_code}*.ZIP",
        f"*{comune_code}*.zip",
        f"*{comune_code}*.ZIP",
        f"{comune_folder}*.zip",
        f"{comune_folder}*.ZIP",
    ]

    for archive_dir in archive_dirs:
        if not archive_dir.exists():
            continue

        # Search in the same relative path structure
        rel_parts = []
        try:
            # Get region/province from path
            for part in file_path.parts:
                if part in ["ITALIA", "ITALIA_converted"]:
                    break
                rel_parts.append(part)

            # Look in region/province subdirectories
            for parent in [archive_dir] + list(archive_dir.rglob("*")):
                if not parent.is_dir():
                    continue
                for pattern in patterns:
                    matches = list(parent.glob(pattern))
                    if matches:
                        return matches[0]
        except Exception:
            pass

    return None


def extract_file_from_archive(archive_path: Path, target_file: Path, extract_dir: Path) -> bool:
    """
    Extract a specific file from a ZIP archive.

    Args:
        archive_path: Path to the ZIP archive
        target_file: Path to the file to extract (used to identify the file in archive)
        extract_dir: Directory to extract to

    Returns:
        True if successful
    """
    target_name = target_file.name

    try:
        with zipfile.ZipFile(archive_path, 'r') as zf:
            # Find matching file in archive
            for name in zf.namelist():
                if name.endswith(target_name) or Path(name).name == target_name:
                    # Extract to target location
                    logger.info(f"Extracting {name} from {archive_path.name}")

                    # Read from archive and write to target
                    with zf.open(name) as src:
                        content = src.read()

                    target_file.parent.mkdir(parents=True, exist_ok=True)
                    with open(target_file, 'wb') as dst:
                        dst.write(content)

                    return True

            logger.warning(f"File {target_name} not found in archive {archive_path}")
            return False

    except zipfile.BadZipFile:
        logger.error(f"Bad ZIP file: {archive_path}")
        return False
    except Exception as e:
        logger.error(f"Error extracting from {archive_path}: {e}")
        return False


def repair_from_converted(corrupted_file: Path, source_dir: Path) -> bool:
    """
    Try to repair by copying from converted GPKG directory.

    Since GPKG files might be intact, we can use geopandas to read GPKG
    and write back to GML.

    Args:
        corrupted_file: Path to corrupted GML file
        source_dir: Source directory root

    Returns:
        True if repaired
    """
    try:
        import geopandas as gpd

        # Find corresponding GPKG
        converted_base = Path(str(source_dir) + "_converted")
        if not converted_base.exists():
            return False

        rel_path = corrupted_file.relative_to(source_dir)
        gpkg_path = converted_base / rel_path.with_suffix('.gpkg')

        if not gpkg_path.exists():
            return False

        # Read GPKG and write to GML
        logger.info(f"Repairing {corrupted_file.name} from GPKG...")
        gdf = gpd.read_file(gpkg_path)

        # Write back to GML
        gdf.to_file(corrupted_file, driver='GML')
        logger.info(f"Repaired: {corrupted_file.name}")
        return True

    except ImportError:
        logger.warning("geopandas not available for GPKG repair")
        return False
    except Exception as e:
        logger.error(f"Failed to repair from GPKG: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Identify and repair corrupted cadastral files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Scan for corrupted files
  python repair_corrupted_files.py /path/to/ITALIA --scan-only

  # Repair from GPKG converted files
  python repair_corrupted_files.py /path/to/ITALIA --repair-from-gpkg

  # Repair from ZIP archives
  python repair_corrupted_files.py /path/to/ITALIA --archive-dir /path/to/archives
        """
    )
    parser.add_argument(
        "source",
        help="Source directory (ITALIA level)"
    )
    parser.add_argument(
        "--archive-dir",
        action="append",
        help="Directory containing ZIP archives (can be specified multiple times)"
    )
    parser.add_argument(
        "--scan-only",
        action="store_true",
        help="Only scan and list corrupted files, don't repair"
    )
    parser.add_argument(
        "--repair-from-gpkg",
        action="store_true",
        help="Try to repair corrupted GML files from converted GPKG files"
    )
    parser.add_argument(
        "--output",
        help="Write list of corrupted files to this file"
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

    source_dir = Path(args.source)

    if not source_dir.exists():
        logger.error(f"Source directory does not exist: {source_dir}")
        sys.exit(1)

    # Find corrupted files
    corrupted = find_corrupted_files(source_dir, args.workers)

    print(f"\n{'=' * 60}")
    print(f"Found {len(corrupted)} corrupted files")
    print('=' * 60)

    if args.output:
        output_path = Path(args.output)
        with open(output_path, 'w') as f:
            for path in corrupted:
                f.write(f"{path}\n")
        print(f"Corrupted file list written to: {output_path}")

    if corrupted:
        print("\nCorrupted files:")
        for path in corrupted[:20]:  # Show first 20
            print(f"  {path}")
        if len(corrupted) > 20:
            print(f"  ... and {len(corrupted) - 20} more")

    if args.scan_only:
        return

    # Attempt repairs
    repaired = 0
    failed = 0

    if args.repair_from_gpkg:
        print(f"\nAttempting repair from GPKG files...")
        for path in corrupted:
            if repair_from_converted(path, source_dir):
                repaired += 1
            else:
                failed += 1

    elif args.archive_dir:
        archive_dirs = [Path(d) for d in args.archive_dir]
        print(f"\nAttempting repair from archives in: {archive_dirs}")

        for path in corrupted:
            archive = find_archive_for_file(path, archive_dirs)
            if archive:
                if extract_file_from_archive(archive, path, path.parent):
                    repaired += 1
                else:
                    failed += 1
            else:
                logger.warning(f"No archive found for: {path.name}")
                failed += 1

    if repaired or failed:
        print(f"\n{'=' * 60}")
        print(f"Repair Summary")
        print('=' * 60)
        print(f"Repaired:  {repaired}")
        print(f"Failed:    {failed}")
        print('=' * 60)


if __name__ == "__main__":
    main()
