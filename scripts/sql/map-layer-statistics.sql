-- Run against aecs4u-stats after loading or materially updating map sources.
-- Accurate statistics help PostGIS choose the available spatial and reference
-- indexes instead of estimating large scans poorly.
ANALYZE spatial.cadastral_parcel;
ANALYZE spatial.hazard_area;
ANALYZE spatial.market_zone;
ANALYZE agenziademanio.concessions;
