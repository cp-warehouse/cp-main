import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('product',Path(__file__).resolve().parents[1]/'scripts/ingest_product.py')
product=importlib.util.module_from_spec(spec); spec.loader.exec_module(product)


class ProductUnitTests(unittest.TestCase):
    def test_canonical_url(self):
        self.assertEqual(product.canonical_url('HTTPS://WWW.NAVER.COM#top'),'https://www.naver.com/')

    def test_non_http_url_rejected(self):
        with self.assertRaises(ValueError):
            product.canonical_url('javascript:alert(1)')

    def test_official_domain_allows_only_domain_and_subdomains(self):
        self.assertTrue(product.host_is_allowed('www.kakaocorp.com', 'kakaocorp.com'))
        self.assertTrue(product.host_is_allowed('kakaocorp.com', 'kakaocorp.com'))
        self.assertFalse(product.host_is_allowed('fakekakaocorp.com', 'kakaocorp.com'))


if __name__=='__main__':
    unittest.main()
