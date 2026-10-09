-- Expose the public quality-of-life database views inside aecs4u-stats.
-- The table view feeds BES lookups; the map view preserves its geometry and
-- GeoJSON projections for map clients.
--
-- Run as a role that can create FDW objects and grant access in aecs4u-stats:
--   psql -X -U postgres -d aecs4u-stats -v stats_reader_role=<role> \
--     -f scripts/sql/import-quality-of-life-views.sql

\set ON_ERROR_STOP on
\if :{?stats_reader_role}
\else
\echo 'Set -v stats_reader_role=<database-role> to the Stats application reader role.'
\quit 2
\endif

CREATE EXTENSION IF NOT EXISTS postgres_fdw;

CREATE SERVER IF NOT EXISTS quality_of_life_source
  FOREIGN DATA WRAPPER postgres_fdw
  OPTIONS (
    host '/var/run/postgresql',
    port '5432',
    dbname 'quality_of_life',
    extensions 'postgis'
  );

CREATE USER MAPPING IF NOT EXISTS FOR CURRENT_USER
  SERVER quality_of_life_source
  OPTIONS (user 'postgres');

CREATE SCHEMA IF NOT EXISTS quality_of_life;

SELECT to_regclass('quality_of_life.v_quality_of_life_table') IS NULL AS import_table
\gset
\if :import_table
IMPORT FOREIGN SCHEMA public
  LIMIT TO (v_quality_of_life_table)
  FROM SERVER quality_of_life_source
  INTO quality_of_life;
\endif

SELECT to_regclass('quality_of_life.v_quality_of_life_map') IS NULL AS import_map
\gset
\if :import_map
IMPORT FOREIGN SCHEMA public
  LIMIT TO (v_quality_of_life_map)
  FROM SERVER quality_of_life_source
  INTO quality_of_life;
\endif

GRANT USAGE ON SCHEMA quality_of_life TO :"stats_reader_role";
GRANT SELECT ON quality_of_life.v_quality_of_life_table,
                quality_of_life.v_quality_of_life_map
   TO :"stats_reader_role";
