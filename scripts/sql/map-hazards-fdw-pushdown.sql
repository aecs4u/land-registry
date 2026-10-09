-- Run against aecs4u-stats as the owner of hazards_source (or a superuser).
-- Hazard map tiles use PostGIS bbox predicates. postgres_fdw must be allowed to
-- ship those operators so the remote GiST indexes filter features before
-- geometries cross the connection.
DO $map_hazards_fdw$
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
     WHERE namespace.nspname = 'hazards'
       AND relation.relname IN (
           'flood_hazard', 'landslide_hazard', 'mps04_points',
           'v_seismic_classification_map'
       );

    IF server_count <> 1 THEN
        RAISE EXCEPTION 'Expected the hazard map relations to use one foreign server; found %', server_count;
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
$map_hazards_fdw$;
