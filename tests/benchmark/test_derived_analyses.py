"""Deterministic unit tests for the derived release analyses (N1/N2) and their declarations."""

from __future__ import annotations

from statistics import fmean, stdev
from math import sqrt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments import analyze_crossover_campaign as crossover
from experiments import analyze_capacity_sensitivity as sensitivity
from experiments import analyze_failure_mechanisms as mechanisms
from experiments import replay_release_results as replay
from bottleneckbench.study_design import PRIMARY_CONTRASTS
from bottleneckbench.workflow_boundary import (
    WORKFLOW_BOUNDARY_CERTIFIED_CALL_LEVELS,
    WORKFLOW_BOUNDARY_LOCATOR_LEVELS,
)


def _capacity_row(
    *,
    model: str,
    family: str,
    visible: int,
    workflow: str,
    representation: str,
    success: bool,
    truncated: bool = False,
    calls: int = 1,
    role: str = "primary",
    intent: str = "intent-a",
) -> dict:
    return {
        "study_role": role,
        "status": "verified_success" if success else "agent_failure",
        "condition": {
            "model": model,
            "family_id": family,
            "intent": intent,
            "harness": "react-routed@test",
            "visible_tools": visible,
            "workflow": workflow,
            "representation": representation,
            "outcome_variant": "base",
            "prompt_variant": "base",
        },
        "verdict": {
            "verified_success": success,
            "failure_stage": None if success else "selection",
            "errors": [],
        },
        "errors": ["model_finish_length"] if truncated else [],
        "trace": [{"tool": "search_x", "distractor": False}] * (0 if truncated else calls),
        "call_attempts": [],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "efficiency": {"model_requests_attempted": 1, "tool_schema_bytes": 100, "request_tool_schema_bytes": [100], "tool_schema_exposures": 1},
    }


def _extreme_fixture(model: str, *, truncated_at_128: int) -> list[dict]:
    """36 families x 2 representations x 4 extreme cells for one configuration."""

    rows: list[dict] = []
    truncated_left = truncated_at_128
    for index in range(36):
        family = f"family-{index:02d}"
        for representation in ("raw", "linked"):
            for visible in (8, 128):
                for workflow in ("low", "high"):
                    truncate = visible == 128 and workflow == "low" and truncated_left > 0
                    if truncate:
                        truncated_left -= 1
                    rows.append(
                        _capacity_row(
                            model=model,
                            family=family,
                            visible=visible,
                            workflow=workflow,
                            representation=representation,
                            success=not truncate and (visible == 8 or workflow == "low"),
                            truncated=truncate,
                            intent=f"intent-{index % 12}",
                        )
                    )
    return rows


class CorrectedStageTest(unittest.TestCase):
    def test_zero_call_truncation_is_relabelled_completion_no_call(self) -> None:
        row = {
            "verdict": {"verified_success": False, "failure_stage": "selection"},
            "errors": ["model_finish_length"],
            "trace": [],
            "call_attempts": [],
        }
        self.assertEqual(mechanisms.corrected_stage(row), mechanisms.COMPLETION_NO_CALL)

    def test_truncation_after_a_call_keeps_the_completion_stage(self) -> None:
        row = {
            "verdict": {"verified_success": False, "failure_stage": "selection"},
            "errors": ["model_finish_length"],
            "trace": [{"tool": "search_x"}],
            "call_attempts": [],
        }
        self.assertEqual(mechanisms.corrected_stage(row), "completion")

    def test_success_and_wrong_conclusion_labels(self) -> None:
        success = {"verdict": {"verified_success": True}}
        self.assertEqual(mechanisms.corrected_stage(success), "success")
        wrong = {
            "verdict": {"verified_success": False, "failure_stage": "answer"},
            "errors": ["answer_facts_invalid"],
            "trace": [{"tool": "get_x"}],
            "call_attempts": [],
        }
        self.assertEqual(mechanisms.corrected_stage(wrong), "answer")
        self.assertTrue(mechanisms._wrong_conclusion(wrong))

    def test_a_harness_limited_answer_error_is_not_a_wrong_conclusion(self) -> None:
        row = {
            "verdict": {"verified_success": False, "failure_stage": "answer"},
            "errors": ["answer_facts_invalid", "budget_exceeded"],
            "trace": [{"tool": "get_x"}],
            "call_attempts": [],
        }
        self.assertTrue(mechanisms._harness_limited(row))
        self.assertFalse(mechanisms._wrong_conclusion(row))


class TruncationDecompositionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = _extreme_fixture("vendor/model-a", truncated_at_128=18)
        self.report = mechanisms.visible_load_decomposition(
            self.rows, {"vendor/model-a"}
        )

    def test_matrix_closes_at_the_registered_extreme_cells(self) -> None:
        self.assertEqual(self.report["frozen_rows"], 288)
        self.assertEqual(self.report["rows_per_visible_arm"], 144)
        self.assertEqual(sorted(self.report["cells"]), ["V128_high", "V128_low", "V8_high", "V8_low"])

    def test_truncation_attributable_points_are_the_arm_difference_over_n(self) -> None:
        self.assertEqual(self.report["truncated_failures_visible_128"], 18)
        self.assertEqual(self.report["truncated_failures_visible_8"], 0)
        self.assertAlmostEqual(self.report["truncation_attributable_points"], 18 / 144)
        self.assertAlmostEqual(self.report["truncation_rate_visible_128"], 18 / 144)
        self.assertEqual(self.report["truncation_rate_visible_8"], 0.0)

    def test_visible_contrast_recovers_when_truncated_episodes_are_dropped(self) -> None:
        contrast = self.report["contrasts"]["visible_128_minus_8"]
        # V=8 always succeeds; V=128 succeeds only in the low cell, minus truncation.
        self.assertAlmostEqual(contrast["estimate"], (72 - 18) / 144 - 1.0)
        self.assertAlmostEqual(contrast["estimate_non_truncated"], 54 / 126 - 1.0)
        self.assertEqual(contrast["non_truncated_rows"], {"low": 144, "high": 126})

    def test_matched_twins_partition_the_contrast_exactly(self) -> None:
        pairs = self.report["matched_pair_decomposition"]
        # 144 twins: V=8 always verifies; V=128 fails the whole high arm (72) and the
        # 18 truncated low-arm cells, and never wins one back.
        self.assertEqual(pairs["pairs"], 144)
        self.assertEqual(pairs["lost"], 90)
        self.assertEqual(pairs["lost_with_truncated_visible_128_twin"], 18)
        self.assertEqual(pairs["lost_without_truncated_visible_128_twin"], 72)
        self.assertEqual(pairs["gained"], 0)
        self.assertAlmostEqual(pairs["net"], -90 / 144)
        self.assertAlmostEqual(
            pairs["net"], self.report["contrasts"]["visible_128_minus_8"]["estimate"]
        )

    def test_manski_bounds_bracket_the_observed_contrast(self) -> None:
        bounds = self.report["manski_bounds"]
        self.assertAlmostEqual(bounds["lower"], -90 / 144)
        self.assertAlmostEqual(bounds["upper"], -72 / 144)
        self.assertLessEqual(bounds["lower"], bounds["upper"])

    def test_intact_pairs_keep_the_matching(self) -> None:
        intact = self.report["paired_non_truncated"]
        self.assertEqual(intact["pairs"], 144 - 18)
        self.assertAlmostEqual(intact["estimate"], -72 / 126)

    def test_zero_call_truncation_is_a_subset_of_truncated_failures(self) -> None:
        for cell in self.report["cells"].values():
            self.assertLessEqual(cell["truncated_before_first_call"], cell["truncated_failures"])

    def test_zero_truncation_configurations_are_named_with_their_effects(self) -> None:
        clean = mechanisms.visible_load_decomposition(
            _extreme_fixture("vendor/model-b", truncated_at_128=0),
            {"vendor/model-b"},
        )
        self.assertEqual(clean["zero_truncation_frozen_configurations"], ["vendor/model-b"])
        self.assertAlmostEqual(
            clean["zero_truncation_frozen_visible_effects"]["vendor/model-b"], -0.5
        )


