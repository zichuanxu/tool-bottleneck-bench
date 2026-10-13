"""Synthetic regressions for optional, source-bound normalized row costs."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "experiments"))
sys.path.insert(0, str(ROOT / "src"))
import analyze_model_system_decisions as decisions
import analyze_final_panel_cost as final_cost
import analyze_structural_model_system_decisions as structural


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return sha256(canonical(value)).hexdigest()


def model_of(row):
    return row.get("condition", row)["model"]


def metric_row(model, index=0, **condition):
    cond = {"model": model, **condition}
    return {
        "condition": cond, "condition_id": digest(cond), "status": "agent_failure",
        "verdict": {"verified_success": bool(index % 2)},
        "usage": {"prompt_tokens": 100 + index, "completion_tokens": 10 + index,
                  "total_tokens": 110 + 2 * index},
        "efficiency": {"model_requests_attempted": 2, "tool_schema_bytes": 20,
                       "tool_schema_exposures": 40, "wall_time_seconds": 2 + index},
        "call_attempts": [{"tool": "one"}], "trace": [{"tool": "one"}],
    }


def fixtures():
    models = ("retained/model", "new/model")
    capacity, discovery, repair = [], [], []
    for mi, model in enumerate(models):
        for fi in range(4):
            for role in ("primary", "robustness"):
                for visible in (8, 128):
                    for workflow in ("low", "high"):
                        for representation in ("raw", "linked"):
                            row = metric_row(model, mi + fi + (visible == 128), family_id=f"f{fi}",
                                fixture_role=role, visible_tools=visible, workflow=workflow,
                                representation=representation)
                            row["study_role"] = role
                            capacity.append(row)
            for role, workflow, representation in (
                ("verification_repair", "low", "raw"), ("verification_repair", "high", "raw"),
                ("interface_guide", "low", "raw"), ("interface_guide", "low", "linked"),
            ):
                row = metric_row(model, mi + fi + 2, family_id=f"f{fi}", fixture_role=role,
                                 visible_tools=8, workflow=workflow, representation=representation)
                row["study_role"] = role
                repair.append(row)
            for retriever in ("flat", "hierarchical"):
                for size in (32, 2048):
                    row = metric_row(model, mi + fi + (size == 2048), case_id=f"case{fi}",
                                     retriever=retriever, registry_size=size)
                    row["exposure"] = {"required_tool": f"tool{fi}"}
                    discovery.append(row)
    panel = {"configurations": [{"model": m, "organization": str(i)} for i, m in enumerate(models)],
             "auxiliary_models": list(models)}
    prices = {models[0]: (0.01, 0.02), models[1]: (0.03, 0.04)}
    openrouter = {"selected": [{"requested_model": m, "prompt_price": p[0], "completion_price": p[1]}
                               for m, p in prices.items()]}
    return dict(capacity=capacity, discovery=discovery, repair=repair, panel=panel,
                openrouter_prices=openrouter, google_prices={"models": {}}, samples=32, seed=7), prices


def structural_fixture():
    return [metric_row(model, fi + mi + (arm == "trace_state"), family_id=f"f{fi}",
                       certified_calls=30, representation="raw", delivery="native", orchestration=arm)
            for mi, model in enumerate(("retained/model", "new/model"))
            for fi in range(24) for arm in ("baseline", "trace_state")]


class BoundCosts:
    """Hermetic implementation of the adapter's identity/hash-bound interface."""
    def __init__(self, rows, prices, *, multiplier=1.0, models=frozenset({"new/model"})):
        self.models = frozenset(models)
        self.values = {
            (model_of(row), row["condition_id"]):
                (digest(row), multiplier * (row["usage"]["prompt_tokens"] * prices[model_of(row)][0]
                 + row["usage"]["completion_tokens"] * prices[model_of(row)][1]))
            for row in rows if model_of(row) in self.models
        }
        self.calls = []

    def cost_for_row(self, row):
        key = (model_of(row), row["condition_id"])
        if key not in self.values or self.values[key][0] != digest(row):
            raise ValueError("missing or changed bound row cost")
        self.calls.append(key)
        return self.values[key][1]


