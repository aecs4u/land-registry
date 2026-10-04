-- Run against aecs4u-stats outside a transaction. The map displays only these
-- two concession row kinds; indexing the display predicate avoids fetching
-- duplicate CSV point rows from the heap for every spatial tile.
CREATE INDEX CONCURRENTLY IF NOT EXISTS demanio_marittimo_concessions_map_geom_gist
    ON demanio_marittimo.concessions USING GIST (geom)
    WHERE layer_kind IN ('polygon_shp', 'point_shp');
ANALYZE demanio_marittimo.concessions;