class FrontierContrastTest(unittest.TestCase):
    def _rows(self) -> list[dict]:
        rows = []
        for position, model in enumerate(("m1", "m2", "m3")):
            for family in ("f1", "f2"):
                for locator in WORKFLOW_BOUNDARY_LOCATOR_LEVELS:
                    success = locator != WORKFLOW_BOUNDARY_LOCATOR_LEVELS[-1]
                    # One top-level episode stops short of the rest, so the
                    # reachability block has a cheapest path at its reference count.
                    attempts = 2 if success else (18 if position == 0 and family == "f1" else 20)
                    rows.append({
                        "status": "verified_success" if success else "budget_failure",
                        "condition": {"model": model, "family_id": family, "locator_nodes": locator},
                        "verdict": {"verified_success": success, "failure_stage": None if success else "selection"},
                        "errors": [] if success else ["budget_exceeded"],
                        "trace": [{"tool": "search_x"}] * attempts,
                        "call_attempts": [{"call_id": "c"}] * attempts,
                        "usage": {
                            "prompt_tokens": 1000 if success else 130_000,
                            "total_tokens": 1100 if success else 131_000,
                        },
                    })
        return rows

    def test_levels_and_paired_contrasts_use_certified_calls(self) -> None:
        report = mechanisms.frontier_budget_boundary(self._rows())
        self.assertEqual(
            sorted(int(name) for name in report["levels"]),
            sorted(WORKFLOW_BOUNDARY_CERTIFIED_CALL_LEVELS),
        )
        top = report["levels"][str(WORKFLOW_BOUNDARY_CERTIFIED_CALL_LEVELS[-1])]
        self.assertEqual(top["rows"], 6)
        self.assertEqual(top["budget_exceeded"], 6)
        self.assertEqual(top["episodes_over_episode_token_budget"], 6)
        self.assertEqual(top["executed_calls_median"], 20)
        contrast = report["paired_contrasts"]["calls_30_minus_calls_2"]
        self.assertEqual(contrast["estimate"], -1.0)
        self.assertEqual(contrast["family_clusters"], 2)
        self.assertEqual(contrast["matched_model_family_pairs"], 6)
        self.assertEqual(report["paired_contrasts"]["calls_6_minus_calls_2"]["estimate"], 0.0)

    def test_every_paired_contrast_records_its_effective_seed(self) -> None:
        report = mechanisms.frontier_budget_boundary(self._rows())
        for name, contrast in report["paired_contrasts"].items():
            self.assertIsInstance(contrast["bootstrap"]["seed"], int, name)
            self.assertEqual(contrast["bootstrap"]["draws"], mechanisms.FRONTIER_BOOTSTRAP_DRAWS)

    def test_reachability_reports_the_observed_ceiling_and_the_bounds(self) -> None:
        reach = mechanisms.frontier_budget_boundary(self._rows())["reachability"]
        self.assertEqual(reach["target_calls"], WORKFLOW_BOUNDARY_CERTIFIED_CALL_LEVELS[-1])
        self.assertEqual(reach["executed_calls_max"], 20)
        self.assertEqual(reach["reference_calls"], 18)
        self.assertEqual(reach["cheapest_total_tokens_at_reference_calls"], 131_000)
        # Cheapest observed rate is the 20-call episode; 30 calls at that rate is
        # far past the 128,000-token episode budget.
        self.assertAlmostEqual(reach["cheapest_tokens_per_call_at_reference_or_above"], 131_000 / 20)
        self.assertAlmostEqual(reach["flat_rate_lower_bound_tokens_at_target"], 131_000 / 20 * 30)
        self.assertFalse(reach["reachable_within_episode_token_budget"])
        self.assertEqual(reach["quadratic_fit"]["reported_unit"], "attempted_calls")

    def test_the_quadratic_fit_is_exact_on_a_quadratic(self) -> None:
        a, b = mechanisms._least_squares_quadratic([1.0, 2.0, 3.0], [5.0, 16.0, 33.0])
        self.assertAlmostEqual(a, 3.0)
        self.assertAlmostEqual(b, 2.0)


