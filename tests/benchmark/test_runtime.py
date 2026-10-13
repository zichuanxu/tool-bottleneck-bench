from __future__ import annotations

import json
import unittest

from bottleneckbench.design import (
    CapacityCondition,
    Split,
    WorkflowLoad,
    build_families,
    build_world,
    compile_task,
    visible_menu,
)
from bottleneckbench.runtime import (
    _system_prompt,
    _reference_answer,
    compile_visible_observation,
    efficiency_policy_audit,
    evaluate_episode,
    execute_capacity_tool,
    reference_execution,
    run_episode,
    runtime_construct_audit,
)
from bottleneckbench.core.agnes import ModelRequest, ModelResponse, ToolCall
from bottleneckbench.core.evaluation import Budget
from bottleneckbench.core.schema import Representation, RunStatus


class _ProgramClient:
    def __init__(self, task) -> None:
        self.request_tool_counts = []
        self.request_max_tokens = []
        self.request_seeds = []
        self.responses = []
        for index, step in enumerate(task.program, start=1):
            self.responses.append(
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
            )
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
        self.assert_request(request)
        self.request_tool_counts.append(len(request.tools))
        self.request_max_tokens.append(request.max_tokens)
        self.request_seeds.append(request.seed)
        return self.responses.pop(0)

    @staticmethod
    def assert_request(request: ModelRequest) -> None:
        if not request.tools or request.model != "test-model":
            raise AssertionError("invalid normalized model request")


class _WrongFirstCallClient:
    def __init__(self, task) -> None:
        self.task = task
        self.calls = 0

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        step = self.task.program[0]
        arguments = {**step.argument_map, "query": "definitely-not-the-case"}
        return ModelResponse(
            model="test-model",
            content="",
            reasoning="",
            tool_calls=(ToolCall("wrong-call", step.tool_name, arguments),),
            finish_reason="tool_calls",
            prompt_tokens=10,
            completion_tokens=2,
            raw_id="wrong-response",
        )


class _MalformedResponseClient:
    def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            model=request.model,
            content="",
            reasoning="",
            tool_calls=(),
            finish_reason="tool_calls",
            prompt_tokens=31,
            completion_tokens=7,
            raw_id="malformed-response",
            response_errors=("tool_call_0_arguments_invalid_json",),
        )


class _ParallelProgramClient:
    def __init__(self, task) -> None:
        self.task = task
        self.calls = 0

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self.calls == 1:
            return ModelResponse(
                model="test-model",
                content="",
                reasoning="",
                tool_calls=tuple(
                    ToolCall(
                        call_id=f"call-{index}",
                        name=step.tool_name,
                        arguments=step.argument_map,
                        extra_content=(
                            {"google": {"thought_signature": "signature"}}
                            if index == 1
                            else None
                        ),
                    )
                    for index, step in enumerate(self.task.program, start=1)
                ),
                finish_reason="tool_calls",
                prompt_tokens=10,
                completion_tokens=5,
                raw_id="parallel-response",
            )
        self.assert_grouped_history(request)
        return ModelResponse(
            model="test-model",
            content=_reference_answer(self.task),
            reasoning="",
            tool_calls=(),
            finish_reason="stop",
            prompt_tokens=10,
            completion_tokens=5,
            raw_id="final-response",
        )

    @staticmethod
    def assert_grouped_history(request: ModelRequest) -> None:
        assistant_messages = [
            message for message in request.messages if message.get("role") == "assistant"
        ]
        if len(assistant_messages) != 1:
            raise AssertionError("provider tool-call batch was split")
        calls = assistant_messages[0].get("tool_calls", [])
        if len(calls) != 2:
            raise AssertionError("provider tool-call batch was not preserved")
        if calls[0].get("extra_content") != {
            "google": {"thought_signature": "signature"}
        }:
            raise AssertionError("provider tool-call metadata was not preserved")
        tool_messages = [
            message for message in request.messages if message.get("role") == "tool"
        ]
        if [message.get("tool_call_id") for message in tool_messages] != [
            call["id"] for call in calls
        ]:
            raise AssertionError("tool results do not preserve batch order")


