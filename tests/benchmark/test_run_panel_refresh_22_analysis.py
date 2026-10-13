"""Hermetic driver integration; no live certificates, analysis or hosted calls."""
from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from experiments import run_panel_refresh_22_analysis as driver
from tests.benchmark.test_panel_refresh_22_materialize import MaterializationFixture

s = driver.selection


class ToyCosts:
    """Synthetic canonical row bindings, never a real admission certificate."""
    def __init__(self, material, candidates):
        self.models = frozenset(s.NEW_MODELS)
        self.index, self._artifacts = {}, []
        for group in material.manifest["inputs"].values():
            for entry in group:
                if entry["model"] in self.models:
                    for line in material.files[entry["path"]].splitlines():
                        row = json.loads(line)
                        self.index[(entry["model"], row["condition_id"])] = s.stable_hash(row)
        configs = {c["model"]: c for c in material.provenance["configurations"]}
        for model, c in sorted(candidates.items()):
            self._artifacts.append(driver.hashed({"model": model, "conditions": 1336,
                "model_certificate_sha256": s.stable_hash(c), "reports": c["reports"], "manifest": c["manifest"],
                "configuration_key": configs[model]["configuration_key"], "release_admitted": False,
                "billed_spending_or_budget_hold_accounting": False, "inputs": c["verified_sources"]}))

    @property
    def artifacts(self):
        return deepcopy(self._artifacts)

    def cost_for_row(self, row):
        key = (driver.materializer._model(row), row["condition_id"])
        if key not in self.index or self.index[key] != s.stable_hash(row):
            raise ValueError("missing or changed cost row")
        return 0.0


def synthetic_metadata(material):
    m, p = material.manifest, material.provenance
    old = sorted(set(m["models"]) - s.NEW_MODELS)
    hist_capacity = m["inputs"]["capacity"][0]["model"]
    hist_discovery = m["inputs"]["discovery"][0]["model"]
    panel = {"configurations": [{"model": model, "organization": "fixture"} for model in m["models"]],
        "activation_models": sorted({hist_capacity, hist_discovery}),
        "auxiliary_models": sorted(set(m["models"]) - {old[-1]}),
        "structural_registered_models": old[:2], "structural_additional_models": sorted(old[2:10] + list(s.NEW_MODELS)),
        "external_rank_models": old[:5], "concurrent_menu_models": sorted(old[:5] + list(s.NEW_MODELS)),
        "coverage_counts": deepcopy(s.FINAL_COVERAGE), "episode_counts": deepcopy(s.FINAL_EPISODES),
        "reporting_scope": {}, "release_admitted": False}
    return driver.hashed({"schema": "panel-refresh-22-selected-metadata-candidate-v1", "release_admitted": False,
        "panel": driver.hashed(panel), "analysis_policy": {"layer_activation_models": panel["activation_models"]},
        "source_selection_sha256": p["source_selection_sha256"], "expected_group_rows": m["expected_rows"],
        "planned_ledger_group_models": {g: sorted({e["model"] for e in es}) for g, es in m["inputs"].items()},
        "inputs": p["inputs"]})


class DriverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory()
        cls.root = Path(cls.temp.name).resolve()
        cls.fx = MaterializationFixture(cls.root)
        path = s.RETAINED + "/release-analysis-inputs.json"
        retained = cls.fx.fx.read(path)
        capacity = [json.loads(line)["condition_id"] for line in
                    (cls.root / retained["inputs"]["capacity"][0]["path"]).read_bytes().splitlines()[:8]]
        discovery = [json.loads((cls.root / retained["inputs"]["discovery"][0]["path"]).read_bytes().splitlines()[0])["condition_id"]]
        retained["infrastructure_condition_ids"] = {"capacity": capacity, "discovery": discovery,
            "confirmatory_layer_capacity": [], "confirmatory_layer_discovery": [], "repair": [],
            "retained_layer_capacity": capacity, "retained_layer_discovery": discovery}
        cls.retained = cls.fx.fx.write(path, retained, hash_field="manifest_hash", anchor=True)
        with patch.object(s, "ANCHORS", cls.fx.fx.anchors):
            cls.fx.source_map = s.prepare_source_map(cls.root)
            cls.material = driver.materializer._assemble(cls.root, cls.fx.source_map, cls.fx.candidates)
        cls.meta = synthetic_metadata(cls.material)
        cls.price_bindings = []
        old = sorted(set(cls.material.manifest["models"]) - s.NEW_MODELS)
        prices = [{"selected": [{"requested_model": m, "prompt_price": .000001, "completion_price": .000002} for m in old]},
                  {"models": {}}, {"models": {}}, {"models": {}}, {"models": {}}]
        for (name, _), value in zip(driver.PRICE_BINDINGS, prices):
            cls.fx.write_json(name, value)
            cls.price_bindings.append((name, cls.fx.binding(name)["sha256"]))
        for name in [*driver.PROTOCOLS, "fixture-cross.jsonl", "experiments/analyze_model_system_decisions.py",
                     "experiments/analyze_final_panel_cost.py", "experiments/analyze_structural_model_system_decisions.py",
                     "experiments/panel_refresh_22_external.py"]:
            cls.fx.write_json(name, {"fixture": True})
        cls.preserved_files = {name: driver.encoded({"synthetic": True, "reporting_panel_sha256": "old14"}) + b"\n"
                               for name in driver.PRESERVED}
        cls.preserved_scopes = [{"name": n, "operation": "byte_preserved_not_recomputed",
                                "new_22_panel_binding": False} for n in cls.preserved_files]

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.lookup = ToyCosts(self.material, self.fx.candidates)
        self.output = self.root / "results" / self._testMethodName
        for target, name, value in (
            (driver, "ROOT", self.root), (driver, "PRICE_BINDINGS", self.price_bindings),
            (driver.materializer, "prepare_materialization", Mock(return_value=self.material)),
            (driver.metadata, "build_candidate_metadata", Mock(return_value=deepcopy(self.meta))),
            (driver.costing, "RowCostLookup", Mock(return_value=self.lookup)),
            (driver, "_historical", Mock(return_value=(self.preserved_files, self.preserved_scopes))),
            (driver, "_external_provenance", Mock(return_value={"structural": [], "toolsandbox": [], "duration": []})),
            (driver, "_crossover_paths", Mock(return_value=["fixture-cross.jsonl"])),
        ):
            self.stack.enter_context(patch.object(target, name, value))

    def prepare(self):
        return driver.prepare_analysis(self.root, self.output)

    def test_complete_positive_prepare_is_write_free_and_rebases_exact_bytes(self):
        before = {p.relative_to(self.root).as_posix() for p in self.root.rglob("*")}
        p = self.prepare()
        self.assertFalse(self.output.exists())
        self.assertEqual(before, {p.relative_to(self.root).as_posix() for p in self.root.rglob("*")})
        self.assertEqual(p.manifest["total_rows"], 27912)
        self.assertEqual(p.manifest["path_base"], "repository_root")
        self.assertEqual(len(p.row_costs.models), 8)
        self.assertEqual(p.manifest["infrastructure_condition_ids"], self.retained["infrastructure_condition_ids"])
        self.assertEqual(p.manifest["panel_sha256"], sha256(p.files["release-panel.json"]).hexdigest())
        body = deepcopy(p.manifest); digest = body.pop("manifest_hash")
        self.assertEqual(digest, sha256(driver.encoded(body)).hexdigest())
        self.assertFalse(p.panel["release_admitted"])
        for entries in p.manifest["inputs"].values():
            for e in entries:
                relative = Path(e["path"]).relative_to(self.output.relative_to(self.root)).as_posix()
                self.assertEqual(p.files[relative], self.material.files[relative])
        self.assertNotIn("source_manifests", p.manifest)
        self.assertNotIn("plan_sources", p.manifest)

    def test_public_gate_precedes_metadata_costs_and_directory_creation(self):
        driver.materializer.prepare_materialization.side_effect = s.AdmissionPending("fresh incomplete")
        with self.assertRaises(s.AdmissionPending): self.prepare()
        driver.metadata.build_candidate_metadata.assert_not_called()
        driver.costing.RowCostLookup.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_cost_construction_failure_never_creates_directory(self):
        driver.costing.RowCostLookup.side_effect = ValueError("incomplete certificate")
        with self.assertRaisesRegex(ValueError, "incomplete certificate"): self.prepare()
        self.assertFalse(self.output.exists())

    def test_mismatched_metadata_certificate_configuration_cost_map_fail_before_write(self):
        cases = [("source_selection_sha256", "0" * 64), ("expected_group_rows", {})]
        for key, value in cases:
            changed = deepcopy(self.meta); changed[key] = value
            driver.metadata.build_candidate_metadata.return_value = driver.hashed(changed)
            with self.assertRaises(ValueError): self.prepare()
        driver.metadata.build_candidate_metadata.return_value = deepcopy(self.meta)
        for key in ("model_certificate_sha256", "configuration_key"):
            original = deepcopy(self.lookup._artifacts)
            self.lookup._artifacts[0][key] = "0" * 64
            self.lookup._artifacts[0] = driver.hashed(self.lookup._artifacts[0])
            with self.assertRaises(ValueError): self.prepare()
            self.lookup._artifacts = original
        self.assertFalse(self.output.exists())

    def test_missing_and_tampered_canonical_row_cost_have_no_fallback(self):
        key = next(iter(self.lookup.index)); old = self.lookup.index.pop(key)
        with self.assertRaisesRegex(ValueError, "cost row"): self.prepare()
        self.lookup.index[key] = "0" * 64
        with self.assertRaisesRegex(ValueError, "cost row"): self.prepare()
        self.lookup.index[key] = old
        self.assertFalse(self.output.exists())

    def test_paths_reject_existing_outside_symlink_and_missing_parent(self):
        targets = [self.root, self.root / "results", self.root / "not-results" / "fresh",
                   self.root / "results" / "missing-parent" / "fresh"]
        for target in targets:
            with self.assertRaises(ValueError): driver._destination(self.root, target)
        link = self.root / "results" / "fixture-link"
        link.symlink_to(self.root)
        try:
            with self.assertRaises(ValueError): driver._destination(self.root, link)
        finally: link.unlink()

    def test_ordinary_cli_inventory_scope_infrastructure_and_default_statistics(self):
        p = self.prepare(); commands = driver.ordinary_commands(p)
        self.assertEqual(len(commands), 11)
        by_script = {Path(c[1]).name: c for c in commands}
        self.assertNotIn("analyze_structural_repair_holdout.py", by_script)
        self.assertNotIn("analyze_final_panel_cost.py", by_script)
        self.assertNotIn("analyze_candidate_toolsandbox_release.py", by_script)
        for name in ("analyze_crossover_campaign.py", "analyze_revision_audit.py"):
            command = by_script[name]
            self.assertEqual(command[command.index("--panel") + 1], str(self.output / "release-panel.json"))
        command = by_script["analyze_capacity_sensitivity.py"]
        self.assertEqual(command[command.index("--external") + 1], str(self.output / "toolsandbox-external-validation.json"))
        core = by_script["analyze_unified_core_panel.py"]
        self.assertEqual(core.count("--expected-capacity-infrastructure-id"), 8)
        self.assertEqual(core.count("--expected-discovery-infrastructure-id"), 1)
        for command in commands:
            self.assertNotIn("--seed", command)
            self.assertNotIn("--bootstrap-samples", command)
            self.assertEqual(Path(command[-1]).parent, self.output)

    def test_legacy_five_snapshot_price_policy_and_conflict_gate(self):
        p = self.prepare()
        result = driver._legacy_prices(p.panel, p.prices, p.row_costs)
        self.assertEqual(set(result), set(p.manifest["models"]) - s.NEW_MODELS)
        self.assertTrue(all(value == (.000001, .000002) for value in result.values()))
        model = sorted(result)[0]; prices = deepcopy(list(p.prices))
        prices[2]["models"][model] = {"input": 3, "output": 5}
        result = driver._legacy_prices(p.panel, prices, p.row_costs)
        self.assertEqual(result[model], (.000003, .000005))
        prices[3]["models"][model] = {"input": 6, "output": 5}
        with self.assertRaisesRegex(ValueError, "conflicting"): driver._legacy_prices(p.panel, prices, p.row_costs)

    def test_api_calls_use_lookup_defaults_and_external_populations(self):
        p = self.prepare()
        with ExitStack() as stack:
            checked = stack.enter_context(patch.object(driver.decisions, "_rows", return_value=[]))
            decisions = stack.enter_context(patch.object(driver.decisions, "analyze", return_value={}))
            total = stack.enter_context(patch.object(driver.total_cost, "analyze", return_value={}))
            structural = stack.enter_context(patch.object(driver.structural_cost, "analyze", return_value={}))
            external = stack.enter_context(patch.object(driver.external, "analyze_structural", return_value={"plan_hashes": []}))
            menu = stack.enter_context(patch.object(driver.external, "analyze_toolsandbox", return_value={}))
            result = driver._api_analyses(p)
        self.assertEqual(len(result), 5)
        for call in (decisions, total, structural):
            self.assertIs(call.call_args.kwargs["row_costs"], p.row_costs)
        for call in (decisions, structural):
            self.assertEqual((call.call_args.kwargs["samples"], call.call_args.kwargs["seed"]), (10000, 20260816))
        self.assertEqual([c.kwargs["expected_rows"] for c in checked.call_args_list], [22 * 576, 22 * 216, 21 * 144])
        self.assertEqual(len(external.call_args.args[1]), 18)
        self.assertEqual(len(menu.call_args.args[1]), 13)
        self.assertIs(menu.call_args.kwargs["row_provenance"], p.external_provenance["duration"])

    def test_generation_orchestration_keeps_preserved_bytes_and_no_admission_claim(self):
        names = ("release-model-versus-system.json", "release-panel-cost.json", "structural-model-versus-system.json",
                 "structural-repair-pooled.json", "candidate-toolsandbox-menu.json")
        def fake_command(command, **kwargs):
            self.assertTrue(kwargs["check"])
            self.assertEqual(kwargs["cwd"], self.root)
            Path(command[-1]).write_bytes(b'{"synthetic_statistic":0.25}\n')
        with patch.object(driver.subprocess, "run", side_effect=fake_command), \
             patch.object(driver, "_api_analyses", return_value={n: {"synthetic_statistic": .5} for n in names}):
            report = driver.run(self.root, self.output)
        self.assertFalse(report["release_admitted"])
        self.assertNotIn("passed", report)
        self.assertEqual(len(report["generated_analyses"]), 16)
        self.assertIn("pending_independent", report["status"])
        for name, raw in self.preserved_files.items():
            self.assertEqual((self.output / name).read_bytes(), raw)
        for record in report["generated_analyses"]:
            payload = json.loads((self.root / record["path"]).read_bytes())
            self.assertFalse(payload["release_admitted"])
        with self.assertRaisesRegex(ValueError, "fresh"): driver.run(self.root, self.output)

    def test_failed_subprocess_leaves_only_incomplete_fresh_directory(self):
        def broken(command, **kwargs):
            raise RuntimeError("offline analysis failed")
        with patch.object(driver.subprocess, "run", side_effect=broken):
            with self.assertRaisesRegex(RuntimeError, "failed"): driver.run(self.root, self.output)
        self.assertTrue(self.output.exists())
        self.assertFalse((self.output / driver.SUMMARY).exists())

    def test_input_changes_during_preflight_cannot_succeed(self):
        path = self.root / "experiments/analyze_final_panel_cost.py"
        original = path.read_bytes()
        def changing_costs(*args):
            path.write_bytes(original + b" ")
            return self.lookup
        driver.costing.RowCostLookup.side_effect = changing_costs
        try:
            with self.assertRaisesRegex(ValueError, "changed bound input"): self.prepare()
            self.assertFalse(self.output.exists())
        finally:
            path.write_bytes(original)
            driver.costing.RowCostLookup.side_effect = None

    def test_input_change_after_analysis_prevents_generation_success_record(self):
        names = ("release-model-versus-system.json", "release-panel-cost.json", "structural-model-versus-system.json",
                 "structural-repair-pooled.json", "candidate-toolsandbox-menu.json")
        path = self.root / "fixture-cross.jsonl"
        original = path.read_bytes()
        def fake_command(command, **kwargs):
            Path(command[-1]).write_bytes(b'{"synthetic":true}\n')
        def change_input(prepared):
            path.write_bytes(original + b" ")
            return {name: {"synthetic": True} for name in names}
        try:
            with patch.object(driver.subprocess, "run", side_effect=fake_command), \
                 patch.object(driver, "_api_analyses", side_effect=change_input):
                with self.assertRaisesRegex(ValueError, "changed bound input"):
                    driver.run(self.root, self.output)
            self.assertFalse((self.output / driver.SUMMARY).exists())
        finally: path.write_bytes(original)

    def test_changed_historical_price_bytes_reject_before_any_output(self):
        path = self.root / self.price_bindings[0][0]
        original = path.read_bytes()
        try:
            path.write_bytes(original + b" ")
            with self.assertRaisesRegex(ValueError, "changed bound input"): self.prepare()
            self.assertFalse(self.output.exists())
        finally: path.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
