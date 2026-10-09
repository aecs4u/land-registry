-- Run against the `zornade` database (NOT aecs4u-stats) as the owner of the
-- foreign server (or a superuser).
-- OMI market-zone tiles read zornade.zornade_zone_omi in aecs4u-stats, which
-- is a view over zornade_remote_omi.zone_omi: a second foreign table here,
-- served by server zornade_omi (dbname=omi). map-market-zones-fdw-pushdown.sql
-- already lets aecs4u-stats ship the bbox to the zornade database; this lets
-- that database ship it on to the omi source instead of transferring every
-- zone polygon (tiles stay ~3s without it).
DO $map_market_source_fdw$
DECLARE
    server_name name;
    configured_extensions text;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'postgis') THEN
        RAISE EXCEPTION 'PostGIS must be installed in the zornade database before enabling FDW pushdown';
    END IF;

    SELECT fs.srvname
      INTO STRICT server_name
      FROM pg_foreign_table AS ft
      JOIN pg_class AS relation ON relation.oid = ft.ftrelid
      JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
      JOIN pg_foreign_server AS fs ON fs.oid = ft.ftserver
     WHERE namespace.nspname = 'zornade_remote_omi'
       AND relation.relname = 'zone_omi';

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
$map_market_source_fdw$;