class RuntimeTest(unittest.TestCase):
    def test_deliberate_harness_changes_the_multistep_policy(self) -> None:
        direct = _system_prompt("react-v1@vertex-openai")
        deliberate = _system_prompt("deliberate-verify-v1@vertex-openai")
        interface_guide = _system_prompt("interface-guide-v1@vertex-openai")

        self.assertNotEqual(direct, deliberate)
        self.assertNotEqual(direct, interface_guide)
        self.assertNotEqual(deliberate, interface_guide)
        self.assertIn("privately verify", deliberate)
        self.assertIn("Provenance", interface_guide)
        self.assertIn("relation_dictionary", interface_guide)
        self.assertNotIn("required_tools", interface_guide)

    def test_reference_programs_pass_every_development_cell(self) -> None:
        for family in build_families(Split.DEVELOPMENT):
            for level in WorkflowLoad:
                task = compile_task(family, level)
                with self.subTest(task=task.task_id):
                    self.assertTrue(reference_execution(task)[3].verified_success)

    def test_branch_alternative_order_is_valid_without_trace_equality(self) -> None:
        family = next(item for item in build_families(Split.DEVELOPMENT) if item.branching)
        task = compile_task(family, WorkflowLoad.HIGH)
        steps = list(task.program)
        # Swap the two independent search/get branch blocks.
        alternative = (*steps[:2], *steps[4:6], *steps[2:4], *steps[6:])

        verdict = reference_execution(task, program=alternative)[3]

        self.assertTrue(verdict.verified_success, verdict)

    def test_exact_evaluator_rejects_missing_step_wrong_answer_and_unsafe_state(self) -> None:
        task = compile_task(build_families(Split.DEVELOPMENT)[0], WorkflowLoad.HIGH)
        initial, final, trace, reference = reference_execution(task)
        self.assertTrue(reference.verified_success)

        missing, _ = evaluate_episode(
            task,
            initial,
            final,
            trace[:-1],
            Budget(10, len(trace) - 1, 10000, 100),
            answer=_reference_answer(task),
        )
        payload = json.loads(_reference_answer(task))
        payload["decision"] = "wrong"
        wrong, _ = evaluate_episode(
            task,
            initial,
            final,
            trace,
            Budget(10, len(trace), 10000, 100),
            answer=json.dumps(payload),
        )
        unsafe_final = final.clone()
        node = task.nodes[0]
        unsafe_final.records[node.connector][node.record_id]["tampered"] = True
        unsafe, _ = evaluate_episode(
            task,
            initial,
            unsafe_final,
            trace,
            Budget(10, len(trace), 10000, 100),
            answer=_reference_answer(task),
        )

        self.assertFalse(missing.verified_success)
        self.assertFalse(missing.workflow)
        self.assertFalse(wrong.verified_success)
        self.assertFalse(wrong.answer)
        self.assertFalse(unsafe.verified_success)
        self.assertFalse(unsafe.safety)

    def test_answer_parser_accepts_one_schema_object_but_rejects_ambiguity(self) -> None:
        task = compile_task(build_families(Split.DEVELOPMENT)[0], WorkflowLoad.LOW)
        initial, final, trace, _ = reference_execution(task)
        answer = _reference_answer(task)
        budget = Budget(10, len(trace), 10000, 100)

        wrapped, _ = evaluate_episode(
            task,
            initial,
            final,
            trace,
            budget,
            answer=f"Result:\n```json\n{answer}\n```",
        )
        ambiguous, _ = evaluate_episode(
            task,
            initial,
            final,
            trace,
            budget,
            answer=f"{answer}\n{answer}",
        )

        self.assertTrue(wrapped.verified_success, wrapped)
        self.assertFalse(ambiguous.answer)
        self.assertIn("answer_not_json", ambiguous.errors)

    def test_compact_delivery_includes_custom_field_dictionary(self) -> None:
        task = compile_task(build_families(Split.DEVELOPMENT)[0], WorkflowLoad.LOW)
        world = build_world(task)
        menu = {tool.name: tool for tool in visible_menu(task, 8)}
        search, get = task.program[:2]
        search_result = execute_capacity_tool(world, task, menu[search.tool_name], search.argument_map)
        get_result = execute_capacity_tool(world, task, menu[get.tool_name], get.argument_map)
        seen = {
            get.connector: {
                str(get_result.output["record"]["record_id"]): get_result.output["record"]
            }
        }

        observation = compile_visible_observation(
            get_result,
            get.connector,
            seen,
            Representation.GENERIC_COMPACT,
            world.snapshot_hash,
        )

        self.assertTrue(search_result.ok)
        self.assertEqual(observation["field_dictionary"]["u_commitment_date"], "commitment_date")
        self.assertEqual(observation["field_dictionary"]["co"], "contact")
        self.assertTrue(observation["provenance"])

    def test_core_search_miss_returns_empty_result_without_state_change(self) -> None:
        task = compile_task(build_families(Split.DEVELOPMENT)[0], WorkflowLoad.MEDIUM)
        world = build_world(task)
        menu = {tool.name: tool for tool in visible_menu(task, 8)}
        unused = next(
            tool
            for tool in menu.values()
            if tool.name.startswith("search_")
            and tool.connector not in {node.connector for node in task.nodes}
        )
        before = world.snapshot_hash

        result = execute_capacity_tool(world, task, unused, {"query": task.family.case_id})

        self.assertTrue(result.ok)
        self.assertEqual(result.output, {"records": []})
        self.assertEqual(world.snapshot_hash, before)

    def test_negative_action_case_keeps_action_tool_visible_but_requires_no_mutation(self) -> None:
        family = next(
            item
            for item in build_families(Split.DEVELOPMENT)
            if item.action_name and not item.action_required
        )
        task = compile_task(family, WorkflowLoad.HIGH)

        self.assertIsNone(task.action)
        self.assertIn(family.action_name, task.base_tool_names)
        self.assertTrue(reference_execution(task)[3].verified_success)
        self.assertEqual(len(task.program), 6)

    def test_run_episode_uses_normalized_client_and_passes_reference_behavior(self) -> None:
        task = compile_task(build_families(Split.DEVELOPMENT)[0], WorkflowLoad.LOW)
        condition = CapacityCondition(
            task_id=task.task_id,
            family_id=task.family.family_id,
            intent=task.family.intent,
            outcome_variant=task.family.outcome_variant,
            prompt_variant=task.family.prompt_variant,
            branching=task.family.branching,
            action_required=task.family.action_required,
            model="test-model",
            harness="react",
            workflow=task.workflow,
            representation=Representation.TYPED,
            visible_tools=8,
            seed=task.family.seed,
        )

        client = _ProgramClient(task)
        episode = run_episode(condition, task, client, inference_seed=98765)

        self.assertEqual(episode.status, RunStatus.VERIFIED_SUCCESS)
        self.assertTrue(episode.verdict.verified_success)
        self.assertEqual(len(episode.trace), 2)
        self.assertEqual(episode.total_tokens, 39)
        self.assertEqual(set(client.request_seeds), {98765})

    def test_malformed_native_arguments_preserve_telemetry_as_agent_failure(self) -> None:
        task = compile_task(build_families(Split.DEVELOPMENT)[0], WorkflowLoad.LOW)
        condition = CapacityCondition(
            task_id=task.task_id,
            family_id=task.family.family_id,
            intent=task.family.intent,
            outcome_variant=task.family.outcome_variant,
            prompt_variant=task.family.prompt_variant,
            branching=task.family.branching,
            action_required=task.family.action_required,
            model="test-model",
            harness="react",
            workflow=task.workflow,
            representation=Representation.RAW,
            visible_tools=8,
            seed=task.family.seed,
        )

        episode = run_episode(condition, task, _MalformedResponseClient())

        self.assertEqual(episode.status, RunStatus.AGENT_FAILURE)
        self.assertEqual(episode.total_tokens, 38)
        self.assertEqual(episode.response_ids, ("malformed-response",))
        self.assertEqual(episode.response_models, ("test-model",))
        self.assertIn(
            "model_response_contract:tool_call_0_arguments_invalid_json",
            episode.errors,
        )
        self.assertEqual(episode.verdict.failure_stage, "selection")

    def test_adaptive_policy_shortlists_after_route_selection(self) -> None:
        task = compile_task(build_families(Split.DEVELOPMENT)[0], WorkflowLoad.LOW)
        condition = CapacityCondition(
            task_id=task.task_id,
            family_id=task.family.family_id,
            intent=task.family.intent,
            outcome_variant=task.family.outcome_variant,
            prompt_variant=task.family.prompt_variant,
            branching=task.family.branching,
            action_required=task.family.action_required,
            model="test-model",
            harness="react",
            workflow=task.workflow,
            representation=Representation.RAW,
            visible_tools=128,
            seed=task.family.seed,
        )
        client = _ProgramClient(task)

        episode = run_episode(
            condition,
            task,
            client,
            max_turns=None,
            max_tool_calls=None,
            tool_call_slack=2,
            turn_slack=2,
            max_total_tokens=128000,
            max_completion_tokens=1024,
            dynamic_shortlist=True,
            early_stop_infeasible=True,
        )

        self.assertTrue(episode.verdict.verified_success, episode.errors)
        self.assertEqual(client.request_tool_counts[0], 128)
        self.assertEqual(
            client.request_tool_counts[1:],
            [len(task.base_tool_names)] * len(task.program),
        )
        self.assertEqual(client.request_max_tokens, [1024] * (len(task.program) + 1))
        self.assertEqual(episode.effective_max_tool_calls, len(task.program) + 2)
        self.assertEqual(episode.effective_max_turns, len(task.program) + 4)
        self.assertLess(
            episode.request_tool_schema_bytes[1],
            episode.request_tool_schema_bytes[0],
        )

    def test_fixed_policy_preserves_peak_menu_on_every_request(self) -> None:
        task = compile_task(build_families(Split.DEVELOPMENT)[0], WorkflowLoad.LOW)
        condition = CapacityCondition(
            task_id=task.task_id,
            family_id=task.family.family_id,
            intent=task.family.intent,
            outcome_variant=task.family.outcome_variant,
            prompt_variant=task.family.prompt_variant,
            branching=task.family.branching,
            action_required=task.family.action_required,
            model="test-model",
            harness="react",
            workflow=task.workflow,
            representation=Representation.RAW,
            visible_tools=128,
            seed=task.family.seed,
        )
        client = _ProgramClient(task)

        episode = run_episode(
            condition,
            task,
            client,
            max_turns=None,
            max_tool_calls=None,
            dynamic_shortlist=False,
        )

        self.assertTrue(episode.verdict.verified_success, episode.errors)
        self.assertEqual(client.request_tool_counts, [128] * (len(task.program) + 1))

    def test_infeasible_success_stops_before_wasting_remaining_requests(self) -> None:
        task = compile_task(build_families(Split.DEVELOPMENT)[0], WorkflowLoad.LOW)
        condition = CapacityCondition(
            task_id=task.task_id,
            family_id=task.family.family_id,
            intent=task.family.intent,
            outcome_variant=task.family.outcome_variant,
            prompt_variant=task.family.prompt_variant,
            branching=task.family.branching,
            action_required=task.family.action_required,
            model="test-model",
            harness="react",
            workflow=task.workflow,
            representation=Representation.RAW,
            visible_tools=128,
            seed=task.family.seed,
        )
        client = _WrongFirstCallClient(task)

        episode = run_episode(
            condition,
            task,
            client,
            max_turns=None,
            max_tool_calls=None,
            tool_call_slack=0,
            dynamic_shortlist=True,
            early_stop_infeasible=True,
        )

        self.assertEqual(client.calls, 1)
        self.assertEqual(
            episode.early_stop_reason,
            "success_infeasible_under_call_budget",
        )
        self.assertFalse(episode.verdict.verified_success)

    def test_run_episode_preserves_parallel_provider_batch(self) -> None:
        task = compile_task(build_families(Split.DEVELOPMENT)[0], WorkflowLoad.LOW)
        condition = CapacityCondition(
            task_id=task.task_id,
            family_id=task.family.family_id,
            intent=task.family.intent,
            outcome_variant=task.family.outcome_variant,
            prompt_variant=task.family.prompt_variant,
            branching=task.family.branching,
            action_required=task.family.action_required,
            model="test-model",
            harness="react",
            workflow=task.workflow,
            representation=Representation.RAW,
            visible_tools=8,
            seed=task.family.seed,
        )

        episode = run_episode(condition, task, _ParallelProgramClient(task))

        self.assertTrue(episode.verdict.verified_success, episode.errors)
        self.assertEqual(
            [attempt.outcome for attempt in episode.call_attempts],
            ["executed"] * len(task.program),
        )

    def test_parallel_batch_records_every_call_at_budget_boundary(self) -> None:
        task = compile_task(build_families(Split.DEVELOPMENT)[0], WorkflowLoad.LOW)
        condition = CapacityCondition(
            task_id=task.task_id,
            family_id=task.family.family_id,
            intent=task.family.intent,
            outcome_variant=task.family.outcome_variant,
            prompt_variant=task.family.prompt_variant,
            branching=task.family.branching,
            action_required=task.family.action_required,
            model="test-model",
            harness="react",
            workflow=task.workflow,
            representation=Representation.RAW,
            visible_tools=8,
            seed=task.family.seed,
        )

        episode = run_episode(
            condition,
            task,
            _ParallelProgramClient(task),
            max_tool_calls=1,
        )

        self.assertEqual(episode.status, RunStatus.BUDGET_FAILURE)
        self.assertEqual(
            [attempt.outcome for attempt in episode.call_attempts],
            ["executed", "budget_blocked"],
        )

    def test_repeated_read_executes_and_can_recover_within_budget(self) -> None:
        task = compile_task(build_families(Split.DEVELOPMENT)[0], WorkflowLoad.LOW)
        condition = CapacityCondition(
            task_id=task.task_id,
            family_id=task.family.family_id,
            intent=task.family.intent,
            outcome_variant=task.family.outcome_variant,
            prompt_variant=task.family.prompt_variant,
            branching=task.family.branching,
            action_required=task.family.action_required,
            model="test-model",
            harness="react",
            workflow=task.workflow,
            representation=Representation.RAW,
            visible_tools=8,
            seed=task.family.seed,
        )
        client = _ProgramClient(task)
        first = task.program[0]
        client.responses.insert(
            1,
            ModelResponse(
                model="test-model",
                content="",
                reasoning="",
                tool_calls=(
                    ToolCall(
                        call_id="call-repeat",
                        name=first.tool_name,
                        arguments=first.argument_map,
                    ),
                ),
                finish_reason="tool_calls",
                prompt_tokens=10,
                completion_tokens=2,
                raw_id="response-repeat",
            ),
        )

        episode = run_episode(condition, task, client)

        self.assertEqual(episode.status, RunStatus.VERIFIED_SUCCESS)
        self.assertTrue(episode.verdict.completion)
        self.assertIsNone(episode.verdict.failure_stage)
        self.assertEqual(
            [attempt.outcome for attempt in episode.call_attempts],
            ["executed", "executed", "executed"],
        )
        self.assertEqual(len(episode.trace), 3)

    def test_runtime_construct_audit_passes_without_active_range_claim(self) -> None:
        audit = runtime_construct_audit(Split.DEVELOPMENT)

        self.assertTrue(audit["passed"], audit)
        self.assertEqual(
            {check["name"] for check in audit["checks"]},
            {
                "query_independence",
                "information_equivalence",
                "delivery",
                "benign_search_miss",
                "safe_executable_route_neighbors",
                "exact_evaluation",
                "protocol_validity",
                "reference_solvability",
                "public_handoff_contract",
                "harness_policy_distinction",
            },
        )

    def test_efficiency_policy_audit_preserves_construct_and_reduces_exposure(self) -> None:
        audit = efficiency_policy_audit(Split.DEVELOPMENT)

        self.assertTrue(audit["passed"], audit)
        checks = {check["name"]: check for check in audit["checks"]}
        self.assertGreaterEqual(
            checks["routed_exposure_reduction"]["details"]["minimum_reduction_fraction"],
            0.60,
        )
        self.assertTrue(checks["peak_load_intervention_preserved"]["passed"])


if __name__ == "__main__":
    unittest.main()
