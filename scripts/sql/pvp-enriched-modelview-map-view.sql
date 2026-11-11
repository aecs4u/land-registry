-- Publish all enriched PVP sales with best-available addresses in the pvp_enriched database.
--
-- Run as a PostgreSQL role with access to both local databases:
--   psql -X -U postgres -d postgres -f scripts/sql/pvp-enriched-modelview-map-view.sql

\connect pvp_enriched

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgres_fdw;
CREATE SCHEMA IF NOT EXISTS modelview;

-- The normalized coordinate FK can point at another locality after source
-- database merges. Use ISTAT municipality geometry to catch those bad links.
-- Municipality geometry comes from the ISTAT source database (public.v_comuni_map). It is copied into
-- modelview.istat_municipalities below, so the published views never read across databases at query time.
-- (This used to read the deprecated aecs4u-stats database; that server and its foreign table are replaced.)
DROP FOREIGN TABLE IF EXISTS istat_boundaries.comuni;
DROP SERVER IF EXISTS aecs4u_stats_map_reference CASCADE;
CREATE SERVER IF NOT EXISTS istat_map_reference
    FOREIGN DATA WRAPPER postgres_fdw
    OPTIONS (host '/var/run/postgresql', port '5432', dbname 'istat');

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_user_mappings
         WHERE srvname = 'istat_map_reference'
           AND usename = current_user
    ) THEN
        EXECUTE format(
            'CREATE USER MAPPING FOR %I SERVER istat_map_reference OPTIONS (user %L)',
            current_user,
            current_user
        );
    END IF;
END
$$;

CREATE SCHEMA IF NOT EXISTS istat_boundaries;
CREATE FOREIGN TABLE IF NOT EXISTS istat_boundaries.comuni (
    pro_com integer,
    cod_uts integer,
    cod_reg integer,
    cod_rip integer,
    name text,
    geom geometry(Geometry, 4326)
) SERVER istat_map_reference
  OPTIONS (schema_name 'public', table_name 'v_comuni_map');

CREATE TABLE IF NOT EXISTS modelview.istat_municipalities (
    pro_com integer PRIMARY KEY,
    name text NOT NULL,
    geom geometry(Geometry, 4326) NOT NULL,
    point_latitude double precision,
    point_longitude double precision
);
ALTER TABLE modelview.istat_municipalities
    ADD COLUMN IF NOT EXISTS point_latitude double precision,
    ADD COLUMN IF NOT EXISTS point_longitude double precision;
TRUNCATE modelview.istat_municipalities;
INSERT INTO modelview.istat_municipalities (pro_com, name, geom, point_latitude, point_longitude)
SELECT pro_com, name, geom,
       ST_Y(ST_PointOnSurface(geom)), ST_X(ST_PointOnSurface(geom))
  FROM istat_boundaries.comuni;
ANALYZE modelview.istat_municipalities;

CREATE TABLE IF NOT EXISTS modelview.map_pvp_municipalities (
    code text PRIMARY KEY,
    name text NOT NULL,
    province_code text
);
TRUNCATE modelview.map_pvp_municipalities;
INSERT INTO modelview.map_pvp_municipalities (code, name, province_code)
SELECT code, name, province_code FROM modelview.ref_pvp_municipalities;
ANALYZE modelview.map_pvp_municipalities;
CREATE INDEX IF NOT EXISTS map_pvp_municipalities_name_lookup
    ON modelview.map_pvp_municipalities (lower(BTRIM(name)));

CREATE TABLE IF NOT EXISTS modelview.map_pvp_provinces (
    code text PRIMARY KEY,
    name text NOT NULL
);
TRUNCATE modelview.map_pvp_provinces;
INSERT INTO modelview.map_pvp_provinces (code, name)
SELECT code, name FROM modelview.ref_pvp_provinces;
ANALYZE modelview.map_pvp_provinces;

