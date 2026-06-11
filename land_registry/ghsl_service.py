"""
GHSL (Global Human Settlement Layer) service.

Two enrichment layers:
1. **DEGURBA raster** — samples GHS-SMOD TIFF at polygon centroids to add
   ``ghsl_class_code``, ``ghsl_class_label`` and ``urban_status``.
2. **UCDB spatial join** — joins parcels with the Urban Centre Database to add
   ``ucdb_name``, ``ucdb_pop_2025``, ``ucdb_area_km2``, ``ucdb_gdp_2020``.
"""

import logging
import re
import threading
import zipfile
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# GHSL SMOD class code constants (Level 2, 8-class)
# ---------------------------------------------------------------------------

GHSL_CLASS_LABELS = {
    30: "urban centre",
    23: "dense urban cluster",
    22: "semi-dense urban cluster",
    21: "suburban or peri-urban",
    13: "rural cluster",
    12: "low density rural",
    11: "very low density rural",
    10: "water",
}

URBAN_CODES_BROAD = {30, 23, 22, 21}
URBAN_CODES_STRICT = {30, 23, 22}


def classify_urban_status(code: int, mode: str = "broad") -> str:
    codes = URBAN_CODES_STRICT if mode == "strict" else URBAN_CODES_BROAD
    if code in codes:
        return "urban"
    if code in GHSL_CLASS_LABELS:
        return "not_urban"
    return "unknown"


# ---------------------------------------------------------------------------
# Raster discovery
# ---------------------------------------------------------------------------

_EPOCH_RE = re.compile(r"E(\d{4})")


