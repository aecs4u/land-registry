import os
import zipfile
import subprocess
import shutil
from pathlib import Path
from tqdm import tqdm


def extract_zip_recursively(zip_path, extract_to):
    """Recursively extracts all files from a zip file, handling nested zips."""
    Path(extract_to).mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        zip_ref.extractall(extract_to)

    for root, dirs, files in os.walk(extract_to):
        for file in files:
            if file.endswith(".zip"):
                nested_zip_path = os.path.join(root, file)
                extract_zip_recursively(nested_zip_path, root)
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
            "\n❌ Error: 'ogr2ogr' command not found. Ensure GDAL is installed and in your system's PATH."
        )


def main(initial_zip_file, final_output_dir):
    """Orchestrates the extraction and conversion process."""
    temp_extract_dir = "temp_extracted_files"

    # 1. Extract files recursively
    print(f"➡️ Starting recursive extraction from '{initial_zip_file}'...")
    extract_zip_recursively(initial_zip_file, temp_extract_dir)
    print("✅ Extraction complete.")

    # 2. Find and get the list of all .gml files to show total progress
    gml_files = []
    for root, _, files in os.walk(temp_extract_dir):
        for file in files:
            if file.endswith(".gml"):
                gml_files.append(os.path.join(root, file))

    # 3. Convert .gml files with a progress bar
    print(f"➡️ Converting .gml files to .gpkg in '{final_output_dir}'...")
    Path(final_output_dir).mkdir(parents=True, exist_ok=True)

    with tqdm(total=len(gml_files), desc="Converting GML", unit="file") as pbar:
        for gml_path in gml_files:
            relative_path = os.path.relpath(gml_path, temp_extract_dir)
            output_gpkg_path = os.path.join(final_output_dir, relative_path).replace(
                ".gml", ".gpkg"
            )
            Path(output_gpkg_path).parent.mkdir(parents=True, exist_ok=True)
            convert_gml_to_gpkg(gml_path, output_gpkg_path)
            pbar.update(1)

    print("✅ Conversion complete.")

    # 4. Clean up the temporary directory
    print("➡️ Cleaning up temporary files...")
    shutil.rmtree(temp_extract_dir)
    print("✅ Cleanup complete.")


# --- Usage Example ---
if __name__ == "__main__":
    initial_zip = "ITALIA.zip"  # The name of your top-level zip file
    final_output = "qgis"  # The directory for the final GPKG files
    main(initial_zip, final_output)
