-- Run in the agenziademanio PostgreSQL database before refreshing the stats FDW.
-- public.concessions already stores EPSG:4326 geometry with a GiST index.
-- The former view reparsed and transformed the entire JSON source on each tile.
-- Keep the existing view contract and dependencies while exposing stored geom
-- directly, so a spatial restriction can reach the source index.
BEGIN;
SET LOCAL lock_timeout = '5s';

CREATE OR REPLACE VIEW agenziademanio.v_concession_map_features AS
SELECT c.row_id,
       c.snapshot_id,
       c.idconc,
       c.layer_kind,
       c.geometry_type,
       c.crs_original,
       c.source_release,
       c.source_feature_key,
       c.dataset_release_id,
       c.geometry_valid_4326,
       c.geometry_valid_4326 IS FALSE AS geometry_repaired,
       c.geom,
       CASE WHEN c.layer_kind = 'csv_wgs84'
            THEN NULLIF(btrim(c.raw_json::jsonb ->> 'amministr'), '')
            ELSE NULL::text
       END AS admin_label
FROM public.concessions AS c
WHERE c.geom IS NOT NULL
  AND c.layer_kind IN ('polygon_shp', 'csv_wgs84');

COMMENT ON VIEW agenziademanio.v_concession_map_features IS
    'Map geometries from indexed public.concessions; polygon footprints and WGS84 point records';

COMMIT;
