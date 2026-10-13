"""Secret-safe OpenAI-compatible Chat Completions transport."""

from __future__ import annotations

from dataclasses import replace
import json
import os
from threading import Lock
import time
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener, urlopen

from bottleneckbench.core.agnes import (
    AgnesError,
    ModelRequest,
    ModelResponse,
    parse_chat_completion,
)


_TRANSIENT_ERROR_STATUSES = {
    "DEADLINE_EXCEEDED",
    "INTERNAL",
    "RESOURCE_EXHAUSTED",
    "UNAVAILABLE",
}

_TRANSIENT_INBAND_CODES = {408, 429, 500, 502, 503, 504, 529}
_TRANSIENT_INBAND_TYPES = {
    "provider_overloaded",
    "provider_unavailable",
    "rate_limit_exceeded",
    "server",
    "timeout",
    "unmapped",
}


def _header_retry_delay(
    headers: Mapping[str, str] | None, attempt: int
) -> float:
    """Prefer a numeric Retry-After signal over blind pacing."""

    retry_after = headers.get("Retry-After") if headers is not None else None
    if retry_after is not None:
        try:
            return max(0.0, min(float(retry_after), 60.0))
        except ValueError:
            pass
    return float(min(2**attempt, 30))


def _retry_delay(error: HTTPError, attempt: int) -> float:
    return _header_retry_delay(error.headers, attempt)


def _error_details(body: bytes) -> tuple[str, str | None, bool]:
    """Return message, provider status, and whether the error body was JSON."""

    try:
        payload = json.loads(body.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        return "provider returned a non-JSON error response", None, False
    candidates = payload if isinstance(payload, list) else [payload]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        error = candidate.get("error")
        if not isinstance(error, dict):
            continue
        message = str(error.get("message", "provider HTTP error"))
        status = error.get("status")
        return message, str(status) if status is not None else None, True
    return "provider HTTP error", None, True


def _inband_error(payload: object) -> tuple[int | None, str, str | None] | None:
    """Extract OpenRouter errors carried inside an HTTP-200 JSON response."""

    if not isinstance(payload, dict):
        return None
    raw_error = payload.get("error")
    if not isinstance(raw_error, dict):
        choices = payload.get("choices")
        if isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict):
            raw_error = choices[0].get("error")
    if not isinstance(raw_error, dict):
        return None
    raw_code = raw_error.get("code")
    try:
        code = int(raw_code) if raw_code is not None else None
    except (TypeError, ValueError):
        code = None
    message = str(raw_error.get("message", "provider response error"))
    metadata = raw_error.get("metadata")
    error_type = (
        str(metadata.get("error_type"))
        if isinstance(metadata, dict) and metadata.get("error_type") is not None
        else None
    )
    return code, message, error_type


def _retryable_inband_error(
    code: int | None, message: str, error_type: str | None
) -> bool:
    """Classify typed provider-availability failures without retrying bad schemas."""

    normalized = message.lower()
    if (
        "tool call validation failed" in normalized
        or "did not match schema" in normalized
    ):
        return False
    if error_type is not None:
        return error_type in _TRANSIENT_INBAND_TYPES
    return code in _TRANSIENT_INBAND_CODES


