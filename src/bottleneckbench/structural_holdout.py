"""Structural repair holdout: task compiler, public-handoff controller, audits.

This module is the deterministic, offline half of the structural repair holdout
study.  It compiles the 24 unseen-intent semantic families into 2-call and
30-call twins, and it ships the two system-side clients the study measures:

``CanonicalDeliveryClient``
    a lossless observation adapter, and

``TraceStateClient``
    the *public-handoff controller*.  On a 30-call task the model issues the
    first tool call and the final answer; the controller issues the remaining
    29 by following the ``next_handoff`` chain the sandbox publishes in each
    record.  Its synthetic call ids all begin with ``structural-controller-``,
    which is how released rows can be split into model-issued and
    controller-issued calls.

``static_audit`` runs the construct audits over the whole compiled population,
including the three controller audits the paper names: hidden-program
invariance, public-handoff sensitivity, and fail-closed ambiguity.

Hosted execution is deliberately absent.  The private runner that spends
provider credit, meters budgets, quarantines transport failures, and writes
result rows is outside the public projection; everything here runs offline
against the in-process sandbox.
"""

from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import dataclass, replace
from hashlib import sha256
import json
from pathlib import Path
from typing import Mapping, Sequence

from bottleneckbench.design import (
    CapacityCondition,
    ProgramStep,
    RecordNode,
    SemanticFamily,
    Split,
    WorkflowLoad,
    _base_tool_names,
    _routed_name,
    build_families,
    build_world,
    source_route,
    tasks_for_split,
    visible_menu,
)
from bottleneckbench.experiments import EpisodePolicy, _balanced_order
from bottleneckbench.runtime import (
    CapacityEpisode,
    CompletionClient,
    _remember_records,
    compile_visible_observation,
    execute_capacity_tool,
    run_episode,
)
from bottleneckbench.workflow_boundary import (
    certify_workflow_boundary_program,
    reference_workflow_boundary_execution,
    workflow_boundary_tasks,
)
from bottleneckbench.core.agnes import ModelRequest, ModelResponse, ToolCall
from bottleneckbench.core.schema import (
    JSON,
    Representation,
    canonical_json,
    stable_hash,
)


# The frozen rule inventory and its protocol note live beside the release
# analyses rather than in the package, because they are study evidence.
ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT / "experiments/structural-repair-holdout-intents.json"
PROTOCOL = ROOT / "experiments/structural-repair-holdout-protocol.md"


CALL_LEVELS = (2, 30)
LOCATORS_BY_CALLS = {2: 0, 30: 14}
SEARCH_TOOL = {
    "accounting": "search_invoices",
    "email": "search_email",
    "files": "search_files",
    "calendar": "search_calendar",
}
GET_TOOL = {
    "accounting": "get_invoice",
    "email": "get_email",
    "files": "get_file",
    "calendar": "get_calendar",
}
REGISTERED_PREDICATE_FUNCTIONS = {
    "duration_minutes",
    "intervals_overlap",
    "valid_interval",
}
CELL_SPECS = (
    (2, Representation.RAW, "baseline", "native"),
    (30, Representation.RAW, "baseline", "native"),
    (2, Representation.RAW, "trace_state", "native"),
    (30, Representation.RAW, "trace_state", "native"),
    (2, Representation.RAW, "baseline", "canonical"),
    (30, Representation.RAW, "baseline", "canonical"),
    (2, Representation.GENERIC_COMPACT, "baseline", "native"),
    (2, Representation.GENERIC_COMPACT, "baseline", "canonical"),
    (2, Representation.GENERIC_COMPACT, "trace_state", "native"),
)

# One frozen, human-readable fixture per distinct rule.  Tuple order follows
FACT_FIXTURES: dict[str, tuple[object, object, object]] = {
    "invoice_total_match": (1250, 1250, "USD"),
    "receipt_currency_match": ("USD", "EUR", "not_required"),
    "vendor_identity_match": ("Northstar Labs", "Northstar Labs", "active"),
    "payment_term_compliance": ("2026-10-10", "2026-10-05", "approved"),
    "duplicate_invoice_risk": (True, True, True),
    "approval_evidence_complete": (True, True, False),
    "response_within_sla": (
        "2026-10-01T10:00:00Z",
        "2026-10-01T12:00:00Z",
        True,
    ),
    "sender_authorized": (True, "fail", True),
    "required_recipient_covered": (True, False, "internal_only"),
    "escalation_acknowledged": (
        True,
        "2026-10-01T15:00:00Z",
        "2026-10-01T14:00:00Z",
    ),
    "attachment_requirement_met": (True, True, True),
    "thread_resolution_complete": (True, 1, "closed"),
    "agreement_execution_ready": ("signed", True, True),
    "registration_current": ("active", "2026-09-30", "2026-10-01"),
    "policy_exception_present": (True, "2026-12-31", True),
    "document_version_current": (2, 3, False),
    "evidence_bundle_complete": (True, True, True),
    "retention_window_valid": (400, 365, False),
    "overlap_conflict": (
        {"start": "2026-10-01T10:00:00Z", "end": "2026-10-01T11:00:00Z"},
        {"start": "2026-10-01T10:30:00Z", "end": "2026-10-01T11:30:00Z"},
        False,
    ),
    "attendee_quorum_met": (2, 3, True),
    "prerequisite_complete": (True, True, True),
    "review_window_valid": (
        {"start": "2026-10-01T17:00:00Z", "end": "2026-10-01T19:00:00Z"},
        60,
        False,
    ),
    "timezone_alignment_valid": (True, True, True),
    "deadline_feasible": (4, 8, False),
}


