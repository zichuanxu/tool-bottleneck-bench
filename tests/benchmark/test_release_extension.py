import json
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from subprocess import CompletedProcess
from unittest import mock

from bottleneckbench.core.schema import stable_hash
from experiments.analyze_interaction_scale_sensitivity import exact_sign_flip
from experiments import build_release_analysis_manifest as release_manifest
from experiments.build_release_analysis_manifest import _assert_model_rows, validate_plan_artifact, validate_plan_sources


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".jsonl":
        path.write_text("\n".join(json.dumps(row) for row in value) + "\n", encoding="utf-8")
    else:
        path.write_text(json.dumps(value) + "\n", encoding="utf-8")


class ReleaseExtensionTest(unittest.TestCase):
    def test_exact_sign_flip_enumerates_all_assignments(self) -> None:
        self.assertEqual(exact_sign_flip([1.0, 1.0]), 0.5)

    def test_frozen_plan_accepts_exact_committed_source_after_worktree_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.py"
            source.write_text("new\n", encoding="utf-8")
            plan = {"inputs": [{
                "path": "source.py",
                "sha256": sha256(b"old\n").hexdigest(),
            }]}
            plan["plan_hash"] = stable_hash(plan)
            archived = CompletedProcess([], 0, stdout=b"old\n", stderr=b"")
            with mock.patch.object(release_manifest, "ROOT", root), mock.patch.object(
                release_manifest.subprocess, "run", return_value=archived
            ):
                validate_plan_artifact(root / "plan.json", plan)

    def test_portable_release_validates_plan_hash_without_acquisition_sources(self) -> None:
        plan = {"inputs": [{"path": "private-source.py", "sha256": "0" * 64}]}
        plan["plan_hash"] = stable_hash(plan)
        validate_plan_artifact(Path("plan.json"), plan, verify_inputs=False)

    def test_plan_sources_require_exact_globally_unique_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = []
            for index, model in enumerate(("m0", "m1")):
                condition = {"model": model, "family_id": f"f{index}"}
                condition_id = stable_hash(condition)
                plan = {"conditions": [condition]}
                plan["plan_hash"] = stable_hash(plan)
                plan_path = root / f"plan-{index}.json"
                rows_path = root / f"rows-{index}.jsonl"
                _write(plan_path, plan)
                _write(rows_path, [{
                    "condition": condition,
                    "condition_id": condition_id,
                    "plan_hash": plan["plan_hash"],
                }])
                sources.append({
                    "plan": plan_path,
                    "rows": [rows_path],
                    "models": {model},
                })

            entries = validate_plan_sources(sources, require_zero_weight=False)
            self.assertEqual(sum(int(entry["rows"]) for entry in entries), 2)

            selected = [{**sources[0], "condition_ids": {
                json.loads(sources[0]["rows"][0].read_text().splitlines()[0])["condition_id"]
            }}]
            selected_id = next(iter(selected[0]["condition_ids"]))
            selected_entries = validate_plan_sources(
                selected,
                require_zero_weight=False,
                expected_condition_ids={selected_id},
            )
            self.assertEqual(len(selected_entries), 1)
            _assert_model_rows(selected_entries, {"m0"}, 1, "synthetic")
            with self.assertRaisesRegex(ValueError, "do not close"):
                validate_plan_sources(
                    selected,
                    require_zero_weight=False,
                    expected_condition_ids={"not-selected"},
                )
            with self.assertRaisesRegex(ValueError, "not a complete per-configuration block"):
                _assert_model_rows(selected_entries, {"m0"}, 2, "synthetic")

            _write(sources[1]["rows"][0], [])
            with self.assertRaisesRegex(ValueError, "does not match its frozen plan"):
                validate_plan_sources(sources, require_zero_weight=False)

            duplicate = json.loads(sources[0]["rows"][0].read_text().splitlines()[0])
            duplicate_plan = {"conditions": [duplicate["condition"]]}
            duplicate_plan["plan_hash"] = stable_hash(duplicate_plan)
            duplicate["plan_hash"] = duplicate_plan["plan_hash"]
            _write(sources[1]["plan"], duplicate_plan)
            _write(sources[1]["rows"][0], [duplicate])
            sources[1]["models"] = {"m0"}
            with self.assertRaisesRegex(ValueError, "duplicate condition ID"):
                validate_plan_sources(sources, require_zero_weight=False)


if __name__ == "__main__":
    unittest.main()
