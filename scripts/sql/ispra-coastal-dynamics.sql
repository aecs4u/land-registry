-- One-time provisioning for the versioned ISPRA 2006–2020 shoreline-change
-- snapshot. The refresh script stages a complete import before replacing it.
CREATE SCHEMA IF NOT EXISTS spatial;

CREATE TABLE IF NOT EXISTS spatial.ispra_coastal_dynamics (
    object_id bigint PRIMARY KEY,
    cod_istat integer,
    change_class text,
    coast_type integer,
    shoreline_length_m double precision,
    mean_variation_m double precision,
    max_variation_m double precision,
    severity_index integer,
    raw_attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    geom geometry(MultiLineString, 4326) NOT NULL,
    geom_metric geometry(MultiLineString, 6933) NOT NULL,
    source_release text NOT NULL DEFAULT 'ISPRA_LINEA_COSTA_2020_V2_DYNAMICA_2006_2020',
    imported_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ispra_coastal_dynamics_geom_metric_gix
    ON spatial.ispra_coastal_dynamics USING gist (geom_metric);
CREATE INDEX IF NOT EXISTS ispra_coastal_dynamics_change_class_idx
    ON spatial.ispra_coastal_dynamics (change_class);