def _inventory() -> tuple[JSON, ...]:
    payload = json.loads(INVENTORY.read_text(encoding="utf-8"))
    if payload.get("protocol_version") != "1.2":
        raise ValueError("structural holdout inventory version mismatch")
    entries = payload.get("entries")
    if not isinstance(entries, list) or len(entries) != 24:
        raise ValueError("structural holdout inventory must contain 24 entries")
    return tuple(entries)


def _interval_bounds(value: object) -> tuple[str, str]:
    if not isinstance(value, dict) or set(value) != {"start", "end"}:
        raise ValueError("interval must contain exactly start and end")
    return str(value["start"]), str(value["end"])


def intervals_overlap(left: object, right: object) -> bool:
    left_start, left_end = _interval_bounds(left)
    right_start, right_end = _interval_bounds(right)
    return max(left_start, right_start) < min(left_end, right_end)


def valid_interval(value: object) -> bool:
    start, end = _interval_bounds(value)
    return start < end


def duration_minutes(value: object) -> float:
    from datetime import datetime

    start, end = _interval_bounds(value)
    parse = lambda raw: datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return (parse(end) - parse(start)).total_seconds() / 60.0


def _predicate_value(predicate: str, facts: Mapping[str, object]) -> bool:
    tree = ast.parse(predicate, mode="eval")
    allowed_nodes = (
        ast.Expression,
        ast.BoolOp,
        ast.And,
        ast.Or,
        ast.UnaryOp,
        ast.Not,
        ast.Compare,
        ast.Eq,
        ast.NotEq,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
        ast.Name,
        ast.Load,
        ast.Constant,
        ast.Call,
    )
    for node in ast.walk(tree):
        if not isinstance(node, allowed_nodes):
            raise ValueError(f"unsupported predicate syntax: {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id not in (
            set(facts) | REGISTERED_PREDICATE_FUNCTIONS
        ):
            raise ValueError(f"unknown predicate name: {node.id}")
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name)
            or node.func.id not in REGISTERED_PREDICATE_FUNCTIONS
            or node.keywords
        ):
            raise ValueError("predicate calls an unregistered function")
    scope = {
        **facts,
        "duration_minutes": duration_minutes,
        "intervals_overlap": intervals_overlap,
        "valid_interval": valid_interval,
    }
    return bool(eval(compile(tree, "<frozen-predicate>", "eval"), {"__builtins__": {}}, scope))


def structural_families() -> tuple[SemanticFamily, ...]:
    families: list[SemanticFamily] = []
    for offset, entry in enumerate(_inventory()):
        intent = str(entry["intent"])
        names = tuple(str(value) for value in entry["required_facts"])
        values = FACT_FIXTURES[intent]
        facts = tuple(zip(names, deepcopy(values), strict=True))
        positive = _predicate_value(str(entry["predicate"]), dict(facts))
        if positive is not bool(entry["positive_outcome"]):
            raise ValueError(f"fixture outcome does not match frozen rule: {intent}")
        index = 300 + offset
        seed = 600 + offset
        case_id = "RH-" + stable_hash(
            {"study": "structural-repair-holdout-v1", "index": index}
        )[:12].upper()
        family_id = "repair-holdout-" + stable_hash(
            {"index": index, "case_id": case_id, "intent": intent}
        )[:16]
        labels = tuple(str(value) for value in entry["labels"])
        families.append(
            SemanticFamily(
                family_id=family_id,
                split=Split.MAIN,
                seed=seed,
                case_id=case_id,
                intent=intent,
                connectors=tuple(str(value) for value in entry["route"]),
                facts=facts,
                decision=labels[0 if positive else 1],
                outcome_variant=0 if positive else 1,
                prompt_variant=offset % 3,
                branching=False,
                action_name=None,
            )
        )
    return tuple(families)


def _entry_by_intent() -> dict[str, JSON]:
    return {str(entry["intent"]): entry for entry in _inventory()}


def _request(family: SemanticFamily) -> str:
    entry = _entry_by_intent()[family.intent]
    labels = tuple(str(value) for value in entry["labels"])
    facts = ", ".join(f'"{name}": <value>' for name in entry["required_facts"])
    route = ", ".join(f"{name}={value}" for name, value in source_route(family))
    connectors = " -> ".join(str(value) for value in entry["route"])
    openings = (
        "Evaluate the current evidence",
        "Resolve the current decision from authoritative evidence",
        "Apply the stated rule to the current records",
    )
    return (
        f"{openings[family.prompt_variant]} for case {family.case_id}. "
        f"Required source route: {route}. Permitted connector route: {connectors}; "
        f"begin with {family.connectors[0]}. Use only tools whose three source "
        "coordinates exactly match the required route. The public decision rule is "
        f"`{entry['predicate']}`. Return `{labels[0]}` when it is true and "
        f"`{labels[1]}` otherwise. Retrieve and report exactly these facts: "
        f"{', '.join(str(value) for value in entry['required_facts'])}. Return exactly "
        f"one JSON object shaped as {{\"decision\":\"<label>\",\"facts\":{{{facts}}},"
        "\"sources\":[\"<source record id>\"]}}. The sources array must contain "
        "exactly the current records supporting the reported facts, each once. "
        "Follow each public next_handoff when evidence is still incomplete. Do not "
        "mutate state, invent values, or use records from another source route."
    )


