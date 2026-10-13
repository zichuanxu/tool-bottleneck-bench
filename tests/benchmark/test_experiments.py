from __future__ import annotations

from collections import Counter
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from bottleneckbench.design import Split, static_construct_audit, tasks_for_split
from bottleneckbench.cli import _panel_execution
from bottleneckbench.experiments import (
    EpisodePolicy,
    ExecutionProfile,
    build_discovery_plan,
    build_plan,
    execute_discovery_plan,
    execute_plan,
    freeze_discovery_plan,
    freeze_plan,
    load_discovery_plan,
    load_plan,
)
from bottleneckbench.discovery import discovery_cases, discovery_construct_audit
from bottleneckbench.runtime import _reference_answer, runtime_construct_audit
from bottleneckbench.study_design import StudyRole
from bottleneckbench.core.agnes import ModelRequest, ModelResponse, ToolCall
from bottleneckbench.core.schema import stable_hash


_EXECUTION = ExecutionProfile(
    endpoint="https://example.test/v1/chat/completions",
    api_key_env="TEST_API_KEY",
    timeout_seconds=30.0,
    transport_retries=1,
    workers=1,
    provider_concurrency_cap=2,
)
_ADAPTIVE_POLICY = EpisodePolicy(
    name="task-adaptive-routed-v1",
    tool_call_slack=2,
    turn_slack=2,
    max_total_tokens=128000,
    max_completion_tokens=1024,
    dynamic_shortlist=True,
    early_stop_infeasible=True,
)
_PROTOCOL_HASH = stable_hash("test protocol")
_PANEL_HASH = stable_hash({"panel": "test"})


def _admission() -> dict:
    return {
        "audit": "factorized-capacity-development-admission",
        "split": "development",
        "passed": True,
        "checks": [{"name": "test-fixture", "passed": True}],
    }


def _prospective_admission() -> dict:
    return {
        "audit": "prospective-capacity-execution-admission-v1",
        "target_split": "main",
        "protocol_artifact_hash": _PROTOCOL_HASH,
        "model_panel_hash": _PANEL_HASH,
        "passed": True,
        "checks": [{"name": "test-fixture", "passed": True}],
    }


class _ReferenceTaskClient:
    def __init__(self) -> None:
        self.tasks = {
            task.request: task for task in tasks_for_split(Split.DEVELOPMENT)
        }
        self.calls = 0

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        task = self.tasks[str(request.messages[1]["content"])]
        step_index = sum(message.get("role") == "assistant" for message in request.messages)
        if step_index < len(task.program):
            step = task.program[step_index]
            calls = (ToolCall(f"{task.task_id}-{step_index}", step.tool_name, step.argument_map),)
            content = ""
            finish = "tool_calls"
        else:
            calls = ()
            content = _reference_answer(task)
            finish = "stop"
        return ModelResponse(
            model=request.model,
            content=content,
            reasoning="",
            tool_calls=calls,
            finish_reason=finish,
            prompt_tokens=10,
            completion_tokens=2,
            raw_id=f"{task.task_id}-response-{step_index}",
        )


class _FirstDiscoveryClient:
    def __init__(self) -> None:
        self.cases = {case.request: case for case in discovery_cases()}
        self.calls = 0

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        case = self.cases[str(request.messages[1]["content"])]
        return ModelResponse(
            model=request.model,
            content="",
            reasoning="",
            tool_calls=(ToolCall("call-1", case.required_tool, case.argument_map),),
            finish_reason="tool_calls",
            prompt_tokens=10,
            completion_tokens=2,
            raw_id="discovery-response-1",
        )


