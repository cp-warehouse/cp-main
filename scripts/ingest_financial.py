"""Replayable OpenDART annual financial ingestion for selected accounts."""
import argparse
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ingest_company import BASE, ROOT, fetch

REPORT_CODE = '11011'
SCOPE = 'CFS'
EXTRACTOR_VERSION = 'financial-v1'
TARGETS = {
    ('BS', 'ifrs-full_Assets'): ('자산총계', 'instant'),
    ('CIS', 'ifrs-full_Revenue'): ('영업수익', 'annual'),
    ('CIS', 'dart_OperatingIncomeLoss'): ('영업이익', 'annual'),
}


def verified_raw(path, endpoint):
    raw = path.read_bytes()
    meta = json.loads(path.with_name('metadata.json').read_text())
    if meta.get('endpoint') != endpoint:
        raise ValueError('원문 endpoint 불일치')
    if hashlib.sha256(raw).hexdigest() != meta.get('sha256'):
        raise ValueError('원문 해시 불일치')
    stamp = datetime.fromisoformat(meta['collected_at'])
    if stamp.tzinfo is None:
        raise ValueError('수집 시각의 시간대 누락')
    return raw, meta


def decimal_amount(value):
    text = str(value or '').replace(',', '').strip()
    if not re.fullmatch(r'-?\d+', text):
        raise ValueError('금액 형식 오류')
    try:
        return Decimal(text)
    except InvalidOperation:
        raise ValueError('금액 변환 오류') from None


def xbrl_contexts(path):
    raw, meta = verified_raw(path, 'fnlttXbrl.xml')
    if not zipfile.is_zipfile(path):
        raise ValueError('XBRL 응답이 ZIP이 아님')
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith('.xbrl')]
        if len(names) != 1:
            raise ValueError('XBRL 인스턴스 파일이 정확히 1개가 아님')
        root = ET.fromstring(archive.read(names[0]))
    contexts = {}
    for node in root.iter():
        if node.tag.rsplit('}', 1)[-1] != 'context':
            continue
        period = next((x for x in node if x.tag.rsplit('}', 1)[-1] == 'period'), None)
        if period is None:
            continue
        values = {x.tag.rsplit('}', 1)[-1]: (x.text or '').strip() for x in period}
        if values.get('instant'):
            contexts[node.attrib['id']] = ('instant', values['instant'], values['instant'])
        elif values.get('startDate') and values.get('endDate'):
            contexts[node.attrib['id']] = ('duration', values['startDate'], values['endDate'])
    facts = []
    for node in root.iter():
        ref = node.attrib.get('contextRef')
        if ref in contexts and node.text:
            facts.append((node.tag.rsplit('}', 1)[-1], node.text.replace(',', '').strip(), contexts[ref]))
    return facts, meta, hashlib.sha256(raw).hexdigest()


def transform(financial_path, xbrl_path):
    raw, meta = verified_raw(financial_path, 'fnlttSinglAcntAll.json')
    data = json.loads(raw)
    if data.get('status') != '000':
        raise ValueError('DART 응답 상태: ' + str(data.get('status')))
    params = meta.get('params', {})
    corp_code = params.get('corp_code', '')
    year = params.get('bsns_year', '')
    if (not re.fullmatch(r'\d{8}', corp_code) or not re.fullmatch(r'\d{4}', year)
            or params.get('reprt_code') != REPORT_CODE or params.get('fs_div') != SCOPE):
        raise ValueError('재무 요청 조건 불일치')
    facts, xbrl_meta, xbrl_hash = xbrl_contexts(xbrl_path)
    records = []
    receipt_numbers = set()
    for (statement, account_id), (expected_name, basis) in TARGETS.items():
        matches = [row for row in data.get('list', [])
                   if row.get('sj_div') == statement and row.get('account_id') == account_id
                   and (row.get('account_detail') or '-').strip() == '-']
        if len(matches) != 1:
            raise ValueError(f'{account_id} 대상 행이 정확히 1개가 아님')
        row = matches[0]
        if row.get('account_nm') != expected_name or row.get('corp_code') != corp_code:
            raise ValueError(f'{account_id} 계정 또는 기업 불일치')
        amount = decimal_amount(row.get('thstrm_amount'))
        currency = (row.get('currency') or '').strip()
        if not re.fullmatch(r'[A-Z]{3}', currency):
            raise ValueError(f'{account_id} 통화 형식 오류')
        receipt = row.get('rcept_no', '')
        if not re.fullmatch(r'\d{14}', receipt):
            raise ValueError('접수번호 형식 오류')
        receipt_numbers.add(receipt)
        local_name = account_id.split('_', 1)[1]
        periods = {period for tag, value, period in facts if tag == local_name and value == str(amount)}
        expected_period = (('instant', f'{year}-12-31', f'{year}-12-31') if basis == 'instant'
                           else ('duration', f'{year}-01-01', f'{year}-12-31'))
        if expected_period not in periods:
            raise ValueError(f'{account_id} XBRL 기간 교차 검증 실패')
        source_key = f'opendart:fnlttSinglAcntAll:{corp_code}:{year}:{REPORT_CODE}:{SCOPE}'
        record_key = f'{statement}:{account_id}:-:thstrm_amount'
        records.append({
            'corp_code': corp_code, 'account_code': account_id, 'account_name': expected_name,
            'value': str(amount), 'currency': currency, 'fiscal_year': int(year),
            'report_code': REPORT_CODE, 'statement_scope': 'consolidated',
            'period_start': expected_period[1], 'period_end': expected_period[2],
            'period_basis': basis,
            'source_url': f'https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}',
            'source_key': source_key, 'source_record_key': record_key,
            'extractor_version': EXTRACTOR_VERSION,
            'raw_uri': str(financial_path.resolve().relative_to(ROOT)),
            'collected_at': meta['collected_at'],
        })
    if len(receipt_numbers) != 1 or xbrl_meta.get('params', {}).get('rcept_no') not in receipt_numbers:
        raise ValueError('재무 JSON과 XBRL 접수번호 불일치')
    revision = next(iter(receipt_numbers)) + ':' + meta['sha256'] + ':' + xbrl_hash
    for record in records:
        record['source_revision'] = revision
    return records


