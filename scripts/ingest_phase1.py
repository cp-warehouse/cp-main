"""Collect configured companies; record partial failures and retry failed targets."""
import argparse
from contextlib import redirect_stdout
import fcntl
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ingest_company
import ingest_document_financial
import ingest_financial
import ingest_product
from ingestion_db import execute, literal, json_sql

ROOT = Path(__file__).resolve().parents[1]
TARGETS_PATH = ROOT / 'config/phase1_targets.json'


def relative(path):
    return str(path.relative_to(ROOT))


def target_id(target):
    return 'dart:' + target['corp_code'] if target.get('corp_code') else 'official_domain:' + target['official_company']['domain']


def load_targets(path=TARGETS_PATH):
    targets = [target for target in json.loads(path.read_text()) if target.get('enabled', True)]
    identities = [target_id(target) for target in targets]
    if len(set(identities)) != len(identities):
        raise ValueError('중복 기업 식별자')
    for target in targets:
        if not target.get('label'):
            raise ValueError('기업 표시명 누락')
        code = target.get('corp_code')
        if code is not None and not re.fullmatch(r'\d{8}', code):
            raise ValueError('DART 기업 코드는 8자리 숫자 필요')
        if not code:
            official = target['official_company']
            if not official.get('source_url') or not official.get('name_in_source'):
                raise ValueError('공식 기업 근거 누락')
    return targets


def ingest_official_company(target, apply):
    official = target['official_company']
    path = ingest_product.fetch_page(None, official['name_in_source'],
        official['website_url'], official['source_url'], official['domain'])
    page = ingest_product.transform(path)
    record = dict(namespace='official_domain', external_value=official['domain'],
        entity_kind='organization', legal_name=None, display_name=target['label'],
        website_url=official['website_url'], source_url=page['source_url'],
        collected_at=page['collected_at'])
    if apply:
        ingest_company.load(record)
    funding = target.get('funding_evidence')
    if funding:
        evidence = ingest_product.fetch_page(None, funding['stage_in_source'],
            official['website_url'], funding['source_url'], official['domain'])
        verified = ingest_product.transform(evidence)
        if apply:
            execute(f"""INSERT INTO funding_evidence
            SELECT company_id,{literal(funding['stage'])},{literal(funding['announced_on'])}::date,
                   {literal(funding['source_url'])},{literal(relative(evidence))},
                   {literal(verified['collected_at'])}::timestamptz
            FROM company_identifier WHERE namespace='official_domain'
              AND external_value={literal(official['domain'])}
            ON CONFLICT DO NOTHING;""")
    return dict(label=target['label'], company_raw=relative(path),
        product=None, financial=dict(status='not_configured', rows=0),
        funding_evidence=funding)