class IndependenceBenchmarkTest(unittest.TestCase):
    def _units(self, *, joint_successes: int) -> list[dict]:
        """Eight units: base always succeeds, each single stress fails in half of them."""

        rows: list[dict] = []
        for index in range(8):
            family = f"family-{index}"
            outcomes = {
                (8, "low"): True,
                (8, "high"): index % 2 == 0,
                (128, "low"): index < 4,
                (128, "high"): index < joint_successes,
            }
            for (visible, workflow), success in outcomes.items():
                rows.append(
                    _capacity_row(
                        model="vendor/model-a",
                        family=family,
                        visible=visible,
                        workflow=workflow,
                        representation="raw",
                        success=success,
                    )
                )
        return rows

    def test_benchmark_matches_the_closed_form_and_the_floor_share(self) -> None:
        report = sensitivity.interaction_decomposition(
            self._units(joint_successes=2), samples=64, seed=7
        )
        risks = report["cell_risks"]
        p00, p01 = risks["V8_low"], risks["V8_high"]
        p10, p11 = risks["V128_low"], risks["V128_high"]
        self.assertAlmostEqual(
            report["independence_benchmark"], (p00 - p10) * (p00 - p01) / p00
        )
        self.assertAlmostEqual(report["risk_difference"], p11 - p10 - p01 + p00)
        self.assertAlmostEqual(report["risk_difference_from_units"], report["risk_difference"])
        floor = report["floor_pattern"]
        # Units 5 and 7: base succeeds, both single stresses and the joint cell fail.
        self.assertEqual(floor["units"], 2)
        self.assertAlmostEqual(floor["unit_share"], 2 / 8)
        self.assertAlmostEqual(
            floor["share_of_observed_interaction"],
            floor["contribution"] / report["risk_difference_from_units"],
        )

    def test_arithmetically_forced_units_are_dropped_from_the_non_floor_estimate(self) -> None:
        report = sensitivity.interaction_decomposition(
            self._units(joint_successes=2), samples=64, seed=7
        )
        non_floor = report["non_floor_reestimate"]
        self.assertEqual(non_floor["units_dropped"], 2)
        self.assertEqual(
            non_floor["units_dropped_by_reason"], {"both_single_stresses_at_floor": 2}
        )
        self.assertAlmostEqual(non_floor["dropped_share"], 2 / 8)

    def test_non_monotone_units_are_counted_and_removable(self) -> None:
        report = sensitivity.interaction_decomposition(
            self._units(joint_successes=2), samples=64, seed=7
        )
        monotone = report["monotonicity"]
        # Unit 1 verifies under joint stress while failing the workflow stress alone.
        self.assertEqual(monotone["non_monotone_units"], 1)
        self.assertEqual(monotone["monotone_units"], 7)
        self.assertAlmostEqual(monotone["non_monotone_unit_share"], 1 / 8)
        self.assertAlmostEqual(monotone["risk_difference_dropping_non_monotone"], 1 / 7)
        # Units 5 and 7 fail all three stressed cells; the floor pattern is a subset.
        self.assertEqual(monotone["all_stressed_cells_fail_units"], 2)
        self.assertGreaterEqual(
            monotone["all_stressed_cells_fail_units"], report["floor_pattern"]["units"]
        )

    def test_zero_stressed_success_units_can_be_removed_from_the_log_ratio(self) -> None:
        report = sensitivity.interaction_decomposition(
            self._units(joint_successes=2), samples=64, seed=7
        )
        restricted = report["non_zero_unit_family_cluster_log_risk_ratio"]
        # Units 5 and 7 verify no stressed cell; every other unit verifies at least one.
        self.assertEqual(restricted["units"], 6)
        self.assertEqual(restricted["units_dropped"], 2)
        self.assertEqual(restricted["units_dropped_by_pattern"], {"1000": 2})
        self.assertEqual(restricted["clusters"], 6)

    def test_every_interaction_bootstrap_records_its_seed(self) -> None:
        report = sensitivity.interaction_decomposition(
            self._units(joint_successes=2), samples=64, seed=7
        )
        for key in (
            "family_cluster_log_risk_ratio",
            "family_cluster_log_odds_ratio",
            "non_floor_reestimate",
            "all_unit_family_cluster",
        ):
            self.assertIn("seed", report[key], key)