@dataclass(frozen=True, slots=True)
class StructuralTask:
    family: SemanticFamily
    locator_nodes: int
    nodes: tuple[RecordNode, ...]
    program: tuple[ProgramStep, ...]
    request: str
    base_tool_names: tuple[str, ...]
    action: None = None
    workflow: WorkflowLoad = WorkflowLoad.HIGH

    @property
    def task_id(self) -> str:
        return f"{self.family.family_id}-structural-c{self.certified_calls}"

    @property
    def certified_calls(self) -> int:
        return len(self.program)

    @property
    def semantic_signature(self) -> str:
        return self.family.semantic_signature

    @property
    def expected_fact_map(self) -> dict[str, object]:
        return self.family.fact_map

    @property
    def required_tool_names(self) -> tuple[str, ...]:
        return self.base_tool_names

    @property
    def task_hash(self) -> str:
        return stable_hash(self.hidden_payload())

    def hidden_payload(self) -> JSON:
        return {
            "study": "structural_repair_holdout",
            "task_id": self.task_id,
            "semantic_signature": self.semantic_signature,
            "locator_nodes": self.locator_nodes,
            "nodes": [
                {
                    "connector": node.connector,
                    "record_id": node.record_id,
                    "lookup_key": node.lookup_key,
                    "facts": list(node.facts),
                    "next_queries": list(node.next_queries),
                }
                for node in self.nodes
            ],
            "program": [
                {
                    "step_id": step.step_id,
                    "tool_name": step.tool_name,
                    "connector": step.connector,
                    "arguments": step.argument_map,
                    "depends_on": list(step.depends_on),
                    "produces_facts": list(step.produces_facts),
                }
                for step in self.program
            ],
            "request": self.request,
            "base_tool_names": list(self.base_tool_names),
        }


@dataclass(frozen=True, slots=True)
class StructuralCondition:
    task_id: str
    family_id: str
    intent: str
    stratum: str
    outcome_variant: int
    prompt_variant: int
    model: str
    harness: str
    representation: Representation
    certified_calls: int
    orchestration: str
    delivery: str
    seed: int
    branching: bool = False
    action_required: bool = False
    workflow: WorkflowLoad = WorkflowLoad.HIGH
    visible_tools: int = 8

    def __post_init__(self) -> None:
        cell = (
            self.certified_calls,
            self.representation,
            self.orchestration,
            self.delivery,
        )
        if cell not in CELL_SPECS:
            raise ValueError(f"unregistered structural holdout cell: {cell}")
        if not all(
            (
                self.task_id,
                self.family_id,
                self.intent,
                self.stratum,
                self.model,
                self.harness,
            )
        ):
            raise ValueError("structural condition identity is incomplete")

    def to_json(self) -> JSON:
        return {
            "study": "structural_repair_holdout",
            "task_id": self.task_id,
            "family_id": self.family_id,
            "intent": self.intent,
            "stratum": self.stratum,
            "outcome_variant": self.outcome_variant,
            "prompt_variant": self.prompt_variant,
            "branching": self.branching,
            "action_required": self.action_required,
            "model": self.model,
            "harness": self.harness,
            "workflow": self.workflow.value,
            "representation": self.representation.value,
            "visible_tools": self.visible_tools,
            "certified_calls": self.certified_calls,
            "orchestration": self.orchestration,
            "delivery": self.delivery,
            "seed": self.seed,
        }

    @property
    def condition_id(self) -> str:
        return stable_hash(self.to_json())

    @classmethod
    def from_json(cls, payload: object) -> StructuralCondition:
        if not isinstance(payload, dict):
            raise ValueError("structural condition must be an object")
        return cls(
            task_id=str(payload["task_id"]),
            family_id=str(payload["family_id"]),
            intent=str(payload["intent"]),
            stratum=str(payload["stratum"]),
            outcome_variant=int(payload["outcome_variant"]),
            prompt_variant=int(payload["prompt_variant"]),
            model=str(payload["model"]),
            harness=str(payload["harness"]),
            representation=Representation(str(payload["representation"])),
            certified_calls=int(payload["certified_calls"]),
            orchestration=str(payload["orchestration"]),
            delivery=str(payload["delivery"]),
            seed=int(payload["seed"]),
            branching=bool(payload["branching"]),
            action_required=bool(payload["action_required"]),
            workflow=WorkflowLoad(str(payload["workflow"])),
            visible_tools=int(payload["visible_tools"]),
        )


def _record_id(family: SemanticFamily, kind: str, index: int) -> str:
    return "src-" + stable_hash(
        {
            "study": "structural-repair-holdout-v1",
            "family": family.family_id,
            "kind": kind,
            "index": index,
            "connector": family.connectors[0],
        }
    )[:16]


def _lookup(family: SemanticFamily, position: int) -> str:
    return "lookup-" + stable_hash(
        {
            "study": "structural-repair-holdout-v1",
            "family": family.family_id,
            "position": position,
        }
    )[:18]