def _find_tif_in_zip(zip_path: str) -> Optional[str]:
    """Return /vsizip/ path for the main .tif inside a GHSL zip archive."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            tifs = [n for n in zf.namelist() if n.lower().endswith(".tif") and not n.endswith(".ovr")]
            if not tifs:
                return None
            # Prefer the main file (no _DUC/_RC/_SDUC/_UC suffix)
            main = [t for t in tifs if "_DUC_" not in t and "_RC_" not in t
                    and "_SDUC_" not in t and "_UC_" not in t]
            chosen = main[0] if main else tifs[0]
            return f"/vsizip/{zip_path}/{chosen}"
    except Exception:
        return None


def _parse_epoch(name: str) -> int:
    m = _EPOCH_RE.search(name)
    return int(m.group(1)) if m else 0


def discover_raster(data_dir: str, preferred_epoch: int = 2025) -> Optional[str]:
    """Find the best GHSL/DEGURBA raster in *data_dir*.

    Prefers the epoch closest to *preferred_epoch*. Supports both bare .tif
    files and .tif inside .zip archives (via GDAL /vsizip/).
    """
    candidates: List[Tuple[int, str]] = []

    data_path = Path(data_dir)
    if not data_path.is_dir():
        logger.warning("GHSL data directory does not exist: %s", data_dir)
        return None

    for entry in data_path.iterdir():
        name = entry.name.upper()
        if "DEGURBA" not in name and "SMOD" not in name:
            continue
        epoch = _parse_epoch(name)
        if entry.suffix.lower() == ".tif":
            candidates.append((epoch, str(entry)))
        elif entry.suffix.lower() == ".zip":
            vsi = _find_tif_in_zip(str(entry))
            if vsi:
                candidates.append((epoch, vsi))

    if not candidates:
        logger.info("No GHSL/DEGURBA rasters found in %s", data_dir)
        return None

    # Sort by distance to preferred epoch (closest first)
    candidates.sort(key=lambda c: abs(c[0] - preferred_epoch))
    chosen = candidates[0]
    logger.info("Selected GHSL raster epoch=%d path=%s", chosen[0], chosen[1])
    return chosen[1]


# ---------------------------------------------------------------------------
# GHSLService
# ---------------------------------------------------------------------------

class GHSLService:
    """Thread-safe GHSL raster sampler with lazy initialization."""

    def __init__(self, data_dir: str = "/data/ghsl", raster_path: Optional[str] = None):
        self._data_dir = data_dir
        self._explicit_path = raster_path
        self._lock = threading.Lock()
        self._raster = None  # rasterio DatasetReader
        self._transformer = None  # pyproj Transformer (EPSG:4326 → raster CRS)
        self._raster_path: Optional[str] = None
        self._nodata: float = -200.0
        self._initialized = False

    # -- lazy init ----------------------------------------------------------

    def _ensure_open(self) -> bool:
        if self._initialized:
            return self._raster is not None
        with self._lock:
            if self._initialized:
                return self._raster is not None
            self._initialized = True
            try:
                import rasterio
                from pyproj import Transformer

                path = self._explicit_path or discover_raster(self._data_dir)
                if not path:
                    return False

                self._raster = rasterio.open(path)
                self._raster_path = path
                self._nodata = self._raster.nodata or -200.0
                self._transformer = Transformer.from_crs(
                    "EPSG:4326", self._raster.crs, always_xy=True
                )
                logger.info(
                    "GHSL raster opened: %s  CRS=%s  shape=%s",
                    path, self._raster.crs, self._raster.shape,
                )
                return True
            except Exception:
                logger.warning("Failed to open GHSL raster", exc_info=True)
                return False

    # -- public API ---------------------------------------------------------

    @property
    def available(self) -> bool:
        return self._ensure_open()

    @property
    def raster_path(self) -> Optional[str]:
        self._ensure_open()
        return self._raster_path

    def sample_points(self, lons: np.ndarray, lats: np.ndarray) -> np.ndarray:
        """Sample raster at WGS-84 lon/lat arrays. Returns int16 class codes."""
        if not self._ensure_open():
            return np.full(len(lons), -1, dtype=np.int16)

        xs, ys = self._transformer.transform(lons, lats)
        coords = list(zip(xs, ys))
        values = np.array([v[0] for v in self._raster.sample(coords)], dtype=np.int16)
        # Replace nodata with -1
        values[values == int(self._nodata)] = -1
        return values

    def enrich_geodataframe(self, gdf, mode: str = "broad"):
        """Add ghsl_class_code, ghsl_class_label, urban_status columns.

        Idempotent: only processes rows where ghsl_class_code is NaN/missing.
        Returns the (modified in-place) GeoDataFrame.
        """
        if gdf is None or gdf.empty:
            return gdf
        if not self._ensure_open():
            return gdf

        # Determine which rows need enrichment
        if "ghsl_class_code" in gdf.columns:
            mask = gdf["ghsl_class_code"].isna()
            if not mask.any():
                return gdf  # all rows already enriched
        else:
            mask = gdf.index == gdf.index  # all True

        import warnings
        subset = gdf.loc[mask]
        # Centroid in geographic CRS is fine — 1km raster resolution makes
        # sub-metre centroid error negligible.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            centroids = subset.geometry.centroid
        lons = centroids.x.values
        lats = centroids.y.values

        codes = self.sample_points(lons, lats)

        labels = np.array([GHSL_CLASS_LABELS.get(int(c), "unknown") for c in codes])
        statuses = np.array([classify_urban_status(int(c), mode) for c in codes])

        gdf.loc[mask, "ghsl_class_code"] = codes.astype(float)  # float to allow NaN
        gdf.loc[mask, "ghsl_class_label"] = labels
        gdf.loc[mask, "urban_status"] = statuses

        n_urban = (statuses == "urban").sum()
        n_not = (statuses == "not_urban").sum()
        n_unk = (statuses == "unknown").sum()
        logger.info(
            "GHSL enrichment: %d features (urban=%d, not_urban=%d, unknown=%d, mode=%s)",
            len(subset), n_urban, n_not, n_unk, mode,
        )
        return gdf

    def status_info(self) -> dict:
        """Return status dict for API /ghsl/status endpoint."""
        available = self._ensure_open()
        return {
            "available": available,
            "raster_path": self._raster_path,
            "crs": str(self._raster.crs) if self._raster else None,
            "shape": list(self._raster.shape) if self._raster else None,
            "data_dir": self._data_dir,
            "ucdb_available": self._ucdb is not None,
            "ucdb_italy_count": len(self._ucdb) if self._ucdb is not None else 0,
        }

    # ------------------------------------------------------------------
    # UCDB (Urban Centre Database) spatial join
    # ------------------------------------------------------------------

    _ucdb = None  # cached Italian urban centres GeoDataFrame (EPSG:4326)

    def _ensure_ucdb(self) -> bool:
        """Lazy-load Italian urban centres from UCDB GPKG inside ZIP."""
        if self._ucdb is not None:
            return True
        with self._lock:
            if self._ucdb is not None:
                return True
            try:
                import geopandas as _gpd

                ucdb_zip = None
                for entry in Path(self._data_dir).iterdir():
                    if "UCDB" in entry.name.upper() and "GLOBE" in entry.name.upper() and entry.suffix.lower() == ".zip":
                        # Prefer the main UCDB, not MTUC or OSM_COMPLETENESS
                        if "MTUC" not in entry.name.upper() and "COMPLETENESS" not in entry.name.upper():
                            ucdb_zip = str(entry)
                            break

                if not ucdb_zip:
                    logger.info("No UCDB GPKG found in %s", self._data_dir)
                    return False

                # Find the .gpkg inside the zip
                gpkg_name = None
                with zipfile.ZipFile(ucdb_zip) as zf:
                    for n in zf.namelist():
                        if n.lower().endswith(".gpkg"):
                            gpkg_name = n
                            break
                if not gpkg_name:
                    return False

                vsi_path = f"/vsizip/{ucdb_zip}/{gpkg_name}"
                layer = "GHS_UCDB_THEME_GENERAL_CHARACTERISTICS_GLOBE_R2024A"

                gdf = _gpd.read_file(vsi_path, layer=layer)
                # Strip BOM from column names and string values
                gdf.columns = [c.lstrip("\ufeff") for c in gdf.columns]
                for col in gdf.select_dtypes(include="object").columns:
                    gdf[col] = gdf[col].str.lstrip("\ufeff")

                # Filter to Italy and reproject to EPSG:4326
                italy = gdf[gdf["GC_CNT_GAD_2025"] == "Italy"].copy()
                italy = italy.to_crs("EPSG:4326")

                # Load GDP from socioeconomic layer
                try:
                    se = _gpd.read_file(vsi_path, layer="GHS_UCDB_THEME_SOCIOECONOMIC_GLOBE_R2024A")
                    se.columns = [c.lstrip("\ufeff") for c in se.columns]
                    se_italy = se[se["ID_UC_G0"].isin(italy["ID_UC_G0"])][
                        ["ID_UC_G0", "SC_GDP_AVG_2020"]
                    ].copy()
                    italy = italy.merge(se_italy, on="ID_UC_G0", how="left")
                except Exception:
                    italy["SC_GDP_AVG_2020"] = None

                # Rename for clarity
                italy = italy.rename(columns={
                    "GC_UCN_MAI_2025": "ucdb_name",
                    "GC_POP_TOT_2025": "ucdb_pop_2025",
                    "GC_UCA_KM2_2025": "ucdb_area_km2",
                    "SC_GDP_AVG_2020": "ucdb_gdp_2020",
                    "ID_UC_G0": "ucdb_id",
                })
                # Keep only the columns we'll join
                keep = ["ucdb_id", "ucdb_name", "ucdb_pop_2025", "ucdb_area_km2", "ucdb_gdp_2020", "geometry"]
                self._ucdb = italy[[c for c in keep if c in italy.columns]].copy()
                logger.info("UCDB loaded: %d Italian urban centres", len(self._ucdb))
                return True
            except Exception:
                logger.warning("Failed to load UCDB", exc_info=True)
                return False

    def enrich_ucdb(self, gdf):
        """Spatial-join parcels with UCDB urban centres.

        Adds ucdb_name, ucdb_pop_2025, ucdb_area_km2, ucdb_gdp_2020 columns.
        Parcels outside any urban centre get NaN.  Idempotent.
        """
        if gdf is None or gdf.empty:
            return gdf
        if not self._ensure_ucdb():
            return gdf

        # Skip if already enriched
        if "ucdb_name" in gdf.columns:
            mask = gdf["ucdb_name"].isna()
            if not mask.any():
                return gdf
        else:
            mask = gdf.index == gdf.index  # all True

        import geopandas as _gpd
        import warnings

        subset = gdf.loc[mask].copy()
        # Use centroid for point-in-polygon join
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            subset_pts = subset.copy()
            subset_pts.geometry = subset.geometry.centroid

        # Ensure CRS match (UCDB is EPSG:4326; cadastral data may be EPSG:6706)
        if subset_pts.crs and self._ucdb.crs and subset_pts.crs != self._ucdb.crs:
            subset_pts = subset_pts.to_crs(self._ucdb.crs)

        joined = _gpd.sjoin(
            subset_pts, self._ucdb, how="left", predicate="within"
        )
        # Drop duplicates (parcel on boundary of two UCs — keep first)
        joined = joined[~joined.index.duplicated(keep="first")]

        for col in ("ucdb_name", "ucdb_pop_2025", "ucdb_area_km2", "ucdb_gdp_2020"):
            if col in joined.columns:
                gdf.loc[mask, col] = joined[col].values

        n_matched = gdf.loc[mask, "ucdb_name"].notna().sum()
        logger.info("UCDB join: %d/%d parcels inside an urban centre", n_matched, mask.sum())
        return gdf

    def get_ucdb_italy(self):
        """Return the cached Italian UCDB GeoDataFrame (or None)."""
        if self._ensure_ucdb():
            return self._ucdb
        return None

    def close(self):
        with self._lock:
            if self._raster is not None:
                self._raster.close()
                self._raster = None
                self._initialized = False
