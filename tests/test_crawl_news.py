import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from crawl_news import SearchParser, article_url, parse_article


class NewsTests(unittest.TestCase):
    def test_search_deduplicates_and_excludes_channel_and_foreign_hosts(self):
        parser = SearchParser()
        parser.feed('''<a href="http://v.daum.net/v/123?x=1">title</a>
            <a href="https://v.daum.net/v/123">duplicate</a>
            <a href="https://v.daum.net/channel/123">publisher</a>
            <a href="https://v.daum.net.evil/v/456">foreign</a>''')
        self.assertEqual(parser.urls, ['https://v.daum.net/v/123'])

    def test_body_without_image_and_exact_date(self):
        paragraph = '기업이 신규 서비스를 발표했습니다. ' * 5
        article = parse_article(f'''<meta property="og:title" content="기업 소식">
            <meta property="og:regDate" content="20261002020611">
            <div>외부 메뉴</div><div class="article_view"><section>
            <p>{paragraph}<strong>강조</strong></p><script>광고코드</script>
            </section></div><footer>푸터</footer>''', 'https://v.daum.net/v/123')
        self.assertIn('강조', article['body'])
        for excluded in ('외부 메뉴', '광고코드', '푸터'):
            self.assertNotIn(excluded, article['body'])
        self.assertIsNone(article['image_url'])
        self.assertEqual(article['published_at'], '2026-10-02T02:06:11+09:00')

    def test_missing_date_is_not_replaced_by_collection_time(self):
        record = parse_article('<meta property="og:title" content="제목">'
            '<div class="article_view">' + '본문 내용 ' * 20 + '</div>',
            'https://v.daum.net/v/123')
        self.assertIsNone(record['published_at'])

    def test_layout_change_fails_instead_of_saving_menu(self):
        with self.assertRaises(ValueError):
            parse_article('<meta property="og:title" content="제목"><div>'
                          + '메뉴 ' * 100 + '</div>', 'https://v.daum.net/v/123')

    def test_direct_url_rejects_other_sites(self):
        with self.assertRaises(ValueError):
            article_url('http://127.0.0.1/v/123')


if __name__ == '__main__':
    unittest.main()
