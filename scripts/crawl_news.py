"""Small Daum news crawl smoke test; stdlib only, no DB/API key required."""
import argparse
from datetime import datetime, timezone, timedelta
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import Request

from ingestion_http import get

ROOT = Path(__file__).resolve().parents[1]
VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}


def article_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme in ('http', 'https') and parsed.hostname == 'v.daum.net'
            and re.fullmatch(r'/v/\d+', parsed.path)
            and not parsed.username and not parsed.password and parsed.port in (None, 80, 443)):
        return 'https://v.daum.net' + parsed.path
    raise ValueError('지원 URL: https://v.daum.net/v/<기사번호>')


class SearchParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        if tag != 'a':
            return
        try:
            url = article_url(dict(attrs).get('href', ''))
        except ValueError:
            return
        if url not in self.urls:
            self.urls.append(url)


class ArticleParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta = {}
        self.stack = []
        self.parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta':
            self.meta[attrs.get('property') or attrs.get('name')] = attrs.get('content', '')
        if tag in ('p', 'br', 'div', 'section'):
            self.parts.append('\n')
        if tag not in VOID:
            self.stack.append((tag, 'article_view' in attrs.get('class', '').split()))

    def handle_endtag(self, tag):
        for index in range(len(self.stack)-1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break
        if tag in ('p', 'div', 'section'):
            self.parts.append('\n')

    def handle_data(self, data):
        if any(body for _, body in self.stack) and not any(
                tag in ('script', 'style', 'noscript') for tag, _ in self.stack):
            self.parts.append(data)


def parse_article(html, url):
    parser = ArticleParser()
    parser.feed(html)
    body = '\n'.join(line for line in
        (' '.join(line.split()) for line in ''.join(parser.parts).splitlines()) if line)
    title = parser.meta.get('og:title', '').strip()
    if not title or len(body) < 50:
        raise ValueError('제목 또는 본문 누락: 페이지 구조/응답 확인 필요')
    raw_date = parser.meta.get('og:regDate')
    published_at = None
    if raw_date:
        try:
            published_at = datetime.strptime(raw_date, '%Y%m%d%H%M%S').replace(
                tzinfo=timezone(timedelta(hours=9))).isoformat()
        except ValueError:
            pass
    return dict(url=article_url(url), title=title, body=body,
                publisher=parser.meta.get('og:article:author'),
                published_at=published_at, published_at_raw=raw_date,
                image_url=parser.meta.get('og:image') or None,
                extractor_version='daum-news-v1')


def download(url, folder, name):
    raw, final_url, content_type, status = get(Request(url, headers={
        'User-Agent': 'cp-main-news-smoke/1.0'}))
    if status != 200 or content_type != 'text/html':
        raise ValueError(f'HTML 응답이 아님: {status} {content_type}')
    path = folder / name
    path.write_bytes(raw)
    return raw.decode('utf-8'), final_url, str(path.relative_to(ROOT))


def positive(value):
    number = int(value)
    if not 1 <= number <= 100:
        raise argparse.ArgumentTypeError('1~100 사이 값 필요')
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--query', help='기업명 또는 뉴스 검색어')
    source.add_argument('--url', action='append', help='다음 기사 URL; 반복 지정 가능')
    parser.add_argument('--limit', type=positive, default=3, help='최대 기사 요청 수 (기본 3)')
    parser.add_argument('--pages', type=positive, default=1, help='최대 검색 페이지 (기본 1)')
    args = parser.parse_args()
    if args.query is not None and not args.query.strip():
        parser.error('빈 검색어는 사용할 수 없습니다')
    try:
        urls = list(dict.fromkeys(article_url(url) for url in (args.url or [])))
    except ValueError as exc:
        parser.error(str(exc))
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    raw_dir = ROOT / 'data/raw/news' / run_id
    out_dir = ROOT / 'data/processed/news' / run_id
    raw_dir.mkdir(parents=True)
    out_dir.mkdir(parents=True)
    report = dict(run_id=run_id, query=args.query, source='daum_news',
                  relevance_status='unverified', articles=[], errors=[], search_pages=[])
    if args.query:
        for page in range(1, args.pages+1):
            url = 'https://search.daum.net/search?' + urlencode(dict(
                w='news', q=args.query, sort='recency', p=page))
            try:
                html, _, raw_path = download(url, raw_dir, f'search-{page}.html')
                search = SearchParser()
                search.feed(html)
                report['search_pages'].append(dict(url=url, raw_uri=raw_path, found=len(search.urls)))
                urls.extend(item for item in search.urls if item not in urls)
                if len(urls) >= args.limit or not search.urls:
                    break
            except Exception as exc:
                report['errors'].append(dict(stage='search', url=url, error=str(exc)))
                break
            time.sleep(1)
    for url in urls[:args.limit]:
        time.sleep(1)
        try:
            digest = hashlib.sha256(url.encode()).hexdigest()
            html, final_url, raw_path = download(url, raw_dir, digest + '.html')
            record = parse_article(html, final_url)
            record.update(document_id=digest, raw_uri=raw_path,
                          collected_at=datetime.now(timezone.utc).isoformat())
            report['articles'].append(record)
            print(f"OK {record['title']} ({len(record['body'])}자)")
        except Exception as exc:
            report['errors'].append(dict(stage='article', url=url, error=str(exc)))
            print(f'FAIL {url}: {exc}', file=sys.stderr)
    report['discovered'] = len(urls)
    report['attempted'] = min(len(urls), args.limit)
    report['status'] = ('partial' if report['errors'] else 'success') if report['articles'] else 'failed'
    result = out_dir / 'result.json'
    result.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"{report['status']}: {len(report['articles'])}건 저장, 오류 {len(report['errors'])}건\n결과: {result}")
    return 0 if report['status'] == 'success' else 1


if __name__ == '__main__':
    sys.exit(main())
