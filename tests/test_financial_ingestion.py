from decimal import Decimal
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('financial', Path(__file__).resolve().parents[1]/'scripts/ingest_financial.py')
financial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(financial)


class FinancialUnitTests(unittest.TestCase):
    def test_decimal_amount(self):
        self.assertEqual(financial.decimal_amount('12,345'), Decimal('12345'))
        self.assertEqual(financial.decimal_amount('-10'), Decimal('-10'))

    def test_empty_and_decimal_amount_rejected(self):
        for value in ['', None, '1.5', '원']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                financial.decimal_amount(value)

    def test_operating_income_source_name_variants(self):
        source_names, canonical_name, basis = financial.TARGETS[
            ('CIS', 'dart_OperatingIncomeLoss')]
        self.assertEqual(source_names, {'영업이익', '영업이익(손실)'})
        self.assertEqual(canonical_name, '영업이익')
        self.assertEqual(basis, 'annual')


if __name__ == '__main__':
    unittest.main()
