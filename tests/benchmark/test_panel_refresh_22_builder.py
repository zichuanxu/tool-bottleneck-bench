"""Synthetic, offline admission-negative tests; never touch acquisition evidence."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / "experiments/build_panel_refresh_22.py"
SPEC = importlib.util.spec_from_file_location("panel_refresh_22_builder", SCRIPT)
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)

RETAINED_GROUPS = {
    "candidate_capacity": 3456, "candidate_discovery": 1296,
    "candidate_menu_intervention": 192, "candidate_repair": 720,
    "candidate_sentinel": 360, "candidate_structural_additional": 1080,
    "candidate_workflow_frontier": 80, "capacity": 2880, "discovery": 1080,
    "external": 160, "frontier_capacity": 1728, "frontier_discovery": 648,
    "frontier_menu_intervention": 288, "frontier_repair": 432,
    "frontier_sentinel": 216, "frontier_structural_additional": 648,
    "frontier_workflow_frontier": 48, "historical_menu_intervention": 320,
    "repair": 720, "sentinel": 360, "structural_holdout": 432, "workflow_frontier": 80}


class Fixture:
    def __init__(self, root):
        self.root = root
        self.anchors = {}
        self.plan_objects = {}
        self.make_retained()
        self.make_campaigns()
        for model in builder.V5_REPORTS:
            self.make_v5(model)

    def write(self, name, value, *, hash_field=None, anchor=False):
        value = deepcopy(value)
        if hash_field:
            value.pop(hash_field, None)
            value[hash_field] = builder.stable_hash(value)
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(builder.canonical(value) + b"\n")
        if anchor:
            self.anchors[name] = builder.bind(self.root, name)["sha256"]
        return value

    def journal(self, name, rows):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [builder.canonical(row) + b"\n" for row in rows]
        path.write_bytes(b"".join(lines))
        return lines

    def read(self, name):
        return json.loads((self.root / name).read_text())

    def make_retained(self):
        models = [f"retained/model-{i}" for i in range(14)]
        panel_path = builder.RETAINED + "/release-panel.json"
        panel = {"configurations": [{"model": m} for m in models],
            "coverage_counts": {"core_descriptive": 14, "auxiliary_descriptive": 13,
                "structural_additional_descriptive": 8, "concurrent_menu_descriptive": 5,
                "layer_activation_initial": 2, "structural_registered": 2}}
        self.write(panel_path, panel, hash_field="artifact_hash", anchor=True)
        inputs = {}
        for i, (group, count) in enumerate(RETAINED_GROUPS.items()):
            model = models[i % 14]
            name = f"synthetic-retained/{group}.jsonl"
            self.journal(name, [{"condition_id": f"retained-{group}-{j}", "model": model} for j in range(count)])
            inputs[group] = [{**builder.bind(self.root, name), "model": model, "rows": count}]
        manifest = {"models": models, "total_rows": 17224,
            "panel_sha256": self.anchors[panel_path], "inputs": inputs, "expected_rows": RETAINED_GROUPS}
        self.write(builder.RETAINED + "/release-analysis-inputs.json", manifest, hash_field="manifest_hash", anchor=True)

    def make_campaigns(self):
        sets = {"original": builder.ORIGINAL_SIX | {builder.DEEPSEEK},
                "haiku": {builder.HAIKU}, "deepinfra": {builder.DEEPSEEK}}
        for campaign, models in sets.items():
            configs, entries, all_ids = [], [], []
            for model in sorted(models):
                tag = "deepinfra/fp8" if campaign == "deepinfra" else model.split("/")[0]
                config = {"model": model,
                          "display_name": "DeepSeek V4.1 Flash" if model == builder.DEEPSEEK else model.split("/")[-1],
                          "provider": {"only": [tag], "allow_fallbacks": False},
                          "selected_route": {"pricing": {"prompt": "0.0000001", "completion": "0.0000005"}}}
                if campaign == "haiku":
                    config["selected_route"]["pricing"]["overrides"] = [
                        {"min_prompt_tokens": 100000, "prompt": "0.0000005", "completion": "0.0000025"}]
                if campaign == "deepinfra":
                    config.update(configuration_id=builder.DEEPINFRA_CONFIGURATION)
                    config["provider"]["quantizations"] = ["fp8"]
                configs.append(config)
                for block, count in builder.BLOCKS.items():
                    conditions = [{"model": model, "block": block, "index": i} for i in range(count)]
                    if block == "toolsandbox":
                        conditions = [{**c, "condition_id": builder.stable_hash(c)} for c in conditions]
                    execution = {"provider_only": [tag], "provider_allow_fallbacks": False}
                    if campaign == "deepinfra":
                        execution["provider_quantizations"] = ["fp8"]
                    plan = {"conditions": conditions, "execution": execution}
                    base = {"original": builder.BASE, "haiku": builder.HAIKU_BASE,
                            "deepinfra": builder.DEEPINFRA_BASE}[campaign]
                    if campaign == "deepinfra":
                        name, output = base + f"/plans/{block}.json", base + f"/execution/scored/{block}.jsonl"
                    else:
                        slug = model.replace("/", "--")
                        name, output = base + f"/plans/{slug}/{block}.json", base + f"/execution/{slug}/{block}.jsonl"
                    plan = self.write(name, plan, hash_field=None if block == "workflow_frontier" else "plan_hash")
                    entry = {"model": model, "block": block, **builder.bind(self.root, name),
                             "plan_hash": plan.get("plan_hash", builder.stable_hash(plan)), "conditions": count, "output": output}
                    if campaign == "deepinfra":
                        entry["configuration_id"] = builder.DEEPINFRA_CONFIGURATION
                    entries.append(entry)
                    all_ids.extend(builder.condition_id(c) for c in conditions)
                    self.plan_objects[(campaign, model, block)] = (entry, plan)
            price = {"configuration": configs[0]} if campaign == "deepinfra" else {"configurations": configs}
            self.write(builder.PRICE_PATHS[campaign], price, hash_field="artifact_hash", anchor=True)
            manifest = {"plans": entries, "planned_conditions": sum(e["conditions"] for e in entries),
                        "inputs": [builder.bind(self.root, builder.PRICE_PATHS[campaign])],
                        "condition_ids_sha256": builder.stable_hash(sorted(all_ids))}
            if campaign == "deepinfra":
                manifest.update(model=builder.DEEPSEEK, configuration_id=builder.DEEPINFRA_CONFIGURATION)
            else:
                manifest["models"] = sorted(models)
            self.write(builder.MANIFEST_PATHS[campaign], manifest, hash_field="manifest_hash", anchor=True)

    def make_v5(self, model):
        selected = []
        for block in builder.BLOCKS:
            if block != "toolsandbox":
                entry, plan = self.plan_objects[("original", model, block)]
                selected.extend({"block": block, "condition_id": builder.condition_id(c)} for c in plan["conditions"])
        entry, plan = self.plan_objects[("original", model, "toolsandbox")]
        rows = [{**c, "plan_hash": entry["plan_hash"], "status": "complete", "score": {"similarity": 1}}
                for c in plan["conditions"]]
        keep = 68 if model == builder.QWEN else 25
        original = deepcopy(rows[:69 if model == builder.QWEN else 25])
        original[-1].update(status="failed_closed", score={"similarity": 0})
        overlay = builder.BASE + "/execution/toolsandbox-recovery-v5/" + model.replace("/", "--") + ".jsonl"
        originals = self.journal(entry["output"], original)
        supplements = self.journal(overlay, rows[keep:])
        for name, lines, count in ((entry["output"], originals, keep), (overlay, supplements, len(supplements))):
            for index, line in enumerate(lines[:count]):
                row = json.loads(line)
                selected.append({"block": "toolsandbox", "condition_id": row["condition_id"], "journal": name,
                    "row_index": index, "literal_line_sha256": hashlib.sha256(line).hexdigest(),
                    "row_sha256": builder.stable_hash(row)})
        audit_name = str(Path(builder.V5_REPORTS[model]).parent / "input-audit.json")
        audit = {"schema": "panel-refresh-v5-nine-block-audit-v1", "model": model, "passed": True,
            "release_admitted": False, "completed_conditions": 1336, "conditions_by_block": builder.BLOCKS,
            "selected": selected, "inputs": [builder.bind(self.root, p) for p in (entry["output"], overlay)]}
        self.write(audit_name, audit)
        report = {"schema": "panel-refresh-v5-nine-block-offline-replay-v2", "verification_driver_version": "v6",
            "model": model, "passed": True, "release_admitted": False, "conditions": 1336,
            "internal_conditions": 1240, "toolsandbox_conditions": 96, "new_api_calls": 0,
            "network_disabled": True, "toolsandbox_official_regrades": 96 if model == builder.QWEN else 95,
            "toolsandbox_frozen_zero_fallbacks": 0 if model == builder.QWEN else 1,
            "outputs": [builder.bind(self.root, audit_name)]}
        self.write(builder.V5_REPORTS[model], report, anchor=True)


class Refresh22BuilderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fx = Fixture(self.root)
        self.anchors = patch.object(builder, "ANCHORS", self.fx.anchors)
        self.anchors.start()
        self.addCleanup(self.anchors.stop)

    def rewrite_manifest(self, campaign, change):
        path = builder.MANIFEST_PATHS[campaign]
        value = self.fx.read(path)
        change(value)
        self.fx.write(path, value, hash_field="manifest_hash", anchor=True)

    def test_complete_planned_selection_is_not_admission_and_does_not_read_partial_outputs(self):
        result = builder.prepare_source_map(self.root)
        self.assertFalse(result["release_admitted"])
        self.assertEqual(result["independent_certificate_admission"], "not_implemented")
        self.assertEqual((len(result["sources"]), result["planned_new_conditions"], result["expected_total_rows"]), (72, 10688, 27912))
        self.assertEqual(result["expected_coverage_counts"], builder.FINAL_COVERAGE)
        self.assertEqual(result["expected_episode_counts"], builder.FINAL_EPISODES)
        self.assertEqual(sum(result["expected_group_rows"].values()), 27912)
        self.assertEqual(len(result["price_snapshots"]), 3)
        self.assertFalse((self.root / builder.HAIKU_BASE / "execution").exists())
        ds = [s for s in result["sources"] if s["model"] == builder.DEEPSEEK]
        self.assertEqual(len(ds), 9)
        self.assertTrue(all(s["plan"]["path"].startswith(builder.DEEPINFRA_BASE + "/") for s in ds))
        self.assertEqual(len({c["configuration_key"] for c in result["configurations"]}), 8)

    def test_prices_preserve_haiku_tiers_and_deepinfra_identity(self):
        configs = {c["model"]: c for c in builder.prepare_source_map(self.root)["configurations"]}
        self.assertEqual(configs[builder.HAIKU]["raw_route_pricing"]["overrides"][0]["min_prompt_tokens"], 100000)
        self.assertEqual(configs[builder.DEEPSEEK]["route"]["only"], ["deepinfra/fp8"])
        self.assertEqual(configs[builder.DEEPSEEK]["configuration_id"], builder.DEEPINFRA_CONFIGURATION)
        self.assertEqual(configs[builder.DEEPSEEK]["display_name"], "DeepSeek V4.1 Flash")

    def test_v5_selects_literal_rows_and_preserves_zero_and_timing(self):
        selections = {s["model"]: s["row_selection"] for s in builder.prepare_source_map(self.root)["sources"] if s["block"] == "toolsandbox"}
        self.assertEqual(len(selections[builder.QWEN]["selected_rows"]), 96)
        self.assertEqual(selections[builder.QWEN]["timing_policy"], "segmented_not_uninterrupted_latency")
        gem = selections[builder.GEMINI]
        zero = gem["selected_rows"][24]
        line = (self.root / zero["journal"]).read_bytes().splitlines(keepends=True)[zero["row_index"]]
        self.assertEqual(json.loads(line)["score"]["similarity"], 0)
        self.assertEqual(json.loads(line)["status"], "failed_closed")

    def test_build_refuses_before_creating_root(self):
        output = self.root / "new-analysis-root"
        with self.assertRaisesRegex(builder.AdmissionPending, "All eight complete independent model certificates"):
            builder.build(output, root=self.root)
        self.assertFalse(output.exists())

    def test_existing_output_is_not_touched(self):
        output = self.root / "existing"
        output.mkdir()
        marker = output / "keep"
        marker.write_text("preserve")
        with self.assertRaisesRegex(ValueError, "existing output"):
            builder.build(output, root=self.root)
        self.assertEqual(marker.read_text(), "preserve")

    def test_missing_independent_manifest_fails(self):
        (self.root / builder.MANIFEST_PATHS["haiku"]).unlink()
        with self.assertRaisesRegex(ValueError, "missing bound artifact"):
            builder.prepare_source_map(self.root)

    def test_missing_or_repeated_plan_fails(self):
        self.rewrite_manifest("haiku", lambda m: m["plans"].pop())
        with self.assertRaisesRegex(ValueError, "exactly nine"):
            builder.prepare_source_map(self.root)

    def test_repeated_block_fails(self):
        self.rewrite_manifest("haiku", lambda m: m["plans"].__setitem__(1, deepcopy(m["plans"][0])))
        with self.assertRaisesRegex(ValueError, "exactly nine"):
            builder.prepare_source_map(self.root)

    def test_plan_bytes_tampered_fails(self):
        entry, _ = self.fx.plan_objects[("haiku", builder.HAIKU, "structural")]
        (self.root / entry["path"]).write_text("{}")
        with self.assertRaisesRegex(ValueError, "artifact hash changed"):
            builder.prepare_source_map(self.root)

    def test_wrong_deepseek_output_route_fails_even_with_new_manifest_hash(self):
        self.rewrite_manifest("deepinfra", lambda m: m["plans"][0].update(
            output=builder.BASE + "/execution/deepseek-data-allow-v1/primary_capacity.jsonl"))
        with self.assertRaisesRegex(ValueError, "old DeepSeek route"):
            builder.prepare_source_map(self.root)

    def test_wrong_deepseek_configuration_id_fails(self):
        self.rewrite_manifest("deepinfra", lambda m: m.update(configuration_id="same-model-wrong-route"))
        with self.assertRaisesRegex(ValueError, "isolated DeepInfra"):
            builder.prepare_source_map(self.root)

    def test_price_substitution_fails(self):
        (self.root / builder.PRICE_PATHS["deepinfra"]).write_text("{}")
        with self.assertRaisesRegex(ValueError, "artifact hash changed"):
            builder.prepare_source_map(self.root)

    def test_retained_rows_changed_fails(self):
        (self.root / "synthetic-retained/capacity.jsonl").write_text("{}\n")
        with self.assertRaisesRegex(ValueError, "bound input changed"):
            builder.prepare_source_map(self.root)

    def test_duplicate_condition_payload_fails_even_with_consistent_hashes(self):
        entry, plan = self.fx.plan_objects[("haiku", builder.HAIKU, "primary_capacity")]
        plan["conditions"][1] = plan["conditions"][0]
        new = self.fx.write(entry["path"], plan, hash_field="plan_hash")
        self.rewrite_manifest("haiku", lambda m: m["plans"][0].update(
            sha256=builder.bind(self.root, entry["path"])["sha256"], plan_hash=new["plan_hash"]))
        with self.assertRaisesRegex(ValueError, "condition IDs overlap"):
            builder.prepare_source_map(self.root)

    def test_v5_literal_bytes_tampered_fails(self):
        entry, _ = self.fx.plan_objects[("original", builder.QWEN, "toolsandbox")]
        path = self.root / entry["output"]
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "bound input changed"):
            builder.prepare_source_map(self.root)

    def test_v5_selected_duplicate_fails_with_rebound_report(self):
        report_path = builder.V5_REPORTS[builder.GEMINI]
        report = self.fx.read(report_path)
        audit_path = report["outputs"][0]["path"]
        audit = self.fx.read(audit_path)
        audit["selected"][-1] = deepcopy(audit["selected"][-2])
        self.fx.write(audit_path, audit)
        report["outputs"] = [builder.bind(self.root, audit_path)]
        self.fx.write(report_path, report, anchor=True)
        with self.assertRaisesRegex(ValueError, "selected condition inventory"):
            builder.prepare_source_map(self.root)

    def test_boolean_integer_payload_alias_fails_with_rebound_bytes(self):
        model = builder.GEMINI
        report_path = builder.V5_REPORTS[model]
        report = self.fx.read(report_path)
        audit_path = report["outputs"][0]["path"]
        audit = self.fx.read(audit_path)
        item = next(i for i in audit["selected"] if i["block"] == "toolsandbox")
        journal = self.root / item["journal"]
        lines = journal.read_bytes().splitlines(keepends=True)
        row = json.loads(lines[item["row_index"]])
        self.assertEqual(row["index"], 0)
        row["index"] = False
        line = builder.canonical(row) + b"\n"
        lines[item["row_index"]] = line
        journal.write_bytes(b"".join(lines))
        item["row_sha256"] = builder.stable_hash(row)
        item["literal_line_sha256"] = hashlib.sha256(line).hexdigest()
        audit["inputs"] = [builder.bind(self.root, i["path"]) for i in audit["inputs"]]
        self.fx.write(audit_path, audit)
        report["outputs"] = [builder.bind(self.root, audit_path)]
        self.fx.write(report_path, report, anchor=True)
        with self.assertRaisesRegex(ValueError, "condition payload differs"):
            builder.prepare_source_map(self.root)

    def test_wrong_new_scientific_denominator_fails(self):
        self.rewrite_manifest("haiku", lambda m: m["plans"][0].update(conditions=431))
        with self.assertRaisesRegex(ValueError, "block denominator"):
            builder.prepare_source_map(self.root)

    def test_returned_source_map_cannot_mutate_global_denominators(self):
        result = builder.prepare_source_map(self.root)
        result["expected_coverage_counts"]["core_descriptive"] = 999
        result["expected_episode_counts"]["core"] = 999
        self.assertEqual(builder.FINAL_COVERAGE["core_descriptive"], 22)
        self.assertEqual(builder.FINAL_EPISODES["core"], 17424)

    def test_paths_cannot_escape_repository(self):
        for name in ("../foreign.json", "/tmp/foreign.json"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "escapes repository"):
                builder.resolve(self.root, name)
        (self.root / "escape").symlink_to(self.root.parent, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink escapes"):
            builder.resolve(self.root, "escape/foreign.json")

    def test_no_network_imports_or_output_writes_on_preparation(self):
        before = {p.relative_to(self.root): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in self.root.rglob("*") if p.is_file()}
        with patch("socket.socket", side_effect=AssertionError("network forbidden")):
            builder.prepare_source_map(self.root)
        after = {p.relative_to(self.root): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
