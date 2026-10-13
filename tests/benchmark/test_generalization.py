from __future__ import annotations

import copy
import unittest

from bottleneckbench.generalization import (
    NAMED_REPLICATION_CLAIMS,
    analyze_main_model_generalization,
)
from bottleneckbench.study_design import (
    DISCOVERY_REGISTRY_SIZES,
    PRIMARY_CAPACITY_CELLS,
    ROBUSTNESS_CAPACITY_CELLS,
)
from bottleneckbench.core.schema import stable_hash


def _capacity_rows(models: tuple[str, ...]) -> list[dict]:
    rows: list[dict] = []
    for model in models:
        for family_index in range(2):
            family = f"family-{family_index}"
            for role, harness, cells in (
                ("primary", "routed@test", PRIMARY_CAPACITY_CELLS),
                ("robustness", "fixed@test", ROBUSTNESS_CAPACITY_CELLS),
            ):
                for cell in cells:
                    success = cell.visible_tools == 8
                    condition = {
                        "task_id": f"{family}-task",
                        "family_id": family,
                        "intent": "shared-intent",
                        "outcome_variant": 0,
                        "prompt_variant": 0,
                        "branching": False,
                        "action_required": False,
                        "model": model,
                        "harness": harness,
                        "visible_tools": cell.visible_tools,
                        "workflow": cell.workflow.value,
                        "representation": cell.representation.value,
                        "seed": 1,
                    }
                    rows.append(
                        {
                            "condition": condition,
                            "condition_id": stable_hash(condition),
                            "study_role": role,
                            "status": "verified_success" if success else "agent_failure",
                            "verdict": {
                                "verified_success": success,
                                "failure_stage": None if success else "selection",
                            },
                            "usage": {
                                "prompt_tokens": 10,
                                "completion_tokens": 2,
                                "total_tokens": 12,
                            },
                            "response_ids": ["response"],
                            "efficiency": {
                                "model_requests_attempted": 1,
                                "tool_schema_exposures": 10 if role == "primary" else 100,
                                "tool_schema_bytes": 1000,
                                "wall_time_seconds": 1.0,
                            },
                        }
                    )
    return rows


def _discovery_rows(models: tuple[str, ...]) -> list[dict]:
    rows: list[dict] = []
    for model in models:
        for case_index in range(2):
            for retriever in ("flat", "hierarchical"):
                for registry_size in DISCOVERY_REGISTRY_SIZES:
                    success = retriever == "hierarchical" or registry_size != 2048
                    condition = {
                        "case_id": f"case-{case_index}",
                        "split": "main",
                        "model": model,
                        "harness": "routed@test",
                        "retriever": retriever,
                        "registry_size": registry_size,
                        "visible_tools": 16,
                        "seed": 1,
                    }
                    rows.append(
                        {
                            "condition": condition,
                            "condition_id": stable_hash(condition),
                            "status": "verified_success" if success else "agent_failure",
                            "success": success,
                            "exposure": {
                                "required_available": True,
                                "required_tool": f"required-tool-{case_index}",
                            },
                            "usage": {
                                "prompt_tokens": 10,
                                "completion_tokens": 2,
                                "total_tokens": 12,
                            },
                        }
                    )
    return rows


def _boundary_rows(models: tuple[str, ...]) -> list[dict]:
    rows: list[dict] = []
    for model in models:
        for family_index in range(2):
            for locator in (0, 2, 6, 14):
                success = locator == 0
                condition = {
                    "study": "workflow_capacity_frontier",
                    "task_id": f"boundary-{family_index}-{locator}",
                    "family_id": f"boundary-family-{family_index}",
                    "model": model,
                    "harness": "routed@test",
                    "locator_nodes": locator,
                }
                rows.append(
                    {
                        "condition": condition,
                        "condition_id": stable_hash(condition),
                        "status": "verified_success" if success else "agent_failure",
                        "verdict": {"verified_success": success},
                    }
                )
    return rows


