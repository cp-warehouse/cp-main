SELECT c.display_name AS company, i.external_value AS dart_code,
       n.title, n.publisher, n.published_at, cn.relevance_status, n.url
FROM company_news cn
JOIN company c USING(company_id)
JOIN news_document n USING(document_id)
LEFT JOIN company_identifier i ON i.company_id=c.company_id AND i.namespace='dart'
ORDER BY cn.checked_at DESC, c.display_name LIMIT 30;
