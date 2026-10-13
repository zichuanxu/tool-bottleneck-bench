"""Registered-catalog discovery block with a fixed visible shortlist budget."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Mapping, Protocol, Sequence

from bottleneckbench.design import Split
from bottleneckbench.core.agnes import AgnesError, ModelRequest, ModelResponse
from bottleneckbench.core.schema import JSON, RunStatus, stable_hash
from bottleneckbench.core.world import ToolSpec, core_tools, transfer_tools
from bottleneckbench.request_scope import completion_request_scope


class Retriever(StrEnum):
    FLAT = "flat"
    HIERARCHICAL = "hierarchical"


@dataclass(frozen=True, slots=True)
class DiscoveryCase:
    case_id: str
    split: Split
    seed: int
    request: str
    required_tool: str
    expected_arguments: tuple[tuple[str, str], ...]
    connector: str

    @property
    def argument_map(self) -> dict[str, str]:
        return dict(self.expected_arguments)

    @property
    def case_hash(self) -> str:
        return stable_hash(
            {
                "case_id": self.case_id,
                "split": self.split.value,
                "request": self.request,
                "required_tool": self.required_tool,
                "expected_arguments": self.argument_map,
                "connector": self.connector,
            }
        )


@dataclass(frozen=True, slots=True)
class DiscoveryCondition:
    case_id: str
    split: Split
    model: str
    harness: str
    retriever: Retriever
    registry_size: int
    visible_tools: int
    seed: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "split", Split(self.split))
        object.__setattr__(self, "retriever", Retriever(self.retriever))
        if self.split is Split.TRANSFER:
            raise ValueError("discovery supports development and main splits only")
        if self.registry_size not in {32, 128, 512, 2048}:
            raise ValueError("registry_size must be 32, 128, 512, or 2048")
        if self.visible_tools != 16:
            raise ValueError("discovery visible_tools is frozen at 16")

    def to_json(self) -> JSON:
        return {
            "case_id": self.case_id,
            "split": self.split.value,
            "model": self.model,
            "harness": self.harness,
            "retriever": self.retriever.value,
            "registry_size": self.registry_size,
            "visible_tools": self.visible_tools,
            "seed": self.seed,
        }

    @property
    def condition_id(self) -> str:
        return stable_hash(self.to_json())


@dataclass(frozen=True, slots=True)
class DiscoveryExposure:
    tools: tuple[ToolSpec, ...]
    required_tool: str
    selected_connectors: tuple[str, ...]

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(tool.name for tool in self.tools)

    @property
    def required_available(self) -> bool:
        return self.required_tool in self.tool_names


@dataclass(frozen=True, slots=True)
class DiscoveryEpisode:
    condition: DiscoveryCondition
    status: RunStatus
    exposure: DiscoveryExposure
    selected_tool: str | None
    selected_arguments: JSON
    success: bool
    prompt_tokens: int
    completion_tokens: int
    response_id: str
    errors: tuple[str, ...]
    response_model: str | None = None
    usage_source: str | None = None

    def to_json(self) -> JSON:
        payload: JSON = {
            "condition": self.condition.to_json(),
            "condition_id": self.condition.condition_id,
            "status": self.status.value,
            "exposure": {
                "tool_names": list(self.exposure.tool_names),
                "selected_connectors": list(self.exposure.selected_connectors),
                "required_tool": self.exposure.required_tool,
                "required_available": self.exposure.required_available,
            },
            "selected_tool": self.selected_tool,
            "selected_arguments": deepcopy(self.selected_arguments),
            "success": self.success,
            "usage": {
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.prompt_tokens + self.completion_tokens,
            },
            "response_id": self.response_id,
            "errors": list(self.errors),
        }
        if self.response_model is not None:
            payload["response_model"] = self.response_model
        if self.usage_source is not None:
            payload["usage_source"] = self.usage_source
        return payload


class CompletionClient(Protocol):
    def complete(self, request: ModelRequest) -> ModelResponse:
        """Return one normalized completion."""


_BASE_TOOLS = core_tools() + transfer_tools()
_CONNECTOR_TERMS = {
    "accounting": {"accounting", "invoice", "vendor", "amount", "currency", "overdue"},
    "email": {"email", "mail", "message", "sender", "recipient", "draft"},
    "files": {"file", "document", "agreement", "policy", "receipt"},
    "calendar": {"calendar", "event", "meeting", "schedule", "time"},
    "crm": {"crm", "contact", "organization", "customer", "relationship"},
    "tasks": {"task", "project", "owner", "due", "work"},
}
_ALIASES = {
    "find": "search",
    "locate": "search",
    "look": "search",
    "read": "get",
    "open": "get",
    "document": "file",
    "message": "email",
    "mail": "email",
}
_CONNECTORS = tuple(_CONNECTOR_TERMS)

_SEARCH_QUERIES = {
    "email": ("billing notice", "renewal notice", "client approval"),
    "files": ("signed agreement", "expense receipt", "security addendum"),
    "calendar": ("client kickoff", "quarterly review", "renewal meeting"),
    "accounting": ("overdue invoice", "approved invoice", "Northstar balance"),
    "crm": ("Northstar Labs", "Example Client", "renewal owner"),
    "tasks": ("overdue invoice", "client onboarding", "renewal review"),
}
_GET_IDS = {
    "email": ("mail-public-01", "mail-public-19", "mail-public-37"),
    "files": ("file-public-02", "file-public-20", "file-public-38"),
    "calendar": ("cal-public-03", "cal-public-21", "cal-public-39"),
    "accounting": ("acct-public-04", "acct-public-22", "acct-public-40"),
    "crm": ("crm-public-05", "crm-public-23", "crm-public-41"),
    "tasks": ("task-public-06", "task-public-24", "task-public-42"),
}


def _discovery_arguments(tool: ToolSpec, variant: int) -> dict[str, str]:
    if tool.name.startswith("search_"):
        return {"query": _SEARCH_QUERIES[tool.connector][variant]}
    if tool.name.startswith("get_"):
        return {"record_id": _GET_IDS[tool.connector][variant]}
    suffix = variant + 1
    if tool.name == "create_email_draft":
        return {
            "recipient": f"owner{suffix}@example.test",
            "subject": f"Evidence review {suffix}",
            "body": f"Please review evidence set {suffix}.",
            "idempotency_key": f"discovery-email-{variant}",
        }
    if tool.name == "create_file":
        return {
            "title": f"Evidence note {suffix}",
            "text": f"Verified evidence set {suffix}.",
            "idempotency_key": f"discovery-file-{variant}",
        }
    if tool.name == "create_calendar_event":
        return {
            "title": f"Evidence review {suffix}",
            "start": f"2026-10-0{suffix}T10:00:00Z",
            "end": f"2026-10-0{suffix}T10:30:00Z",
            "idempotency_key": f"discovery-calendar-{variant}",
        }
    if tool.name == "update_invoice_status":
        return {
            "record_id": _GET_IDS["accounting"][variant],
            "status": ("reviewed", "approved", "escalated")[variant],
            "idempotency_key": f"discovery-invoice-{variant}",
        }
    if tool.name == "update_crm_status":
        return {
            "record_id": _GET_IDS["crm"][variant],
            "status": ("reviewed", "active", "renewal_due")[variant],
            "idempotency_key": f"discovery-crm-{variant}",
        }
    if tool.name == "create_task":
        return {
            "title": f"Review evidence {suffix}",
            "owner": f"owner{suffix}@example.test",
            "due_date": f"2026-10-0{suffix}",
            "idempotency_key": f"discovery-task-{variant}",
        }
    raise AssertionError(f"unsupported discovery prototype: {tool.name}")


def _discovery_request(tool: ToolSpec, arguments: Mapping[str, str], variant: int) -> str:
    if tool.name.startswith("search_"):
        instruction = (
            f"Search current {tool.connector} records using the exact query "
            f"{arguments['query']}."
        )
    elif tool.name.startswith("get_"):
        instruction = (
            f"Read the current {tool.connector} record with exact source identifier "
            f"{arguments['record_id']}."
        )
    elif tool.name == "create_email_draft":
        instruction = (
            "Create but do not send an email draft with "
            f"recipient={arguments['recipient']}, subject={arguments['subject']}, "
            f"body={arguments['body']}, idempotency_key={arguments['idempotency_key']}."
        )
    elif tool.name == "create_file":
        instruction = (
            f"Create a document with title={arguments['title']}, text={arguments['text']}, "
            f"idempotency_key={arguments['idempotency_key']}."
        )
    elif tool.name == "create_calendar_event":
        instruction = (
            f"Create a calendar event with title={arguments['title']}, start={arguments['start']}, "
            f"end={arguments['end']}, idempotency_key={arguments['idempotency_key']}."
        )
    elif tool.name == "update_invoice_status":
        instruction = (
            f"Update invoice record_id={arguments['record_id']} to status={arguments['status']} with "
            f"idempotency_key={arguments['idempotency_key']}; do not issue payment."
        )
    elif tool.name == "update_crm_status":
        instruction = (
            f"Update CRM record_id={arguments['record_id']} to status={arguments['status']} with "
            f"idempotency_key={arguments['idempotency_key']}."
        )
    elif tool.name == "create_task":
        instruction = (
            f"Create a project task with title={arguments['title']}, owner={arguments['owner']}, "
            f"due_date={arguments['due_date']}, idempotency_key={arguments['idempotency_key']}."
        )
    else:
        raise AssertionError(f"unsupported discovery request: {tool.name}")
    openings = ("Complete this operation.", "Execute this request.", "Perform this operation.")
    return (
        f"{openings[variant]} {instruction} Call exactly one listed function with the requested arguments; "
        "do not answer in prose."
    )


def discovery_cases(split: Split | str = Split.DEVELOPMENT) -> tuple[DiscoveryCase, ...]:
    """Return disjoint development or held-out main one-decision cases."""

    normalized = Split(split)
    if normalized is Split.TRANSFER:
        raise ValueError("discovery supports development and main splits only")
    variants = (0,) if normalized is Split.DEVELOPMENT else (1, 2)
    cases: list[DiscoveryCase] = []
    for tool in _BASE_TOOLS:
        for variant in variants:
            arguments = _discovery_arguments(tool, variant)
            request = _discovery_request(tool, arguments, variant)
            identity = {"split": normalized.value, "tool": tool.name, "variant": variant}
            cases.append(
                DiscoveryCase(
                    case_id=f"discovery-{stable_hash(identity)[:12]}",
                    split=normalized,
                    seed=(700 if normalized is Split.DEVELOPMENT else 1700) + len(cases),
                    request=request,
                    required_tool=tool.name,
                    expected_arguments=tuple(arguments.items()),
                    connector=tool.connector,
                )
            )
    return tuple(cases)


def _distractor(index: int, seed: int) -> ToolSpec:
    anchor = _BASE_TOOLS[index % len(_BASE_TOOLS)]
    kind = ("random", "same_domain", "adversarial")[(index // len(_BASE_TOOLS)) % 3]
    suffix = stable_hash({"seed": seed, "index": index})[:12]
    if kind == "random":
        description = "Inspect connector service-health metadata; this function does not read business records."
        name = f"inspect_service_health_{suffix}"
        capability = f"service_health_{index}"
    elif kind == "same_domain":
        description = (
            f"Search reusable {anchor.connector} templates by query; templates are not case evidence."
        )
        name = f"search_{anchor.connector}_templates_{suffix}"
        capability = f"template_search_{anchor.connector}_{index}"
    else:
        adversarial_index = (index // len(_BASE_TOOLS)) // 3
        anchor_index = index % len(_BASE_TOOLS)
        hard_limit = 14 if anchor_index % 3 == 0 else 18
        if adversarial_index < hard_limit:
            description = (
                f"Search a derived summary related to {anchor.description.rstrip('.').casefold()}; "
                "summaries aid navigation but are not authoritative records."
            )
            name = f"{anchor.name}_summary_{suffix}"
            capability = f"auxiliary_{anchor.capability}_{index}"
        else:
            description = (
                f"Inspect a derived {anchor.connector} summary cache; cached summaries are not authoritative records."
            )
            name = f"inspect_{anchor.connector}_summary_cache_{suffix}"
            capability = f"summary_cache_{anchor.connector}_{index}"
    connector = anchor.connector
    if kind == "adversarial":
        anchor_index = _CONNECTORS.index(anchor.connector)
        connector = _CONNECTORS[(anchor_index + 3) % len(_CONNECTORS)]
    return ToolSpec(
        name=name,
        connector=connector,
        capability=capability,
        description=description,
        input_schema=deepcopy(anchor.input_schema),
        side_effect=False,
        distractor_kind=kind,
        executable=False,
    )


def build_registry(size: int, seed: int = 20260805) -> tuple[ToolSpec, ...]:
    """Build a nested master registry with unchanged operational schemas."""

    if size not in {32, 128, 512, 2048}:
        raise ValueError("registry size must be 32, 128, 512, or 2048")
    registry = _BASE_TOOLS + tuple(
        _distractor(index, seed) for index in range(size - len(_BASE_TOOLS))
    )
    if len({tool.name for tool in registry}) != size:
        raise AssertionError("discovery registry contains duplicate names")
    return registry


def _tokens(text: str) -> set[str]:
    raw = re.findall(r"[a-z0-9]+", text.casefold())
    normalized = set()
    for token in raw:
        singular = token[:-1] if token.endswith("s") and len(token) > 3 else token
        normalized.add(_ALIASES.get(singular, singular))
    return normalized


def _score(request: str, tool: ToolSpec) -> float:
    query = _tokens(request)
    name = _tokens(tool.name.replace("_", " "))
    capability = _tokens(tool.capability.replace("_", " "))
    description = _tokens(tool.description)
    return (
        4.0 * len(query & name)
        + 2.0 * len(query & capability)
        + 1.0 * len(query & description)
    )


def _rank(request: str, tools: Sequence[ToolSpec]) -> list[ToolSpec]:
    return sorted(
        tools,
        key=lambda tool: (
            -_score(request, tool),
            stable_hash({"request": request, "tool": tool.name}),
        ),
    )


def retrieve(
    case: DiscoveryCase,
    registry: Sequence[ToolSpec],
    architecture: Retriever | str,
    limit: int = 16,
) -> DiscoveryExposure:
    """Freeze either flat tool ranking or connector-first routing."""

    if limit <= 0 or limit > len(registry):
        raise ValueError("invalid retrieval limit")
    retriever = Retriever(architecture)
    selected_connectors: tuple[str, ...] = ()
    candidates = list(registry)
    if retriever is Retriever.HIERARCHICAL:
        query_tokens = _tokens(case.request)
        connector_scores = {
            connector: len(query_tokens & terms)
            for connector, terms in _CONNECTOR_TERMS.items()
        }
        connector_limit = 1 if max(connector_scores.values(), default=0) > 0 else 2
        selected_connectors = tuple(
            sorted(
                _CONNECTOR_TERMS,
                key=lambda connector: (
                    -connector_scores[connector],
                    stable_hash({"case": case.case_id, "connector": connector}),
                ),
            )[:connector_limit]
        )
        eligible = [tool for tool in candidates if tool.connector in selected_connectors]
        if len(eligible) < limit:
            eligible.extend(
                _rank(
                    case.request,
                    [tool for tool in candidates if tool.connector not in selected_connectors],
                )[: limit - len(eligible)]
            )
        candidates = eligible
    selected = tuple(_rank(case.request, candidates)[:limit])
    return DiscoveryExposure(selected, case.required_tool, selected_connectors)


def availability_profile(split: Split | str = Split.DEVELOPMENT) -> JSON:
    """Return deterministic pre-model required-tool availability by R and retriever."""

    cases = discovery_cases(split)
    profile: JSON = {}
    for architecture in Retriever:
        profile[architecture.value] = {}
        for size in (32, 128, 512, 2048):
            registry = build_registry(size)
            available = sum(
                retrieve(case, registry, architecture).required_available for case in cases
            )
            profile[architecture.value][str(size)] = {
                "available": available,
                "total": len(cases),
                "rate": available / len(cases),
            }
    return profile


def _system_prompt(harness: str) -> str:
    prompt = (
        "Select exactly one listed current operational function. Derived summaries, templates, and "
        "service diagnostics are not authoritative business-record operations. "
        "Return the native function call only."
    )
    if harness.startswith("deliberate-verify-v1@"):
        prompt += (
            " Before emitting the one allowed call, internally verify the requested connector, "
            "operation, and exact arguments."
        )
    return prompt


def run_discovery_episode(
    condition: DiscoveryCondition,
    case: DiscoveryCase,
    client: CompletionClient,
) -> DiscoveryEpisode:
    """Evaluate one one-shot tool-selection decision under intention to treat."""

    if (
        condition.case_id != case.case_id
        or condition.split is not case.split
        or condition.seed != case.seed
    ):
        raise ValueError("discovery condition does not match case")
    exposure = retrieve(
        case,
        build_registry(condition.registry_size),
        condition.retriever,
        condition.visible_tools,
    )
    if not exposure.required_available:
        return DiscoveryEpisode(
            condition=condition,
            status=RunStatus.CAPABILITY_UNAVAILABLE,
            exposure=exposure,
            selected_tool=None,
            selected_arguments={},
            success=False,
            prompt_tokens=0,
            completion_tokens=0,
            response_id="",
            errors=("required_tool_unavailable",),
        )
    request = ModelRequest(
        model=condition.model,
        messages=(
            {
                "role": "system",
                "content": _system_prompt(condition.harness),
            },
            {"role": "user", "content": case.request},
        ),
        tools=tuple(tool.to_openai() for tool in exposure.tools),
        temperature=0.0,
        max_tokens=1024,
        seed=condition.seed,
    )
    try:
        with completion_request_scope(
            client, condition_id=condition.condition_id, attempt=1
        ):
            response = client.complete(request)
    except AgnesError as exc:
        return DiscoveryEpisode(
            condition,
            RunStatus.PROVIDER_FAILURE,
            exposure,
            None,
            {},
            False,
            0,
            0,
            "",
            (f"provider_failure:{exc}",),
        )
    errors = [
        f"model_response_contract:{error}" for error in response.response_errors
    ]
    if response.response_errors:
        selected_tool = None
        arguments = {}
    elif len(response.tool_calls) != 1:
        errors.append("one_tool_call_required")
        selected_tool = None
        arguments: JSON = {}
    else:
        call = response.tool_calls[0]
        selected_tool = call.name
        arguments = deepcopy(call.arguments)
        if selected_tool != case.required_tool:
            errors.append("wrong_tool_selected")
        if arguments != case.argument_map:
            errors.append("tool_arguments_invalid")
    success = not errors
    return DiscoveryEpisode(
        condition=condition,
        status=RunStatus.VERIFIED_SUCCESS if success else RunStatus.AGENT_FAILURE,
        exposure=exposure,
        selected_tool=selected_tool,
        selected_arguments=arguments,
        success=success,
        prompt_tokens=response.prompt_tokens,
        completion_tokens=response.completion_tokens,
        response_id=response.raw_id,
        errors=tuple(errors),
        response_model=response.model,
        usage_source=response.usage_source,
    )


def discovery_construct_audit(
    split: Split | str = Split.DEVELOPMENT,
) -> JSON:
    normalized = Split(split)
    if normalized is Split.TRANSFER:
        raise ValueError("discovery supports development and main splits only")
    cases = discovery_cases(normalized)
    registries = {size: build_registry(size) for size in (32, 128, 512, 2048)}
    nested = all(
        [tool.name for tool in registries[small]]
        == [tool.name for tool in registries[large][:small]]
        for small, large in ((32, 128), (128, 512), (512, 2048))
    )
    prototype_unique = (
        len({case.request for case in cases}) == len(cases)
        and len({frozenset(_tokens(case.request)) for case in cases}) == len(cases)
    )
    tool_counts = {
        tool.name: sum(case.required_tool == tool.name for case in cases)
        for tool in _BASE_TOOLS
    }
    tool_support = len(set(tool_counts.values())) == 1 and min(tool_counts.values()) > 0
    base_unchanged = all(
        tuple(registry[: len(_BASE_TOOLS)]) == _BASE_TOOLS
        for registry in registries.values()
    )
    profile = availability_profile(normalized)
    rates = [
        profile[architecture.value][str(size)]["rate"]
        for architecture in Retriever
        for size in (32, 128, 512, 2048)
    ]
    active_offline_range = min(rates) < max(rates)
    rate_sequences = {
        architecture: [
            profile[architecture.value][str(size)]["rate"]
            for size in (32, 128, 512, 2048)
        ]
        for architecture in Retriever
    }
    monotone = all(
        all(left >= right for left, right in zip(values, values[1:]))
        for values in rate_sequences.values()
    )
    terminal_rates = [
        profile[architecture.value]["2048"]["rate"] for architecture in Retriever
    ]
    terminal_active = all(0.0 < rate < 1.0 for rate in terminal_rates)
    retriever_interaction = abs(terminal_rates[0] - terminal_rates[1]) >= 0.20
    other_split = Split.MAIN if normalized is Split.DEVELOPMENT else Split.DEVELOPMENT
    split_disjoint = not (
        {case.case_id for case in cases}
        & {case.case_id for case in discovery_cases(other_split)}
    )
    direct_prompt = _system_prompt("react-v1@vertex-openai")
    deliberate_prompt = _system_prompt("deliberate-verify-v1@vertex-openai")
    harness_policy_distinct = (
        direct_prompt != deliberate_prompt
        and "internally verify" in deliberate_prompt
    )
    checks = (
        {"name": "case_uniqueness", "passed": prototype_unique, "details": {"cases": len(cases)}},
        {"name": "tool_support", "passed": tool_support, "details": {"counts": tool_counts}},
        {"name": "nested_registry", "passed": nested, "details": {"levels": [32, 128, 512, 2048]}},
        {"name": "operational_schema_invariance", "passed": base_unchanged, "details": {"tools": len(_BASE_TOOLS)}},
        {"name": "offline_active_range", "passed": active_offline_range, "details": profile},
        {"name": "offline_monotonicity", "passed": monotone, "details": profile},
        {"name": "terminal_non_degeneracy", "passed": terminal_active, "details": {"rates": terminal_rates}},
        {"name": "retriever_interaction", "passed": retriever_interaction, "details": {"rates": terminal_rates}},
        {"name": "split_isolation", "passed": split_disjoint, "details": {"other_split": other_split.value}},
        {
            "name": "harness_policy_distinction",
            "passed": harness_policy_distinct,
            "details": {
                "direct_hash": stable_hash(direct_prompt),
                "deliberate_hash": stable_hash(deliberate_prompt),
            },
        },
    )
    return {
        "audit": "capacity-surface-discovery-construct-v3",
        "split": normalized.value,
        "passed": all(check["passed"] for check in checks),
        "checks": list(checks),
        "case_manifest_hash": stable_hash([case.case_hash for case in cases]),
    }