def compile_structural_task(family: SemanticFamily, calls: int) -> StructuralTask:
    locator_nodes = LOCATORS_BY_CALLS[calls]
    connector = family.connectors[0]
    node_count = locator_nodes + 1
    lookup_keys = [family.case_id] + [
        _lookup(family, position) for position in range(1, node_count)
    ]
    nodes: list[RecordNode] = []
    for position in range(node_count):
        evidence = position == node_count - 1
        nodes.append(
            RecordNode(
                connector=connector,
                record_id=_record_id(
                    family,
                    "evidence" if evidence else "locator",
                    0 if evidence else position,
                ),
                lookup_key=lookup_keys[position],
                facts=tuple(family.fact_map) if evidence else (),
                next_queries=(lookup_keys[position + 1],)
                if position + 1 < node_count
                else (),
            )
        )
    search = _routed_name(family, SEARCH_TOOL[connector])
    get = _routed_name(family, GET_TOOL[connector])
    program: list[ProgramStep] = []
    for position, node in enumerate(nodes):
        number = position + 1
        search_id = f"search-{number}"
        get_id = f"get-{number}"
        program.extend(
            (
                ProgramStep(
                    step_id=search_id,
                    tool_name=search,
                    connector=connector,
                    arguments=(("query", node.lookup_key),),
                    depends_on=() if position == 0 else (f"get-{position}",),
                ),
                ProgramStep(
                    step_id=get_id,
                    tool_name=get,
                    connector=connector,
                    arguments=(("record_id", node.record_id),),
                    depends_on=(search_id,),
                    produces_facts=node.facts,
                ),
            )
        )
    task = StructuralTask(
        family=family,
        locator_nodes=locator_nodes,
        nodes=tuple(nodes),
        program=tuple(program),
        request=_request(family),
        base_tool_names=_base_tool_names(family),
    )
    if task.certified_calls != calls:
        raise AssertionError("structural task call target is inconsistent")
    return task


def structural_tasks() -> tuple[StructuralTask, ...]:
    return tuple(
        compile_structural_task(family, calls)
        for family in structural_families()
        for calls in CALL_LEVELS
    )


def structural_conditions(model: str, harness: str) -> tuple[StructuralCondition, ...]:
    entries = _entry_by_intent()
    tasks = {(task.family.family_id, task.certified_calls): task for task in structural_tasks()}
    conditions = tuple(
        StructuralCondition(
            task_id=tasks[(family.family_id, calls)].task_id,
            family_id=family.family_id,
            intent=family.intent,
            stratum=str(entries[family.intent]["stratum"]),
            outcome_variant=family.outcome_variant,
            prompt_variant=family.prompt_variant,
            model=model,
            harness=harness,
            representation=representation,
            certified_calls=calls,
            orchestration=orchestration,
            delivery=delivery,
            seed=family.seed,
        )
        for family in structural_families()
        for calls, representation, orchestration, delivery in CELL_SPECS
    )
    return tuple(
        _balanced_order(
            conditions,
            group_key=lambda item: item.family_id,
            cell_key=lambda item: (
                item.certified_calls,
                item.representation.value,
                item.orchestration,
                item.delivery,
            ),
            schedule_key={"study": "structural-repair-holdout-v1", "model": model},
        )
    )


def _pointer(name: str) -> str:
    return "/" + name.replace("~", "~0").replace("/", "~1")


def canonicalize_observation(serialized: str) -> str:
    """Losslessly adapt one public observation without a request or task object."""

    observation = json.loads(serialized)
    if not isinstance(observation, dict):
        raise ValueError("observation must be a JSON object")
    representation = Representation(str(observation["representation"]))
    if representation not in {Representation.RAW, Representation.GENERIC_COMPACT}:
        raise ValueError("structural adapter accepts only raw or generic compact input")
    objects = observation.get("objects")
    if not isinstance(objects, list):
        raise ValueError("observation objects must be an array")
    if representation is Representation.RAW:
        decoded = {
            (str(item["connector"]), str(item["payload"]["record_id"])): deepcopy(
                item["payload"]
            )
            for item in objects
        }
    else:
        dictionary = observation.get("field_dictionary")
        if not isinstance(dictionary, dict):
            raise ValueError("compact observation lacks its public field dictionary")
        decoded = {}
        for item in objects:
            payload = {
                str(dictionary.get(encoded, str(encoded).removeprefix("u_"))): deepcopy(value)
                for encoded, value in item["p"].items()
            }
            decoded[(str(item["c"]), str(payload["record_id"]))] = payload
    provenance = {
        (
            str(item["connector"]),
            str(item["record_id"]),
            int(item["version"]),
            str(item["field"]),
        ): deepcopy(item)
        for item in observation.get("provenance", [])
    }
    records: list[JSON] = []
    for (connector, record_id), record in sorted(decoded.items()):
        version = int(record["version"])
        values = []
        for name, value in sorted(record.items()):
            key = (connector, record_id, version, str(name))
            if key not in provenance:
                raise ValueError(f"canonical adapter lacks provenance for {key}")
            values.append(
                {
                    "path": _pointer(str(name)),
                    "value": deepcopy(value),
                    "provenance": provenance[key],
                }
            )
        records.append(
            {
                "record_type": str(record.get("kind", connector)),
                "source": {
                    "connector": connector,
                    "record_id": record_id,
                    "version": version,
                },
                "values": values,
            }
        )
    adapted = {
        "ok": bool(observation.get("ok", True)),
        "tool": observation.get("tool"),
        "delivery": "canonical",
        "source_ids": deepcopy(observation.get("source_ids", [])),
        "records": records,
        "relation_edges": sorted(
            deepcopy(observation.get("links", [])), key=canonical_json
        ),
        "source_state_hash": observation.get("source_state_hash"),
        "visible_history_hash": observation.get("visible_history_hash"),
        "world_hash": observation.get("world_hash"),
    }
    return canonical_json(adapted)


