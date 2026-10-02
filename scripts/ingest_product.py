"""Capture one official product page and replay it into the local PostgreSQL."""
import argparse
from datetime import datetime, timezone
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        if data.strip():
            self.parts.append(data.strip())


def canonical_url(value):
    parsed = urllib.parse.urlsplit(value.strip())
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        raise ValueError('HTTP(S) URL 필요')
    host = parsed.hostname.lower()
    port = f':{parsed.port}' if parsed.port else ''
    path = parsed.path or '/'
    return urllib.parse.urlunsplit((parsed.scheme.lower(), host + port, path, parsed.query, ''))


def fetch_page(corp_code, name, website_url, source_url):
    if not re.fullmatch(r'\d{8}', corp_code) or not name.strip():
        raise ValueError('DART 기업 코드 8자리와 제품명 필요')
    website_url, source_url = canonical_url(website_url), canonical_url(source_url)
    if urllib.parse.urlsplit(source_url).hostname not in ('navercorp.com', 'www.navercorp.com'):
        raise ValueError('첫 적재 출처는 NAVER Corp 공식 도메인 필요')
    request = urllib.request.Request(source_url, headers={'User-Agent':'cp-main-phase1/1.0'})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
            final_url = canonical_url(response.url)
            content_type = response.headers.get_content_type()
            status = response.status
    except Exception as exc:
        raise RuntimeError('공식 제품 페이지 요청 실패: ' + type(exc).__name__) from None
    if status != 200 or content_type != 'text/html':
        raise ValueError('공식 제품 페이지 응답 형식 확인 필요')
    if urllib.parse.urlsplit(final_url).hostname not in ('navercorp.com', 'www.navercorp.com'):
        raise ValueError('공식 도메인 밖으로 리디렉션됨')
    digest = hashlib.sha256(raw).hexdigest()
    params = {'corp_code':corp_code, 'name':name.strip(), 'website_url':website_url,
              'source_url':source_url, 'final_url':final_url}
    request_hash = hashlib.sha256(json.dumps(params,sort_keys=True).encode()).hexdigest()
    folder = ROOT/'data/raw/web'/request_hash/digest
    folder.mkdir(parents=True, exist_ok=True)
    path = folder/'response.html'
    if not path.exists():
        path.write_bytes(raw)
    meta = folder/'metadata.json'
    if not meta.exists():
        meta.write_text(json.dumps({**params, 'collected_at':datetime.now(timezone.utc).isoformat(),
                                    'sha256':digest, 'content_type':content_type}, indent=2))
    return path


def transform(path):
    raw = path.read_bytes()
    meta = json.loads(path.with_name('metadata.json').read_text())
    if hashlib.sha256(raw).hexdigest() != meta.get('sha256'):
        raise ValueError('제품 원문 해시 불일치')
    parser = TextExtractor()
    parser.feed(raw.decode('utf-8'))
    text = ' '.join(parser.parts)
    name = meta.get('name', '').strip()
    website = canonical_url(meta.get('website_url', ''))
    if name not in text:
        raise ValueError('공식 페이지에서 제품명 근거를 찾지 못함')
    decoded_html = raw.decode('utf-8').replace('&amp;', '&')
    if urllib.parse.urlsplit(website).hostname not in decoded_html:
        raise ValueError('공식 페이지에서 제품 URL 근거를 찾지 못함')
    stamp = datetime.fromisoformat(meta['collected_at'])
    if stamp.tzinfo is None:
        raise ValueError('수집 시각의 시간대 누락')
    return {'corp_code':meta['corp_code'], 'name':name, 'website_url':website,
            'source_url':canonical_url(meta['final_url']), 'collected_at':stamp.isoformat()}


def load(record):
    payload = json.dumps(record, ensure_ascii=False).encode().hex()
    sql = r"""
\set ON_ERROR_STOP on
BEGIN;
CREATE TEMP TABLE input AS SELECT convert_from(decode('%s','hex'),'UTF8')::jsonb AS d;
SELECT pg_advisory_xact_lock(hashtextextended('product:' || (d->>'corp_code') || ':' || (d->>'website_url'),0)) FROM input;
CREATE TEMP TABLE owner AS SELECT company_id FROM company_identifier
 WHERE namespace='dart' AND external_value=(SELECT d->>'corp_code' FROM input);
DO $$ BEGIN
 IF (SELECT count(*) FROM owner) <> 1 THEN RAISE EXCEPTION 'DART 기업 식별자 1개 필요'; END IF;
END $$;
INSERT INTO product(company_id,name,website_url,source_url,collected_at)
 SELECT company_id,d->>'name',d->>'website_url',d->>'source_url',(d->>'collected_at')::timestamptz
 FROM input CROSS JOIN owner
 ON CONFLICT(company_id,website_url) WHERE website_url IS NOT NULL DO UPDATE SET
 name=EXCLUDED.name,source_url=EXCLUDED.source_url,collected_at=EXCLUDED.collected_at
 WHERE product.collected_at < EXCLUDED.collected_at;
SELECT json_build_object('product_id',p.product_id,'name',p.name,'website',p.website_url)
 FROM product p JOIN owner o USING(company_id)
 WHERE p.website_url=(SELECT d->>'website_url' FROM input);
COMMIT;
""" % payload
    result = subprocess.run(['docker','compose','-f',str(ROOT/'infra/postgres/compose.yaml'),
                             'exec','-T','db','psql','-X','-U','company_dev','-d','company_analysis'],
                            input=sql,text=True,capture_output=True)
    if result.returncode:
        raise RuntimeError('제품 적재 실패·트랜잭션 롤백: ' + result.stderr)
    print(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command',required=True)
    fetch_parser = sub.add_parser('fetch')
    for option in ('corp-code','name','website-url','source-url'):
        fetch_parser.add_argument('--'+option,required=True)
    replay = sub.add_parser('replay')
    replay.add_argument('raw',type=Path)
    replay.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    if args.command=='fetch':
        path=fetch_page(args.corp_code,args.name,args.website_url,args.source_url)
        print(json.dumps({'raw':str(path.relative_to(ROOT)),'preview':transform(path)},ensure_ascii=False,indent=2))
    else:
        record=transform(args.raw)
        load(record) if args.apply else print(json.dumps(record,ensure_ascii=False,indent=2))


if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        print(str(exc),file=sys.stderr)
        sys.exit(1)
