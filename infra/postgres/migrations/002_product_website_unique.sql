-- Existing databases: prevent duplicate products for the same verified operator and canonical URL.
-- Run only after reviewing that the query below returns no rows:
-- SELECT company_id, website_url, count(*) FROM product
-- WHERE website_url IS NOT NULL GROUP BY company_id, website_url HAVING count(*) > 1;
BEGIN;

CREATE UNIQUE INDEX IF NOT EXISTS product_company_website_unique_idx
    ON product(company_id, website_url) WHERE website_url IS NOT NULL;

COMMIT;
