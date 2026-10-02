CREATE SCHEMA IF NOT EXISTS bench;

-- One row per provider per data year (grain of CMS "by Provider" file).
CREATE TABLE IF NOT EXISTS bench.provider_year (
    data_year               integer      NOT NULL,
    npi                     bigint       NOT NULL,
    provider_type           text         NOT NULL,
    state                   text         NOT NULL,
    entity_code             text,
    total_benes             integer      NOT NULL,
    total_srvcs             numeric      NOT NULL,
    total_submitted_charges numeric      NOT NULL,
    total_allowed           numeric      NOT NULL,
    total_payment           numeric      NOT NULL,
    avg_risk_score          numeric,
    drug_payment            numeric,              -- Part B drug share of total_payment; NULL when CMS suppresses it
    data_source             text         NOT NULL,   -- cms-api | csv | synthetic
    loaded_at               timestamptz  NOT NULL DEFAULT now(),
    PRIMARY KEY (data_year, npi)
);
ALTER TABLE bench.provider_year ADD COLUMN IF NOT EXISTS drug_payment numeric;   -- databases created before it existed
CREATE INDEX IF NOT EXISTS ix_provider_year_spec ON bench.provider_year (data_year, provider_type, state);

-- Audit trail: one row per pipeline execution.
CREATE TABLE IF NOT EXISTS bench.pipeline_run (
    run_id        serial PRIMARY KEY,
    started_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz,
    source        text NOT NULL,
    years         text,
    rows_loaded   integer,
    rows_rejected integer,
    status        text NOT NULL DEFAULT 'running',
    detail        text
);
