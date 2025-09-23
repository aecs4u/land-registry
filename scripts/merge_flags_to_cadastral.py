#!/usr/bin/env python3
"""
Script to merge municipality flags into cadastral_structure.json.
Adds flag_url field to each municipality entry where a matching flag exists.
"""

import json
import os

def normalize_name_for_matching(name):
    """Normalize municipality name for matching with flags."""
    # Convert to title case and replace backticks with apostrophes
    return name.title().replace('`', "'")

def main():
    # Path to the data directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(script_dir, "..", "data")

    # Load files
    flags_file = os.path.join(data_dir, "municipality_flags.json")
    cadastral_file = os.path.join(data_dir, "cadastral_structure.json")

    if not os.path.exists(flags_file):
        print(f"Error: {flags_file} not found")
        return

    if not os.path.exists(cadastral_file):
        print(f"Error: {cadastral_file} not found")
        return

    # Load the data
    with open(flags_file, 'r', encoding='utf-8') as f:
        municipality_flags = json.load(f)

    with open(cadastral_file, 'r', encoding='utf-8') as f:
        cadastral_data = json.load(f)

    print(f"Loaded {len(municipality_flags)} municipality flags")

    # Statistics
    total_municipalities = 0
    flags_added = 0
    unmatched_municipalities = []

    # Process each municipality in the cadastral data
    for region_name, region_data in cadastral_data.items():
        for province_code, province_data in region_data.items():
            for municipality_key, municipality_data in province_data.items():
                if isinstance(municipality_data, dict):
                    total_municipalities += 1
                    municipality_name = municipality_data.get('name', municipality_key)

                    # Normalize the name for matching
                    normalized_name = normalize_name_for_matching(municipality_name)

                    # Check if we have a flag for this municipality
                    if normalized_name in municipality_flags:
                        municipality_data['flag_url'] = municipality_flags[normalized_name]
                        flags_added += 1
                        print(f"✅ Added flag for: {municipality_name} → {normalized_name}")
                    else:
                        unmatched_municipalities.append((municipality_name, normalized_name))
                        print(f"❌ No flag found for: {municipality_name} → {normalized_name}")

    print(f"\n📊 Summary:")
    print(f"   Total municipalities: {total_municipalities}")
    print(f"   Flags added: {flags_added}")
    print(f"   Unmatched: {len(unmatched_municipalities)}")
    print(f"   Match rate: {(flags_added / total_municipalities * 100):.1f}%")

    # Show some examples of unmatched municipalities
    if unmatched_municipalities:
        print(f"\n❌ Some unmatched examples:")
        for i, (orig, normalized) in enumerate(unmatched_municipalities[:10]):
            print(f"   {orig} → {normalized}")
        if len(unmatched_municipalities) > 10:
            print(f"   ... and {len(unmatched_municipalities) - 10} more")

    # Create backup of original file
    backup_file = cadastral_file + ".backup"
    if not os.path.exists(backup_file):
        with open(backup_file, 'w', encoding='utf-8') as f:
            json.dump(cadastral_data, f, indent=2, ensure_ascii=False)
        print(f"\n💾 Created backup: {backup_file}")

    # Write the updated cadastral structure
    with open(cadastral_file, 'w', encoding='utf-8') as f:
        json.dump(cadastral_data, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Successfully updated cadastral_structure.json")
    print(f"   Added flag_url field to {flags_added} municipalities")

if __name__ == "__main__":
    main()