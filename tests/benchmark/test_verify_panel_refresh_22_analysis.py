"""Synthetic deterministic-verifier contracts, never actual panel analyses.

The real driver/materializer build a complete 27,912-row toy candidate. Hosted
certificates/costs are replaced solely inside fixtures. Scientific algorithms
and subprocess execution are mocked with deterministic payloads; the verifier's
own orchestration, joins, hashes, comparisons and failure paths run normally.
"""
from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from experiments import verify_panel_refresh_22_analysis as verifier
from tests.benchmark import test_run_panel_refresh_22_analysis as fixture_module

driver = verifier.driver


def cli_payload(script):
    return {"synthetic_only": True, "script": script,
        "statistic": {"estimate": .25, "count": 2, "decision": False},
        "nested": {"path": "scientific-path-label", "timestamp": "fixed-label", "artifact_hash": "nested-meaningful-hash"}}


def api_payload(name):
    value = {"synthetic_only": True, "api": name, "statistic": {"estimate": .5, "count": 3},
             "nested": {"path": "scientific-path-label", "timestamp": "fixed-label", "artifact_hash": "nested-meaningful-hash"}}
    if name == "structural-repair-pooled.json":
        value["plan_hashes"] = ["0" * 64]
    return value


class VerifierIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture_module.DriverTests.setUpClass()
        cls.root = fixture_module.DriverTests.root
        # Real default declarations, parsed but never executed in subprocesses.
        for script, flags in verifier.CLI_PARAMETERS.items():
            path = cls.root / "experiments" / script
            path.write_text("\n".join(f"parser.add_argument({flag!r}, default={value})" for flag, value in flags.items()) + "\n")
        for original in (Path(driver.__file__), Path(verifier.__file__), Path(__file__)):
            relative = ("tests/benchmark/" if original.name.startswith("test_") else "experiments/") + original.name
            target = cls.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(original.read_bytes())

    @classmethod
    def tearDownClass(cls):
        fixture_module.DriverTests.tearDownClass()

    def setUp(self):
        self.fixture = fixture_module.DriverTests("test_complete_positive_prepare_is_write_free_and_rebases_exact_bytes")
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(verifier, "ROOT", self.root))
        self.candidate = self.root / "results" / ("candidate-" + self._testMethodName)
        self.output = self.root / "results" / ("verify-" + self._testMethodName)
        self.subprocess = self.stack.enter_context(patch.object(verifier.subprocess, "run", side_effect=self.fake_cli))
        with patch.object(driver, "_api_analyses", return_value={name: api_payload(name) for name in verifier.API_SOURCES}):
            driver.run(self.root, self.candidate)
        self.subprocess.reset_mock()
        # Only the algorithm calls are replaced during recomputation. The new
        # verifier's _api_recompute and its own CLI specifications execute.
        self.checked_rows = self.stack.enter_context(patch.object(verifier.decisions, "_rows", return_value=[]))
        self.api_calls = {}
        for module, name in ((verifier.decisions, "release-model-versus-system.json"),
                             (verifier.total_cost, "release-panel-cost.json"),
                             (verifier.structural_cost, "structural-model-versus-system.json")):
            self.api_calls[name] = self.stack.enter_context(patch.object(module, "analyze", return_value=api_payload(name)))
        self.api_calls["structural-repair-pooled.json"] = self.stack.enter_context(patch.object(verifier.external,
            "analyze_structural", return_value=api_payload("structural-repair-pooled.json")))
        self.api_calls["candidate-toolsandbox-menu.json"] = self.stack.enter_context(patch.object(verifier.external,
            "analyze_toolsandbox", return_value=api_payload("candidate-toolsandbox-menu.json")))

    def fake_cli(self, command, **kwargs):
        self.assertEqual(kwargs["cwd"], self.root)
        self.assertTrue(kwargs["check"])
        Path(command[-1]).write_bytes(verifier.canonical(cli_payload(Path(command[1]).name)) + b"\n")

    def prepare(self):
        return verifier.prepare_verification(self.root, self.candidate, self.output)

    def verify(self):
        return verifier.verify(self.root, self.candidate, self.output)

    def rewrite(self, name, mutate, *, reseal=True, bind_output=False):
        path = self.candidate / name
        value = json.loads(path.read_bytes()); mutate(value)
        if reseal: value = verifier._hash(value, "manifest_hash" if name == verifier.MANIFEST else "artifact_hash")
        path.write_bytes(verifier.canonical(value) + b"\n")
        if bind_output:
            def update(report):
                entry = next(r for r in report["generated_analyses"] if Path(r["path"]).name == name)
                entry["sha256"] = sha256(path.read_bytes()).hexdigest()
            self.rewrite(verifier.GENERATION, update)

    def test_positive_full_rerun_uses_own_orchestration_and_preserves_candidate(self):
        before = verifier._snapshot(self.root, self.candidate)
        with patch.object(driver, "run", side_effect=AssertionError("must not delegate rerun")), \
             patch.object(driver, "ordinary_commands", side_effect=AssertionError("must use own commands")), \
             patch.object(driver, "_api_analyses", side_effect=AssertionError("must use direct APIs")):
            report = self.verify()
        self.assertTrue(report["passed"])
        self.assertFalse(report["release_admitted"])
        self.assertEqual(len(report["checks"]), 16)
        self.assertEqual(len({r["name"] for r in report["checks"]}), 16)
        self.assertEqual(len(report["preserved_artifact_checks"]), 3)
        self.assertEqual(len(report["prior_model_certificate_revalidation"]), 8)
        self.assertEqual(len(report["scientific_row_cost_revalidation"]), 8)
        self.assertEqual(report["ignored_result_fields"], [])
        self.assertEqual(before, verifier._snapshot(self.root, self.candidate))
        self.assertEqual(set(verifier._snapshot(self.root, self.output)),
                         {verifier.PLAN, verifier.REPORT, *verifier.OUTPUT_NAMES, *verifier.PRESERVED_NAMES})
        self.assertEqual(self.subprocess.call_count, 11)
        for call in self.api_calls.values(): self.assertEqual(call.call_count, 1)
        for name in verifier.PRESERVED_NAMES:
            self.assertEqual((self.output / name).read_bytes(), (self.candidate / name).read_bytes())

    def test_explicit_cli_parameters_and_only_output_path_relocated(self):
        plan = self.prepare()
        self.assertFalse(self.output.exists())
        for command, (script, name) in zip(plan.commands, verifier.CLI_OUTPUTS):
            self.assertEqual(command[-2:], ["--output", str(self.output / name)])
            for flag, value in verifier.CLI_PARAMETERS[script].items():
                self.assertEqual(command[command.index(flag) + 1], str(value))
            for flag in ("--panel", "--manifest", "--external"):
                if flag in command:
                    self.assertTrue(Path(command[command.index(flag) + 1]).is_relative_to(self.candidate))
            self.assertEqual(sum(str(self.output) in item for item in command), 1)
        self.assertEqual(plan.prepared.output_root, self.candidate)
        self.assertEqual(plan.prepared.manifest["panel_path"], self.candidate.relative_to(self.root).as_posix() + "/release-panel.json")

    def test_explicit_api_parameters_populations_costs_and_itt_scope(self):
        self.verify()
        for name in ("release-model-versus-system.json", "structural-model-versus-system.json"):
            call = self.api_calls[name].call_args
            self.assertEqual((call.kwargs["samples"], call.kwargs["seed"]), (10000, 20260816))
            self.assertIs(call.kwargs["row_costs"], self.fixture.lookup)
        self.assertIs(self.api_calls["release-panel-cost.json"].call_args.kwargs["row_costs"], self.fixture.lookup)
        call = self.api_calls["structural-repair-pooled.json"].call_args
        self.assertEqual(len(call.args[1]), 18)
        self.assertEqual({k: call.kwargs[k] for k in ("bootstrap_samples", "randomization_samples", "seed")},
                         verifier.API_PARAMETERS["structural_bootstrap"])
        self.assertEqual(len(self.api_calls["candidate-toolsandbox-menu.json"].call_args.args[1]), 13)
        self.assertEqual([c.kwargs["expected_rows"] for c in self.checked_rows.call_args_list], [12672, 4752, 3024])
        self.assertEqual([len(c.kwargs["allowed_infrastructure"]) for c in self.checked_rows.call_args_list], [8, 1, 0])

    def test_current_deepseek_admission_failure_is_before_mkdir(self):
        driver.materializer.prepare_materialization.side_effect = driver.selection.AdmissionPending("DeepSeek certificate pending")
        with self.assertRaisesRegex(driver.selection.AdmissionPending, "DeepSeek"): self.verify()
        self.assertFalse(self.output.exists())
        self.subprocess.assert_not_called()

    def test_candidate_generation_status_types_and_unknown_fields_fail_closed(self):
        original = (self.candidate / verifier.GENERATION).read_bytes()
        cases = [{"rows": 27912.0}, {"configurations": 21}, {"new_api_calls": False},
                 {"release_admitted": 0}, {"status": "complete"}, {"passed": True},
                 {"verification_scope": "live API reproduction"}]
        for change in cases:
            self.rewrite(verifier.GENERATION, lambda r: r.update(change))
            with self.assertRaises(ValueError): self.prepare()
            self.assertFalse(self.output.exists())
            (self.candidate / verifier.GENERATION).write_bytes(original)

    def test_candidate_self_hash_and_self_referential_output_are_rejected(self):
        original = (self.candidate / verifier.GENERATION).read_bytes()
        self.rewrite(verifier.GENERATION, lambda r: r.update(artifact_hash="0" * 64), reseal=False)
        with self.assertRaisesRegex(ValueError, "self hash"): self.prepare()
        (self.candidate / verifier.GENERATION).write_bytes(original)
        self.rewrite(verifier.GENERATION, lambda r: r["generated_analyses"][0].update(path=self.candidate.relative_to(self.root).as_posix() + "/" + verifier.GENERATION))
        with self.assertRaisesRegex(ValueError, "output coverage"): self.prepare()
        self.assertFalse(self.output.exists())

    def test_extra_missing_and_illegal_output_inventory(self):
        extra = self.candidate / "extra.json"; extra.write_bytes(b"{}\n")
        with self.assertRaisesRegex(ValueError, "extra or missing"): self.prepare()
        extra.unlink()
        path = self.candidate / verifier.OUTPUT_NAMES[0]; raw = path.read_bytes(); path.unlink()
        with self.assertRaisesRegex(ValueError, "extra or missing"): self.prepare()
        path.write_bytes(raw)
        self.rewrite(verifier.GENERATION, lambda r: r["generated_analyses"][0].update(path="../outside.json"))
        with self.assertRaises(ValueError): self.prepare()
        self.assertFalse(self.output.exists())

    def test_candidate_evidence_and_panel_membership_tamper_rejected(self):
        manifest = json.loads((self.candidate / verifier.MANIFEST).read_bytes())
        entry = next(iter(manifest["inputs"].values()))[0]
        path = self.root / entry["path"]; original = path.read_bytes()
        path.write_bytes(original + b" ")
        with self.assertRaisesRegex(ValueError, "verified material"): self.prepare()
        path.write_bytes(original)
        self.rewrite(verifier.PANEL, lambda p: p["configurations"].pop())
        with self.assertRaisesRegex(ValueError, "verified material"): self.prepare()
        self.assertFalse(self.output.exists())

    def test_manifest_path_mapping_does_not_ignore_nonpath_fields_or_hashes(self):
        self.rewrite(verifier.MANIFEST, lambda m: m.update(total_rows=27911))
        with self.assertRaisesRegex(ValueError, "verified material"): self.prepare()
        self.assertFalse(self.output.exists())

    def test_candidate_command_or_parameter_tamper_rejected(self):
        self.rewrite(verifier.GENERATION, lambda r: r["commands"][0].extend(["--seed", "7"]))
        with self.assertRaisesRegex(ValueError, "commands/parameters"): self.prepare()
        self.assertFalse(self.output.exists())

    def test_changed_bound_analysis_code_rejected(self):
        path = self.root / "experiments/analyze_model_system_decisions.py"; original = path.read_bytes()
        try:
            path.write_bytes(original + b" ")
            with self.assertRaisesRegex(ValueError, "binding differs: inputs"): self.prepare()
            self.assertFalse(self.output.exists())
        finally: path.write_bytes(original)

    def test_unreviewed_generator_and_statistical_default_changes_rejected(self):
        path = self.root / verifier.DRIVER_PATH; original = path.read_bytes()
        try:
            path.write_bytes(original + b" ")
            with self.assertRaisesRegex(ValueError, "driver version"): self.prepare()
        finally: path.write_bytes(original)
        path = self.root / "experiments/analyze_unified_core_panel.py"; original = path.read_bytes()
        try:
            path.write_bytes(original.replace(b"20260805", b"20260000"))
            with self.assertRaisesRegex(ValueError, "defaults changed"): self.prepare()
        finally: path.write_bytes(original)
        self.assertFalse(self.output.exists())

    def test_recomputed_numeric_type_difference_cannot_pass_resealed_candidate(self):
        name = "release-model-versus-system.json"
        self.rewrite(name, lambda a: a["statistic"].update(count=3.0), bind_output=True)
        with self.assertRaisesRegex(ValueError, "typed deterministic"): self.verify()
        self.assertFalse((self.output / verifier.REPORT).exists())
        self.assertFalse(json.loads((self.output / verifier.FAILURE).read_bytes())["passed"])

    def test_nested_path_timestamp_and_hash_are_not_comparison_exceptions(self):
        name = "release-model-versus-system.json"
        self.api_calls[name].return_value["nested"]["path"] = "changed-scientific-value"
        self.api_calls[name].return_value["nested"]["timestamp"] = "another-value"
        self.api_calls[name].return_value["nested"]["artifact_hash"] = "different-nested-digest"
        with self.assertRaisesRegex(ValueError, "typed deterministic"): self.verify()
        self.assertFalse((self.output / verifier.REPORT).exists())

    def test_candidate_mutation_during_rerun_prevents_success(self):
        original = self.fake_cli
        def mutate(command, **kwargs):
            original(command, **kwargs)
            path = self.candidate / "historical-toolsandbox-menu.json"
            path.write_bytes(path.read_bytes() + b" ")
        self.subprocess.side_effect = mutate
        with self.assertRaisesRegex(ValueError, "preserved|candidate input/output"): self.verify()
        self.assertFalse((self.output / verifier.REPORT).exists())
        self.assertTrue((self.output / verifier.FAILURE).is_file())

    def test_source_mutation_during_rerun_prevents_success(self):
        path = self.root / "fixture-cross.jsonl"; original = path.read_bytes()
        def mutate(command, **kwargs):
            self.fake_cli(command, **kwargs)
            path.write_bytes(original + b" ")
        self.subprocess.side_effect = mutate
        try:
            with self.assertRaisesRegex(ValueError, "changed bound input"): self.verify()
            self.assertFalse((self.output / verifier.REPORT).exists())
        finally: path.write_bytes(original)

    def test_subprocess_failure_retains_diagnostics_without_success(self):
        self.subprocess.side_effect = RuntimeError("synthetic offline failure")
        with self.assertRaisesRegex(RuntimeError, "synthetic"): self.verify()
        self.assertTrue((self.output / verifier.PLAN).exists())
        self.assertTrue((self.output / verifier.FAILURE).exists())
        self.assertFalse((self.output / verifier.REPORT).exists())

    def test_output_changes_after_value_comparison_cannot_be_blessed_by_new_hash(self):
        original = verifier.compare_artifact
        calls = 0
        def changing_comparison(candidate, recomputed):
            nonlocal calls
            original(candidate, recomputed)
            calls += 1
            if calls == len(verifier.OUTPUT_NAMES):
                path = self.output / verifier.OUTPUT_NAMES[0]
                payload = json.loads(path.read_bytes())
                payload["statistic"]["estimate"] = .75
                path.write_bytes(verifier.canonical(verifier._hash(payload)) + b"\n")
        with patch.object(verifier, "compare_artifact", side_effect=changing_comparison):
            with self.assertRaisesRegex(ValueError, "changed since comparison"): self.verify()
        self.assertFalse((self.output / verifier.REPORT).exists())

    def test_preserved_copy_changes_after_equality_read_cannot_get_new_blessed_hash(self):
        target = self.output / "historical-toolsandbox-menu.json"
        original_read = Path.read_bytes
        reads = 0
        def changing_read(path):
            nonlocal reads
            raw = original_read(path)
            if path == target:
                reads += 1
                # First read inventories the completed output directory; the
                # second supplies the bytes for the preservation equality.
                if reads == 2:
                    path.write_bytes(raw + b" ")
            return raw
        with patch.object(Path, "read_bytes", changing_read):
            with self.assertRaisesRegex(ValueError, "changed since comparison"): self.verify()
        self.assertGreaterEqual(reads, 3)
        self.assertFalse((self.output / verifier.REPORT).exists())

    def test_existing_nested_symlink_and_outside_destinations_rejected(self):
        for path in (self.candidate, self.candidate / "nested", self.root / "results", self.root.parent / "outside"):
            with self.assertRaises(ValueError): verifier.prepare_verification(self.root, self.candidate, path)
        link = self.root / "results" / "linked-candidate"; link.symlink_to(self.candidate, target_is_directory=True)
        try:
            with self.assertRaisesRegex(ValueError, "symlink"): verifier.prepare_verification(self.root, link, self.output)
        finally: link.unlink()
        link = self.candidate / "symlink.json"; link.symlink_to(self.candidate / verifier.PANEL)
        with self.assertRaisesRegex(ValueError, "symlink"): self.prepare()


