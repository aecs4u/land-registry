#!/usr/bin/env python3
"""
Script to transform municipality_flags.json keys to match cadastral_structure.json format.
Converts from proper case with apostrophes to uppercase with backticks.
"""

import json
import os

def transform_municipality_key(key):
    """Transform municipality key from proper case to cadastral format."""
    # Convert to uppercase and replace apostrophes with backticks
    return key.upper().replace("'", "`")

def main():
    # Path to the data directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(script_dir, "..", "data")

    # Load municipality flags
    flags_file = os.path.join(data_dir, "municipality_flags.json")
    cadastral_file = os.path.join(data_dir, "cadastral_structure.json")

    if not os.path.exists(flags_file):
        print(f"Error: {flags_file} not found")
        return

    if not os.path.exists(cadastral_file):
        print(f"Error: {cadastral_file} not found")
        return

    # Load the original municipality flags
    with open(flags_file, 'r', encoding='utf-8') as f:
        original_flags = json.load(f)

    # Load cadastral structure to get the exact municipality names
    with open(cadastral_file, 'r', encoding='utf-8') as f:
        cadastral_data = json.load(f)

    # Collect all municipality names from cadastral data
    cadastral_municipalities = set()
    for region_name, region_data in cadastral_data.items():
        for province_code, province_data in region_data.items():
            for municipality_key, municipality_data in province_data.items():
                if isinstance(municipality_data, dict):
                    municipality_name = municipality_data.get('name', municipality_key)
                    cadastral_municipalities.add(municipality_name)

    print(f"Found {len(cadastral_municipalities)} municipalities in cadastral data")
    print(f"Found {len(original_flags)} municipalities in flags data")

    # Transform the flags dictionary
    transformed_flags = {}
    matched_count = 0
    unmatched_flags = []

    for original_key, flag_url in original_flags.items():
        # Transform the key to cadastral format
        transformed_key = transform_municipality_key(original_key)

        # Check if this transformed key exists in cadastral data
        if transformed_key in cadastral_municipalities:
            transformed_flags[transformed_key] = flag_url
            matched_count += 1
            print(f"✅ {original_key} → {transformed_key}")
        else:
            unmatched_flags.append((original_key, transformed_key))
            print(f"❌ {original_key} → {transformed_key} (not found in cadastral data)")

    print("\n📊 Summary:")
    print(f"   Matched: {matched_count}")
    print(f"   Unmatched: {len(unmatched_flags)}")
    print(f"   Total transformed: {len(transformed_flags)}")

    # Show some examples of unmatched entries
    if unmatched_flags:
        print("\n❌ Some unmatched examples:")
        for i, (orig, trans) in enumerate(unmatched_flags[:10]):
            print(f"   {orig} → {trans}")
        if len(unmatched_flags) > 10:
            print(f"   ... and {len(unmatched_flags) - 10} more")

    # Create backup of original file
    backup_file = flags_file + ".backup"
    if not os.path.exists(backup_file):
        with open(backup_file, 'w', encoding='utf-8') as f:
            json.dump(original_flags, f, indent=2, ensure_ascii=False)
        print(f"\n💾 Created backup: {backup_file}")

    # Write the transformed flags
    with open(flags_file, 'w', encoding='utf-8') as f:
        json.dump(transformed_flags, f, indent=2, ensure_ascii=False)

    print("\n✅ Successfully transformed municipality_flags.json")
    print(f"   Original entries: {len(original_flags)}")
    print(f"   Transformed entries: {len(transformed_flags)}")
    print(f"   Match rate: {(matched_count / len(original_flags) * 100):.1f}%")

if __name__ == "__main__":
    main()