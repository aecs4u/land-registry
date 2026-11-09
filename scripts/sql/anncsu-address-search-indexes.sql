-- Optional indexes for the map address search (land_registry/anncsu_lookup.py).
-- Run BY HAND against the `anncsu` database, not the stats database, so it is
-- deliberately absent from scripts/apply_map_sql.py. Safe to re-run; build
-- outside a transaction (CREATE INDEX CONCURRENTLY).
--
-- The lookup works without them: it reads a municipality's street list once
-- (WHERE istat_code = ...) and one street's accesses through the existing
-- (comune_code) index. Add these if street-level reads become slow on
-- municipalities with very many addresses.
--
-- Tables live in schema `public` (package importer layout) or `anncsu`.
-- Edit the schema below, and repeat the pair for every loaded region NN.
SET search_path TO public, anncsu;

CREATE INDEX CONCURRENTLY IF NOT EXISTS r19__addresses_comune_street_idx
    ON r19__addresses (comune_code, street_id);

CREATE INDEX CONCURRENTLY IF NOT EXISTS r19__streets_istat_idx
    ON r19__streets (istat_code);

ANALYZE r19__addresses;
ANALYZE r19__streets;