class ModelGeneralizationAnalysisTest(unittest.TestCase):
    def test_full_replication_and_seven_model_analysis(self) -> None:
        original_models = ("original-a", "original-b", "original-c")
        replication_models = (
            "replication-a",
            "replication-b",
            "replication-c",
            "replication-d",
        )
        result = analyze_main_model_generalization(
            _capacity_rows(original_models),
            _discovery_rows(original_models),
            _capacity_rows(replication_models),
            _discovery_rows(replication_models),
            _boundary_rows(replication_models),
            bootstrap_samples=300,
            seed=7,
            expected_capacity_rows_per_model=None,
            expected_discovery_rows_per_model=None,
            expected_boundary_rows_per_model=None,
        )

        self.assertEqual(len(result["per_model"]), 7)
        self.assertEqual(tuple(result["named_replication_claims"]), NAMED_REPLICATION_CLAIMS)
        self.assertTrue(
            all(
                claim["supported_at_0.05"] is True
                for claim in result["named_replication_claims"].values()
            ),
            result["named_replication_claims"],
        )
        self.assertEqual(
            result["study_blocks"]["replication"]["claim_points"][
                "visible_load_loss"
            ],
            -1.0,
        )
        self.assertEqual(
            result["study_blocks"]["replication"]["claim_points"][
                "visible_workflow_floor_interaction"
            ],
            0.0,
        )
        self.assertEqual(
            result["named_replication_claims"]["workflow_boundary_loss"]["test"][
                "estimate"
            ],
            -1.0,
        )
        routing = result["study_blocks"]["replication"]["claim_points"][
            "routed_exposure_efficiency"
        ]
        self.assertEqual(routing["fixed_minus_routed_verified_success"], 0.0)
        self.assertEqual(routing["fixed_minus_routed_tool_schema_exposures"], 90.0)
        self.assertEqual(
            result["named_replication_claims"]["routed_exposure_efficiency"][
                "success_component"
            ]["clusters"],
            2,
        )
        self.assertTrue(
            all(
                summary["unanimous"] is True
                for summary in result["replication_model_sign_consistency"][
                    "summary"
                ].values()
            )
        )
        self.assertEqual(
            len(
                result["replication_leave_one_model_out"][
                    "excluded_model_estimates"
                ]
            ),
            4,
        )
        self.assertEqual(
            result["seven_model_heterogeneity"]["capacity_contrasts"][
                "visible_128_minus_8"
            ]["range"],
            0.0,
        )

    def test_named_visible_claim_uses_fixed_menu_rows_not_routed_factorial(self) -> None:
        original_models = ("original-a", "original-b", "original-c")
        replication_models = (
            "replication-a",
            "replication-b",
            "replication-c",
            "replication-d",
        )
        replication_capacity = _capacity_rows(replication_models)
        mutated_capacity = copy.deepcopy(replication_capacity)
        for row in mutated_capacity:
            if row["study_role"] == "primary":
                row["verdict"]["verified_success"] = not row["verdict"][
                    "verified_success"
                ]

        def analyze(capacity: list[dict]) -> dict:
            return analyze_main_model_generalization(
                _capacity_rows(original_models),
                _discovery_rows(original_models),
                capacity,
                _discovery_rows(replication_models),
                _boundary_rows(replication_models),
                bootstrap_samples=20,
                seed=11,
                expected_capacity_rows_per_model=None,
                expected_discovery_rows_per_model=None,
                expected_boundary_rows_per_model=None,
            )

        baseline = analyze(replication_capacity)
        mutated = analyze(mutated_capacity)
        baseline_claim = baseline["named_replication_claims"]["visible_load_loss"]
        mutated_claim = mutated["named_replication_claims"]["visible_load_loss"]
        self.assertEqual(baseline_claim["test"]["estimate"], -1.0)
        self.assertEqual(
            baseline_claim["test"]["estimate"],
            mutated_claim["test"]["estimate"],
        )
        self.assertNotEqual(
            baseline["study_blocks"]["replication"]["capacity"][
                "primary_effects"
            ]["visible_128_minus_8"]["estimate"],
            mutated["study_blocks"]["replication"]["capacity"][
                "primary_effects"
            ]["visible_128_minus_8"]["estimate"],
        )

    def test_wrong_replication_model_count_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "expected 4 shared models"):
            analyze_main_model_generalization(
                _capacity_rows(("original-a", "original-b", "original-c")),
                _discovery_rows(("original-a", "original-b", "original-c")),
                _capacity_rows(("replication-a", "replication-b", "replication-c")),
                _discovery_rows(("replication-a", "replication-b", "replication-c")),
                _boundary_rows(("replication-a", "replication-b", "replication-c")),
                bootstrap_samples=20,
                expected_capacity_rows_per_model=None,
                expected_discovery_rows_per_model=None,
                expected_boundary_rows_per_model=None,
            )


if __name__ == "__main__":
    unittest.main()
