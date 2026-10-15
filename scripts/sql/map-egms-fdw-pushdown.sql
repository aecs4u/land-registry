-- Run against aecs4u-stats as the owner of egms_source (or a superuser).
-- Parcel and point lookups on geo_surface_change.egms_subsidenza use PostGIS
-- predicates such as ST_Intersects. The egms_source foreign server does not
-- list postgis in its ``extensions`` option, so postgres_fdw cannot ship those
-- predicates: the 8.5 million cells cross the connection and are filtered
-- locally (about 39 s for a single point on 2026-10-10). With the option set
-- the remote GiST index, if present, filters them first.
--
-- After applying, confirm in the ``egms`` database that
-- ``public.egms_subsidenza`` has a GiST index on ``geom``; create it there
-- with ``CREATE INDEX CONCURRENTLY`` if it does not.
DO $map_egms_fdw$
DECLARE
    server_name name;
    server_count integer;
    configured_extensions text;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'postgis') THEN
        RAISE EXCEPTION 'PostGIS must be installed in aecs4u-stats before enabling FDW pushdown';
    END IF;

    SELECT count(DISTINCT fs.oid), min(fs.srvname)
      INTO server_count, server_name
      FROM pg_foreign_table AS ft
      JOIN pg_class AS relation ON relation.oid = ft.ftrelid
      JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
      JOIN pg_foreign_server AS fs ON fs.oid = ft.ftserver
     WHERE namespace.nspname = 'geo_surface_change'
       AND relation.relname = 'egms_subsidenza';

    IF server_count <> 1 THEN
        RAISE EXCEPTION 'Expected the EGMS relation to use one foreign server; found %', server_count;
    END IF;

    SELECT split_part(option_value, '=', 2)
      INTO configured_extensions
      FROM pg_foreign_server AS fs,
           LATERAL unnest(fs.srvoptions) AS options(option_value)
     WHERE fs.srvname = server_name
       AND option_value LIKE 'extensions=%';

    IF NOT EXISTS (
        SELECT 1
          FROM unnest(string_to_array(coalesce(configured_extensions, ''), ',')) AS extension_name
         WHERE btrim(extension_name) = 'postgis'
    ) THEN
        IF configured_extensions IS NULL THEN
            EXECUTE format(
                'ALTER SERVER %I OPTIONS (ADD extensions %L)', server_name, 'postgis'
            );
        ELSE
            EXECUTE format(
                'ALTER SERVER %I OPTIONS (SET extensions %L)',
                server_name,
                concat_ws(',', nullif(configured_extensions, ''), 'postgis')
            );
        END IF;
    END IF;
END
$map_egms_fdw$;