class ExperimentPlanTest(unittest.TestCase):
    def test_execution_profile_rejects_scalar_string_sequences(self) -> None:
        payload = _EXECUTION.to_json()
        payload["provider_only"] = "groq"
        with self.assertRaisesRegex(ValueError, "array of strings"):
            ExecutionProfile.from_json(payload)

    @classmethod
    def setUpClass(cls) -> None:
        cls.static = {
            split: static_construct_audit(split) for split in Split
        }
        cls.runtime = {
            split: runtime_construct_audit(split) for split in Split
        }

    def _plan(
        self,
        split: Split,
        *,
        workers: int = 1,
        study_role: StudyRole = StudyRole.PRIMARY,
    ):
        return build_plan(
            split=split,
            model="test-model",
            harness="react",
            study_role=study_role,
            frozen_at="2026-08-05T00:00:00Z",
            protocol_artifact_hash=_PROTOCOL_HASH,
            model_panel_hash=_PANEL_HASH,
            execution=replace(_EXECUTION, workers=workers),
            static_audit=self.static[split],
            runtime_audit=self.runtime[split],
            admission_audit=_admission() if split is not Split.DEVELOPMENT else None,
        )

    def test_registered_condition_counts_match_efficient_design(self) -> None:
        development = self._plan(Split.DEVELOPMENT)
        main = self._plan(Split.MAIN)
        transfer = self._plan(Split.TRANSFER)
        development_robustness = self._plan(
            Split.DEVELOPMENT, study_role=StudyRole.ROBUSTNESS
        )
        main_robustness = self._plan(
            Split.MAIN, study_role=StudyRole.ROBUSTNESS
        )
        main_verification = build_plan(
            split=Split.MAIN,
            model="test-model",
            harness="deliberate-verify-v1@test",
            study_role=StudyRole.VERIFICATION_REPAIR,
            frozen_at="2026-08-05T00:00:00Z",
            protocol_artifact_hash=_PROTOCOL_HASH,
            model_panel_hash=_PANEL_HASH,
            execution=_EXECUTION,
            static_audit=self.static[Split.MAIN],
            runtime_audit=self.runtime[Split.MAIN],
            admission_audit=_admission(),
        )
        main_interface = build_plan(
            split=Split.MAIN,
            model="test-model",
            harness="interface-guide-v1@test",
            study_role=StudyRole.INTERFACE_GUIDE,
            frozen_at="2026-08-05T00:00:00Z",
            protocol_artifact_hash=_PROTOCOL_HASH,
            model_panel_hash=_PANEL_HASH,
            execution=_EXECUTION,
            static_audit=self.static[Split.MAIN],
            runtime_audit=self.runtime[Split.MAIN],
            admission_audit=_admission(),
        )

        self.assertEqual(len(development.conditions), 144)
        self.assertEqual(len(main.conditions), 432)
        self.assertEqual(len(transfer.conditions), 72)
        self.assertEqual(len(development_robustness.conditions), 48)
        self.assertEqual(len(main_robustness.conditions), 144)
        self.assertEqual(len(main_verification.conditions), 72)
        self.assertEqual(len(main_interface.conditions), 72)
        for plan in (
            development,
            main,
            transfer,
            development_robustness,
            main_robustness,
            main_verification,
            main_interface,
        ):
            self.assertEqual(
                len({condition.condition_id for condition in plan.conditions}),
                len(plan.conditions),
            )

    def test_repair_roles_require_the_registered_harness(self) -> None:
        with self.assertRaisesRegex(ValueError, "deliberate-verify-v1"):
            self._plan(Split.MAIN, study_role=StudyRole.VERIFICATION_REPAIR)
        with self.assertRaisesRegex(ValueError, "interface-guide-v1"):
            self._plan(Split.MAIN, study_role=StudyRole.INTERFACE_GUIDE)

    def test_prospective_admission_is_bound_to_protocol_panel_and_split(self) -> None:
        plan = build_plan(
            split=Split.MAIN,
            model="test-model",
            harness="react",
            frozen_at="2026-08-13T00:00:00Z",
            protocol_artifact_hash=_PROTOCOL_HASH,
            model_panel_hash=_PANEL_HASH,
            execution=_EXECUTION,
            static_audit=self.static[Split.MAIN],
            runtime_audit=self.runtime[Split.MAIN],
            admission_audit=_prospective_admission(),
        )
        self.assertEqual(plan.split, Split.MAIN)
        discovery = build_discovery_plan(
            split=Split.MAIN,
            model="test-model",
            harness="react",
            frozen_at="2026-08-13T00:00:00Z",
            protocol_artifact_hash=_PROTOCOL_HASH,
            model_panel_hash=_PANEL_HASH,
            execution=_EXECUTION,
            construct_audit=discovery_construct_audit(Split.MAIN),
            admission_audit=_prospective_admission(),
        )
        self.assertEqual(discovery.split, Split.MAIN)
        invalid = {**_prospective_admission(), "model_panel_hash": "f" * 64}
        with self.assertRaisesRegex(ValueError, "does not bind"):
            build_plan(
                split=Split.MAIN,
                model="test-model",
                harness="react",
                frozen_at="2026-08-13T00:00:00Z",
                protocol_artifact_hash=_PROTOCOL_HASH,
                model_panel_hash=_PANEL_HASH,
                execution=_EXECUTION,
                static_audit=self.static[Split.MAIN],
                runtime_audit=self.runtime[Split.MAIN],
                admission_audit=invalid,
            )

    def test_capacity_execution_order_is_factor_balanced_by_block(self) -> None:
        for split, block_size, family_repetitions in (
            (Split.DEVELOPMENT, 12, 1),
            (Split.MAIN, 12, 1),
            (Split.TRANSFER, 6, 1),
        ):
            plan = self._plan(split)
            for start in range(0, len(plan.conditions), block_size):
                block = plan.conditions[start : start + block_size]
                cells = {
                    (item.visible_tools, item.workflow, item.representation)
                    for item in block
                }
                self.assertEqual(len(cells), block_size)
                self.assertEqual(
                    set(Counter(item.family_id for item in block).values()),
                    {family_repetitions},
                )

    def test_discovery_plans_are_split_bound_and_have_registered_counts(self) -> None:
        plan = build_discovery_plan(
            split=Split.DEVELOPMENT,
            model="test-model",
            harness="native-tool-call",
            frozen_at="2026-08-05T00:00:00Z",
            protocol_artifact_hash=_PROTOCOL_HASH,
            model_panel_hash=_PANEL_HASH,
            execution=_EXECUTION,
            construct_audit=discovery_construct_audit(),
        )

        main = build_discovery_plan(
            split=Split.MAIN,
            model="test-model",
            harness="native-tool-call",
            frozen_at="2026-08-05T00:00:00Z",
            protocol_artifact_hash=_PROTOCOL_HASH,
            model_panel_hash=_PANEL_HASH,
            execution=_EXECUTION,
            construct_audit=discovery_construct_audit(Split.MAIN),
            admission_audit=_admission(),
        )

        self.assertEqual(len(plan.conditions), 108)
        self.assertEqual(len(main.conditions), 216)
        self.assertEqual(len({condition.condition_id for condition in plan.conditions}), 108)
        self.assertFalse(
            {condition.condition_id for condition in plan.conditions}
            & {condition.condition_id for condition in main.conditions}
        )
        for candidate in (plan, main):
            for start in range(0, len(candidate.conditions), 6):
                block = candidate.conditions[start : start + 6]
                self.assertEqual(
                    len({(item.registry_size, item.retriever) for item in block}),
                    6,
                )
                self.assertEqual(len({item.case_id for item in block}), 6)

    def test_discovery_plan_roundtrip_and_resume(self) -> None:
        plan = build_discovery_plan(
            split=Split.DEVELOPMENT,
            model="test-model",
            harness="native-tool-call",
            frozen_at="2026-08-05T00:00:00Z",
            protocol_artifact_hash=_PROTOCOL_HASH,
            model_panel_hash=_PANEL_HASH,
            execution=_EXECUTION,
            construct_audit=discovery_construct_audit(),
        )
        client = _FirstDiscoveryClient()
        with tempfile.TemporaryDirectory() as directory:
            plan_path = Path(directory) / "discovery-plan.json"
            runs_path = Path(directory) / "discovery-runs.jsonl"
            freeze_discovery_plan(plan_path, plan)
            loaded = load_discovery_plan(plan_path)
            first = execute_discovery_plan(loaded, client, runs_path, max_conditions=1)
            second = execute_discovery_plan(loaded, client, runs_path, max_conditions=1)
            row = json.loads(runs_path.read_text().strip())

        self.assertEqual(loaded, plan)
        self.assertEqual(first, {"planned": 1, "executed": 1, "skipped": 0})
        self.assertEqual(second, {"planned": 1, "executed": 0, "skipped": 1})
        self.assertLessEqual(client.calls, 1)
        self.assertEqual(row.get("response_model"), "test-model")

    def test_main_and_transfer_freeze_fail_closed_without_development_admission(self) -> None:
        with self.assertRaisesRegex(ValueError, "admission"):
            build_plan(
                split=Split.MAIN,
                model="test-model",
                harness="react",
                frozen_at="2026-08-05T00:00:00Z",
                protocol_artifact_hash=_PROTOCOL_HASH,
                model_panel_hash=_PANEL_HASH,
                execution=_EXECUTION,
                static_audit=self.static[Split.MAIN],
                runtime_audit=self.runtime[Split.MAIN],
            )
        with self.assertRaisesRegex(ValueError, "admission"):
            build_discovery_plan(
                split=Split.MAIN,
                model="test-model",
                harness="native-tool-call",
                frozen_at="2026-08-05T00:00:00Z",
                protocol_artifact_hash=_PROTOCOL_HASH,
                model_panel_hash=_PANEL_HASH,
                execution=_EXECUTION,
                construct_audit=discovery_construct_audit(Split.MAIN),
            )

    def test_audit_split_mismatch_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "split"):
            build_plan(
                split=Split.MAIN,
                model="test-model",
                harness="react",
                frozen_at="2026-08-05T00:00:00Z",
                protocol_artifact_hash=_PROTOCOL_HASH,
                model_panel_hash=_PANEL_HASH,
                execution=_EXECUTION,
                static_audit=self.static[Split.DEVELOPMENT],
                runtime_audit=self.runtime[Split.MAIN],
                admission_audit=_admission(),
            )

    def test_frozen_plan_roundtrip_preserves_hashes_and_conditions(self) -> None:
        plan = self._plan(Split.DEVELOPMENT)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            freeze_plan(path, plan)
            loaded = load_plan(path)

        self.assertEqual(loaded, plan)
        self.assertEqual(loaded.plan_hash, plan.plan_hash)
        self.assertEqual(len(loaded.source_hash), 64)
        self.assertEqual(len(loaded.task_manifest_hash), 64)
        self.assertEqual(loaded.execution, _EXECUTION)
        self.assertEqual(loaded.protocol_artifact_hash, _PROTOCOL_HASH)
        self.assertEqual(loaded.model_panel_hash, _PANEL_HASH)

    def test_execution_profile_changes_plan_hash_and_binds_workers(self) -> None:
        one_worker = self._plan(Split.DEVELOPMENT)
        two_workers = self._plan(Split.DEVELOPMENT, workers=2)

        self.assertNotEqual(one_worker.plan_hash, two_workers.plan_hash)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "frozen execution profile"):
                execute_plan(
                    one_worker,
                    _ReferenceTaskClient(),
                    Path(directory) / "runs.jsonl",
                    max_conditions=1,
                    workers=2,
                )

    def test_frozen_panel_defines_the_execution_profile(self) -> None:
        panel = {
            "frozen_for_study": True,
            "execution_policy": {
                "timeout_seconds": 30.0,
                "transport_retries": 1,
                "workers_per_job": 1,
                "provider_concurrency_cap": 2,
            },
            "configurations": [
                {
                    "model": "test-model",
                    "harness": "react",
                    "endpoint": "https://example.test/v1/chat/completions",
                    "api_key_env": "TEST_API_KEY",
                }
            ],
        }

        self.assertEqual(_panel_execution(panel, "test-model", "react"), _EXECUTION)
        panel["execution_policy"]["minimum_request_interval_seconds"] = 3.2
        self.assertEqual(
            _panel_execution(panel, "test-model", "react"),
            replace(_EXECUTION, minimum_request_interval_seconds=3.2),
        )
        panel["configurations"][0]["api_key_header"] = "x-goog-api-key"
        self.assertEqual(
            _panel_execution(panel, "test-model", "react"),
            replace(
                _EXECUTION,
                minimum_request_interval_seconds=3.2,
                api_key_header="x-goog-api-key",
            ),
        )
        panel["execution_policy"]["episode_policy"] = _ADAPTIVE_POLICY.to_json()
        self.assertEqual(
            _panel_execution(panel, "test-model", "react"),
            replace(
                _EXECUTION,
                minimum_request_interval_seconds=3.2,
                api_key_header="x-goog-api-key",
                episode_policy=_ADAPTIVE_POLICY,
            ),
        )
        fixed_policy = replace(
            _ADAPTIVE_POLICY,
            name="task-adaptive-fixed-v1",
            dynamic_shortlist=False,
        )
        panel["configurations"][0]["episode_policy"] = fixed_policy.to_json()
        self.assertEqual(
            _panel_execution(panel, "test-model", "react").episode_policy,
            fixed_policy,
        )
        panel["configurations"][0]["workers_per_job"] = 2
        panel["configurations"][0]["provider_concurrency_cap"] = 4
        self.assertEqual(
            _panel_execution(panel, "test-model", "react").workers,
            2,
        )
        self.assertEqual(
            _panel_execution(panel, "test-model", "react").provider_concurrency_cap,
            4,
        )
        panel["configurations"][0].update(
            {
                "provider_only": ["groq"],
                "provider_allow_fallbacks": False,
                "provider_data_collection": "deny",
                "provider_require_parameters": True,
                "provider_quantizations": ["fp16"],
                "service_tier": "default",
                "accepted_response_models": ["test-model-20260811"],
                "proxy_url": "http://127.0.0.1:7890",
                "omit_temperature": True,
                "omit_seed": True,
                "omit_parallel_tool_calls": True,
                "require_usage": True,
                "stream": True,
                "disable_response_cache": True,
            }
        )
        routed = _panel_execution(panel, "test-model", "react")
        self.assertEqual(routed.provider_only, ("groq",))
        self.assertFalse(routed.provider_allow_fallbacks)
        self.assertEqual(routed.provider_data_collection, "deny")
        self.assertTrue(routed.provider_require_parameters)
        self.assertEqual(routed.provider_quantizations, ("fp16",))
        self.assertEqual(routed.service_tier, "default")
        self.assertEqual(
            routed.accepted_response_models, ("test-model-20260811",)
        )
        self.assertEqual(routed.proxy_url, "http://127.0.0.1:7890")
        self.assertTrue(routed.omit_temperature)
        self.assertTrue(routed.omit_seed)
        self.assertTrue(routed.omit_parallel_tool_calls)
        self.assertTrue(routed.require_usage)
        self.assertTrue(routed.stream)
        self.assertTrue(routed.disable_response_cache)
        self.assertEqual(ExecutionProfile.from_json(routed.to_json()), routed)
        panel["frozen_for_study"] = False
        with self.assertRaisesRegex(ValueError, "frozen"):
            _panel_execution(panel, "test-model", "react")

    def test_execute_plan_is_resume_safe(self) -> None:
        plan = self._plan(Split.DEVELOPMENT)
        client = _ReferenceTaskClient()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runs.jsonl"
            first = execute_plan(plan, client, path, max_conditions=1)
            second = execute_plan(plan, client, path, max_conditions=2)
            third = execute_plan(plan, client, path, max_conditions=2)
            rows = [json.loads(line) for line in path.read_text().splitlines()]

        self.assertEqual(first, {"planned": 1, "executed": 1, "skipped": 0})
        self.assertEqual(second, {"planned": 2, "executed": 1, "skipped": 1})
        self.assertEqual(third, {"planned": 2, "executed": 0, "skipped": 2})
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["plan_hash"], plan.plan_hash)
        self.assertEqual(set(rows[0]["response_models"]), {"test-model"})

    def test_execute_plan_applies_frozen_adaptive_episode_policy(self) -> None:
        plan = replace(
            self._plan(Split.DEVELOPMENT),
            execution=replace(_EXECUTION, episode_policy=_ADAPTIVE_POLICY),
        )
        first_condition = plan.conditions[0]
        task = next(
            item
            for item in tasks_for_split(Split.DEVELOPMENT)
            if item.task_id == first_condition.task_id
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runs.jsonl"
            execute_plan(plan, _ReferenceTaskClient(), path, max_conditions=1)
            row = json.loads(path.read_text().strip())

        self.assertEqual(
            row["efficiency"]["effective_max_tool_calls"],
            len(task.program) + _ADAPTIVE_POLICY.tool_call_slack,
        )
        self.assertEqual(row["efficiency"]["request_tool_counts"][0], first_condition.visible_tools)
        self.assertLessEqual(
            max(row["efficiency"]["request_tool_counts"][1:]),
            len(task.base_tool_names),
        )

    def test_execute_plan_supports_bounded_parallel_batches(self) -> None:
        plan = self._plan(Split.DEVELOPMENT, workers=2)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runs.jsonl"
            result = execute_plan(
                plan,
                _ReferenceTaskClient(),
                path,
                max_conditions=2,
                workers=2,
            )
            rows = [json.loads(line) for line in path.read_text().splitlines()]

        self.assertEqual(result, {"planned": 2, "executed": 2, "skipped": 0})
        self.assertEqual(len({row["condition_id"] for row in rows}), 2)
        self.assertTrue(all(row["executor"]["worker_limit"] == 2 for row in rows))
        self.assertTrue(all(row["executor"]["completed_at"] for row in rows))

    def test_source_hash_mismatch_blocks_before_provider_call(self) -> None:
        plan = replace(self._plan(Split.DEVELOPMENT), source_hash="0" * 64)
        client = _ReferenceTaskClient()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "source hash"):
                execute_plan(plan, client, Path(directory) / "runs.jsonl", max_conditions=1)
        self.assertEqual(client.calls, 0)


if __name__ == "__main__":
    unittest.main()