def load(records):
    payload = json.dumps(records, ensure_ascii=False).encode().hex()
    sql = r"""
\set ON_ERROR_STOP on
BEGIN;
CREATE TEMP TABLE input AS
 SELECT value AS d FROM jsonb_array_elements(convert_from(decode('%s','hex'),'UTF8')::jsonb);
SELECT pg_advisory_xact_lock(hashtextextended('dart-financial:' || (d->>'source_key'),0)) FROM input LIMIT 1;
CREATE TEMP TABLE owner AS
 SELECT company_id FROM company_identifier
 WHERE namespace='dart' AND external_value=(SELECT d->>'corp_code' FROM input LIMIT 1);
DO $$ BEGIN
 IF (SELECT count(*) FROM owner) <> 1 THEN RAISE EXCEPTION 'DART 기업 식별자 1개 필요'; END IF;
END $$;
INSERT INTO financial_observation(
 company_id,account_code,account_name,value,currency,fiscal_year,report_code,
 statement_scope,period_start,period_end,period_basis,source_url,source_key,
 source_revision,source_record_key,extractor_version,raw_uri,collected_at)
SELECT o.company_id,d->>'account_code',d->>'account_name',(d->>'value')::numeric,
 d->>'currency',(d->>'fiscal_year')::integer,d->>'report_code',d->>'statement_scope',
 (d->>'period_start')::date,(d->>'period_end')::date,d->>'period_basis',d->>'source_url',
 d->>'source_key',d->>'source_revision',d->>'source_record_key',d->>'extractor_version',
 d->>'raw_uri',(d->>'collected_at')::timestamptz
FROM input CROSS JOIN owner o
ON CONFLICT(company_id,source_key,source_revision,source_record_key,extractor_version) DO NOTHING;
SELECT json_build_object('total',count(*),'accounts',array_agg(account_name ORDER BY account_name))
FROM financial_observation WHERE company_id=(SELECT company_id FROM owner)
 AND source_key=(SELECT d->>'source_key' FROM input LIMIT 1);
COMMIT;
""" % payload
    result = subprocess.run(['docker','compose','-f',str(ROOT/'infra/postgres/compose.yaml'),
                             'exec','-T','db','psql','-X','-U','company_dev','-d','company_analysis'],
                            input=sql, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError('재무 적재 실패·트랜잭션 롤백: ' + result.stderr)
    print(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    fetch_parser = sub.add_parser('fetch')
    fetch_parser.add_argument('--corp-code', required=True)
    fetch_parser.add_argument('--year', required=True)
    replay = sub.add_parser('replay')
    replay.add_argument('financial_raw', type=Path)
    replay.add_argument('xbrl_raw', type=Path)
    replay.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.command == 'fetch':
        if not re.fullmatch(r'\d{8}', args.corp_code) or not re.fullmatch(r'\d{4}', args.year):
            raise ValueError('기업 코드 8자리·사업연도 4자리 필요')
        financial = fetch('fnlttSinglAcntAll.json', {
            'corp_code': args.corp_code, 'bsns_year': args.year,
            'reprt_code': REPORT_CODE, 'fs_div': SCOPE})
        body = json.loads(financial.read_text())
        receipts = {row.get('rcept_no') for row in body.get('list', []) if row.get('rcept_no')}
        if body.get('status') != '000' or len(receipts) != 1:
            raise ValueError('재무 응답 또는 접수번호 확인 필요')
        xbrl = fetch('fnlttXbrl.xml', {'rcept_no': next(iter(receipts)), 'reprt_code': REPORT_CODE})
        print(json.dumps({'financial_raw':str(financial.relative_to(ROOT)),
                          'xbrl_raw':str(xbrl.relative_to(ROOT)),
                          'preview':transform(financial,xbrl)}, ensure_ascii=False, indent=2))
    else:
        records = transform(args.financial_raw, args.xbrl_raw)
        if args.apply:
            load(records)
        else:
            print(json.dumps(records, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
