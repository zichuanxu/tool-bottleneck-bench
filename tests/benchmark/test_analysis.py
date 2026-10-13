from __future__ import annotations

import unittest

from bottleneckbench.admission import development_admission
from bottleneckbench.analysis import (
    _efficiency_summary,
    _failure_stage,
    _repeated_attempts,
    analyze_capacity_rows,
    analyze_discovery_rows,
    analyze_repair_rows,
    is_infrastructure_failure,
    prospective_power,
    prospective_precision,
)
from bottleneckbench.study_design import (
    DISCOVERY_REGISTRY_SIZES,
    PRIMARY_CAPACITY_CELLS,
    ROBUSTNESS_CAPACITY_CELLS,
    StudyRole,
    efficient_design_audit,
)
from bottleneckbench.core.schema import stable_hash


REPRESENTATIONS = ("raw", "generic_compact", "typed", "linked")


def _capacity_row(
    family: str,
    model: str,
    harness: str,
    visible: int,
    workflow: str,
    representation: str,
    success: bool,
    study_role: str = "primary",
) -> dict:
    condition = {
        "task_id": f"{family}-{workflow}",
        "family_id": family,
        "intent": family.replace("development-family-", "intent-"),
        "outcome_variant": 0,
        "prompt_variant": 0,
        "branching": False,
        "action_required": False,
        "model": model,
        "harness": harness,
        "visible_tools": visible,
        "workflow": workflow,
        "representation": representation,
        "seed": 1,
    }
    return {
        "condition": condition,
        "condition_id": stable_hash(condition),
        "plan_hash": stable_hash({"kind": "capacity", "model": model, "harness": harness, "study_role": study_role}),
        "study_role": study_role,
        "status": "verified_success" if success else "agent_failure",
        "verdict": {
            "verified_success": success,
            "failure_stage": None if success else "workflow",
        },
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
    }


def _discovery_row(
    case: str,
    model: str,
    harness: str,
    retriever: str,
    registry_size: int,
    success: bool,
) -> dict:
    condition = {
        "case_id": case,
        "split": "development",
        "model": model,
        "harness": harness,
        "retriever": retriever,
        "registry_size": registry_size,
        "visible_tools": 16,
        "seed": 1,
    }
    return {
        "condition": condition,
        "condition_id": stable_hash(condition),
        "plan_hash": stable_hash({"kind": "discovery", "model": model, "harness": harness}),
        "status": "verified_success" if success else "agent_failure",
        "exposure": {"required_available": True, "required_tool": f"tool-{case}"},
        "success": success,
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
    }