def canonical_source_facts(serialized: str) -> dict[tuple[str, str], JSON]:
    payload = json.loads(serialized)
    facts: dict[tuple[str, str], JSON] = {}
    for record in payload["records"]:
        source = record["source"]
        values = {
            str(item["path"])[1:].replace("~1", "/").replace("~0", "~"): deepcopy(
                item["value"]
            )
            for item in record["values"]
        }
        facts[(str(source["connector"]), str(source["record_id"]))] = values
    return facts


class ProviderCallLog:
    """Record the provider calls a controller-wrapped episode actually issues.

    The private hosted runner wrapped the same bookkeeping in a budgeted,
    spend-aware client.  Only the deterministic accounting is public, because
    it is what the controller audits and the released rows are checked against.
    """

    def __init__(self, base: CompletionClient) -> None:
        self.base = base
        self.request_tool_counts: list[int] = []
        self.request_tool_schema_bytes: list[int] = []
        self.responses: list[ModelResponse] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.request_tool_counts.append(len(request.tools))
        self.request_tool_schema_bytes.append(
            len(canonical_json(list(request.tools)).encode("utf-8"))
        )
        response = self.base.complete(request)
        self.responses.append(response)
        return response

    def normalize(self, episode: CapacityEpisode) -> CapacityEpisode:
        return replace(
            episode,
            response_ids=tuple(response.raw_id for response in self.responses),
            response_models=tuple(response.model for response in self.responses),
            usage_sources=tuple(response.usage_source for response in self.responses),
            request_tool_counts=tuple(self.request_tool_counts),
            request_tool_schema_bytes=tuple(self.request_tool_schema_bytes),
        )


class CanonicalDeliveryClient:
    def __init__(self, base: CompletionClient) -> None:
        self.base = base

    def complete(self, request: ModelRequest) -> ModelResponse:
        messages: list[JSON] = []
        for original in request.messages:
            message = deepcopy(original)
            if message.get("role") == "tool" and isinstance(message.get("content"), str):
                try:
                    payload = json.loads(message["content"])
                except json.JSONDecodeError:
                    payload = None
                if isinstance(payload, dict) and payload.get("representation") in {
                    Representation.RAW.value,
                    Representation.GENERIC_COMPACT.value,
                }:
                    message["content"] = canonicalize_observation(message["content"])
            messages.append(message)
        return self.base.complete(replace(request, messages=tuple(messages)))


def _tool_function(request: ModelRequest, name: str) -> JSON | None:
    for tool in request.tools:
        function = tool.get("function")
        if isinstance(function, dict) and function.get("name") == name:
            return function
    return None


def _source_route_description(function: Mapping[str, object]) -> str | None:
    description = str(function.get("description", ""))
    marker = "using source route "
    if marker not in description:
        return None
    return description.split(marker, 1)[1].split(".", 1)[0]


def _decoded_records(observation: Mapping[str, object]) -> dict[tuple[str, str], JSON]:
    representation = Representation(str(observation["representation"]))
    objects = observation.get("objects")
    if not isinstance(objects, list):
        raise ValueError("controller observation lacks objects")
    if representation is Representation.RAW:
        return {
            (str(item["connector"]), str(item["payload"]["record_id"])): deepcopy(
                item["payload"]
            )
            for item in objects
        }
    if representation is not Representation.GENERIC_COMPACT:
        raise ValueError("controller accepts only native raw or compact delivery")
    dictionary = observation.get("field_dictionary")
    if not isinstance(dictionary, dict):
        raise ValueError("compact controller observation lacks dictionary")
    records = {}
    for item in objects:
        payload = {
            str(dictionary.get(encoded, str(encoded).removeprefix("u_"))): deepcopy(value)
            for encoded, value in item["p"].items()
        }
        records[(str(item["c"]), str(payload["record_id"]))] = payload
    return records


