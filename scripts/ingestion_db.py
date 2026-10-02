"""Local Compose database helpers. Never interpolate unescaped source text."""
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def literal(value):
    return "convert_from(decode('%s','hex'),'UTF8')" % str(value).encode().hex()


def json_sql(value):
    return literal(json.dumps(value, ensure_ascii=False)) + '::jsonb'


def execute(sql):
    result = subprocess.run([
        'docker', 'compose', '-f', str(ROOT / 'infra/postgres/compose.yaml'),
        'exec', '-T', 'db', 'psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1',
        '-U', 'company_dev', '-d', 'company_analysis'],
        input=sql, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError('PostgreSQL command failed; transaction not completed')
    return result.stdout.strip()
