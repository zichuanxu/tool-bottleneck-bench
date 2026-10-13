from __future__ import annotations

import unittest

from bottleneckbench.design import Split
from bottleneckbench.discovery import (
    DiscoveryCondition,
    Retriever,
    availability_profile,
    build_registry,
    discovery_cases,
    discovery_construct_audit,
    _system_prompt,
    retrieve,
    run_discovery_episode,
)
from bottleneckbench.core.agnes import ModelRequest, ModelResponse, ToolCall
from bottleneckbench.core.schema import RunStatus


class _SelectionClient:
    def __init__(self, case) -> None:
        self.case = case
        self.calls = 0

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        return ModelResponse(
            model=request.model,
            content="",
            reasoning="",
            tool_calls=(
                ToolCall("call-1", self.case.required_tool, self.case.argument_map),
            ),
            finish_reason="tool_calls",
            prompt_tokens=20,
            completion_tokens=5,
            raw_id="response-1",
        )


class _MalformedSelectionClient:
    def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            model=request.model,
            content="",
            reasoning="",
            tool_calls=(),
            finish_reason="tool_calls",
            prompt_tokens=17,
            completion_tokens=3,
            raw_id="malformed-selection",
            response_errors=("tool_call_0_arguments_invalid_json",),
        )


class DiscoveryTest(unittest.TestCase):
    def test_deliberate_harness_changes_the_one_shot_selection_policy(self) -> None:
        direct = _system_prompt("react-v1@vertex-openai")
        deliberate = _system_prompt("deliberate-verify-v1@vertex-openai")

        self.assertNotEqual(direct, deliberate)
        self.assertIn("internally verify", deliberate)

    def test_cases_are_split_isolated_unique_and_tool_balanced(self) -> None:
        development = discovery_cases(Split.DEVELOPMENT)
        main = discovery_cases(Split.MAIN)

        self.assertEqual((len(development), len(main)), (18, 36))
        self.assertEqual(len({case.request for case in development}), 18)
        self.assertEqual(len({case.required_tool for case in development}), 18)
        self.assertEqual(len({case.case_hash for case in development}), 18)
        self.assertEqual(len({case.case_hash for case in main}), 36)
        self.assertFalse(
            {case.case_id for case in development} & {case.case_id for case in main}
        )

    def test_registered_catalogs_are_nested_and_operational_prefix_is_frozen(self) -> None:
        registries = {size: build_registry(size) for size in (32, 128, 512, 2048)}

        for small, large in ((32, 128), (128, 512), (512, 2048)):
            self.assertEqual(
                [tool.name for tool in registries[small]],
                [tool.name for tool in registries[large][:small]],
            )

    def test_offline_availability_has_active_range_and_retriever_interaction(self) -> None:
        profile = availability_profile()

        self.assertEqual(profile["flat"]["32"]["rate"], 1.0)
        self.assertLess(profile["flat"]["2048"]["rate"], 0.5)
        self.assertGreater(
            profile["hierarchical"]["2048"]["rate"],
            profile["flat"]["2048"]["rate"],
        )

    def test_available_required_tool_can_pass_one_shot_selection(self) -> None:
        case = discovery_cases()[0]
        condition = DiscoveryCondition(
            case_id=case.case_id,
            split=case.split,
            model="test-model",
            harness="native-tool-call",
            retriever=Retriever.FLAT,
            registry_size=32,
            visible_tools=16,
            seed=case.seed,
        )
        client = _SelectionClient(case)

        episode = run_discovery_episode(condition, case, client)

        self.assertEqual(episode.status, RunStatus.VERIFIED_SUCCESS)
        self.assertTrue(episode.success)
        self.assertEqual(client.calls, 1)

    def test_malformed_selection_is_agent_failure_with_usage(self) -> None:
        case = discovery_cases()[0]
        condition = DiscoveryCondition(
            case_id=case.case_id,
            split=case.split,
            model="test-model",
            harness="native-tool-call",
            retriever=Retriever.FLAT,
            registry_size=32,
            visible_tools=16,
            seed=case.seed,
        )

        episode = run_discovery_episode(
            condition, case, _MalformedSelectionClient()
        )

        self.assertEqual(episode.status, RunStatus.AGENT_FAILURE)
        self.assertEqual(episode.prompt_tokens + episode.completion_tokens, 20)
        self.assertEqual(episode.response_id, "malformed-selection")
        self.assertEqual(episode.response_model, "test-model")
        self.assertIn(
            "model_response_contract:tool_call_0_arguments_invalid_json",
            episode.errors,
        )

    def test_unavailable_required_tool_is_itt_failure_without_provider_call(self) -> None:
        registry = build_registry(2048)
        case = next(
            item
            for item in discovery_cases()
            if not retrieve(item, registry, Retriever.FLAT).required_available
        )
        condition = DiscoveryCondition(
            case_id=case.case_id,
            split=case.split,
            model="test-model",
            harness="native-tool-call",
            retriever=Retriever.FLAT,
            registry_size=2048,
            visible_tools=16,
            seed=case.seed,
        )
        client = _SelectionClient(case)

        episode = run_discovery_episode(condition, case, client)

        self.assertEqual(episode.status, RunStatus.CAPABILITY_UNAVAILABLE)
        self.assertFalse(episode.success)
        self.assertEqual(client.calls, 0)

    def test_discovery_construct_audit_passes(self) -> None:
        for split in (Split.DEVELOPMENT, Split.MAIN):
            audit = discovery_construct_audit(split)
            self.assertTrue(audit["passed"], audit)
            self.assertEqual(len(audit["checks"]), 10)


if __name__ == "__main__":
    unittest.main()
