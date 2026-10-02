"""Small, replayable DART company ingestion; Python stdlib + Docker psql."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime, timezone
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ingestion_http import get

ROOT = Path(__file__).resolve().parents[1]
BASE = 'https://opendart.fss.or.kr/api/'
ZIP_ENDPOINTS = {'corpCode.xml', 'fnlttXbrl.xml', 'document.xml'}


class DartAccessError(RuntimeError):
    pass


def api_key():
    key = os.environ.get('OPENDART_API_KEY')
    if not key and (ROOT / '.env').exists():
        for line in (ROOT / '.env').read_text().splitlines():
            if line.strip().startswith('OPENDART_API_KEY='):
                key = line.split('=', 1)[1].strip().strip('\"\'')
    if not key or not re.fullmatch(r'[A-Za-z0-9]{40}', key):
        raise ValueError('루트 .env의 OPENDART_API_KEY 확인 필요')
    return key


def fetch(endpoint, params):
    url = BASE + endpoint + '?' + urllib.parse.urlencode({**params, 'crtfc_key': api_key()})
    try:
        raw, _, _, _ = get(url)
    except Exception as exc:
        # urllib errors can include the credential-bearing URL.
        raise RuntimeError('DART 요청 실패: ' + type(exc).__name__) from None
    stamp = datetime.now(timezone.utc).isoformat()
    digest = hashlib.sha256(raw).hexdigest()
    request_hash = hashlib.sha256(json.dumps([endpoint, params], sort_keys=True).encode()).hexdigest()
    folder = ROOT / 'data/raw/dart' / request_hash / digest
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ('response.zip' if endpoint in ZIP_ENDPOINTS else 'response.json')
    if not path.exists():
        path.write_bytes(raw)
    meta = folder / 'metadata.json'
    if not meta.exists():
        meta.write_text(json.dumps(dict(endpoint=endpoint, params=params, collected_at=stamp,
                                       sha256=digest, extractor_version='company-v1'), indent=2))
    if endpoint.endswith('.json'):
        body = json.loads(raw)
        if body.get('status') in ('010', '011', '012', '020'):
            raise DartAccessError('DART access or request limit; stop batch')
    return path


def discover(stock_code):
    path = fetch('corpCode.xml', {})
    if not zipfile.is_zipfile(path):
        raise ValueError('고유번호 응답이 ZIP이 아님; 원문 확인 필요')
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read('CORPCODE.xml'))
    matches = [{c.tag: c.text for c in row} for row in root.findall('list')
               if row.findtext('stock_code') == stock_code]
    if len(matches) != 1:
        raise ValueError('종목코드와 일치하는 기업이 정확히 1개가 아님')
    print(json.dumps({'match': matches[0], 'raw': str(path.relative_to(ROOT))}, ensure_ascii=False))


def transform(path):
    raw = path.read_bytes()
    meta = json.loads(path.with_name('metadata.json').read_text())
    if hashlib.sha256(raw).hexdigest() != meta['sha256']:
        raise ValueError('원문 해시 불일치')
    data = json.loads(raw)
    if data.get('status') != '000':
        raise ValueError('DART 응답 상태: ' + str(data.get('status')))
    code = meta['params'].get('corp_code', '')
    if meta['endpoint'] != 'company.json' or not re.fullmatch(r'\d{8}', code):
        raise ValueError('기업개황 요청 정보 불일치')
    name = (data.get('corp_name') or '').strip()
    if not name:
        raise ValueError('법인명 누락')
    stamp = datetime.fromisoformat(meta['collected_at'])
    if stamp.tzinfo is None:
        raise ValueError('수집 시각의 시간대 누락')
    website = (data.get('hm_url') or '').strip() or None
    # Preserve missing protocol rather than inventing the website's HTTPS support.
    if website and '://' in website and urllib.parse.urlsplit(website).scheme not in ('http', 'https'):
        raise ValueError('지원하지 않는 홈페이지 URL')
    return dict(corp_code=code, legal_name=name,
                display_name=(data.get('stock_name') or '').strip() or name,
                website_url=website, collected_at=stamp.isoformat(),
                source_url=BASE + 'company.json?corp_code=' + code)


def load(record):
    record = dict(record)
    record.setdefault('namespace', 'dart')
    record.setdefault('external_value', record.get('corp_code'))
    record.setdefault('entity_kind', 'legal_entity')
    if not record.get('external_value'):
        raise ValueError('기업 식별자 누락')
    # Hex encoding transports JSON as a SQL literal without interpolating source text.
    payload = json.dumps(record, ensure_ascii=False).encode().hex()
    sql = r"""
\set ON_ERROR_STOP on
BEGIN;
CREATE TEMP TABLE input AS SELECT convert_from(decode('%s','hex'),'UTF8')::jsonb AS d;
SELECT pg_advisory_xact_lock(hashtextextended((d->>'namespace') || ':' || (d->>'external_value'),0)) FROM input;
CREATE TEMP TABLE chosen AS
 SELECT COALESCE((SELECT company_id FROM company_identifier
 WHERE namespace=d->>'namespace' AND external_value=d->>'external_value'),gen_random_uuid()) AS id FROM input;
INSERT INTO company(company_id,legal_name,display_name,website_url,entity_kind,source_url,collected_at)
 SELECT id,d->>'legal_name',d->>'display_name',d->>'website_url',d->>'entity_kind',
 d->>'source_url',(d->>'collected_at')::timestamptz FROM input CROSS JOIN chosen
 ON CONFLICT(company_id) DO UPDATE SET
 legal_name=EXCLUDED.legal_name, display_name=EXCLUDED.display_name,
 website_url=COALESCE(EXCLUDED.website_url,company.website_url),
 source_url=EXCLUDED.source_url, collected_at=EXCLUDED.collected_at
 WHERE company.collected_at < EXCLUDED.collected_at;
INSERT INTO company_identifier(company_id,namespace,external_value,source_url,collected_at)
 SELECT id,d->>'namespace',d->>'external_value',d->>'source_url',(d->>'collected_at')::timestamptz
 FROM input CROSS JOIN chosen ON CONFLICT(namespace,external_value) DO NOTHING;
SELECT json_build_object('company_id',c.company_id,'name',c.legal_name,'dart',i.external_value)
 FROM company c JOIN company_identifier i USING(company_id) JOIN chosen x ON c.company_id=x.id;
COMMIT;
""" % payload
    result = subprocess.run(['docker','compose','-f',str(ROOT/'infra/postgres/compose.yaml'),
                             'exec','-T','db','psql','-X','-U','company_dev','-d','company_analysis'],
                            input=sql, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError('DB 적재 실패·트랜잭션 롤백: ' + result.stderr)
    print(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('discover').add_argument('--stock-code', required=True)
    sub.add_parser('fetch').add_argument('--corp-code', required=True)
    replay = sub.add_parser('replay')
    replay.add_argument('raw', type=Path)
    replay.add_argument('--apply', action='store_true', help='미지정 시 DB 변경 없는 미리보기')
    args = parser.parse_args()
    if args.command == 'discover':
        discover(args.stock_code)
    elif args.command == 'fetch':
        if not re.fullmatch(r'\d{8}', args.corp_code):
            raise ValueError('기업 코드는 8자리 숫자 문자열 필요')
        path = fetch('company.json', {'corp_code': args.corp_code})
        print(json.dumps({'raw': str(path.relative_to(ROOT)), 'preview': transform(path)}, ensure_ascii=False))
    else:
        record = transform(args.raw)
        if args.apply:
            load(record)
        else:
            print(json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