class TraceStateClient:
    """Follow only unambiguous public record IDs and next_handoff values."""

    def __init__(self, base: CompletionClient) -> None:
        self.base = base
        self.pending_tool: str | None = None
        self.route: str | None = None
        self.active = False
        self.closed = False
        self.controller_calls = 0

    def _last_observation(self, request: ModelRequest) -> JSON | None:
        if not request.messages or self.pending_tool is None:
            return None
        message = request.messages[-1]
        if (
            message.get("role") != "tool"
            or message.get("name") != self.pending_tool
            or not isinstance(message.get("content"), str)
        ):
            return None
        try:
            payload = json.loads(message["content"])
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) and payload.get("ok") is True else None

    def _matching_tool(
        self, request: ModelRequest, *, connector: str, operation: str
    ) -> str | None:
        prefix = (SEARCH_TOOL if operation == "search" else GET_TOOL)[connector] + "_route_"
        matches = []
        for tool in request.tools:
            function = tool.get("function")
            if not isinstance(function, dict):
                continue
            name = str(function.get("name", ""))
            if name.startswith(prefix) and _source_route_description(function) == self.route:
                matches.append(name)
        return matches[0] if len(matches) == 1 else None

    def _synthetic(self, request: ModelRequest, name: str, arguments: JSON) -> ModelResponse:
        self.controller_calls += 1
        call_id = "structural-controller-" + stable_hash(
            {
                "index": self.controller_calls,
                "name": name,
                "arguments": arguments,
                "messages": list(request.messages[-2:]),
            }
        )[:16]
        self.pending_tool = name
        return ModelResponse(
            model=request.model,
            content="",
            reasoning="",
            tool_calls=(ToolCall(call_id, name, deepcopy(arguments)),),
            finish_reason="tool_calls",
            prompt_tokens=0,
            completion_tokens=0,
            raw_id=call_id,
            usage_source="deterministic_structural_controller",
        )

    def _controller_response(self, request: ModelRequest) -> ModelResponse | None:
        observation = self._last_observation(request)
        if observation is None:
            return None
        records = _decoded_records(observation)
        if len(records) != 1:
            return None
        (connector, record_id), record = next(iter(records.items()))
        if self.pending_tool and self.pending_tool.startswith("search_"):
            if len(observation.get("source_ids", [])) != 1:
                return None
            name = self._matching_tool(request, connector=connector, operation="get")
            return self._synthetic(request, name, {"record_id": record_id}) if name else None
        if not self.pending_tool or not self.pending_tool.startswith("get_"):
            return None
        handoff = record.get("next_handoff")
        if handoff is None and "next_handoffs" not in record:
            self.closed = True
            self.pending_tool = None
            return None
        if not isinstance(handoff, dict) or set(handoff) != {"connector", "query"}:
            return None
        next_connector = str(handoff["connector"])
        if next_connector not in SEARCH_TOOL:
            return None
        name = self._matching_tool(
            request, connector=next_connector, operation="search"
        )
        return self._synthetic(request, name, {"query": str(handoff["query"])}) if name else None

    def complete(self, request: ModelRequest) -> ModelResponse:
        if self.pending_tool is not None and not self.closed:
            response = self._controller_response(request)
            if response is not None:
                self.active = True
                return response
            self.closed = True
            self.pending_tool = None
        response = self.base.complete(request)
        if self.closed:
            return response
        if len(response.tool_calls) == 1 and response.tool_calls[0].name.startswith("search_"):
            function = _tool_function(request, response.tool_calls[0].name)
            self.route = _source_route_description(function) if function else None
            self.pending_tool = response.tool_calls[0].name if self.route else None
        else:
            self.closed = True
        return response


def run_structural_episode(
    condition: StructuralCondition,
    task: StructuralTask,
    provider: CompletionClient,
    policy: object,
) -> CapacityEpisode:
    meter = ProviderCallLog(provider)
    client: CompletionClient = meter
    if condition.delivery == "canonical":
        client = CanonicalDeliveryClient(client)
    if condition.orchestration == "trace_state":
        client = TraceStateClient(client)
    episode = run_episode(
        condition,  # type: ignore[arg-type]
        task,  # type: ignore[arg-type]
        client,
        max_turns=None,
        max_tool_calls=None,
        tool_call_slack=policy.tool_call_slack,  # type: ignore[attr-defined]
        turn_slack=policy.turn_slack,  # type: ignore[attr-defined]
        max_total_tokens=policy.max_total_tokens,  # type: ignore[attr-defined]
        max_completion_tokens=policy.max_completion_tokens,  # type: ignore[attr-defined]
        dynamic_shortlist=policy.dynamic_shortlist,  # type: ignore[attr-defined]
        early_stop_infeasible=policy.early_stop_infeasible,  # type: ignore[attr-defined]
    )
    return meter.normalize(episode)


class _AuditClient:
    def __init__(self, task: StructuralTask) -> None:
        self.task = task
        self.calls = 0

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self.calls == 1:
            step = self.task.program[0]
            content = ""
            calls = (ToolCall("audit-model-entry", step.tool_name, step.argument_map),)
            finish = "tool_calls"
        else:
            content = canonical_json(
                {
                    "decision": self.task.family.decision,
                    "facts": self.task.expected_fact_map,
                    "sources": [node.record_id for node in self.task.nodes if node.facts],
                }
            )
            calls = ()
            finish = "stop"
        return ModelResponse(
            model=request.model,
            content=content,
            reasoning="",
            tool_calls=calls,
            finish_reason=finish,
            prompt_tokens=1,
            completion_tokens=1,
            raw_id=f"audit-provider-{self.calls}",
        )


class _SequentialAuditClient:
    def __init__(self, task: StructuralTask) -> None:
        self.task = task
        self.calls = 0

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self.calls <= len(self.task.program):
            step = self.task.program[self.calls - 1]
            content = ""
            calls = (
                ToolCall(
                    f"audit-sequential-{self.calls}",
                    step.tool_name,
                    step.argument_map,
                ),
            )
            finish = "tool_calls"
        else:
            content = canonical_json(
                {
                    "decision": self.task.family.decision,
                    "facts": self.task.expected_fact_map,
                    "sources": [
                        node.record_id for node in self.task.nodes if node.facts
                    ],
                }
            )
            calls = ()
            finish = "stop"
        return ModelResponse(
            model=request.model,
            content=content,
            reasoning="",
            tool_calls=calls,
            finish_reason=finish,
            prompt_tokens=1,
            completion_tokens=1,
            raw_id=f"audit-sequential-provider-{self.calls}",
        )


