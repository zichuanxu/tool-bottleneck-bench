from __future__ import annotations

from io import BytesIO
import json
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from bottleneckbench.providers import ChatCompletionsClient
from bottleneckbench.core.agnes import AgnesError, ModelRequest


class _Response:
    def __init__(
        self,
        model: str = "test-model",
        *,
        prompt_tokens: int = 1,
        completion_tokens: int = 1,
        generation_id: str | None = None,
    ) -> None:
        self.model = model
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.headers = (
            {"X-Generation-Id": generation_id} if generation_id else {}
        )

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(
            {
                "id": "response-1",
                "model": self.model,
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "done", "tool_calls": []},
                    }
                ],
                "usage": {
                    "prompt_tokens": self.prompt_tokens,
                    "completion_tokens": self.completion_tokens,
                },
            }
        ).encode()


class _PayloadResponse:
    def __init__(
        self, payload: object, headers: dict[str, str] | None = None
    ) -> None:
        self.payload = payload
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


class _SseResponse:
    def __init__(
        self,
        events: list[object],
        *,
        generation_id: str = "gen-stream-1",
    ) -> None:
        self.events = events
        self.headers = {"X-Generation-Id": generation_id}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        lines = [
            f"data: {json.dumps(event)}\n\n" for event in self.events
        ]
        lines.append("data: [DONE]\n\n")
        return "".join(lines).encode()


