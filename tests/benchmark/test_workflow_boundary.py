from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from dataclasses import replace
from unittest.mock import patch

from bottleneckbench.design import Split, build_families, visible_menu
from bottleneckbench.experiments import EpisodePolicy, ExecutionProfile
from bottleneckbench.runtime import _reference_answer, run_episode
from bottleneckbench.workflow_boundary import (
    WORKFLOW_BOUNDARY_LOCATOR_LEVELS,
    WORKFLOW_BOUNDARY_STUDY,
    certify_workflow_boundary_program,
    compile_workflow_boundary_task,
    reference_workflow_boundary_execution,
    workflow_boundary_static_audit,
    workflow_boundary_tasks,
)
from bottleneckbench.workflow_boundary_experiments import (
    ProviderFailureBudget,
    ProviderFailureBudgetExceeded,
    build_workflow_boundary_plan,
    execute_workflow_boundary_plan,
    freeze_workflow_boundary_plan,
    load_workflow_boundary_plan,
    workflow_boundary_conditions,
)
from bottleneckbench.workflow_boundary_analysis import (
    _row_usage_contract,
    analyze_workflow_boundary,
    workflow_boundary_development_admission,
)
from bottleneckbench.core.agnes import AgnesError, ModelRequest, ModelResponse, ToolCall
from bottleneckbench.core.schema import stable_hash


class _ProgramClient:
    def __init__(self, task) -> None:
        self.responses = [
            ModelResponse(
                model="test-model",
                content="",
                reasoning="",
                tool_calls=(
                    ToolCall(
                        call_id=f"call-{index}",
                        name=step.tool_name,
                        arguments=step.argument_map,
                    ),
                ),
                finish_reason="tool_calls",
                prompt_tokens=10,
                completion_tokens=2,
                raw_id=f"response-{index}",
            )
            for index, step in enumerate(task.program, start=1)
        ]
        self.responses.append(
            ModelResponse(
                model="test-model",
                content=_reference_answer(task),
                reasoning="",
                tool_calls=(),
                finish_reason="stop",
                prompt_tokens=10,
                completion_tokens=5,
                raw_id="response-final",
            )
        )

    def complete(self, request: ModelRequest) -> ModelResponse:
        if request.model != "test-model" or not request.tools:
            raise AssertionError("invalid workflow-boundary request")
        return self.responses.pop(0)


class _FailingClient:
    def complete(self, _request: ModelRequest) -> ModelResponse:
        raise AgnesError("synthetic provider failure")


def _execution_profile() -> ExecutionProfile:
    return ExecutionProfile(
        endpoint="https://example.invalid/v1/chat/completions",
        api_key_env="TEST_API_KEY",
        timeout_seconds=30.0,
        transport_retries=0,
        workers=1,
        provider_concurrency_cap=1,
        episode_policy=EpisodePolicy(
            name="workflow-boundary-test",
            tool_call_slack=2,
            turn_slack=2,
            max_total_tokens=100_000,
            max_completion_tokens=1024,
            dynamic_shortlist=True,
            early_stop_infeasible=True,
        ),
    )