class RowCostsTests(unittest.TestCase):
    def setUp(self):
        self.kwargs, self.prices = fixtures()
        self.rows = [*self.kwargs["capacity"], *self.kwargs["discovery"], *self.kwargs["repair"]]

    def test_default_decision_output_has_frozen_synthetic_digest(self):
        result = decisions.analyze(**self.kwargs)
        self.assertEqual(digest(result), "245fb8b19b47589d67d291f7401ee610bbf4d915387b963eda664c139fa834fd")
        self.assertEqual(canonical(result), canonical(decisions.analyze(**self.kwargs, row_costs=None)))

    def test_default_structural_output_has_frozen_synthetic_digest(self):
        rows = structural_fixture()
        args = (["plan"], rows, self.prices, frozenset(self.prices))
        result = structural.analyze(*args, samples=32, seed=7)
        self.assertEqual(digest(result), "11e04ecab33863511d850aed08e51f0dbf3d6f531eccfd545d3acc55ac546b00")
        self.assertEqual(canonical(result), canonical(structural.analyze(*args, samples=32, seed=7, row_costs=None)))

    def test_equal_row_costs_preserve_complete_decision_and_structural_outputs(self):
        costs = BoundCosts(self.rows, self.prices)
        self.assertEqual(canonical(decisions.analyze(**self.kwargs)),
                         canonical(decisions.analyze(**self.kwargs, row_costs=costs)))
        rows = structural_fixture()
        args = (["plan"], rows, self.prices, frozenset(self.prices))
        self.assertEqual(canonical(structural.analyze(*args, samples=32, seed=7)),
                         canonical(structural.analyze(*args, samples=32, seed=7,
                                                      row_costs=BoundCosts(rows, self.prices))))

    def test_cost_override_changes_only_row_cost_not_other_metrics(self):
        row = self.rows[-1]
        costs = BoundCosts([row], self.prices, multiplier=3)
        for function in (decisions._row_metrics, structural._metrics):
            old = function(row, self.prices)
            new = function(row, {"retained/model": self.prices["retained/model"]}, row_costs=costs)
            self.assertEqual(new.pop("cost_usd"), 3 * old.pop("cost_usd"))
            self.assertEqual(canonical(new), canonical(old))

    def test_missing_or_tampered_row_never_falls_back_to_available_flat_price(self):
        row = self.rows[-1]
        costs = BoundCosts([row], self.prices)
        changed = deepcopy(row)
        changed["verdict"]["verified_success"] = not changed["verdict"]["verified_success"]
        for function in (decisions._row_metrics, structural._metrics):
            for bad in (changed, self.rows[-2]):
                with self.assertRaisesRegex(ValueError, "bound row cost"):
                    function(bad, self.prices, row_costs=costs)

    def test_lookup_value_requires_finite_nonnegative_float(self):
        row = self.rows[-1]
        costs = BoundCosts([row], self.prices)
        key = (model_of(row), row["condition_id"])
        for bad in (True, "1", -1.0, float("nan"), float("inf")):
            costs.values[key] = (digest(row), bad)
            with self.subTest(value=bad), self.assertRaisesRegex(ValueError, "row cost"):
                decisions._row_metrics(row, self.prices, row_costs=costs)

    def test_model_coverage_split_preserves_retained_snapshot_and_requires_missing_new_rows(self):
        kwargs = deepcopy(self.kwargs)
        kwargs["openrouter_prices"]["selected"] = kwargs["openrouter_prices"]["selected"][:1]
        costs = BoundCosts(self.rows, self.prices)
        expected = decisions.analyze(**self.kwargs)
        self.assertEqual(canonical(expected), canonical(decisions.analyze(**kwargs, row_costs=costs)))
        with self.assertRaisesRegex(ValueError, "price snapshot"):
            decisions.analyze(**kwargs)
        kwargs["openrouter_prices"]["selected"] = []
        with self.assertRaisesRegex(ValueError, "price snapshot"):
            decisions.analyze(**kwargs, row_costs=costs)
        kwargs = deepcopy(self.kwargs)
        costs.values.pop(next(iter(costs.values)))
        with self.assertRaisesRegex(ValueError, "bound row cost"):
            decisions.analyze(**kwargs, row_costs=costs)

    def test_pooled_prices_omit_covered_models_without_placeholder(self):
        costs = BoundCosts(self.rows, self.prices)
        kwargs = self.kwargs
        old = structural._pooled_prices(kwargs["panel"], kwargs["openrouter_prices"], kwargs["google_prices"], [], frozenset(self.prices))
        self.assertEqual(old, self.prices)
        new = structural._pooled_prices(kwargs["panel"], {"selected": kwargs["openrouter_prices"]["selected"][:1]},
            kwargs["google_prices"], [], frozenset(self.prices), row_costs=costs)
        self.assertEqual(new, {"retained/model": self.prices["retained/model"]})

    def test_lookup_models_outside_the_selected_panel_are_rejected(self):
        costs = BoundCosts(self.rows, self.prices)
        costs.models |= {"foreign/model"}
        with self.assertRaisesRegex(ValueError, "outside the analysis panel"):
            decisions.analyze(**self.kwargs, row_costs=costs)
        with self.assertRaisesRegex(ValueError, "outside the analysis panel"):
            structural.analyze(["plan"], structural_fixture(), self.prices,
                frozenset(self.prices), samples=32, seed=7, row_costs=costs)
        root, manifest = self.final_fixture([self.rows[0], self.rows[-1]])
        with patch.object(final_cost, "ROOT", root), self.assertRaisesRegex(ValueError, "outside the analysis panel"):
            final_cost.analyze(manifest, self.prices, row_costs=costs)

    def test_covered_incomplete_usage_fails_but_zero_usage_is_valid(self):
        row = deepcopy(self.rows[-1])
        row["efficiency"]["usage_missing_responses"] = 1
        costs = BoundCosts([row], self.prices)
        for function in (decisions._row_metrics, structural._metrics):
            with self.assertRaisesRegex(ValueError, "complete row usage"):
                function(row, self.prices, row_costs=costs)
        root, manifest = self.final_fixture([row])
        with patch.object(final_cost, "ROOT", root), self.assertRaisesRegex(ValueError, "complete row usage"):
            final_cost.analyze(manifest, self.prices, row_costs=costs)
        row["efficiency"]["usage_missing_responses"] = 0
        row["usage"] = dict.fromkeys(("prompt_tokens", "completion_tokens", "total_tokens"), 0)
        costs = BoundCosts([row], self.prices)
        self.assertEqual(decisions._row_metrics(row, {}, row_costs=costs)["cost_usd"], 0)
        self.assertEqual(structural._metrics(row, {}, row_costs=costs)["cost_usd"], 0)

    def test_flat_price_for_a_covered_model_never_overrides_bound_cost(self):
        kwargs = deepcopy(self.kwargs)
        kwargs["candidate_prices"] = {"models": {"new/model": {"input": 999, "output": 999}}}
        costs = BoundCosts(self.rows, self.prices)
        self.assertEqual(canonical(decisions.analyze(**self.kwargs)),
                         canonical(decisions.analyze(**kwargs, row_costs=costs)))
        pooled = structural._pooled_prices(kwargs["panel"], kwargs["openrouter_prices"],
            kwargs["google_prices"], [kwargs["candidate_prices"]], frozenset(self.prices), row_costs=costs)
        self.assertNotIn("new/model", pooled)

    def final_fixture(self, rows):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        data = b"".join(canonical(row) + b"\n" for row in rows)
        (root / "rows.jsonl").write_bytes(data)
        manifest = {"inputs": {"synthetic": [{"path": "rows.jsonl", "sha256": sha256(data).hexdigest(), "rows": len(rows)}]}}
        return root, manifest

    def test_final_panel_default_and_equal_override_are_byte_identical(self):
        rows = [self.rows[0], self.rows[-1]]
        root, manifest = self.final_fixture(rows)
        with patch.object(final_cost, "ROOT", root):
            old = final_cost.analyze(manifest, self.prices)
            self.assertEqual(digest(old), "7f375afa4850bb89dc5d8f933bcfe6c6d17d7a66163b6d7b1de408be29ce46bb")
            self.assertEqual(canonical(old), canonical(final_cost.analyze(manifest, self.prices, row_costs=None)))
            self.assertEqual(canonical(old), canonical(final_cost.analyze(manifest,
                {"retained/model": self.prices["retained/model"]}, row_costs=BoundCosts(rows, self.prices))))

    def test_final_panel_missing_and_tampered_cost_rows_fail(self):
        row = self.rows[-1]
        costs = BoundCosts([row], self.prices)
        for changed in (self.rows[-2], {**row, "status": "different"}):
            root, manifest = self.final_fixture([changed])
            with patch.object(final_cost, "ROOT", root), self.assertRaisesRegex(ValueError, "bound row cost"):
                final_cost.analyze(manifest, self.prices, row_costs=costs)

    def test_final_panel_accepts_flat_model_rows_and_zero_request_costs(self):
        row = deepcopy(self.rows[-1])
        row["model"] = row.pop("condition")["model"]
        row["usage"] = dict.fromkeys(("prompt_tokens", "completion_tokens", "total_tokens"), 0)
        root, manifest = self.final_fixture([row])
        with patch.object(final_cost, "ROOT", root):
            result = final_cost.analyze(manifest, {}, row_costs=BoundCosts([row], self.prices))
        self.assertEqual(result["estimated_cost_usd"], 0)
        self.assertEqual(result["usage_coverage"], 1)

    def test_optional_costs_do_not_exempt_uncovered_models_from_flat_price_coverage(self):
        old = deepcopy(self.rows[0])
        old["usage"] = None
        new = self.rows[-1]
        root, manifest = self.final_fixture([old, new])
        with patch.object(final_cost, "ROOT", root):
            legacy = final_cost.analyze(manifest, self.prices)
            self.assertTrue(legacy["cost_is_lower_bound"])
            with self.assertRaisesRegex(ValueError, "do not cover"):
                final_cost.analyze(manifest, {}, row_costs=BoundCosts([new], self.prices))


if __name__ == "__main__":
    unittest.main()