class ConfigurationClusterBootstrapTest(unittest.TestCase):
    def _rows(self) -> list[dict]:
        cells = sorted({cell for terms in PRIMARY_CONTRASTS.values() for cell, _ in terms})
        rows: list[dict] = []
        for position, model in enumerate(("vendor/a", "vendor/b", "vendor/c", "vendor/d")):
            for index in range(3):
                for visible, workflow, representation in cells:
                    rows.append(
                        _capacity_row(
                            model=model,
                            family=f"family-{index}",
                            visible=visible,
                            workflow=workflow,
                            representation=representation,
                            success=visible == 8 or (position + index) % 2 == 0,
                            intent=f"intent-{index}",
                        )
                    )
        return rows

    def test_every_registered_contrast_is_clustered_by_configuration(self) -> None:
        report = sensitivity.configuration_cluster_bootstrap(self._rows(), samples=128, seed=11)
        self.assertEqual(sorted(report), sorted(PRIMARY_CONTRASTS))
        for name, effect in report.items():
            self.assertEqual(effect["clusters"], 4, name)
            self.assertEqual(len(effect["by_configuration"]), 4, name)
            self.assertGreaterEqual(effect["holm_p"], effect["bootstrap_p_two_sided"], name)
            self.assertLessEqual(effect["holm_p"], 1.0, name)
            self.assertAlmostEqual(
                effect["estimate"], fmean(effect["by_configuration"].values()), places=12
            )

    def test_holm_is_monotone_in_the_raw_bootstrap_p(self) -> None:
        report = sensitivity.configuration_cluster_bootstrap(self._rows(), samples=128, seed=11)
        ordered = sorted(report.values(), key=lambda effect: effect["bootstrap_p_two_sided"])
        adjusted = [effect["holm_p"] for effect in ordered]
        self.assertEqual(adjusted, sorted(adjusted))

    def test_bootstrap_p_cannot_resolve_below_one_draw(self) -> None:
        report = sensitivity.configuration_cluster_bootstrap(self._rows(), samples=128, seed=11)
        for effect in report.values():
            self.assertGreaterEqual(effect["bootstrap_p_two_sided"], 1 / 129)


class EquivalenceMarginTest(unittest.TestCase):
    def test_student_t_quantiles_match_published_values(self) -> None:
        for probability, degrees, expected in (
            (0.975, 1, 12.706204736),
            (0.975, 2, 4.302652730),
            (0.95, 35, 1.689572456),
            (0.975, 19, 2.093024054),
            (0.95, 3, 2.353363435),
        ):
            self.assertAlmostEqual(
                sensitivity.student_t_ppf(probability, degrees), expected, places=6
            )

    def _repair_rows(self, gains: list[float]) -> tuple[list[dict], list[dict]]:
        capacity: list[dict] = []
        repair: list[dict] = []
        for index, gain in enumerate(gains):
            family = f"family-{index}"
            plan = (
                ((8, "low", "raw"), True, True, "verification_repair"),
                ((8, "high", "raw"), False, gain > 0, "verification_repair"),
                ((8, "low", "linked"), False, False, "interface_guide"),
            )
            for cell, base_success, repaired_success, role in plan:
                capacity.append(
                    _capacity_row(
                        model="vendor/a",
                        family=family,
                        visible=cell[0],
                        workflow=cell[1],
                        representation=cell[2],
                        success=base_success,
                    )
                )
                repair.append(
                    _capacity_row(
                        model="vendor/a",
                        family=family,
                        visible=cell[0],
                        workflow=cell[1],
                        representation=cell[2],
                        success=repaired_success,
                        role=role,
                    )
                )
            repair.append(
                _capacity_row(
                    model="vendor/a",
                    family=family,
                    visible=8,
                    workflow="low",
                    representation="raw",
                    success=True,
                    role="interface_guide",
                )
            )
        return capacity, repair

    def test_smallest_rejectable_margin_is_the_one_sided_t_bound(self) -> None:
        gains = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0]
        capacity, repair = self._repair_rows(gains)
        report = sensitivity.repair_equivalence(
            capacity, repair, ["vendor/a"], samples=64, seed=3
        )
        workflow = report["workflow_prompt"]
        self.assertEqual(workflow["family_clusters"], len(gains))
        self.assertAlmostEqual(workflow["stress_repair_gain"]["estimate"], fmean(gains))
        error = stdev(gains) / sqrt(len(gains))
        expected = abs(fmean(gains)) + sensitivity.student_t_ppf(0.95, len(gains) - 1) * error
        self.assertAlmostEqual(workflow["equivalence"]["standard_error"], error)
        self.assertAlmostEqual(workflow["equivalence"]["smallest_rejectable_margin"], expected)


