import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from crawl_dart_news import read_catalog, select_companies, relevance, search_name


class PipelineTests(unittest.TestCase):
    def test_catalog_keeps_leading_zero_and_unlisted_companies(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'catalog.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('CORPCODE.xml', '<result><list><corp_code>00000001</corp_code>'
                    '<corp_name>새기업</corp_name><stock_code> </stock_code></list></result>')
            rows = read_catalog(path)
            self.assertEqual(rows[0]['corp_code'], '00000001')
            self.assertEqual(rows[0]['stock_code'], '')
            self.assertEqual(select_companies(rows, ['새기업'], 1), rows)

    def test_rejects_non_zip_api_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'response.zip'
            path.write_text('<result><status>020</status></result>')
            with self.assertRaisesRegex(ValueError, 'ZIP'):
                read_catalog(path)

    def test_ambiguous_name_does_not_pick_arbitrary_legal_entity(self):
        with self.assertRaisesRegex(ValueError, '2개'):
            select_companies([{'corp_name': '동명기업'}, {'corp_name': '동명기업'}], ['동명기업'], 1)

    def test_default_sample_comes_from_listed_catalog(self):
        rows = [{'corp_name': '비상장', 'stock_code': ''},
                {'corp_name': '두번째', 'stock_code': '000020'},
                {'corp_name': '첫번째', 'stock_code': '000010'}]
        self.assertEqual(select_companies(rows, None, 1)[0]['corp_name'], '첫번째')

    def test_name_match_is_evidence_not_verified_link(self):
        result = relevance({'title': '새기업 실적 발표', 'body': '내용'}, ['새기업'])
        self.assertEqual(result['status'], 'candidate')
        self.assertFalse(result['verified'])
        self.assertEqual(result['evidence'][0]['field'], 'title')
        self.assertEqual(relevance({'title': '다른 기업', 'body': '내용'}, ['새기업'])['status'], 'unmatched')

    def test_search_name_strips_only_legal_prefix_suffix(self):
        self.assertEqual(search_name('주식회사 새로운기업'), '새로운기업')
        self.assertEqual(search_name('새로운기업(주)'), '새로운기업')
        self.assertEqual(search_name('기업주식회사연구소'), '기업주식회사연구소')
