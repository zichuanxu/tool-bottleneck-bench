from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from bottleneckbench.core.agnes import (
    AgnesClient,
    AgnesError,
    ModelRequest,
    ModelResponse,
    ToolCall,
    parse_chat_completion,
)
from bottleneckbench.core.harness import run_episode
from bottleneckbench.core.schema import Condition, Representation, RunStatus, Track
from bottleneckbench.core.tasks import development_templates


class AgnesParserTest(unittest.TestCase):
    def test_parser_separates_reasoning_from_final_content(self) -> None:
        payload = {
            "model": "agnes-2.5-flash",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "reasoning_content": "private reasoning",
                        "content": "final answer",
                    },
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
        }

        response = parse_chat_completion(payload, expected_model="agnes-2.5-flash")

        self.assertEqual(response.content, "final answer")
        self.assertEqual(response.reasoning, "private reasoning")
        self.assertEqual(response.tool_calls, ())
        self.assertEqual(response.total_tokens, 14)

    def test_parser_accepts_openai_style_tool_calls_and_closed_json_arguments(self) -> None:
        payload = {
            "model": "agnes-2.5-flash",
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "reasoning_content": "find invoice",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "extra_content": {
                                    "google": {"thought_signature": "signature-1"}
                                },
                                "function": {
                                    "name": "search_invoices",
                                    "arguments": '{"query":"overdue"}',
                                },
                            }
                        ],
                    },
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
        }

        response = parse_chat_completion(payload, expected_model="agnes-2.5-flash")

        self.assertEqual(
            response.tool_calls,
            (
                ToolCall(
                    call_id="call-1",
                    name="search_invoices",
                    arguments={"query": "overdue"},
                    extra_content={
                        "google": {"thought_signature": "signature-1"}
                    },
                ),
            ),
        )
        self.assertEqual(
            response.tool_calls[0].to_openai()["extra_content"],
            {"google": {"thought_signature": "signature-1"}},
        )
        self.assertEqual(response.finish_reason, "tool_calls")

    def test_parser_records_invalid_arguments_and_rejects_served_model(self) -> None:
        malformed = {
            "model": "agnes-2.5-flash",
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "function": {"name": "search_invoices", "arguments": "not-json"},
                            }
                        ]
                    },
                }
            ],
        }
        response = parse_chat_completion(
            malformed, expected_model="agnes-2.5-flash"
        )
        self.assertEqual(response.tool_calls, ())
        self.assertEqual(
            response.response_errors,
            ("tool_call_0_arguments_invalid_json",),
        )
        with self.assertRaisesRegex(AgnesError, "served model"):
            parse_chat_completion(
                {"model": "other", "choices": [{"message": {}, "finish_reason": "stop"}]},
                expected_model="agnes-2.5-flash",
            )

    def test_client_requires_environment_secret_without_exposing_it(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(AgnesError, "AGNES_API_KEY") as error:
                AgnesClient()
        self.assertNotIn("sk-", str(error.exception))


class _FakeClient:
    def __init__(self, responses: list[ModelResponse]) -> None:
        self.responses = responses
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return self.responses.pop(0)


class HarnessTest(unittest.TestCase):
    def test_react_episode_executes_tool_and_passes_strict_vts(self) -> None:
        task = development_templates()[0]
        condition = Condition(
            task_id=task.template_id,
            track=Track.DISCOVERY,
            model="agnes-2.5-flash",
            harness="flat-retrieval",
            representation=Representation.TYPED,
            registry_size=32,
            exposure_size=8,
            seed=3,
        )
        fake = _FakeClient(
            [
                ModelResponse(
                    model="agnes-2.5-flash",
                    content="",
                    reasoning="find invoice",
                    tool_calls=(
                        ToolCall("call-1", "search_invoices", {"query": "overdue"}),
                    ),
                    finish_reason="tool_calls",
                    prompt_tokens=100,
                    completion_tokens=20,
                    raw_id="response-1",
                ),
                ModelResponse(
                    model="agnes-2.5-flash",
                    content="INV-003 is overdue. Source: invoice-target-3.",
                    reasoning="summarize",
                    tool_calls=(),
                    finish_reason="stop",
                    prompt_tokens=140,
                    completion_tokens=30,
                    raw_id="response-2",
                ),
            ]
        )

        episode = run_episode(condition, task, fake)

        self.assertEqual(episode.status, RunStatus.VERIFIED_SUCCESS)
        self.assertTrue(episode.verdict.strict_vts)
        self.assertEqual(len(episode.trace), 1)
        self.assertEqual(episode.trace[0].tool, "search_invoices")
        self.assertEqual(len(fake.requests), 2)
        self.assertTrue(fake.requests[0].tools)
        self.assertEqual(episode.total_tokens, 290)
        serialized = json.dumps(episode.to_json())
        self.assertNotIn("Authorization", serialized)
        self.assertNotIn("AGNES_API_KEY", serialized)

    def test_length_termination_cannot_pass_on_a_valid_partial_answer(self) -> None:
        task = development_templates()[0]
        condition = Condition(
            task_id=task.template_id,
            track=Track.DISCOVERY,
            model="agnes-2.5-flash",
            harness="flat-retrieval",
            representation=Representation.RAW,
            registry_size=32,
            exposure_size=8,
            seed=3,
        )
        fake = _FakeClient(
            [
                ModelResponse(
                    model="agnes-2.5-flash",
                    content="",
                    reasoning="find",
                    tool_calls=(ToolCall("call-1", "search_invoices", {"query": "overdue"}),),
                    finish_reason="tool_calls",
                    prompt_tokens=100,
                    completion_tokens=20,
                    raw_id="response-tool",
                ),
                ModelResponse(
                    model="agnes-2.5-flash",
                    content="INV-003 is overdue. Source invoice-target-3.",
                    reasoning="unfinished tail",
                    tool_calls=(),
                    finish_reason="length",
                    prompt_tokens=140,
                    completion_tokens=1024,
                    raw_id="response-length-valid-prefix",
                ),
            ]
        )

        episode = run_episode(condition, task, fake)

        self.assertEqual(episode.status, RunStatus.AGENT_FAILURE)
        self.assertFalse(episode.verdict.strict_vts)
        self.assertIn("model_finish_length", episode.errors)

    def test_length_termination_is_agent_failure_not_success(self) -> None:
        task = development_templates()[0]
        condition = Condition(
            task_id=task.template_id,
            track=Track.DISCOVERY,
            model="agnes-2.5-flash",
            harness="flat-retrieval",
            representation=Representation.RAW,
            registry_size=32,
            exposure_size=8,
            seed=3,
        )
        fake = _FakeClient(
            [
                ModelResponse(
                    model="agnes-2.5-flash",
                    content="",
                    reasoning="unfinished",
                    tool_calls=(),
                    finish_reason="length",
                    prompt_tokens=100,
                    completion_tokens=128,
                    raw_id="response-length",
                )
            ]
        )

        episode = run_episode(condition, task, fake)

        self.assertEqual(episode.status, RunStatus.AGENT_FAILURE)
        self.assertFalse(episode.verdict.strict_vts)
        self.assertIn("model_finish_length", episode.errors)


if __name__ == "__main__":
    unittest.main()
