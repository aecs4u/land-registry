"""Parcel-scale summaries from a locally prepared NASA Black Marble raster.

The VNP46A4 annual product is a roughly 460 m grid, so many cadastral parcels
are smaller than a pixel. The returned value is the mean of good-quality
pixel-centre samples inside the parcel, with a representative-point pixel used
when no pixel centre falls inside. The match method is returned explicitly.
"""

from __future__ import annotations

import math
import os
from datetime import datetime
from pathlib import Path
from typing import Any

_SOURCE = "NASA Black Marble VIIRS VNP46A4"
_SOURCE_URL = "https://doi.org/10.5067/VIIRS/VNP46A4.002"
_DATASET_VERSION = "VNP46A4.002"
_DEFAULT_DATA_DIR = Path("/data/viirs/nightlights")
_PRODUCT = "VNP46A4"
_COLLECTION = "5200"
_MAX_SAMPLED_PIXELS = 10_000
_QUALITY_POLICY = "Only pixels with NASA VNP46A4 quality flag 0 (good quality) are retained."


def _raster_path() -> Path | None:
    configured = os.environ.get("VIIRS_NIGHTLIGHTS_RASTER_PATH")
    if configured:
        return Path(configured).expanduser()
    data_dir = Path(os.environ.get("VIIRS_NIGHTLIGHTS_DATA_DIR", str(_DEFAULT_DATA_DIR))).expanduser()
    candidates = sorted(data_dir.glob("VNP46A4_*_italy.tif"), reverse=True)
    return candidates[0] if candidates else None


def _metadata() -> dict[str, Any]:
    return {
        "source": _SOURCE,
        "source_url": _SOURCE_URL,
        "product": _PRODUCT,
        "collection": _COLLECTION,
        "dataset_version": _DATASET_VERSION,
        "units": "nW/(cm² sr)",
        "spatial_resolution": "15 arc-second linear latitude/longitude grid (about 463 m at the equator)",
        "license": None,
        "attribution": (
            "NASA VIIRS Land Science Investigator-led Processing System, "
            "VNP46A4.002, NASA LAADS DAAC, https://doi.org/10.5067/VIIRS/VNP46A4.002"
        ),
        "quality_policy": _QUALITY_POLICY,
    }


def _missing_raster_metadata(dataset: Any, tags: dict[str, str]) -> list[str]:
    """Reject rasters that cannot prove product, scaling, QA, vintage and import provenance."""
    missing = []
    expected = {
        "product": _PRODUCT,
        "collection": _COLLECTION,
        "dataset_version": _DATASET_VERSION,
        "source_doi": _SOURCE_URL,
        "radiance_units": "nW cm-2 sr-1",
        "radiance_scale_factor_applied": "0.1",
        "radiance_offset_applied": "0.0",
    }
    for key, value in expected.items():
        if tags.get(key) != value:
            missing.append(key)

    year = tags.get("observation_year", "")
    try:
        numeric_year = int(year)
    except (TypeError, ValueError):
        numeric_year = 0
    if numeric_year < 2012 or numeric_year > datetime.now().year:
        missing.append("observation_year")

    imported_at = tags.get("imported_at", "")
    try:
        parsed_import_time = datetime.fromisoformat(imported_at.replace("Z", "+00:00"))
        if parsed_import_time.tzinfo is None:
            missing.append("imported_at_timezone")
    except (TypeError, ValueError):
        missing.append("imported_at")

    quality_policy = tags.get("quality_policy", "").casefold()
    if "0" not in quality_policy or "nodata" not in quality_policy:
        missing.append("quality_policy")
    if dataset.count != 1:
        missing.append("single_band_raster")
    if dataset.nodata is None or not math.isclose(float(dataset.nodata), -999.9, abs_tol=1e-4):
        missing.append("quality_filtered_nodata")
    return missing


def nightlights_raster_marker() -> str:
    """Return a cheap cache marker for the configured annual raster."""
    path = _raster_path()
    if path is None:
        return "-"
    try:
        stat = path.stat()
        return f"{path}:{stat.st_size}:{stat.st_mtime_ns}"
    except OSError:
        return f"{path}:-"


def nightlights_store_status() -> dict[str, Any]:
    """Report whether a readable, tagged Italy raster is available."""
    path = _raster_path()
    base = _metadata()
    if path is None or not path.is_file():
        return {**base, "available": False, "reason": "not_built"}
    try:
        import rasterio

        with rasterio.open(path) as dataset:
            if dataset.count < 1 or dataset.crs is None or dataset.width < 1 or dataset.height < 1:
                return {**base, "available": False, "reason": "invalid_raster"}
            if dataset.crs.to_epsg() != 4326:
                return {**base, "available": False, "reason": "unsupported_raster_crs"}
            tags = dataset.tags()
            missing = _missing_raster_metadata(dataset, tags)
            if missing:
                return {
                    **base,
                    "available": False,
                    "reason": "missing_required_metadata",
                    "missing_metadata": missing,
                }
            year = tags["observation_year"]
            version = tags["dataset_version"]
            return {
                **base,
                "available": True,
                "reason": None,
                "dataset_version": version,
                "data_vintage": year,
                "updated_at": tags["imported_at"],
                "width": dataset.width,
                "height": dataset.height,
                "crs": dataset.crs.to_string(),
            }
    except Exception as exc:  # optional deployment raster may be corrupt or unreadable
        return {**base, "available": False, "reason": "probe_failed", "error": str(exc)[:240]}


def _unavailable(reason: str, **fields: Any) -> dict[str, Any]:
    return {
        **_metadata(),
        "available": False,
        "coverage": "unavailable",
        "coverage_status": reason,
        "reason": reason,
        **fields,
    }


