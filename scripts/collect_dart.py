"""Independent DART catalogue/profile batches. Never fetch news or enable news targets."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time

import ingest_company as source
from crawl_dart_news import read_catalog
from collection_common import ROOT, execute, rows, literal, js, lock, bounded, Pace, pacing, validate_pacing
from collection_common import create_batch, resume_batch, pending, mark, finish, reserved_keys


def plan(catalog, completed, reserved, limit, listed_only=False):
    return [row for row in sorted(catalog,key=lambda row:row['corp_code'])
            if row['corp_code'] not in completed|reserved
            and (not listed_only or re.fullmatch(r'\d{6}',row.get('stock_code','')))][:limit]


def catalogue(path, pace):
    if path:
        return Path(path).resolve()
    pointer = ROOT/'data/processed/collectors/dart-catalog.json'
    if pointer.exists():
        meta = json.loads(pointer.read_text())
        cached = ROOT/meta['path']
        if cached.exists() and time.time()-meta['fetched_at'] < 86400:
            return cached
    pace.before()
    result = source.fetch('corpCode.xml',{})
    read_catalog(result)  # Never cache error XML as a catalogue.
    pointer.write_text(json.dumps(dict(path=str(result.relative_to(ROOT)),fetched_at=time.time())))
    return result


def store_profile(path):
    record = source.transform(path)
    raw = json.loads(path.read_text())
    if raw.get('corp_code') != record['corp_code']:
        raise ValueError('기업개황 응답의 DART 코드 불일치')
    record.update(raw_uri=str(path.relative_to(ROOT)), metadata={key:raw.get(key) for key in (
        'corp_name_eng','stock_name','stock_code','corp_cls','jurir_no','bizr_no','adres',
        'hm_url','ir_url','induty_code','est_dt','acc_mt','ceo_nm')})
    # Same per-identity lock as the existing loader. Company+identifier+profile commit together.
    output = execute(f"""BEGIN;
        CREATE TEMP TABLE input AS SELECT {js(record)} AS d;
        SELECT pg_advisory_xact_lock(hashtextextended('dart:' || (d->>'corp_code'),0)) FROM input;
        CREATE TEMP TABLE chosen AS SELECT COALESCE((SELECT company_id FROM public.company_identifier
          WHERE namespace='dart' AND external_value=d->>'corp_code'),gen_random_uuid()) AS id FROM input;
        INSERT INTO public.company(company_id,legal_name,display_name,website_url,entity_kind,source_url,collected_at)
          SELECT id,d->>'legal_name',d->>'display_name',d->>'website_url','legal_entity',d->>'source_url',
            (d->>'collected_at')::timestamptz FROM input CROSS JOIN chosen
          ON CONFLICT(company_id) DO UPDATE SET legal_name=EXCLUDED.legal_name,display_name=EXCLUDED.display_name,
            website_url=COALESCE(EXCLUDED.website_url,company.website_url),source_url=EXCLUDED.source_url,
            collected_at=EXCLUDED.collected_at WHERE company.collected_at<EXCLUDED.collected_at;
        INSERT INTO public.company_identifier(company_id,namespace,external_value,source_url,collected_at)
          SELECT id,'dart',d->>'corp_code',d->>'source_url',(d->>'collected_at')::timestamptz FROM input CROSS JOIN chosen
          ON CONFLICT(namespace,external_value) DO NOTHING;
        UPDATE public.company c SET metadata=d->'metadata',metadata_raw_uri=d->>'raw_uri',
          metadata_collected_at=(d->>'collected_at')::timestamptz FROM input CROSS JOIN chosen
          WHERE c.company_id=chosen.id AND (c.metadata_collected_at IS NULL OR
            c.metadata_collected_at<=(d->>'collected_at')::timestamptz);
        SELECT id FROM chosen;
        COMMIT;""")
    return output.splitlines()[-1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch-size',type=bounded(1000),default=1000)
    parser.add_argument('--corp-code',action='append',help='명시적 소수 대상/갱신; 반복 가능')
    parser.add_argument('--listed-only',action='store_true')
    parser.add_argument('--catalog-file',help='기존 DART ZIP 사용 (네트워크 목록 조회 생략)')
    parser.add_argument('--resume',help='중단/실패 배치 ID. 완료 기업은 건너뜀')
    pacing(parser,1)
    args=parser.parse_args()
    validate_pacing(parser,args,1)
    if args.resume and (args.corp_code or args.catalog_file or args.listed_only):
        parser.error('--resume은 대상 선택 옵션과 함께 사용할 수 없습니다')
    pace=Pace(args.interval,args.pause_every,args.pause_min,args.pause_max)
    with lock('dart'):
        execute('SELECT metadata FROM public.company LIMIT 1;')
        source.api_key()
        if args.resume:
            batch_id=resume_batch(args.resume,'dart')['batch_id']
        else:
            path=catalogue(args.catalog_file,pace)
            catalog=read_catalog(path)
            if args.corp_code:
                codes=list(dict.fromkeys(args.corp_code))
                by_code={row['corp_code']:row for row in catalog}
                if len(codes)>args.batch_size or any(code not in by_code for code in codes):
                    parser.error('목록에 없는 코드 또는 batch-size 초과')
                selected=[by_code[code] for code in codes]
            else:
                completed={row['code'] for row in rows("SELECT i.external_value AS code FROM public.company p JOIN public.company_identifier i USING(company_id) WHERE i.namespace='dart' AND p.metadata_collected_at IS NOT NULL")}
                reserved=reserved_keys('dart')
                selected=plan(catalog,completed,reserved,args.batch_size,args.listed_only)
            if not selected:
                print('새 수집 대상 없음. 미완료 배치는 --resume으로 처리하세요.')
                return 0
            settings=vars(args)|{'catalog_path':str(path),'catalog_count':len(catalog)}
            batch_id=create_batch('dart',settings,[dict(key=row['corp_code'],details=row) for row in selected])
        print(f'DART 배치: {batch_id} (뉴스 수집 없음)',flush=True)
        stopped=False
        try:
            work=pending(batch_id)
            for index,item in enumerate(work,1):
                key,detail=item['item_key'],item['details']
                mark(batch_id,key,'running',detail)
                try:
                    pace.before()
                    path=source.fetch('company.json',{'corp_code':key})
                    detail['company_id']=store_profile(path)
                    detail['raw_uri']=str(path.relative_to(ROOT))
                    mark(batch_id,key,'done',detail)
                    print(f"[{index}/{len(work)}] {detail.get('corp_name',key)} 저장",flush=True)
                except source.DartAccessError:
                    mark(batch_id,key,'pending',detail,'DART 인증/호출 한도 오류')
                    stopped=True
                    break
                except Exception as exc:
                    mark(batch_id,key,'failed',detail,str(exc))
                    print(f'[{index}/{len(work)}] {key} 실패: {exc}',flush=True)
        except KeyboardInterrupt:
            stopped=True
        finally:
            return_code=finish(batch_id,stopped)
        return return_code


if __name__=='__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print(str(exc),file=sys.stderr)
        sys.exit(1)
