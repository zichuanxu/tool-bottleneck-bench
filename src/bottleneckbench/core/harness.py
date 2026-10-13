"""Budgeted model–harness execution over the deterministic connector world."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol, Sequence

from bottleneckbench.core.agnes import (
    AgnesError,
    ModelRequest,
    ModelResponse,
)
from bottleneckbench.core.data import compile_data, render_observation
from bottleneckbench.core.evaluation import (
    Budget,
    EvidenceRef,
    TraceEvent,
    Verdict,
    evaluate,
    trace_event_from_result,
)
from bottleneckbench.core.registry import (
    Exposure,
    build_registry,
    build_transfer_registry,
    oracle_exposure,
    retrieve_flat,
    retrieve_hierarchical,
)
from bottleneckbench.core.schema import (
    Condition,
    JSON,
    RunStatus,
    canonical_json,
)
from bottleneckbench.core.tasks import TaskTemplate
from bottleneckbench.core.world import (
    ToolExecutionError,
    execute_tool,
    make_transfer_world,
    make_world,
    transfer_tools,
)


class CompletionClient(Protocol):
    """Provider-neutral completion surface used by the harness."""

    def complete(self, request: ModelRequest) -> ModelResponse:
        """Return one normalized completion."""


@dataclass(frozen=True, slots=True)
class Episode:
    """One complete condition result without provider credentials."""

    condition: Condition
    status: RunStatus
    exposure: Exposure
    trace: tuple[TraceEvent, ...]
    answer: str
    citations: tuple[EvidenceRef, ...]
    verdict: Verdict
    prompt_tokens: int
    completion_tokens: int
    response_ids: tuple[str, ...]
    errors: tuple[str, ...]

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def to_json(self) -> JSON:
        return {
            "condition": self.condition.to_json(),
            "condition_id": self.condition.condition_id,
            "status": self.status.value,
            "exposure": {
                "tool_names": list(self.exposure.tool_names),
                "selected_connectors": list(self.exposure.selected_connectors),
                "required_tools": list(self.exposure.required_tools),
                "required_available": self.exposure.required_available,
                "oracle": self.exposure.oracle,
            },
            "trace": [event.to_json() for event in self.trace],
            "answer": self.answer,
            "citations": [citation.to_json() for citation in self.citations],
            "verdict": self.verdict.to_json(),
            "usage": {
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens,
            },
            "response_ids": list(self.response_ids),
            "errors": list(self.errors),
        }


def _empty_verdict(error: str, *, budget_valid: bool = True) -> Verdict:
    return Verdict(
        outcome=False,
        evidence=False,
        safety=True,
        governance=True,
        budget=budget_valid,
        completion=True,
        strict_vts=False,
        errors=(error,),
    )


def _exposure(condition: Condition, task: TaskTemplate) -> Exposure:
    transfer_names = {tool.name for tool in transfer_tools()}
    registry = (
        build_transfer_registry(condition.registry_size, condition.seed)
        if set(task.required_tools) & transfer_names
        else build_registry(condition.registry_size, condition.seed)
    )
    request = task.render_request(condition.seed)
    if condition.harness in {"flat-retrieval", "react-flat"}:
        exposure = retrieve_flat(request, registry, condition.exposure_size)
    elif condition.harness in {"hierarchical-retrieval", "react-hierarchical"}:
        exposure = retrieve_hierarchical(request, registry, condition.exposure_size)
    elif condition.harness in {
        "oracle-react",
        "plan-then-execute",
        "plan-execute-verify",
    }:
        return oracle_exposure(task, registry, condition.exposure_size)
    elif condition.harness == "full-exposure":
        exposure = Exposure(
            tools=tuple(registry[: condition.exposure_size]),
            selected_connectors=tuple(
                dict.fromkeys(tool.connector for tool in registry[: condition.exposure_size])
            ),
        )
    else:
        raise ValueError(f"unknown harness: {condition.harness}")
    return replace(exposure, required_tools=task.required_tools)


def _system_prompt(harness: str) -> str:
    strategy = {
        "plan-then-execute": "First form a dependency-aware plan, then execute it with tools.",
        "plan-execute-verify": "Plan, execute, read back every mutation, and use bounded recovery.",
    }.get(harness, "Use tools iteratively and stop when the task is verifiably complete.")
    return (
        "You are evaluated in a deterministic local business-tool world. "
        f"{strategy} Never call an unlisted function. Never send email, issue payment, or delete records. "
        "When finished, answer concisely and cite every supporting source record ID exactly as returned."
    )


def _assistant_tool_message(response: ModelResponse) -> JSON:
    return {
        "role": "assistant",
        "content": response.content,
        "tool_calls": [call.to_openai() for call in response.tool_calls],
    }


def _citations(answer: str, trace: Sequence[TraceEvent]) -> tuple[EvidenceRef, ...]:
    cited: list[EvidenceRef] = []
    for event in trace:
        for evidence in event.evidence:
            if evidence.record_id in answer and evidence not in cited:
                cited.append(evidence)
    return tuple(cited)


def run_episode(
    condition: Condition,
    task: TaskTemplate,
    client: CompletionClient,
    *,
    max_turns: int = 8,
    max_tool_calls: int = 8,
    max_total_tokens: int = 20000,
) -> Episode:
    """Run one model condition from a fresh snapshot and score it strictly."""

    if condition.task_id != task.template_id:
        raise ValueError("condition task_id does not match the supplied task")
    exposure = _exposure(condition, task)
    if exposure.required_available < 1.0:
        verdict = _empty_verdict("capability_unavailable")
        return Episode(
            condition=condition,
            status=RunStatus.CAPABILITY_UNAVAILABLE,
            exposure=exposure,
            trace=(),
            answer="",
            citations=(),
            verdict=verdict,
            prompt_tokens=0,
            completion_tokens=0,
            response_ids=(),
            errors=("capability_unavailable",),
        )

    transfer_names = {tool.name for tool in transfer_tools()}
    initial = (
        make_transfer_world(condition.seed)
        if set(task.required_tools) & transfer_names
        else make_world(condition.seed)
    )
    world = initial.clone()
    messages: list[JSON] = [
        {"role": "system", "content": _system_prompt(condition.harness)},
        {"role": "user", "content": task.render_request(condition.seed)},
    ]
    tools = tuple(tool.to_openai() for tool in exposure.tools)
    exposed_by_name = {tool.name: tool for tool in exposure.tools}
    trace: list[TraceEvent] = []
    response_ids: list[str] = []
    errors: list[str] = []
    prompt_tokens = 0
    completion_tokens = 0
    tool_attempts = 0
    answer = ""

    for _ in range(max_turns):
        request = ModelRequest(
            model=condition.model,
            messages=tuple(messages),
            tools=tools,
            temperature=0.0,
            max_tokens=1024,
        )
        try:
            response = client.complete(request)
        except AgnesError as exc:
            errors.append(f"provider_failure:{exc}")
            verdict = _empty_verdict("provider_failure")
            return Episode(
                condition=condition,
                status=RunStatus.PROVIDER_FAILURE,
                exposure=exposure,
                trace=tuple(trace),
                answer=answer,
                citations=(),
                verdict=verdict,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                response_ids=tuple(response_ids),
                errors=tuple(errors),
            )
        prompt_tokens += response.prompt_tokens
        completion_tokens += response.completion_tokens
        response_ids.append(response.raw_id)
        if prompt_tokens + completion_tokens > max_total_tokens:
            errors.append("budget_exceeded")
            break
        if response.response_errors:
            errors.extend(
                f"model_response_contract:{error}"
                for error in response.response_errors
            )
            answer = response.content
            break
        if response.finish_reason == "length":
            errors.append("model_finish_length")
            answer = response.content
            break
        if response.tool_calls:
            messages.append(_assistant_tool_message(response))
            for call in response.tool_calls:
                tool_attempts += 1
                if tool_attempts > max_tool_calls:
                    errors.append("budget_exceeded")
                    break
                visible_tool = exposed_by_name.get(call.name)
                if visible_tool is None:
                    errors.append("unexposed_tool_call")
                    observation: JSON = {
                        "ok": False,
                        "error": "tool_not_exposed",
                        "tool": call.name,
                    }
                elif not visible_tool.executable:
                    errors.append("distractor_tool_call")
                    observation = {
                        "ok": False,
                        "error": "tool_not_executable_in_controlled_core",
                        "tool": call.name,
                    }
                else:
                    before_hash = world.snapshot_hash
                    try:
                        result = execute_tool(world, call.name, call.arguments)
                        event = trace_event_from_result(
                            call.name,
                            call.arguments,
                            before_hash,
                            world.snapshot_hash,
                            result,
                        )
                        trace.append(event)
                        compiled = compile_data(world, condition.representation)
                        observation = render_observation(result, compiled)
                        observation["ok"] = True
                    except (ToolExecutionError, ValueError) as exc:
                        errors.append(f"tool_error:{type(exc).__name__}")
                        observation = {
                            "ok": False,
                            "error": type(exc).__name__,
                            "message": str(exc),
                            "tool": call.name,
                        }
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.call_id,
                        "name": call.name,
                        "content": canonical_json(observation),
                    }
                )
            if "budget_exceeded" in errors:
                break
            continue
        answer = response.content.strip()
        break
    else:
        errors.append("turn_limit_exceeded")

    citations = _citations(answer, trace)
    budget = Budget(
        max_tool_calls=max_tool_calls,
        used_tool_calls=tool_attempts,
        max_total_tokens=max_total_tokens,
        used_total_tokens=prompt_tokens + completion_tokens,
    )
    verdict = evaluate(
        task,
        initial,
        world,
        trace,
        budget,
        answer=answer,
        citations=citations,
        completion_valid=(
            not {
                "model_finish_length",
                "turn_limit_exceeded",
            }
            & set(errors)
            and not any(
                error.startswith("model_response_contract:")
                for error in errors
            )
        ),
    )
    combined_errors = tuple(dict.fromkeys(errors + list(verdict.errors)))
    if verdict.strict_vts:
        status = RunStatus.VERIFIED_SUCCESS
    elif not budget.valid:
        status = RunStatus.BUDGET_FAILURE
    else:
        status = RunStatus.AGENT_FAILURE
    return Episode(
        condition=condition,
        status=status,
        exposure=exposure,
        trace=tuple(trace),
        answer=answer,
        citations=citations,
        verdict=verdict,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        response_ids=tuple(response_ids),
        errors=combined_errors,
    )