class AnalysisTest(unittest.TestCase):
    def test_missing_usage_is_explicit_and_not_reported_as_complete_efficiency(self) -> None:
        summary = _efficiency_summary(
            [
                {
                    "condition": {"visible_tools": 8},
                    "verdict": {"verified_success": True},
                    "usage": {
                        "prompt_tokens": 900,
                        "completion_tokens": 100,
                        "total_tokens": 1000,
                    },
                    "response_ids": ["generation-1"],
                    "usage_sources": ["stream_usage_missing"],
                }
            ]
        )

        self.assertFalse(summary["token_telemetry_complete"])
        self.assertEqual(summary["responses_missing_usage"], 1)
        self.assertEqual(summary["response_token_telemetry_coverage"], 0.0)
        self.assertIsNone(summary["verified_successes_per_million_tokens"])
        self.assertEqual(
            summary["verified_successes_per_million_observed_tokens"], 1000.0
        )

    construct_audits = (
        {"audit": "capacity-surface-static-construct-v4", "split": "development", "passed": True},
        {"audit": "capacity-surface-runtime-construct-v4", "split": "development", "passed": True},
        {"audit": "capacity-surface-discovery-construct-v3", "split": "development", "passed": True},
        efficient_design_audit(),
        {"audit": "factorized-capacity-efficiency-policy", "split": "development", "passed": True},
    )
    def test_capacity_analysis_recovers_paired_visible_effect(self) -> None:
        rows = [
            _capacity_row(
                family,
                "model-a",
                "react@test",
                visible,
                workflow,
                representation,
                visible == 8,
            )
            for family in ("family-a", "family-b")
            for visible in (8, 32, 128)
            for workflow in ("low", "medium", "high")
            for representation in REPRESENTATIONS
        ]
        rows[0]["answer"] = (
            '```json\n{"decision":"ok","facts":{},"sources":[]}\n```'
        )
        rows[0]["verdict"]["answer"] = True

        result = analyze_capacity_rows(rows, bootstrap_samples=200)

        effect = result["primary_effects"]["visible_128_minus_8"]
        self.assertEqual(effect["estimate"], -1.0)
        self.assertEqual(effect["ci95"], [-1.0, -1.0])
        self.assertEqual(effect["clusters"], 2)
        self.assertEqual(
            result["transitions"]["visible_8_to_128"]["failure_transitions"],
            {"workflow": 24},
        )
        self.assertEqual(
            result["by_configuration"][0]["descriptive_effects"][
                "visible_128_minus_8"
            ]["estimate"],
            -1.0,
        )
        self.assertEqual(result["answer_wrappers_normalized"], 1)
        self.assertEqual(result["by_configuration"][0]["answer_wrappers_normalized"], 1)

    def test_power_simulation_is_deterministic_and_intent_clustered(self) -> None:
        rows = [
            _capacity_row(
                f"development-family-{family_index}",
                "model-a",
                "react@test",
                visible,
                workflow,
                representation,
                visible == 8,
            )
            for family_index in range(12)
            for visible in (8, 32, 128)
            for workflow in ("low", "medium", "high")
            for representation in REPRESENTATIONS
        ]

        first = prospective_power(rows, simulations=200)
        second = prospective_power(rows, simulations=200)

        self.assertEqual(first, second)
        self.assertTrue(first["passed"], first)
        self.assertTrue(
            all(
                effect["development_intent_clusters"] == 12
                for effect in first["effects"].values()
            )
        )

    def test_precision_projection_is_deterministic_and_family_level(self) -> None:
        rows = [
            _capacity_row(
                f"development-family-{family_index}",
                "model-a",
                "react@test",
                cell.visible_tools,
                cell.workflow.value,
                cell.representation.value,
                cell.visible_tools != 128,
            )
            for family_index in range(12)
            for cell in PRIMARY_CAPACITY_CELLS
        ]

        first = prospective_precision(rows)
        second = prospective_precision(rows)

        self.assertEqual(first, second)
        self.assertTrue(first["passed"], first)
        self.assertTrue(
            all(
                effect["development_family_units"] == 12
                and effect["projected_simultaneous_95_half_width"] <= 0.15
                for effect in first["effects"].values()
            )
        )

    def test_primary_family_inference_retains_intent_sensitivity(self) -> None:
        rows = [
            _capacity_row(
                f"family-{family_index}",
                "model-a",
                "react@test",
                cell.visible_tools,
                cell.workflow.value,
                cell.representation.value,
                cell.visible_tools != 128,
            )
            for family_index in range(4)
            for cell in PRIMARY_CAPACITY_CELLS
        ]
        for row in rows:
            family_index = int(row["condition"]["family_id"].rsplit("-", 1)[1])
            row["condition"]["intent"] = f"intent-{family_index % 2}"
            row["condition_id"] = stable_hash(row["condition"])

        result = analyze_capacity_rows(rows, bootstrap_samples=200)

        self.assertEqual(
            result["primary_effects"]["visible_128_minus_8"]["clusters"], 4
        )
        self.assertEqual(
            result["intent_cluster_sensitivity"]["visible_128_minus_8"][
                "clusters"
            ],
            2,
        )

    def test_fixed_menu_robustness_is_paired_but_not_pooled(self) -> None:
        primary = [
            _capacity_row(
                family,
                "model-a",
                "react-routed@test",
                cell.visible_tools,
                cell.workflow.value,
                cell.representation.value,
                True,
                StudyRole.PRIMARY.value,
            )
            for family in ("family-a", "family-b")
            for cell in PRIMARY_CAPACITY_CELLS
        ]
        robustness = [
            _capacity_row(
                family,
                "model-a",
                "react-fixed@test",
                cell.visible_tools,
                cell.workflow.value,
                cell.representation.value,
                False,
                StudyRole.ROBUSTNESS.value,
            )
            for family in ("family-a", "family-b")
            for cell in ROBUSTNESS_CAPACITY_CELLS
        ]
        for row in primary:
            row["efficiency"] = {
                "model_requests_attempted": 2,
                "tool_schema_exposures": 10,
            }
        for row in robustness:
            row["efficiency"] = {
                "model_requests_attempted": 2,
                "tool_schema_exposures": 100,
            }

        result = analyze_capacity_rows(
            [*primary, *robustness], bootstrap_samples=200
        )

        comparison = result["routing_policy_robustness"]
        self.assertEqual(comparison["pairs"], 8)
        self.assertEqual(
            comparison["fixed_minus_routed_verified_success"]["estimate"], -1.0
        )
        self.assertEqual(comparison["discordance"], {"routed_only_success": 8})
        self.assertEqual(
            comparison["mean_fixed_minus_routed_tool_schema_exposures"], 90
        )
        self.assertEqual(result["verified_success"], 1.0)

    def test_matched_repairs_must_help_stress_more_than_low_pressure(self) -> None:
        rows = []
        for model in ("model-a", "model-b"):
            for family in ("family-a", "family-b", "family-c", "family-d"):
                rows.extend(
                    (
                        _capacity_row(family, model, "react@test", 8, "low", "raw", True),
                        _capacity_row(family, model, "react@test", 8, "high", "raw", False),
                        _capacity_row(family, model, "react@test", 8, "low", "linked", False),
                        _capacity_row(
                            family,
                            model,
                            "deliberate-verify-v1@test",
                            8,
                            "low",
                            "raw",
                            True,
                            StudyRole.VERIFICATION_REPAIR.value,
                        ),
                        _capacity_row(
                            family,
                            model,
                            "deliberate-verify-v1@test",
                            8,
                            "high",
                            "raw",
                            True,
                            StudyRole.VERIFICATION_REPAIR.value,
                        ),
                        _capacity_row(
                            family,
                            model,
                            "interface-guide-v1@test",
                            8,
                            "low",
                            "raw",
                            True,
                            StudyRole.INTERFACE_GUIDE.value,
                        ),
                        _capacity_row(
                            family,
                            model,
                            "interface-guide-v1@test",
                            8,
                            "low",
                            "linked",
                            True,
                            StudyRole.INTERFACE_GUIDE.value,
                        ),
                    )
                )

        result = analyze_repair_rows(
            rows, bootstrap_samples=200, randomization_samples=2000
        )

        for effect in result["effects"].values():
            self.assertEqual(effect["metrics"]["repair_selectivity"]["estimate"], 1.0)
            self.assertEqual(effect["recoverable_loss_fraction"], 1.0)
            self.assertEqual(effect["configuration_family_pairs"], 8)

    def test_malformed_tool_arguments_are_an_agent_failure(self) -> None:
        row = _capacity_row(
            "family-a", "model-a", "react@test", 8, "low", "raw", False
        )
        row["status"] = "provider_failure"
        row["errors"] = ["provider_failure:tool call arguments are invalid JSON"]

        self.assertFalse(is_infrastructure_failure(row))

        row["status"] = "agent_failure"
        row["errors"] = [
            "model_response_contract:tool_call_0_arguments_invalid_json"
        ]
        self.assertFalse(is_infrastructure_failure(row))
        self.assertEqual(_failure_stage(row), "arguments")

    def test_failed_call_attempts_resolve_first_failure_stages(self) -> None:
        row = _capacity_row(
            "family-a", "model-a", "react@test", 8, "low", "raw", False
        )
        row["call_attempts"] = [
            {"outcome": "tool_error", "error": "RecordNotFound"}
        ]
        self.assertEqual(_failure_stage(row), "arguments")

        row["call_attempts"][0]["error"] = "PermissionDenied"
        self.assertEqual(_failure_stage(row), "execution")

        row["call_attempts"] = [
            {"outcome": "repeat_blocked", "error": "repeated_tool_call"},
            {"outcome": "tool_error", "error": "RecordNotFound"},
        ]
        self.assertEqual(_failure_stage(row), "completion")

        row["call_attempts"] = [
            {"outcome": "budget_blocked", "error": "tool_call_limit_exceeded"}
        ]
        self.assertEqual(_failure_stage(row), "budget")

    def test_repeated_attempts_are_an_efficiency_diagnostic(self) -> None:
        row = _capacity_row(
            "family-a", "model-a", "react@test", 8, "low", "raw", True
        )
        row["call_attempts"] = [
            {"tool": "search", "arguments": {"query": "case-a"}},
            {"tool": "search", "arguments": {"query": "case-a"}},
            {"tool": "get", "arguments": {"record_id": "record-a"}},
        ]

        self.assertEqual(_repeated_attempts(row), 1)

    def test_discovery_analysis_separates_availability_and_selection(self) -> None:
        rows = [
            _discovery_row(case, "model-a", "native@test", retriever, registry_size, case == "case-a")
            for case in ("case-a", "case-b")
            for retriever in ("flat", "hierarchical")
            for registry_size in (32, 2048)
        ]
        for row in rows:
            if row["condition"]["case_id"] == "case-b":
                row["status"] = "capability_unavailable"
                row["exposure"]["required_available"] = False
                row["usage"] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        profile = next(
            item
            for item in analyze_discovery_rows(rows)["profiles"]
            if item["retriever"] == "flat" and item["registry_size"] == 32
        )

        self.assertEqual(profile["availability"], 0.5)
        self.assertEqual(profile["conditional_selection_success"], 1.0)
        self.assertEqual(profile["itt_success"], 0.5)

    def test_complete_non_degenerate_development_panel_is_admitted(self) -> None:
        capacity = []
        discovery = []
        for index in range(3):
            model = f"model-{index}"
            for family_index in range(12):
                family = f"development-family-{family_index}"
                for role, harness, cells in (
                    (StudyRole.PRIMARY, "react@test", PRIMARY_CAPACITY_CELLS),
                    (StudyRole.ROBUSTNESS, "deliberate@test", ROBUSTNESS_CAPACITY_CELLS),
                ):
                    for cell in cells:
                        capacity.append(
                            _capacity_row(
                                family,
                                model,
                                harness,
                                cell.visible_tools,
                                cell.workflow.value,
                                cell.representation.value,
                                cell.visible_tools != 128
                                and cell.workflow.value != "high"
                                and cell.representation.value in {"raw", "generic_compact"},
                                role.value,
                            )
                        )
            for case_index in range(18):
                for retriever in ("flat", "hierarchical"):
                    for registry_size in DISCOVERY_REGISTRY_SIZES:
                        discovery.append(
                            _discovery_row(
                                f"case-{case_index}",
                                model,
                                "react@test",
                                retriever,
                                registry_size,
                                not (retriever == "flat" and registry_size == 2048),
                            )
                        )

        expected_capacity = [
            (f"model-{index}", harness, role.value)
            for index in range(3)
            for role, harness in (
                (StudyRole.PRIMARY, "react@test"),
                (StudyRole.ROBUSTNESS, "deliberate@test"),
            )
        ]
        expected_discovery = [(f"model-{index}", "react@test") for index in range(3)]
        audit = development_admission(
            capacity,
            discovery,
            self.construct_audits,
            expected_capacity_configurations=expected_capacity,
            expected_discovery_configurations=expected_discovery,
            model_panel_hash="a" * 64,
            protocol_artifact_hash="b" * 64,
        )

        self.assertTrue(audit["passed"], audit)
        self.assertEqual(len(audit["checks"]), 15)
        self.assertEqual(audit["model_panel_hash"], "a" * 64)
        self.assertEqual(audit["protocol_artifact_hash"], "b" * 64)
        self.assertTrue(
            next(check for check in audit["checks"] if check["name"] == "prospective_precision")[
                "passed"
            ]
        )
        self.assertFalse(
            development_admission(capacity[:-1], discovery, self.construct_audits)["passed"]
        )
        self.assertFalse(
            development_admission(capacity, discovery, self.construct_audits[:-1])["passed"]
        )
        self.assertFalse(
            development_admission(
                capacity,
                discovery,
                self.construct_audits,
                expected_capacity_configurations=expected_capacity[:-1],
                expected_discovery_configurations=expected_discovery,
            )["passed"]
        )

    def test_representation_gate_preserves_configuration_crossovers(self) -> None:
        capacity = []
        discovery = []
        for index in range(4):
            model = f"model-{index}"
            preferred = (
                {"raw", "typed"}
                if index % 2 == 0
                else {"generic_compact", "linked"}
            )
            for family_index in range(12):
                family = f"development-family-{family_index}"
                for role, harness, cells in (
                    (StudyRole.PRIMARY, "react@test", PRIMARY_CAPACITY_CELLS),
                    (StudyRole.ROBUSTNESS, "deliberate@test", ROBUSTNESS_CAPACITY_CELLS),
                ):
                    for cell in cells:
                        capacity.append(
                            _capacity_row(
                                family,
                                model,
                                harness,
                                cell.visible_tools,
                                cell.workflow.value,
                                cell.representation.value,
                                cell.visible_tools != 128
                                and cell.workflow.value != "high"
                                and cell.representation.value in preferred,
                                role.value,
                            )
                        )
            for case_index in range(18):
                for retriever in ("flat", "hierarchical"):
                    for registry_size in DISCOVERY_REGISTRY_SIZES:
                        discovery.append(
                            _discovery_row(
                                f"case-{case_index}",
                                model,
                                "react@test",
                                retriever,
                                registry_size,
                                not (retriever == "flat" and registry_size == 2048),
                            )
                        )

        audit = development_admission(capacity, discovery, self.construct_audits)
        check = next(
            item
            for item in audit["checks"]
            if item["name"] == "representation_active_range"
        )

        self.assertTrue(check["passed"], check)
        self.assertEqual(check["details"]["pooled_maximum_minus_minimum"], 0.0)
        self.assertEqual(check["details"]["active_configurations"], 4)
        self.assertGreaterEqual(check["details"]["mean_configuration_spread"], 0.05)

    def test_separately_frozen_boundary_can_activate_a_flat_core_workflow_axis(self) -> None:
        capacity = []
        discovery = []
        for index in range(3):
            model = f"model-{index}"
            for family_index in range(12):
                family = f"development-family-{family_index}"
                for role, harness, cells in (
                    (StudyRole.PRIMARY, "react@test", PRIMARY_CAPACITY_CELLS),
                    (StudyRole.ROBUSTNESS, "deliberate@test", ROBUSTNESS_CAPACITY_CELLS),
                ):
                    for cell in cells:
                        capacity.append(
                            _capacity_row(
                                family,
                                model,
                                harness,
                                cell.visible_tools,
                                cell.workflow.value,
                                cell.representation.value,
                                cell.visible_tools != 128
                                and cell.representation.value
                                in {"raw", "generic_compact"},
                                role.value,
                            )
                        )
            for case_index in range(18):
                for retriever in ("flat", "hierarchical"):
                    for registry_size in DISCOVERY_REGISTRY_SIZES:
                        discovery.append(
                            _discovery_row(
                                f"case-{case_index}",
                                model,
                                "react@test",
                                retriever,
                                registry_size,
                                not (retriever == "flat" and registry_size == 2048),
                            )
                        )
        panel_hash = "a" * 64
        protocol_hash = "b" * 64
        boundary = {
            "audit": "workflow-capacity-frontier-development-admission-v1",
            "split": "development",
            "passed": True,
            "model_panel_hash": panel_hash,
            "protocol_artifact_hash": protocol_hash,
        }

        without_boundary = development_admission(
            capacity,
            discovery,
            self.construct_audits,
            model_panel_hash=panel_hash,
            protocol_artifact_hash=protocol_hash,
        )
        with_boundary = development_admission(
            capacity,
            discovery,
            self.construct_audits,
            model_panel_hash=panel_hash,
            protocol_artifact_hash=protocol_hash,
            workflow_boundary_admission=boundary,
        )

        self.assertFalse(without_boundary["passed"])
        workflow = next(
            check
            for check in with_boundary["checks"]
            if check["name"] == "workflow_load_active_range"
        )
        self.assertTrue(workflow["passed"], workflow)
        self.assertFalse(workflow["details"]["core_three_level_active"])
        self.assertTrue(workflow["details"]["boundary_extension_active"])
        self.assertTrue(with_boundary["passed"], with_boundary)
        self.assertIsNone(without_boundary["workflow_boundary_admission_hash"])
        self.assertEqual(
            with_boundary["workflow_boundary_admission_hash"],
            stable_hash(boundary),
        )


if __name__ == "__main__":
    unittest.main()
