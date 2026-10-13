from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from bottleneckbench.core.cli import main
from bottleneckbench.core.schema import canonical_json


class CliTest(unittest.TestCase):
    def test_audit_fails_closed_and_freeze_is_blocked_for_invalid_panel(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audit_path = root / "audit.json"
            plan_path = root / "plan.json"

            audit_status = main(
                ["audit", "--panel", "T", "--output", str(audit_path)]
            )
            with self.assertRaisesRegex(ValueError, "construct audit"):
                main(
                    [
                        "freeze",
                        "--panel",
                        "T",
                        "--output",
                        str(plan_path),
                        "--model",
                        "agnes-2.5-flash",
                        "--selector-seed",
                        "20260803",
                        "--frozen-at",
                        "internally-labeled",
                    ]
                )
            audit = json.loads(audit_path.read_text())

        self.assertEqual(audit_status, 2)
        self.assertFalse(audit["passed"])
        self.assertEqual(audit["panel"], "T")
        self.assertFalse(plan_path.exists())

    def test_analyze_command_is_byte_deterministic(self) -> None:
        row = {
            "condition_id": "one",
            "condition": {
                "task_id": "D1",
                "track": "discovery",
                "model": "agnes-2.5-flash",
                "harness": "flat-retrieval",
                "representation": "raw",
                "registry_size": 32,
                "exposure_size": 8,
                "seed": 1,
            },
            "status": "verified_success",
            "exposure": {"required_available": 1.0},
            "verdict": {"strict_vts": True},
            "usage": {"total_tokens": 10},
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = root / "runs.jsonl"
            first = root / "analysis-1.json"
            second = root / "analysis-2.json"
            runs.write_text(canonical_json(row) + "\n")

            self.assertEqual(main(["analyze", "--input", str(runs), "--output", str(first)]), 0)
            self.assertEqual(main(["analyze", "--input", str(runs), "--output", str(second)]), 0)

            self.assertEqual(first.read_bytes(), second.read_bytes())


if __name__ == "__main__":
    unittest.main()
