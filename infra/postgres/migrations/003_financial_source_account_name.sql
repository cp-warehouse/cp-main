\set ON_ERROR_STOP on
BEGIN;

ALTER TABLE financial_observation
    ADD COLUMN IF NOT EXISTS source_account_name text;

UPDATE financial_observation
SET source_account_name = account_name
WHERE source_account_name IS NULL;

ALTER TABLE financial_observation
    ALTER COLUMN source_account_name SET NOT NULL;

COMMENT ON COLUMN financial_observation.source_account_name IS
    'Account label exactly as supplied by the source; account_name is the normalized service label.';

COMMIT;
