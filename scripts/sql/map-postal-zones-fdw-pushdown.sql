-- Run against aecs4u-stats as the owner of the foreign server (or a superuser).
-- Postal zone tiles filter source_cap.cap_subcomunali by geom. postgres_fdw
-- needs PostGIS listed as a shippable extension to send that bbox predicate to
-- the source instead of transferring every CAP geometry for each tile.
DO $map_postal_fdw$
DECLARE
    server_name name;
    configured_extensions text;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'postgis') THEN
        RAISE EXCEPTION 'PostGIS must be installed in aecs4u-stats before enabling FDW pushdown';
    END IF;

    SELECT fs.srvname
      INTO STRICT server_name
      FROM pg_foreign_table AS ft
      JOIN pg_class AS relation ON relation.oid = ft.ftrelid
      JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
      JOIN pg_foreign_server AS fs ON fs.oid = ft.ftserver
     WHERE namespace.nspname = 'source_cap'
       AND relation.relname = 'cap_subcomunali';

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
$map_postal_fdw$;