def get_parcel_nightlights(geometry: dict[str, Any]) -> dict[str, Any]:
    """Return annual Black Marble radiance sampled over one parcel polygon."""
    from shapely.geometry import shape

    path = _raster_path()
    if path is None or not path.is_file():
        return _unavailable("not_built")
    try:
        parcel = shape(geometry.get("geometry") if geometry.get("type") == "Feature" else geometry)
        if parcel.is_empty or parcel.geom_type not in {"Polygon", "MultiPolygon"}:
            return _unavailable("parcel_polygon_unavailable")
        if not parcel.is_valid:
            parcel = parcel.buffer(0)
        if parcel.is_empty or parcel.area <= 0:
            return _unavailable("parcel_polygon_invalid")
    except Exception:
        return _unavailable("parcel_geometry_invalid")

    try:
        import numpy as np
        import rasterio
        from affine import Affine
        from rasterio.enums import Resampling
        from rasterio.features import geometry_mask
        from rasterio.windows import Window, from_bounds

        with rasterio.open(path) as dataset:
            tags = dataset.tags()
            missing_metadata = _missing_raster_metadata(dataset, tags)
            if missing_metadata:
                return _unavailable("missing_required_metadata", missing_metadata=missing_metadata)
            base = {
                **_metadata(),
                "dataset_version": tags["dataset_version"],
                "data_vintage": tags["observation_year"],
                "updated_at": tags["imported_at"],
            }
            if dataset.crs is None or dataset.crs.to_epsg() != 4326:
                return {**base, "available": False, "reason": "unsupported_raster_crs"}
            raw_window = from_bounds(*parcel.bounds, transform=dataset.transform)
            # Floor the origin and ceil the far edge. Rasterio's default
            # round_lengths floors widths and heights, which turns a valid
            # subpixel parcel into an empty window before point fallback runs.
            column_start = math.floor(raw_window.col_off)
            row_start = math.floor(raw_window.row_off)
            column_stop = math.ceil(raw_window.col_off + raw_window.width)
            row_stop = math.ceil(raw_window.row_off + raw_window.height)
            window = Window(
                column_start,
                row_start,
                column_stop - column_start,
                row_stop - row_start,
            )
            try:
                window = window.intersection(Window(0, 0, dataset.width, dataset.height))
            except rasterio.errors.WindowError:
                return {
                    **base,
                    "available": True,
                    "matched": False,
                    "coverage": "unknown",
                    "coverage_status": "outside_raster_coverage",
                    "reason": "outside_raster_coverage",
                }
            if window.width <= 0 or window.height <= 0:
                return {
                    **base,
                    "available": True,
                    "matched": False,
                    "coverage": "unknown",
                    "coverage_status": "outside_raster_coverage",
                    "reason": "outside_raster_coverage",
                }

            source_pixels = int(window.width * window.height)
            downsampled = source_pixels > _MAX_SAMPLED_PIXELS
            if downsampled:
                factor = math.sqrt(source_pixels / _MAX_SAMPLED_PIXELS)
                out_height = max(1, int(math.ceil(window.height / factor)))
                out_width = max(1, int(math.ceil(window.width / factor)))
                values = dataset.read(
                    1,
                    window=window,
                    out_shape=(out_height, out_width),
                    resampling=Resampling.average,
                    masked=True,
                )
            else:
                values = dataset.read(1, window=window, masked=True)
                out_height, out_width = values.shape

            transform = dataset.window_transform(window) @ Affine.scale(
                window.width / out_width, window.height / out_height
            )
            inside = geometry_mask(
                [parcel.__geo_interface__],
                out_shape=(out_height, out_width),
                transform=transform,
                all_touched=False,
                invert=True,
            )
            raw_values = np.asarray(values.data, dtype="float64")
            valid = inside & ~np.ma.getmaskarray(values) & np.isfinite(raw_values) & (raw_values >= 0)
            selected = raw_values[valid]
            match_method = "parcel_pixel_mean"
            if not selected.size:
                point = parcel.representative_point()
                row, column = dataset.index(point.x, point.y)
                if row < 0 or column < 0 or row >= dataset.height or column >= dataset.width:
                    return {
                        **base,
                        "available": True,
                        "matched": False,
                        "coverage": "unknown",
                        "coverage_status": "outside_raster_coverage",
                        "reason": "outside_raster_coverage",
                    }
                value = dataset.read(1, window=Window(column, row, 1, 1), masked=True)[0, 0]
                if np.ma.is_masked(value) or not np.isfinite(float(value)) or float(value) < 0:
                    return {
                        **base,
                        "available": True,
                        "matched": False,
                        "coverage": "unknown",
                        "coverage_status": "no_good_quality_pixel",
                        "reason": "no_good_quality_pixel",
                    }
                selected = np.asarray([float(value)], dtype="float64")
                match_method = "representative_point_pixel"

            radiance = float(np.mean(selected))
            selected_pixel_count = int(np.count_nonzero(inside))
            if match_method == "representative_point_pixel":
                selected_pixel_count = 1
            partial = downsampled or match_method != "parcel_pixel_mean" or selected.size < selected_pixel_count
            return {
                **base,
                "available": True,
                "matched": True,
                "intensity_nw_cm2_sr": round(radiance, 4),
                "units": "nW/(cm² sr)",
                "valid_pixel_count": int(selected.size),
                "sampled_pixel_count": selected_pixel_count,
                "coverage": "partial" if partial else "full",
                "coverage_status": "partial_sampling" if partial else "sampled",
                "match_method": match_method,
                "quality_policy": _QUALITY_POLICY,
                "reason": "pixel_grid_resolution_limit" if match_method == "representative_point_pixel" else None,
            }
    except Exception as exc:  # file format and optional deployment failures are returned to the panel
        return _unavailable("raster_query_failed", error=str(exc)[:240])

