#!/usr/bin/env python3
"""
Recursively unzip Italian cadastral archives preserving nested folder structure.

Structure: ITALIA.zip -> REGIONE.zip -> PROVINCIA.zip -> COMUNE.zip -> .gml files
Output:    ITALIA/REGIONE/PROVINCIA/COMUNE/*.gml

Usage:
    python scripts/recursive_unzip.py /path/to/source [/path/to/output]

If output path is not specified, extracts to source directory.
"""

import os
import sys
import zipfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
import logging
from typing import Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class RecursiveUnzipper:
    """Recursively extract nested zip files into folder hierarchy."""

    def __init__(
        self,
        output_dir: Path,
        delete_zips: bool = False,
        max_depth: int = 10,
        parallel_workers: int = 4
    ):
        self.output_dir = Path(output_dir)
        self.delete_zips = delete_zips
        self.max_depth = max_depth
        self.parallel_workers = parallel_workers
        self.stats = {
            "zips_found": 0,
            "zips_extracted": 0,
            "files_extracted": 0,
            "errors": 0
        }

    def extract_zip_to_folder(
        self,
        zip_path: Path,
        parent_folder: Path,
        depth: int = 0
    ) -> None:
        """
        Extract a zip file, creating a folder named after the zip (without .zip).
        Recursively process any nested zip files.

        Args:
            zip_path: Path to the zip file
            parent_folder: Parent folder where to create the extraction folder
            depth: Current recursion depth
        """
        if depth > self.max_depth:
            logger.warning(f"Max depth {self.max_depth} reached at {zip_path.name}")
            return

        # Create folder named after zip file (e.g., VENETO.zip -> VENETO/)
        folder_name = zip_path.stem  # Remove .zip extension
        target_folder = parent_folder / folder_name
        target_folder.mkdir(parents=True, exist_ok=True)

        self.stats["zips_found"] += 1

        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                file_count = len(zf.namelist())
                logger.info(f"{'  ' * depth}Extracting {zip_path.name} ({file_count} files) -> {target_folder.relative_to(self.output_dir)}/")

                # Extract all contents to the target folder
                zf.extractall(target_folder)
                self.stats["zips_extracted"] += 1

                # Find and process nested zip files
                nested_zips = []
                for name in zf.namelist():
                    extracted_path = target_folder / name
                    if extracted_path.suffix.lower() == '.zip' and extracted_path.exists():
                        nested_zips.append(extracted_path)
                    elif extracted_path.is_file() and not name.endswith('.zip'):
                        self.stats["files_extracted"] += 1

                # Process nested zips (use parallel at depth 1 for regions)
                if nested_zips:
                    if depth == 0 and len(nested_zips) > 1 and self.parallel_workers > 1:
                        # Parallel extraction for top-level (regions)
                        self._extract_parallel(nested_zips, target_folder, depth + 1)
                    else:
                        # Sequential extraction for deeper levels
                        for nested_zip in nested_zips:
                            self.extract_zip_to_folder(nested_zip, target_folder, depth + 1)
                            # Delete nested zip after extraction if requested
                            if self.delete_zips and nested_zip.exists():
                                nested_zip.unlink()

            # Delete the original zip after successful extraction if requested
            if self.delete_zips and zip_path.exists():
                zip_path.unlink()
                logger.debug(f"{'  ' * depth}Deleted {zip_path.name}")

        except zipfile.BadZipFile:
            logger.error(f"{'  ' * depth}Bad zip file: {zip_path}")
            self.stats["errors"] += 1
        except PermissionError as e:
            logger.error(f"{'  ' * depth}Permission error: {zip_path}: {e}")
            self.stats["errors"] += 1
        except Exception as e:
            logger.error(f"{'  ' * depth}Error extracting {zip_path}: {e}")
            self.stats["errors"] += 1

    def _extract_parallel(self, zip_files: list[Path], parent_folder: Path, depth: int) -> None:
        """Extract multiple zip files in parallel."""
        logger.info(f"{'  ' * (depth-1)}Processing {len(zip_files)} archives in parallel...")

        with ThreadPoolExecutor(max_workers=self.parallel_workers) as executor:
            futures = {
                executor.submit(
                    self.extract_zip_to_folder, zf, parent_folder, depth
                ): zf for zf in zip_files
            }
            for future in as_completed(futures):
                zf = futures[future]
                try:
                    future.result()
                    # Delete after parallel extraction
                    if self.delete_zips and zf.exists():
                        zf.unlink()
                except Exception as e:
                    logger.error(f"Error processing {zf.name}: {e}")
                    self.stats["errors"] += 1

    def run(self, source: Path) -> dict:
        """
        Run the recursive unzip process.

        Args:
            source: Directory containing zip files, OR a single zip file

        Returns:
            Statistics dictionary
        """
        source = Path(source)

        if not source.exists():
            logger.error(f"Source does not exist: {source}")
            return {"error": "Source not found"}

        # Create output directory
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Handle both single zip file and directory of zips
        if source.is_file() and source.suffix.lower() == '.zip':
            # Single zip file provided
            zip_files = [source]
            logger.info(f"Processing single zip file: {source}")
        else:
            # Directory provided - find all zip files
            zip_files = list(source.glob("*.zip")) + list(source.glob("*.ZIP"))

        if not zip_files:
            logger.warning(f"No zip files found in {source}")
            return self.stats

        logger.info(f"Starting recursive unzip")
        logger.info(f"Output directory: {self.output_dir}")
        logger.info(f"Found {len(zip_files)} top-level zip file(s)")

        # Process each top-level zip
        for zip_file in zip_files:
            self.extract_zip_to_folder(zip_file, self.output_dir, depth=0)

        return self.stats


def main():
    parser = argparse.ArgumentParser(
        description="Recursively unzip Italian cadastral archives into nested folder structure"
    )
    parser.add_argument(
        "source",
        help="Source zip file or directory containing zip files"
    )
    parser.add_argument(
        "output",
        nargs="?",
        default=None,
        help="Output directory (defaults to source directory)"
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete zip files after extraction"
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=10,
        help="Maximum recursion depth (default: 10)"
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

    source = Path(args.source)
    output = Path(args.output) if args.output else source

    unzipper = RecursiveUnzipper(
        output_dir=output,
        delete_zips=args.delete,
        max_depth=args.max_depth,
        parallel_workers=args.workers
    )

    stats = unzipper.run(source)

    print("\n" + "=" * 60)
    print("Extraction Complete!")
    print("=" * 60)
    print(f"Zip archives found:     {stats.get('zips_found', 0)}")
    print(f"Zip archives extracted: {stats.get('zips_extracted', 0)}")
    print(f"Data files extracted:   {stats.get('files_extracted', 0)}")
    if stats.get('errors'):
        print(f"Errors:                 {stats['errors']}")
    print("=" * 60)
    print(f"\nOutput structure: {output}/ITALIA/REGION/PROVINCE/MUNICIPALITY/")


if __name__ == "__main__":
    main()