class TypedComparisonTests(unittest.TestCase):
    def test_nested_numeric_bool_null_and_array_types_are_distinct(self):
        for left, right in ((1, True), (1, 1.0), (0, False), (None, False), ([1], [1.0]), (-0.0, 0.0)):
            a = verifier._hash({"nested": {"value": left}})
            b = verifier._hash({"nested": {"value": right}})
            with self.assertRaisesRegex(ValueError, "typed deterministic"): verifier.compare_artifact(a, b)

    def test_every_nested_special_name_remains_scientific_content(self):
        for key in ("path", "timestamp", "generated_at", "artifact_hash", "sha256"):
            a = verifier._hash({"nested": {key: "left"}})
            b = verifier._hash({"nested": {key: "right"}})
            with self.assertRaisesRegex(ValueError, "typed deterministic"): verifier.compare_artifact(a, b)

    def test_self_hash_is_checked_independently_not_stripped_for_comparison(self):
        a = verifier._hash({"estimate": 1})
        b = deepcopy(a); b["estimate"] = 2
        with self.assertRaisesRegex(ValueError, "self hash"): verifier.compare_artifact(a, b)
        b = deepcopy(a); b["artifact_hash"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "self hash"): verifier.compare_artifact(a, b)

    def test_duplicate_keys_nonfinite_and_nonobjects_rejected(self):
        for raw in (b'{"x":1,"x":1}', b'{"x":NaN}', b'{"x":1e999}', b'[]'):
            with self.assertRaises(ValueError): verifier._json(raw)


if __name__ == "__main__":
    unittest.main()
