"""DART catalogue -> company profiles -> Daum news; local evidence-only pilot."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlencode
import xml.etree.ElementTree as ET
import zipfile

import ingest_company as dart
from crawl_news import SearchParser, download, parse_article, positive

ROOT = Path(__file__).resolve().parents[1]


def read_catalog(path):
    if not zipfile.is_zipfile(path):
        # Do not echo arbitrary API responses or credential-bearing request URLs.
        raise ValueError('DART 기업 목록이 ZIP이 아닙니다. 인증키·호출 한도 확인 필요')
    with zipfile.ZipFile(path) as archive:
        tree = ET.fromstring(archive.read('CORPCODE.xml'))
    records = []
    seen = set()
    for node in tree.findall('list'):
        row = {child.tag: (child.text or '').strip() for child in node}
        code = row.get('corp_code', '')
        if not re.fullmatch(r'\d{8}', code) or not row.get('corp_name') or code in seen:
            raise ValueError('기업 목록의 식별자·기업명 누락 또는 중복')
        seen.add(code)
        records.append(row)
    if not records:
        raise ValueError('기업 목록이 비어 있습니다')
    return records


def select_companies(catalog, names, limit):
    if names:
        selected = []
        for name in dict.fromkeys(names):
            matches = [row for row in catalog if row['corp_name'].casefold() == name.casefold()]
            if len(matches) != 1:
                raise ValueError(f'{name}: 정확한 기업명과 일치하는 기업 {len(matches)}개; 다른 이름을 지정하세요')
            selected.extend(matches)
        return selected[:limit]
    # Broad catalogue, small deterministic pilot sample. No hard-coded target list.
    listed = [row for row in catalog if re.fullmatch(r'\d{6}', row.get('stock_code', ''))]
    return sorted(listed, key=lambda row: row['stock_code'])[:limit]


def search_name(value):
    value = re.sub(r'^(?:주식회사\s*|\(주\)\s*|㈜\s*)', '', value.strip())
    return re.sub(r'\s*(?:주식회사|\(주\)|㈜)$', '', value).strip()


def relevance(record, names):
    evidence = []
    for name in dict.fromkeys(names):
        if not name:
            continue
        for field in ('title', 'body'):
            match = re.search(re.escape(name), record[field], re.IGNORECASE)
            if match:
                evidence.append(dict(name=name, field=field,
                    excerpt=record[field][max(0, match.start()-35):match.end()+65]))
    return dict(status='candidate' if evidence else 'unmatched',
                method='literal_name_v1', evidence=evidence,
                verified=False)


def save_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--name', action='append', help='DART 기업명 정확히 일치; 반복 가능')
    source.add_argument('--corp-code', action='append', help='DART 코드; 동명 기업 구분, 반복 가능')
    parser.add_argument('--company-limit', type=positive, default=3)
    parser.add_argument('--article-limit', type=positive, default=2)
    args = parser.parse_args()
    if len(set(args.name or args.corp_code or [])) > args.company_limit:
        parser.error('--company-limit보다 지정한 기업 수가 많습니다')
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = ROOT / 'data/processed/dart-news' / run_id
    raw_dir = ROOT / 'data/raw/dart-news' / run_id
    output.mkdir(parents=True)
    raw_dir.mkdir(parents=True)
    report = dict(run_id=run_id, status='running', storage='local_json',
                  selection=vars(args), companies=[], documents=[], company_documents=[], errors=[])
    result_path = output / 'result.json'
    documents = {}
    try:
        dart.api_key()  # Validate without printing or copying the key.
        path = dart.fetch('corpCode.xml', {})
        catalog = read_catalog(path)
        report['catalog'] = dict(count=len(catalog), raw_uri=str(path.relative_to(ROOT)),
                                  file='companies.json')
        save_json(output / 'companies.json', catalog)
        if args.corp_code:
            by_code = {row['corp_code']: row for row in catalog}
            if any(code not in by_code for code in args.corp_code):
                raise ValueError('기업 목록에 없는 DART 코드')
            targets = [by_code[code] for code in dict.fromkeys(args.corp_code)]
        else:
            targets = select_companies(catalog, args.name, args.company_limit)
        if not targets:
            raise ValueError('선택된 기업이 없습니다')
        print(f'DART 목록 {len(catalog):,}개 저장; 테스트 {len(targets)}개', flush=True)
        save_json(result_path, report)
        for target in targets:
            code = target['corp_code']
            company = dict(corp_code=code, catalog_name=target['corp_name'], status='running',
                           article_attempts=0, article_links=0)
            report['companies'].append(company)
            stage = 'profile'
            try:
                time.sleep(1)
                profile_path = dart.fetch('company.json', {'corp_code': code})
                base = dart.transform(profile_path)
                profile = json.loads(profile_path.read_text())
                fields = ('corp_name', 'corp_name_eng', 'stock_name', 'stock_code', 'ceo_nm',
                          'corp_cls', 'jurir_no', 'bizr_no', 'adres', 'hm_url', 'ir_url',
                          'induty_code', 'est_dt', 'acc_mt')
                company.update(profile={field: profile.get(field) for field in fields},
                               profile_raw_uri=str(profile_path.relative_to(ROOT)),
                               collected_at=base['collected_at'])
                query = search_name(base['legal_name'])
                if not query:
                    raise ValueError('검색어가 비어 있습니다')
                names = [query, base['legal_name'], (profile.get('stock_name') or '').strip()]
                company['query'] = query
                stage = 'search'
                search_url = 'https://search.daum.net/search?' + urlencode(dict(
                    w='news', q=query, sort='recency', p=1))
                time.sleep(1)
                html, _, raw_path = download(search_url, raw_dir, code + '-search.html')
                search = SearchParser()
                search.feed(html)
                company.update(search_url=search_url, search_raw_uri=raw_path,
                               discovered=len(search.urls))
                for url in search.urls[:args.article_limit]:
                    company['article_attempts'] += 1
                    try:
                        if url not in documents:
                            time.sleep(1)
                            digest = hashlib.sha256(url.encode()).hexdigest()
                            html, final_url, raw_path = download(url, raw_dir, digest + '.html')
                            record = parse_article(html, final_url)
                            record.update(document_id=hashlib.sha256(record['url'].encode()).hexdigest(),
                                          raw_uri=raw_path, collected_at=datetime.now(timezone.utc).isoformat())
                            documents[url] = record
                        record = documents[url]
                        link = dict(corp_code=code, document_id=record['document_id'],
                                    query=query, **relevance(record, names))
                        if not any(row['corp_code'] == code and row['document_id'] == link['document_id']
                                   for row in report['company_documents']):
                            report['company_documents'].append(link)
                            company['article_links'] += 1
                    except Exception as exc:
                        report['errors'].append(dict(corp_code=code, stage='article', url=url, error=str(exc)))
                company['status'] = ('partial' if company['article_links'] < company['article_attempts']
                                     else 'success') if company['article_links'] else 'no_articles'
                print(f"{query}: 검색 {len(search.urls)}건, 저장 연결 {company['article_links']}건 ({company['status']})", flush=True)
            except dart.DartAccessError:
                company['status'] = 'failed'
                raise
            except Exception as exc:
                company['status'] = 'failed'
                report['errors'].append(dict(corp_code=code, stage=stage, error=str(exc)))
                print(f"{target['corp_name']}: {stage} 실패", flush=True)
            report['documents'] = list({row['document_id']: row for row in documents.values()}.values())
            save_json(result_path, report)
        statuses = [company['status'] for company in report['companies']]
        report['status'] = ('success' if all(status == 'success' for status in statuses)
                            and not report['errors'] else 'partial') if report['documents'] else 'failed'
    except Exception as exc:
        report['status'] = 'failed'
        report['errors'].append(dict(stage='pipeline', error=str(exc)))
        print(str(exc), file=sys.stderr)
    finally:
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        report['documents'] = list({row['document_id']: row for row in documents.values()}.values())
        save_json(result_path, report)
        print(f"{report['status']}: 문서 {len(report['documents'])}건, 기업 연결 {len(report['company_documents'])}건, 오류 {len(report['errors'])}건\n결과: {result_path}", flush=True)
    return 0 if report['status'] == 'success' else 1


if __name__ == '__main__':
    sys.exit(main())
