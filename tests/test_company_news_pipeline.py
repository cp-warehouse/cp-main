import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import company_news_pipeline as pipeline


class CompatibilityTests(unittest.TestCase):
    def test_sync_preserves_codes_without_news_activation(self):
        target,args=pipeline.translate(['sync','--corp-code','00126380','--enable-news'])
        self.assertIs(target,pipeline.collect_dart.main)
        self.assertEqual(args,['--corp-code','00126380'])

    def test_news_keeps_limits_and_maps_article_option(self):
        target,args=pipeline.translate(['news','--company-limit','3','--article-limit','2'])
        self.assertIs(target,pipeline.collect_news.main)
        self.assertEqual(args,['--company-limit','3','--articles-per-company','2'])

    def test_show_uses_company_query(self):
        target,args=pipeline.translate(['show'])
        self.assertIs(target,pipeline.collection_status.main)
        self.assertEqual(args,['--companies','1000'])

    def test_unknown_command_rejected(self):
        with self.assertRaises(ValueError):
            pipeline.translate(['invalid'])
