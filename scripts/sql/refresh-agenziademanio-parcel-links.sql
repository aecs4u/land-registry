-- Schema for the locally stored spatial concession-to-parcel crosswalk.
-- The refresh script computes matches against the separate `cadastral`
-- database and replaces the rows atomically in `aecs4u-stats`.

CREATE SCHEMA IF NOT EXISTS agenziademanio;

CREATE TABLE IF NOT EXISTS agenziademanio.concession_parcel_links (
    snapshot_id text NOT NULL,
    concession_row_id bigint NOT NULL,
    idconc text,
    admin_label text,
    concession_source_release text,
    geometry_valid_4326 boolean,
    parcel_id bigint NOT NULL,
    canonical_reference text NOT NULL,
    parcel_source_releases text[] NOT NULL DEFAULT ARRAY[]::text[],
    match_method text NOT NULL CHECK (match_method IN ('polygon_overlap', 'point_covered')),
    intersection_area_sqm double precision,
    computed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (snapshot_id, concession_row_id, parcel_id, canonical_reference),
    CHECK (
        (match_method = 'polygon_overlap'
            AND intersection_area_sqm IS NOT NULL
            AND intersection_area_sqm > 0)
        OR (match_method = 'point_covered' AND intersection_area_sqm IS NULL)
    )
);

ALTER TABLE agenziademanio.concession_parcel_links
    ADD COLUMN IF NOT EXISTS admin_label text;

CREATE INDEX IF NOT EXISTS concession_parcel_links_concession_idx
    ON agenziademanio.concession_parcel_links (snapshot_id, idconc);
CREATE INDEX IF NOT EXISTS concession_parcel_links_parcel_idx
    ON agenziademanio.concession_parcel_links (parcel_id);
