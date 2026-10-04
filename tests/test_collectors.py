import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import collection_common as common
import collect_dart as dart
import collect_news as news


class BatchTests(unittest.TestCase):
    def test_thousand_selection_and_next_batch(self):
        catalog=[dict(corp_code=f'{i:08}',corp_name=str(i),stock_code='') for i in range(1100)]
        first=dart.plan(catalog,set(),set(),1000)
        self.assertEqual(len(first),1000)
        completed={row['corp_code'] for row in first[:900]}
        reserved={row['corp_code'] for row in first[900:]}
        second=dart.plan(catalog,completed,reserved,1000)
        self.assertEqual(len(second),100)
        self.assertFalse({row['corp_code'] for row in first}&{row['corp_code'] for row in second})

    def test_resume_survives_memory_reset_and_skips_completed(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(common,'STATE',Path(folder)):
            batch=common.create_batch('dart',{},[dict(key='a',details={}),dict(key='b',details={})])
            common.mark(batch,'a','done',{})
            common.mark(batch,'b','running',{'progress':1})
            common._batches.clear()
            common.resume_batch(batch,'dart')
            self.assertEqual([item['item_key'] for item in common.pending(batch)],['b'])
            self.assertEqual(common.reserved_keys('dart'),{'b'})
            self.assertEqual(common.pending(batch)[0]['details']['progress'],1)

    def test_wrong_collector_resume_rejected(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(common,'STATE',Path(folder)):
            batch=common.create_batch('dart',{},[])
            with self.assertRaises(ValueError):
                common.resume_batch(batch,'news')

    def test_budget_prevents_request_and_batch_pause_added(self):
        pace=common.Pace(2,50,5,10,budget=51)
        pace.count=50
        pace.last=100
        with patch.object(common.time,'monotonic',return_value=100),patch.object(common.time,'sleep') as sleep,patch.object(common.random,'uniform',return_value=7):
            pace.before()
            sleep.assert_called_once_with(9)
            with self.assertRaises(common.Stop):
                pace.before()

    def test_news_resume_reuses_saved_search_and_skips_completed_urls(self):
        item={'item_key':'id','details':dict(legal_name='회사',display_name='회사',search_done=True,
              urls=['https://v.daum.net/v/1','https://v.daum.net/v/2'],completed_urls=['https://v.daum.net/v/1'])}
        doc=dict(document_id='doc',title='회사',body='회사 기사')
        with patch.object(news,'execute'),patch.object(news,'rows',return_value=[doc]),patch.object(news,'store_link') as link,patch.object(news,'mark'),patch.object(news,'fetch') as fetch:
            news.collect('batch',item,dict(articles_per_company=2),Path('/tmp'),None)
            fetch.assert_not_called()
            link.assert_called_once()
            self.assertEqual(len(item['details']['completed_urls']),2)

    def test_news_access_limit_stops_and_no_retries(self):
        pace=common.Pace(2)
        with patch.object(news,'get',side_effect=RuntimeError('HTTP 429')) as get:
            with self.assertRaises(common.Stop):
                news.fetch('https://example.test',Path('/tmp'),pace)
            self.assertEqual(get.call_args.kwargs['attempts'],1)

    def test_no_dart_or_schema_creation_in_news(self):
        import ast
        tree=ast.parse(Path(news.__file__).read_text())
        modules=[node.module for node in ast.walk(tree) if isinstance(node,ast.ImportFrom)]
        self.assertFalse(any('dart' in (name or '') or 'ingest_company' in (name or '') for name in modules))

    def test_recent_news_attempt_is_read_from_checkpoint(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(common,'STATE',Path(folder)):
            batch=common.create_batch('news',{},[dict(key='company-a',details={}),dict(key='company-b',details={})])
            common.mark(batch,'company-a','running',{})
            common.mark(batch,'company-a','done',{})
            attempts=common.last_attempts('news')
            self.assertIn('company-a',attempts)
            self.assertNotIn('company-b',attempts)