def _stream_payload(body: bytes) -> JSON:
    """Assemble one OpenAI-compatible SSE chat completion deterministically."""

    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("stream response is not UTF-8") from exc
    events: list[JSON] = []
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        raw = line[5:].strip()
        if not raw or raw == "[DONE]":
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("stream contained a non-JSON data event") from exc
        if not isinstance(event, dict):
            raise ValueError("stream data event is not an object")
        events.append(event)
    if not events:
        raise ValueError("stream did not contain any data events")
    for event in events:
        if _inband_error(event) is not None:
            return event

    models = {
        str(event["model"])
        for event in events
        if isinstance(event.get("model"), str) and event.get("model")
    }
    if len(models) != 1:
        raise ValueError("stream must expose exactly one served model")
    identifiers = [
        str(event["id"])
        for event in events
        if isinstance(event.get("id"), str) and event.get("id")
    ]
    content: list[str] = []
    reasoning: list[str] = []
    calls: dict[int, JSON] = {}
    finish_reason: str | None = None
    usage: JSON | None = None
    for event in events:
        if isinstance(event.get("usage"), dict):
            usage = event["usage"]
        choices = event.get("choices")
        if not isinstance(choices, list) or not choices:
            continue
        if len(choices) != 1 or not isinstance(choices[0], dict):
            raise ValueError("stream event must contain at most one choice")
        choice = choices[0]
        if isinstance(choice.get("finish_reason"), str):
            finish_reason = str(choice["finish_reason"])
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            continue
        if isinstance(delta.get("content"), str):
            content.append(str(delta["content"]))
        if isinstance(delta.get("reasoning_content"), str):
            reasoning.append(str(delta["reasoning_content"]))
        raw_calls = delta.get("tool_calls")
        if raw_calls is None:
            continue
        if not isinstance(raw_calls, list):
            raise ValueError("stream tool_calls must be a list")
        for raw_call in raw_calls:
            if not isinstance(raw_call, dict):
                raise ValueError("stream tool-call fragment is not an object")
            index = raw_call.get("index")
            if not isinstance(index, int) or index < 0:
                raise ValueError("stream tool-call fragment is missing an index")
            call = calls.setdefault(
                index,
                {
                    "id_parts": [],
                    "name_parts": [],
                    "argument_parts": [],
                    "extra_content": None,
                },
            )
            if isinstance(raw_call.get("id"), str):
                call["id_parts"].append(str(raw_call["id"]))
            function = raw_call.get("function")
            if isinstance(function, dict):
                if isinstance(function.get("name"), str):
                    call["name_parts"].append(str(function["name"]))
                if isinstance(function.get("arguments"), str):
                    call["argument_parts"].append(str(function["arguments"]))
            extra_content = raw_call.get("extra_content")
            if extra_content is not None:
                if not isinstance(extra_content, dict):
                    raise ValueError("stream tool-call extra_content is not an object")
                if (
                    call["extra_content"] is not None
                    and call["extra_content"] != extra_content
                ):
                    raise ValueError("stream tool-call extra_content changed across fragments")
                call["extra_content"] = extra_content
    if finish_reason is None:
        raise ValueError("stream is missing a terminal finish reason")
    tool_calls: list[JSON] = []
    for index in sorted(calls):
        call = calls[index]
        raw_call: JSON = {
            "id": "".join(call["id_parts"]),
            "type": "function",
            "function": {
                "name": "".join(call["name_parts"]),
                "arguments": "".join(call["argument_parts"]),
            },
        }
        if call["extra_content"] is not None:
            raw_call["extra_content"] = call["extra_content"]
        tool_calls.append(raw_call)
    return {
        "id": identifiers[-1] if identifiers else "",
        "model": next(iter(models)),
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {
                    "content": "".join(content),
                    "reasoning_content": "".join(reasoning),
                    "tool_calls": tool_calls,
                },
            }
        ],
        "usage": usage,
    }


