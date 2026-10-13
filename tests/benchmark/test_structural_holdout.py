"""Deterministic checks for the structural repair holdout controller."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from bottleneckbench.core.agnes import ModelRequest, ModelResponse, ToolCall
from bottleneckbench.core.schema import Representation, canonical_json, stable_hash
from bottleneckbench.design import (
    CapacityCondition,
    WorkflowLoad,
    build_world,
    visible_menu,
)
from bottleneckbench.runtime import (
    _remember_records,
    compile_visible_observation,
    execute_capacity_tool,
    run_episode,
)
from bottleneckbench import structural_holdout as holdout


CONTROLLER_CALL_PREFIX = "structural-controller-"
RELEASED_PLAN = (
    Path(__file__).resolve().parents[2]
    / "results/prospective-plans/structural-repair-holdout/plan.redacted.json"
)


class _FirstCallThenAnswerClient:
    """A model that issues only the entry call and the final answer JSON."""

    def __init__(self, task: holdout.StructuralTask) -> None:
        self.task = task
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            step = self.task.program[0]
            return ModelResponse(
                model=request.model,
                content="",
                reasoning="",
                tool_calls=(ToolCall("stub-entry", step.tool_name, step.argument_map),),
                finish_reason="tool_calls",
                prompt_tokens=1,
                completion_tokens=1,
                raw_id="stub-provider-1",
            )
        return ModelResponse(
            model=request.model,
            content=canonical_json(
                {
                    "decision": self.task.family.decision,
                    "facts": self.task.expected_fact_map,
                    "sources": [
                        node.record_id for node in self.task.nodes if node.facts
                    ],
                }
            ),
            reasoning="",
            tool_calls=(),
            finish_reason="stop",
            prompt_tokens=1,
            completion_tokens=1,
            raw_id="stub-provider-2",
        )


def _condition(task: holdout.StructuralTask) -> CapacityCondition:
    return CapacityCondition(
        task_id=task.task_id,
        family_id=task.family.family_id,
        intent=task.family.intent,
        outcome_variant=task.family.outcome_variant,
        prompt_variant=task.family.prompt_variant,
        branching=False,
        action_required=False,
        model="offline/stub",
        harness="react-routed@offline",
        workflow=WorkflowLoad.HIGH,
        representation=Representation.RAW,
        visible_tools=8,
        seed=task.family.seed,
    )


def _tasks_by_calls(calls: int) -> tuple[holdout.StructuralTask, ...]:
    return tuple(
        task for task in holdout.structural_tasks() if task.certified_calls == calls
    )


class StructuralCompileTest(unittest.TestCase):
    def test_population_is_the_frozen_inventory(self) -> None:
        families = holdout.structural_families()
        tasks = holdout.structural_tasks()
        self.assertEqual(len(families), 24)
        self.assertEqual(len(tasks), 48)
        self.assertEqual(len({family.intent for family in families}), 24)
        self.assertEqual(sorted({task.certified_calls for task in tasks}), [2, 30])

    def test_compilation_is_deterministic(self) -> None:
        first = holdout.structural_tasks()
        second = holdout.structural_tasks()
        self.assertEqual(
            [task.task_id for task in first], [task.task_id for task in second]
        )
        self.assertEqual(
            [task.task_hash for task in first], [task.task_hash for task in second]
        )
        for family in holdout.structural_families():
            recompiled = [
                holdout.compile_structural_task(family, calls)
                for calls in holdout.CALL_LEVELS
                for _ in range(2)
            ]
            self.assertEqual(recompiled[0].hidden_payload(), recompiled[1].hidden_payload())
            self.assertEqual(recompiled[2].hidden_payload(), recompiled[3].hidden_payload())

    def test_conditions_are_deterministic_and_complete(self) -> None:
        first = holdout.structural_conditions("offline/stub", "react-routed@offline")
        second = holdout.structural_conditions("offline/stub", "react-routed@offline")
        self.assertEqual(len(first), 24 * len(holdout.CELL_SPECS))
        self.assertEqual([item.condition_id for item in first], [item.condition_id for item in second])
        self.assertEqual(len({item.condition_id for item in first}), len(first))


class StructuralStaticAuditTest(unittest.TestCase):
    def test_static_audit_passes_and_is_stable(self) -> None:
        first = holdout.static_audit()
        self.assertTrue(first["passed"])
        self.assertEqual(first["intent_rules"], 24)
        self.assertEqual(first["tasks"], 48)
        self.assertEqual(first["certified_call_levels"], [2, 30])
        self.assertEqual(
            first["controller_reference"],
            {
                "certified_calls": 30,
                "controller_calls": 29,
                "provider_calls": 2,
                "hidden_program_mutation_invariant": True,
                "public_handoff_mutation_sensitive": True,
                "invalid_entry_fail_closed": True,
            },
        )
        self.assertEqual(holdout.static_audit()["task_manifest_hash"], first["task_manifest_hash"])

    def test_audit_matches_the_released_plan(self) -> None:
        """The audit the release froze is the audit this library still produces."""

        frozen = json.loads(RELEASED_PLAN.read_text(encoding="utf-8"))["construct_audit"]
        live = holdout.static_audit()
        self.assertEqual(live, frozen)
        self.assertEqual(frozen["controller_reference"]["controller_calls"], 29)
        self.assertEqual(frozen["controller_reference"]["provider_calls"], 2)

    def test_three_controller_audits_pass_on_every_unseen_intent(self) -> None:
        tasks = _tasks_by_calls(30)
        self.assertEqual(len(tasks), 24)
        for task in tasks:
            with self.subTest(task=task.task_id):
                result = holdout._controller_audit(task)
                self.assertTrue(result["hidden_program_mutation_invariant"])
                self.assertTrue(result["public_handoff_mutation_sensitive"])
                self.assertTrue(result["invalid_entry_fail_closed"])
                self.assertEqual(result["certified_calls"], 30)
                self.assertEqual(result["controller_calls"], 29)
                self.assertEqual(result["provider_calls"], 2)


class PublicHandoffControllerTest(unittest.TestCase):
    def test_controller_issues_the_certified_remaining_calls(self) -> None:
        task = _tasks_by_calls(30)[0]
        stub = _FirstCallThenAnswerClient(task)
        meter = holdout.ProviderCallLog(stub)
        controller = holdout.TraceStateClient(meter)
        episode = meter.normalize(
            run_episode(
                _condition(task),
                task,
                controller,
                max_turns=None,
                max_tool_calls=None,
                tool_call_slack=0,
                turn_slack=2,
                max_total_tokens=1_000_000,
                max_completion_tokens=2048,
                dynamic_shortlist=True,
                early_stop_infeasible=True,
            )
        )

        self.assertTrue(episode.verdict.verified_success, episode.errors)
        self.assertEqual(len(episode.trace), 30)
        self.assertEqual(controller.controller_calls, 29)
        self.assertEqual(len(stub.requests), 2)
        self.assertEqual(len(episode.response_ids), 2)

        issued = [attempt.call_id for attempt in episode.call_attempts]
        self.assertEqual(len(issued), 30)
        controller_issued = [
            call_id for call_id in issued if call_id.startswith(CONTROLLER_CALL_PREFIX)
        ]
        self.assertEqual(len(controller_issued), 29)
        self.assertFalse(issued[0].startswith(CONTROLLER_CALL_PREFIX))
        self.assertEqual(
            [attempt.tool for attempt in episode.call_attempts],
            [step.tool_name for step in task.program],
        )
        self.assertEqual(
            {attempt.outcome for attempt in episode.call_attempts}, {"executed"}
        )

    def test_baseline_arm_needs_one_provider_call_per_certified_call(self) -> None:
        task = _tasks_by_calls(2)[0]
        stub = holdout._SequentialAuditClient(task)
        meter = holdout.ProviderCallLog(stub)
        episode = meter.normalize(
            run_episode(
                _condition(task),
                task,
                meter,
                max_turns=None,
                max_tool_calls=None,
                tool_call_slack=0,
                turn_slack=2,
                max_total_tokens=1_000_000,
                max_completion_tokens=2048,
                dynamic_shortlist=True,
                early_stop_infeasible=True,
            )
        )
        self.assertTrue(episode.verdict.verified_success, episode.errors)
        self.assertEqual(len(episode.trace), 2)
        self.assertEqual(len(episode.response_ids), 3)
        self.assertFalse(
            [
                attempt.call_id
                for attempt in episode.call_attempts
                if attempt.call_id.startswith(CONTROLLER_CALL_PREFIX)
            ]
        )


class CanonicalDeliveryTest(unittest.TestCase):
    def _observations(self, task: holdout.StructuralTask) -> list[tuple[str, str]]:
        world = build_world(task)
        menu = {tool.name: tool for tool in visible_menu(task, 8)}
        seen: dict[str, dict[str, object]] = {}
        pairs: list[tuple[str, str]] = []
        for step in task.program:
            tool = menu[step.tool_name]
            result = execute_capacity_tool(world, task, tool, step.argument_map)
            _remember_records(seen, tool.connector, result)
            rendered = []
            for representation in (Representation.RAW, Representation.GENERIC_COMPACT):
                observation = compile_visible_observation(
                    result, tool.connector, seen, representation, world.snapshot_hash
                )
                observation["ok"] = True
                rendered.append(canonical_json(observation))
            pairs.append((rendered[0], rendered[1]))
        return pairs

    def test_canonical_adapter_is_lossless_across_representations(self) -> None:
        task = _tasks_by_calls(2)[0]
        pairs = self._observations(task)
        self.assertTrue(pairs)
        for raw, compact in pairs:
            self.assertNotEqual(raw, compact)
            adapted_raw = holdout.canonicalize_observation(raw)
            adapted_compact = holdout.canonicalize_observation(compact)
            self.assertEqual(adapted_raw, adapted_compact)
            self.assertEqual(
                holdout.canonical_source_facts(adapted_raw),
                holdout.canonical_source_facts(adapted_compact),
            )
            source = json.loads(raw)
            facts = holdout.canonical_source_facts(adapted_raw)
            for item in source["objects"]:
                key = (str(item["connector"]), str(item["payload"]["record_id"]))
                self.assertIn(key, facts)
                self.assertEqual(facts[key], item["payload"])

    def test_delivery_client_rewrites_only_tool_messages(self) -> None:
        task = _tasks_by_calls(2)[0]
        raw, _ = self._observations(task)[0]
        captured: list[ModelRequest] = []

        class _Capture:
            def complete(self, request: ModelRequest) -> ModelResponse:
                captured.append(request)
                return ModelResponse(
                    model=request.model,
                    content="",
                    reasoning="",
                    tool_calls=(),
                    finish_reason="stop",
                    prompt_tokens=0,
                    completion_tokens=0,
                    raw_id="capture",
                )

        request = ModelRequest(
            model="offline/stub",
            messages=(
                {"role": "system", "content": "offline"},
                {"role": "user", "content": task.request},
                {
                    "role": "tool",
                    "name": task.program[0].tool_name,
                    "tool_call_id": "x",
                    "content": raw,
                },
            ),
            tools=(),
        )
        holdout.CanonicalDeliveryClient(_Capture()).complete(request)
        delivered = captured[0].messages
        self.assertEqual(delivered[:2], request.messages[:2])
        self.assertEqual(
            delivered[2]["content"], holdout.canonicalize_observation(raw)
        )
        self.assertEqual(
            holdout.canonical_source_facts(delivered[2]["content"]),
            holdout.canonical_source_facts(holdout.canonicalize_observation(raw)),
        )


if __name__ == "__main__":
    unittest.main()
