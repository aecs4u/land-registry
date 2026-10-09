-- Run in psql against the cadastral database, outside a transaction. PVP
-- municipality IDs are not cadastral municipality codes, so the map joins on
-- normalized municipality name + province code + sheet + parcel.
\connect cadastral

-- Remove the superseded code-based indexes from the first implementation.
SELECT format('DROP INDEX CONCURRENTLY IF EXISTS public.%I',
              regexp_replace(t.relname, '__particelle$', '') || '_particelle_map_lookup_idx')
FROM pg_class t
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE n.nspname = 'public' AND t.relname ~ '^[a-z][a-z_]*__particelle$'
\gexec

SELECT format(
    'CREATE INDEX CONCURRENTLY IF NOT EXISTS %I ON public.%I '
    '(lower(btrim(municipality_name)), upper(btrim(province)), '
    'upper(btrim(sheet_number)), upper(btrim(parcel_number)))',
    regexp_replace(t.relname, '__particelle$', '') || '_particelle_muni_map_lookup_idx',
    t.relname
)
FROM pg_class t
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE n.nspname = 'public' AND t.relname ~ '^[a-z][a-z_]*__particelle$'
\gexec

SELECT format('ANALYZE public.%I', t.relname)
FROM pg_class t
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE n.nspname = 'public' AND t.relname ~ '^[a-z][a-z_]*__particelle$'
\gexec
