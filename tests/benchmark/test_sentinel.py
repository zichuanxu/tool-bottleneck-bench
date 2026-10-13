from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
import tempfile
import unittest

from bottleneckbench.experiments import EpisodePolicy, ExecutionProfile
from bottleneckbench.runtime import _reference_answer
from bottleneckbench.sentinel import (
    analyze_sentinel_rows,
    build_sentinel_plan,
    execute_sentinel_plan,
    sentinel_conditions,
    sentinel_construct_audit,
    sentinel_tasks,
    validate_sentinel_plan,
)
from bottleneckbench.core.agnes import ModelRequest, ModelResponse, ToolCall
from bottleneckbench.core.schema import stable_hash


HARNESSES = {
    "routed": "react-routed@test",
    "fixed": "react-fixed-menu@test",
    "verification": "deliberate-verify-v1@test",
    "interface": "interface-guide-v1@test",
}
EXECUTION = ExecutionProfile(
    endpoint="https://example.test/v1/chat/completions",
    api_key_env="TEST_API_KEY",
    timeout_seconds=30,
    transport_retries=1,
    workers=2,
    provider_concurrency_cap=2,
    episode_policy=EpisodePolicy(
        name="task-adaptive-routed-v1",
        tool_call_slack=2,
        turn_slack=2,
        max_total_tokens=128000,
        max_completion_tokens=1024,
        dynamic_shortlist=True,
        early_stop_infeasible=True,
    ),
)


class _ReferenceClient:
    def __init__(self) -> None:
        self.tasks = {}
        for task in sentinel_tasks():
            current = self.tasks.get(task.request)
            if current is None or len(task.program) < len(current.program):
                self.tasks[task.request] = task
        self.seeds: list[int | None] = []
        self.lock = Lock()

    def complete(self, request: ModelRequest) -> ModelResponse:
        task = self.tasks[str(request.messages[1]["content"])]
        step = sum(message.get("role") == "assistant" for message in request.messages)
        with self.lock:
            self.seeds.append(request.seed)
        if step < len(task.program):
            program = task.program[step]
            calls = (ToolCall(f"{task.task_id}-{step}", program.tool_name, program.argument_map),)
            content = ""
            finish = "tool_calls"
        else:
            calls = ()
            content = _reference_answer(task)
            finish = "stop"
        return ModelResponse(
            model="test-model",
            content=content,
            reasoning="",
            tool_calls=calls,
            finish_reason=finish,
            prompt_tokens=10,
            completion_tokens=2,
            raw_id=f"{task.task_id}-{request.seed}-{step}",
        )


class SentinelTest(unittest.TestCase):
    def test_construct_and_condition_matrix_are_closed(self) -> None:
        audit = sentinel_construct_audit()
        conditions = sentinel_conditions("test-model", HARNESSES)
        self.assertTrue(audit["passed"], audit)
        self.assertEqual(len(conditions), 72)
        self.assertEqual(len({item["condition_id"] for item in conditions}), 72)

    def test_plan_executes_with_fixed_world_and_separate_sampling_seed(self) -> None:
        audit = sentinel_construct_audit()
        plan = build_sentinel_plan(
            model="test-model",
            harnesses=HARNESSES,
            frozen_at="2026-08-13T00:00:00Z",
            protocol_artifact_hash=stable_hash("protocol"),
            model_panel_hash=stable_hash("panel"),
            execution=EXECUTION,
            construct_audit=audit,
            transport_artifact_hashes={"contract": stable_hash("contract")},
        )
        validate_sentinel_plan(plan)
        client = _ReferenceClient()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "rows.jsonl"
            result = execute_sentinel_plan(plan, client, output, max_conditions=2)
            rows = [json.loads(line) for line in output.read_text().splitlines()]
        self.assertEqual(result["executed"], 2)
        self.assertTrue(all(row["verdict"]["verified_success"] for row in rows))
        self.assertEqual({row["condition"]["seed"] for row in rows}, {901})
        self.assertEqual({row["condition"]["inference_seed"] for row in rows}, set(client.seeds))

    def test_analysis_reports_repair_gain_and_zero_disagreement(self) -> None:
        rows = [
            {
                "condition": condition,
                "verdict": {"verified_success": condition["arm"] == "repair"},
            }
            for condition in sentinel_conditions("test-model", HARNESSES)
        ]
        result = analyze_sentinel_rows(rows)
        self.assertEqual(result["rows"], 72)
        self.assertEqual(result["disagreement_rate"], 0.0)
        self.assertEqual(result["pass_at_one"], 0.5)
        self.assertTrue(
            all(
                profile["mean_repair_gain"] == 1.0
                for profile in result["models"]["test-model"].values()
            )
        )


if __name__ == "__main__":
    unittest.main()
