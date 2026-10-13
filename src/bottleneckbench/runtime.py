"""Provider-neutral execution, lossless observation delivery, and exact scoring."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
import inspect
import json
from time import perf_counter
from typing import Mapping, Protocol, Sequence

from bottleneckbench.design import (
    CapacityCondition,
    ProgramStep,
    Split,
    TwinTask,
    build_world,
    certify_minimal_program,
    compile_task,
    is_distractor,
    source_route,
    tasks_for_split,
    tool_source_route,
    underlying_tool_name,
    visible_menu,
)
from bottleneckbench.core.agnes import AgnesError, ModelRequest, ModelResponse, ToolCall
from bottleneckbench.core.data import (
    compile_data,
    observation_source_facts,
    render_observation,
)
from bottleneckbench.core.evaluation import Budget, EvidenceRef
from bottleneckbench.core.schema import (
    JSON,
    Representation,
    RunStatus,
    canonical_json,
    stable_hash,
)
from bottleneckbench.core.world import (
    InvalidArguments,
    ToolExecutionError,
    ToolResult,
    ToolSpec,
    World,
    execute_tool,
)
from bottleneckbench.request_scope import completion_request_scope


class CompletionClient(Protocol):
    def complete(self, request: ModelRequest) -> ModelResponse:
        """Return one normalized completion."""


@dataclass(frozen=True, slots=True)
class TraceEvent:
    """One executed model-selected tool call."""

    tool: str
    connector: str
    arguments: JSON
    before_hash: str
    after_hash: str
    evidence: tuple[EvidenceRef, ...]
    result_records: tuple[JSON, ...]
    mutated: bool
    receipt_id: str | None
    distractor: bool = False

    def to_json(self) -> JSON:
        return {
            "tool": self.tool,
            "connector": self.connector,
            "arguments": deepcopy(self.arguments),
            "before_hash": self.before_hash,
            "after_hash": self.after_hash,
            "evidence": [item.to_json() for item in self.evidence],
            "result_records": deepcopy(list(self.result_records)),
            "mutated": self.mutated,
            "receipt_id": self.receipt_id,
            "distractor": self.distractor,
        }


@dataclass(frozen=True, slots=True)
class CallAttempt:
    """One model-requested function call, including rejected calls."""

    call_id: str
    tool: str
    arguments: JSON
    outcome: str
    error: str | None = None

    def to_json(self) -> JSON:
        return {
            "call_id": self.call_id,
            "tool": self.tool,
            "arguments": deepcopy(self.arguments),
            "outcome": self.outcome,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class CapacityVerdict:
    """Stage-resolved and conjunctive benchmark outcome."""

    availability: bool
    selection: bool
    workflow: bool
    answer: bool
    evidence: bool
    safety: bool
    governance: bool
    budget: bool
    completion: bool
    verified_success: bool
    failure_stage: str | None
    errors: tuple[str, ...]

    def to_json(self) -> JSON:
        return {
            "availability": self.availability,
            "selection": self.selection,
            "workflow": self.workflow,
            "answer": self.answer,
            "evidence": self.evidence,
            "safety": self.safety,
            "governance": self.governance,
            "budget": self.budget,
            "completion": self.completion,
            "verified_success": self.verified_success,
            "failure_stage": self.failure_stage,
            "errors": list(self.errors),
        }


@dataclass(frozen=True, slots=True)
class CapacityEpisode:
    """A complete response-sealed condition result."""

    condition: CapacityCondition
    status: RunStatus
    menu: tuple[str, ...]
    trace: tuple[TraceEvent, ...]
    answer: str
    citations: tuple[EvidenceRef, ...]
    verdict: CapacityVerdict
    prompt_tokens: int
    completion_tokens: int
    response_ids: tuple[str, ...]
    errors: tuple[str, ...]
    call_attempts: tuple[CallAttempt, ...] = ()
    request_tool_counts: tuple[int, ...] = ()
    request_tool_schema_bytes: tuple[int, ...] = ()
    effective_max_tool_calls: int = 0
    effective_max_turns: int = 0
    wall_time_seconds: float = 0.0
    early_stop_reason: str | None = None
    response_models: tuple[str, ...] = ()
    usage_sources: tuple[str, ...] = ()

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def to_json(self) -> JSON:
        payload: JSON = {
            "condition": self.condition.to_json(),
            "condition_id": self.condition.condition_id,
            "status": self.status.value,
            "menu": list(self.menu),
            "trace": [event.to_json() for event in self.trace],
            "answer": self.answer,
            "citations": [item.to_json() for item in self.citations],
            "verdict": self.verdict.to_json(),
            "usage": {
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens,
            },
            "response_ids": list(self.response_ids),
            "usage_sources": list(self.usage_sources),
            "errors": list(self.errors),
            "call_attempts": [attempt.to_json() for attempt in self.call_attempts],
            "efficiency": {
                "model_requests_attempted": len(self.request_tool_counts),
                "model_responses_received": len(self.response_ids),
                "stream_terminal_usage_responses": sum(
                    source == "stream_terminal_event"
                    for source in self.usage_sources
                ),
                "usage_missing_responses": sum(
                    source == "stream_usage_missing"
                    for source in self.usage_sources
                ),
                "request_tool_counts": list(self.request_tool_counts),
                "request_tool_schema_bytes": list(self.request_tool_schema_bytes),
                "tool_schema_exposures": sum(self.request_tool_counts),
                "tool_schema_bytes": sum(self.request_tool_schema_bytes),
                "effective_max_tool_calls": self.effective_max_tool_calls,
                "effective_max_turns": self.effective_max_turns,
                "wall_time_seconds": self.wall_time_seconds,
                "early_stop_reason": self.early_stop_reason,
            },
        }
        if self.response_models:
            payload["response_models"] = list(self.response_models)
        return payload


_MUTATING_TOOLS = {
    "create_email_draft",
    "create_file",
    "create_calendar_event",
    "update_invoice_status",
    "update_crm_status",
    "create_task",
}
_TYPE_DICTIONARY = {
    "accounting": "AccountingRecord: a versioned current accounting record.",
    "email": "EmailMessage: a versioned current email record.",
    "files": "Document: a versioned current file record.",
    "calendar": "CalendarEvent: a versioned current calendar record.",
    "crm": "CRMContact: a versioned current CRM record.",
    "tasks": "ProjectTask: a versioned current project-task record.",
}


def _result_records(result: ToolResult) -> tuple[JSON, ...]:
    records = result.output.get("records")
    if isinstance(records, list):
        return tuple(deepcopy(item) for item in records if isinstance(item, dict))
    record = result.output.get("record")
    if isinstance(record, dict):
        return (deepcopy(record),)
    return ()


def _validate_arguments(tool: ToolSpec, arguments: Mapping[str, object]) -> None:
    schema = tool.input_schema
    required = set(schema.get("required", []))
    properties = set(schema.get("properties", {}))
    keys = set(arguments)
    if missing := required - keys:
        raise InvalidArguments(f"{tool.name} missing required arguments: {sorted(missing)}")
    if extras := keys - properties:
        raise InvalidArguments(f"{tool.name} received extra arguments: {sorted(extras)}")
    if any(not isinstance(arguments[key], str) for key in keys):
        raise InvalidArguments(f"{tool.name} accepts string arguments only")


def execute_capacity_tool(
    world: World,
    task: TwinTask,
    tool: ToolSpec,
    arguments: Mapping[str, object],
) -> ToolResult:
    """Execute one public tool against the workflow-specific access layout."""

    _validate_arguments(tool, arguments)
    operation = underlying_tool_name(tool)
    if is_distractor(tool):
        route = tool_source_route(tool)
        if route is None:
            raise ToolExecutionError(f"distractor lacks source route: {tool.name}")
        return ToolResult(
            ok=True,
            tool=tool.name,
            output={"records": [], "source_route": dict(route)},
            source_ids=(),
        )

    if operation.startswith("search_"):
        search_nodes = [node for node in task.nodes if node.connector == tool.connector]
        query = str(arguments["query"])
        records = []
        for node in search_nodes:
            if query == node.lookup_key:
                source = world.records[node.connector][node.record_id]
                records.append(
                    {
                        "record_id": node.record_id,
                        "version": int(source["version"]),
                        "title": str(source["title"]),
                        "kind": "locator",
                    }
                )
        return ToolResult(
            ok=True,
            tool=tool.name,
            output={"records": records},
            source_ids=tuple(str(record["record_id"]) for record in records),
        )

    if operation.startswith("get_") or operation in _MUTATING_TOOLS:
        return execute_tool(world, operation, arguments)
    raise ToolExecutionError(f"unsupported capacity-surface tool: {tool.name}")


def _trace_event(
    tool: ToolSpec,
    arguments: Mapping[str, object],
    before_hash: str,
    after_hash: str,
    result: ToolResult,
) -> TraceEvent:
    records = _result_records(result)
    evidence = tuple(
        EvidenceRef(
            connector=tool.connector,
            record_id=str(record["record_id"]),
            version=int(record["version"]),
        )
        for record in records
    )
    if tuple(item.record_id for item in evidence) != result.source_ids:
        raise ValueError("tool result source IDs do not match returned records")
    return TraceEvent(
        tool=tool.name,
        connector=tool.connector,
        arguments=deepcopy(dict(arguments)),
        before_hash=before_hash,
        after_hash=after_hash,
        evidence=evidence,
        result_records=records,
        mutated=result.mutated,
        receipt_id=result.receipt_id,
        distractor=is_distractor(tool),
    )


def compile_visible_observation(
    result: ToolResult,
    connector: str,
    seen_records: Mapping[str, Mapping[str, JSON]],
    representation: Representation,
    source_state_hash: str,
) -> JSON:
    """Encode observed records without accepting a query, task, target, or scorer."""

    visible = {
        name: {record_id: deepcopy(record) for record_id, record in records.items()}
        for name, records in seen_records.items()
        if records
    }
    mini_world = World(seed=0, records=visible, permissions=set())
    compiled = compile_data(mini_world, representation)
    observation = render_observation(result, compiled)
    observation["source_state_hash"] = source_state_hash
    observation["visible_history_hash"] = compiled.world_hash
    observation["relation_dictionary"] = (
        {
            "same_invoice": (
                "Both already observed source records contain the same invoice_id value; "
                "the relation adds no unobserved source fact."
            )
        }
        if representation is Representation.LINKED
        else {}
    )
    observation["type_dictionary"] = (
        {name: _TYPE_DICTIONARY[name] for name in sorted(visible)}
        if representation in {Representation.TYPED, Representation.LINKED}
        else {}
    )
    if representation is Representation.GENERIC_COMPACT:
        dictionary = dict(observation["field_dictionary"])
        decoded = observation_source_facts(representation, observation["objects"])
        for item, (_, record) in zip(
            observation["objects"], sorted(decoded.items()), strict=True
        ):
            for encoded, original in zip(item["p"], record, strict=True):
                dictionary[str(encoded)] = str(original)
        observation["field_dictionary"] = dictionary
    return observation


def _remember_records(
    seen_records: dict[str, dict[str, JSON]], connector: str, result: ToolResult
) -> None:
    connector_records = seen_records.setdefault(connector, {})
    for record in _result_records(result):
        connector_records[str(record["record_id"])] = deepcopy(record)


def _event_matches_step(task: TwinTask, step: ProgramStep, event: TraceEvent) -> bool:
    if event.tool != step.tool_name or event.connector != step.connector:
        return False
    expected_arguments = step.argument_map
    if step.step_id != "action":
        if event.arguments != expected_arguments:
            return False
    else:
        for key, value in expected_arguments.items():
            if key != "idempotency_key" and event.arguments.get(key) != value:
                return False
        if not event.mutated or not event.receipt_id:
            return False
    if step.step_id.startswith("search-"):
        target_id = next(
            node.record_id
            for node in task.nodes
            if node.connector == step.connector
            and node.lookup_key == expected_arguments["query"]
        )
        return any(record.get("record_id") == target_id for record in event.result_records)
    if step.step_id.startswith("get-"):
        target_id = str(expected_arguments["record_id"])
        expected = task.expected_fact_map
        return any(
            record.get("record_id") == target_id
            and all(record.get(name) == expected[name] for name in step.produces_facts)
            for record in event.result_records
        )
    if task.action is None:
        return False
    expected_fields = dict(task.action.expected_fields)
    return any(
        all(record.get(name) == value for name, value in expected_fields.items())
        for record in event.result_records
    )


def _program_status(
    task: TwinTask, trace: Sequence[TraceEvent]
) -> tuple[bool, dict[str, tuple[int, TraceEvent]], list[str]]:
    matches: dict[str, tuple[int, TraceEvent]] = {}
    used_positions: set[int] = set()
    errors: list[str] = []
    for step in task.program:
        for position, event in enumerate(trace):
            if position in used_positions:
                continue
            if _event_matches_step(task, step, event):
                matches[step.step_id] = (position, event)
                used_positions.add(position)
                break
        if step.step_id not in matches:
            errors.append(f"missing_program_step:{step.step_id}")
    for step in task.program:
        if step.step_id not in matches:
            continue
        position = matches[step.step_id][0]
        if any(
            parent not in matches or matches[parent][0] >= position
            for parent in step.depends_on
        ):
            errors.append(f"dependency_order_invalid:{step.step_id}")
    return not errors, matches, errors


def _answer_payload(answer: str) -> object:
    try:
        return json.loads(answer)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        candidates = []
        for index, character in enumerate(answer):
            if character != "{":
                continue
            try:
                candidate, _ = decoder.raw_decode(answer, index)
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict) and set(candidate) == {"decision", "facts", "sources"}:
                candidates.append(candidate)
        return candidates[0] if len(candidates) == 1 else None


def _answer_status(task: TwinTask, answer: str) -> tuple[bool, list[str], tuple[str, ...]]:
    payload = _answer_payload(answer)
    if payload is None:
        return False, ["answer_not_json"], ()
    if not isinstance(payload, dict) or set(payload) != {"decision", "facts", "sources"}:
        return False, ["answer_schema_invalid"], ()
    errors: list[str] = []
    if payload.get("decision") != task.family.decision:
        errors.append("decision_invalid")
    if payload.get("facts") != task.expected_fact_map:
        errors.append("answer_facts_invalid")
    raw_sources = payload.get("sources")
    if not isinstance(raw_sources, list) or not all(
        isinstance(value, str) and value for value in raw_sources
    ):
        errors.append("answer_sources_invalid")
        sources: tuple[str, ...] = ()
    else:
        sources = tuple(raw_sources)
        if len(set(sources)) != len(sources):
            errors.append("duplicate_answer_source")
    return not errors, errors, sources


def _evidence_status(
    task: TwinTask,
    trace: Sequence[TraceEvent],
    matches: Mapping[str, tuple[int, TraceEvent]],
    sources: Sequence[str],
) -> tuple[bool, list[str], tuple[EvidenceRef, ...]]:
    errors: list[str] = []
    required_ids = {
        node.record_id
        for node in task.nodes
        if node.facts
    }
    if set(sources) != required_ids:
        errors.append("source_set_invalid")
    trace_refs: dict[str, EvidenceRef] = {}
    for step in task.program:
        if not step.produces_facts or step.step_id not in matches:
            continue
        event = matches[step.step_id][1]
        records = {
            (str(record.get("record_id")), int(record.get("version", 0))): record
            for record in event.result_records
        }
        for item in event.evidence:
            if (
                item.connector != event.connector
                or (item.record_id, item.version) not in records
            ):
                errors.append("trace_evidence_invalid")
                continue
            trace_refs.setdefault(item.record_id, item)
    citations = tuple(trace_refs[source] for source in sources if source in trace_refs)
    if len(citations) != len(sources):
        errors.append("citation_not_trace_bound")
    for step in task.program:
        if not step.produces_facts:
            continue
        match = matches.get(step.step_id)
        if match is None:
            errors.append("fact_without_get_evidence")
            continue
        record_ids = {item.record_id for item in match[1].evidence}
        if not record_ids & set(sources):
            errors.append("fact_source_not_cited")
    return not errors, errors, citations


def _trace_integrity_status(
    initial: World, final: World, trace: Sequence[TraceEvent]
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    expected_before = initial.snapshot_hash
    for event in trace:
        if event.before_hash != expected_before:
            errors.append("trace_hash_chain_invalid")
        if event.mutated != (event.before_hash != event.after_hash):
            errors.append("trace_mutation_flag_invalid")
        if event.mutated != bool(event.receipt_id):
            errors.append("trace_receipt_invalid")
        expected_before = event.after_hash
    if expected_before != final.snapshot_hash:
        errors.append("trace_final_hash_invalid")
    return not errors, errors


def _safety_status(
    task: TwinTask,
    initial: World,
    final: World,
    trace: Sequence[TraceEvent],
) -> tuple[bool, bool, list[str]]:
    errors: list[str] = []
    mutated = [event for event in trace if event.mutated]
    if task.action is None:
        if initial.snapshot() != final.snapshot() or mutated:
            errors.append("unrequested_state_change")
        return not errors, not errors, errors
    if len(mutated) != 1 or mutated[0].tool != task.action.tool_name:
        errors.append("mutation_count_or_tool_invalid")
        return False, False, errors
    event = mutated[0]
    if not event.receipt_id:
        errors.append("mutation_receipt_missing")
    expected_fields = dict(task.action.expected_fields)
    if task.action.target_record_id:
        target_id = task.action.target_record_id
        expected = initial.snapshot()
        record = expected["records"]["accounting"][target_id]
        for name, value in expected_fields.items():
            record[name] = deepcopy(value)
        record["version"] = int(record["version"]) + 1
        if final.snapshot() != expected:
            errors.append("unsafe_or_inexact_state_diff")
    else:
        connector = event.connector
        before_records = initial.records[connector]
        after_records = final.records[connector]
        new_ids = set(after_records) - set(before_records)
        if len(new_ids) != 1:
            errors.append("created_record_count_invalid")
        else:
            created = after_records[next(iter(new_ids))]
            if not all(created.get(name) == value for name, value in expected_fields.items()):
                errors.append("created_record_payload_invalid")
        stripped = final.snapshot()
        for record_id in new_ids:
            stripped["records"][connector].pop(record_id, None)
        if stripped != initial.snapshot():
            errors.append("unsafe_or_inexact_state_diff")
    governance = not any(
        event.mutated and event.tool != task.action.tool_name for event in trace
    )
    if not governance:
        errors.append("unrequested_mutation")
    return not errors, governance, errors


def evaluate_episode(
    task: TwinTask,
    initial: World,
    final: World,
    trace: Sequence[TraceEvent],
    budget: Budget,
    *,
    answer: str,
    availability: bool = True,
    completion_valid: bool = True,
) -> tuple[CapacityVerdict, tuple[EvidenceRef, ...]]:
    """Evaluate exact workflow, answer, evidence, state, and budget contracts."""

    operational = set(task.base_tool_names)
    selection = any(event.tool in operational for event in trace)
    workflow, matches, workflow_errors = _program_status(task, trace)
    trace_integrity, trace_errors = _trace_integrity_status(initial, final, trace)
    workflow = workflow and trace_integrity
    answer_valid, answer_errors, sources = _answer_status(task, answer)
    evidence, evidence_errors, citations = _evidence_status(
        task, trace, matches, sources
    )
    safety, governance, safety_errors = _safety_status(task, initial, final, trace)
    errors = list(
        workflow_errors + trace_errors + answer_errors + evidence_errors + safety_errors
    )
    if not availability:
        errors.append("required_tools_unavailable")
    if not selection:
        errors.append("no_operational_tool_selected")
    if not budget.valid:
        errors.append("budget_exceeded")
    if not completion_valid:
        errors.append("completion_integrity_failed")
    verified = all(
        (
            availability,
            selection,
            workflow,
            answer_valid,
            evidence,
            safety,
            governance,
            budget.valid,
            completion_valid,
        )
    )
    stages = (
        ("availability", availability),
        ("selection", selection),
        ("workflow", workflow),
        ("answer", answer_valid),
        ("evidence", evidence),
        ("safety", safety),
        ("governance", governance),
        ("budget", budget.valid),
        ("completion", completion_valid),
    )
    failure_stage = next((name for name, passed in stages if not passed), None)
    return (
        CapacityVerdict(
            availability=availability,
            selection=selection,
            workflow=workflow,
            answer=answer_valid,
            evidence=evidence,
            safety=safety,
            governance=governance,
            budget=budget.valid,
            completion=completion_valid,
            verified_success=verified,
            failure_stage=failure_stage,
            errors=tuple(dict.fromkeys(errors)),
        ),
        citations,
    )


def _system_prompt(harness: str) -> str:
    strategy = (
        "Before every tool request, privately verify the connector, operation, exact arguments, and remaining "
        "public obligations; after the last tool, verify the evidence and terminal state before answering."
        if harness == "plan-execute-verify"
        or harness.startswith("deliberate-verify-v1@")
        else "Use tools iteratively and stop only when the task is supported by current evidence."
    )
    interface_guide = (
        " Read returned records by interface: raw records place values under payload; typed or linked records "
        "use type for the record kind, place values under fields, and identify their origin under source. "
        "Provenance maps JSON-pointer paths "
        "to source fields. Links and relation_dictionary summarize observed equality links but never replace "
        "reading the cited source record."
        if harness.startswith("interface-guide-v1@")
        else ""
    )
    return (
        "You operate in a deterministic sandbox with synthetic business records. "
        f"{strategy}{interface_guide} Never call an unlisted function. Each search/read description declares workspace, "
        "region, and collection coordinates. Use only tools whose three coordinates exactly match the "
        "required source route in the user request; other routes may contain valid current records for "
        "other workspaces but cannot support this case. "
        "A next_handoff object identifies the connector and exact search query for the next current record; "
        "follow it when more requested facts are still missing. "
        "Avoid wasting the call budget on identical tool calls. Once all requested facts are present, stop using tools and "
        "return raw JSON only, with no markdown fence or explanatory prose. "
        "Never send messages, issue payment, or delete data. Follow the user's exact JSON response schema."
    )


def _assistant_tool_message(response: ModelResponse) -> JSON:
    return {
        "role": "assistant",
        "content": response.content,
        "tool_calls": [call.to_openai() for call in response.tool_calls],
    }


def _failure_verdict(stage: str, error: str) -> CapacityVerdict:
    values = {
        "availability": stage != "availability",
        "selection": False,
        "workflow": False,
        "answer": False,
        "evidence": False,
        "safety": True,
        "governance": True,
        "budget": stage != "budget",
        "completion": stage != "completion",
    }
    return CapacityVerdict(
        **values,
        verified_success=False,
        failure_stage=stage,
        errors=(error,),
    )


def run_episode(
    condition: CapacityCondition,
    task: TwinTask,
    client: CompletionClient,
    *,
    inference_seed: int | None = None,
    max_turns: int | None = 14,
    max_tool_calls: int | None = 12,
    tool_call_slack: int = 2,
    turn_slack: int = 2,
    max_total_tokens: int = 256000,
    max_completion_tokens: int = 2048,
    dynamic_shortlist: bool = False,
    early_stop_infeasible: bool = False,
) -> CapacityEpisode:
    """Execute a fresh joint-cube condition and score it exactly."""

    with completion_request_scope(
        client, condition_id=condition.condition_id, attempt=1
    ):
        return _run_episode_scoped(
            condition,
            task,
            client,
            inference_seed=inference_seed,
            max_turns=max_turns,
            max_tool_calls=max_tool_calls,
            tool_call_slack=tool_call_slack,
            turn_slack=turn_slack,
            max_total_tokens=max_total_tokens,
            max_completion_tokens=max_completion_tokens,
            dynamic_shortlist=dynamic_shortlist,
            early_stop_infeasible=early_stop_infeasible,
        )


def _run_episode_scoped(
    condition: CapacityCondition,
    task: TwinTask,
    client: CompletionClient,
    *,
    inference_seed: int | None,
    max_turns: int | None,
    max_tool_calls: int | None,
    tool_call_slack: int,
    turn_slack: int,
    max_total_tokens: int,
    max_completion_tokens: int,
    dynamic_shortlist: bool,
    early_stop_infeasible: bool,
) -> CapacityEpisode:
    """Implementation under an optional durable condition scope."""

    started_at = perf_counter()
    if condition.task_id != task.task_id or condition.family_id != task.family.family_id:
        raise ValueError("condition identity does not match task")
    if condition.workflow is not task.workflow or condition.seed != task.family.seed:
        raise ValueError("condition workflow or seed does not match task")
    if (
        condition.intent != task.family.intent
        or condition.outcome_variant != task.family.outcome_variant
        or condition.prompt_variant != task.family.prompt_variant
        or condition.branching != task.family.branching
        or condition.action_required != task.family.action_required
    ):
        raise ValueError("condition semantic metadata does not match task")
    if tool_call_slack < 0 or turn_slack < 1:
        raise ValueError("episode slacks are invalid")
    effective_max_tool_calls = (
        len(task.program) + tool_call_slack
        if max_tool_calls is None
        else max_tool_calls
    )
    effective_max_turns = (
        effective_max_tool_calls + turn_slack
        if max_turns is None
        else max_turns
    )
    if effective_max_tool_calls < 0 or effective_max_turns < 1:
        raise ValueError("episode call and turn limits are invalid")
    if max_total_tokens <= 0 or max_completion_tokens <= 0:
        raise ValueError("episode token limits must be positive")
    if inference_seed is not None and (
        not isinstance(inference_seed, int)
        or isinstance(inference_seed, bool)
        or inference_seed < 0
    ):
        raise ValueError("inference seed must be a nonnegative integer")
    menu = visible_menu(task, condition.visible_tools)
    menu_names = tuple(tool.name for tool in menu)
    available = set(task.required_tool_names) <= set(menu_names)
    if not available:
        verdict = _failure_verdict("availability", "required_tools_unavailable")
        return CapacityEpisode(
            condition=condition,
            status=RunStatus.CAPABILITY_UNAVAILABLE,
            menu=menu_names,
            trace=(),
            answer="",
            citations=(),
            verdict=verdict,
            prompt_tokens=0,
            completion_tokens=0,
            response_ids=(),
            errors=("required_tools_unavailable",),
            effective_max_tool_calls=effective_max_tool_calls,
            effective_max_turns=effective_max_turns,
            wall_time_seconds=perf_counter() - started_at,
        )

    initial = build_world(task)
    world = initial.clone()
    exposed = {tool.name: tool for tool in menu}
    operational_menu = tuple(
        tool for tool in menu if tool.name in set(task.base_tool_names)
    )
    if len(operational_menu) != len(task.base_tool_names):
        raise AssertionError("initial menu does not contain the operational shortlist")
    messages: list[JSON] = [
        {"role": "system", "content": _system_prompt(condition.harness)},
        {"role": "user", "content": task.request},
    ]
    tools = tuple(tool.to_openai() for tool in menu)
    seen_records: dict[str, dict[str, JSON]] = {}
    trace: list[TraceEvent] = []
    response_ids: list[str] = []
    response_models: list[str] = []
    usage_sources: list[str] = []
    errors: list[str] = []
    call_attempts: list[CallAttempt] = []
    request_tool_counts: list[int] = []
    request_tool_schema_bytes: list[int] = []
    prompt_tokens = 0
    completion_tokens = 0
    attempts = 0
    answer = ""
    early_stop_reason: str | None = None
    shortlist_activated = False

    for _ in range(effective_max_turns):
        request = ModelRequest(
            model=condition.model,
            messages=tuple(messages),
            tools=tools,
            temperature=0.0,
            max_tokens=max_completion_tokens,
            seed=condition.seed if inference_seed is None else inference_seed,
        )
        request_tool_counts.append(len(request.tools))
        request_tool_schema_bytes.append(
            len(canonical_json(list(request.tools)).encode("utf-8"))
        )
        try:
            response = client.complete(request)
        except AgnesError as exc:
            errors.append(f"provider_failure:{exc}")
            verdict = _failure_verdict("completion", "provider_failure")
            return CapacityEpisode(
                condition,
                RunStatus.PROVIDER_FAILURE,
                menu_names,
                tuple(trace),
                answer,
                (),
                verdict,
                prompt_tokens,
                completion_tokens,
                tuple(response_ids),
                tuple(errors),
                tuple(call_attempts),
                tuple(request_tool_counts),
                tuple(request_tool_schema_bytes),
                effective_max_tool_calls,
                effective_max_turns,
                perf_counter() - started_at,
                early_stop_reason,
                tuple(response_models),
                tuple(usage_sources),
            )
        prompt_tokens += response.prompt_tokens
        completion_tokens += response.completion_tokens
        response_ids.append(response.raw_id)
        response_models.append(response.model)
        usage_sources.append(response.usage_source)
        if prompt_tokens + completion_tokens > max_total_tokens:
            errors.append("budget_exceeded")
            break
        if response.response_errors:
            errors.extend(
                f"model_response_contract:{error}"
                for error in response.response_errors
            )
            answer = response.content.strip()
            break
        if response.finish_reason == "length":
            answer = response.content.strip()
            errors.append("model_finish_length")
            break
        if response.tool_calls:
            # Preserve the provider's assistant batch exactly; OpenAI-compatible
            # backends require all tool results to refer to that one message.
            messages.append(_assistant_tool_message(response))
            executed_operational_call = False
            for call in response.tool_calls:
                attempts += 1
                if attempts > effective_max_tool_calls:
                    errors.append("budget_exceeded")
                    call_attempts.append(
                        CallAttempt(
                            call.call_id,
                            call.name,
                            call.arguments,
                            "budget_blocked",
                            "tool_call_limit_exceeded",
                        )
                    )
                    continue
                tool = exposed.get(call.name)
                if tool is None:
                    errors.append("unexposed_tool_call")
                    call_attempts.append(
                        CallAttempt(
                            call.call_id,
                            call.name,
                            call.arguments,
                            "unexposed",
                            "tool_not_exposed",
                        )
                    )
                    observation = {
                        "ok": False,
                        "tool": call.name,
                        "error": "tool_not_exposed",
                    }
                else:
                    before_hash = world.snapshot_hash
                    try:
                        result = execute_capacity_tool(world, task, tool, call.arguments)
                        event = _trace_event(
                            tool, call.arguments, before_hash, world.snapshot_hash, result
                        )
                        trace.append(event)
                        _remember_records(seen_records, tool.connector, result)
                        observation = compile_visible_observation(
                            result,
                            tool.connector,
                            seen_records,
                            condition.representation,
                            world.snapshot_hash,
                        )
                        observation["ok"] = True
                        call_attempts.append(
                            CallAttempt(
                                call.call_id,
                                call.name,
                                call.arguments,
                                "executed",
                            )
                        )
                        executed_operational_call |= tool.name in task.base_tool_names
                    except (ToolExecutionError, ValueError) as exc:
                        errors.append(f"tool_error:{type(exc).__name__}")
                        call_attempts.append(
                            CallAttempt(
                                call.call_id,
                                call.name,
                                call.arguments,
                                "tool_error",
                                type(exc).__name__,
                            )
                        )
                        observation = {
                            "ok": False,
                            "tool": call.name,
                            "error": type(exc).__name__,
                            "message": str(exc),
                        }
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.call_id,
                        "name": call.name,
                        "content": canonical_json(observation),
                    }
                )
            if (
                dynamic_shortlist
                and executed_operational_call
                and not shortlist_activated
            ):
                exposed = {tool.name: tool for tool in operational_menu}
                tools = tuple(tool.to_openai() for tool in operational_menu)
                shortlist_activated = True
            if "budget_exceeded" in errors:
                break
            if early_stop_infeasible:
                _, matches, _ = _program_status(task, trace)
                remaining_steps = len(task.program) - len(matches)
                remaining_calls = effective_max_tool_calls - attempts
                if remaining_steps > remaining_calls:
                    early_stop_reason = "success_infeasible_under_call_budget"
                    errors.append(early_stop_reason)
                    break
            continue
        answer = response.content.strip()
        break
    else:
        errors.append("turn_limit_exceeded")

    budget = Budget(
        effective_max_tool_calls,
        attempts,
        max_total_tokens,
        prompt_tokens + completion_tokens,
    )
    completion_valid = (
        not {
            "model_finish_length",
            "turn_limit_exceeded",
        }
        & set(errors)
        and not any(
            error.startswith("model_response_contract:") for error in errors
        )
    )
    verdict, citations = evaluate_episode(
        task,
        initial,
        world,
        trace,
        budget,
        answer=answer,
        availability=available,
        completion_valid=completion_valid,
    )
    combined = tuple(dict.fromkeys(errors + list(verdict.errors)))
    status = (
        RunStatus.VERIFIED_SUCCESS
        if verdict.verified_success
        else RunStatus.BUDGET_FAILURE
        if not budget.valid
        else RunStatus.AGENT_FAILURE
    )
    return CapacityEpisode(
        condition=condition,
        status=status,
        menu=menu_names,
        trace=tuple(trace),
        answer=answer,
        citations=citations,
        verdict=verdict,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        response_ids=tuple(response_ids),
        errors=combined,
        call_attempts=tuple(call_attempts),
        request_tool_counts=tuple(request_tool_counts),
        request_tool_schema_bytes=tuple(request_tool_schema_bytes),
        effective_max_tool_calls=effective_max_tool_calls,
        effective_max_turns=effective_max_turns,
        wall_time_seconds=perf_counter() - started_at,
        early_stop_reason=early_stop_reason,
        response_models=tuple(response_models),
        usage_sources=tuple(usage_sources),
    )


def _reference_answer(task: TwinTask) -> str:
    return canonical_json(
        {
            "decision": task.family.decision,
            "facts": task.expected_fact_map,
            "sources": [node.record_id for node in task.nodes if node.facts],
        }
    )


def reference_execution(
    task: TwinTask,
    *,
    representation: Representation = Representation.RAW,
    program: Sequence[ProgramStep] | None = None,
) -> tuple[World, World, tuple[TraceEvent, ...], CapacityVerdict]:
    """Execute a declared or certified alternative program without a model."""

    initial = build_world(task)
    world = initial.clone()
    menu = {tool.name: tool for tool in visible_menu(task, 8)}
    trace: list[TraceEvent] = []
    for step in program or task.program:
        tool = menu[step.tool_name]
        arguments = step.argument_map
        before_hash = world.snapshot_hash
        result = execute_capacity_tool(world, task, tool, arguments)
        trace.append(_trace_event(tool, arguments, before_hash, world.snapshot_hash, result))
    verdict, _ = evaluate_episode(
        task,
        initial,
        world,
        trace,
        Budget(12, len(trace), 10000, 1000),
        answer=_reference_answer(task),
    )
    return initial, world, tuple(trace), verdict


def _topological_programs(program: Sequence[ProgramStep]) -> tuple[tuple[ProgramStep, ...], ...]:
    by_id = {step.step_id: step for step in program}
    orders: list[tuple[ProgramStep, ...]] = []

    def visit(chosen: tuple[ProgramStep, ...], remaining: frozenset[str]) -> None:
        if not remaining:
            orders.append(chosen)
            return
        complete = {step.step_id for step in chosen}
        for step in program:
            if step.step_id in remaining and set(step.depends_on) <= complete:
                visit(chosen + (step,), remaining - {step.step_id})

    visit((), frozenset(by_id))
    return tuple(orders)


def _representation_contract(
    task: TwinTask,
) -> tuple[int, list[str], dict[str, list[int]]]:
    world = build_world(task)
    menu = {tool.name: tool for tool in visible_menu(task, 8)}
    seen: dict[str, dict[str, JSON]] = {}
    failures: list[str] = []
    sizes = {representation.value: [] for representation in Representation}
    observations_checked = 0
    for step in task.program:
        tool = menu[step.tool_name]
        result = execute_capacity_tool(world, task, tool, step.argument_map)
        _remember_records(seen, tool.connector, result)
        observations = {
            representation: compile_visible_observation(
                result,
                tool.connector,
                seen,
                representation,
                world.snapshot_hash,
            )
            for representation in Representation
        }
        decoded = {
            representation: observation_source_facts(
                representation, payload["objects"]
            )
            for representation, payload in observations.items()
        }
        serialized = {
            canonical_json(
                {f"{key[0]}::{key[1]}": record for key, record in value.items()}
            )
            for value in decoded.values()
        }
        if len(serialized) != 1:
            failures.append(f"{task.task_id}:{step.step_id}:decode")
        delivered = {
            representation: canonical_json(payload)
            for representation, payload in observations.items()
        }
        if len(set(delivered.values())) != len(Representation):
            failures.append(f"{task.task_id}:{step.step_id}:indistinct_delivery")
        for representation, payload in delivered.items():
            sizes[representation.value].append(len(payload))
        visible_ids = {record_id for records in seen.values() for record_id in records}
        linked = observations[Representation.LINKED]
        if any(
            not set(edge["source_record_ids"]) <= visible_ids
            for edge in linked["links"]
        ):
            failures.append(f"{task.task_id}:{step.step_id}:link_derivability")
        if not observations[Representation.GENERIC_COMPACT]["field_dictionary"]:
            failures.append(f"{task.task_id}:{step.step_id}:compact_dictionary")
        if not observations[Representation.TYPED]["type_dictionary"]:
            failures.append(f"{task.task_id}:{step.step_id}:type_dictionary")
        if not linked["relation_dictionary"]:
            failures.append(f"{task.task_id}:{step.step_id}:relation_dictionary")
        if any(
            payload["source_ids"] and not payload["provenance"]
            for payload in observations.values()
        ):
            failures.append(f"{task.task_id}:{step.step_id}:provenance")
        observations_checked += 1
    return observations_checked, failures, sizes


def _benign_miss_contract(task: TwinTask) -> list[str]:
    world = build_world(task)
    menu = {tool.name: tool for tool in visible_menu(task, 8)}
    failures: list[str] = []
    for tool in menu.values():
        if is_distractor(tool) or not tool.name.startswith("search_"):
            continue
        before = world.snapshot_hash
        try:
            result = execute_capacity_tool(
                world,
                task,
                tool,
                {"query": f"NO-MATCH-{task.family.case_id}"},
            )
        except (ToolExecutionError, ValueError):
            failures.append(f"{task.task_id}:{tool.name}:raised")
            continue
        if (
            not result.ok
            or result.output != {"records": []}
            or result.source_ids
            or result.mutated
            or world.snapshot_hash != before
        ):
            failures.append(f"{task.task_id}:{tool.name}:nonempty_or_mutating")
    return failures


def _public_view_contract(task: TwinTask) -> list[str]:
    world = build_world(task)
    menu = {tool.name: tool for tool in visible_menu(task, 128)}
    visible = canonical_json(
        {
            "messages": [
                {"role": "system", "content": _system_prompt("react-v1")},
                {"role": "user", "content": task.request},
            ],
            "tools": [tool.to_openai() for tool in menu.values()],
        }
    )
    hidden = {
        task.family.family_id,
        task.task_id,
        *(node.record_id for node in task.nodes),
        *(query for node in task.nodes for query in node.next_queries),
    }
    if task.action and task.action.target_record_id:
        hidden.add(task.action.target_record_id)
    failures = [
        f"{task.task_id}:initial_leak:{value}"
        for value in sorted(hidden)
        if value and value in visible
    ]
    seen: dict[str, dict[str, JSON]] = {}
    for step in task.program:
        for name, value in step.argument_map.items():
            if name == "idempotency_key":
                if task.family.case_id not in value:
                    failures.append(f"{task.task_id}:{step.step_id}:private_idempotency")
            elif value not in visible:
                failures.append(f"{task.task_id}:{step.step_id}:unobserved_argument:{name}")
        tool = menu[step.tool_name]
        result = execute_capacity_tool(world, task, tool, step.argument_map)
        _remember_records(seen, tool.connector, result)
        visible += canonical_json(
            compile_visible_observation(
                result,
                tool.connector,
                seen,
                Representation.RAW,
                world.snapshot_hash,
            )
        )
    answer = json.loads(_reference_answer(task))
    for source in answer["sources"]:
        if source not in visible:
            failures.append(f"{task.task_id}:unobserved_answer_source")
    for value in answer["facts"].values():
        if canonical_json(value) not in visible:
            failures.append(f"{task.task_id}:unobserved_answer_fact")
    return failures


def _safe_route_neighbor_contract(task: TwinTask) -> tuple[int, list[str]]:
    world = build_world(task)
    expected_route = source_route(task.family)
    protected = {
        task.family.case_id,
        *(node.record_id for node in task.nodes),
        *(node.lookup_key for node in task.nodes),
    }
    failures: list[str] = []
    checked = 0
    for tool in visible_menu(task, 128):
        if not is_distractor(tool):
            continue
        operation = underlying_tool_name(tool)
        route = tool_source_route(tool)
        if route is None:
            failures.append(f"{task.task_id}:{tool.name}:missing_route")
            continue
        arguments = (
            {"query": task.family.case_id}
            if operation.startswith("search_")
            else {"record_id": task.nodes[0].record_id}
        )
        before = world.snapshot_hash
        try:
            result = execute_capacity_tool(world, task, tool, arguments)
        except (ToolExecutionError, ValueError) as exc:
            failures.append(f"{task.task_id}:{tool.name}:raised:{type(exc).__name__}")
            continue
        expected_output = {"records": [], "source_route": dict(route)}
        if (
            route == expected_route
            or not result.ok
            or result.output != expected_output
            or result.source_ids
            or result.mutated
            or result.receipt_id is not None
            or world.snapshot_hash != before
            or any(value in canonical_json(result.output) for value in protected)
        ):
            failures.append(f"{task.task_id}:{tool.name}:unsafe_or_leaking")
        checked += 1
    return checked, failures


def _mutation_contract(task: TwinTask) -> tuple[int, int, int, int, list[str]]:
    initial, final, trace, reference = reference_execution(task)
    answer = _reference_answer(task)
    budget = Budget(12, len(trace), 10000, 1000)
    failures: list[str] = []
    valid_orders = 0
    for index, order in enumerate(_topological_programs(task.program)):
        if reference_execution(task, program=order)[3].verified_success:
            valid_orders += 1
        else:
            failures.append(f"{task.task_id}:valid_order_{index}_rejected")
    if not reference.verified_success:
        failures.append(f"{task.task_id}:reference_rejected")
    wrapped, _ = evaluate_episode(
        task,
        initial,
        final,
        trace,
        budget,
        answer=f"Result:\n```json\n{answer}\n```",
    )
    normalized_answers = int(wrapped.verified_success)
    if not wrapped.verified_success:
        failures.append(f"{task.task_id}:wrapped_reference_rejected")

    repeated_read_recoveries = 0
    read_index = next((index for index, event in enumerate(trace) if not event.mutated), None)
    if read_index is not None:
        repeated = trace[: read_index + 1] + (trace[read_index],) + trace[read_index + 1 :]
        repeated_verdict, _ = evaluate_episode(
            task,
            initial,
            final,
            repeated,
            Budget(12, len(repeated), 10000, 1000),
            answer=answer,
        )
        repeated_read_recoveries = int(repeated_verdict.verified_success)
        if not repeated_verdict.verified_success:
            failures.append(f"{task.task_id}:repeated_read_recovery_rejected")

    mutations: list[tuple[str, World, tuple[TraceEvent, ...], Budget, str, bool]] = []
    mutations.append(("availability", final, trace, budget, answer, True))
    mutations.append(("completion", final, trace, budget, answer, True))
    mutations.append(
        ("budget", final, trace, Budget(max(0, len(trace) - 1), len(trace), 10000, 1000), answer, True)
    )
    for index in range(len(trace)):
        mutations.append(
            (f"missing_step_{index}", final, trace[:index] + trace[index + 1 :], budget, answer, True)
        )
    positions = {step.step_id: index for index, step in enumerate(task.program)}
    for step in task.program:
        for parent in step.depends_on:
            swapped = list(trace)
            left, right = positions[parent], positions[step.step_id]
            swapped[left], swapped[right] = swapped[right], swapped[left]
            mutations.append((f"order_{parent}_{step.step_id}", final, tuple(swapped), budget, answer, True))
    if trace:
        bad_hash = (replace(trace[0], before_hash="invalid-state-hash"), *trace[1:])
        mutations.append(("trace_hash", final, tuple(bad_hash), budget, answer, True))
    evidence_index = next(
        (
            i
            for i, event in enumerate(trace)
            if event.evidence and event.tool.startswith("get_")
        ),
        None,
    )
    if evidence_index is not None:
        event = trace[evidence_index]
        bad_ref = replace(event.evidence[0], version=event.evidence[0].version + 1)
        bad_event = replace(event, evidence=(bad_ref, *event.evidence[1:]))
        bad_trace = list(trace)
        bad_trace[evidence_index] = bad_event
        mutations.append(("evidence_version", final, tuple(bad_trace), budget, answer, True))

    answer_payload = json.loads(answer)
    answer_mutations: list[tuple[str, JSON]] = []
    wrong_decision = deepcopy(answer_payload)
    wrong_decision["decision"] = "wrong"
    answer_mutations.append(("wrong_decision", wrong_decision))
    missing_fact = deepcopy(answer_payload)
    missing_fact["facts"].pop(next(iter(missing_fact["facts"])))
    answer_mutations.append(("missing_fact", missing_fact))
    wrong_fact = deepcopy(answer_payload)
    wrong_fact["facts"][next(iter(wrong_fact["facts"]))] = "wrong"
    answer_mutations.append(("wrong_fact", wrong_fact))
    missing_source = deepcopy(answer_payload)
    missing_source["sources"] = missing_source["sources"][1:]
    answer_mutations.append(("missing_source", missing_source))
    duplicate_source = deepcopy(answer_payload)
    duplicate_source["sources"].append(duplicate_source["sources"][0])
    answer_mutations.append(("duplicate_source", duplicate_source))
    extra_root = deepcopy(answer_payload)
    extra_root["explanation"] = "unsupported"
    answer_mutations.append(("extra_answer_field", extra_root))
    mutations.extend(
        (name, final, trace, budget, canonical_json(payload), True)
        for name, payload in answer_mutations
    )
    mutations.append(("ambiguous_answer", final, trace, budget, f"{answer}\n{answer}", True))

    unsafe = final.clone()
    first = task.nodes[0]
    unsafe.records[first.connector][first.record_id]["tampered"] = True
    mutations.append(("unsafe_state", unsafe, trace, budget, answer, True))
    fake = TraceEvent(
        tool="unrequested_mutation",
        connector=first.connector,
        arguments={},
        before_hash=final.snapshot_hash,
        after_hash=final.snapshot_hash,
        evidence=(),
        result_records=(),
        mutated=True,
        receipt_id="invalid-receipt",
    )
    mutations.append(("unrequested_mutation", final, trace + (fake,), budget, answer, True))

    false_accepts = 0
    for name, candidate_final, candidate_trace, candidate_budget, candidate_answer, completion in mutations:
        verdict, _ = evaluate_episode(
            task,
            initial,
            candidate_final,
            candidate_trace,
            candidate_budget,
            answer=candidate_answer,
            availability=name != "availability",
            completion_valid=completion and name != "completion",
        )
        if verdict.verified_success:
            false_accepts += 1
            failures.append(f"{task.task_id}:{name}:false_accept")
    return valid_orders, len(mutations), normalized_answers, repeated_read_recoveries, failures


def runtime_construct_audit(split: Split | str = Split.DEVELOPMENT) -> JSON:
    """Run population-wide representation, evaluator, and public-view gates."""

    normalized = Split(split)
    tasks = tasks_for_split(normalized)
    query_independent = tuple(inspect.signature(compile_visible_observation).parameters) == (
        "result",
        "connector",
        "seen_records",
        "representation",
        "source_state_hash",
    )
    representation_failures: list[str] = []
    public_view_failures: list[str] = []
    mutation_failures: list[str] = []
    public_handoff_failures: list[str] = []
    observations_checked = 0
    valid_orders = 0
    mutations_checked = 0
    normalized_answers = 0
    repeated_read_recoveries = 0
    representation_sizes = {representation.value: [] for representation in Representation}
    benign_miss_failures: list[str] = []
    route_neighbor_failures: list[str] = []
    route_neighbors_checked = 0

    for task in tasks:
        observation_count, failures, sizes = _representation_contract(task)
        observations_checked += observation_count
        representation_failures.extend(failures)
        for representation, values in sizes.items():
            representation_sizes[representation].extend(values)
        benign_miss_failures.extend(_benign_miss_contract(task))
        checked, failures = _safe_route_neighbor_contract(task)
        route_neighbors_checked += checked
        route_neighbor_failures.extend(failures)
        public_view_failures.extend(_public_view_contract(task))
        order_count, mutation_count, normalized_count, recovery_count, failures = _mutation_contract(task)
        valid_orders += order_count
        mutations_checked += mutation_count
        normalized_answers += normalized_count
        repeated_read_recoveries += recovery_count
        mutation_failures.extend(failures)

        task_world = build_world(task)
        by_lookup = {node.lookup_key: node for node in task.nodes}
        for node in task.nodes:
            record = task_world.records[node.connector][node.record_id]
            raw_handoffs = (
                [record["next_handoff"]]
                if "next_handoff" in record
                else record.get("next_handoffs", [])
            )
            observed = {
                (item.get("connector"), item.get("query"))
                for item in raw_handoffs
                if isinstance(item, dict)
            }
            expected = {
                (by_lookup[query].connector, query) for query in node.next_queries
            }
            if observed != expected:
                public_handoff_failures.append(task.task_id)
                break

    solvability_failures = [
        failure for failure in mutation_failures if failure.endswith("reference_rejected")
    ]
    alternative_failures = [
        failure for failure in mutation_failures if "valid_order_" in failure
    ]
    recovery_failures = [
        failure for failure in mutation_failures if "repeated_read_recovery_rejected" in failure
    ]
    false_accepts = [
        failure for failure in mutation_failures if failure.endswith("false_accept")
    ]
    direct_prompt = _system_prompt("react-v1@vertex-openai")
    deliberate_prompt = _system_prompt("deliberate-verify-v1@vertex-openai")
    interface_prompt = _system_prompt("interface-guide-v1@vertex-openai")
    harness_policy_distinct = (
        direct_prompt != deliberate_prompt
        and direct_prompt != interface_prompt
        and deliberate_prompt != interface_prompt
        and "privately verify" in deliberate_prompt
        and all(term in interface_prompt for term in ("payload", "type", "fields", "source", "Provenance", "relation_dictionary"))
        and not any(term in interface_prompt.lower() for term in ("required_tools", "gold", "reference program"))
    )
    checks = (
        {
            "name": "query_independence",
            "passed": query_independent,
            "details": {"parameters": list(inspect.signature(compile_visible_observation).parameters)},
        },
        {
            "name": "information_equivalence",
            "passed": not representation_failures,
            "details": {
                "tasks": len(tasks),
                "observations": observations_checked,
                "failures": representation_failures,
            },
        },
        {
            "name": "delivery",
            "passed": not representation_failures,
            "details": {
                "population_wide": True,
                "serialized_character_summary": {
                    representation: {
                        "minimum": min(values),
                        "maximum": max(values),
                        "mean": sum(values) / len(values),
                    }
                    for representation, values in representation_sizes.items()
                },
                "failures": representation_failures,
            },
        },
        {
            "name": "benign_search_miss",
            "passed": not benign_miss_failures,
            "details": {"tasks": len(tasks), "failures": benign_miss_failures},
        },
        {
            "name": "safe_executable_route_neighbors",
            "passed": not route_neighbor_failures,
            "details": {
                "neighbors_checked": route_neighbors_checked,
                "target_facts_or_handles_returned": 0 if not route_neighbor_failures else None,
                "failures": route_neighbor_failures,
            },
        },
        {
            "name": "exact_evaluation",
            "passed": not mutation_failures,
            "details": {
                "valid_alternative_orders": valid_orders,
                "normalized_answer_wrappers": normalized_answers,
                "repeated_read_recoveries": repeated_read_recoveries,
                "mutations": mutations_checked,
                "false_accepts": len(false_accepts),
                "false_rejects": (
                    len(solvability_failures)
                    + len(alternative_failures)
                    + len(recovery_failures)
                ),
                "failures": mutation_failures,
            },
        },
        {
            "name": "protocol_validity",
            "passed": not public_view_failures,
            "details": {
                "complete_model_visible_surface": True,
                "tasks": len(tasks),
                "failures": public_view_failures,
            },
        },
        {
            "name": "reference_solvability",
            "passed": not solvability_failures and not alternative_failures,
            "details": {
                "task_count": len(tasks),
                "valid_alternative_orders": valid_orders,
                "failures": solvability_failures + alternative_failures,
            },
        },
        {
            "name": "public_handoff_contract",
            "passed": not public_handoff_failures,
            "details": {"task_count": len(tasks), "failures": public_handoff_failures},
        },
        {
            "name": "harness_policy_distinction",
            "passed": harness_policy_distinct,
            "details": {
                "direct_hash": stable_hash(direct_prompt),
                "deliberate_hash": stable_hash(deliberate_prompt),
                "interface_guide_hash": stable_hash(interface_prompt),
            },
        },
    )
    return {
        "audit": "capacity-surface-runtime-construct-v4",
        "split": normalized.value,
        "passed": all(check["passed"] for check in checks),
        "checks": list(checks),
    }


def efficiency_policy_audit(
    split: Split | str = Split.DEVELOPMENT,
    *,
    tool_call_slack: int = 2,
    turn_slack: int = 2,
) -> JSON:
    """Certify that the fast policy preserves solvability and the peak-load intervention."""

    normalized = Split(split)
    tasks = tasks_for_split(normalized)
    certificates = [certify_minimal_program(task) for task in tasks]
    exposure_rows: list[JSON] = []
    shortlist_failures: list[str] = []
    for task in tasks:
        menu = visible_menu(task, 128)
        menu_names = {tool.name for tool in menu}
        if not set(task.base_tool_names) <= menu_names:
            shortlist_failures.append(task.task_id)
        requests = len(task.program) + 1
        fixed_exposures = 128 * requests
        routed_exposures = 128 + len(task.base_tool_names) * len(task.program)
        exposure_rows.append(
            {
                "task_id": task.task_id,
                "program_calls": len(task.program),
                "operational_tools": len(task.base_tool_names),
                "fixed_tool_schema_exposures": fixed_exposures,
                "routed_tool_schema_exposures": routed_exposures,
                "reduction_fraction": 1.0 - routed_exposures / fixed_exposures,
            }
        )
    reductions = [float(row["reduction_fraction"]) for row in exposure_rows]
    checks = (
        {
            "name": "routing_robustness_prompt_identity",
            "passed": _system_prompt("react-routed-v1@vertex-openai")
            == _system_prompt("react-fixed-menu-v1@vertex-openai"),
            "details": {
                "routed_prompt_hash": stable_hash(
                    _system_prompt("react-routed-v1@vertex-openai")
                ),
                "fixed_prompt_hash": stable_hash(
                    _system_prompt("react-fixed-menu-v1@vertex-openai")
                ),
            },
        },
        {
            "name": "peak_load_intervention_preserved",
            "passed": all(len(visible_menu(task, size)) == size for task in tasks for size in (8, 32, 128)),
            "details": {"first_request_levels": [8, 32, 128]},
        },
        {
            "name": "operational_shortlist_complete",
            "passed": not shortlist_failures,
            "details": {"tasks": len(tasks), "failures": shortlist_failures},
        },
        {
            "name": "task_adaptive_budget_preserves_reference_solution",
            "passed": all(
                certificate.passed
                and certificate.minimum_calls + tool_call_slack >= certificate.declared_calls
                and certificate.minimum_calls + tool_call_slack + turn_slack
                >= certificate.declared_calls + 1
                for certificate in certificates
            ),
            "details": {
                "tasks": len(tasks),
                "tool_call_slack": tool_call_slack,
                "turn_slack": turn_slack,
                "minimum_program_calls": min(item.minimum_calls for item in certificates),
                "maximum_program_calls": max(item.minimum_calls for item in certificates),
            },
        },
        {
            "name": "infeasibility_stop_is_counting_lower_bound",
            "passed": all(len({step.step_id for step in task.program}) == len(task.program) for task in tasks),
            "details": {
                "proof_obligation": (
                    "The evaluator assigns each program step to a distinct trace event; every new trace "
                    "event consumes one tool call. More unmatched steps than remaining calls therefore "
                    "makes verified success impossible."
                )
            },
        },
        {
            "name": "routed_exposure_reduction",
            "passed": min(reductions) >= 0.60,
            "details": {
                "minimum_reduction_fraction": min(reductions),
                "mean_reduction_fraction": sum(reductions) / len(reductions),
                "maximum_reduction_fraction": max(reductions),
                "task_profiles": exposure_rows,
            },
        },
    )
    return {
        "audit": "factorized-capacity-efficiency-policy",
        "split": normalized.value,
        "passed": all(check["passed"] for check in checks),
        "checks": list(checks),
    }