class ProviderTest(unittest.TestCase):
    @staticmethod
    def _request() -> ModelRequest:
        return ModelRequest(
            model="test-model",
            messages=({"role": "user", "content": "test"},),
        )

    @staticmethod
    def _http_error(
        code: int, body: bytes, headers: dict[str, str] | None = None
    ) -> HTTPError:
        return HTTPError(
            "https://example.test", code, "error", headers or {}, BytesIO(body)
        )

    def test_google_api_key_uses_frozen_header_without_bearer_auth(self) -> None:
        captured = {}

        def fake_urlopen(request, **_kwargs):
            captured.update(dict(request.header_items()))
            return _Response()

        request = ModelRequest(
            model="test-model",
            messages=({"role": "user", "content": "test"},),
        )
        with patch("bottleneckbench.providers.urlopen", fake_urlopen):
            ChatCompletionsClient(
                endpoint="https://example.test",
                api_key="test-key",
                api_key_header="x-goog-api-key",
            ).complete(request)

        self.assertEqual(captured["X-goog-api-key"], "test-key")
        self.assertNotIn("Authorization", captured)

    def test_tool_requests_disable_parallel_calls(self) -> None:
        captured = {}

        def fake_urlopen(request, **_kwargs):
            captured.update(json.loads(request.data.decode()))
            return _Response()

        request = ModelRequest(
            model="test-model",
            messages=({"role": "user", "content": "test"},),
            tools=(
                {
                    "type": "function",
                    "function": {
                        "name": "lookup",
                        "description": "Lookup.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
            ),
        )
        with patch("bottleneckbench.providers.urlopen", fake_urlopen):
            ChatCompletionsClient(endpoint="https://example.test").complete(request)

        self.assertIs(captured["parallel_tool_calls"], False)

    def test_provider_profile_can_omit_unsupported_parallel_call_flag(self) -> None:
        captured = {}

        def fake_urlopen(request, **_kwargs):
            captured.update(json.loads(request.data.decode()))
            return _Response()

        request = ModelRequest(
            model="test-model",
            messages=({"role": "user", "content": "test"},),
            tools=(
                {
                    "type": "function",
                    "function": {
                        "name": "lookup",
                        "description": "Lookup.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
            ),
        )
        with patch("bottleneckbench.providers.urlopen", fake_urlopen):
            ChatCompletionsClient(
                endpoint="https://example.test",
                omit_parallel_tool_calls=True,
            ).complete(request)

        self.assertNotIn("parallel_tool_calls", captured)

    def test_synthetic_contract_can_force_one_function(self) -> None:
        captured = {}

        def fake_urlopen(request, **_kwargs):
            captured.update(json.loads(request.data.decode()))
            return _Response()

        request = ModelRequest(
            model="test-model",
            messages=({"role": "user", "content": "test"},),
            tools=(
                {
                    "type": "function",
                    "function": {
                        "name": "advance",
                        "description": "Advance.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
            ),
            tool_choice={
                "type": "function",
                "function": {"name": "advance"},
            },
        )
        with patch("bottleneckbench.providers.urlopen", fake_urlopen):
            ChatCompletionsClient(endpoint="https://example.test").complete(request)

        self.assertEqual(captured["tool_choice"], request.tool_choice)

    def test_positive_usage_is_required_for_scored_transport(self) -> None:
        with (
            patch(
                "bottleneckbench.providers.urlopen",
                lambda *_args, **_kwargs: _Response(
                    prompt_tokens=0,
                    completion_tokens=0,
                ),
            ),
            self.assertRaisesRegex(AgnesError, "positive token usage"),
        ):
            ChatCompletionsClient(endpoint="https://example.test").complete(
                self._request()
            )

    def test_streaming_tool_call_and_terminal_usage_are_assembled(self) -> None:
        captured = {}
        events = [
            {
                "id": "stream-body-1",
                "model": "test-model",
                "choices": [
                    {
                        "finish_reason": None,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call-stream-1",
                                    "function": {
                                        "name": "lookup",
                                        "arguments": '{"query":',
                                    },
                                    "extra_content": {
                                        "google": {"thought_signature": "sig-1"}
                                    },
                                }
                            ]
                        },
                    }
                ],
            },
            {
                "model": "test-model",
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "function": {"arguments": '"value"}'},
                                }
                            ]
                        },
                    }
                ],
            },
            {
                "model": "test-model",
                "choices": [],
                "usage": {
                    "prompt_tokens": 23,
                    "completion_tokens": 7,
                },
            },
        ]

        def fake_urlopen(request, **_kwargs):
            captured["payload"] = json.loads(request.data.decode())
            captured["headers"] = dict(request.header_items())
            return _SseResponse(events)

        request = ModelRequest(
            model="test-model",
            messages=({"role": "user", "content": "test"},),
            tools=(
                {
                    "type": "function",
                    "function": {
                        "name": "lookup",
                        "description": "Lookup.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
            ),
        )
        with patch("bottleneckbench.providers.urlopen", fake_urlopen):
            response = ChatCompletionsClient(
                endpoint="https://example.test",
                stream=True,
                disable_response_cache=True,
            ).complete(request)

        self.assertIs(captured["payload"]["stream"], True)
        self.assertEqual(captured["headers"]["X-openrouter-cache"], "false")
        self.assertEqual(response.raw_id, "gen-stream-1")
        self.assertEqual(response.finish_reason, "tool_calls")
        self.assertEqual(response.prompt_tokens, 23)
        self.assertEqual(response.completion_tokens, 7)
        self.assertEqual(response.usage_source, "stream_terminal_event")
        self.assertEqual(len(response.tool_calls), 1)
        self.assertEqual(response.tool_calls[0].name, "lookup")
        self.assertEqual(response.tool_calls[0].arguments, {"query": "value"})
        self.assertEqual(
            response.tool_calls[0].extra_content,
            {"google": {"thought_signature": "sig-1"}},
        )

    def test_stream_without_terminal_usage_fails_closed(self) -> None:
        events = [
            {
                "id": "stream-body-1",
                "model": "test-model",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "delta": {"content": "done"},
                    }
                ],
            }
        ]
        with (
            patch(
                "bottleneckbench.providers.urlopen",
                return_value=_SseResponse(events),
            ),
            self.assertRaisesRegex(AgnesError, "positive token usage"),
        ):
            ChatCompletionsClient(
                endpoint="https://example.test",
                stream=True,
            ).complete(self._request())

    def test_optional_stream_usage_preserves_response_and_marks_missingness(self) -> None:
        events = [
            {
                "id": "stream-body-1",
                "model": "test-model",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "delta": {"content": "done"},
                    }
                ],
            }
        ]
        with patch(
            "bottleneckbench.providers.urlopen",
            return_value=_SseResponse(events),
        ):
            response = ChatCompletionsClient(
                endpoint="https://example.test",
                stream=True,
                require_usage=False,
            ).complete(self._request())

        self.assertEqual(response.content, "done")
        self.assertEqual(response.raw_id, "gen-stream-1")
        self.assertEqual(response.total_tokens, 0)
        self.assertEqual(response.usage_source, "stream_usage_missing")

    def test_openrouter_generation_header_is_preferred_for_provenance(self) -> None:
        with patch(
            "bottleneckbench.providers.urlopen",
            lambda *_args, **_kwargs: _Response(generation_id="gen-header-1"),
        ):
            response = ChatCompletionsClient(
                endpoint="https://example.test"
            ).complete(self._request())

        self.assertEqual(response.raw_id, "gen-header-1")

    def test_response_observer_receives_raw_body_without_authorization(self) -> None:
        observed = []
        with patch(
            "bottleneckbench.providers.urlopen",
            lambda *_args, **_kwargs: _Response(generation_id="gen-observed"),
        ):
            ChatCompletionsClient(
                endpoint="https://example.test",
                api_key="secret",
                response_observer=observed.append,
            ).complete(self._request())

        self.assertEqual(observed[0]["generation_id"], "gen-observed")
        self.assertEqual(json.loads(observed[0]["response_body"])["id"], "response-1")
        self.assertNotIn("secret", json.dumps(observed[0]))

    def test_response_observer_records_transport_failure(self) -> None:
        observed = []
        with patch(
            "bottleneckbench.providers.urlopen",
            side_effect=URLError("offline"),
        ):
            with self.assertRaises(AgnesError):
                ChatCompletionsClient(
                    endpoint="https://example.test",
                    max_retries=0,
                    response_observer=observed.append,
                ).complete(self._request())

        self.assertEqual(observed[0]["http_status"], 0)
        self.assertEqual(observed[0]["transport_error"], "URLError")

    def test_unsupported_temperature_and_seed_can_be_omitted(self) -> None:
        captured = {}

        def fake_urlopen(request, **_kwargs):
            captured.update(json.loads(request.data.decode()))
            return _Response()

        request = ModelRequest(
            model="test-model",
            messages=({"role": "user", "content": "test"},),
            temperature=0.0,
            seed=20260811,
        )
        with patch("bottleneckbench.providers.urlopen", fake_urlopen):
            ChatCompletionsClient(
                endpoint="https://example.test",
                omit_temperature=True,
            ).complete(request)

        self.assertEqual(captured["seed"], 20260811)
        self.assertNotIn("temperature", captured)

        captured.clear()
        with patch("bottleneckbench.providers.urlopen", fake_urlopen):
            ChatCompletionsClient(
                endpoint="https://example.test",
                omit_seed=True,
            ).complete(request)

        self.assertNotIn("seed", captured)
        self.assertEqual(captured["temperature"], 0.0)

    def test_openrouter_routing_and_version_contract_are_sent_and_enforced(self) -> None:
        captured = {}

        def fake_urlopen(request, **_kwargs):
            captured.update(json.loads(request.data.decode()))
            return _Response("canonical-model-20260811")

        request = ModelRequest(
            model="model-alias",
            messages=({"role": "user", "content": "test"},),
        )
        with patch("bottleneckbench.providers.urlopen", fake_urlopen):
            response = ChatCompletionsClient(
                endpoint="https://example.test",
                provider_only=("groq",),
                provider_allow_fallbacks=False,
                provider_data_collection="deny",
                provider_require_parameters=True,
                provider_quantizations=("fp16",),
                service_tier="default",
                accepted_response_models=("canonical-model-20260811",),
            ).complete(request)

        self.assertEqual(
            captured["provider"],
            {
                "only": ["groq"],
                "allow_fallbacks": False,
                "data_collection": "deny",
                "require_parameters": True,
                "quantizations": ["fp16"],
            },
        )
        self.assertEqual(captured["service_tier"], "default")
        self.assertEqual(response.model, "canonical-model-20260811")

    def test_unregistered_served_model_fails_closed(self) -> None:
        with (
            patch(
                "bottleneckbench.providers.urlopen",
                lambda *_args, **_kwargs: _Response("unexpected-model"),
            ),
            self.assertRaisesRegex(AgnesError, "served model mismatch"),
        ):
            ChatCompletionsClient(
                endpoint="https://example.test",
                accepted_response_models=("registered-model",),
            ).complete(self._request())

    def test_frozen_proxy_uses_a_dedicated_opener(self) -> None:
        opener = MagicMock()
        opener.open.return_value = _Response()
        with patch(
            "bottleneckbench.providers.build_opener",
            return_value=opener,
        ) as build:
            client = ChatCompletionsClient(
                endpoint="https://example.test",
                proxy_url="http://127.0.0.1:7890",
            )
            response = client.complete(self._request())

        self.assertEqual(response.model, "test-model")
        build.assert_called_once()
        opener.open.assert_called_once()

    def test_requests_observe_frozen_minimum_interval(self) -> None:
        request = ModelRequest(
            model="test-model",
            messages=({"role": "user", "content": "test"},),
        )
        client = ChatCompletionsClient(
            endpoint="https://example.test",
            minimum_request_interval_seconds=3.2,
        )
        with (
            patch("bottleneckbench.providers.urlopen", lambda *_args, **_kwargs: _Response()),
            patch("bottleneckbench.providers.time.monotonic", return_value=0.0),
            patch("bottleneckbench.providers.time.sleep") as sleep,
        ):
            client.complete(request)
            client.complete(request)

        sleep.assert_called_once_with(3.2)

    def test_vertex_array_invalid_argument_is_not_retried(self) -> None:
        body = json.dumps(
            [
                {
                    "error": {
                        "code": 400,
                        "message": "Expected input to contain field: messages.",
                        "status": "INVALID_ARGUMENT",
                    }
                }
            ]
        ).encode()
        failure = self._http_error(400, body)
        with (
            patch(
                "bottleneckbench.providers.urlopen",
                side_effect=failure,
            ) as urlopen,
            self.assertRaisesRegex(AgnesError, "Expected input"),
        ):
            ChatCompletionsClient(
                endpoint="https://example.test", max_retries=2
            ).complete(self._request())

        self.assertEqual(urlopen.call_count, 1)

    def test_non_json_http_400_is_retried(self) -> None:
        with (
            patch(
                "bottleneckbench.providers.urlopen",
                side_effect=[self._http_error(400, b"<html>Bad Request</html>"), _Response()],
            ) as urlopen,
            patch("bottleneckbench.providers.time.sleep") as sleep,
        ):
            response = ChatCompletionsClient(
                endpoint="https://example.test", max_retries=1
            ).complete(self._request())

        self.assertEqual(response.content, "done")
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_transient_vertex_status_inside_http_400_is_retried(self) -> None:
        body = json.dumps(
            [
                {
                    "error": {
                        "code": 503,
                        "message": "Backend unavailable.",
                        "status": "UNAVAILABLE",
                    }
                }
            ]
        ).encode()
        with (
            patch(
                "bottleneckbench.providers.urlopen",
                side_effect=[self._http_error(400, body), _Response()],
            ) as urlopen,
            patch("bottleneckbench.providers.time.sleep"),
        ):
            ChatCompletionsClient(
                endpoint="https://example.test", max_retries=1
            ).complete(self._request())

        self.assertEqual(urlopen.call_count, 2)

    def test_retry_after_header_controls_transient_backoff(self) -> None:
        failure = self._http_error(
            429,
            json.dumps({"error": {"message": "rate limited"}}).encode(),
            {"Retry-After": "7.5"},
        )
        with (
            patch(
                "bottleneckbench.providers.urlopen",
                side_effect=[failure, _Response()],
            ),
            patch("bottleneckbench.providers.time.sleep") as sleep,
        ):
            ChatCompletionsClient(
                endpoint="https://example.test", max_retries=1
            ).complete(self._request())

        sleep.assert_called_once_with(7.5)

    def test_transient_error_inside_http_200_consumes_frozen_retry_budget(self) -> None:
        failure = _PayloadResponse(
            {
                "error": {
                    "code": 502,
                    "message": "Provider unavailable.",
                    "metadata": {"error_type": "provider_unavailable"},
                }
            },
            {"Retry-After": "0.25", "X-Generation-Id": "failed-generation"},
        )
        with (
            patch(
                "bottleneckbench.providers.urlopen",
                side_effect=[failure, _Response(generation_id="successful-generation")],
            ) as urlopen,
            patch("bottleneckbench.providers.time.sleep") as sleep,
        ):
            client = ChatCompletionsClient(
                endpoint="https://example.test", max_retries=1
            )
            response = client.complete(self._request())

        self.assertEqual(urlopen.call_count, 2)
        self.assertEqual(client.retry_count, 1)
        self.assertEqual(response.raw_id, "successful-generation")
        sleep.assert_called_once_with(0.25)

    def test_inband_invalid_request_fails_without_retry_or_model_mismatch(self) -> None:
        failure = _PayloadResponse(
            {
                "error": {
                    "code": 400,
                    "message": "Invalid request.",
                    "metadata": {"error_type": "invalid_request"},
                }
            }
        )
        with (
            patch(
                "bottleneckbench.providers.urlopen",
                return_value=failure,
            ) as urlopen,
            self.assertRaisesRegex(
                AgnesError,
                r"provider response error 400 \[invalid_request\]: Invalid request",
            ),
        ):
            ChatCompletionsClient(
                endpoint="https://example.test", max_retries=2
            ).complete(self._request())

        self.assertEqual(urlopen.call_count, 1)

    def test_tool_schema_validation_error_is_not_retried_as_availability(self) -> None:
        failure = _PayloadResponse(
            {
                "error": {
                    "code": 502,
                    "message": "tool call validation failed: parameters did not match schema",
                }
            }
        )
        with (
            patch(
                "bottleneckbench.providers.urlopen",
                return_value=failure,
            ) as urlopen,
            self.assertRaisesRegex(AgnesError, "tool call validation failed"),
        ):
            ChatCompletionsClient(
                endpoint="https://example.test", max_retries=2
            ).complete(self._request())

        self.assertEqual(urlopen.call_count, 1)

    def test_choice_level_inband_error_is_retried(self) -> None:
        failure = _PayloadResponse(
            {
                "choices": [
                    {
                        "finish_reason": "error",
                        "message": {"content": "", "tool_calls": []},
                        "error": {
                            "code": 503,
                            "message": "Provider overloaded.",
                            "metadata": {"error_type": "provider_overloaded"},
                        },
                    }
                ]
            }
        )
        with (
            patch(
                "bottleneckbench.providers.urlopen",
                side_effect=[failure, _Response()],
            ) as urlopen,
            patch("bottleneckbench.providers.time.sleep"),
        ):
            response = ChatCompletionsClient(
                endpoint="https://example.test", max_retries=1
            ).complete(self._request())

        self.assertEqual(response.content, "done")
        self.assertEqual(urlopen.call_count, 2)


if __name__ == "__main__":
    unittest.main()