class ChatCompletionsClient:
    """Minimal provider-neutral client; credentials never enter artifacts."""

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str | None = None,
        api_key_env: str | None = None,
        timeout_seconds: float = 120.0,
        max_retries: int = 2,
        minimum_request_interval_seconds: float = 0.0,
        api_key_header: str = "Authorization",
        provider_only: Sequence[str] = (),
        provider_allow_fallbacks: bool = True,
        provider_data_collection: str | None = None,
        provider_require_parameters: bool = False,
        provider_quantizations: Sequence[str] = (),
        service_tier: str | None = None,
        accepted_response_models: Sequence[str] = (),
        proxy_url: str | None = None,
        omit_temperature: bool = False,
        omit_seed: bool = False,
        omit_parallel_tool_calls: bool = False,
        require_usage: bool = True,
        stream: bool = False,
        disable_response_cache: bool = False,
        response_observer: Callable[[Mapping[str, object]], None] | None = None,
        before_attempt: Callable[[], None] | None = None,
    ) -> None:
        if not endpoint.startswith(("http://", "https://")):
            raise AgnesError("chat-completions endpoint must be HTTP(S)")
        secret = api_key
        if api_key_env:
            secret = secret or os.environ.get(api_key_env)
            if not secret:
                raise AgnesError(f"required API key environment variable is missing: {api_key_env}")
        self.endpoint = endpoint
        self._api_key = secret
        self.timeout_seconds = timeout_seconds
        if max_retries < 0:
            raise AgnesError("max_retries must be non-negative")
        self.max_retries = max_retries
        if minimum_request_interval_seconds < 0:
            raise AgnesError("minimum request interval must be non-negative")
        self.minimum_request_interval_seconds = minimum_request_interval_seconds
        if api_key_header not in {"Authorization", "x-goog-api-key"}:
            raise AgnesError("unsupported API-key header")
        self.api_key_header = api_key_header
        self.provider_only = tuple(provider_only)
        if any(not value for value in self.provider_only):
            raise AgnesError("provider_only entries must be nonempty")
        self.provider_allow_fallbacks = provider_allow_fallbacks
        if provider_data_collection not in {None, "allow", "deny"}:
            raise AgnesError("unsupported provider data-collection policy")
        self.provider_data_collection = provider_data_collection
        self.provider_require_parameters = provider_require_parameters
        self.provider_quantizations = tuple(provider_quantizations)
        if any(not value for value in self.provider_quantizations):
            raise AgnesError("provider quantizations must be nonempty")
        if service_tier not in {None, "default", "flex", "priority"}:
            raise AgnesError("unsupported service tier")
        self.service_tier = service_tier
        self.accepted_response_models = tuple(accepted_response_models)
        if any(not value for value in self.accepted_response_models):
            raise AgnesError("accepted response models must be nonempty")
        if proxy_url is not None and not proxy_url.startswith(("http://", "https://")):
            raise AgnesError("proxy URL must be HTTP(S)")
        self.proxy_url = proxy_url
        self.omit_temperature = omit_temperature
        self.omit_seed = omit_seed
        self.omit_parallel_tool_calls = omit_parallel_tool_calls
        self.require_usage = require_usage
        self.stream = stream
        self.disable_response_cache = disable_response_cache
        self.response_observer = response_observer
        self.before_attempt = before_attempt
        self._proxy_open = (
            build_opener(
                ProxyHandler({"http": proxy_url, "https": proxy_url})
            ).open
            if proxy_url is not None
            else None
        )
        self._request_lock = Lock()
        self._next_request_at = 0.0
        self._stats_lock = Lock()
        self._retry_count = 0
        self._retry_event_times: list[float] = []

    @property
    def retry_count(self) -> int:
        with self._stats_lock:
            return self._retry_count

    @property
    def retry_event_times(self) -> tuple[float, ...]:
        with self._stats_lock:
            return tuple(self._retry_event_times)

    def _record_retry(self) -> None:
        with self._stats_lock:
            self._retry_count += 1
            self._retry_event_times.append(time.perf_counter())

    def _wait_for_request_slot(self) -> None:
        if not self.minimum_request_interval_seconds:
            return
        with self._request_lock:
            delay = self._next_request_at - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self._next_request_at = (
                time.monotonic() + self.minimum_request_interval_seconds
            )

    def _authorization_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._api_key:
            headers[self.api_key_header] = (
                f"Bearer {self._api_key}"
                if self.api_key_header == "Authorization"
                else self._api_key
            )
        return headers

    def _open(self, request: Request):
        open_request = self._proxy_open or urlopen
        return open_request(request, timeout=self.timeout_seconds)

    def _observe_response(
        self,
        *,
        attempt: int,
        request_payload: Mapping[str, object],
        response_body: bytes,
        status: int,
        generation_id: str | None,
        transport_error: str | None = None,
    ) -> None:
        if self.response_observer is None:
            return
        event: dict[str, object] = {
            "attempt": attempt + 1,
            "generation_id": generation_id,
            "http_status": status,
            "request": dict(request_payload),
            "response_body": response_body.decode("utf-8", "replace"),
        }
        if transport_error is not None:
            event["transport_error"] = transport_error
        self.response_observer(event)

    def complete(self, request: ModelRequest) -> ModelResponse:
        payload = request.to_json()
        if self.omit_temperature:
            payload.pop("temperature", None)
        if self.omit_seed:
            payload.pop("seed", None)
        if request.tools and not self.omit_parallel_tool_calls:
            payload["parallel_tool_calls"] = False
        provider: dict[str, object] = {}
        if self.provider_only:
            provider["only"] = list(self.provider_only)
        if not self.provider_allow_fallbacks:
            provider["allow_fallbacks"] = False
        if self.provider_data_collection is not None:
            provider["data_collection"] = self.provider_data_collection
        if self.provider_require_parameters:
            provider["require_parameters"] = True
        if self.provider_quantizations:
            provider["quantizations"] = list(self.provider_quantizations)
        if provider:
            payload["provider"] = provider
        if self.service_tier is not None:
            payload["service_tier"] = self.service_tier
        if self.stream:
            payload["stream"] = True
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            **self._authorization_headers(),
        }
        if self.disable_response_cache:
            headers["X-OpenRouter-Cache"] = "false"
        http_request = Request(
            self.endpoint,
            data=body,
            method="POST",
            headers=headers,
        )
        generation_id: str | None = None
        response_payload: Any = None
        for attempt in range(self.max_retries + 1):
            if self.before_attempt is not None:
                self.before_attempt()
            self._wait_for_request_slot()
            attempt_generation_id: str | None = None
            try:
                with self._open(http_request) as response:
                    response_body = response.read()
                    response_headers = getattr(response, "headers", None)
                    if response_headers is not None:
                        header_value = response_headers.get("X-Generation-Id")
                        if isinstance(header_value, str) and header_value:
                            attempt_generation_id = header_value
                    self._observe_response(
                        attempt=attempt,
                        request_payload=payload,
                        response_body=response_body,
                        status=int(getattr(response, "status", 200)),
                        generation_id=attempt_generation_id,
                    )
            except HTTPError as exc:
                error_body = exc.read()
                self._observe_response(
                    attempt=attempt,
                    request_payload=payload,
                    response_body=error_body,
                    status=exc.code,
                    generation_id=None,
                )
                message, provider_status, structured = _error_details(error_body)
                retryable = (
                    exc.code in {408, 429, 500, 502, 503, 504}
                    or provider_status in _TRANSIENT_ERROR_STATUSES
                    or (exc.code == 400 and not structured)
                )
                if retryable and attempt < self.max_retries:
                    self._record_retry()
                    time.sleep(_retry_delay(exc, attempt))
                    continue
                raise AgnesError(f"provider HTTP {exc.code}: {message[:300]}") from exc
            except (URLError, TimeoutError, OSError) as exc:
                self._observe_response(
                    attempt=attempt,
                    request_payload=payload,
                    response_body=b"",
                    status=0,
                    generation_id=None,
                    transport_error=type(exc).__name__,
                )
                if attempt < self.max_retries:
                    self._record_retry()
                    time.sleep(2**attempt)
                    continue
                raise AgnesError(
                    f"provider transport failure: {type(exc).__name__}"
                ) from exc
            try:
                response_payload = (
                    _stream_payload(response_body)
                    if self.stream
                    else json.loads(response_body.decode("utf-8"))
                )
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                if attempt < self.max_retries:
                    self._record_retry()
                    time.sleep(_header_retry_delay(response_headers, attempt))
                    continue
                raise AgnesError("provider returned a non-JSON response") from exc
            carried_error = _inband_error(response_payload)
            if carried_error is not None:
                code, message, error_type = carried_error
                if (
                    _retryable_inband_error(code, message, error_type)
                    and attempt < self.max_retries
                ):
                    self._record_retry()
                    time.sleep(_header_retry_delay(response_headers, attempt))
                    continue
                label = str(code) if code is not None else "unknown"
                detail = f" [{error_type}]" if error_type is not None else ""
                raise AgnesError(
                    f"provider response error {label}{detail}: {message[:300]}"
                )
            generation_id = attempt_generation_id
            break
        served_model = (
            response_payload.get("model")
            if isinstance(response_payload, dict)
            else None
        )
        accepted = self.accepted_response_models or (request.model,)
        if served_model not in accepted:
            raise AgnesError(
                f"served model mismatch: expected one of {accepted!r}, "
                f"received {served_model!r}"
            )
        parsed = parse_chat_completion(
            response_payload, expected_model=str(served_model)
        )
        if self.stream:
            parsed = replace(
                parsed,
                usage_source=(
                    "stream_terminal_event"
                    if parsed.total_tokens > 0
                    else "stream_usage_missing"
                ),
            )
        if generation_id is not None:
            parsed = replace(parsed, raw_id=generation_id)
        if self.require_usage and parsed.total_tokens <= 0:
            raise AgnesError("provider response is missing positive token usage")
        return parsed
