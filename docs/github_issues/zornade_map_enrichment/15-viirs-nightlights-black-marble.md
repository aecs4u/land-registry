## Summary

Add parcel-scale annual nighttime radiance from NASA Black Marble, with an
explicit quality filter, source vintage and coarse-grid sampling disclosure.

**Implementation status (2026-10-16):** the app has a plan-only/token-aware
Italy-tile importer that applies the `0.1` radiance scale factor, a
quality-filtered local GeoTIFF query, read-model block, lazy parcel-panel
section, `/api/v2` `nightlights` section and source catalog entry. No annual
raster is provisioned on the current host, so live parcel values and national
coverage have not been verified. The installed
`aecs4u_stats` package has no reusable Black Marble module; the current
importer/query should move upstream before another application consumes it.
The query now rejects rasters whose tags do not prove the expected product,
version, observation year, import time, physical units, applied scale and QA
policy.

## Source and contract

- Product: NASA Black Marble VNP46A4 Collection 2, version `VNP46A4.002`.
- Input: annual HDF5 product, four 10-degree tiles covering Italy
  (`h18v04`, `h19v04`, `h18v05`, `h19v05`).
- Data: `NearNadir_Composite_Snow_Free`; retain only QA flag `0` (good quality)
  and mask other flags plus fill values.
- LAADS stores this radiance layer with scale factor `0.1` and offset `0`.
  The importer applies both before writing physical values to the GeoTIFF.
- Units: nW/(cm² sr). Return the annual mean of good-quality pixel centres
  inside the parcel. If none fall inside, sample the pixel at the parcel's
  representative point and report that method.
- The 15 arc-second grid is about 463 m north-south. Many parcels are
  subpixel; radiance is not a parcel/building light measurement and does not
  support socioeconomic class inference by itself.
- Missing quality pixels and out-of-coverage parcels are unmatched, not zero.
- The source catalog cites NASA and links the NASA Earthdata data-use policy;
  the application does not assert a separate named licence.

## Provisioning

`scripts/import_viirs_nightlights.py --year <year>` is plan-only by default.
Use `--apply` with an authorized `LAADS_DAAC_TOKEN` to download and build, or
`--build-only` from the four HDF5 source tiles. The app reads the configured
`VIIRS_NIGHTLIGHTS_RASTER_PATH` or newest `VNP46A4_*_italy.tif` in
`VIIRS_NIGHTLIGHTS_DATA_DIR` (default `/data/viirs/nightlights`).

## Acceptance criteria

- [x] Synthetic GeoTIFF tests cover multi-pixel means, subpixel fallback,
  quality-filtered no-match, outside coverage and incomplete raster provenance.
  The subpixel case also guards against zero-sized windows from integer
  rounding.
- A provisioned annual raster reports product/version, observation year,
  import time, units, source attribution and quality policy.
- A parcel with multiple valid pixels reports their mean and sample count;
  subpixel parcels report the representative-point fallback explicitly.
- Invalid-quality, missing, outside-coverage and unavailable-raster states are
  distinct and never represented as zero.
- A raster without the expected VNP46A4.002 product, observation-year,
  import-time, unit, scale-factor, quality-policy or nodata metadata is
  reported unavailable rather than identified by filename alone.
- The panel and public API explain the raster's resolution and sampling limit.
- The API catalog documents stable fields, source metadata and method scope.
- Verify urban, rural, subpixel and outside-coverage parcels against a
  provisioned raster; verify the importer against an authorized LAADS archive.

## References

- [NASA LAADS VNP46A4 product page](https://ladsweb.modaps.eosdis.nasa.gov/missions-and-measurements/products/VNP46A4)
- [NASA LAADS VNP46A4 file specification](https://ladsweb.modaps.eosdis.nasa.gov/filespec/VIIRS/1/VNP46A4)
- [Black Marble Collection 2 user guide](https://landweb.modaps.eosdis.nasa.gov/data/userguide/BlackMarbleUserGuide_Collection2.0_20241203.pdf)
- [NASA Earthdata data-use policy](https://www.earthdata.nasa.gov/engage/open-data-services-software/data-use-policy)
- [LAADS API v2 quick start](https://ladsweb.modaps.eosdis.nasa.gov/tools-and-services/api-v2/quick-start-guide/)
