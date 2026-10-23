import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box, mapping

from land_registry import nightlights


_TAGS = {
    "product": "VNP46A4",
    "collection": "5200",
    "dataset_version": "VNP46A4.002",
    "observation_year": "2025",
    "imported_at": "2026-10-16T00:00:00+00:00",
    "source_doi": "https://doi.org/10.5067/VIIRS/VNP46A4.002",
    "radiance_units": "nW cm-2 sr-1",
    "radiance_scale_factor_applied": "0.1",
    "radiance_offset_applied": "0.0",
    "quality_policy": "NASA quality flag 0 only; poor, gap-filled and fill-value cells are nodata",
}


def _write_raster(path, pixels, *, tags=True):
    values = np.asarray(pixels, dtype="float32")
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=values.shape[0],
        width=values.shape[1],
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(10, 46, 0.1, 0.1),
        nodata=-999.9,
    ) as dataset:
        dataset.write(values, 1)
        if tags:
            dataset.update_tags(**_TAGS)
    return path


def test_good_quality_pixel_mean_and_raster_provenance(tmp_path, monkeypatch):
    path = _write_raster(tmp_path / "VNP46A4_2025_italy.tif", [[15, 25], [35, 45]])
    monkeypatch.setattr(nightlights, "_raster_path", lambda: path)

    status = nightlights.nightlights_store_status()
    result = nightlights.get_parcel_nightlights(mapping(box(10.01, 45.91, 10.19, 45.99)))

    assert status["available"] is True, status
    assert status["data_vintage"] == "2025"
    assert result.get("matched") is True, result
    assert result["intensity_nw_cm2_sr"] == 20
    assert result["valid_pixel_count"] == 2
    assert result["match_method"] == "parcel_pixel_mean"


def test_subpixel_parcel_uses_representative_point_pixel(tmp_path, monkeypatch):
    path = _write_raster(tmp_path / "VNP46A4_2025_italy.tif", [[15, 25], [35, 45]])
    monkeypatch.setattr(nightlights, "_raster_path", lambda: path)

    result = nightlights.get_parcel_nightlights(mapping(box(10.01, 45.91, 10.04, 45.94)))

    assert result.get("matched") is True, result
    assert result["intensity_nw_cm2_sr"] == 15
    assert result["match_method"] == "representative_point_pixel"
    assert result["coverage_status"] == "partial_sampling"


def test_bad_quality_pixels_are_unmatched_not_zero(tmp_path, monkeypatch):
    path = _write_raster(tmp_path / "VNP46A4_2025_italy.tif", [[-999.9, -999.9], [-999.9, -999.9]])
    monkeypatch.setattr(nightlights, "_raster_path", lambda: path)

    result = nightlights.get_parcel_nightlights(mapping(box(10.01, 45.91, 10.09, 45.99)))

    assert result["available"] is True, result
    assert result["matched"] is False
    assert result["reason"] == "no_good_quality_pixel"
    assert "intensity_nw_cm2_sr" not in result


def test_outside_raster_coverage_is_distinct(tmp_path, monkeypatch):
    path = _write_raster(tmp_path / "VNP46A4_2025_italy.tif", [[15, 25], [35, 45]])
    monkeypatch.setattr(nightlights, "_raster_path", lambda: path)

    result = nightlights.get_parcel_nightlights(mapping(box(20, 40, 21, 41)))

    assert result["available"] is True
    assert result["matched"] is False
    assert result["reason"] == "outside_raster_coverage"


def test_raster_without_required_provenance_is_unavailable(tmp_path, monkeypatch):
    path = _write_raster(tmp_path / "VNP46A4_2025_italy.tif", [[15, 25], [35, 45]], tags=False)
    monkeypatch.setattr(nightlights, "_raster_path", lambda: path)

    status = nightlights.nightlights_store_status()
    result = nightlights.get_parcel_nightlights(mapping(box(10.01, 45.91, 10.09, 45.99)))

    assert status["available"] is False
    assert status["reason"] == "missing_required_metadata"
    assert result["available"] is False
    assert result["reason"] == "missing_required_metadata"
    assert {"observation_year", "quality_policy", "radiance_scale_factor_applied"}.issubset(
        status["missing_metadata"]
    )
