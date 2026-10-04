"""Collect news for existing DB companies. No DART calls or company creation."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import re
import sys
import uuid
from urllib.parse import urlencode
from urllib.request import Request

from collection_common import ROOT, execute, rows, literal, js, lock, bounded, Pace, Stop, pacing, validate_pacing
from collection_common import create_batch, resume_batch, pending, mark, finish, reserved_keys, last_attempts

from crawl_news import SearchParser, parse_article
from ingestion_http import get


def query_name(name):
    name=re.sub(r'^(?:주식회사\s*|\(주\)\s*|㈜\s*)','',name.strip())
    return re.sub(r'\s*(?:주식회사|\(주\)|㈜)$','',name).strip()


def evidence(document,names):
    found=[]
    for name in dict.fromkeys(filter(None,names)):
        for field in ('title','body'):
            match=re.search(re.escape(name),document[field],re.IGNORECASE)
            if match:
                found.append(dict(name=name,field=field,excerpt=document[field][max(0,match.start()-35):match.end()+65]))
    return found


def fetch(url,folder,pace):
    pace.before()
    try:
        raw,final_url,content_type,status=get(Request(url,headers={'User-Agent':'cp-main-news-pilot/1.0'}),attempts=1)
    except RuntimeError as exc:
        if any(code in str(exc) for code in ('HTTP 403','HTTP 429')):
            raise Stop('접근/요청 제한 응답: 전체 실행 중단') from None
        raise
    if status!=200 or content_type!='text/html':
        raise ValueError('HTML 응답이 아닙니다')
    path=folder/(hashlib.sha256(url.encode()).hexdigest()+'.html')
    path.write_bytes(raw)
    return raw.decode('utf-8'),final_url,str(path.relative_to(ROOT))


def store_document(record,batch_id):
    execute(f"""INSERT INTO news_document(url,title,body,publisher,published_at,image_url,raw_uri,
        extractor_version,collected_at,run_id)
        SELECT d->>'url',d->>'title',d->>'body',d->>'publisher',(d->>'published_at')::timestamptz,
        d->>'image_url',d->>'raw_uri',d->>'extractor_version',(d->>'collected_at')::timestamptz,
        {literal(batch_id)}::uuid FROM (SELECT {js(record)} AS d) input
        ON CONFLICT(url) DO NOTHING;""")
    return rows(f"SELECT * FROM news_document WHERE url={literal(record['url'])}")[0]


def store_link(cid,document,query,names,batch_id):
    matches=evidence(document,names)
    execute(f"""INSERT INTO company_news(company_id,document_id,query,relevance_status,evidence,run_id)
        VALUES({literal(cid)}::uuid,{literal(document['document_id'])}::uuid,{literal(query)},
        {literal('candidate' if matches else 'unmatched')},{js(matches)},{literal(batch_id)}::uuid)
        ON CONFLICT(company_id,document_id) DO UPDATE SET query=EXCLUDED.query,
        relevance_status=EXCLUDED.relevance_status,evidence=EXCLUDED.evidence,
        run_id=EXCLUDED.run_id,checked_at=now();""")


def collect(batch_id,item,settings,folder,pace):
    cid,detail=item['item_key'],item['details']
    query=detail.setdefault('query',query_name(detail['legal_name'] or detail['display_name']))
    if not query:
        raise ValueError('검색어 누락')
    urls=detail.setdefault('urls',[])
    while not detail.get('search_done'):
        page=detail.get('next_page',1)
        url='https://search.daum.net/search?'+urlencode(dict(w='news',q=query,sort='recency',p=page))
        html,_,raw=fetch(url,folder,pace)
        parser=SearchParser()
        parser.feed(html)
        urls.extend(url for url in parser.urls if url not in urls)
        detail.setdefault('search_raw',[]).append(raw)
        detail['next_page']=page+1
        detail['search_done']=len(urls)>=settings['articles_per_company'] or not parser.urls or page>=settings['search_pages']
        mark(batch_id,cid,'pending',detail)
    done=detail.setdefault('completed_urls',[])
    failures=[]
    for url in urls[:settings['articles_per_company']]:
        if url in done:
            continue
        try:
            existing=rows(f'SELECT * FROM news_document WHERE url={literal(url)}')
            if existing:
                document=existing[0]
                detail['reused']=detail.get('reused',0)+1
            else:
                html,final_url,raw=fetch(url,folder,pace)
                record=parse_article(html,final_url)
                record.update(raw_uri=raw,collected_at=datetime.now(timezone.utc).isoformat())
                document=store_document(record,batch_id)
                detail['downloaded']=detail.get('downloaded',0)+1
            store_link(cid,document,query,[query,detail['legal_name'],detail['display_name']],batch_id)
            done.append(url)
            mark(batch_id,cid,'pending',detail)
        except Stop:
            raise
        except Exception as exc:
            failures.append(dict(url=url,error=str(exc)))
    detail['article_errors']=failures
    detail['outcome']='articles' if done else 'no_articles'
    if failures:
        raise RuntimeError(f'기사 {len(failures)}건 실패; 재개 시 해당 URL 재시도')
    mark(batch_id,cid,'done',detail)
    print(f"{detail['display_name']}: 저장 연결 {len(done)}건 ({detail['outcome']})",flush=True)


def select_targets(args):
    conditions=[]
    if args.company_id:
        ids=list(dict.fromkeys(str(uuid.UUID(value)) for value in args.company_id))
        conditions.append('c.company_id IN ('+','.join(literal(value)+'::uuid' for value in ids)+')')
        wanted=len(ids)
    elif args.corp_code:
        codes=list(dict.fromkeys(args.corp_code))
        if any(not re.fullmatch(r'\d{8}',value) for value in codes):
            raise ValueError('DART 코드는 8자리입니다')
        conditions.append("EXISTS(SELECT 1 FROM company_identifier i WHERE i.company_id=c.company_id AND i.namespace='dart' AND i.external_value IN ("+','.join(literal(code) for code in codes)+'))')
        wanted=len(codes)
    else:
        reserved=reserved_keys('news')
        if reserved:
            conditions.append('c.company_id NOT IN ('+','.join(literal(str(uuid.UUID(value)))+'::uuid' for value in reserved)+')')
        wanted=None
    if wanted and wanted>args.company_limit:
        raise ValueError('명시한 기업 수가 company-limit을 초과합니다')
    selected=rows(f"""SELECT c.company_id,c.legal_name,c.display_name FROM company c
        {'WHERE '+ ' AND '.join(conditions) if conditions else ''}
        ORDER BY c.company_id""")
    if not wanted:
        attempted=last_attempts('news')
        selected.sort(key=lambda row:(attempted.get(row['company_id'],0),row['company_id']))
    selected=selected[:args.company_limit]
    if wanted and len(selected)!=wanted:
        raise ValueError('지정 기업 중 DB에 없는 기업이 있습니다. 기업 등록을 먼저 실행하세요')
    return selected


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--company-limit',type=bounded(1000),default=10)
    selection=parser.add_mutually_exclusive_group()
    selection.add_argument('--company-id',action='append')
    selection.add_argument('--corp-code',action='append',help='DB 조회 조건일 뿐 DART 호출 없음')
    parser.add_argument('--articles-per-company',type=bounded(100),default=3)
    parser.add_argument('--search-pages',type=bounded(10),default=1)
    parser.add_argument('--request-budget',type=bounded(10000),default=100)
    parser.add_argument('--resume',help='기존 배치의 미완료 기업/기사만 재개')
    pacing(parser,2)
    args=parser.parse_args()
    validate_pacing(parser,args,2)
    if args.resume and (args.company_id or args.corp_code):
        parser.error('재개 시 기업을 다시 지정하지 마세요')
    pace=Pace(args.interval,args.pause_every,args.pause_min,args.pause_max,args.request_budget)
    with lock('news'):
        execute('SELECT 1 FROM news_document LIMIT 1;')
        if args.resume:
            batch=resume_batch(args.resume,'news')
            batch_id,settings=batch['batch_id'],batch['settings']
        else:
            targets=select_targets(args)
            if not targets:
                print('대상 없음. 기업 등록 또는 미완료 배치 --resume 확인 필요')
                return 0
            settings=vars(args)
            batch_id=create_batch('news',settings,[dict(key=row['company_id'],details=row) for row in targets])
        folder=ROOT/'data/raw/collectors/news'/batch_id
        folder.mkdir(parents=True,exist_ok=True)
        print(f'뉴스 배치: {batch_id} (DART 호출 없음)',flush=True)
        stopped=False
        try:
            for item in pending(batch_id):
                cid,detail=item['item_key'],item['details']
                mark(batch_id,cid,'running',detail)
                try:
                    collect(batch_id,item,settings,folder,pace)
                except Stop as exc:
                    mark(batch_id,cid,'pending',detail,str(exc))
                    print(str(exc),flush=True)
                    stopped=True
                    break
                except Exception as exc:
                    mark(batch_id,cid,'failed',detail,str(exc))
                    print(f"{detail['display_name']}: {exc}",flush=True)
        except KeyboardInterrupt:
            stopped=True
        finally:
            result=finish(batch_id,stopped)
            print(f'이번 뉴스 요청: {pace.count}회',flush=True)
        return result


if __name__=='__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print(str(exc),file=sys.stderr)
        sys.exit(1)
