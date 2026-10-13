"""The public pilot export must reject altered evidence before analysis."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "experiments"))
import replay_policy_pilot as pilot


class PolicyReplayTest(unittest.TestCase):
    def test_export_is_complete_and_tampering_fails_closed(self):
        costs = pilot.validate_inputs()
        self.assertEqual(len(costs), 1536)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name in (*pilot.FILES, "provenance.json"):
                shutil.copyfile(pilot.PUBLIC / name, directory / name)
            (directory / "costs.json").write_text(json.dumps({}))
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                pilot.validate_inputs(directory)
