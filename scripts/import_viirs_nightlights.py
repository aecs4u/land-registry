#!/usr/bin/env python3
"""Prepare an Italy-only NASA Black Marble VNP46A4 annual raster.

The command is plan-only by default. ``--apply`` downloads only the four
10-degree tiles intersecting Italy and builds a quality-filtered GeoTIFF. It
requires ``LAADS_DAAC_TOKEN`` from an authorized Earthdata/LAADS account.
Alternatively, ``--build-only`` builds from four already-downloaded files.
"""

from __future__ import annotations

import argparse
import html.parser
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import Request, urlopen

PRODUCT = "VNP46A4"
COLLECTION = "5200"
DATASET_VERSION = "VNP46A4.002"
DOI = "https://doi.org/10.5067/VIIRS/VNP46A4.002"
ARCHIVE_ROOT = "https://ladsweb.modaps.eosdis.nasa.gov/api/v2/content/archives/"
ITALY_TILES = ((18, 4), (19, 4), (18, 5), (19, 5))
RADIANCE_LAYER = "NearNadir_Composite_Snow_Free"
QUALITY_LAYER = "NearNadir_Composite_Snow_Free_Quality"
FILL_VALUE = -999.9
RADIANCE_SCALE_FACTOR = 0.1
RADIANCE_OFFSET = 0.0


