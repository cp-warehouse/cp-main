\set ON_ERROR_STOP on
BEGIN;
DO $$
DECLARE cid uuid; did uuid; rid uuid := gen_random_uuid(); cid2 uuid;
BEGIN
  INSERT INTO company(display_name,entity_kind,source_url)
    VALUES('pipeline synthetic A','legal_entity','https://example.test') RETURNING company_id INTO cid;
  INSERT INTO company(display_name,entity_kind,source_url)
    VALUES('pipeline synthetic A','legal_entity','https://example.test') RETURNING company_id INTO cid2;
  INSERT INTO company_identifier(company_id,namespace,external_value,source_url)
    VALUES(cid,'test-pipeline',rid::text,'https://example.test');
  BEGIN
    INSERT INTO company_identifier(company_id,namespace,external_value,source_url)
      VALUES(cid2,'test-pipeline',rid::text,'https://example.test');
    RAISE EXCEPTION 'duplicate external identity accepted';
  EXCEPTION WHEN unique_violation THEN NULL;
  END;
  INSERT INTO news_document(url,title,body,raw_uri,extractor_version,collected_at,run_id)
    VALUES('https://example.test/'||rid,'test','body','test.html','test',now(),rid)
    RETURNING document_id INTO did;
  INSERT INTO news_document(url,title,body,raw_uri,extractor_version,collected_at,run_id)
    VALUES('https://example.test/'||rid,'test','body','test.html','test',now(),rid)
    ON CONFLICT(url) DO NOTHING;
  IF (SELECT count(*) FROM news_document WHERE run_id=rid) <> 1 THEN
    RAISE EXCEPTION 'duplicate article';
  END IF;
  INSERT INTO company_news(company_id,document_id,query,relevance_status,evidence,run_id)
    VALUES(cid,did,'test','candidate','[]',rid),(cid2,did,'test','candidate','[]',rid);
  INSERT INTO company_news(company_id,document_id,query,relevance_status,evidence,run_id)
    VALUES(cid,did,'test','candidate','[]',rid) ON CONFLICT(company_id,document_id) DO NOTHING;
  IF (SELECT count(*) FROM company_news WHERE document_id=did) <> 2 THEN
    RAISE EXCEPTION 'many-company link or link deduplication failed';
  END IF;
END $$;
ROLLBACK;
