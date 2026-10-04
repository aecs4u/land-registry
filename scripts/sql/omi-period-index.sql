-- Supports the OMI market-zone overlay (land_registry/map_layers.py):
--   * the latest semester (MAX(period)) and the period list for /map/omi/filters,
--   * the distinct typology and condition lists, read with loose index scans
--     over (period, typology[, condition]) instead of scanning ~10M quote rows.
-- Without it each list is a full sequential scan (~30 s per DISTINCT).
-- Build outside a transaction.
CREATE INDEX CONCURRENTLY IF NOT EXISTS facts_market_quote_period_dims_idx
    ON facts.market_quote_fact (period, typology, condition);

ANALYZE facts.market_quote_fact;