class WorkflowBoundaryDesignTest(unittest.TestCase):
    def test_missing_usage_fails_contract(self) -> None:
        base = {
            "response_ids": ["generation-1"],
            "usage_sources": ["stream_usage_missing"],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
            "status": "verified_success",
        }
        row = {
            **base,
            "condition": {
                "study": WORKFLOW_BOUNDARY_STUDY,
                "model": "qwen/qwen3.7-flash",
            },
        }

        self.assertFalse(_row_usage_contract(row))

    def test_locator_depth_is_the_only_family_level_intervention(self) -> None:
        family = build_families(Split.DEVELOPMENT)[0]
        tasks = tuple(
            compile_workflow_boundary_task(family, level)
            for level in WORKFLOW_BOUNDARY_LOCATOR_LEVELS
        )

        self.assertEqual(len({task.semantic_signature for task in tasks}), 1)
        self.assertEqual(len({task.request for task in tasks}), 1)
        self.assertEqual(len({task.base_tool_names for task in tasks}), 1)
        evidence = [
            tuple(
                (node.connector, node.record_id, node.facts)
                for node in task.nodes
                if node.facts
            )
            for task in tasks
        ]
        self.assertEqual(len(set(evidence)), 1)
        menus = [
            tuple(tool.to_openai() for tool in visible_menu(task, 8))
            for task in tasks
        ]
        self.assertTrue(all(menu == menus[0] for menu in menus))

    def test_certified_calls_span_the_registered_boundary(self) -> None:
        negative = next(
            family
            for family in build_families(Split.DEVELOPMENT)
            if not family.action_required
        )
        positive = next(
            family
            for family in build_families(Split.DEVELOPMENT)
            if family.action_required
        )

        self.assertEqual(
            [
                certify_workflow_boundary_program(
                    compile_workflow_boundary_task(negative, level)
                ).minimum_calls
                for level in WORKFLOW_BOUNDARY_LOCATOR_LEVELS
            ],
            [2, 6, 14, 30],
        )
        self.assertEqual(
            [
                certify_workflow_boundary_program(
                    compile_workflow_boundary_task(positive, level)
                ).minimum_calls
                for level in WORKFLOW_BOUNDARY_LOCATOR_LEVELS
            ],
            [3, 7, 15, 31],
        )

    def test_exact_reference_and_live_runtime_pass_longest_task(self) -> None:
        family = next(
            family
            for family in build_families(Split.DEVELOPMENT)
            if family.action_name is None
        )
        task = compile_workflow_boundary_task(family, 14)
        condition = next(
            condition
            for condition in workflow_boundary_conditions(
                Split.DEVELOPMENT, "test-model", "react-routed@test"
            )
            if condition.task_id == task.task_id
        )

        self.assertTrue(reference_workflow_boundary_execution(task).verified_success)
        episode = run_episode(
            condition,  # type: ignore[arg-type]
            task,  # type: ignore[arg-type]
            _ProgramClient(task),
            max_turns=None,
            max_tool_calls=None,
            tool_call_slack=2,
            turn_slack=2,
        )

        self.assertTrue(episode.verdict.verified_success, episode.verdict)
        self.assertEqual(len(episode.trace), 30)
        self.assertEqual(episode.effective_max_tool_calls, 32)

    def test_static_construct_audit_passes_before_hosted_execution(self) -> None:
        for split in (Split.DEVELOPMENT, Split.MAIN):
            with self.subTest(split=split):
                audit = workflow_boundary_static_audit(split)
                self.assertTrue(audit["passed"], audit)
                self.assertEqual(len(audit["checks"]), 8)

    def test_static_construct_audit_rejects_semantic_gold_and_shortcut_tampering(self) -> None:
        original = list(workflow_boundary_tasks(Split.DEVELOPMENT))
        semantic = original.copy()
        semantic[0] = replace(semantic[0], request=semantic[0].request + " changed")

        gold = original.copy()
        gold[0] = replace(
            gold[0], request=gold[0].request + " " + gold[0].nodes[-1].record_id
        )

        shortcut = original.copy()
        target_index = next(
            index for index, task in enumerate(shortcut) if task.locator_nodes == 2
        )
        target = shortcut[target_index]
        nodes = list(target.nodes)
        nodes[0] = replace(nodes[0], next_queries=(nodes[-1].lookup_key,))
        shortcut[target_index] = replace(target, nodes=tuple(nodes))

        for label, tasks in (
            ("semantic", semantic),
            ("gold", gold),
            ("shortcut", shortcut),
        ):
            with self.subTest(label=label), patch(
                "bottleneckbench.workflow_boundary.workflow_boundary_tasks",
                return_value=tuple(tasks),
            ):
                self.assertFalse(workflow_boundary_static_audit(Split.DEVELOPMENT)["passed"])

    def test_content_addressed_plan_round_trip_and_main_gate(self) -> None:
        audit = workflow_boundary_static_audit(Split.DEVELOPMENT)
        plan = build_workflow_boundary_plan(
            split=Split.DEVELOPMENT,
            model="test-model",
            harness="react-routed@test",
            frozen_at="2026-08-12T00:00:00Z",
            protocol_artifact_hash="a" * 64,
            model_panel_hash="b" * 64,
            execution=_execution_profile(),
            static_audit=audit,
        )
        self.assertEqual(len(plan.conditions), 24)
        self.assertEqual(len({item.condition_id for item in plan.conditions}), 24)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            freeze_workflow_boundary_plan(path, plan)
            loaded = load_workflow_boundary_plan(path)
        self.assertEqual(loaded, plan)
        self.assertEqual(len(plan.plan_hash), 64)

        with self.assertRaisesRegex(ValueError, "development admission"):
            build_workflow_boundary_plan(
                split=Split.MAIN,
                model="test-model",
                harness="react-routed@test",
                frozen_at="2026-08-12T00:00:00Z",
                protocol_artifact_hash="a" * 64,
                model_panel_hash="b" * 64,
                execution=_execution_profile(),
                static_audit=workflow_boundary_static_audit(Split.MAIN),
            )

        extension_admission = {
            "audit": "workflow-capacity-frontier-extension-admission-v1",
            "passed": True,
            "admission_scope": "descriptive candidate-model extension of an already-observed frontier",
        }
        extension = build_workflow_boundary_plan(
            split=Split.MAIN,
            model="test-model",
            harness="react-routed@test",
            frozen_at="2026-08-12T00:00:00Z",
            protocol_artifact_hash="a" * 64,
            model_panel_hash="b" * 64,
            execution=_execution_profile(),
            static_audit=workflow_boundary_static_audit(Split.MAIN),
            development_admission=extension_admission,
        )
        self.assertEqual(extension.admission_audit_hash, stable_hash(extension_admission))

        self.assertEqual(stable_hash(audit), plan.static_audit_hash)

        family_ids = tuple(
            dict.fromkeys(
                condition.family_id
                for condition in workflow_boundary_conditions(
                    Split.DEVELOPMENT, "test-model", "react-routed@test"
                )
            )
        )[:2]
        subset = build_workflow_boundary_plan(
            split=Split.DEVELOPMENT,
            model="test-model",
            harness="react-routed@test",
            frozen_at="2026-08-12T00:00:00Z",
            protocol_artifact_hash="a" * 64,
            model_panel_hash="b" * 64,
            execution=_execution_profile(),
            static_audit=audit,
            family_ids=family_ids,
        )
        self.assertEqual(len(subset.conditions), 8)
        self.assertEqual(subset.family_ids, family_ids)

    def test_shared_provider_failure_budget_stops_after_gate_is_impossible(self) -> None:
        plan = build_workflow_boundary_plan(
            split=Split.DEVELOPMENT,
            model="test-model",
            harness="react-routed@test",
            frozen_at="2026-08-12T00:00:00Z",
            protocol_artifact_hash="a" * 64,
            model_panel_hash="b" * 64,
            execution=_execution_profile(),
            static_audit=workflow_boundary_static_audit(Split.DEVELOPMENT),
        )
        budget = ProviderFailureBudget(maximum_failures=0)
        with TemporaryDirectory() as directory:
            output = Path(directory) / "rows.jsonl"
            with self.assertRaisesRegex(
                ProviderFailureBudgetExceeded, "mathematically unattainable"
            ):
                execute_workflow_boundary_plan(
                    plan,
                    _FailingClient(),
                    output,
                    max_conditions=4,
                    failure_budget=budget,
                )
            rows = output.read_text(encoding="utf-8").splitlines()

        self.assertEqual(len(rows), 1)
        self.assertEqual(budget.failures, 1)
        self.assertTrue(budget.stopped)

    def test_development_admission_requires_a_solvable_active_frontier(self) -> None:
        audit = workflow_boundary_static_audit(Split.DEVELOPMENT)
        plans = tuple(
            build_workflow_boundary_plan(
                split=Split.DEVELOPMENT,
                model=f"test-model-{index}",
                harness="react-routed@test",
                frozen_at="2026-08-12T00:00:00Z",
                protocol_artifact_hash="a" * 64,
                model_panel_hash="b" * 64,
                execution=_execution_profile(),
                static_audit=audit,
            )
            for index in range(4)
        )
        rows = []
        for model_index, plan in enumerate(plans):
            for condition in plan.conditions:
                success = not (
                    model_index < 2 and condition.locator_nodes >= 4
                )
                rows.append(
                    {
                        "condition": condition.to_json(),
                        "condition_id": condition.condition_id,
                        "plan_hash": plan.plan_hash,
                        "status": "verified_success" if success else "agent_failure",
                        "verdict": {
                            "verified_success": success,
                            "failure_stage": None if success else "workflow",
                        },
                        "usage": {
                            "prompt_tokens": 100,
                            "completion_tokens": 10,
                            "total_tokens": 110,
                        },
                        "response_ids": ["response-id"],
                        "efficiency": {
                            "tool_schema_bytes": 1000,
                            "wall_time_seconds": 1.0,
                        },
                    }
                )

        admission = workflow_boundary_development_admission(plans, rows)
        analysis = analyze_workflow_boundary(rows, bootstrap_draws=200)

        self.assertTrue(admission["passed"], admission)
        active = next(
            check
            for check in admission["checks"]
            if check["name"] == "workflow_frontier_activation"
        )
        self.assertEqual(len(active["active_models"]), 2)
        self.assertEqual(analysis["row_count"], 96)
        self.assertEqual(len(analysis["paired_contrasts"]), 3)
        self.assertEqual(len(analysis["capacity_boundaries"]), 4)


if __name__ == "__main__":
    unittest.main()
