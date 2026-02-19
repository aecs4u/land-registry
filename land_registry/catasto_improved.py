import os
import zipfile
import subprocess
import shutil
from pathlib import Path
from tqdm import tqdm
import sys


def check_dependencies():
    """Checks for the presence of required command-line tools."""
    try:
        # Check for ogr2ogr by running a simple, harmless command
        subprocess.run(["ogr2ogr", "--version"], check=True, capture_output=True)
        print("✅ Required dependency 'ogr2ogr' found.")
    except (subprocess.CalledProcessError, FileNotFoundError):
        print(
            "❌ Error: 'ogr2ogr' command not found. Please install GDAL/OGR and ensure it's in your system's PATH."
        )
        print(
            "💡 On Ubuntu/Debian, you can install it with: sudo apt-get install gdal-bin"
        )
        sys.exit(1)


def extract_zip_recursively(zip_path, extract_to):
    """
    Recursively extracts files from a zip archive.

    If a nested zip is found, it is extracted into a new directory
    within the current level, named after the nested zip file itself.
    """

    Path(extract_to).mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        zip_ref.extractall(extract_to)

    for root, _, files in os.walk(extract_to):
        for file in files:
            if file.endswith(".zip"):
                nested_zip_path = os.path.join(root, file)

                # Create a new directory for the nested zip's contents
                nested_extract_dir = os.path.join(root, file.replace(".zip", ""))

                print(
                    f"➡️ Found nested archive: '{nested_zip_path}'. Extracting to '{nested_extract_dir}'..."
                )

                # Recursively call the function for the nested zip
                extract_zip_recursively(nested_zip_path, nested_extract_dir)

                # Delete the original nested zip file after extraction
                os.remove(nested_zip_path)


def convert_gml_to_gpkg(gml_file, output_gpkg):
    """Converts a GML file to a GPKG file using GDAL."""
    ogr_command = ["ogr2ogr", "-f", "GPKG", output_gpkg, gml_file]

    try:
        subprocess.run(ogr_command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Error converting '{gml_file}': {e.stderr}")
    except FileNotFoundError:
        print(
            "\n❌ Error: 'ogr2ogr' command not found. This should have been caught by the pre-flight check."
        )


def main(initial_zip_file, final_output_dir):
    """Orchestrates the extraction and conversion process."""
    check_dependencies()

    temp_extract_dir = "temp_extracted_files"

    # 1. Start or resume extraction
    print(f"➡️ Starting or resuming recursive extraction from '{initial_zip_file}'...")
    extract_zip_recursively(initial_zip_file, temp_extract_dir)
    print("✅ Extraction complete.")

    # 2. Find and get the list of all .gml files to show total progress
    gml_files = []
    for root, _, files in os.walk(temp_extract_dir):
        for file in files:
            if file.endswith(".gml"):
                gml_files.append(os.path.join(root, file))

    # 3. Convert .gml files with a progress bar, preserving structure
    print(f"➡️ Converting .gml files to .gpkg in '{final_output_dir}'...")

    with tqdm(total=len(gml_files), desc="Converting GML", unit="file") as pbar:
        for gml_path in gml_files:
            relative_path = os.path.relpath(gml_path, temp_extract_dir)
            base_dir = os.path.dirname(relative_path)
            file_name_gpkg = os.path.basename(relative_path).replace(".gml", ".gpkg")
            output_dir = os.path.join(final_output_dir, base_dir)
            output_gpkg_path = os.path.join(output_dir, file_name_gpkg)

            # --- Checkpoint: Skip if output file already exists ---
            if Path(output_gpkg_path).exists():
                print(f"  Skipping '{relative_path}', already converted.")
                pbar.update(1)
                continue

            Path(output_dir).mkdir(parents=True, exist_ok=True)

            convert_gml_to_gpkg(gml_path, output_gpkg_path)
            pbar.update(1)

    print("✅ Conversion complete.")

    print(
        "💡 Process completed. The temporary directory is kept for potential restarts. Run 'cleanup_temp_dir()' when done."
    )


def cleanup_temp_dir():
    """Manually clean up the temporary extraction directory."""
    temp_extract_dir = "temp_extracted_files"
    if os.path.exists(temp_extract_dir):
        print("➡️ Cleaning up temporary files...")
        shutil.rmtree(temp_extract_dir)
        print("✅ Cleanup complete.")
    else:
        print("No temporary directory found to clean.")


# --- Usage Example ---
if __name__ == "__main__":
    initial_zip = "ITALIA.zip"
    final_output = "qgis"
    main(initial_zip, final_output)

    # To clean up the temporary directory after the script runs,
    # you can call this function from your terminal or another script:
    # cleanup_temp_dir()
