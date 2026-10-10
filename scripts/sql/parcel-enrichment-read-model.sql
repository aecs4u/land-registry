-- Provision the parcel enrichment cache consumed by stats_service.py.
-- Run as a database owner in aecs4u-stats; the application role receives
-- access to this cache table only, not write access to the source relations.
--
--   psql -X -U postgres -d aecs4u-stats \
--     -v stats_app_role=<land-registry-service-role> \
--     -f scripts/sql/parcel-enrichment-read-model.sql

\set ON_ERROR_STOP on
\if :{?stats_app_role}
\else
\echo 'Set -v stats_app_role=<database-role> to the land-registry service role.'
\quit 2
\endif

CREATE SCHEMA IF NOT EXISTS serving;

CREATE TABLE IF NOT EXISTS serving.parcel_enrichment_read_model (
    parcel_key TEXT PRIMARY KEY,
    payload JSONB NOT NULL,
    source_fingerprint TEXT,
    refreshed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- The primary key supplies the index used by the parcel_key equality lookup.
GRANT USAGE ON SCHEMA serving TO :"stats_app_role";
GRANT SELECT, INSERT, UPDATE
    ON TABLE serving.parcel_enrichment_read_model
    TO :"stats_app_role";
