-- Fast bulk publication for the auction map. v_map_sales remains the indexed
-- single-sale detail view. This view reads live source rows; no refresh job or
-- second copy of the sales dataset is required.
-- Apply after pvp-enriched-modelview-map-view.sql has installed map references.

\connect pvp_enriched

-- Keep ingestion writable while creating the narrow bulk-query indexes.
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_map_assets_address_links
    ON modelview.modelview_assets (sale_id, address_id)
    WHERE address_id IS NOT NULL;
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_map_assets_text_address_links
    ON modelview.modelview_assets (sale_id, address_id)
    WHERE NULLIF(BTRIM(address_text), '') IS NOT NULL;
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_map_lots_address_links
    ON modelview.modelview_lots (sale_id, address_id)
    WHERE address_id IS NOT NULL;
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_map_sales_address_links
    ON modelview.modelview_sales (id, address_id)
    WHERE address_id IS NOT NULL;
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_map_addresses_sale_links
    ON modelview.modelview_addresses (sale_id, id)
    WHERE sale_id IS NOT NULL;
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_map_sales_city_province
    ON modelview.modelview_sales (city, province);

CREATE OR REPLACE VIEW modelview.v_map_sale_points AS
WITH address_checks AS MATERIALIZED (
    -- Validate each address once, before assets/lots/sales reuse its location.
    -- Materialising these booleans prevents repeated PostGIS evaluation when
    -- the final query filters and projects both coordinate columns.
    SELECT a.id,
           a.municipality_code,
           c.latitude,
           c.longitude,
           c.latitude BETWEEN -90 AND 90
               AND c.longitude BETWEEN -180 AND 180 AS valid_coordinate,
           locality.pro_com IS NOT NULL AS has_locality,
           CASE WHEN locality.pro_com IS NOT NULL
                THEN ST_Covers(locality.geom, ST_SetSRID(ST_MakePoint(c.longitude, c.latitude), 4326))
           END AS covered,
           locality.point_latitude,
           locality.point_longitude
      FROM modelview.modelview_addresses AS a
      LEFT JOIN modelview.modelview_coordinates AS c ON c.id = a.coordinate_id
      LEFT JOIN modelview.map_pvp_municipalities AS municipality
        ON municipality.code = a.municipality_code
      LEFT JOIN modelview.istat_municipalities AS locality
        ON locality.pro_com = CASE WHEN municipality.code ~ '^[0-9]+$'
                                   THEN municipality.code::integer END
), candidate_links AS (
    SELECT asset.sale_id, asset.address_id, 1 AS source_priority, FALSE AS has_address_text
      FROM modelview.modelview_assets AS asset
     WHERE asset.address_id IS NOT NULL
    UNION ALL
    SELECT asset.sale_id, NULL::bigint, 1, TRUE
      FROM modelview.modelview_assets AS asset
      LEFT JOIN address_checks AS address ON address.id = asset.address_id
     WHERE NULLIF(BTRIM(asset.address_text), '') IS NOT NULL AND address.id IS NULL
    UNION ALL
    SELECT lot.sale_id, lot.address_id, 2, FALSE
      FROM modelview.modelview_lots AS lot
     WHERE lot.address_id IS NOT NULL
    UNION ALL
    SELECT sale.id, sale.address_id, 3, FALSE
      FROM modelview.modelview_sales AS sale
     WHERE sale.address_id IS NOT NULL
    UNION ALL
    SELECT address.sale_id, address.id, 4, FALSE
      FROM modelview.modelview_addresses AS address
     WHERE address.sale_id IS NOT NULL
), best_addresses AS MATERIALIZED (
    SELECT DISTINCT ON (sale.id)
           sale.id AS sale_id,
           address.municipality_code,
           CASE WHEN address.has_locality
                      AND (address.latitude IS NULL OR address.longitude IS NULL OR NOT address.covered)
                THEN address.point_latitude ELSE address.latitude END AS latitude,
           CASE WHEN address.has_locality
                      AND (address.latitude IS NULL OR address.longitude IS NULL OR NOT address.covered)
                THEN address.point_longitude ELSE address.longitude END AS longitude,
           COALESCE(address.has_locality
                      AND (address.latitude IS NULL OR address.longitude IS NULL OR NOT address.covered), FALSE)
                AS coordinate_is_approximate
      FROM candidate_links AS link
      JOIN modelview.modelview_sales AS sale ON sale.id = link.sale_id
      LEFT JOIN address_checks AS address ON address.id = link.address_id
     WHERE address.id IS NOT NULL OR (link.source_priority = 1 AND link.has_address_text)
     ORDER BY sale.id,
              CASE WHEN address.id IS NULL THEN FALSE
                   ELSE address.has_locality AND address.valid_coordinate AND address.covered
              END DESC NULLS LAST,
              address.valid_coordinate DESC NULLS LAST,
              link.source_priority,
              address.id NULLS LAST
), city_keys AS (
    SELECT DISTINCT lower(BTRIM(s.city)) AS city_key,
                    lower(BTRIM(COALESCE(s.province, ''))) AS province_key
      FROM modelview.modelview_sales AS s
     WHERE NULLIF(BTRIM(s.city), '') IS NOT NULL
), city_matches AS MATERIALIZED (
    -- Match each distinct city/province, rather than looking up half a million
    -- addresses without municipality codes separately.
    SELECT DISTINCT ON (city.city_key, city.province_key)
           city.city_key, city.province_key,
           CASE WHEN municipality.code ~ '^[0-9]+$'
                THEN municipality.code::integer END AS pro_com
      FROM city_keys AS city
      JOIN modelview.map_pvp_municipalities AS municipality
        ON lower(BTRIM(municipality.name)) = city.city_key
      LEFT JOIN modelview.map_pvp_provinces AS province
        ON province.code = municipality.province_code
     WHERE city.province_key = ''
        OR lower(BTRIM(province.name)) = city.province_key
        OR lower(BTRIM(province.code)) = city.province_key
     ORDER BY city.city_key, city.province_key, municipality.code
), location_checks AS MATERIALIZED (
    SELECT s.id AS sale_id,
           address.latitude,
           address.longitude,
           COALESCE(address.coordinate_is_approximate, FALSE) AS address_is_approximate,
           locality.point_latitude,
           locality.point_longitude,
           locality.pro_com IS NOT NULL
               AND (address.latitude IS NULL OR address.longitude IS NULL
                    OR NOT ST_Covers(locality.geom, ST_SetSRID(ST_MakePoint(address.longitude, address.latitude), 4326)))
               AS city_is_approximate
      FROM modelview.modelview_sales AS s
      LEFT JOIN best_addresses AS address ON address.sale_id = s.id
      LEFT JOIN city_matches AS city
        ON address.municipality_code IS NULL
       AND city.city_key = lower(BTRIM(s.city))
       AND city.province_key = lower(BTRIM(COALESCE(s.province, '')))
      LEFT JOIN modelview.istat_municipalities AS locality ON locality.pro_com = city.pro_com
)
SELECT location.sale_id,
       CASE WHEN city_is_approximate THEN point_latitude ELSE latitude END AS latitude,
       CASE WHEN city_is_approximate THEN point_longitude ELSE longitude END AS longitude,
       sale.base_auction_price AS price,
       sale.sale_datetime,
       sale.property_type,
       address_is_approximate OR city_is_approximate AS coordinate_is_approximate,
       CASE WHEN NULLIF(BTRIM(sale.property_type), '') IS NULL
            THEN left(sale.description, 240) END AS description_hint
  FROM location_checks AS location
  JOIN modelview.modelview_sales AS sale ON sale.id = location.sale_id;

COMMENT ON VIEW modelview.v_map_sale_points IS
    'Live bulk auction map points; batched address ranking and municipality coordinate validation preserve v_map_sales locations.';

\connect aecs4u-stats

CREATE FOREIGN TABLE IF NOT EXISTS pvp.v_map_sale_points (
    sale_id bigint,
    latitude double precision,
    longitude double precision,
    price double precision,
    sale_datetime timestamp without time zone,
    property_type character varying,
    coordinate_is_approximate boolean,
    description_hint text
) SERVER pvp_enriched_modelview_source
  OPTIONS (schema_name 'modelview', table_name 'v_map_sale_points', fetch_size '10000');

-- Transfer the bulk result in larger batches when only the stats DSN is
-- configured. postgres_fdw otherwise fetches just 100 records per round trip.

COMMENT ON FOREIGN TABLE pvp.v_map_sale_points IS
    'Compact live map points from pvp_enriched; sale details remain in pvp.v_map_sales.';