def _adapter_audit(tasks: Sequence[StructuralTask]) -> int:
    comparisons = 0
    for task in tasks:
        world = build_world(task)  # type: ignore[arg-type]
        menu = {tool.name: tool for tool in visible_menu(task, 8)}  # type: ignore[arg-type]
        seen: dict[str, dict[str, JSON]] = {}
        for step in task.program:
            tool = menu[step.tool_name]
            result = execute_capacity_tool(
                world, task, tool, step.argument_map  # type: ignore[arg-type]
            )
            _remember_records(seen, tool.connector, result)
            canonical: list[str] = []
            decoded: list[dict[tuple[str, str], JSON]] = []
            for representation in (Representation.RAW, Representation.GENERIC_COMPACT):
                observation = compile_visible_observation(
                    result,
                    tool.connector,
                    seen,
                    representation,
                    world.snapshot_hash,
                )
                observation["ok"] = True
                adapted = canonicalize_observation(canonical_json(observation))
                canonical.append(adapted)
                decoded.append(canonical_source_facts(adapted))
            if canonical[0] != canonical[1] or decoded[0] != decoded[1]:
                raise AssertionError(f"canonical adapter changed information: {task.task_id}")
            comparisons += 1
    return comparisons


def _public_handoff_request(task: StructuralTask, query: str | None = None) -> ModelRequest:
    world = build_world(task)  # type: ignore[arg-type]
    menu = {tool.name: tool for tool in visible_menu(task, 8)}  # type: ignore[arg-type]
    seen: dict[str, dict[str, JSON]] = {}
    observation: JSON | None = None
    for step in task.program[:2]:
        tool = menu[step.tool_name]
        result = execute_capacity_tool(
            world, task, tool, step.argument_map  # type: ignore[arg-type]
        )
        _remember_records(seen, tool.connector, result)
        observation = compile_visible_observation(
            result,
            tool.connector,
            seen,
            Representation.RAW,
            world.snapshot_hash,
        )
    if observation is None:
        raise AssertionError("public handoff fixture has no observation")
    observation["ok"] = True
    if query is not None:
        observation["objects"][0]["payload"]["next_handoff"]["query"] = query
    return ModelRequest(
        model="audit/model",
        messages=(
            {"role": "system", "content": "public audit"},
            {"role": "user", "content": task.request},
            {
                "role": "tool",
                "name": task.program[1].tool_name,
                "tool_call_id": "audit-get",
                "content": canonical_json(observation),
            },
        ),
        tools=tuple(tool.to_openai() for tool in menu.values()),
    )


def _controller_audit(task: StructuralTask) -> JSON:
    base = _AuditClient(task)
    meter = ProviderCallLog(base)
    controller = TraceStateClient(meter)
    condition = CapacityCondition(
        task_id=task.task_id,
        family_id=task.family.family_id,
        intent=task.family.intent,
        outcome_variant=task.family.outcome_variant,
        prompt_variant=task.family.prompt_variant,
        branching=False,
        action_required=False,
        model="audit/model",
        harness="react-routed@structural-audit",
        workflow=WorkflowLoad.HIGH,
        representation=Representation.RAW,
        visible_tools=8,
        seed=task.family.seed,
    )
    episode = run_episode(
        condition,
        task,  # type: ignore[arg-type]
        controller,
        max_turns=None,
        max_tool_calls=None,
        tool_call_slack=0,
        turn_slack=2,
        max_total_tokens=1_000_000,
        max_completion_tokens=2048,
        dynamic_shortlist=True,
        early_stop_infeasible=True,
    )
    normalized = meter.normalize(episode)
    if not normalized.verdict.verified_success:
        raise AssertionError(f"controller reference failed: {normalized.errors}")
    if (
        len(normalized.trace) != task.certified_calls
        or controller.controller_calls != task.certified_calls - 1
        or base.calls != 2
        or len(normalized.response_ids) != 2
    ):
        raise AssertionError("controller accounting is not exact")
    request = _public_handoff_request(task)
    selected_get = task.program[1].tool_name
    function = _tool_function(request, selected_get)
    route = _source_route_description(function) if function else None
    first = TraceStateClient(_AuditClient(task))
    first.active = True
    first.pending_tool = selected_get
    first.route = route
    first_response = first._controller_response(request)
    # Hidden program state is deliberately changed but never passed to the
    # controller; equal public inputs must therefore produce equal behavior.
    hidden_mutation = replace(task, program=tuple(reversed(task.program)))
    second = TraceStateClient(_AuditClient(hidden_mutation))
    second.active = True
    second.pending_tool = selected_get
    second.route = route
    second_response = second._controller_response(request)
    if (
        first_response is None
        or second_response is None
        or first_response.tool_calls[0].name != second_response.tool_calls[0].name
        or first_response.tool_calls[0].arguments != second_response.tool_calls[0].arguments
    ):
        raise AssertionError("controller is not invariant to hidden-program mutation")
    mutated_request = _public_handoff_request(task, "public-mutated-handoff")
    sensitive = TraceStateClient(_AuditClient(task))
    sensitive.active = True
    sensitive.pending_tool = selected_get
    sensitive.route = route
    sensitive_response = sensitive._controller_response(mutated_request)
    if (
        sensitive_response is None
        or sensitive_response.tool_calls[0].arguments
        == first_response.tool_calls[0].arguments
    ):
        raise AssertionError("controller is insensitive to a public handoff mutation")
    invalid = TraceStateClient(_AuditClient(task))
    invalid.pending_tool = task.program[0].tool_name
    invalid.route = route
    empty = deepcopy(request)
    empty_observation = json.loads(empty.messages[-1]["content"])
    empty_observation["objects"] = []
    empty_observation["source_ids"] = []
    empty = replace(
        empty,
        messages=empty.messages[:-1]
        + ({**empty.messages[-1], "name": task.program[0].tool_name, "content": canonical_json(empty_observation)},),
    )
    invalid.complete(empty)
    if not invalid.closed or invalid.controller_calls:
        raise AssertionError("controller did not fail closed after an invalid entry")
    return {
        "certified_calls": task.certified_calls,
        "controller_calls": controller.controller_calls,
        "provider_calls": base.calls,
        "hidden_program_mutation_invariant": True,
        "public_handoff_mutation_sensitive": True,
        "invalid_entry_fail_closed": True,
    }


