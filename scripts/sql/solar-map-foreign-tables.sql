-- Run as a PostgreSQL role with access to both databases.
-- Links solar aggregates into aecs4u-stats without copying source rows, then
-- joins the municipal aggregate to the current canonical municipality geometry.
--
--   psql -X -U postgres -d postgres -v stats_reader_role=postgres \
--     -f scripts/sql/solar-map-foreign-tables.sql

\set ON_ERROR_STOP on
\if :{?stats_reader_role}
\else
\echo 'Set -v stats_reader_role=<database-role> to the Stats application reader role.'
\quit 2
\endif

\connect aecs4u-stats

CREATE EXTENSION IF NOT EXISTS postgres_fdw;
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE SERVER IF NOT EXISTS solar_source
    FOREIGN DATA WRAPPER postgres_fdw
    OPTIONS (host '/var/run/postgresql', port '5432', dbname 'solar');

SELECT format(
    'CREATE USER MAPPING FOR %I SERVER solar_source OPTIONS (user %L)',
    :'stats_reader_role', :'stats_reader_role'
)
WHERE NOT EXISTS (
    SELECT 1
      FROM pg_user_mappings AS mapping
      JOIN pg_foreign_server AS server ON server.oid = mapping.srvid
     WHERE server.srvname = 'solar_source'
       AND mapping.usename = :'stats_reader_role'
)
\gexec

CREATE SCHEMA IF NOT EXISTS solar;

-- Keep these projections aligned with solar.public's published relations.
-- Recreate the local wrappers when the source projection changes.
DROP VIEW IF EXISTS solar.potential_by_municipality;
DROP FOREIGN TABLE IF EXISTS solar.solar_potential_comuni;
DROP FOREIGN TABLE IF EXISTS solar.solar_potential_province;

CREATE FOREIGN TABLE solar.solar_potential_comuni (
    pro_com_t text,
    n_buildings integer,
    pvout_pessimistic_kwh_year_total double precision,
    pvout_modern_kwh_year_total double precision,
    pvout_per_capita_kwh double precision,
    kwp_max_total double precision,
    high_viability_pct double precision,
    medium_viability_pct double precision,
    low_viability_pct double precision,
    not_eligible_pct double precision,
    solar_data_version text,
    source text,
    updated_at timestamp with time zone
) SERVER solar_source
  OPTIONS (schema_name 'public', table_name 'solar_potential_comuni');

CREATE FOREIGN TABLE solar.solar_potential_province (
    cod_prov text,
    province_name text,
    regione_name text,
    n_comuni bigint,
    n_buildings double precision,
    pvout_pessimistic_kwh_year_total double precision,
    pvout_modern_kwh_year_total double precision,
    kwp_max_total double precision,
    high_viability_pct double precision,
    medium_viability_pct double precision,
    low_viability_pct double precision,
    not_eligible_pct double precision,
    solar_data_version text,
    source text,
    updated_at timestamp with time zone
) SERVER solar_source
  OPTIONS (schema_name 'public', table_name 'solar_potential_province');

COMMENT ON FOREIGN TABLE solar.solar_potential_comuni IS
    'Live FDW link to solar.public.solar_potential_comuni; source rows remain in the solar database.';
COMMENT ON FOREIGN TABLE solar.solar_potential_province IS
    'Live FDW link to solar.public.solar_potential_province; source rows remain in the solar database.';

CREATE VIEW solar.potential_by_municipality AS
SELECT u.id AS id,
       u.id AS geo_unit_id,
       u.canonical_name,
       boundary.geom,
       potential.n_buildings,
       potential.pvout_modern_kwh_year_total,
       potential.pvout_pessimistic_kwh_year_total,
       potential.kwp_max_total,
       potential.high_viability_pct,
       potential.medium_viability_pct,
       potential.low_viability_pct,
       potential.not_eligible_pct,
       potential.solar_data_version,
       potential.updated_at
  FROM solar.solar_potential_comuni AS potential
  JOIN geo.geo_identifier AS identifier
    ON identifier.scheme = 'ISTAT_COMUNE'
   AND identifier.code = potential.pro_com_t::integer::text
  JOIN geo.geo_unit AS u
    ON u.id = identifier.geo_unit_id
   AND u.unit_type = 'municipality'
  JOIN LATERAL (
       SELECT current_boundary.geom
         FROM geo.geo_boundary_current AS current_boundary
        WHERE current_boundary.geo_unit_id = u.id
        ORDER BY (current_boundary.generalization IS NOT NULL),
                 current_boundary.id
        LIMIT 1
  ) AS boundary ON true;

COMMENT ON VIEW solar.potential_by_municipality IS
    'Municipal solar potential joined to the current canonical municipality boundary for map rendering.';

GRANT USAGE ON SCHEMA solar TO :"stats_reader_role";
GRANT SELECT ON solar.solar_potential_comuni,
                solar.solar_potential_province,
                solar.potential_by_municipality TO :"stats_reader_role";
