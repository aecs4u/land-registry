-- Run against aecs4u-stats by hand (not part of apply_map_sql.py: it copies data).
-- Local, indexed copy of the auction (PVP) map points.
--
-- pvp.v_map_sale_points is a foreign table over the pvp_enriched view
-- modelview.v_map_sale_points, which geocodes ~850k sales on every read
-- (per-sale ST_Covers checks against municipality polygons). On a loaded
-- machine the national read no longer fits the map loader's 300 s statement
-- limit, so the auction layer never loads. The loader prefers this copy, reads
-- it in seconds, and falls back to the live views only while it does not exist.
--
-- Building it runs the expensive view once, without the loader's time limit
-- (10-20 minutes on a busy machine). Run it outside a transaction:
--   psql -d aecs4u-stats -f scripts/sql/pvp-map-sale-points-local-copy.sql
--
-- Refresh after PVP data is reloaded (the unique index allows CONCURRENTLY,
-- which keeps the copy readable meanwhile), then restart the app or call
-- /api/v1/sales/map-points?refresh=1:
--   SET statement_timeout = 0;
--   REFRESH MATERIALIZED VIEW CONCURRENTLY pvp.map_sale_points_local;
SET statement_timeout = 0;

CREATE MATERIALIZED VIEW IF NOT EXISTS pvp.map_sale_points_local AS
SELECT sale_id,
       latitude,
       longitude,
       price,
       sale_datetime,
       property_type,
       coordinate_is_approximate,
       description_hint
  FROM pvp.v_map_sale_points
 WHERE sale_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS map_sale_points_local_sale_id
    ON pvp.map_sale_points_local (sale_id);

ANALYZE pvp.map_sale_points_local;