def ingest_target(target, year, apply, progress=lambda stage: None):
    progress('company')
    if not target.get('corp_code'):
        return ingest_official_company(target, apply)
    code = target['corp_code']
    company_raw = ingest_company.fetch('company.json', {'corp_code': code})
    company = ingest_company.transform(company_raw)
    if apply:
        ingest_company.load(company)

    progress('product')
    product_config = target['product']
    product_raw = ingest_product.fetch_page(
        code, product_config['name'], product_config['website_url'],
        product_config['source_url'], product_config['official_domain'])
    product = ingest_product.transform(product_raw)
    if apply:
        ingest_product.load(product)

    progress('financial')
    financial_raw = ingest_company.fetch('fnlttSinglAcntAll.json', {
        'corp_code': code, 'bsns_year': year,
        'reprt_code': ingest_financial.REPORT_CODE,
        'fs_div': ingest_financial.SCOPE,
    })
    financial_body = json.loads(financial_raw.read_text())
    financial = {
        'status': 'unavailable',
        'dart_status': financial_body.get('status'),
        'message': financial_body.get('message'),
        'raw': relative(financial_raw),
        'rows': 0,
    }
    if financial_body.get('status') == '000':
        receipts = {row.get('rcept_no') for row in financial_body.get('list', [])
                    if row.get('rcept_no')}
        if len(receipts) != 1:
            raise ValueError(f'{code}: 재무 접수번호가 정확히 1개가 아님')
        xbrl_raw = ingest_company.fetch('fnlttXbrl.xml', {
            'rcept_no': next(iter(receipts)),
            'reprt_code': ingest_financial.REPORT_CODE,
        })
        records = ingest_financial.transform(financial_raw, xbrl_raw)
        if apply:
            ingest_financial.load(records)
        financial = {
            'status': 'loaded' if apply else 'verified',
            'dart_status': '000',
            'raw': relative(financial_raw),
            'xbrl_raw': relative(xbrl_raw),
            'rows': len(records),
        }
    elif (financial_body.get('status') == '013' and target.get('financial_document')
          and target['financial_document']['period_start'][:4] == year):
        document_config = target['financial_document']
        document_raw = ingest_company.fetch(
            'document.xml', {'rcept_no': document_config['receipt_no']})
        records = ingest_document_financial.transform(
            document_raw, code, document_config)
        if apply:
            ingest_document_financial.load(records)
        financial = {
            'status': 'loaded' if apply else 'verified',
            'dart_status': '013',
            'fallback': 'official_filing_document',
            'raw': relative(document_raw),
            'rows': len(records),
        }
    elif financial_body.get('status') != '013':
        raise ValueError(
            f"{code}: DART 재무 오류 {financial_body.get('status')} "
            f"{financial_body.get('message')}")

    return {
        'label': target['label'],
        'corp_code': code,
        'company_raw': relative(company_raw),
        'product': product['name'],
        'product_raw': relative(product_raw),
        'financial': financial,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--year', default='2025')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--config', type=Path, default=TARGETS_PATH)
    parser.add_argument('--target', action='append', help='target_id 선택, 여러 번 지정 가능')
    parser.add_argument('--retry-run', help='실패·중단 기업만 재실행할 run UUID')
    args = parser.parse_args()
    if not re.fullmatch(r'\d{4}', args.year):
        raise ValueError('사업연도는 4자리 숫자 필요')
    targets = load_targets(args.config)
    if args.retry_run:
        retry = str(uuid.UUID(args.retry_run))
        original = execute(f"SELECT json_build_object('year',requested_year,'hash',config_hash) FROM ingestion_run WHERE run_id='{retry}';")
        if not original:
            raise ValueError('존재하지 않는 실행 ID')
        original = json.loads(original)
        if (original['year'] != int(args.year) or
                original['hash'] != hashlib.sha256(args.config.read_bytes()).hexdigest()):
            raise ValueError('재시도는 최초 실행과 동일한 연도·설정 필요; 변경 시 새 실행 사용')
        ids = execute(f"SELECT target_id FROM ingestion_result WHERE run_id='{retry}' AND status IN ('failed','interrupted');").splitlines()
        targets = [target for target in targets if target_id(target) in ids]
    if args.target:
        known = {target_id(target) for target in targets}
        if not set(args.target) <= known:
            raise ValueError('설정에 없는 target_id')
        targets = [target for target in targets if target_id(target) in args.target]
    if not targets:
        raise ValueError('실행할 기업 없음')
    # Local scheduler/manual invocations must not overlap. Lock is released on process exit.
    lock_dir = ROOT / 'data/processed'
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock = (lock_dir / 'ingestion.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise RuntimeError('이미 실행 중인 수집 작업 존재') from None
    run_id = str(uuid.uuid4())
    if args.apply:
        execute("""UPDATE ingestion_result SET status='interrupted',finished_at=now()
        WHERE status='running';
        UPDATE ingestion_run SET status='interrupted',finished_at=now() WHERE status='running';""")
        execute(f"""INSERT INTO ingestion_run(run_id,status,config_hash,requested_year,retry_of)
        VALUES('{run_id}','running','{hashlib.sha256(args.config.read_bytes()).hexdigest()}',
        {int(args.year)},{literal(args.retry_run) + '::uuid' if args.retry_run else 'NULL'});""")
    results = []
    halted = False
    for target in targets:
        identity = target_id(target)
        if args.apply:
            execute(f"""INSERT INTO ingestion_result(run_id,target_id,label,status)
            VALUES('{run_id}',{literal(identity)},{literal(target['label'])},'running');""")
        stage = ['pending']
        def progress(value):
            stage[0] = value
            if args.apply:
                execute(f"UPDATE ingestion_result SET details={json_sql({'stage':value})} WHERE run_id='{run_id}' AND target_id={literal(identity)};")
        try:
            if halted:
                raise ingest_company.DartAccessError('batch halted')
            with redirect_stdout(io.StringIO()):
                result = ingest_target(target, args.year, args.apply, progress)
            result.update(target_id=identity, status='success')
        except Exception as exc:
            # Exception URLs may contain credentials. Persist the type only.
            result = dict(target_id=identity, label=target['label'], status='failed',
                          error_type=type(exc).__name__, stage=stage[0])
            if isinstance(exc, ingest_company.DartAccessError):
                halted = True
        results.append(result)
        if args.apply:
            execute(f"""UPDATE ingestion_result SET status={literal(result['status'])},
            finished_at=now(),details={json_sql(result)},
            error_type={literal(result['error_type']) if result.get('error_type') else 'NULL'}
            WHERE run_id='{run_id}' AND target_id={literal(identity)};""")
        print(target['label'] + ': ' + result['status'], file=sys.stderr, flush=True)
    failed = sum(item['status']=='failed' for item in results)
    if args.apply:
        status = 'success' if not failed else ('failed' if failed==len(results) else 'partial')
        execute(f"UPDATE ingestion_run SET status='{status}',finished_at=now() WHERE run_id='{run_id}';")
    print(json.dumps({
        'mode': 'apply' if args.apply else 'preview',
        'run_id': run_id if args.apply else None,
        'year': args.year,
        'targets': results,
        'summary': {
            'succeeded_targets': len(results)-failed,
            'failed_targets': failed,
            'financial_records_processed': sum(item.get('financial',{}).get('rows',0) for item in results),
        },
    }, ensure_ascii=False, indent=2))
    lock.close()
    if failed:
        sys.exit(1)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