class _Links(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.hrefs.append(href)


def _request(url: str, token: str | None = None) -> Request:
    headers = {"User-Agent": "land-registry-viirs-import/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return Request(url, headers=headers)


def _files_for_year(year: int, token: str | None) -> dict[tuple[int, int], str]:
    directory = f"allData/{COLLECTION}/{PRODUCT}/{year}/001/"
    url = urljoin(ARCHIVE_ROOT, directory)
    with urlopen(_request(url, token), timeout=45) as response:
        page = response.read().decode("utf-8", errors="replace")
    links = _Links()
    links.feed(page)
    pattern = re.compile(
        rf"^{PRODUCT}\.A{year}\d{{3}}\.h(?P<h>\d{{2}})v(?P<v>\d{{2}})\.002\..+\.h5$",
        re.IGNORECASE,
    )
    result: dict[tuple[int, int], str] = {}
    for href in links.hrefs:
        filename = Path(unquote(urlparse(href).path)).name
        match = pattern.fullmatch(filename)
        if not match:
            continue
        tile = (int(match.group("h")), int(match.group("v")))
        if tile in ITALY_TILES:
            result[tile] = urljoin(url, href)
    return result


def _download(url: str, destination: Path, token: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size > 1_000_000:
        print(f"Already downloaded: {destination.name}")
        return
    temporary = destination.with_suffix(destination.suffix + ".part")
    request = _request(url, token)
    try:
        with urlopen(request, timeout=120) as response, temporary.open("wb") as output:
            first = response.read(1024)
            if first.lstrip().startswith((b"<!DOCTYPE html", b"<html", b"{\"error")):
                raise RuntimeError(f"LAADS returned an error page for {destination.name}")
            output.write(first)
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        if temporary.stat().st_size <= 1_000_000:
            raise RuntimeError(f"Downloaded file is unexpectedly small: {destination.name}")
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _tile_from_filename(path: Path, year: int) -> tuple[int, int] | None:
    match = re.search(rf"{PRODUCT}\.A{year}\d{{3}}\.h(\d{{2}})v(\d{{2}})\.002\..+\.h5$", path.name)
    return (int(match.group(1)), int(match.group(2))) if match else None


def _find_sds(subdatasets: list[str], base_name: str, *, optional_suffix: str = "") -> str:
    normalized_base = re.sub(r"[^a-z0-9]", "", base_name.casefold())
    normalized_suffix = re.sub(r"[^a-z0-9]", "", optional_suffix.casefold())
    candidates = []
    for subdataset in subdatasets:
        leaf = re.sub(r"[^a-z0-9]", "", unquote(subdataset.rsplit("/", 1)[-1]).casefold())
        if leaf == normalized_base or (normalized_suffix and leaf == normalized_base + normalized_suffix):
            candidates.append(subdataset)
    if len(candidates) != 1:
        raise RuntimeError(f"Expected one HDF5 subdataset named {base_name!r}; found {len(candidates)}")
    return candidates[0]


def _tile_transform(dataset, tile: tuple[int, int]):
    from affine import Affine
    from rasterio.transform import from_origin

    transform = dataset.transform
    meaningful = (
        dataset.crs is not None
        and abs(transform.a) > 0
        and abs(transform.e) > 0
        and (transform != Affine.identity())
    )
    if meaningful:
        return transform, dataset.crs
    horizontal, vertical = tile
    west = -180.0 + horizontal * 10.0
    north = 90.0 - vertical * 10.0
    pixel_width = 10.0 / dataset.width
    pixel_height = 10.0 / dataset.height
    return from_origin(west, north, pixel_width, pixel_height), "EPSG:4326"


def _build_tile(source_file: Path, output_file: Path, tile: tuple[int, int], year: int) -> None:
    import numpy as np
    import rasterio

    with rasterio.open(source_file) as container:
        subdatasets = list(container.subdatasets)
    radiance_sds = _find_sds(subdatasets, RADIANCE_LAYER)
    quality_sds = _find_sds(subdatasets, QUALITY_LAYER, optional_suffix="1")
    with rasterio.open(radiance_sds) as radiance_source, rasterio.open(quality_sds) as quality_source:
        if (radiance_source.width, radiance_source.height) != (quality_source.width, quality_source.height):
            raise RuntimeError(f"Radiance and quality dimensions do not match in {source_file.name}")
        if radiance_source.count < 1 or quality_source.count < 1:
            raise RuntimeError(f"Required subdataset is empty in {source_file.name}")
        transform, crs = _tile_transform(radiance_source, tile)
        if str(crs).upper() not in {"EPSG:4326", "OGC:CRS84"}:
            raise RuntimeError(f"Unexpected CRS {crs} in {source_file.name}")
        radiance = radiance_source.read(1, masked=True).astype("float32").filled(FILL_VALUE)
        quality = quality_source.read(1, masked=True)
        qa_valid = ~np.ma.getmaskarray(quality) & (np.asarray(quality.data) == 0)
        valid = qa_valid & np.isfinite(radiance) & (radiance >= 0)
        # LAADS stores the radiance SDS as scaled integers. Rasterio returns
        # the stored values, so apply the VNP46A4 scale and offset explicitly
        # before writing physical nW/(cm² sr) values.
        radiance[valid] = radiance[valid] * RADIANCE_SCALE_FACTOR + RADIANCE_OFFSET
        radiance[~valid] = FILL_VALUE
        profile = {
            "driver": "GTiff",
            "height": radiance_source.height,
            "width": radiance_source.width,
            "count": 1,
            "dtype": "float32",
            "crs": "EPSG:4326",
            "transform": transform,
            "nodata": FILL_VALUE,
            "compress": "DEFLATE",
            "predictor": 3,
            "tiled": True,
            "blockxsize": 256,
            "blockysize": 256,
            "BIGTIFF": "IF_SAFER",
        }
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(output_file, "w", **profile) as output:
            output.write(radiance, 1)
            output.set_band_description(1, "NearNadir_Composite_Snow_Free, good-quality pixels only")
            output.update_tags(
                dataset_version=DATASET_VERSION,
                observation_year=str(year),
                source_doi=DOI,
                product=PRODUCT,
                collection=COLLECTION,
                tile=f"h{tile[0]:02d}v{tile[1]:02d}",
                radiance_units="nW cm-2 sr-1",
                radiance_scale_factor_applied=str(RADIANCE_SCALE_FACTOR),
                radiance_offset_applied=str(RADIANCE_OFFSET),
                quality_policy="Quality flag 0 (good quality); all other flags and fill values are nodata",
                pixel_size="15 arc-second linear latitude/longitude grid",
            )


def _build_mosaic(year: int, source_dir: Path, output_path: Path) -> None:
    import rasterio
    from rasterio.merge import merge

    candidates: dict[tuple[int, int], Path] = {}
    for path in source_dir.rglob("*.h5"):
        tile = _tile_from_filename(path, year)
        if tile in ITALY_TILES:
            candidates[tile] = path
    missing = [tile for tile in ITALY_TILES if tile not in candidates]
    if missing:
        raise RuntimeError(f"Missing VNP46A4 tiles for {year}: {missing}")

    processed_dir = source_dir / "processed"
    tile_paths = []
    for tile in ITALY_TILES:
        output_tile = processed_dir / f"VNP46A4_{year}_h{tile[0]:02d}v{tile[1]:02d}.tif"
        _build_tile(candidates[tile], output_tile, tile, year)
        tile_paths.append(output_tile)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".part")
    temporary.unlink(missing_ok=True)
    datasets = [rasterio.open(path) for path in tile_paths]
    try:
        first = datasets[0]
        profile = first.profile.copy()
        profile.update(
            driver="GTiff",
            compress="DEFLATE",
            predictor=3,
            tiled=True,
            blockxsize=256,
            blockysize=256,
            BIGTIFF="IF_SAFER",
        )
        merge(datasets, dst_path=temporary, dst_kwds=profile, nodata=FILL_VALUE)
    finally:
        for dataset in datasets:
            dataset.close()
    with rasterio.open(temporary, "r+") as output:
        output.set_band_description(1, "NearNadir_Composite_Snow_Free, good-quality pixels only")
        output.update_tags(
            dataset_version=DATASET_VERSION,
            observation_year=str(year),
            imported_at=datetime.now(timezone.utc).isoformat(),
            source_doi=DOI,
            product=PRODUCT,
            collection=COLLECTION,
            source_files=",".join(candidates[tile].name for tile in ITALY_TILES),
            source_tiles=",".join(f"h{h:02d}v{v:02d}" for h, v in ITALY_TILES),
            radiance_units="nW cm-2 sr-1",
            radiance_scale_factor_applied=str(RADIANCE_SCALE_FACTOR),
            radiance_offset_applied=str(RADIANCE_OFFSET),
            quality_policy="NASA quality flag 0 only; poor, gap-filled and fill-value cells are nodata",
            pixel_size="15 arc-second linear latitude/longitude grid",
        )
    temporary.replace(output_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=datetime.now().year - 1)
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("VIIRS_NIGHTLIGHTS_DATA_DIR", "/data/viirs/nightlights")))
    parser.add_argument("--apply", action="store_true", help="Download the four Italy tiles and build the raster")
    parser.add_argument("--build-only", action="store_true", help="Build from four already-downloaded HDF5 files")
    args = parser.parse_args()
    if args.year < 2012 or args.year > datetime.now().year:
        parser.error("--year must be 2012 or later and cannot be in the future")
    if args.apply and args.build_only:
        parser.error("Choose either --apply or --build-only")

    source_dir = args.data_dir / "source" / str(args.year) / "001"
    output_path = args.data_dir / f"VNP46A4_{args.year}_italy.tif"
    if not args.apply and not args.build_only:
        print(f"Plan: NASA {DATASET_VERSION}, observation year {args.year}")
        print("Tiles: " + ", ".join(f"h{h:02d}v{v:02d}" for h, v in ITALY_TILES))
        print(f"Source directory: {source_dir}")
        print(f"Output raster: {output_path}")
        print("No files were downloaded or written. Pass --apply to fetch/build or --build-only for local HDF5 files.")
        return 0

    try:
        if args.apply:
            token = os.environ.get("LAADS_DAAC_TOKEN", "").strip()
            if not token:
                raise RuntimeError("Set LAADS_DAAC_TOKEN to an authorized Earthdata/LAADS download token")
            files = _files_for_year(args.year, token)
            missing = [tile for tile in ITALY_TILES if tile not in files]
            if missing:
                raise RuntimeError(f"LAADS archive has no matching annual files for tiles: {missing}")
            for tile in ITALY_TILES:
                filename = Path(unquote(urlparse(files[tile]).path)).name
                _download(files[tile], source_dir / filename, token)
        _build_mosaic(args.year, source_dir, output_path)
        print(f"Built {output_path}")
        return 0
    except Exception as exc:
        print(f"VIIRS import failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
