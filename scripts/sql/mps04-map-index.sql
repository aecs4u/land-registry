-- Supports MPS04 map tiles by selecting one probability/period curve and
-- joining it to the spatially bounded grid points. Build outside a transaction.
CREATE INDEX CONCURRENTLY IF NOT EXISTS mps04_hazard_curve_map_lookup_idx
    ON hazards_mps04.mps04_hazard_curve
       (exceedance_probability_pct, period_s, grid_variant, point_id)
    INCLUDE (value_median, value_p16, value_p84);

ANALYZE hazards_mps04.mps04_hazard_curve;
