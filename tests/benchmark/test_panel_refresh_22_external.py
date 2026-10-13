"""Synthetic, offline parity/qualification tests; never open acquisition evidence."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from experiments import panel_refresh_22_external as adapter


def provenance(models):
    return [{"model": model, "plan_hash": adapter.stable_hash(model),
             "plan_frozen_at": None, "manifest_prepared_at_utc": "2026-10-08T00:00:00Z",
             "core_outcome_selection": None, "literal_fraction": .1234567890123456}
            for model in sorted(models)]


def tool_rows(models):
    rows = []
    for mi, model in enumerate(sorted(models)):
        for si in range(32):
            scenario = adapter.CONTINUATION_SCENARIO if si == 0 else f"scenario-{si}"
            for ai, arm in enumerate(adapter.ARMS):
                identity = adapter.stable_hash((model, scenario, arm))
                if model == adapter.QWEN and si == 0 and arm == "all_tools":
                    identity = adapter.CONTINUATION_ID
                score = ((si + ai + mi) % 5) / 4
                rows.append({"condition_id": identity, "model": model,
                    "plan_hash": adapter.stable_hash(model), "base_scenario_id": scenario,
                    "menu_arm": arm, "stratum": f"stratum-{si % 4}", "status": "complete",
                    "score": {"similarity": score, "milestone_similarity": score, "minefield_similarity": 0.0},
                    "usage": {"total_tokens": 10 + si + ai, "requests": 1 + ai},
                    "duration_seconds": .125 * (si + ai + 1)})
    return rows


def structural_rows(models):
    rows = []
    for model in sorted(models):
        for family in range(24):
            for ci, cell in enumerate(adapter.structural.CELLS):
                calls, representation, orchestration, delivery = cell
                condition = {"model": model, "family_id": f"family-{family}",
                    "intent": f"intent-{family}", "stratum": f"stratum-{family % 4}",
                    "certified_calls": calls, "representation": representation,
                    "orchestration": orchestration, "delivery": delivery}
                rows.append({"condition": condition, "condition_id": adapter.stable_hash(condition),
                    "plan_hash": adapter.stable_hash(model), "status": "success",
                    "verdict": {"verified_success": (family + ci) % 3 == 0},
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
                    "efficiency": {"wall_time_seconds": 1.25, "tool_schema_bytes": 5,
                        "tool_schema_exposures": 2, "model_requests_attempted": 1}, "trace": []})
    return rows


class ExternalRowsTests(unittest.TestCase):
    def setUp(self):
        # Same original statistical functions in both paths; reduce only draw
        # counts in this isolated test process to make parity tests inexpensive.
        for name, value in (("BOOTSTRAP_SAMPLES", 25), ("SIGN_FLIP_SAMPLES", 50)):
            context = patch.object(adapter.concurrent.menu, name, value)
            context.start()
            self.addCleanup(context.stop)
        self.models = {"toy-a", "toy-b"}

    def tools(self, rows=None, models=None, **kwargs):
        models = self.models if models is None else models
        return adapter.analyze_toolsandbox(tool_rows(models) if rows is None else rows,
            models, plan_provenance=provenance(models), **kwargs)

    def structural(self, rows=None, models=None, **kwargs):
        models = self.models if models is None else models
        return adapter.analyze_structural(structural_rows(models) if rows is None else rows,
            models, plan_provenance=provenance(models), bootstrap_samples=25,
            randomization_samples=50, **kwargs)

    def test_balanced_toolsandbox_matches_all_old_metrics(self):
        rows = tool_rows(self.models)
        before = deepcopy(rows)
        old = adapter.concurrent._analyze_rows(rows, self.models,
            {r["base_scenario_id"]: r["stratum"] for r in rows},
            [p["plan_hash"] for p in provenance(self.models)])
        result = self.tools(rows)
        for arm in adapter.ARMS[1:]:
            for metric, values in old["contrasts"][arm].items():
                observed = result["contrasts"][arm][metric]
                self.assertEqual({key: observed[key] for key in values}, values)
                self.assertEqual(observed["eligible_pairs"], 64)
                self.assertEqual(observed["scenario_clusters"], 32)
        self.assertEqual(result["all_tools_minus_ten_distractors_similarity"],
                         old["all_tools_minus_ten_distractors_similarity"])
        self.assertEqual(rows, before)
        self.assertIs(result["release_admitted"], False)

    def test_structural_matches_original_layer_and_resource_formulas(self):
        rows = structural_rows(self.models)
        before = deepcopy(rows)
        layers = {layer: adapter.structural._summarize_layer(values, layer=layer,
            bootstrap_samples=25, randomization_samples=50, seed=20260814)
            for layer, values in adapter.structural._extract(rows).items()}
        for layer, p in adapter.structural.holm_adjust(
                {k: v["activation_test"]["raw_p"] for k, v in layers.items()}).items():
            test = layers[layer]["activation_test"]
            test["holm_p"] = p
            test["activated_at_0.05"] = test["all_point_directions_positive"] is True and p < .05
        result = self.structural(rows)
        self.assertEqual(result["layers"], layers)
        self.assertEqual(result["resource_profile_by_cell"], adapter.structural._resource_profile(rows))
        self.assertEqual(rows, before)
        self.assertIn("separate", result["registered_analysis"])

    def test_missing_duplicate_foreign_rows_fail_closed(self):
        for factory, analyze in ((tool_rows, self.tools), (structural_rows, self.structural)):
            for mutation in ("missing", "duplicate", "foreign"):
                with self.subTest(factory=factory.__name__, mutation=mutation):
                    rows = factory(self.models)
                    if mutation == "missing":
                        rows.pop()
                    elif mutation == "duplicate":
                        rows[-1] = deepcopy(rows[0])
                    else:
                        rows[0]["condition_id"] = "foreign"
                        target = rows[0].get("condition", rows[0])
                        target["model"] = "foreign-model"
                    with self.assertRaises(ValueError):
                        analyze(rows)

    def test_duplicate_cell_even_with_unique_id_rejected(self):
        rows = tool_rows(self.models)
        rows[-1]["menu_arm"] = rows[-2]["menu_arm"]
        with self.assertRaisesRegex(ValueError, "duplicate matched"):
            self.tools(rows)

    def test_cross_model_scenario_stratum_drift_rejected(self):
        rows = tool_rows(self.models)
        rows[0]["stratum"] = "other"
        with self.assertRaisesRegex(ValueError, "stratum differs"):
            self.tools(rows)

    def test_cross_model_structural_metadata_drift_rejected(self):
        rows = structural_rows(self.models)
        for row in rows[:9]:
            row["condition"]["intent"] = "different-intent"
            row["condition_id"] = adapter.stable_hash(row["condition"])
        with self.assertRaisesRegex(ValueError, "inventories differ"):
            self.structural(rows)

    def test_qwen_duration_only_is_unavailable_and_scores_are_unchanged(self):
        models = {adapter.QWEN, "toy-a"}
        rows = tool_rows(models)
        record = {"condition_id": adapter.CONTINUATION_ID, "duration_scope": "continuation_segment_only"}
        old = adapter.concurrent._analyze_rows(rows, models,
            {r["base_scenario_id"]: r["stratum"] for r in rows}, [p["plan_hash"] for p in provenance(models)])
        result = self.tools(rows, models, row_provenance=[record])
        for arm in adapter.ARMS[1:]:
            for metric, values in old["contrasts"][arm].items():
                if arm == "all_tools" and metric == "duration_seconds":
                    continue
                self.assertEqual({k: result["contrasts"][arm][metric][k] for k in values}, values)
        duration = result["contrasts"]["all_tools"]["duration_seconds"]
        self.assertEqual((duration["planned_pairs"], duration["eligible_pairs"], duration["ineligible_pairs"]),
                         (64, 63, 1))
        self.assertIsNone(duration["estimate"])
        self.assertNotIn("sign_flip_p", duration)
        self.assertEqual(result["rows"], 192)

    def test_missing_or_forged_duration_qualification_rejected(self):
        models = {adapter.QWEN, "toy-a"}
        for records in ([], [{"condition_id": adapter.CONTINUATION_ID, "duration_scope": "unchanged"}],
                        [{"condition_id": adapter.CONTINUATION_ID, "duration_scope": "unknown"}]):
            with self.subTest(records=records), self.assertRaises(ValueError):
                self.tools(models=models, row_provenance=records)
        rows = tool_rows(self.models)
        with self.assertRaisesRegex(ValueError, "unsupported continuation"):
            self.tools(rows, row_provenance=[{"condition_id": rows[0]["condition_id"],
                "duration_scope": "continuation_segment_only"}])

    def test_gemini_adjudicated_zero_is_retained_in_both_score_contrasts(self):
        models = {"google/gemini-3.8-flash", "toy-a"}
        rows = tool_rows(models)
        row = next(r for r in rows if r["model"] == "google/gemini-3.8-flash" and r["menu_arm"] == "scoped")
        row["condition_id"] = "4cee4a20d5026460fb8d4a5c079ff052904d329af30c425f41c8c3d7f55f6bda"
        row.update(status="failed_closed", error="KeyError: unknown allowed tool")
        row["score"].update(similarity=0.0, milestone_similarity=0.0, minefield_similarity=0.0)
        before = deepcopy(rows)
        result = self.tools(rows, models)
        old = adapter.concurrent._analyze_rows(rows, models,
            {r["base_scenario_id"]: r["stratum"] for r in rows},
            [p["plan_hash"] for p in provenance(models)])
        for arm in adapter.ARMS[1:]:
            effect = result["contrasts"][arm]["similarity"]
            self.assertEqual(effect["eligible_pairs"], 64)
            self.assertEqual(effect["degraded"] + effect["tied"] + effect["improved"], 64)
            for metric, values in old["contrasts"][arm].items():
                self.assertEqual({k: result["contrasts"][arm][metric][k] for k in values}, values)
        self.assertEqual(rows, before)

    def test_freeze_metadata_remains_literal_and_detached(self):
        for analyze, factory in ((adapter.analyze_structural, structural_rows),
                                 (adapter.analyze_toolsandbox, tool_rows)):
            records = provenance(self.models)
            kwargs = {"bootstrap_samples": 25, "randomization_samples": 50} if factory is structural_rows else {}
            result = analyze(factory(self.models), self.models, plan_provenance=records, **kwargs)
            self.assertEqual(result["plan_provenance"], records)
            self.assertIsNone(result["plan_provenance"][0]["plan_frozen_at"])
            self.assertNotIn("slug", result["plan_provenance"][0])
            records[0]["plan_frozen_at"] = "invented"
            self.assertIsNone(result["plan_provenance"][0]["plan_frozen_at"])

    def test_provenance_scope_and_row_hash_mismatch_rejected(self):
        rows = tool_rows(self.models)
        for records in (provenance(self.models)[:1], provenance(self.models) * 2):
            with self.assertRaises(ValueError):
                adapter.analyze_toolsandbox(rows, self.models, plan_provenance=records)
        rows[0]["plan_hash"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "plan provenance differs"):
            self.tools(rows)

    def test_nonfinite_boolean_resource_or_score_rejected(self):
        for container, name, value in (("score", "similarity", float("nan")),
                                       ("score", "similarity", True),
                                       ("usage", "requests", 1.0)):
            rows = tool_rows(self.models)
            rows[0][container][name] = value
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                self.tools(rows)
        rows = structural_rows(self.models)
        rows[0]["efficiency"]["wall_time_seconds"] = float("inf")
        with self.assertRaises(ValueError):
            self.structural(rows)

    def test_single_model_cannot_create_undefined_leave_one_out(self):
        for analyze in (self.tools, self.structural):
            with self.assertRaisesRegex(ValueError, "at least two"):
                analyze(models={"toy-a"})

    def test_infrastructure_failure_not_admitted_as_zero(self):
        rows = tool_rows(self.models)
        rows[0].update(status="failed_closed", error="AgnesError: HTTP 503")
        with self.assertRaisesRegex(ValueError, "infrastructure row"):
            self.tools(rows)

    def test_typed_structural_cell_is_not_coerced(self):
        rows = structural_rows(self.models)
        rows[0]["condition"]["certified_calls"] = 2.0
        rows[0]["condition_id"] = adapter.stable_hash(rows[0]["condition"])
        with self.assertRaisesRegex(ValueError, "noninteger structural cell"):
            self.structural(rows)


if __name__ == "__main__":
    unittest.main()
