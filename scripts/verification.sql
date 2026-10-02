-- Synthetic fixtures only. This file is not mounted as an initialization script.
INSERT INTO company (company_id, display_name, entity_kind, source_url)
VALUES ('11111111-1111-1111-1111-111111111111', 'Synthetic Test Company', 'organization', 'https://example.invalid/company');

INSERT INTO company_identifier (company_id, namespace, external_value, source_url)
VALUES ('11111111-1111-1111-1111-111111111111', 'test.company', 'test-001', 'https://example.invalid/company');

INSERT INTO product (company_id, name, source_url)
VALUES ('11111111-1111-1111-1111-111111111111', 'Synthetic Product', 'https://example.invalid/product');

INSERT INTO financial_observation (
    company_id, account_name, source_account_name, value, currency, fiscal_year, report_code,
    statement_scope, period_start, period_end, period_basis, source_url,
    source_key, source_revision, source_record_key, extractor_version, raw_uri
) VALUES (
    '11111111-1111-1111-1111-111111111111', 'Revenue', 'Revenue', 100, 'KRW', 2025, 'test-annual',
    'consolidated', '2025-01-01', '2025-12-31', 'annual', 'https://example.invalid/ir',
    'test:annual:2025', 'revision-1', 'revenue', 'v1', 'test://revision-1.json'
), (
    '11111111-1111-1111-1111-111111111111', 'Revenue', 'Revenue', 110, 'KRW', 2025, 'test-annual',
    'consolidated', '2025-01-01', '2025-12-31', 'annual', 'https://example.invalid/ir',
    'test:annual:2025', 'revision-2', 'revenue', 'v1', 'test://revision-2.json'
);

DO $$
BEGIN
    BEGIN
        INSERT INTO company_identifier (company_id, namespace, external_value, source_url)
        VALUES ('11111111-1111-1111-1111-111111111111', 'test.company', 'test-001', 'https://example.invalid/duplicate');
        RAISE EXCEPTION 'Duplicate external ID was accepted';
    EXCEPTION WHEN unique_violation THEN NULL;
    END;
    BEGIN
        INSERT INTO product (company_id, name, source_url)
        VALUES ('22222222-2222-2222-2222-222222222222', 'Orphan', 'https://example.invalid/orphan');
        RAISE EXCEPTION 'Missing company was accepted';
    EXCEPTION WHEN foreign_key_violation THEN NULL;
    END;
    BEGIN
        INSERT INTO financial_observation (
            company_id, account_name, source_account_name, value, currency, fiscal_year, report_code,
            statement_scope, period_start, period_end, period_basis, source_url,
            source_key, source_revision, source_record_key, extractor_version, raw_uri
        ) SELECT company_id, account_name, source_account_name, value, currency, fiscal_year, report_code,
            statement_scope, period_start, period_end, period_basis, source_url,
            source_key, source_revision, source_record_key, extractor_version, raw_uri
          FROM financial_observation WHERE source_revision='revision-1';
        RAISE EXCEPTION 'Repeated financial record was accepted';
    EXCEPTION WHEN unique_violation THEN NULL;
    END;
    BEGIN
        UPDATE financial_observation SET period_end='2024-12-31';
        RAISE EXCEPTION 'Invalid period was accepted';
    EXCEPTION WHEN check_violation THEN NULL;
    END;
    BEGIN
        UPDATE financial_observation SET currency='won';
        RAISE EXCEPTION 'Invalid currency was accepted';
    EXCEPTION WHEN check_violation THEN NULL;
    END;
    BEGIN
        UPDATE financial_observation SET statement_scope='unsupported';
        RAISE EXCEPTION 'Invalid scope was accepted';
    EXCEPTION WHEN check_violation THEN NULL;
    END;
END $$;
