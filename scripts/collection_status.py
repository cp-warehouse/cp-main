"""Inspect local checkpoints or existing company records."""
import argparse
import json
from collection_common import STATE, rows, bounded


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--companies',type=bounded(1000),help='DB 기업 최대 N개 조회')
    args=parser.parse_args()
    if args.companies:
        print(json.dumps(rows(f"""SELECT c.company_id,c.display_name,i.external_value AS dart_code,
            c.metadata->>'induty_code' AS industry_code,
            (SELECT count(*) FROM company_news n WHERE n.company_id=c.company_id) AS news_links
            FROM company c LEFT JOIN company_identifier i ON c.company_id=i.company_id AND i.namespace='dart'
            ORDER BY c.created_at DESC,c.company_id LIMIT {args.companies}"""),ensure_ascii=False,indent=2))
    else:
        for path in sorted(STATE.glob('*.json'),key=lambda path:path.stat().st_mtime,reverse=True)[:30]:
            batch=json.loads(path.read_text())
            counts={}
            for item in batch['items']:
                counts[item['status']]=counts.get(item['status'],0)+1
            print(json.dumps(dict(batch_id=batch['batch_id'],kind=batch['kind'],status=batch['status'],items=counts),ensure_ascii=False))


if __name__=='__main__':
    main()
