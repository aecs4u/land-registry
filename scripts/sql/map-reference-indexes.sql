-- Run against the aecs4u-stats PostgreSQL database (outside a transaction).
-- These indexes make exact map search and legacy reference-only links bounded.
CREATE INDEX CONCURRENTLY IF NOT EXISTS cadastral_parcel_canonical_reference_idx
    ON spatial.cadastral_parcel (canonical_reference);
CREATE INDEX CONCURRENTLY IF NOT EXISTS cadastral_parcel_national_reference_idx
    ON spatial.cadastral_parcel (national_cadastral_reference);

-- Refresh planner statistics. Without them the planner estimated ~30,000
-- matches per reference and chose a primary-key walk (a full scan) over these
-- indexes; the Veneto load had never been analyzed.
ANALYZE spatial.cadastral_parcel;
