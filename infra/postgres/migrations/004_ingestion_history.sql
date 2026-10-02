CREATE TABLE ingestion_run (
    run_id uuid PRIMARY KEY,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    status text NOT NULL CHECK (status IN ('running','success','partial','failed','interrupted')),
    config_hash text NOT NULL,
    requested_year integer NOT NULL,
    retry_of uuid REFERENCES ingestion_run(run_id)
);

CREATE TABLE ingestion_result (
    run_id uuid NOT NULL REFERENCES ingestion_run(run_id),
    target_id text NOT NULL,
    label text NOT NULL,
    status text NOT NULL CHECK (status IN ('running','success','failed','interrupted')),
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    details jsonb NOT NULL DEFAULT '{}',
    error_type text,
    PRIMARY KEY (run_id,target_id)
);

CREATE TABLE entity_history (
    history_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_table text NOT NULL,
    entity_id uuid NOT NULL,
    operation text NOT NULL CHECK (operation IN ('baseline','INSERT','UPDATE','DELETE')),
    recorded_at timestamptz NOT NULL DEFAULT now(),
    before_data jsonb,
    after_data jsonb
);
CREATE INDEX entity_history_entity_idx ON entity_history(entity_table,entity_id,recorded_at);

CREATE FUNCTION record_entity_change() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE previous jsonb; current_row jsonb; identifier uuid;
BEGIN
    IF TG_OP <> 'INSERT' THEN previous := to_jsonb(OLD); END IF;
    IF TG_OP <> 'DELETE' THEN current_row := to_jsonb(NEW); END IF;
    IF TG_OP = 'UPDATE' AND
       (previous - 'collected_at' - 'created_at') =
       (current_row - 'collected_at' - 'created_at') THEN
        RETURN NEW;
    END IF;
    identifier := (COALESCE(current_row,previous)->>TG_ARGV[0])::uuid;
    INSERT INTO entity_history(entity_table,entity_id,operation,before_data,after_data)
    VALUES(TG_TABLE_NAME,identifier,TG_OP,previous,current_row);
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;

INSERT INTO entity_history(entity_table,entity_id,operation,after_data)
SELECT 'company',company_id,'baseline',to_jsonb(c) FROM company c;
INSERT INTO entity_history(entity_table,entity_id,operation,after_data)
SELECT 'product',product_id,'baseline',to_jsonb(p) FROM product p;
CREATE TRIGGER company_history AFTER INSERT OR UPDATE OR DELETE ON company
FOR EACH ROW EXECUTE FUNCTION record_entity_change('company_id');
CREATE TRIGGER product_history AFTER INSERT OR UPDATE OR DELETE ON product
FOR EACH ROW EXECUTE FUNCTION record_entity_change('product_id');

CREATE TABLE funding_evidence (
    company_id uuid NOT NULL REFERENCES company(company_id),
    stage text NOT NULL,
    announced_on date NOT NULL,
    source_url text NOT NULL,
    raw_uri text NOT NULL,
    checked_at timestamptz NOT NULL,
    PRIMARY KEY(company_id,source_url,stage,announced_on)
);
