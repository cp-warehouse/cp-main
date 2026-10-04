-- Company metadata belongs to company; only documents and links are new tables.
ALTER TABLE company ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}';
ALTER TABLE company ADD COLUMN IF NOT EXISTS metadata_raw_uri text;
ALTER TABLE company ADD COLUMN IF NOT EXISTS metadata_collected_at timestamptz;
DO $$ BEGIN
    IF to_regclass('public.company_profile') IS NOT NULL THEN
        UPDATE company c SET metadata=p.metadata,metadata_raw_uri=p.raw_uri,
            metadata_collected_at=p.collected_at FROM company_profile p
        WHERE c.company_id=p.company_id AND
            (c.metadata_collected_at IS NULL OR c.metadata_collected_at<=p.collected_at);
    END IF;
END $$;
CREATE TABLE IF NOT EXISTS news_document (
    document_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    url text NOT NULL UNIQUE,
    title text NOT NULL CHECK(btrim(title) <> ''),
    body text NOT NULL CHECK(btrim(body) <> ''),
    publisher text,
    published_at timestamptz,
    image_url text,
    raw_uri text NOT NULL,
    extractor_version text NOT NULL,
    collected_at timestamptz NOT NULL,
    run_id uuid NOT NULL
);
CREATE TABLE IF NOT EXISTS company_news (
    company_id uuid NOT NULL REFERENCES company(company_id),
    document_id uuid NOT NULL REFERENCES news_document(document_id),
    query text NOT NULL,
    relevance_status text NOT NULL CHECK(relevance_status IN ('candidate','unmatched')),
    evidence jsonb NOT NULL,
    run_id uuid NOT NULL,
    checked_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(company_id,document_id)
);
ALTER TABLE news_document DROP CONSTRAINT IF EXISTS news_document_run_id_fkey;
ALTER TABLE company_news DROP CONSTRAINT IF EXISTS company_news_run_id_fkey;
DROP TABLE IF EXISTS company_profile;
DROP TABLE IF EXISTS company_news_target;
DROP TABLE IF EXISTS collection_run;
CREATE INDEX IF NOT EXISTS company_news_document_idx ON company_news(document_id);
CREATE INDEX IF NOT EXISTS news_document_published_idx ON news_document(published_at DESC);
COMMENT ON COLUMN news_document.run_id IS 'Local checkpoint batch UUID; no execution table.';
COMMENT ON TABLE company_news IS 'Search/name-match candidates only; not confirmed legal-entity attribution.';
