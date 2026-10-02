import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    'phase1', Path(__file__).resolve().parents[1] / 'scripts/ingest_phase1.py')
phase1 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(phase1)


class Phase1TargetsTests(unittest.TestCase):
    def test_unique_identities_allow_non_dart_companies(self):
        targets = phase1.load_targets()
        self.assertEqual(len({phase1.target_id(target) for target in targets}), len(targets))
        self.assertTrue(any(not target.get('corp_code') for target in targets))


if __name__ == '__main__':
    unittest.main()
