-- Run in the agenziademanio database as the owner of public.concessions.
-- Keep this file outside a transaction: CREATE INDEX CONCURRENTLY cannot run
-- inside the transaction used by agenziademanio-map-features.sql.
-- The Stats FDW pushes feature-detail row_id predicates to the map view; that
-- view must use a btree lookup rather than scan the full source table.
SET lock_timeout = '5s';

CREATE INDEX CONCURRENTLY IF NOT EXISTS public_concessions_row_id_idx
    ON public.concessions (row_id);

ANALYZE public.concessions;
