#!/usr/bin/env python3
"""
Builds a JSON mapping: { "<Municipality name>": "<raw image URL>" }
for Italian municipalities (comuni) with a flag image on Wikidata.

Outputs:
  - flags_it_municipalities.json
  - flags_it_missing.csv   (items with no flag image on Wikidata)
"""

import json
import csv
import time
import sys
from urllib.parse import quote
import requests

WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"

HEADERS = {
    "User-Agent": "it-municipal-flags-bot/1.0 (contact: your-email@example.com)"
}

# 1) Get all Italian comuni with P41 (flag image).
#    We also get labels in Italian or English as name fallbacks.
SPARQL = """
SELECT ?item ?itemLabel ?flagFile
WHERE {
  ?item wdt:P31 wd:Q747074 .        # instance of: comune of Italy
  ?item wdt:P41 ?flagFile .         # has flag image
  SERVICE wikibase:label { bd:serviceParam wikibase:language "it,en". }
}
"""

def wd_sparql(query):
    r = requests.get(WIKIDATA_SPARQL, params={"format": "json", "query": query}, headers=HEADERS, timeout=60)
    r.raise_for_status()
    return r.json()

def commons_raw_url_from_filename(filename):
    """
    Given a Commons filename like 'Flag_of_Ancona.svg', return the direct URL to the file.
    Prefer the 'url' returned by imageinfo. If multiple, pick the original.
    """
    # Use MediaWiki imageinfo to resolve the file to a raw URL
    # iiurlwidth/height omitted so we get the original file URL.
    params = {
        "action": "query",
        "titles": f"File:{filename}",
        "prop": "imageinfo",
        "iiprop": "url|mime|size",
        "format": "json"
    }
    r = requests.get(COMMONS_API, params=params, headers=HEADERS, timeout=60)
    r.raise_for_status()
    data = r.json()
    pages = data.get("query", {}).get("pages", {})
    if not pages:
        return None
    for _, page in pages.items():
        ii = page.get("imageinfo", [])
        if not ii:
            return None
        # choose the first entry; it's the original
        return ii[0].get("url")
    return None

def normalize_name(label: str) -> str:
    """
    Keep just the town name (no region/province).
    The SPARQL label is already just the municipality name (e.g., 'Ancona').
    Strip whitespace and standardize apostrophes.
    """
    name = (label or "").strip()
    name = name.replace("’", "'")
    return name

def main():
    print("Querying Wikidata (this may take ~10–30s depending on your connection)…", file=sys.stderr)
    data = wd_sparql(SPARQL)
    rows = data["results"]["bindings"]

    results = {}
    missing = []  # municipalities where we failed to resolve a raw URL (should be rare)

    # Deduplicate by name: if multiple flags exist (rare), keep the first resolved URL.
    seen = set()

    for i, b in enumerate(rows, 1):
        label = b.get("itemLabel", {}).get("value")
        commons_file = b.get("flagFile", {}).get("value")  # e.g., "Flag of Ancona.svg"

        name = normalize_name(label)
        if not name or not commons_file:
            continue
        # Extract filename part if a full URL appears (Wikidata stores as a Commons file name literal)
        # The flagFile is usually a filename (not URL). If it contains a slash, keep the last segment.
        filename = commons_file.split("/")[-1]

        if name in seen:
            continue

        try:
            url = commons_raw_url_from_filename(filename)
            if url:
                results[name] = url
                seen.add(name)
            else:
                missing.append([name, filename, "no_url_from_commons"])
        except Exception as e:
            missing.append([name, filename, f"error:{e}"])
            # be a good API citizen if throttled
            time.sleep(0.5)

        # Gentle throttling to respect Commons
        if i % 50 == 0:
            time.sleep(1.0)

    # Save JSON (sorted by key for reproducibility)
    with open("flags_it_municipalities.json", "w", encoding="utf-8") as f:
        json.dump(dict(sorted(results.items())), f, ensure_ascii=False, indent=2)

    # Save missing
    if missing:
        with open("flags_it_missing.csv", "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["name", "filename_or_literal", "note"])
            w.writerows(missing)

    print(f"✅ Done. Flags resolved: {len(results)}; Missing/errored: {len(missing)}", file=sys.stderr)
    print("Files written: flags_it_municipalities.json, flags_it_missing.csv", file=sys.stderr)

def wikidata_curl_2_json():
    data = json.load(open("../data/wikidata_flags.json"))
    out = {}
    for b in data["results"]["bindings"]:
        town = b["name"]["value"].replace("’","'").strip()
        filename = b["file"]["value"].split("/")[-1]  # e.g. Flag_of_Ancona.svg
        out[town] = f"https://commons.wikimedia.org/wiki/File:{filename}"
    json.dump(dict(sorted(out.items())), open("flags_it_municipalities_desc_urls.json","w"), ensure_ascii=False, indent=2)
    print("Wrote flags_it_municipalities_desc_urls.json with", len(out), "entries")


if __name__ == "__main__":
    # main()
    wikidata_curl_2_json()
