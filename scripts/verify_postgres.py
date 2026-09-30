#!/usr/bin/env python3
"""Verify disposable PostgreSQL resources; never operate on the development volume."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]


def main():
    token = uuid.uuid4().hex[:12]
    project = f"cp-main-check-{token}"
    volume = f"{project}-data"
    env = dict(os.environ, DB_PASSWORD=secrets.token_urlsafe(32),
               DB_PORT="0", DB_VOLUME_NAME=volume)
    compose = ["docker", "compose", "--env-file", os.devnull,
               "-f", str(ROOT / "infra/postgres/compose.yaml"), "-p", project]

    def run(args, data=None):
        return subprocess.run(args, input=data, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, env=env, check=True).stdout

    def dc(*args, data=None):
        return run(compose + list(args), data)

    def sql(statement, database="company_analysis"):
        return dc("exec", "-T", "db", "psql", "-X", "-v", "ON_ERROR_STOP=1",
                  "-At", "-U", "company_dev", "-d", database, data=statement.encode()).decode().strip()

    def snapshot(database="company_analysis"):
        tables = ["company", "company_identifier", "product", "financial_observation"]
        return {table: json.loads(sql(
            f"SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text), '[]'::jsonb) FROM {table} t;",
            database)) for table in tables}

    run(["docker", "info", "--format", "{{.ServerVersion}}"])
    dc("config", "--quiet")
    # Only this newly named, explicitly labelled resource may be removed below.
    run(["docker", "volume", "create", "--label", f"cp-main.verification={token}", volume])
    try:
        dc("up", "-d", "--wait", "--wait-timeout", "120")
        table_count = sql("SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE';")
        assert table_count == "4", table_count
        fixture = (ROOT / "scripts/verification.sql").read_bytes()
        dc("exec", "-T", "db", "psql", "-X", "-v", "ON_ERROR_STOP=1",
           "-U", "company_dev", "-d", "company_analysis", data=fixture)
        expected = snapshot()
        assert {t: len(rows) for t, rows in expected.items()} == {
            "company": 1, "company_identifier": 1, "product": 1, "financial_observation": 2}
        assert {r["value"] for r in expected["financial_observation"]} == {100, 110}
        print("PASS: four tables, FK/unique/check constraints, two financial revisions", flush=True)

        dc("stop")
        dc("up", "-d", "--wait", "--wait-timeout", "120")
        assert snapshot() == expected
        print("PASS: data unchanged after stop/start", flush=True)

        old_id = dc("ps", "-q", "db").strip()
        dc("down")
        dc("up", "-d", "--wait", "--wait-timeout", "120")
        assert dc("ps", "-q", "db").strip() != old_id
        assert snapshot() == expected
        print("PASS: new container reuses volume and retains all rows", flush=True)

        backup = dc("exec", "-T", "db", "pg_dump", "-U", "company_dev", "-d", "company_analysis", "-Fc")
        dc("exec", "-T", "db", "createdb", "-U", "company_dev", "restore_check")
        dc("exec", "-T", "db", "pg_restore", "-U", "company_dev", "-d", "restore_check",
           "--exit-on-error", data=backup)
        assert snapshot("restore_check") == expected
        print("PASS: pg_dump restored into separate database; every table/row/field matches", flush=True)
        print("PostgreSQL: " + sql("SHOW server_version;"), flush=True)
    except subprocess.CalledProcessError as error:
        # psql/Docker diagnostics may include synthetic fixture SQL; no credentials in command args.
        print(error.stderr.decode(errors="replace"), flush=True)
        raise
    finally:
        dc("down")
        labels = json.loads(run(["docker", "volume", "inspect", volume]))[0].get("Labels", {})
        assert labels.get("cp-main.verification") == token, "Refusing to remove an unowned volume"
        run(["docker", "volume", "rm", volume])
        print("Cleaned up this run's disposable container, network and volume", flush=True)


if __name__ == "__main__":
    main()
