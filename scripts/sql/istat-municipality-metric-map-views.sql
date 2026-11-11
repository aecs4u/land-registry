-- Case B of the aecs4u-stats deprecation: map layers that join municipality
-- geometry (istat) with metrics from another source database.
--
-- Run against the ISTAT database: it owns the geometry and its index, so tiles
-- stay local. The metric tables (~8k rows each, no geometry) are read from their
-- source databases through postgres_fdw ONLY when the materialized views are
-- refreshed, never per tile request. Idempotent.
--
--   psql -h <host> -U postgres -d istat -f scripts/sql/istat-municipality-metric-map-views.sql
--   select lr_map.refresh_metric_views();       -- after a solar / MEF source reload
--
-- Staleness: the metric views are as old as their last refresh (see the
-- refreshed_at column). Cloud SQL: replace the socket host with the source
-- instance address and add `password` to the USER MAPPING options.
BEGIN;

CREATE EXTENSION IF NOT EXISTS postgres_fdw;
CREATE SCHEMA IF NOT EXISTS lr_src;
CREATE SCHEMA IF NOT EXISTS lr_map;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_foreign_server WHERE srvname = 'lr_solar_source') THEN
        CREATE SERVER lr_solar_source FOREIGN DATA WRAPPER postgres_fdw
            OPTIONS (host '/var/run/postgresql', port '5432', dbname 'solar');
        CREATE USER MAPPING FOR CURRENT_USER SERVER lr_solar_source OPTIONS (user 'postgres');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_foreign_server WHERE srvname = 'lr_mef_irpef_source') THEN
        CREATE SERVER lr_mef_irpef_source FOREIGN DATA WRAPPER postgres_fdw
            OPTIONS (host '/var/run/postgresql', port '5432', dbname 'mef_irpef');
        CREATE USER MAPPING FOR CURRENT_USER SERVER lr_mef_irpef_source OPTIONS (user 'postgres');
    END IF;
    -- Import only when missing, so re-running never drops what depends on them.
    IF to_regclass('lr_src.solar_potential_comuni') IS NULL THEN
        IMPORT FOREIGN SCHEMA public LIMIT TO (solar_potential_comuni)
            FROM SERVER lr_solar_source INTO lr_src;
    END IF;
    IF to_regclass('lr_src.irpef_comuni') IS NULL THEN
        IMPORT FOREIGN SCHEMA mef_irpef LIMIT TO (irpef_comuni)
            FROM SERVER lr_mef_irpef_source INTO lr_src;
    END IF;
END $$;

-- Local copies, one row per municipality, keyed by the ISTAT code without
-- leading zeroes (the form public.v_comuni_map.pro_com has).
CREATE MATERIALIZED VIEW IF NOT EXISTS lr_map.mv_solar_potential_comuni AS
SELECT ltrim(s.pro_com_t::text, '0') AS pro_com_key,
       s.n_buildings,
       s.pvout_modern_kwh_year_total,
       s.pvout_pessimistic_kwh_year_total,
       s.kwp_max_total,
       s.high_viability_pct,
       s.medium_viability_pct,
       s.low_viability_pct,
       s.not_eligible_pct,
       s.solar_data_version,
       s.updated_at,
       now() AS refreshed_at
FROM lr_src.solar_potential_comuni AS s;
CREATE UNIQUE INDEX IF NOT EXISTS mv_solar_potential_comuni_key
    ON lr_map.mv_solar_potential_comuni (pro_com_key);

CREATE MATERIALIZED VIEW IF NOT EXISTS lr_map.mv_mef_income_comuni AS
WITH latest_year AS (
    SELECT max(anno) AS year
    FROM lr_src.irpef_comuni
    WHERE cod_istat IS NOT NULL AND imponibile_amount IS NOT NULL AND imponibile_freq > 0
)
SELECT NULLIF(ltrim(t.cod_istat::text, '0'), '') AS pro_com_key,
       t.anno AS tax_year,
       t.imponibile_freq::bigint AS taxpayers,
       (t.imponibile_amount / NULLIF(t.imponibile_freq, 0))::double precision AS mean_taxable_income_eur,
       (t.bracket_over_120k_freq / NULLIF(t.imponibile_freq, 0) * 100)::double precision AS high_income_share_pct,
       ((COALESCE(t.bracket_le_0_freq, 0) + COALESCE(t.bracket_0_10k_freq, 0))
           / NULLIF(t.imponibile_freq, 0) * 100)::double precision AS low_income_share_pct,
       now() AS refreshed_at
FROM lr_src.irpef_comuni AS t
JOIN latest_year AS y ON y.year = t.anno
WHERE t.cod_istat IS NOT NULL AND t.imponibile_amount IS NOT NULL AND t.imponibile_freq > 0
  AND NULLIF(ltrim(t.cod_istat::text, '0'), '') IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS mv_mef_income_comuni_key
    ON lr_map.mv_mef_income_comuni (pro_com_key);

-- Map-facing views: current ISTAT geometry joined to the local metric copies.
CREATE OR REPLACE VIEW lr_map.v_solar_potential_comuni_map AS
SELECT u.pro_com::bigint AS id,
       u.pro_com::bigint AS geo_unit_id,
       u.name AS canonical_name,
       s.n_buildings,
       s.pvout_modern_kwh_year_total,
       s.pvout_pessimistic_kwh_year_total,
       s.kwp_max_total,
       s.high_viability_pct,
       s.medium_viability_pct,
       s.low_viability_pct,
       s.not_eligible_pct,
       s.solar_data_version,
       s.updated_at,
       u.geom
FROM public.v_comuni_map AS u
JOIN lr_map.mv_solar_potential_comuni AS s ON s.pro_com_key = u.pro_com::text;

CREATE OR REPLACE VIEW lr_map.v_mef_income_comuni_map AS
SELECT c.pro_com::bigint AS geo_unit_id,
       c.name::text AS canonical_name,
       i.tax_year::integer AS tax_year,
       i.taxpayers,
       i.mean_taxable_income_eur,
       i.high_income_share_pct,
       i.low_income_share_pct,
       c.geom AS geom
FROM public.v_comuni_map AS c
JOIN lr_map.mv_mef_income_comuni AS i ON i.pro_com_key = c.pro_com::text;

-- Refresh without blocking readers (needs the unique indexes above).
CREATE OR REPLACE FUNCTION lr_map.refresh_metric_views() RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY lr_map.mv_solar_potential_comuni;
    REFRESH MATERIALIZED VIEW CONCURRENTLY lr_map.mv_mef_income_comuni;
END $$;

COMMIT;
