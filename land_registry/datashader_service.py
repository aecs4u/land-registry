"""
Datashader-based tile generation service for high-performance visualization of massive cadastral datasets.

This service generates rasterized map tiles on-the-fly using datashader's GPU/CPU-accelerated aggregation,
enabling visualization of millions of cadastral parcels without client-side performance degradation.
"""

import logging
from io import BytesIO
from typing import Literal, Optional

import colorcet
import datashader as ds
import datashader.transfer_functions as tf
import geopandas as gpd
import numpy as np
import pandas as pd
from PIL import Image

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

        # Cache for performance
        self._region_cache = {}

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
            return self._image_to_bytes(img)

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