def _execution_smoke_audit() -> JSON:
    family = structural_families()[0]
    tasks = {
        calls: compile_structural_task(family, calls) for calls in CALL_LEVELS
    }
    policy = EpisodePolicy(
        name="structural-audit",
        tool_call_slack=2,
        turn_slack=2,
        max_total_tokens=128000,
        max_completion_tokens=1024,
        dynamic_shortlist=True,
        early_stop_infeasible=True,
    )
    conditions = [
        condition
        for condition in structural_conditions("audit/model", "react-routed@audit")
        if condition.family_id == family.family_id
    ]
    for condition in conditions:
        task = tasks[condition.certified_calls]
        base: CompletionClient = (
            _AuditClient(task)
            if condition.orchestration == "trace_state"
            else _SequentialAuditClient(task)
        )
        episode = run_structural_episode(condition, task, base, policy)
        expected_provider_calls = (
            2 if condition.orchestration == "trace_state" else task.certified_calls + 1
        )
        if (
            not episode.verdict.verified_success
            or len(episode.trace) != task.certified_calls
            or len(episode.response_ids) != expected_provider_calls
        ):
            raise AssertionError(f"structural execution smoke failed: {condition.to_json()}")
    return {"cells": len(conditions), "passed": True}


def static_audit() -> JSON:
    families = structural_families()
    tasks = structural_tasks()
    if len(families) != 24 or len(tasks) != 48:
        raise AssertionError("structural holdout population is incomplete")
    existing_families = tuple(
        family for split in Split for family in build_families(split)
    )
    if {family.family_id for family in families} & {
        family.family_id for family in existing_families
    }:
        raise AssertionError("structural family ID overlaps an existing split")
    if {family.case_id for family in families} & {
        family.case_id for family in existing_families
    }:
        raise AssertionError("structural case ID overlaps an existing split")
    old_tasks = tuple(
        task for split in Split for task in tasks_for_split(split)
    ) + tuple(
        task for split in Split for task in workflow_boundary_tasks(split)
    )
    old_records = {node.record_id for task in old_tasks for node in task.nodes}
    old_lookups = {node.lookup_key for task in old_tasks for node in task.nodes}
    if old_records & {node.record_id for task in tasks for node in task.nodes}:
        raise AssertionError("structural source ID overlaps an existing task")
    if old_lookups & {node.lookup_key for task in tasks for node in task.nodes}:
        raise AssertionError("structural lookup token overlaps an existing task")
    certificates = [certify_workflow_boundary_program(task) for task in tasks]  # type: ignore[arg-type]
    if not all(
        certificate.passed and certificate.minimum_calls in CALL_LEVELS
        for certificate in certificates
    ):
        raise AssertionError("structural shortest-program certificate failed")
    if not all(reference_workflow_boundary_execution(task).verified_success for task in tasks):  # type: ignore[arg-type]
        raise AssertionError("structural reference execution failed")
    for family in families:
        low, high = (
            compile_structural_task(family, calls) for calls in CALL_LEVELS
        )
        if not (
            low.semantic_signature == high.semantic_signature
            and low.request == high.request
            and low.expected_fact_map == high.expected_fact_map
            and low.nodes[-1].record_id == high.nodes[-1].record_id
            and low.base_tool_names == high.base_tool_names
            and [tool.to_openai() for tool in visible_menu(low, 8)]
            == [tool.to_openai() for tool in visible_menu(high, 8)]
        ):
            raise AssertionError(f"workflow twin drift: {family.family_id}")
    adapter_comparisons = _adapter_audit(tasks)
    controller = _controller_audit(tasks[-1])
    execution_smoke = _execution_smoke_audit()
    manifest = {
        "families": [
            {
                "family_id": family.family_id,
                "semantic_signature": family.semantic_signature,
                "intent": family.intent,
                "case_id": family.case_id,
                "seed": family.seed,
            }
            for family in families
        ],
        "tasks": [task.hidden_payload() for task in tasks],
    }
    return {
        "audit": "structural-repair-holdout-static-construct-v1",
        "passed": True,
        "intent_rules": len(families),
        "tasks": len(tasks),
        "certified_call_levels": list(CALL_LEVELS),
        "adapter_step_comparisons": adapter_comparisons,
        "controller_reference": controller,
        "execution_smoke": execution_smoke,
        "inventory_sha256": sha256(INVENTORY.read_bytes()).hexdigest(),
        "protocol_sha256": sha256(PROTOCOL.read_bytes()).hexdigest(),
        "task_manifest_hash": stable_hash(manifest),
    }
