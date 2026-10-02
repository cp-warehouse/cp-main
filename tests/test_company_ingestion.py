import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('ingest', Path(__file__).resolve().parents[1]/'scripts/ingest_company.py')
ingest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ingest)


class TransformTests(unittest.TestCase):
    def snapshot(self, folder, data):
        path = Path(folder)/'response.json'
        path.write_text(json.dumps(data))
        path.with_name('metadata.json').write_text(json.dumps({
            'endpoint':'company.json','params':{'corp_code':'00000001'},
            'collected_at':'2026-10-02T00:00:00+00:00',
            'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}))
        return path

    def test_missing_optional_and_leading_zero(self):
        with tempfile.TemporaryDirectory() as folder:
            result = ingest.transform(self.snapshot(folder, {'status':'000','corp_name':'합성 기업'}))
            self.assertEqual(result['display_name'], '합성 기업')
            self.assertEqual(result['corp_code'], '00000001')
            self.assertIsNone(result['website_url'])

    def test_api_failure_not_empty_success(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, '013'):
                ingest.transform(self.snapshot(folder, {'status':'013'}))

    def test_tampered_raw_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.snapshot(folder, {'status':'000','corp_name':'합성 기업'})
            path.write_text('{}')
            with self.assertRaisesRegex(ValueError, '해시'):
                ingest.transform(path)

    def test_missing_name_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, '법인명'):
                ingest.transform(self.snapshot(folder, {'status':'000','corp_name':' '}))


if __name__ == '__main__':
    unittest.main()
