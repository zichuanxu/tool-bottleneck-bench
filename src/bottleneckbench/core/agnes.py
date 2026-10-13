"""Secret-safe Agnes AI Chat Completions adapter."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from bottleneckbench.core.schema import JSON


class AgnesError(RuntimeError):
    """Provider, transport, or response-contract failure."""


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One parsed native function call."""

    call_id: str
    name: str
    arguments: JSON
    extra_content: JSON | None = None

    def to_openai(self) -> JSON:
        payload: JSON = {
            "id": self.call_id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(
                    self.arguments,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            },
        }
        if self.extra_content is not None:
            payload["extra_content"] = self.extra_content
        return payload


@dataclass(frozen=True, slots=True)
class ModelRequest:
    """Provider-independent chat request used by the harness."""

    model: str
    messages: tuple[JSON, ...]
    tools: tuple[JSON, ...] = ()
    temperature: float = 0.0
    max_tokens: int = 1024
    seed: int | None = None
    tool_choice: str | JSON = "auto"

    def to_json(self) -> JSON:
        payload: JSON = {
            "model": self.model,
            "messages": list(self.messages),
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self.tools:
            payload["tools"] = list(self.tools)
            payload["tool_choice"] = self.tool_choice
        if self.seed is not None:
            payload["seed"] = self.seed
        return payload


@dataclass(frozen=True, slots=True)
class ModelResponse:
    """Normalized response with reasoning separated from visible content."""

    model: str
    content: str
    reasoning: str
    tool_calls: tuple[ToolCall, ...]
    finish_reason: str
    prompt_tokens: int
    completion_tokens: int
    raw_id: str
    response_errors: tuple[str, ...] = ()
    usage_source: str = "response_body"

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def _usage_integer(usage: object, key: str) -> int:
    if not isinstance(usage, dict):
        return 0
    value = usage.get(key, 0)
    return int(value) if isinstance(value, (int, float)) else 0


def parse_chat_completion(payload: object, *, expected_model: str) -> ModelResponse:
    """Parse one OpenAI-style Chat Completions object fail closed."""

    if not isinstance(payload, dict):
        raise AgnesError("provider response is not a JSON object")
    served_model = payload.get("model")
    if served_model != expected_model:
        raise AgnesError(
            f"served model mismatch: expected {expected_model!r}, received {served_model!r}"
        )
    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise AgnesError("provider response must contain exactly one choice")
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict):
        raise AgnesError("provider choice does not contain a message object")
    content_value = message.get("content", "")
    reasoning_value = message.get("reasoning_content", "")
    content = "" if content_value is None else str(content_value)
    reasoning = "" if reasoning_value is None else str(reasoning_value)
    parsed_calls: list[ToolCall] = []
    response_errors: list[str] = []
    raw_calls = message.get("tool_calls", [])
    if raw_calls is None:
        raw_calls = []
    if not isinstance(raw_calls, list):
        response_errors.append("tool_calls_not_list")
        raw_calls = []
    for index, raw_call in enumerate(raw_calls):
        prefix = f"tool_call_{index}"
        if not isinstance(raw_call, dict):
            response_errors.append(f"{prefix}_not_object")
            continue
        function = raw_call.get("function")
        if not isinstance(function, dict):
            response_errors.append(f"{prefix}_missing_function")
            continue
        raw_arguments = function.get("arguments")
        if not isinstance(raw_arguments, str):
            response_errors.append(f"{prefix}_arguments_not_json_string")
            continue
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError:
            response_errors.append(f"{prefix}_arguments_invalid_json")
            continue
        if not isinstance(arguments, dict) or not all(
            isinstance(key, str) for key in arguments
        ):
            response_errors.append(f"{prefix}_arguments_not_object")
            continue
        call_id = raw_call.get("id")
        name = function.get("name")
        if not isinstance(call_id, str) or not call_id:
            response_errors.append(f"{prefix}_missing_id")
            continue
        if not isinstance(name, str) or not name:
            response_errors.append(f"{prefix}_missing_function_name")
            continue
        extra_content = raw_call.get("extra_content")
        if extra_content is not None and not isinstance(extra_content, dict):
            response_errors.append(f"{prefix}_extra_content_not_object")
            continue
        parsed_calls.append(
            ToolCall(
                call_id=call_id,
                name=name,
                arguments=arguments,
                extra_content=extra_content,
            )
        )
    finish_reason = choice.get("finish_reason")
    if not isinstance(finish_reason, str):
        raise AgnesError("provider choice is missing finish_reason")
    usage = payload.get("usage")
    return ModelResponse(
        model=expected_model,
        content=content,
        reasoning=reasoning,
        tool_calls=tuple(parsed_calls),
        finish_reason=finish_reason,
        prompt_tokens=_usage_integer(usage, "prompt_tokens"),
        completion_tokens=_usage_integer(usage, "completion_tokens"),
        raw_id=str(payload.get("id", "")),
        response_errors=tuple(response_errors),
    )


class AgnesClient:
    """Minimal synchronous client that never serializes its authorization secret."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        endpoint: str | None = None,
        timeout_seconds: float = 90.0,
    ) -> None:
        secret = api_key or os.environ.get("AGNES_API_KEY")
        if not secret:
            raise AgnesError("AGNES_API_KEY is required in the environment")
        self._api_key = secret
        self.endpoint = endpoint or os.environ.get(
            "AGNES_CHAT_COMPLETIONS_URL",
            "https://apihub.agnes-ai.com/v1/chat/completions",
        )
        self.timeout_seconds = timeout_seconds

    def complete(self, request: ModelRequest) -> ModelResponse:
        """Submit one exact request and normalize the response."""

        body = json.dumps(request.to_json(), ensure_ascii=False).encode("utf-8")
        http_request = Request(
            self.endpoint,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(http_request, timeout=self.timeout_seconds) as response:
                response_body = response.read()
        except HTTPError as exc:
            try:
                error_payload = json.loads(exc.read().decode("utf-8", "replace"))
                error_object = error_payload.get("error", {})
                message = str(error_object.get("message", "provider HTTP error"))
            except (json.JSONDecodeError, AttributeError):
                message = "provider HTTP error"
            raise AgnesError(f"Agnes HTTP {exc.code}: {message[:300]}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise AgnesError(f"Agnes transport failure: {type(exc).__name__}") from exc
        try:
            payload: Any = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AgnesError("Agnes returned a non-JSON response") from exc
        return parse_chat_completion(payload, expected_model=request.model)