-- Keep every sale in the view. Prefer a valid coordinate, starting with the
-- property-level asset/lot addresses (the place being auctioned), then fall
-- back to sale-level address links. Map clients can
-- use `geom IS NOT NULL` for spatial rendering while still querying the full
-- sales set for tables and address searches.
CREATE OR REPLACE VIEW modelview.v_map_sales AS
SELECT s.id AS sale_id,
       COALESCE(s.pvp_id, s.id) AS detail_id,
       s.source,
       COALESCE(NULLIF(BTRIM(address.municipality_name), ''), NULLIF(BTRIM(city_municipality.name), ''), NULLIF(BTRIM(s.city), ''), NULLIF(BTRIM(address.foreign_municipality), ''))::text AS city,
       COALESCE(NULLIF(BTRIM(address.province_name), ''), NULLIF(BTRIM(city_municipality.province_name), ''), s.province)::varchar AS province,
       COALESCE(NULLIF(BTRIM(address.postal_code), ''), NULLIF(BTRIM(s.postal_code), '')) AS postal_code,
       address.municipality_code,
       address.street,
       address.house_number,
       COALESCE(
           NULLIF(BTRIM(CONCAT_WS(' ', NULLIF(BTRIM(address.street), ''), NULLIF(BTRIM(address.house_number), ''))), ''),
           NULLIF(BTRIM(address.address_text), ''),
           NULLIF(BTRIM(s.address_legacy), '')
       ) AS address,
       COALESCE(
           address.address_source,
           CASE
               WHEN NULLIF(BTRIM(s.address_legacy), '') IS NOT NULL THEN 'sales.address_legacy'
               WHEN NULLIF(BTRIM(s.city), '') IS NOT NULL OR NULLIF(BTRIM(s.province), '') IS NOT NULL THEN 'sales.city_province'
           END
       ) AS address_source,
       s.property_type,
       s.description,
       s.base_auction_price AS price,
       s.sale_datetime,
       s.url,
       adjusted.latitude,
       adjusted.longitude,
       s.geocoding_source,
       CASE
           WHEN adjusted.latitude BETWEEN -90 AND 90
            AND adjusted.longitude BETWEEN -180 AND 180
           THEN ST_SetSRID(ST_MakePoint(adjusted.longitude, adjusted.latitude), 4326)::geometry(Point, 4326)
       END AS geom,
       adjusted.coordinate_is_approximate
  FROM modelview.modelview_sales AS s
  LEFT JOIN LATERAL (
       SELECT candidate.address_source,
              candidate.postal_code,
              candidate.municipality_code,
              candidate.street,
              candidate.house_number,
              candidate.address_text,
              candidate.foreign_municipality,
              address_municipality.name AS municipality_name,
              address_province.name AS province_name,
              locality.geom IS NOT NULL
                  AND (c.latitude IS NULL OR c.longitude IS NULL
                       OR NOT ST_Covers(locality.geom, ST_SetSRID(ST_MakePoint(c.longitude, c.latitude), 4326)))
                  AS coordinate_is_approximate,
              CASE
                  WHEN locality.geom IS NOT NULL
                   AND (c.latitude IS NULL OR c.longitude IS NULL
                        OR NOT ST_Covers(locality.geom, ST_SetSRID(ST_MakePoint(c.longitude, c.latitude), 4326)))
                  THEN locality.point_latitude
                  ELSE c.latitude
              END AS latitude,
              CASE
                  WHEN locality.geom IS NOT NULL
                   AND (c.latitude IS NULL OR c.longitude IS NULL
                        OR NOT ST_Covers(locality.geom, ST_SetSRID(ST_MakePoint(c.longitude, c.latitude), 4326)))
                  THEN locality.point_longitude
                  ELSE c.longitude
              END AS longitude
         FROM (
               SELECT 'asset.address_id'::text AS address_source,
                      1 AS source_priority,
                      a.id AS address_id,
                      a.postal_code,
                      a.municipality_code,
                      a.street,
                      a.house_number,
                      ast.address_text,
                      a.foreign_municipality,
                      a.coordinate_id
                 FROM modelview.modelview_assets AS ast
                 LEFT JOIN modelview.modelview_addresses AS a ON a.id = ast.address_id
                WHERE ast.sale_id = s.id
                  AND (a.id IS NOT NULL OR NULLIF(BTRIM(ast.address_text), '') IS NOT NULL)
               UNION ALL
               SELECT 'lot.address_id',
                      2,
                      a.id AS address_id,
                      a.postal_code,
                      a.municipality_code,
                      a.street,
                      a.house_number,
                      NULL::text AS address_text,
                      a.foreign_municipality,
                      a.coordinate_id
                 FROM modelview.modelview_lots AS lot
                 JOIN modelview.modelview_addresses AS a ON a.id = lot.address_id
                WHERE lot.sale_id = s.id
               UNION ALL
               SELECT 'sale.address_id',
                      3,
                      a.id,
                      a.postal_code,
                      a.municipality_code,
                      a.street,
                      a.house_number,
                      NULL::text,
                      a.foreign_municipality,
                      a.coordinate_id
                 FROM modelview.modelview_addresses AS a
                WHERE a.id = s.address_id
               UNION ALL
               SELECT 'addresses.sale_id',
                      4,
                      a.id,
                      a.postal_code,
                      a.municipality_code,
                      a.street,
                      a.house_number,
                      NULL::text,
                      a.foreign_municipality,
                      a.coordinate_id
                 FROM modelview.modelview_addresses AS a
                WHERE a.sale_id = s.id
         ) AS candidate
         LEFT JOIN modelview.modelview_coordinates AS c ON c.id = candidate.coordinate_id
         LEFT JOIN modelview.map_pvp_municipalities AS address_municipality
           ON address_municipality.code = candidate.municipality_code
         LEFT JOIN modelview.map_pvp_provinces AS address_province
           ON address_province.code = address_municipality.province_code
         LEFT JOIN modelview.istat_municipalities AS locality
           ON address_municipality.code ~ '^[0-9]+$'
          AND locality.pro_com = address_municipality.code::integer
        ORDER BY (
                     locality.geom IS NOT NULL
                 AND c.latitude BETWEEN -90 AND 90
                 AND c.longitude BETWEEN -180 AND 180
                 AND ST_Covers(locality.geom, ST_SetSRID(ST_MakePoint(c.longitude, c.latitude), 4326))
                 ) DESC NULLS LAST,
                 (
                     c.latitude BETWEEN -90 AND 90
                 AND c.longitude BETWEEN -180 AND 180
                 ) DESC NULLS LAST,
                 candidate.source_priority,
                 candidate.address_id NULLS LAST
        LIMIT 1
  ) AS address ON TRUE
  LEFT JOIN LATERAL (
       SELECT municipality.name,
              province.name AS province_name,
              municipality.code
         FROM modelview.map_pvp_municipalities AS municipality
         LEFT JOIN modelview.map_pvp_provinces AS province
           ON province.code = municipality.province_code
        WHERE address.municipality_code IS NULL
          AND lower(BTRIM(municipality.name)) = lower(BTRIM(s.city))
          AND (
               NULLIF(BTRIM(s.province), '') IS NULL
            OR lower(BTRIM(province.name)) = lower(BTRIM(s.province))
            OR upper(BTRIM(province.code)) = upper(BTRIM(s.province))
          )
        ORDER BY municipality.code
        LIMIT 1
  ) AS city_municipality ON TRUE
  LEFT JOIN modelview.istat_municipalities AS city_locality
    ON city_municipality.code ~ '^[0-9]+$'
   AND city_locality.pro_com = city_municipality.code::integer
  CROSS JOIN LATERAL (
       SELECT CASE
                  WHEN city_locality.geom IS NOT NULL
                   AND (address.latitude IS NULL OR address.longitude IS NULL
                        OR NOT ST_Covers(city_locality.geom, ST_SetSRID(ST_MakePoint(address.longitude, address.latitude), 4326)))
                  THEN city_locality.point_latitude
                  ELSE address.latitude
              END AS latitude,
              CASE
                  WHEN city_locality.geom IS NOT NULL
                   AND (address.latitude IS NULL OR address.longitude IS NULL
                        OR NOT ST_Covers(city_locality.geom, ST_SetSRID(ST_MakePoint(address.longitude, address.latitude), 4326)))
                  THEN city_locality.point_longitude
                  ELSE address.longitude
              END AS longitude,
              COALESCE(address.coordinate_is_approximate, FALSE)
              OR (city_locality.geom IS NOT NULL
                  AND (address.latitude IS NULL OR address.longitude IS NULL
                       OR NOT ST_Covers(city_locality.geom, ST_SetSRID(ST_MakePoint(address.longitude, address.latitude), 4326))))
                  AS coordinate_is_approximate
  ) AS adjusted;

COMMENT ON VIEW modelview.v_map_sales IS
    'All enriched Italian PVP sales with best-available address fields; geom is a WGS84 point when a valid linked coordinate exists.';

\ir pvp-enriched-modelview-map-points.sql
