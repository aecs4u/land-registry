-- Backfill only the Veneto map import whose FlatGeobuf source is tagged by
-- source_release. The importer already stored the source local ID as
-- `source_feature_key` and the same reference as `canonical_reference`.
-- Two source checks confirmed the ID suffix equals NATIONALCADASTRALREFERENCE.
-- Geometry area is recomputed geodesically from the canonical WGS84 geometry.
WITH updated AS (
    UPDATE spatial.cadastral_parcel
    SET area_sqm = COALESCE(area_sqm, ST_Area(geom::geography)),
        national_cadastral_reference = COALESCE(
            national_cadastral_reference,
            CASE
                WHEN source_feature_key = 'veneto:particelle:IT.AGE.PLA.' || canonical_reference
                THEN canonical_reference
            END
        )
    WHERE source_release = 'cadastral.veneto.IT.duckdb'
      AND geom IS NOT NULL
      AND ST_SRID(geom) = 4326
      AND (area_sqm IS NULL OR national_cadastral_reference IS NULL)
    RETURNING national_cadastral_reference IS NOT NULL AS has_national_reference,
              area_sqm IS NOT NULL AS has_area
)
SELECT count(*) AS rows_updated,
       count(*) FILTER (WHERE has_national_reference) AS rows_with_national_reference,
       count(*) FILTER (WHERE has_area) AS rows_with_area
FROM updated;
