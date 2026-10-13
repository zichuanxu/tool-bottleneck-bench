import json
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "experiments"))
import build_opus_release_source_map as source
from build_release_analysis_manifest import plan_configurations


def write(path, value, hash_field=None):
    if hash_field:
        value.pop(hash_field, None)
        value[hash_field] = source.stable_hash(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source.canonical_json(value) + "\n")


class OpusReleaseSourceMapTests(unittest.TestCase):
    def fixture(self, root):
        root = root.resolve()
        generator = root / "experiments/build_opus_release_source_map.py"
        generator.parent.mkdir(parents=True)
        shutil.copyfile(source.__file__, generator)
        launch_path = root / source.ACQUISITION / "plans/manifest.json"
        launch = {"model": source.MODEL, "planned_conditions": 1336, "condition_ids": [], "plans": []}
        certificate = {"status": "complete", "rows": 1336,
            "eligible_for_unified_first_class_panel": True, "condition_ids_exact_once": True,
            "provider_failures": 0, "transport_failures": 0, "hard_cumulative_cap_usd": 210, "outputs": []}
        for block, (count, _, _) in source.BLOCKS.items():
            conditions = [{"model": source.MODEL, "block": block, "index": i} for i in range(count)]
            plan = {"conditions": conditions, "model": source.MODEL, "execution": {
                "provider_only": ["anthropic"], "provider_allow_fallbacks": False,
                "omit_temperature": True, "omit_seed": True, "omit_parallel_tool_calls": True}}
            path = launch_path.parent / f"{block}.json"
            write(path, plan, "plan_hash")
            entry = {"name": path.name, "block": block, "conditions": count,
                "sha256": source._sha(path), "output": str(source.ACQUISITION / "execution" / f"{block}.jsonl")}
            launch["plans"].append(entry)
            output = source._selected_output(root, entry)
            output.parent.mkdir(parents=True, exist_ok=True)
            rows = [{"condition": c, "condition_id": source.stable_hash(c),
                "plan_hash": plan["plan_hash"], "attempt": 1, "status": "complete"} for c in conditions]
            output.write_text("".join(source.canonical_json(r) + "\n" for r in rows))
            launch["condition_ids"].extend(r["condition_id"] for r in rows)
            certificate["outputs"].append({"name": str(output.relative_to(root)), "sha256": source._sha(output), "rows": count})
        write(launch_path, launch, "manifest_hash")
        fixed_manifest = patch.object(source, "FROZEN_MANIFEST_HASH", launch["manifest_hash"])
        fixed_manifest.start()
        self.addCleanup(fixed_manifest.stop)
        amendment_path = root / "results/prospective-plans/opus-5-completion-continuation-12/amendment.json"
        amendment = {"hard_cumulative_cap_usd": 210}
        write(amendment_path, amendment, "artifact_hash")
        amendment_path.with_name("protocol.md").write_text("Synthetic test protocol; no hosted measurements.\n")
        fixed_authorization = patch.object(source, "FROZEN_AUTHORIZATION_HASH", amendment["artifact_hash"])
        fixed_authorization.start()
        self.addCleanup(fixed_authorization.stop)
        certificate.update(manifest_hash=launch["manifest_hash"], completion_continuation_amendment=amendment["artifact_hash"])
        write(root / source.ACQUISITION / "execution/completion.json", certificate, "artifact_hash")
        source.prepare_admission(root)
        write(root / source.PRICING, {"models": {source.MODEL: {"input": 5, "output": 25,
            "provider_only": ["anthropic"], "provider": "OpenRouter / Anthropic"}}})
        write(root / source.ACQUISITION / "endpoint-snapshot.json", {"fixture": "not a hosted measurement",
            "selected_route": {"provider_only": ["anthropic"], "allow_fallbacks": False,
                "pricing": {"prompt": "0.000005", "completion": "0.000025"}}})
        result = source.build(root)
        path = root / source.ACQUISITION / "release-source-map.json"
        write(path, result)
        return path, result

    def test_complete_portable_inventory_replays_without_any_raw_receipts(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            path, _ = self.fixture(root)
            (root / source.ACQUISITION / "execution/completion.json").unlink()
            (root / "results/prospective-plans/opus-5-completion-continuation-12/amendment.json").unlink()
            result = source.validate_source_map(path, root)
            self.assertEqual(result["rows"], 1336)
            self.assertEqual(len(result["sources"]), 9)
            self.assertFalse((root / source.ACQUISITION / "execution/receipts").exists())

    def test_partial_or_infrastructure_certificate_cannot_admit(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            _, _ = self.fixture(root)
            path = root / source.ACQUISITION / "execution/completion.json"
            baseline = source._read(path)
            for mutation in ({"rows": 1335}, {"status": "incomplete"}, {"provider_failures": 1},
                             {"condition_ids_exact_once": False}, {"eligible_for_unified_first_class_panel": False}):
                with self.subTest(mutation=mutation):
                    write(path, {**baseline, **mutation}, "artifact_hash")
                    with self.assertRaisesRegex(ValueError, "admission gate is closed"):
                        source.prepare_admission(root)

    def test_incomplete_selected_map_and_changed_row_fail_closed(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            path, result = self.fixture(root)
            write(path, {**result, "sources": result["sources"][:-1]}, "artifact_hash")
            with self.assertRaisesRegex(ValueError, "inventory identity"):
                source.validate_source_map(path, root)
            write(path, result)
            row_path = root / result["sources"][0]["rows"][0]
            row_path.write_text(row_path.read_text() + "{}\n")
            with self.assertRaisesRegex(ValueError, "bound artifact changed"):
                source.validate_source_map(path, root)

    def test_configuration_adapter_never_chooses_between_ambiguous_inventories(self):
        one = {"model": source.MODEL, "slug": "claude-opus-5-structural"}
        self.assertEqual(plan_configurations({"configuration": one}), [one])
        self.assertEqual(plan_configurations({"configurations": [one]}), [one])
        with self.assertRaisesRegex(ValueError, "unambiguous"):
            plan_configurations({"configuration": one, "configurations": [one]})


if __name__ == "__main__":
    unittest.main()
