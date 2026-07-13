"""
Datashader-based tile generation service for high-performance visualization of massive cadastral datasets.

This service generates rasterized map tiles on-the-fly using datashader's GPU/CPU-accelerated aggregation,
enabling visualization of millions of cadastral parcels without client-side performance degradation.
"""

import logging
from collections import OrderedDict
from io import BytesIO
from pathlib import Path
from typing import Optional

import colorcet
import datashader as ds
import datashader.transfer_functions as tf
import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
from PIL import Image
from shapely.geometry import Point

log = logging.getLogger(__name__)


class DatashaderTileService:
    """
    Server-side tile generation using datashader for massive datasets.

    Provides on-demand map tile generation compatible with Leaflet TileLayer,
    supporting density heatmaps, categorical visualizations, and aggregated statistics.
    """

    def __init__(self, cadastral_db=None):
        """
        Initialize the datashader tile service.

        Args:
            cadastral_db: CadastralDatabase instance for querying parcel data
        """
        self.db = cadastral_db
        self.tile_size = 256  # Standard map tile size (256x256 pixels)

        # LRU tile cache: keys are (x, y, z, region, agg_type, colormap) or
        # ("boundary", layer_type, x, y, z) for generate_boundary_tile.
        self._tile_cache: OrderedDict = OrderedDict()
        self._tile_cache_max = 100  # max cached tiles (~25 MB at ~256 KB/tile)

        # layer_type ("map"/"ple") -> {region_slug: (minx, miny, maxx, maxy)} in the
        # source fgb CRS (EPSG:6706), lazily populated by _region_fgb_bounds() from
        # each file's header (cheap regardless of file size — used only to pick
        # which fgb file(s) to read).
        self._fgb_bounds: dict[str, dict[str, tuple]] = {}

        log.info("DatashaderTileService initialized with tile_size=%d", self.tile_size)

    def generate_tile(
        self,
        x: int,
        y: int,
        z: int,
        region: Optional[str] = None,
        agg_type: str = "count",
        colormap: str = "fire",
    ) -> bytes:
        """
        Generate a single map tile using datashader.

        Args:
            x: Tile X coordinate (TMS)
            y: Tile Y coordinate (TMS)
            z: Zoom level (0-18)
            region: Optional region filter for data query
            agg_type: Aggregation type - "count" (density), "mean", "sum"
            colormap: Colorcet color palette name (fire, viridis, blues, etc.)

        Returns:
            PNG tile image as bytes
        """
        cache_key = (x, y, z, region, agg_type, colormap)

        # Check LRU cache first
        if cache_key in self._tile_cache:
            self._tile_cache.move_to_end(cache_key)
            log.debug("Tile cache hit: %d/%d/%d", z, x, y)
            return self._tile_cache[cache_key]

        try:
            # Convert tile coordinates to lat/lon bounding box
            bounds = self._tile_to_bbox(x, y, z)

            # Query cadastral data within tile bounds
            if not self.db:
                log.warning("No database configured, returning empty tile")
                return self._empty_tile()

            from land_registry.cadastral_db import CadastralFilter

            filter_obj = CadastralFilter(
                regione=region,
                bbox=(bounds["west"], bounds["south"], bounds["east"], bounds["north"]),
            )

            gdf = self.db.query_parcels(filter_obj)

            if gdf is None or len(gdf) == 0:
                # Return transparent tile for empty areas
                return self._empty_tile()

            log.debug(
                "Tile %d/%d/%d: Loaded %d parcels for bounds %s",
                z,
                x,
                y,
                len(gdf),
                bounds,
            )

            # Convert polygons to point centroids for aggregation
            points_df = self._polygons_to_points(gdf)

            if len(points_df) == 0:
                return self._empty_tile()

            # Create datashader canvas for this tile
            canvas = ds.Canvas(
                plot_width=self.tile_size,
                plot_height=self.tile_size,
                x_range=(bounds["west"], bounds["east"]),
                y_range=(bounds["south"], bounds["north"]),
            )

            # Aggregate based on type
            if agg_type == "count":
                agg = canvas.points(points_df, "lon", "lat", agg=ds.count())
            elif agg_type == "mean" and "value" in points_df.columns:
                agg = canvas.points(points_df, "lon", "lat", agg=ds.mean("value"))
            elif agg_type == "sum" and "value" in points_df.columns:
                agg = canvas.points(points_df, "lon", "lat", agg=ds.sum("value"))
            else:
                # Default to count
                agg = canvas.points(points_df, "lon", "lat", agg=ds.count())

            # Get colormap
            cmap = self._get_colormap(colormap)

            # Render to image with logarithmic scaling for better density visualization
            img = tf.shade(agg, cmap=cmap, how="log")

            # Set transparent background
            img = tf.set_background(img, None)

            # Convert to PNG bytes
            result = self._image_to_bytes(img)

            # Store in LRU cache, evicting oldest entry if at capacity
            self._tile_cache[cache_key] = result
            if len(self._tile_cache) > self._tile_cache_max:
                self._tile_cache.popitem(last=False)

            return result

        except Exception as e:
            log.error(f"Error generating datashader tile {z}/{x}/{y}: {e}", exc_info=True)
            return self._empty_tile()

    def generate_density_heatmap(
        self,
        region: str,
        width: int = 800,
        height: int = 600,
        colormap: str = "fire",
    ) -> bytes:
        """
        Generate a full-region density heatmap.

        Args:
            region: Region name (e.g., "LOMBARDIA")
            width: Image width in pixels
            height: Image height in pixels
            colormap: Color palette name

        Returns:
            PNG image as bytes
        """
        try:
            if not self.db:
                return self._empty_image(width, height)

            from land_registry.cadastral_db import CadastralFilter

            # Query all parcels in region
            filter_obj = CadastralFilter(regione=region)
            gdf = self.db.query_parcels(filter_obj)

            if gdf is None or len(gdf) == 0:
                log.warning(f"No data found for region: {region}")
                return self._empty_image(width, height)

            log.info(f"Generating density heatmap for {region} with {len(gdf)} parcels")

            # Get geographic bounds
            bounds = gdf.total_bounds  # [minx, miny, maxx, maxy]

            # Convert to points
            points_df = self._polygons_to_points(gdf)

            if len(points_df) == 0:
                return self._empty_image(width, height)

            # Create canvas
            canvas = ds.Canvas(
                plot_width=width,
                plot_height=height,
                x_range=(bounds[0], bounds[2]),
                y_range=(bounds[1], bounds[3]),
            )

            # Aggregate by count (density)
            agg = canvas.points(points_df, "lon", "lat", agg=ds.count())

            # Get colormap
            cmap = self._get_colormap(colormap)

            # Shade with color and logarithmic scaling
            img = tf.shade(agg, cmap=cmap, how="log")
            img = tf.set_background(img, "white")

            return self._image_to_bytes(img)

        except Exception as e:
            log.error(f"Error generating density heatmap for {region}: {e}", exc_info=True)
            return self._empty_image(width, height)

    def generate_categorical_map(
        self,
        region: str,
        category_field: str = "foglio",
        width: int = 800,
        height: int = 600,
    ) -> bytes:
        """
        Generate a categorical map colored by field value (e.g., foglio, property type).

        Args:
            region: Region name
            category_field: Field name to use for categorization
            width: Image width in pixels
            height: Image height in pixels

        Returns:
            PNG image as bytes
        """
        try:
            if not self.db:
                return self._empty_image(width, height)

            from land_registry.cadastral_db import CadastralFilter

            # Query parcels
            filter_obj = CadastralFilter(regione=region)
            gdf = self.db.query_parcels(filter_obj)

            if gdf is None or len(gdf) == 0:
                log.warning(f"No data found for region: {region}")
                return self._empty_image(width, height)

            log.info(
                f"Generating categorical map for {region} by {category_field} with {len(gdf)} parcels"
            )

            bounds = gdf.total_bounds
            points_df = self._polygons_to_points(gdf)

            if len(points_df) == 0:
                return self._empty_image(width, height)

            # Ensure category field exists
            if category_field not in points_df.columns:
                log.warning(f"Category field {category_field} not found, falling back to 'foglio'")
                category_field = "foglio" if "foglio" in points_df.columns else points_df.columns[0]

            # Create canvas
            canvas = ds.Canvas(
                plot_width=width,
                plot_height=height,
                x_range=(bounds[0], bounds[2]),
                y_range=(bounds[1], bounds[3]),
            )

            # Aggregate by category
            agg = canvas.points(
                points_df,
                "lon",
                "lat",
                agg=ds.count_cat(category_field),
            )

            # Shade with categorical colors
            img = tf.shade(agg, cmap=colorcet.glasbey_category10)
            img = tf.set_background(img, "white")

            return self._image_to_bytes(img)

        except Exception as e:
            log.error(
                f"Error generating categorical map for {region} by {category_field}: {e}",
                exc_info=True,
            )
            return self._empty_image(width, height)

    # layer_type -> fgb filename prefix ("map" = foglio/sheet outlines,
    # "ple" = particella/individual parcel outlines).
    _LAYER_FGB_PREFIX = {"map": "cadastral_map", "ple": "cadastral_ple"}
    # Distinct outline color per layer so both can be told apart when stacked.
    _LAYER_LINE_COLOR = {"map": "#cc5500", "ple": "#1f7a8c"}

    def generate_boundary_tile(self, x: int, y: int, z: int, layer_type: str = "map") -> bytes:
        """
        Generate a map tile showing actual cadastral boundary polygons —
        either "map" (foglio/map-sheet outlines) or "ple" (individual
        particella/parcel outlines) — rasterized from the per-region
        FlatGeobuf source files, as opposed to generate_tile()'s centroid
        density heatmap.

        Reads only the fgb file(s) whose bounds overlap this tile (via each
        file's spatial index, no full-file load) and rasterizes a crisp
        outline only — no fill — so the tile can be stacked on top of the
        base map without obscuring it.

        Args:
            x: Tile X coordinate (TMS)
            y: Tile Y coordinate (TMS)
            z: Zoom level (0-18)
            layer_type: "map" (fogli) or "ple" (particelle)

        Returns:
            PNG tile image as bytes (transparent where there is no data)
        """
        cache_key = ("boundary", layer_type, x, y, z)
        if cache_key in self._tile_cache:
            self._tile_cache.move_to_end(cache_key)
            log.debug("Boundary tile cache hit: %s %d/%d/%d", layer_type, z, x, y)
            return self._tile_cache[cache_key]

        try:
            bounds = self._tile_to_bbox(x, y, z)
            bbox = (bounds["west"], bounds["south"], bounds["east"], bounds["north"])

            fgb_files = self._candidate_fgb_files(bbox, layer_type)
            if not fgb_files:
                return self._empty_tile()

            # Defensive cap: this is a public, unauthenticated endpoint, and a
            # low-zoom bbox can span several hundred-MB (or, for "ple",
            # multi-GB) region files at once (a client respecting the
            # intended minZoom never triggers this — see the sales-map
            # TileLayer config). Stop accumulating once the tile would be too
            # expensive to rasterize; partial coverage at that point is fine
            # since the tile isn't meant to be viewed here.
            max_features = 3000
            frames = []
            total = 0
            for fgb_path in fgb_files:
                try:
                    frame = gpd.read_file(fgb_path, bbox=bbox, engine="pyogrio")
                except Exception as e:
                    log.warning(f"Failed reading {fgb_path} for tile {z}/{x}/{y}: {e}")
                    continue
                if frame is not None and len(frame) > 0:
                    remaining = max_features - total
                    if len(frame) > remaining:
                        frame = frame.iloc[:remaining].copy()
                    frames.append(frame)
                    total += len(frame)
                if total >= max_features:
                    log.warning(
                        f"Tile {z}/{x}/{y} ({layer_type}) hit the {max_features}-feature cap "
                        f"({len(fgb_files)} candidate file(s)); rendering partial coverage"
                    )
                    break

            if not frames:
                return self._empty_tile()

            gdf = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
            gdf = gdf.to_crs(4326)

            canvas = ds.Canvas(
                plot_width=self.tile_size,
                plot_height=self.tile_size,
                x_range=(bbox[0], bbox[2]),
                y_range=(bbox[1], bbox[3]),
            )

            boundary = gdf.copy()
            boundary["geometry"] = boundary.boundary
            line_agg = canvas.line(boundary, geometry="geometry", agg=ds.any())
            img = tf.shade(line_agg, cmap=[self._LAYER_LINE_COLOR.get(layer_type, "#cc5500")])

            result = self._image_to_bytes(img)

            self._tile_cache[cache_key] = result
            if len(self._tile_cache) > self._tile_cache_max:
                self._tile_cache.popitem(last=False)

            return result

        except Exception as e:
            log.error(f"Error generating boundary tile {z}/{x}/{y} ({layer_type}): {e}", exc_info=True)
            return self._empty_tile()

    # layer_type -> the field holding the human-readable foglio/particella
    # reference on that layer's source schema (they're named differently).
    _LAYER_REFERENCE_FIELD = {
        "map": "NATIONALCADASTRALZONINGREFERENCE",
        "ple": "NATIONALCADASTRALREFERENCE",
    }

    def identify_feature(self, lat: float, lng: float, layer_type: str = "ple") -> Optional[dict]:
        """
        Find the cadastral polygon (foglio or particella, per layer_type)
        containing the given point, for a click-to-identify popup — the
        boundary tiles themselves are rasterized PNGs and can't carry
        per-feature tooltips the way a vector Leaflet layer would.

        Args:
            lat, lng: click location (WGS84-ish, matches the source EPSG:6706)
            layer_type: "map" (fogli) or "ple" (particelle)

        Returns:
            A plain dict of display fields, or None if no polygon contains
            the point (e.g. sea, or outside all indexed regions).
        """
        try:
            from shapely.geometry import Point as ShapelyPoint

            # Small buffer around the point: comfortably larger than any
            # single parcel/sheet, cheap to read via the fgb spatial index.
            buf = 0.01
            bbox = (lng - buf, lat - buf, lng + buf, lat + buf)

            fgb_files = self._candidate_fgb_files(bbox, layer_type)
            if not fgb_files:
                return None

            point = ShapelyPoint(lng, lat)
            for fgb_path in fgb_files:
                try:
                    frame = gpd.read_file(fgb_path, bbox=bbox, engine="pyogrio")
                except Exception as e:
                    log.warning(f"Failed reading {fgb_path} for identify: {e}")
                    continue
                if frame is None or len(frame) == 0:
                    continue
                hits = frame[frame.geometry.contains(point)]
                if len(hits) == 0:
                    continue
                row = hits.iloc[0]
                ref_field = self._LAYER_REFERENCE_FIELD.get(layer_type)
                return {
                    "label": row.get("LABEL"),
                    "reference": row.get(ref_field) if ref_field else None,
                    "comune": row.get("_comune_name"),
                    "provincia": row.get("_provincia"),
                    "regione": row.get("_regione"),
                    "administrative_unit": row.get("ADMINISTRATIVEUNIT"),
                }

            return None

        except Exception as e:
            log.error(f"Error identifying feature at ({lat}, {lng}) ({layer_type}): {e}", exc_info=True)
            return None

    def _region_fgb_bounds(self, layer_type: str = "map") -> dict[str, tuple]:
        """
        Lazily map each cadastral_<layer_type>.<region>.fgb file to its
        total_bounds (in the source EPSG:6706 CRS), read once from each
        file's header via pyogrio (cheap regardless of file size — no full
        read). Cached separately per layer_type ("map" vs "ple").
        """
        if layer_type in self._fgb_bounds:
            return self._fgb_bounds[layer_type]

        from land_registry.config import spatialite_settings

        prefix = self._LAYER_FGB_PREFIX[layer_type]
        fgb_dir = Path(spatialite_settings.fgb_directory)
        bounds: dict[str, tuple] = {}
        if not fgb_dir.exists():
            log.warning(f"FGB directory does not exist: {fgb_dir}")
            self._fgb_bounds[layer_type] = bounds
            return bounds

        for fgb_path in fgb_dir.glob(f"{prefix}.*.fgb"):
            try:
                info = pyogrio.read_info(fgb_path)
                bounds[fgb_path.name] = tuple(info["total_bounds"])
            except Exception as e:
                log.warning(f"Failed reading fgb header for {fgb_path}: {e}")

        self._fgb_bounds[layer_type] = bounds
        log.info(f"Indexed {len(bounds)} {prefix} fgb file(s) for boundary tiles")
        return bounds

    def _candidate_fgb_files(self, bbox: tuple, layer_type: str = "map") -> list:
        """
        Return the cadastral_<layer_type>.*.fgb files whose bounds overlap
        ``bbox``.

        The bounds comparison is a coarse EPSG:6706-vs-EPSG:4326 rectangle
        overlap (the two CRSs are close enough in extent for file selection —
        actual geometry is reprojected properly before rendering).
        """
        from land_registry.config import spatialite_settings

        fgb_dir = Path(spatialite_settings.fgb_directory)
        west, south, east, north = bbox
        matches = []
        for name, (minx, miny, maxx, maxy) in self._region_fgb_bounds(layer_type).items():
            if minx > east or maxx < west or miny > north or maxy < south:
                continue
            matches.append(fgb_dir / name)
        return matches

    def warmup_jit(self) -> None:
        """
        Pay datashader/numba's one-time JIT compile cost (~7s for
        Canvas.polygons/Canvas.line) with a throwaway call, so the first real
        tile request doesn't stall. Also pre-indexes the "map" and "ple" fgb
        bounds (see _region_fgb_bounds) — for "ple" specifically this reads
        pyogrio.read_info() across ~19 files up to several GB each, which can
        take well over a minute on a cold cache; better to eat that once here
        than on a user's first click-to-identify or boundary-tile request.
        Call this once at process startup, off the request path.
        """
        try:
            dummy = gpd.GeoDataFrame(
                {"geometry": [Point(0, 0).buffer(0.01)]}, crs=4326
            )
            canvas = ds.Canvas(plot_width=16, plot_height=16, x_range=(-1, 1), y_range=(-1, 1))
            canvas.polygons(dummy, geometry="geometry", agg=ds.count())
            boundary = dummy.copy()
            boundary["geometry"] = boundary.boundary
            canvas.line(boundary, geometry="geometry", agg=ds.any())
            log.info("Datashader JIT warm-up complete")
        except Exception as e:
            log.warning(f"Datashader JIT warm-up failed (non-fatal): {e}")

        for layer_type in ("map", "ple"):
            try:
                bounds = self._region_fgb_bounds(layer_type)
                log.info(f"Pre-indexed {len(bounds)} cadastral_{layer_type} fgb file(s)")
            except Exception as e:
                log.warning(f"Cadastral {layer_type} fgb pre-indexing failed (non-fatal): {e}")

    def _polygons_to_points(self, gdf: gpd.GeoDataFrame) -> pd.DataFrame:
        """
        Convert polygon geometries to centroid points for datashader aggregation.

        Args:
            gdf: GeoDataFrame with polygon geometries

        Returns:
            DataFrame with lon, lat, and attribute columns
        """
        if gdf is None or len(gdf) == 0:
            return pd.DataFrame()

        # Calculate centroids
        centroids = gdf.geometry.centroid

        # Build DataFrame with centroids and select attributes
        data = {
            "lon": centroids.x,
            "lat": centroids.y,
        }

        # Add optional attributes if they exist
        for field in ["foglio", "particella", "comune_code", "layer_type"]:
            if field in gdf.columns:
                data[field] = gdf[field].values

        return pd.DataFrame(data)

    def _tile_to_bbox(self, x: int, y: int, z: int) -> dict:
        """
        Convert TMS (Tile Map Service) tile coordinates to geographic bounding box.

        Args:
            x: Tile X coordinate
            y: Tile Y coordinate
            z: Zoom level

        Returns:
            Dictionary with 'west', 'south', 'east', 'north' bounds in EPSG:4326
        """
        n = 2.0**z

        # Calculate longitude
        lon_min = x / n * 360.0 - 180.0
        lon_max = (x + 1) / n * 360.0 - 180.0

        # Calculate latitude using Mercator projection inverse
        lat_min = np.arctan(np.sinh(np.pi * (1 - 2 * y / n))) * 180.0 / np.pi
        lat_max = np.arctan(np.sinh(np.pi * (1 - 2 * (y + 1) / n))) * 180.0 / np.pi

        return {
            "west": lon_min,
            "south": min(lat_min, lat_max),
            "east": lon_max,
            "north": max(lat_min, lat_max),
        }

    def _get_colormap(self, colormap_name: str):
        """
        Get colorcet colormap by name.

        Args:
            colormap_name: Name of colormap (fire, viridis, blues, etc.)

        Returns:
            Colorcet colormap array
        """
        # Map common names to colorcet palettes
        colormap_mapping = {
            "fire": colorcet.fire,
            "viridis": colorcet.fire,  # colorcet doesn't have viridis, use fire
            "blues": colorcet.blues,
            "reds": colorcet.fire,
            "greens": colorcet.kbc,
            "rainbow": colorcet.rainbow,
            "coolwarm": colorcet.coolwarm,
            "inferno": colorcet.fire,
            "plasma": colorcet.fire,
        }

        return colormap_mapping.get(colormap_name.lower(), colorcet.fire)

    def _image_to_bytes(self, img) -> bytes:
        """
        Convert datashader image to PNG bytes.

        Args:
            img: Datashader Image object

        Returns:
            PNG image as bytes
        """
        pil_img = img.to_pil()
        buffer = BytesIO()
        pil_img.save(buffer, format="PNG")
        buffer.seek(0)
        return buffer.getvalue()

    def _empty_tile(self) -> bytes:
        """
        Return a transparent empty tile for areas with no data.

        Returns:
            Transparent PNG tile as bytes
        """
        # Create transparent RGBA image
        img = Image.new("RGBA", (self.tile_size, self.tile_size), (255, 255, 255, 0))
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)
        return buffer.getvalue()

    def _empty_image(self, width: int, height: int) -> bytes:
        """
        Return an empty white image.

        Args:
            width: Image width
            height: Image height

        Returns:
            White PNG image as bytes
        """
        img = Image.new("RGB", (width, height), (255, 255, 255))
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)
        return buffer.getvalue()
