"""Hermetic complete22 projection tests; never use live model certificates."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from experiments import panel_refresh_22_materialize as materialize
from experiments import build_panel_refresh_22 as selection
from tests.benchmark.test_panel_refresh_22_builder import Fixture


class MaterializationFixture:
    def __init__(self, root):
        self.root = root
        self.fx = Fixture(root)
        self.records, self.journals = {}, {}
        self.make_retained_identifiers_and_poison_metadata()
        self.make_plans()
        self.make_rows()
        self.make_v5()
        with patch.object(selection, "ANCHORS", self.fx.anchors):
            self.source_map = selection.prepare_source_map(root)
        self.candidates = self.make_candidates()

    def write_json(self, name, value):
        return self.fx.write(name, value)

    def binding(self, name):
        return selection.bind(self.root, name)

    def journal(self, name, rows):
        # Deliberately noncanonical whitespace and CRLF to prove literal copies.
        data = [json.dumps(row, ensure_ascii=False).encode() + b"\r\n" for row in rows]
        path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"".join(data))
        return data

    def make_retained_identifiers_and_poison_metadata(self):
        name = selection.RETAINED + "/release-analysis-inputs.json"
        manifest = self.fx.read(name)
        manifest.update(analysis_policy={"layer_activation_models": ["excluded/model-secret"]},
            archival_provenance={"all_models": ["excluded/model-secret"]},
            source_manifests=["full-history-with-excluded-models.json"])
        for group, field in (("external", "episode_id"), ("candidate_discovery", "sample_id")):
            entry = manifest["inputs"][group][0]
            rows = [json.loads(line) for line in (self.root / entry["path"]).read_bytes().splitlines()]
            for row in rows:
                row[field] = row.pop("condition_id")
            self.journal(entry["path"], rows)
            entry.update(self.binding(entry["path"]), identifier_field=field)
        # A selected historical plan can itself contain an old full-model list.
        # Preserve its binding internally without copying that file publicly.
        entry = manifest["inputs"]["capacity"][0]
        plan_name = "fixture-retained-plans/selected.json"
        plan = {"all_historical_models": ["excluded/model-secret"]}
        self.write_json(plan_name, plan)
        plan_hash = selection.stable_hash(plan)
        rows = [json.loads(line) for line in (self.root / entry["path"]).read_bytes().splitlines()]
        for row in rows:
            row["plan_hash"] = plan_hash
        self.journal(entry["path"], rows)
        entry.update(self.binding(entry["path"]), plan_path=plan_name,
            plan_sha256=self.binding(plan_name)["sha256"], plan_hash=plan_hash)
        self.fx.write(name, manifest, hash_field="manifest_hash", anchor=True)

    def make_plans(self):
        for campaign, manifest_path in selection.MANIFEST_PATHS.items():
            manifest = self.fx.read(manifest_path)
            for entry in manifest["plans"]:
                if entry["block"] not in {"structural", "toolsandbox"}:
                    continue
                plan = self.fx.read(entry["path"])
                plan["confirmatory_weight"] = 0
                plan = self.fx.write(entry["path"], plan, hash_field="plan_hash")
                entry.update(self.binding(entry["path"]), plan_hash=plan["plan_hash"])
                self.fx.plan_objects[(campaign, entry["model"], entry["block"])] = (entry, plan)
            self.fx.write(manifest_path, manifest, hash_field="manifest_hash", anchor=True)

    def make_rows(self):
        for model in sorted(selection.NEW_MODELS):
            campaign = "haiku" if model == selection.HAIKU else "deepinfra" if model == selection.DEEPSEEK else "original"
            manifest = self.fx.read(selection.MANIFEST_PATHS[campaign])
            model_records = []
            for entry in [e for e in manifest["plans"] if e["model"] == model]:
                block = entry["block"]; plan = self.fx.read(entry["path"])
                rows = []
                for condition in plan["conditions"]:
                    row = {"condition_id": selection.condition_id(condition), "plan_hash": entry["plan_hash"], "status": "agent_failure"}
                    if block == "toolsandbox":
                        row.update(condition, schema="candidate-toolsandbox-matched-menu-row-v1", confirmatory_weight=0,
                            duration_seconds=25.868042541995237, score={"similarity": 0})
                    else:
                        row["condition"] = deepcopy(condition)
                        if block == "structural":
                            row.update(model=model, confirmatory_weight=0)
                    rows.append(row)
                if block == "toolsandbox" and model in selection.V5_REPORTS:
                    keep = 68 if model == selection.QWEN else 25
                    original = deepcopy(rows[:69 if model == selection.QWEN else 25])
                    original[-1]["status"] = "failed_closed"
                    overlay = selection.BASE + "/execution/toolsandbox-recovery-v5/" + model.replace("/", "--") + ".jsonl"
                    origin_lines = self.journal(entry["output"], original)
                    overlay_lines = self.journal(overlay, rows[keep:])
                    selected = [(entry["output"], i, line) for i, line in enumerate(origin_lines[:keep])]
                    selected += [(overlay, i, line) for i, line in enumerate(overlay_lines)]
                    self.journals[(model, block)] = [self.binding(name) for name in sorted((entry["output"], overlay))]
                else:
                    lines = self.journal(entry["output"], list(reversed(rows)))
                    selected = [(entry["output"], i, line) for i, line in enumerate(lines)]
                    self.journals[(model, block)] = [self.binding(entry["output"])]
                for name, index, line in selected:
                    row = json.loads(line)
                    record = {"block": block, "condition_id": row["condition_id"], "journal": name, "row_index": index,
                        "literal_line_sha256": sha256(line).hexdigest(), "row_sha256": selection.stable_hash(row)}
                    if model in {selection.HAIKU, selection.QWEN, selection.GEMINI}:
                        record["disposition"] = "original_complete"
                    if block == "toolsandbox" and model in selection.V5_REPORTS:
                        original = name == entry["output"]
                        record["disposition"] = ("model_unknown_tool_zero_score" if model == selection.GEMINI and original and index == 24 else
                            "preserved_prefix_continuation" if model == selection.QWEN and not original and index == 0 else
                            "original_complete" if original else "new_complete")
                    model_records.append(record)
            self.records[model] = model_records

    def make_v5(self):
        for model, report_path in selection.V5_REPORTS.items():
            report = self.fx.read(report_path)
            audit_path = str(Path(report_path).parent / "input-audit.json")
            audit = self.fx.read(audit_path)
            audit["selected"] = self.records[model]
            audit["inputs"] = self.journals[(model, "toolsandbox")]
            self.write_json(audit_path, audit)
            report["outputs"] = [self.binding(audit_path)]
            self.fx.write(report_path, report, anchor=True)

    def make_candidates(self):
        configs = {item["model"]: item for item in self.source_map["configurations"]}
        candidates = {}
        for model in sorted(selection.NEW_MODELS):
            config = configs[model]
            sources = [source for source in self.source_map["sources"] if source["model"] == model]
            report_path = "fixture-certificates/" + model.replace("/", "--") + ".json"
            self.write_json(report_path, {"synthetic": True, "model": model})
            bindings = {b["path"]: b for b in [config["manifest"], config["pricing"], self.binding(report_path)]}
            blocks = []
            for source in sources:
                bindings[source["plan"]["path"]] = source["plan"]
                for b in self.journals[(model, source["block"])]:
                    bindings[b["path"]] = b
                blocks.append({"block": source["block"], "conditions": source["planned_conditions"], "plan": source["plan"],
                    "plan_hash": source["plan_hash"], "row_selection": self.journals[(model, source["block"])]})
            candidates[model] = {"schema": "panel-refresh-22-verified-model-candidate-v1", "model": model,
                "status": "model_certificate_verified_for_candidate_selection", "release_admitted": False,
                "conditions": 1336, "new_api_calls": 0, "configuration_sha256": config["configuration_sha256"],
                "configuration_id": config.get("configuration_id"), "manifest": config["manifest"],
                "reports": [self.binding(report_path)], "source_blocks": blocks,
                "verified_sources": list(bindings.values()), "selected_rows": self.records[model]}
        return candidates


class MaterializationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory(); cls.root = Path(cls.temp.name)
        cls.fx = MaterializationFixture(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.anchors = patch.object(selection, "ANCHORS", self.fx.fx.anchors)
        self.anchors.start(); self.addCleanup(self.anchors.stop)

    def assemble(self, source_map=None, candidates=None):
        return materialize._assemble(self.root, source_map or self.fx.source_map,
            self.fx.candidates if candidates is None else candidates)

    def test_complete_fixture_exact_rows_groups_and_no_disk_output(self):
        before = {str(path.relative_to(self.root)) for path in self.root.rglob("*")}
        value = self.assemble(); manifest = value.manifest
        self.assertEqual((manifest["total_rows"], manifest["retained_rows"], manifest["new_rows"]), (27912, 17224, 10688))
        self.assertEqual(manifest["path_base"], "materialization_root")
        self.assertEqual(manifest["expected_rows"], self.fx.source_map["expected_group_rows"])
        self.assertEqual(len(manifest["models"]), 22)
        self.assertFalse(manifest["release_admitted"])
        self.assertEqual(json.loads(value.files[materialize.MANIFEST_PATH]), manifest)
        self.assertEqual(before, {str(path.relative_to(self.root)) for path in self.root.rglob("*")})
        self.assertFalse((self.root / "evidence").exists())
        self.assertNotIn("panel_sha256", manifest)
        self.assertNotIn("analysis_policy", manifest)

    def test_retained_bytes_identifier_fields_and_entry_metadata_preserved(self):
        value = self.assemble()
        for projection in value.provenance["retained_projections"]:
            original = projection["source_entry"]
            self.assertEqual(value.files[projection["destination"]["path"]], (self.root / original["path"]).read_bytes())
        inputs = value.manifest["inputs"]
        self.assertEqual(inputs["external"][0]["identifier_field"], "episode_id")
        self.assertEqual(inputs["candidate_discovery"][0]["identifier_field"], "sample_id")
        self.assertNotIn("identifier_field", inputs["capacity"][0])
        self.assertIn("plan_hash", inputs["capacity"][0])
        self.assertNotIn("plan_path", inputs["capacity"][0])
        self.assertIn("episode_id", json.loads(value.files[inputs["external"][0]["path"]].splitlines()[0]))

    def test_new_lines_match_certificate_exactly_and_frozen_plan_order(self):
        value = self.assemble()
        for projection in value.provenance["new_projections"]:
            evidence = projection["projection"]
            expected = []
            for record in evidence["selected_rows"]:
                line = (self.root / record["journal"]).read_bytes().splitlines(keepends=True)[record["row_index"]]
                expected.append(line)
            raw = value.files[projection["destination"]["path"]]
            self.assertEqual(raw, b"".join(expected))
            self.assertTrue(raw.endswith(b"\r\n"))
            self.assertEqual(json.loads(raw.splitlines()[0]).get("condition", json.loads(raw.splitlines()[0]))["index"], 0)
            self.assertFalse(evidence["source_row_bytes_modified"])

    def test_overlay_zero_and_segment_timing_preserved_not_rewritten(self):
        value = self.assemble()
        projections = {p["projection"]["model"]: p for p in value.provenance["new_projections"] if p["projection"]["block"] == "toolsandbox"}
        qwen = projections[selection.QWEN]; gemini = projections[selection.GEMINI]
        self.assertEqual(qwen["projection"]["timing_policy"], "segmented_not_uninterrupted_latency")
        self.assertEqual(qwen["projection"]["selected_rows"][68]["duration_scope"], "continuation_segment_only")
        qrow = json.loads(value.files[qwen["destination"]["path"]].splitlines()[68])
        self.assertEqual(qrow["duration_seconds"], 25.868042541995237)
        grow = json.loads(value.files[gemini["destination"]["path"]].splitlines()[24])
        self.assertEqual((grow["status"], grow["score"]), ("failed_closed", {"similarity": 0}))
        self.assertEqual(gemini["external_plan_metadata"]["plan_frozen_at"], None)

    def test_no_historical_metadata_or_original_plan_paths_in_public_files(self):
        value = self.assemble()
        joined = b"".join(value.files.values())
        for forbidden in (b"excluded/model-secret", b"archival_provenance", b"source_manifests", b"plan_path", b"source_row_index"):
            self.assertNotIn(forbidden, joined)
        self.assertEqual(set(value.files), {materialize.MANIFEST_PATH} | {
            entry["path"] for entries in value.manifest["inputs"].values() for entry in entries})
        self.assertEqual(value.provenance["visibility"], "internal_only_not_public_package_content")

    def test_public_entry_internally_verifies_all_eight_before_projecting(self):
        called = []
        def verify(root, model):
            self.assertEqual(root, self.root.resolve()); called.append(model)
            return deepcopy(self.fx.candidates[model])
        with patch.object(selection, "prepare_source_map", return_value=deepcopy(self.fx.source_map)), patch.object(
                materialize.certificates, "verify_model_certificate", side_effect=verify), patch.object(
                materialize, "_assemble", return_value="only-after-all-certificates") as assemble:
            self.assertEqual(materialize.prepare_materialization(self.root), "only-after-all-certificates")
        self.assertEqual((len(called), len(set(called))), (8, 8))
        self.assertEqual(set(called), selection.NEW_MODELS)
        self.assertEqual(set(assemble.call_args.args[2]), selection.NEW_MODELS)

    def test_pending_fresh_certificate_returns_no_projection_or_files(self):
        for blocked in (selection.HAIKU, selection.DEEPSEEK):
            def verify(root, model):
                if model == blocked:
                    raise selection.AdmissionPending("still incomplete")
                return deepcopy(self.fx.candidates[model])
            before = {str(path) for path in self.root.rglob("*")}
            with patch.object(selection, "prepare_source_map", return_value=self.fx.source_map), patch.object(
                    materialize.certificates, "verify_model_certificate", side_effect=verify), patch.object(materialize, "_assemble") as assemble:
                with self.assertRaisesRegex(selection.AdmissionPending, "No materialization produced"):
                    materialize.prepare_materialization(self.root)
                assemble.assert_not_called()
            self.assertEqual(before, {str(path) for path in self.root.rglob("*")})
        with self.assertRaises(TypeError): materialize.prepare_materialization(self.root, passed=True)
        with self.assertRaises(TypeError): materialize.prepare_materialization(self.root, candidates=self.fx.candidates)

    def test_missing_foreign_partial_or_self_admitted_candidates_rejected(self):
        for mutate in (lambda cs: cs.pop(selection.HAIKU), lambda cs: cs.update({"foreign/model": cs[selection.HAIKU]}),
                       lambda cs: cs[selection.HAIKU].update(conditions=1335),
                       lambda cs: cs[selection.HAIKU].update(release_admitted=True),
                       lambda cs: cs[selection.HAIKU].update(configuration_sha256="0" * 64),
                       lambda cs: cs[selection.HAIKU]["selected_rows"].pop(),
                       lambda cs: cs[selection.HAIKU]["source_blocks"].pop()):
            candidates = deepcopy(self.fx.candidates); mutate(candidates)
            with self.assertRaises(ValueError): self.assemble(candidates=candidates)

    def test_selected_row_certificate_hash_index_and_disposition_cannot_change(self):
        for key, value in (("row_sha256", "0" * 64), ("literal_line_sha256", "0" * 64),
                           ("row_index", True), ("journal", "foreign.jsonl"), ("disposition", "new_complete")):
            candidates = deepcopy(self.fx.candidates)
            record = next(r for r in candidates[selection.QWEN]["selected_rows"] if r["block"] == "toolsandbox")
            record[key] = value
            with self.assertRaisesRegex(ValueError, "literal row join|disposition"):
                self.assemble(candidates=candidates)

    def test_whole_source_auditor_label_is_not_an_overlay_authorization(self):
        source = {"row_selection": {"mode": "whole_frozen_output"}}
        for record in ({}, {"disposition": None}, {"disposition": "original_complete"}):
            materialize._join_disposition(source, record, {"disposition": None})
        for value in ("new_complete", "preserved_prefix_continuation", "model_unknown_tool_zero_score", True, 0):
            with self.assertRaisesRegex(ValueError, "disposition"):
                materialize._join_disposition(source, {"disposition": value}, {"disposition": None})
        with self.assertRaisesRegex(ValueError, "disposition"):
            materialize._join_disposition(source, {"disposition": "original_complete"}, {"disposition": "original_complete"})
        overlay = {"row_selection": {"mode": "literal_v5_selection"}}
        with self.assertRaisesRegex(ValueError, "disposition"):
            materialize._join_disposition(overlay, {"disposition": "original_complete"}, {"disposition": None})

    def test_configuration_group_execution_and_scope_mutations_rejected(self):
        changes = [lambda s: s["sources"][0].update(configuration_key="0" * 64),
            lambda s: s["sources"][0].update(ledger_group="external"),
            lambda s: s["sources"][0].update(execution_sha256="0" * 64),
            lambda s: s["sources"].pop(), lambda s: s.update(expected_total_rows=27911),
            lambda s: s.update(new_models=list(s["new_models"][:-1])),
            lambda s: s["configurations"][0].update(configuration_key="0" * 64),
            lambda s: s["configurations"][0]["route"].update(allow_fallbacks=True),
            lambda s: s["configurations"][0].update(display_name="foreign-name")]
        for change in changes:
            source_map = deepcopy(self.fx.source_map); change(source_map)
            source_map.pop("artifact_hash"); source_map["artifact_hash"] = selection.stable_hash(source_map)
            with self.assertRaises(ValueError): self.assemble(source_map=source_map)

    def test_changed_bound_journal_and_cross_model_condition_identity_rejected(self):
        candidates = deepcopy(self.fx.candidates)
        source = next(s for s in self.fx.source_map["sources"] if s["model"] == selection.HAIKU and s["block"] == "primary_capacity")
        path = self.root / source["row_selection"]["path"]; original = path.read_bytes()
        path.write_bytes(original + b" ")
        try:
            with self.assertRaisesRegex(ValueError, "source bytes changed"): self.assemble(candidates=candidates)
        finally: path.write_bytes(original)
        row = {"model": "selected", "condition_id": "same"}; seen = set()
        materialize._identity(row, "condition_id", "selected", seen)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            materialize._identity({"model": "other", "condition_id": "same"}, "condition_id", "other", seen)

    def test_projection_deterministic_and_returned_metadata_immutable(self):
        one = self.assemble(); two = self.assemble()
        self.assertEqual(dict(one.files), dict(two.files))
        self.assertEqual(one.provenance["artifact_hash"], two.provenance["artifact_hash"])
        changed = one.manifest; changed["models"].clear()
        changed = one.provenance; changed["inputs"].clear()
        self.assertEqual(len(one.manifest["models"]), 22)
        self.assertTrue(one.provenance["inputs"])
        with self.assertRaises(TypeError): one.files["escape"] = b"write"

    def test_destination_paths_never_escape_or_alias_sources(self):
        for path in ("/evidence/x", "../evidence/x", "evidence/../x", "raw.jsonl"):
            with self.assertRaises(ValueError): materialize._put({}, path, b"x")
        files = {}; materialize._put(files, "evidence/a.jsonl", b"x")
        with self.assertRaises(ValueError): materialize._put(files, "evidence/a.jsonl", b"x")


if __name__ == "__main__":
    unittest.main()
