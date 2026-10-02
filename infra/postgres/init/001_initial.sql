-- Four-table initial proposal. Applied only to an empty PostgreSQL data directory.
-- Independent of the previous 23-table ERD; not a migration of existing data.
BEGIN;

CREATE TABLE company (
    company_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    legal_name text,
    display_name text NOT NULL CHECK (btrim(display_name) <> ''),
    website_url text,
    entity_kind text NOT NULL CHECK (entity_kind IN ('legal_entity', 'organization')),
    source_url text NOT NULL,
    collected_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE company_identifier (
    identifier_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid NOT NULL REFERENCES company(company_id),
    namespace text NOT NULL CHECK (btrim(namespace) <> ''),
    external_value text NOT NULL CHECK (btrim(external_value) <> ''),
    source_url text NOT NULL,
    collected_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (namespace, external_value)
);
CREATE INDEX company_identifier_company_idx ON company_identifier(company_id);

CREATE TABLE product (
    product_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid NOT NULL REFERENCES company(company_id),
    name text NOT NULL CHECK (btrim(name) <> ''),
    website_url text,
    source_url text NOT NULL,
    collected_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX product_company_idx ON product(company_id);
CREATE UNIQUE INDEX product_company_website_unique_idx
    ON product(company_id, website_url) WHERE website_url IS NOT NULL;
COMMENT ON COLUMN product.company_id IS 'Initial scope: one verified current operator per product; no operator history.';

CREATE TABLE financial_observation (
    financial_observation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid NOT NULL REFERENCES company(company_id),
    account_code text,
    account_name text NOT NULL,
    value numeric(30,6),
    currency text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    fiscal_year integer NOT NULL,
    report_code text NOT NULL,
    statement_scope text NOT NULL CHECK (statement_scope IN ('consolidated', 'separate')),
    period_start date NOT NULL,
    period_end date NOT NULL CHECK (period_end >= period_start),
    period_basis text NOT NULL CHECK (period_basis IN ('instant', 'quarter', 'ytd', 'annual')),
    source_url text NOT NULL,
    source_key text NOT NULL,
    source_revision text NOT NULL,
    source_record_key text NOT NULL,
    extractor_version text NOT NULL,
    raw_uri text NOT NULL,
    collected_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (company_id, source_key, source_revision, source_record_key, extractor_version)
);
CREATE INDEX financial_company_period_idx ON financial_observation(company_id, period_end);
COMMENT ON COLUMN financial_observation.value IS 'Normalized currency units, e.g. KRW won; missing value is NULL, not zero.';
COMMENT ON COLUMN financial_observation.source_key IS 'Stable source request identity, including query scope; excludes credentials.';
COMMENT ON COLUMN financial_observation.source_revision IS 'Filing revision ID or immutable local snapshot ID; reuse for retries, new ID for corrections.';
COMMENT ON COLUMN financial_observation.raw_uri IS 'Immutable source file location outside the database; backup separately.';
COMMENT ON TABLE financial_observation IS 'Insert new corrections as new revisions. This schema does not prevent UPDATE or DELETE.';

COMMIT;
