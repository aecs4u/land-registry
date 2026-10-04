-- Publish all enriched PVP sales with best-available addresses, then expose
-- the view through aecs4u-stats without copying its rows.
--
-- Run as a PostgreSQL role with access to both local databases:
--   psql -X -U postgres -d postgres -f scripts/sql/pvp-enriched-modelview-map-view.sql

\connect pvp_enriched_modelview

CREATE EXTENSION IF NOT EXISTS postgis;

-- Keep every sale in the view. Prefer a valid coordinate, then fall back
-- through normalized sale, asset, lot, and sale-address links. Map clients can
-- use `geom IS NOT NULL` for spatial rendering while still querying the full
-- sales set for tables and address searches.
DROP VIEW IF EXISTS public.v_map_sales;
CREATE VIEW public.v_map_sales AS
SELECT s.id AS sale_id,
       COALESCE(s.pvp_id, s.id) AS detail_id,
       s.source,
       COALESCE(NULLIF(BTRIM(s.city), ''), NULLIF(BTRIM(address.foreign_municipality), '')) AS city,
       s.province,
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
       address.latitude,
       address.longitude,
       s.geocoding_source,
       CASE
           WHEN address.latitude BETWEEN -90 AND 90
            AND address.longitude BETWEEN -180 AND 180
           THEN ST_SetSRID(ST_MakePoint(address.longitude, address.latitude), 4326)::geometry(Point, 4326)
       END AS geom
  FROM public.modelview_sales AS s
  LEFT JOIN LATERAL (
       SELECT candidate.address_source,
              candidate.postal_code,
              candidate.municipality_code,
              candidate.street,
              candidate.house_number,
              candidate.address_text,
              candidate.foreign_municipality,
              c.latitude,
              c.longitude
         FROM (
               SELECT 'sale.address_id'::text AS address_source,
                      1 AS source_priority,
                      a.id AS address_id,
                      a.postal_code,
                      a.municipality_code,
                      a.street,
                      a.house_number,
                      NULL::text AS address_text,
                      a.foreign_municipality,
                      a.coordinate_id
                 FROM public.modelview_addresses AS a
                WHERE a.id = s.address_id
               UNION ALL
               SELECT 'asset.address_id',
                      2,
                      a.id,
                      a.postal_code,
                      a.municipality_code,
                      a.street,
                      a.house_number,
                      ast.address_text,
                      a.foreign_municipality,
                      a.coordinate_id
                 FROM public.modelview_assets AS ast
                 LEFT JOIN public.modelview_addresses AS a ON a.id = ast.address_id
                WHERE ast.sale_id = s.id
                  AND (a.id IS NOT NULL OR NULLIF(BTRIM(ast.address_text), '') IS NOT NULL)
               UNION ALL
               SELECT 'lot.address_id',
                      3,
                      a.id,
                      a.postal_code,
                      a.municipality_code,
                      a.street,
                      a.house_number,
                      NULL::text,
                      a.foreign_municipality,
                      a.coordinate_id
                 FROM public.modelview_lots AS lot
                 JOIN public.modelview_addresses AS a ON a.id = lot.address_id
                WHERE lot.sale_id = s.id
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
                 FROM public.modelview_addresses AS a
                WHERE a.sale_id = s.id
         ) AS candidate
         LEFT JOIN public.modelview_coordinates AS c ON c.id = candidate.coordinate_id
        ORDER BY (
                     c.latitude BETWEEN -90 AND 90
                 AND c.longitude BETWEEN -180 AND 180
                 ) DESC NULLS LAST,
                 candidate.source_priority,
                 candidate.address_id NULLS LAST
        LIMIT 1
  ) AS address ON TRUE;

COMMENT ON VIEW public.v_map_sales IS
    'All enriched Italian PVP sales with best-available address fields; geom is a WGS84 point when a valid linked coordinate exists.';

\connect aecs4u-stats

CREATE EXTENSION IF NOT EXISTS postgres_fdw;
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE SERVER IF NOT EXISTS pvp_enriched_modelview_source
    FOREIGN DATA WRAPPER postgres_fdw
    OPTIONS (host '/var/run/postgresql', port '5432', dbname 'pvp_enriched_modelview');

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_user_mappings
         WHERE srvname = 'pvp_enriched_modelview_source'
           AND usename = current_user
    ) THEN
        EXECUTE format(
            'CREATE USER MAPPING FOR %I SERVER pvp_enriched_modelview_source OPTIONS (user %L)',
            current_user,
            current_user
        );
    END IF;
END
$$;

CREATE SCHEMA IF NOT EXISTS pvp;

-- Recreate this foreign table when the source view's projection changes.
DROP FOREIGN TABLE IF EXISTS pvp.v_map_sales;
CREATE FOREIGN TABLE pvp.v_map_sales (
    sale_id bigint,
    detail_id bigint,
    source text,
    city text,
    province text,
    postal_code text,
    municipality_code text,
    street text,
    house_number text,
    address text,
    address_source text,
    property_type text,
    description text,
    price double precision,
    sale_datetime timestamp without time zone,
    url text,
    latitude double precision,
    longitude double precision,
    geocoding_source text,
    geom geometry(Point, 4326)
) SERVER pvp_enriched_modelview_source
  OPTIONS (schema_name 'public', table_name 'v_map_sales');

COMMENT ON FOREIGN TABLE pvp.v_map_sales IS
    'Live foreign table for pvp_enriched_modelview.public.v_map_sales; all sale rows and address data remain in the source database.';
