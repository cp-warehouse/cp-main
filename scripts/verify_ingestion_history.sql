\set ON_ERROR_STOP on
BEGIN;
DO $$
DECLARE id uuid := gen_random_uuid(); initial_count bigint;
BEGIN
  INSERT INTO company(company_id,display_name,entity_kind,source_url)
  VALUES(id,'synthetic history fixture','organization','https://example.invalid');
  SELECT count(*) INTO initial_count FROM entity_history WHERE entity_id=id;
  IF initial_count <> 1 THEN RAISE EXCEPTION 'insert history missing'; END IF;
  UPDATE company SET collected_at=collected_at+interval '1 minute' WHERE company_id=id;
  IF (SELECT count(*) FROM entity_history WHERE entity_id=id) <> initial_count THEN
    RAISE EXCEPTION 'unchanged data created history';
  END IF;
  UPDATE company SET display_name='changed synthetic fixture' WHERE company_id=id;
  IF (SELECT count(*) FROM entity_history WHERE entity_id=id AND operation='UPDATE'
      AND before_data->>'display_name'='synthetic history fixture'
      AND after_data->>'display_name'='changed synthetic fixture') <> 1 THEN
    RAISE EXCEPTION 'before/after not retained';
  END IF;
  DELETE FROM company WHERE company_id=id;
  IF (SELECT count(*) FROM entity_history WHERE entity_id=id AND operation='DELETE') <> 1 THEN
    RAISE EXCEPTION 'delete history missing';
  END IF;
END $$;
ROLLBACK;
