"""Apply numbered SQL migrations transactionally with checksums and a DB lock."""
import argparse
import hashlib
import re
from ingestion_db import ROOT, execute, literal


def migration_body(text):
    # Existing standalone migration wrappers are replaced by the runner's transaction.
    return '\n'.join(line for line in text.splitlines()
                     if not re.fullmatch(r'\s*(BEGIN;|COMMIT;|\\set ON_ERROR_STOP on)\s*', line))


def apply():
    statements = ["BEGIN; SELECT pg_advisory_xact_lock(773144);",
        """CREATE TABLE IF NOT EXISTS schema_migration (
        version text PRIMARY KEY, checksum text NOT NULL,
        applied_at timestamptz NOT NULL DEFAULT now());"""]
    for path in sorted((ROOT / 'infra/postgres/migrations').glob('*.sql')):
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        name = literal(path.name)
        # Explicitly supported rewrite of the original, local-only 005 migration.
        # Only its exact former checksum is eligible; all other mismatches still fail.
        if path.name == '005_company_news_pipeline.sql':
            statements.append(f"DELETE FROM schema_migration WHERE version={name} AND checksum='9d77e7c1b34d3dd4c01cbb819888aa24588039bc56353b7edc2421f65c2af656';")
        statements.append(fr"""DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM schema_migration WHERE version={name}
                     AND checksum <> '{checksum}') THEN
            RAISE EXCEPTION 'Applied migration checksum changed';
          END IF;
        END $$;
        SELECT NOT EXISTS(SELECT 1 FROM schema_migration WHERE version={name}) AS pending
        \gset
        \if :pending
        {migration_body(path.read_text())}
        INSERT INTO schema_migration(version,checksum) VALUES ({name},'{checksum}');
        \endif
        """)
    statements.append('COMMIT; SELECT version FROM schema_migration ORDER BY version;')
    print(execute('\n'.join(statements)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--status', action='store_true')
    args = parser.parse_args()
    if args.status:
        print(execute('SELECT version,checksum,applied_at FROM schema_migration ORDER BY version;'))
    else:
        apply()
