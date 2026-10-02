import io
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import ingest_phase1 as batch
import ingestion_http
from migrate import migration_body


class AutomationTests(unittest.TestCase):
    def test_transient_failure_retries_but_404_does_not(self):
        for code, expected in [(503, 3), (404, 1), (429, 1)]:
            with self.subTest(code=code), patch('ingestion_http.urllib.request.urlopen',
                    side_effect=urllib.error.HTTPError('https://secret.invalid/?key=secret', code,'',{},None)) as call, \
                    patch('ingestion_http.time.sleep'):
                with self.assertRaises(RuntimeError) as error:
                    ingestion_http.get('https://example.invalid')
                self.assertEqual(call.call_count, expected)
                self.assertNotIn('secret', str(error.exception))

    def test_migration_wrapper_removed_without_removing_function_begin(self):
        text = 'BEGIN;\nDO $$ BEGIN\nEND $$;\nCOMMIT;\n'
        self.assertEqual(migration_body(text), 'DO $$ BEGIN\nEND $$;')

    def test_one_failure_does_not_prevent_next_company(self):
        targets = [{'corp_code':'00000001','label':'first'},
                   {'corp_code':'00000002','label':'second'}]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'targets.json'
            path.write_text(json.dumps(targets))
            with patch.object(sys,'argv',['batch','--config',str(path)]), \
                 patch('ingest_phase1.ingest_target',side_effect=[ValueError('bad record'),{'label':'second'}]) as call, \
                 patch('sys.stdout',new_callable=io.StringIO) as output:
                with self.assertRaises(SystemExit) as error:
                    batch.main()
                self.assertEqual(error.exception.code,1)
                self.assertEqual(call.call_count,2)
                result=json.loads(output.getvalue())
                self.assertEqual(result['summary']['failed_targets'],1)
                self.assertEqual(result['summary']['succeeded_targets'],1)

    def test_duplicate_identity_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'targets.json'
            path.write_text(json.dumps([{'corp_code':'00000001','label':'a'}]*2))
            with self.assertRaises(ValueError):
                batch.load_targets(path)

    def test_retry_selects_only_failed_company(self):
        targets = [{'corp_code':'00000001','label':'first'},
                   {'corp_code':'00000002','label':'second'}]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'targets.json'
            path.write_text(json.dumps(targets))
            checksum = hashlib.sha256(path.read_bytes()).hexdigest()
            def query(sql):
                if 'json_build_object' in sql:
                    return json.dumps({'year':2025,'hash':checksum})
                if 'SELECT target_id' in sql:
                    return 'dart:00000002'
                return ''
            with patch.object(sys,'argv',['batch','--config',str(path),'--retry-run',
                    '11111111-1111-1111-1111-111111111111']), \
                 patch('ingest_phase1.execute',side_effect=query), \
                 patch('ingest_phase1.ingest_target',return_value={'label':'second'}) as call, \
                 patch('sys.stdout',new_callable=io.StringIO):
                batch.main()
                self.assertEqual(call.call_count,1)
                self.assertEqual(call.call_args.args[0]['corp_code'],'00000002')


if __name__ == '__main__':
    unittest.main()
