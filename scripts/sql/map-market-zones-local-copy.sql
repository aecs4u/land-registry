-- Run against aecs4u-stats by hand (not part of apply_map_sql.py: it copies data).
-- Local, indexed copy of the OMI market-zone map columns.
--
-- zornade.zornade_zone_omi is a foreign table chained over two databases, so a
-- tile scans the whole remote source (3-6 s per tile at every zoom, 27,228
-- polygons, ~371 MB of geometry) and no index can be added to it. The
-- `market-zones` map layer prefers this copy and falls back to the foreign
-- table while the copy does not exist. Same column names and OGC_FID values as
-- the source, so feature ids in tiles and detail sheets are unchanged.
--
-- Geometry is flattened to 2D MultiPolygon: the source is GeometryZ with no
-- real elevation, and the map only needs the footprint.
--
-- Refresh after the source is reloaded (the unique index allows CONCURRENTLY):
--   REFRESH MATERIALIZED VIEW CONCURRENTLY zornade.omi_market_zone_map;
CREATE MATERIALIZED VIEW IF NOT EXISTS zornade.omi_market_zone_map AS
SELECT "OGC_FID",
       codcom,
       codzona,
       zona_descr,
       comune_descrizione,
       cod_tip_prev,
       descr_tip_prev,
       descr_tipologia_en,
       stato_prev,
       compr_min,
       compr_max,
       loc_min,
       loc_max,
       ST_Multi(ST_Force2D(geom))::geometry(MultiPolygon, 4326) AS geom
  FROM zornade.zornade_zone_omi;

CREATE UNIQUE INDEX IF NOT EXISTS omi_market_zone_map_ogc_fid
    ON zornade.omi_market_zone_map ("OGC_FID");

CREATE INDEX IF NOT EXISTS omi_market_zone_map_geom_gist
    ON zornade.omi_market_zone_map USING gist (geom);

ANALYZE zornade.omi_market_zone_map;