class ReplayRegistrationTest(unittest.TestCase):
    def test_base_outputs_cover_every_replayable_analysis(self) -> None:
        for name in (
            "structural-repair-holdout.json",
            "structural-model-versus-system.json",
            "failure-mechanism-decomposition.json",
            "capacity-cluster-sensitivity.json",
            "crossover-campaign.json",
            "release-failure-localization-v2.json",
        ):
            self.assertIn(name, replay.BASE_OUTPUTS)
        self.assertEqual(len(replay.BASE_OUTPUTS), len(set(replay.BASE_OUTPUTS)))

    def test_crossover_accounting_needs_no_private_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repeat = root / "repeat-1"
            repeat.mkdir()
            rows = [{"usage": {"prompt_tokens": 100, "completion_tokens": 10},
                     "errors": ["provider_http_520"] if i < 11 else []}
                    for i in range(50)]
            (repeat / "muse-glimmer-30b-cap4096.jsonl").write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n")
            with patch.object(crossover, "EXECUTION", root):
                without = crossover._execution_accounting()
                (root / "cost-ledger.json").write_text('{"spent_usd":999,"quarantined":[]}')
                with_private = crossover._execution_accounting()
            self.assertEqual(without, with_private)
            self.assertEqual(without["quarantined"], [("muse-glimmer-30b", "cap4096")])
            self.assertEqual(without["jobs"][0]["transport_failures"], 11)
            self.assertAlmostEqual(without["spent_usd"], 0.0021)

    def test_unestimable_crossover_is_valid_json_not_nan(self) -> None:
        block = crossover._stratum_block([], 10, 1, "B")
        self.assertIsNone(block["interaction_pp"]["estimate_pp"])
        self.assertEqual(block["reading"], "not estimable")
        json.dumps(block, allow_nan=False)

    def test_analyses_needing_withheld_plans_are_not_replayed(self) -> None:
        # Their plan inputs name a configuration excluded from the public panel,
        # so the artifacts ship with recorded digests but cannot be regenerated here.
        for name in ("toolsandbox-external-validation.json", "external-menu-intervention-analysis.json"):
            self.assertNotIn(name, replay.BASE_OUTPUTS)
        self.assertFalse(hasattr(replay, "COMMITTED_ELSEWHERE"))
        for released in (
            replay.ROOT / "results/external-menu-intervention/analysis.json",
        ):
            self.assertTrue(released.exists(), released)

    def test_new_analyses_are_bound_to_a_released_protocol_note(self) -> None:
        for path in (mechanisms.DEFAULT_PROTOCOL, sensitivity.DEFAULT_PROTOCOL):
            self.assertTrue(path.exists(), path)
        plan = replay.ROOT / "results/prospective-plans/structural-repair-holdout/plan.redacted.json"
        self.assertTrue(plan.exists())


if __name__ == "__main__":
    unittest.main()
