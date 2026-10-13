"""Semantic-twin task construction and static construct certification."""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Iterable, Mapping, Sequence

from bottleneckbench.core.schema import JSON, Representation, canonical_json, stable_hash
from bottleneckbench.core.world import ToolSpec, World, core_tools, make_transfer_world, transfer_tools


class WorkflowLoad(StrEnum):
    """Shortest valid workflow-program bundle."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Split(StrEnum):
    """Non-overlapping benchmark partitions."""

    DEVELOPMENT = "development"
    MAIN = "main"
    TRANSFER = "transfer"


@dataclass(frozen=True, slots=True)
class ActionContract:
    """One reversible terminal-state obligation."""

    tool_name: str
    expected_fields: tuple[tuple[str, object], ...]
    target_record_id: str | None = None

    def to_json(self, *, logical_target: bool = False) -> JSON:
        return {
            "tool_name": self.tool_name,
            "expected_fields": dict(self.expected_fields),
            "target_record_id": (
                "logical_case_record"
                if logical_target and self.target_record_id is not None
                else self.target_record_id
            ),
        }


@dataclass(frozen=True, slots=True)
class SemanticFamily:
    """User-level semantics shared by all workflow twins."""

    family_id: str
    split: Split
    seed: int
    case_id: str
    intent: str
    connectors: tuple[str, str, str]
    facts: tuple[tuple[str, object], ...]
    decision: str
    outcome_variant: int
    prompt_variant: int
    branching: bool
    action_name: str | None

    @property
    def fact_map(self) -> dict[str, object]:
        return dict(self.facts)

    @property
    def positive_outcome(self) -> bool:
        return self.decision == _DECISION_LABELS[self.intent][0]

    @property
    def action_required(self) -> bool:
        return self.action_name is not None and self.positive_outcome

    @property
    def semantic_signature(self) -> str:
        return stable_hash(
            {
                "case_id": self.case_id,
                "intent": self.intent,
                "facts": dict(self.facts),
                "decision": self.decision,
                "outcome_variant": self.outcome_variant,
                "action": _logical_action_payload(self),
            }
        )


@dataclass(frozen=True, slots=True)
class RecordNode:
    """One hidden case record in the workflow access graph."""

    connector: str
    record_id: str
    lookup_key: str
    facts: tuple[str, ...]
    next_queries: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProgramStep:
    """One step in a certified partial-order reference program."""

    step_id: str
    tool_name: str
    connector: str
    arguments: tuple[tuple[str, str], ...]
    depends_on: tuple[str, ...]
    produces_facts: tuple[str, ...] = ()

    @property
    def argument_map(self) -> dict[str, str]:
        return dict(self.arguments)


@dataclass(frozen=True, slots=True)
class TwinTask:
    """One workflow intervention compiled from a semantic family."""

    family: SemanticFamily
    workflow: WorkflowLoad
    nodes: tuple[RecordNode, ...]
    program: tuple[ProgramStep, ...]
    action: ActionContract | None
    request: str
    base_tool_names: tuple[str, ...]

    @property
    def task_id(self) -> str:
        return f"{self.family.family_id}-{self.workflow.value}"

    @property
    def task_hash(self) -> str:
        return stable_hash(self.hidden_payload())

    @property
    def semantic_signature(self) -> str:
        return self.family.semantic_signature

    @property
    def expected_fact_map(self) -> dict[str, object]:
        return self.family.fact_map

    @property
    def required_tool_names(self) -> tuple[str, ...]:
        # Every workflow twin receives the same family-level operational menu.
        return self.base_tool_names

    def public_payload(self) -> JSON:
        return {
            "task_id": stable_hash(
                {"family": self.family.semantic_signature, "request": self.request}
            )[:20],
            "request": self.request,
        }

    def hidden_payload(self) -> JSON:
        return {
            "task_id": self.task_id,
            "semantic_signature": self.semantic_signature,
            "workflow": self.workflow.value,
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
            "action": self.action.to_json() if self.action else None,
            "base_tool_names": list(self.base_tool_names),
        }


@dataclass(frozen=True, slots=True)
class MinimalProgramCertificate:
    """Independent abstract-search certificate for a compiled task."""

    minimum_calls: int
    declared_calls: int
    minimal_plan_count: int
    critical_depth: int

    @property
    def passed(self) -> bool:
        return self.minimum_calls == self.declared_calls and self.minimal_plan_count > 0

    def to_json(self) -> JSON:
        return {
            "minimum_calls": self.minimum_calls,
            "declared_calls": self.declared_calls,
            "minimal_plan_count": self.minimal_plan_count,
            "critical_depth": self.critical_depth,
            "passed": self.passed,
        }


@dataclass(frozen=True, slots=True)
class CapacityCondition:
    """A content-addressed cell in the joint capacity surface."""

    task_id: str
    family_id: str
    intent: str
    outcome_variant: int
    prompt_variant: int
    branching: bool
    action_required: bool
    model: str
    harness: str
    workflow: WorkflowLoad
    representation: Representation
    visible_tools: int
    seed: int

    def __post_init__(self) -> None:
        if not self.task_id or not self.family_id or not self.intent or not self.model or not self.harness:
            raise ValueError("condition identity fields must not be empty")
        if self.outcome_variant not in {0, 1, 2} or self.prompt_variant not in {0, 1, 2}:
            raise ValueError("outcome_variant and prompt_variant must be 0, 1, or 2")
        if self.visible_tools not in {8, 32, 128}:
            raise ValueError("visible_tools must be one of 8, 32, or 128")

    def to_json(self) -> JSON:
        return {
            "task_id": self.task_id,
            "family_id": self.family_id,
            "intent": self.intent,
            "outcome_variant": self.outcome_variant,
            "prompt_variant": self.prompt_variant,
            "branching": self.branching,
            "action_required": self.action_required,
            "model": self.model,
            "harness": self.harness,
            "workflow": self.workflow.value,
            "representation": self.representation.value,
            "visible_tools": self.visible_tools,
            "seed": self.seed,
        }

    @property
    def condition_id(self) -> str:
        return stable_hash(self.to_json())


_CORE_CONNECTORS = ("accounting", "email", "files", "calendar")
_TRANSFER_CONNECTORS = ("crm", "tasks")
_INTENTS = (
    "escalation_assessment",
    "amount_reconciliation",
    "meeting_readiness",
    "response_sla_assessment",
    "document_consistency",
    "schedule_conflict_assessment",
    "draft_followup",
    "mark_reviewed",
    "schedule_review",
    "create_case_note",
    "create_meeting_brief",
    "draft_reconciliation_query",
)
_ACTION_BY_INTENT = {
    "draft_followup": "create_email_draft",
    "mark_reviewed": "update_invoice_status",
    "schedule_review": "create_calendar_event",
    "create_case_note": "create_file",
    "create_meeting_brief": "create_file",
    "draft_reconciliation_query": "create_email_draft",
}
_DECISION_LABELS = {
    "escalation_assessment": ("escalate", "do_not_escalate"),
    "amount_reconciliation": ("match", "mismatch"),
    "meeting_readiness": ("ready", "not_ready"),
    "response_sla_assessment": ("within_sla", "outside_sla"),
    "document_consistency": ("consistent", "inconsistent"),
    "schedule_conflict_assessment": ("conflict", "no_conflict"),
    "draft_followup": ("draft_required", "no_draft"),
    "mark_reviewed": ("verified", "not_verified"),
    "schedule_review": ("schedule_required", "no_schedule"),
    "create_case_note": ("note_required", "no_note"),
    "create_meeting_brief": ("brief_required", "no_brief"),
    "draft_reconciliation_query": ("query_required", "no_query"),
}
_SEARCH_TOOL = {
    "accounting": "search_invoices",
    "email": "search_email",
    "files": "search_files",
    "calendar": "search_calendar",
    "crm": "search_crm",
    "tasks": "search_tasks",
}
_GET_TOOL = {
    "accounting": "get_invoice",
    "email": "get_email",
    "files": "get_file",
    "calendar": "get_calendar",
    "crm": "get_crm",
    "tasks": "get_task",
}
_TOOLS_BY_NAME = {tool.name: tool for tool in core_tools() + transfer_tools()}
_ROUTE_FIELDS = ("workspace", "region", "collection")

_CORE_CONNECTORS_BY_INTENT = {
    "escalation_assessment": ("accounting", "calendar", "email"),
    "amount_reconciliation": ("accounting", "files", "email"),
    "meeting_readiness": ("files", "calendar", "email"),
    "response_sla_assessment": ("email", "calendar", "files"),
    "document_consistency": ("files", "accounting", "email"),
    "schedule_conflict_assessment": ("calendar", "calendar", "files"),
    "draft_followup": ("accounting", "email", "files"),
    "mark_reviewed": ("accounting", "files", "email"),
    "schedule_review": ("accounting", "calendar", "calendar"),
    "create_case_note": ("accounting", "email", "files"),
    "create_meeting_brief": ("files", "calendar", "email"),
    "draft_reconciliation_query": ("accounting", "files", "email"),
}
_TRANSFER_CONNECTORS_BY_INTENT = {
    "escalation_assessment": ("tasks", "calendar", "crm"),
    "amount_reconciliation": ("accounting", "files", "crm"),
    "meeting_readiness": ("files", "calendar", "crm"),
    "response_sla_assessment": ("email", "tasks", "crm"),
    "document_consistency": ("files", "accounting", "crm"),
    "schedule_conflict_assessment": ("calendar", "tasks", "files"),
    "draft_followup": ("tasks", "crm", "files"),
    "mark_reviewed": ("accounting", "files", "crm"),
    "schedule_review": ("tasks", "calendar", "calendar"),
    "create_case_note": ("tasks", "crm", "files"),
    "create_meeting_brief": ("files", "calendar", "crm"),
    "draft_reconciliation_query": ("accounting", "files", "crm"),
}


def _case_id(split: Split, index: int) -> str:
    digest = stable_hash({"split": split.value, "index": index, "salt": "capacity-surface-v3"})
    return f"CASE-{digest[:10].upper()}"


def _facts(intent: str, seed: int, outcome_variant: int) -> tuple[tuple[str, object], ...]:
    if outcome_variant not in {0, 1, 2}:
        raise ValueError("outcome_variant must be 0, 1, or 2")
    amount = 1000 + seed
    day = (seed % 20) + 1
    vendor = ("Northstar Labs", "Redwood Systems", "Blue Harbor Co", "Summit Works")[seed % 4]
    other_vendor = ("Redwood Systems", "Blue Harbor Co", "Summit Works", "Northstar Labs")[seed % 4]
    contact = f"case-{stable_hash({'seed': seed, 'kind': 'contact'})[:8]}@example.test"
    before_threshold = f"2026-09-{day:02d}"
    after_threshold = f"2026-10-{day:02d}"
    start = f"2026-10-{day:02d}T10:00:00Z"
    before_start = f"2026-10-{day:02d}T09:30:00Z"
    after_start = f"2026-10-{day:02d}T10:30:00Z"
    values: dict[str, tuple[tuple[str, object], ...]] = {
        "escalation_assessment": (
            ("status", "open" if outcome_variant == 1 else "overdue"),
            ("commitment_date", before_threshold),
            ("contact", contact),
        ),
        "amount_reconciliation": (
            ("invoice_amount", amount),
            ("receipt_amount", amount - 25 if outcome_variant == 1 else amount),
            ("currency", "EUR" if outcome_variant == 2 else "USD"),
        ),
        "meeting_readiness": (
            ("agreement_status", "draft" if outcome_variant == 1 else "signed"),
            ("kickoff_time", f"2026-09-{day:02d}T14:00:00Z"),
            ("attendee_confirmed", True),
        ),
        "response_sla_assessment": (
            ("received_at", f"2026-09-{day:02d}T09:00:00Z"),
            (
                "response_due",
                f"2026-09-{day:02d}T08:00:00Z"
                if outcome_variant == 1
                else f"2026-09-{day:02d}T17:00:00Z",
            ),
            ("owner_acknowledged", outcome_variant != 2),
        ),
        "document_consistency": (
            ("contract_vendor", vendor),
            ("invoice_vendor", other_vendor if outcome_variant == 1 else vendor),
            ("registration_status", "active"),
        ),
        "schedule_conflict_assessment": (
            ("proposed_start", start),
            ("blocking_end", before_start if outcome_variant == 1 else after_start),
            ("priority_override", outcome_variant == 2),
        ),
        "draft_followup": (
            ("status", "current" if outcome_variant == 1 else "overdue"),
            ("contact", contact),
            ("policy", "draft_only"),
        ),
        "mark_reviewed": (
            ("invoice_amount", amount),
            ("receipt_amount", amount - 25 if outcome_variant == 1 else amount),
            ("vendor", "" if outcome_variant == 2 else vendor),
        ),
        "schedule_review": (
            ("status", "current" if outcome_variant == 1 else "overdue"),
            ("review_start", start),
            ("review_end", after_start),
        ),
        "create_case_note": (
            ("status", "current" if outcome_variant == 1 else "overdue"),
            ("owner", contact),
            ("policy", "manual_review" if outcome_variant == 2 else "evidence_note"),
        ),
        "create_meeting_brief": (
            ("agreement_status", "draft" if outcome_variant == 1 else "signed"),
            ("kickoff_time", f"2026-09-{day:02d}T14:00:00Z"),
            ("attendee_confirmed", True),
        ),
        "draft_reconciliation_query": (
            ("invoice_amount", amount),
            ("receipt_amount", amount if outcome_variant == 1 else amount - 25),
            ("contact", "" if outcome_variant == 2 else contact),
        ),
    }
    return values[intent]


def _decision(intent: str, facts: Mapping[str, object]) -> str:
    if intent == "escalation_assessment":
        positive = (
            facts["status"] == "overdue" and str(facts["commitment_date"]) < "2026-10-01"
        )
    elif intent == "amount_reconciliation":
        positive = (
            facts["invoice_amount"] == facts["receipt_amount"] and facts["currency"] == "USD"
        )
    elif intent == "meeting_readiness":
        positive = (
            facts["agreement_status"] == "signed"
            and bool(facts["kickoff_time"])
            and facts["attendee_confirmed"] is True
        )
    elif intent == "response_sla_assessment":
        positive = (
            str(facts["response_due"]) > str(facts["received_at"])
            and facts["owner_acknowledged"] is True
        )
    elif intent == "document_consistency":
        positive = (
            facts["contract_vendor"] == facts["invoice_vendor"]
            and facts["registration_status"] == "active"
        )
    elif intent == "schedule_conflict_assessment":
        positive = (
            str(facts["blocking_end"]) > str(facts["proposed_start"])
            and facts["priority_override"] is False
        )
    elif intent == "draft_followup":
        positive = facts["status"] == "overdue" and facts["policy"] == "draft_only"
    elif intent == "mark_reviewed":
        positive = (
            facts["invoice_amount"] == facts["receipt_amount"] and bool(facts["vendor"])
        )
    elif intent == "schedule_review":
        positive = (
            facts["status"] == "overdue"
            and str(facts["review_end"]) > str(facts["review_start"])
        )
    elif intent == "create_case_note":
        positive = (
            facts["status"] == "overdue" and facts["policy"] == "evidence_note"
        )
    elif intent == "create_meeting_brief":
        positive = (
            facts["agreement_status"] == "signed" and facts["attendee_confirmed"] is True
        )
    elif intent == "draft_reconciliation_query":
        positive = (
            facts["invoice_amount"] != facts["receipt_amount"] and bool(facts["contact"])
        )
    else:
        raise ValueError(f"unknown intent: {intent}")
    return _DECISION_LABELS[intent][0 if positive else 1]


def build_families(split: Split | str, *, seed_offset: int = 0) -> tuple[SemanticFamily, ...]:
    """Generate disjoint semantic families with balanced intents and connector roles.

    ``seed_offset`` shifts every generated fact seed by a constant (a refresh
    pack identifier). The default, 0, reproduces today's output byte-for-byte;
    a nonzero value materializes a disjoint pack without editing this source
    (C11 / `docs/benchmark-card.md`'s refresh contract).
    """

    normalized = Split(split)
    count = 36 if normalized is Split.MAIN else 12
    offset = {Split.DEVELOPMENT: 0, Split.MAIN: 100, Split.TRANSFER: 200}[normalized]
    families: list[SemanticFamily] = []
    for local_index in range(count):
        index = offset + local_index
        seed = 300 + index + seed_offset
        intent_index = local_index % len(_INTENTS)
        repetition = local_index // len(_INTENTS)
        intent = _INTENTS[intent_index]
        connectors = (
            _TRANSFER_CONNECTORS_BY_INTENT[intent]
            if normalized is Split.TRANSFER
            else _CORE_CONNECTORS_BY_INTENT[intent]
        )
        case_id = _case_id(normalized, index)
        outcome_variant = (
            repetition
            if normalized is Split.MAIN
            else intent_index % 3
            if normalized is Split.DEVELOPMENT
            else (intent_index + 1) % 3
        )
        prompt_variant = (
            (repetition + intent_index) % 3
            if normalized is Split.MAIN
            else (intent_index + 1) % 3
            if normalized is Split.DEVELOPMENT
            else (intent_index + 2) % 3
        )
        facts = _facts(intent, seed, outcome_variant)
        families.append(
            SemanticFamily(
                family_id=f"{normalized.value}-{stable_hash({'i': index, 'case': case_id})[:12]}",
                split=normalized,
                seed=seed,
                case_id=case_id,
                intent=intent,
                connectors=(connectors[0], connectors[1], connectors[2]),
                facts=facts,
                decision=_decision(intent, dict(facts)),
                outcome_variant=outcome_variant,
                prompt_variant=prompt_variant,
                branching=(intent_index + repetition) % 2 == 1,
                action_name=_ACTION_BY_INTENT.get(intent),
            )
        )
    return tuple(families)


def _opaque_record_id(family: SemanticFamily, connector: str, node_index: int) -> str:
    return f"src-{stable_hash({'family': family.family_id, 'connector': connector, 'node': node_index})[:16]}"


def _action_target_id(family: SemanticFamily) -> str:
    return f"src-{stable_hash({'family': family.family_id, 'target': 'accounting_case'})[:16]}"


def _lookup_token(family: SemanticFamily, index: int) -> str:
    return f"lookup-{stable_hash({'family': family.family_id, 'edge': index})[:18]}"


def _logical_action_payload(family: SemanticFamily) -> JSON | None:
    if not family.action_required:
        return None
    facts = family.fact_map
    if family.action_name == "create_email_draft":
        if family.intent == "draft_reconciliation_query":
            fields = {
                "recipient": facts["contact"],
                "subject": f"Reconcile {family.case_id}",
                "body": f"Please verify the amount mismatch for {family.case_id}.",
                "status": "draft",
                "kind": "draft",
            }
        else:
            fields = {
                "recipient": facts["contact"],
                "subject": f"Review {family.case_id}",
                "body": f"Evidence-backed follow-up for {family.case_id}.",
                "status": "draft",
                "kind": "draft",
            }
        target = None
    elif family.action_name == "update_invoice_status":
        fields = {"status": "reviewed"}
        target = "logical_case_record"
    elif family.action_name == "create_calendar_event":
        fields = {
            "title": f"Review {family.case_id}",
            "start": facts["review_start"],
            "end": facts["review_end"],
            "kind": "created",
        }
        target = None
    else:
        fields = {
            "title": (
                f"Meeting brief {family.case_id}"
                if family.intent == "create_meeting_brief"
                else f"Case note {family.case_id}"
            ),
            "text": (
                f"Signed agreement and confirmed attendee for {family.case_id}."
                if family.intent == "create_meeting_brief"
                else f"Evidence note for overdue case {family.case_id}."
            ),
            "kind": "created",
        }
        target = None
    return {"tool_name": family.action_name, "expected_fields": fields, "target": target}


def _action_contract(family: SemanticFamily) -> ActionContract | None:
    payload = _logical_action_payload(family)
    if payload is None:
        return None
    target = (
        _action_target_id(family)
        if family.action_name == "update_invoice_status"
        else None
    )
    return ActionContract(
        tool_name=family.action_name or "",
        expected_fields=tuple(payload["expected_fields"].items()),
        target_record_id=target,
    )


def _action_arguments(family: SemanticFamily, action: ActionContract) -> dict[str, str]:
    fields = dict(action.expected_fields)
    if action.tool_name == "create_email_draft":
        return {
            "recipient": str(fields["recipient"]),
            "subject": str(fields["subject"]),
            "body": str(fields["body"]),
            "idempotency_key": f"action-{family.case_id}",
        }
    if action.tool_name == "update_invoice_status":
        return {
            "record_id": str(action.target_record_id),
            "status": str(fields["status"]),
            "idempotency_key": f"action-{family.case_id}",
        }
    if action.tool_name == "create_calendar_event":
        return {
            "title": str(fields["title"]),
            "start": str(fields["start"]),
            "end": str(fields["end"]),
            "idempotency_key": f"action-{family.case_id}",
        }
    return {
        "title": str(fields["title"]),
        "text": str(fields["text"]),
        "idempotency_key": f"action-{family.case_id}",
    }


def source_route(family: SemanticFamily) -> tuple[tuple[str, str], ...]:
    """Return the public three-coordinate source route for one semantic family."""

    digest = stable_hash({"family": family.family_id, "purpose": "source-route"})
    return tuple(
        zip(
            _ROUTE_FIELDS,
            (f"ws_{digest[:10]}", f"rg_{digest[10:20]}", f"cl_{digest[20:30]}"),
            strict=True,
        )
    )


def _route_text(route: Sequence[tuple[str, str]]) -> str:
    return ", ".join(f"{name}={value}" for name, value in route)


def _routed_tool(
    original: ToolSpec,
    route: tuple[tuple[str, str], ...],
    *,
    distractor_kind: str = "core",
) -> ToolSpec:
    route_id = stable_hash(
        {"operation": original.name, "route": dict(route)}
    )[:12]
    if original.name.startswith("search_"):
        operation = f"Search current {original.connector} records"
        continuation = (
            " Use the exact case reference from the user or the exact query value "
            "from a returned next_handoff."
        )
    elif original.name.startswith("get_"):
        operation = f"Read one current {original.connector} record by exact record ID"
        continuation = ""
    else:
        raise ValueError(f"only read tools can be source-routed: {original.name}")
    return replace(
        original,
        name=f"{original.name}_route_{route_id}",
        capability=f"{original.name}@{','.join(value for _, value in route)}",
        description=(
            f"{operation} using source route {_route_text(route)}."
            f"{continuation}"
        ),
        distractor_kind=distractor_kind,
        executable=True,
    )


def underlying_tool_name(tool: ToolSpec) -> str:
    """Return the deterministic sandbox operation behind a public routed alias."""

    return tool.capability.partition("@")[0]


def tool_source_route(tool: ToolSpec) -> tuple[tuple[str, str], ...] | None:
    """Decode a routed tool's public source coordinates from hidden metadata."""

    _, separator, encoded = tool.capability.partition("@")
    if not separator:
        return None
    values = encoded.split(",")
    if len(values) != len(_ROUTE_FIELDS):
        raise ValueError(f"invalid source-route capability: {tool.capability}")
    return tuple(zip(_ROUTE_FIELDS, values, strict=True))


def _routed_read_tools(family: SemanticFamily) -> tuple[ToolSpec, ...]:
    route = source_route(family)
    names = tuple(
        dict.fromkeys(
            name
            for connector in family.connectors
            for name in (_SEARCH_TOOL[connector], _GET_TOOL[connector])
        )
    )
    return tuple(_routed_tool(_TOOLS_BY_NAME[name], route) for name in names)


def _routed_name(family: SemanticFamily, original_name: str) -> str:
    return _routed_tool(_TOOLS_BY_NAME[original_name], source_route(family)).name


def _request(family: SemanticFamily) -> str:
    keys = [name for name, _ in family.facts]
    fact_object = ", ".join(f'"{key}": <value>' for key in keys)
    rules = {
        "escalation_assessment": (
            "Return decision escalate only when the current status is overdue and the commitment date is "
            "before 2026-10-01; otherwise return do_not_escalate. Also report the current contact."
        ),
        "amount_reconciliation": (
            "Return decision match only when the invoice and receipt amounts are equal and the currency is "
            "USD; otherwise return mismatch."
        ),
        "meeting_readiness": (
            "Return decision ready only when the agreement is signed, a kickoff time exists, and the attendee "
            "is confirmed; otherwise return not_ready."
        ),
        "response_sla_assessment": (
            "Return decision within_sla only when the response due time is after receipt and the current owner "
            "has acknowledged the case; otherwise return outside_sla."
        ),
        "document_consistency": (
            "Return decision consistent only when the contract and invoice name the same vendor and its current "
            "registration is active; otherwise return inconsistent."
        ),
        "schedule_conflict_assessment": (
            "Return decision conflict only when the existing block ends after the proposed start and no priority "
            "override is present; otherwise return no_conflict."
        ),
        "draft_followup": (
            "Return decision draft_required and create, but do not send, a draft only when the case is overdue "
            f"and policy is draft_only. Use the current contact, subject 'Review {family.case_id}', and body "
            f"'Evidence-backed follow-up for {family.case_id}.' Otherwise return no_draft and do not mutate state."
        ),
        "mark_reviewed": (
            "Return decision verified and mark the case invoice reviewed only when invoice and receipt amounts "
            "agree and the vendor is identified. Otherwise return not_verified and do not mutate state. Never "
            "issue payment."
        ),
        "schedule_review": (
            "Return decision schedule_required and create a calendar event only when the case is overdue and "
            f"review_end is after review_start. Title it 'Review {family.case_id}'. Otherwise return no_schedule "
            "and do not mutate state."
        ),
        "create_case_note": (
            "Return decision note_required and create a document only when the case is overdue and policy is "
            f"evidence_note. Title it 'Case note {family.case_id}' with text 'Evidence note for overdue case "
            f"{family.case_id}.' Otherwise return no_note and do not mutate state."
        ),
        "create_meeting_brief": (
            "Return decision brief_required and create a document only when the agreement is signed and the "
            f"attendee is confirmed. Title it 'Meeting brief {family.case_id}' with text 'Signed agreement and "
            f"confirmed attendee for {family.case_id}.' Otherwise return no_brief and do not mutate state."
        ),
        "draft_reconciliation_query": (
            "Return decision query_required and create, but do not send, a draft only when invoice_amount and "
            f"receipt_amount differ and a contact is present. Use subject 'Reconcile {family.case_id}' and body "
            f"'Please verify the amount mismatch for {family.case_id}.' Otherwise return no_query and do not "
            "mutate state."
        ),
    }[family.intent]
    openings = (
        f"Assess current records for case {family.case_id}.",
        f"Using only current operational evidence, resolve case {family.case_id}.",
        f"Review the authoritative records associated with case {family.case_id}.",
    )
    opening = openings[family.prompt_variant]
    route = _route_text(source_route(family))
    return (
        f"{opening} Required source route: {route}. Use only current records whose tool description "
        "matches all three route coordinates; current records on other routes belong to other workspaces "
        f"and cannot support this case. {rules} Return exactly one JSON object with this shape: "
        f'{{"decision":"<label>","facts":{{{fact_object}}},"sources":["<source record id>"]}}. '
        "The sources array must contain exactly the current records from the required source route that support the reported "
        "facts, each once; exclude newly created action outputs or receipts. Do not use archived evidence, "
        "send messages, issue payment, delete records, or invent missing values."
    )


def _base_tool_names(family: SemanticFamily) -> tuple[str, ...]:
    names = [tool.name for tool in _routed_read_tools(family)]
    if family.action_name:
        names.append(family.action_name)
    return tuple(dict.fromkeys(names))


def compile_task(family: SemanticFamily, workflow: WorkflowLoad | str) -> TwinTask:
    """Compile one workflow twin without changing user-level semantics."""

    level = WorkflowLoad(workflow)
    node_count = {WorkflowLoad.LOW: 1, WorkflowLoad.MEDIUM: 2, WorkflowLoad.HIGH: 3}[level]
    fact_names = tuple(name for name, _ in family.facts)
    allocations: list[tuple[str, ...]]
    if node_count == 1:
        allocations = [fact_names]
    elif node_count == 2:
        allocations = [(fact_names[0],), fact_names[1:]]
    else:
        allocations = [(name,) for name in fact_names]
    lookup_keys = (family.case_id,) + tuple(
        _lookup_token(family, index) for index in range(1, node_count)
    )
    nodes: list[RecordNode] = []
    for index in range(node_count):
        if index == node_count - 1:
            next_queries: tuple[str, ...] = ()
        elif level is WorkflowLoad.HIGH and family.branching and index == 0:
            next_queries = lookup_keys[1:]
        elif level is WorkflowLoad.HIGH and family.branching:
            next_queries = ()
        else:
            next_queries = (lookup_keys[index + 1],)
        nodes.append(
            RecordNode(
                connector=family.connectors[index],
                record_id=_opaque_record_id(family, family.connectors[index], index),
                lookup_key=lookup_keys[index],
                facts=allocations[index],
                next_queries=next_queries,
            )
        )

    steps: list[ProgramStep] = []
    get_step_ids: list[str] = []
    for index, node in enumerate(nodes):
        search_id = f"search-{index + 1}"
        get_id = f"get-{index + 1}"
        if index == 0:
            search_dependencies: tuple[str, ...] = ()
        elif level is WorkflowLoad.HIGH and family.branching:
            search_dependencies = ("get-1",)
        else:
            search_dependencies = (f"get-{index}",)
        steps.append(
            ProgramStep(
                step_id=search_id,
                tool_name=_routed_name(family, _SEARCH_TOOL[node.connector]),
                connector=node.connector,
                arguments=(("query", node.lookup_key),),
                depends_on=search_dependencies,
            )
        )
        steps.append(
            ProgramStep(
                step_id=get_id,
                tool_name=_routed_name(family, _GET_TOOL[node.connector]),
                connector=node.connector,
                arguments=(("record_id", node.record_id),),
                depends_on=(search_id,),
                produces_facts=node.facts,
            )
        )
        get_step_ids.append(get_id)

    action = _action_contract(family)
    if action:
        dependencies = (
            tuple(get_step_ids)
            if level is WorkflowLoad.HIGH and family.branching
            else (get_step_ids[-1],)
        )
        steps.append(
            ProgramStep(
                step_id="action",
                tool_name=action.tool_name,
                connector=_TOOLS_BY_NAME[action.tool_name].connector,
                arguments=tuple(_action_arguments(family, action).items()),
                depends_on=dependencies,
            )
        )
    return TwinTask(
        family=family,
        workflow=level,
        nodes=tuple(nodes),
        program=tuple(steps),
        action=action,
        request=_request(family),
        base_tool_names=_base_tool_names(family),
    )


def _remap_world(world: World, family: SemanticFamily) -> None:
    remapped: dict[str, dict[str, JSON]] = {}
    for connector, records in world.records.items():
        remapped[connector] = {}
        for old_id, record in records.items():
            new_id = f"src-{stable_hash({'family': family.family_id, 'connector': connector, 'old': old_id})[:16]}"
            copied = deepcopy(record)
            copied["record_id"] = new_id
            remapped[connector][new_id] = copied
    world.records = remapped


def build_world(task: TwinTask) -> World:
    """Build an opaque-ID sandbox whose relevant fact set matches every twin."""

    world = make_transfer_world(task.family.seed)
    _remap_world(world, task.family)
    facts = task.expected_fact_map
    nodes_by_lookup = {node.lookup_key: node for node in task.nodes}
    for node in task.nodes:
        record: JSON = {
            "record_id": node.record_id,
            "version": 1,
            "title": f"Current case record in {node.connector}",
            "kind": "case_record",
            "invoice_id": task.family.case_id,
            "lookup_key": node.lookup_key,
        }
        for fact_name in node.facts:
            record[fact_name] = deepcopy(facts[fact_name])
        handoffs = [
            {
                "connector": nodes_by_lookup[query].connector,
                "query": query,
            }
            for query in node.next_queries
        ]
        if len(handoffs) == 1:
            record["next_handoff"] = handoffs[0]
        elif handoffs:
            record["next_handoffs"] = handoffs
        world.records[node.connector][node.record_id] = record

    if task.action and task.action.target_record_id:
        target_id = task.action.target_record_id
        target = world.records["accounting"].get(target_id)
        if target is None:
            target = {
                "record_id": target_id,
                "version": 1,
                "title": f"Case invoice {task.family.case_id}",
                "kind": "invoice",
                "invoice_id": task.family.case_id,
                "status": "overdue",
            }
            world.records["accounting"][target_id] = target
        # The target handle is learned only after the complete evidence chain.
        world.records[task.nodes[-1].connector][task.nodes[-1].record_id][
            "action_target_id"
        ] = target_id
    return world


def _distractor(index: int, task: TwinTask) -> ToolSpec:
    originals = tuple(
        _TOOLS_BY_NAME[name]
        for name in dict.fromkeys(
            name
            for connector in task.family.connectors
            for name in (_SEARCH_TOOL[connector], _GET_TOOL[connector])
        )
    )
    original = originals[index % len(originals)]
    route = list(source_route(task.family))
    coordinate = (index // len(originals)) % len(route)
    digest = stable_hash(
        {
            "family": task.family.family_id,
            "operation": original.name,
            "index": index,
            "purpose": "route-neighbor",
        }
    )
    field, current = route[coordinate]
    prefix = current.split("_", 1)[0]
    replacement = f"{prefix}_{digest[:10]}"
    if replacement == current:
        replacement = f"{prefix}_{digest[10:20]}"
    route[coordinate] = (field, replacement)
    return _routed_tool(
        original,
        tuple(route),
        distractor_kind="route_neighbor",
    )


def visible_menu(task: TwinTask, size: int) -> tuple[ToolSpec, ...]:
    """Return a deterministic menu with guaranteed operational availability."""

    if size not in {8, 32, 128}:
        raise ValueError("visible menu size must be one of 8, 32, or 128")
    operational = {
        tool.name: tool for tool in _routed_read_tools(task.family)
    }
    if task.family.action_name:
        operational[task.family.action_name] = _TOOLS_BY_NAME[task.family.action_name]
    base = [operational[name] for name in task.base_tool_names]
    if len(base) > size:
        raise ValueError("visible menu is smaller than the family operational set")
    fillers = [_distractor(index, task) for index in range(size - len(base))]
    selected = base + fillers
    selected.sort(
        key=lambda tool: stable_hash(
            {"family": task.family.family_id, "tool": tool.name}
        )
    )
    if len({tool.name for tool in selected}) != size:
        raise AssertionError("visible menu contains duplicate tool names")
    return tuple(selected)


def _critical_depth(program: Sequence[ProgramStep]) -> int:
    depths: dict[str, int] = {}
    remaining = {step.step_id: step for step in program}
    while remaining:
        progressed = False
        for step_id, step in tuple(remaining.items()):
            if all(parent in depths for parent in step.depends_on):
                depths[step_id] = 1 + max(
                    (depths[parent] for parent in step.depends_on), default=0
                )
                del remaining[step_id]
                progressed = True
        if not progressed:
            raise ValueError("program contains a dependency cycle or unknown parent")
    return max(depths.values(), default=0)


@dataclass(frozen=True, slots=True)
class _PlannerState:
    queries: frozenset[str]
    record_ids: frozenset[str]
    facts: frozenset[str]
    action_done: bool


def certify_minimal_program(task: TwinTask) -> MinimalProgramCertificate:
    """Find the shortest valid abstract plan without consulting the declared program."""

    initial = _PlannerState(frozenset({task.family.case_id}), frozenset(), frozenset(), False)
    queue = deque([(initial, 0)])
    distances = {initial: 0}
    ways = {initial: 1}
    minimum: int | None = None
    minimal_ways = 0
    required_facts = frozenset(task.expected_fact_map)

    def goal(state: _PlannerState) -> bool:
        return required_facts <= state.facts and (task.action is None or state.action_done)

    while queue:
        state, distance = queue.popleft()
        if minimum is not None and distance > minimum:
            break
        if goal(state):
            minimum = distance
            minimal_ways += ways[state]
            continue
        successors: list[_PlannerState] = []
        for node in task.nodes:
            if node.lookup_key in state.queries and node.record_id not in state.record_ids:
                successors.append(
                    _PlannerState(
                        state.queries,
                        state.record_ids | {node.record_id},
                        state.facts,
                        state.action_done,
                    )
                )
            if node.record_id in state.record_ids and not set(node.facts) <= state.facts:
                successors.append(
                    _PlannerState(
                        state.queries | frozenset(node.next_queries),
                        state.record_ids,
                        state.facts | frozenset(node.facts),
                        state.action_done,
                    )
                )
        if task.action and required_facts <= state.facts and not state.action_done:
            successors.append(
                _PlannerState(state.queries, state.record_ids, state.facts, True)
            )
        for successor in successors:
            next_distance = distance + 1
            known = distances.get(successor)
            if known is None:
                distances[successor] = next_distance
                ways[successor] = ways[state]
                queue.append((successor, next_distance))
            elif known == next_distance:
                ways[successor] += ways[state]
    if minimum is None:
        minimum = -1
    return MinimalProgramCertificate(
        minimum_calls=minimum,
        declared_calls=len(task.program),
        minimal_plan_count=minimal_ways,
        critical_depth=_critical_depth(task.program),
    )


def _is_subsequence(smaller: Sequence[str], larger: Sequence[str]) -> bool:
    iterator = iter(larger)
    return all(any(candidate == item for candidate in iterator) for item in smaller)


def _source_route_menu_contract(task: TwinTask) -> list[str]:
    expected_route = source_route(task.family)
    expected_operations = {
        underlying_tool_name(tool)
        for tool in _routed_read_tools(task.family)
    }
    failures: list[str] = []
    neighbor_counts: list[int] = []
    for size in (8, 32, 128):
        menu = visible_menu(task, size)
        routed = [tool for tool in menu if tool_source_route(tool) is not None]
        cores_by_operation: dict[str, int] = {
            operation: 0 for operation in expected_operations
        }
        neighbors_by_operation: dict[str, int] = {
            operation: 0 for operation in expected_operations
        }
        for tool in routed:
            operation = underlying_tool_name(tool)
            route = tool_source_route(tool)
            if operation not in expected_operations or route is None:
                failures.append(f"{task.task_id}:v{size}:{tool.name}:unexpected_operation")
                continue
            original = _TOOLS_BY_NAME[operation]
            if tool.input_schema != original.input_schema:
                failures.append(f"{task.task_id}:v{size}:{tool.name}:schema_drift")
            expected_name_prefix = f"{operation}_route_"
            suffix = tool.name.removeprefix(expected_name_prefix)
            if (
                not tool.name.startswith(expected_name_prefix)
                or len(suffix) != 12
                or any(character not in "0123456789abcdef" for character in suffix)
            ):
                failures.append(f"{task.task_id}:v{size}:{tool.name}:alias_imbalance")
            if tool.description != _routed_tool(
                original,
                route,
                distractor_kind=tool.distractor_kind,
            ).description:
                failures.append(f"{task.task_id}:v{size}:{tool.name}:description_drift")
            if any(
                label in tool.description.casefold()
                for label in (
                    "template",
                    "summary",
                    "service health",
                    "non-authoritative",
                    "not authoritative",
                )
            ):
                failures.append(f"{task.task_id}:v{size}:{tool.name}:giveaway_label")
            distance = sum(
                observed != expected
                for (_, observed), (_, expected) in zip(
                    route, expected_route, strict=True
                )
            )
            if is_distractor(tool):
                neighbors_by_operation[operation] += 1
                if distance != 1:
                    failures.append(
                        f"{task.task_id}:v{size}:{tool.name}:distance_{distance}"
                    )
            else:
                cores_by_operation[operation] += 1
                if distance != 0:
                    failures.append(f"{task.task_id}:v{size}:{tool.name}:core_route")
        if any(count != 1 for count in cores_by_operation.values()):
            failures.append(f"{task.task_id}:v{size}:nonunique_correct_route")
        if size == 128 and any(count == 0 for count in neighbors_by_operation.values()):
            failures.append(f"{task.task_id}:v128:unsupported_operation_neighbor")
        neighbor_counts.append(sum(neighbors_by_operation.values()))
    if not neighbor_counts[0] < neighbor_counts[1] < neighbor_counts[2]:
        failures.append(f"{task.task_id}:neighbor_load_not_increasing")
    return failures


def static_construct_audit(split: Split | str) -> JSON:
    """Run pre-model gates that do not depend on outcome calibration."""

    normalized = Split(split)
    families = build_families(normalized)
    grouped = {
        family.family_id: tuple(compile_task(family, level) for level in WorkflowLoad)
        for family in families
    }
    twin_failures: list[str] = []
    gold_failures: list[str] = []
    program_failures: list[str] = []
    menu_failures: list[str] = []
    layout_failures: list[str] = []
    decision_failures: list[str] = []
    source_contract_failures: list[str] = []
    route_contract_failures: list[str] = []
    certificates: dict[str, JSON] = {}
    for family_id, tasks in grouped.items():
        if len({task.semantic_signature for task in tasks}) != 1:
            twin_failures.append(f"{family_id}:semantic_signature")
        if len({task.request for task in tasks}) != 1:
            twin_failures.append(f"{family_id}:request")
        if len({task.base_tool_names for task in tasks}) != 1:
            twin_failures.append(f"{family_id}:operational_menu")
        expected_connectors = (
            _TRANSFER_CONNECTORS_BY_INTENT[tasks[0].family.intent]
            if normalized is Split.TRANSFER
            else _CORE_CONNECTORS_BY_INTENT[tasks[0].family.intent]
        )
        if tasks[0].family.connectors != expected_connectors:
            layout_failures.append(f"{family_id}:connector_roles")
        family = tasks[0].family
        if _decision(family.intent, family.fact_map) != family.decision:
            decision_failures.append(f"{family_id}:decision_rule")
        if any((task.action is not None) != family.action_required for task in tasks):
            decision_failures.append(f"{family_id}:action_contract")
        if any(
            (any(step.step_id == "action" for step in task.program)) != family.action_required
            for task in tasks
        ):
            decision_failures.append(f"{family_id}:action_program")
        if not all(label in tasks[0].request for label in _DECISION_LABELS[family.intent]):
            decision_failures.append(f"{family_id}:decision_labels")
        if not all(
            phrase in tasks[0].request
            for phrase in (
                "Required source route:",
                "matches all three route coordinates",
                "exactly the current records from the required source route",
                "exclude newly created action outputs or receipts",
            )
        ):
            source_contract_failures.append(family_id)
        if _route_text(source_route(family)) not in tasks[0].request:
            source_contract_failures.append(f"{family_id}:route_value")
        route_contract_failures.extend(_source_route_menu_contract(tasks[0]))
        for size in (8, 32, 128):
            serialized = {
                canonical_json([tool.to_openai() for tool in visible_menu(task, size)])
                for task in tasks
            }
            if len(serialized) != 1:
                menu_failures.append(f"{family_id}:workflow_menu_{size}")
        ordered = {
            size: tuple(tool.name for tool in visible_menu(tasks[0], size))
            for size in (8, 32, 128)
        }
        if not (
            set(ordered[8]) < set(ordered[32]) < set(ordered[128])
            and _is_subsequence(ordered[8], ordered[32])
            and _is_subsequence(ordered[32], ordered[128])
        ):
            menu_failures.append(f"{family_id}:nested_master_order")
        for task in tasks:
            lower = task.request.casefold()
            hidden_terms = {
                step.tool_name.casefold() for step in task.program
            } | {
                node.record_id.casefold() for node in task.nodes
            } | {
                query.casefold()
                for node in task.nodes
                for query in node.next_queries
            }
            hidden_terms.update(
                {
                    task.family.family_id.casefold(),
                    task.task_id.casefold(),
                    *(
                        {task.action.target_record_id.casefold()}
                        if task.action and task.action.target_record_id
                        else set()
                    ),
                }
            )
            if any(term in lower for term in hidden_terms) or any(
                term in lower
                for term in ("obligation 1", "required order", "depends_on", "gold plan")
            ):
                gold_failures.append(task.task_id)
            certificate = certify_minimal_program(task)
            certificates[task.task_id] = certificate.to_json()
            expected_range = {
                WorkflowLoad.LOW: {2, 3},
                WorkflowLoad.MEDIUM: {4, 5},
                WorkflowLoad.HIGH: {6, 7},
            }[task.workflow]
            if not certificate.passed or certificate.minimum_calls not in expected_range:
                program_failures.append(task.task_id)

    cell_counts = {
        level.value: sum(1 for tasks in grouped.values() for task in tasks if task.workflow is level)
        for level in WorkflowLoad
    }
    intent_counts = {
        intent: sum(family.intent == intent for family in families) for intent in _INTENTS
    }
    prompt_variant_counts = {
        variant: sum(family.prompt_variant == variant for family in families)
        for variant in range(3)
    }
    outcome_variant_counts = {
        variant: sum(family.outcome_variant == variant for family in families)
        for variant in range(3)
    }
    polarity_counts = {
        "positive": sum(family.positive_outcome for family in families),
        "negative": sum(not family.positive_outcome for family in families),
    }
    action_counts = {
        "required": sum(family.action_required for family in families),
        "forbidden": sum(
            family.action_name is not None and not family.action_required
            for family in families
        ),
    }
    branching_counts = {
        "branching": sum(family.branching for family in families),
        "linear": sum(not family.branching for family in families),
    }
    outcome_prompt_counts = {
        f"{outcome}|{prompt}": sum(
            family.outcome_variant == outcome and family.prompt_variant == prompt
            for family in families
        )
        for outcome in range(3)
        for prompt in range(3)
    }
    connector_counts = {
        connector: sum(connector in family.connectors for family in families)
        for connector in (*_CORE_CONNECTORS, *_TRANSFER_CONNECTORS)
    }
    minimum_families = 36 if normalized is Split.MAIN else 12
    expected_intent_count = minimum_families // len(_INTENTS)
    required_connectors = (
        _TRANSFER_CONNECTORS if normalized is Split.TRANSFER else _CORE_CONNECTORS
    )
    support_pass = (
        len(families) == minimum_families
        and len(set(cell_counts.values())) == 1
        and set(intent_counts.values()) == {expected_intent_count}
        and all(connector_counts[connector] > 0 for connector in required_connectors)
        and min(prompt_variant_counts.values()) > 0
        and max(prompt_variant_counts.values()) - min(prompt_variant_counts.values()) <= 1
        and min(outcome_variant_counts.values()) > 0
        and max(outcome_variant_counts.values()) - min(outcome_variant_counts.values()) <= 1
        and polarity_counts["positive"] == polarity_counts["negative"]
        and action_counts["required"] == action_counts["forbidden"]
        and branching_counts["branching"] == branching_counts["linear"]
    )
    if normalized is Split.MAIN:
        support_pass &= set(outcome_prompt_counts.values()) == {4}
        support_pass &= all(
            {family.positive_outcome for family in families if family.intent == intent}
            == {False, True}
            for intent in _INTENTS
        )
    public_hashes = [stable_hash(task.public_payload()) for tasks in grouped.values() for task in tasks]
    repeated_hashes = [
        stable_hash(compile_task(family, level).public_payload())
        for family in families
        for level in WorkflowLoad
    ]
    reproducible = public_hashes == repeated_hashes
    checks = (
        {
            "name": "semantic_twin_equivalence",
            "passed": not twin_failures,
            "details": {"families": len(families), "failures": twin_failures},
        },
        {
            "name": "minimal_program_certification",
            "passed": not program_failures,
            "details": {"failures": program_failures, "certificates": certificates},
        },
        {
            "name": "factor_support",
            "passed": support_pass,
            "details": {
                "cell_counts": cell_counts,
                "intent_counts": intent_counts,
                "connector_counts": connector_counts,
                "prompt_variant_counts": prompt_variant_counts,
                "outcome_variant_counts": outcome_variant_counts,
                "outcome_prompt_counts": outcome_prompt_counts,
                "polarity_counts": polarity_counts,
                "action_counts": action_counts,
                "branching_counts": branching_counts,
            },
        },
        {
            "name": "decision_and_action_contract",
            "passed": not decision_failures,
            "details": {"failures": decision_failures},
        },
        {
            "name": "response_source_contract",
            "passed": not source_contract_failures,
            "details": {"failures": source_contract_failures},
        },
        {
            "name": "menu_intervention_isolation",
            "passed": not menu_failures,
            "details": {"failures": menu_failures},
        },
        {
            "name": "source_route_near_neighbors",
            "passed": not route_contract_failures,
            "details": {
                "contract": "one exact three-coordinate route; every added read tool is a Hamming-distance-one current-route neighbor",
                "failures": route_contract_failures,
            },
        },
        {
            "name": "connector_fact_layout",
            "passed": not layout_failures,
            "details": {"failures": layout_failures},
        },
        {
            "name": "gold_isolation",
            "passed": not gold_failures,
            "details": {"failures": gold_failures},
        },
        {
            "name": "deterministic_reproducibility",
            "passed": reproducible,
            "details": {"task_count": len(public_hashes)},
        },
    )
    return {
        "audit": "capacity-surface-static-construct-v4",
        "split": normalized.value,
        "passed": all(check["passed"] for check in checks),
        "checks": list(checks),
        "manifest_hash": stable_hash(
            {
                "split": normalized.value,
                "tasks": [task.task_hash for tasks in grouped.values() for task in tasks],
            }
        ),
    }


def tasks_for_split(split: Split | str) -> tuple[TwinTask, ...]:
    return tuple(
        compile_task(family, workflow)
        for family in build_families(split)
        for workflow in WorkflowLoad
    )


def tool_by_name(name: str) -> ToolSpec:
    return _TOOLS_BY_NAME[name]


def is_distractor(tool: ToolSpec) -> bool:
    return tool.distractor_kind != "core"


def canonical_task_manifest(split: Split | str) -> str:
    return canonical_json(
        {
            "split": Split(split).value,
            "tasks": [task.hidden_payload() for task in tasks_for_split(split)],
        }
    )
