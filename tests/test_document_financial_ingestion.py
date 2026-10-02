import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    'document_financial',
    Path(__file__).resolve().parents[1] / 'scripts/ingest_document_financial.py')
document_financial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(document_financial)


class DocumentFinancialTests(unittest.TestCase):
    def test_label_and_current_amount(self):
        self.assertEqual(document_financial.label_key('Ⅲ. 영업이익'), '영업이익')
        self.assertEqual(document_financial.label_key('영업수익(주석13)'), '영업수익')
        self.assertEqual(
            document_financial.current_amount(['영업수익', '25,35', '1,037,917', '964,561']),
            1037917)

    def test_parenthesized_amount_is_negative(self):
        self.assertEqual(document_financial.amount('(14,200)'), -14200)


if __name__ == '__main__':
    unittest.main()
