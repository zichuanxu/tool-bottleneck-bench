"""Nested tool registries and deterministic exposure mechanisms."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Sequence

from bottleneckbench.core.schema import stable_hash
from bottleneckbench.core.tasks import TaskTemplate
from bottleneckbench.core.world import ToolSpec, core_tools, transfer_tools


@dataclass(frozen=True, slots=True)
class Exposure:
    """One exact model-visible tool menu and its availability audit."""

    tools: tuple[ToolSpec, ...]
    selected_connectors: tuple[str, ...]
    required_tools: tuple[str, ...] = ()
    oracle: bool = False

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(tool.name for tool in self.tools)

    @property
    def required_available(self) -> float:
        if not self.required_tools:
            return 1.0
        exposed = set(self.tool_names)
        return sum(name in exposed for name in self.required_tools) / len(self.required_tools)


_CONNECTORS = ("email", "files", "calendar", "accounting")
_CONNECTOR_TERMS = {
    "email": {"email", "mail", "sender", "recipient", "receipt", "message"},
    "files": {"file", "document", "agreement", "policy", "receipt", "text"},
    "calendar": {"calendar", "event", "meeting", "schedule", "kickoff", "time"},
    "accounting": {"accounting", "invoice", "vendor", "amount", "payment", "overdue"},
}
_KINDS = ("random", "same_domain", "adversarial")


def _closed_string_schema(field: str) -> dict[str, object]:
    return {
        "type": "object",
        "properties": {field: {"type": "string"}},
        "required": [field],
        "additionalProperties": False,
    }


def _distractor(index: int, seed: int) -> ToolSpec:
    connector = _CONNECTORS[index % len(_CONNECTORS)]
    kind = _KINDS[(index // len(_CONNECTORS)) % len(_KINDS)]
    suffix = stable_hash({"seed": seed, "index": index})[:10]
    if kind == "random":
        name = f"archive_metric_{suffix}"
        description = "Read a synthetic archival service metric unrelated to business records."
        field = "metric_id"
        side_effect = False
    elif kind == "same_domain":
        name = f"search_{connector}_archive_{suffix}"
        description = f"Search historical {connector} archive metadata, not current operational records."
        field = "query"
        side_effect = False
    else:
        if connector == "email":
            name = f"search_invoice_receipt_email_archive_{suffix}"
            description = "Find receipt email records for an invoice in a stale archive, not current operational email."
            field = "query"
        elif connector == "files":
            name = f"search_signed_invoice_files_archive_{suffix}"
            description = "Find signed invoice files in a stale archive, not current operational documents."
            field = "query"
        elif connector == "calendar":
            name = f"search_invoice_calendar_archive_{suffix}"
            description = "Find invoice calendar events in a stale archive, not the current calendar."
            field = "query"
        else:
            name = f"search_overdue_invoices_archive_{suffix}"
            description = "Find overdue invoice status in an archived accounting snapshot, not current records."
            field = "query"
        side_effect = False
    return ToolSpec(
        name=name,
        connector=connector,
        capability=f"distractor_{kind}_{connector}_{index}",
        description=description,
        input_schema=_closed_string_schema(field),
        side_effect=side_effect,
        distractor_kind=kind,
        executable=False,
    )


def build_registry(size: int, seed: int) -> tuple[ToolSpec, ...]:
    """Build a deterministic nested registry whose prefix is size invariant."""

    controlled = core_tools()
    if size < len(controlled):
        raise ValueError(f"registry size must be at least {len(controlled)}")
    distractors = tuple(
        _distractor(index, seed) for index in range(size - len(controlled))
    )
    registry = controlled + distractors
    names = [tool.name for tool in registry]
    if len(set(names)) != len(names):
        raise AssertionError("registry contains duplicate tool names")
    return registry


def build_transfer_registry(size: int, seed: int) -> tuple[ToolSpec, ...]:
    """Build a nested registry containing core plus CRM/Tasks transfer tools."""

    controlled = core_tools() + transfer_tools()
    if size < len(controlled):
        raise ValueError(f"transfer registry size must be at least {len(controlled)}")
    distractors = tuple(
        _distractor(index, seed) for index in range(size - len(controlled))
    )
    registry = controlled + distractors
    if len({tool.name for tool in registry}) != len(registry):
        raise AssertionError("transfer registry contains duplicate tool names")
    return registry


def _tokens(text: str) -> set[str]:
    raw = re.findall(r"[a-z0-9]+", text.lower())
    normalized: set[str] = set()
    aliases = {
        "find": "search",
        "locate": "search",
        "read": "get",
        "open": "get",
        "document": "file",
        "mail": "email",
    }
    for token in raw:
        singular = token[:-1] if token.endswith("s") and len(token) > 3 else token
        normalized.add(aliases.get(singular, singular))
    return normalized


def _tool_score(query: str, tool: ToolSpec) -> float:
    query_tokens = _tokens(query)
    name_tokens = _tokens(tool.name.replace("_", " "))
    description_tokens = _tokens(tool.description)
    capability_tokens = _tokens(tool.capability.replace("_", " "))
    overlap = 3.0 * len(query_tokens & name_tokens)
    overlap += 1.5 * len(query_tokens & capability_tokens)
    overlap += len(query_tokens & description_tokens)
    if overlap > 0 and tool.distractor_kind == "core":
        overlap += 0.25
    return overlap


def _validate_limit(tools: Sequence[ToolSpec], limit: int) -> None:
    if limit <= 0:
        raise ValueError("exposure limit must be positive")
    if limit > len(tools):
        raise ValueError("exposure limit exceeds registry size")


def _ranked(query: str, tools: Sequence[ToolSpec]) -> list[ToolSpec]:
    scores = {tool.name: _tool_score(query, tool) for tool in tools}
    no_semantic_match = max(scores.values(), default=0.0) == 0.0
    return sorted(
        tools,
        key=lambda tool: (
            -scores[tool.name],
            no_semantic_match and tool.distractor_kind == "core",
            stable_hash({"query": query, "tool": tool.name}),
            tool.name,
        ),
    )


def retrieve_flat(
    query: str, tools: Sequence[ToolSpec], limit: int
) -> Exposure:
    """Select a fixed-size menu directly from the entire registry."""

    _validate_limit(tools, limit)
    selected = tuple(_ranked(query, tools)[:limit])
    connectors = tuple(dict.fromkeys(tool.connector for tool in selected))
    return Exposure(tools=selected, selected_connectors=connectors)


def _connector_score(query: str, connector: str) -> float:
    query_tokens = _tokens(query)
    return float(len(query_tokens & _CONNECTOR_TERMS[connector]))


def retrieve_hierarchical(
    query: str, tools: Sequence[ToolSpec], limit: int
) -> Exposure:
    """Select connectors first, then rank tools inside the selected domains."""

    _validate_limit(tools, limit)
    connector_count = max(1, min(len(_CONNECTORS), math.ceil(limit / 8)))
    if max((_connector_score(query, connector) for connector in _CONNECTORS), default=0.0) == 0.0:
        selected = tuple(_ranked(query, tools)[:limit])
        return Exposure(tools=selected, selected_connectors=())
    connectors = tuple(
        sorted(
            _CONNECTORS,
            key=lambda connector: (
                -_connector_score(query, connector),
                stable_hash({"query": query, "connector": connector}),
            ),
        )[:connector_count]
    )
    eligible = [tool for tool in tools if tool.connector in connectors]
    if len(eligible) < limit:
        remaining = [tool for tool in tools if tool.connector not in connectors]
        eligible.extend(_ranked(query, remaining)[: limit - len(eligible)])
    selected = tuple(_ranked(query, eligible)[:limit])
    return Exposure(tools=selected, selected_connectors=connectors)


def oracle_exposure(
    task: TaskTemplate, tools: Sequence[ToolSpec], limit: int
) -> Exposure:
    """Expose every required tool plus deterministic matched hard negatives."""

    _validate_limit(tools, limit)
    by_name = {tool.name: tool for tool in tools}
    missing = set(task.required_tools) - set(by_name)
    if missing:
        raise ValueError(f"required tools missing from registry: {sorted(missing)}")
    required = [by_name[name] for name in task.required_tools]
    if len(required) > limit:
        raise ValueError("exposure limit is smaller than the required tool set")
    available_names = set(by_name)
    controlled_names = {
        tool.name
        for tool in core_tools() + transfer_tools()
        if tool.name in available_names
    }
    if limit >= len(controlled_names) and controlled_names <= set(by_name):
        controlled = [tool for tool in tools if tool.name in controlled_names]
        neutral = [tool for tool in tools if tool.distractor_kind == "random"]
        neutral.sort(key=lambda tool: stable_hash({"oracle_menu": tool.name}))
        selected = tuple(controlled + neutral[: limit - len(controlled)])
    else:
        required_names = set(task.required_tools)
        fillers = [tool for tool in tools if tool.name not in required_names]
        fillers.sort(
            key=lambda tool: (
                tool.distractor_kind != "random",
                stable_hash({"oracle_menu": tool.name}),
            )
        )
        selected = tuple(required + fillers[: limit - len(required)])
    connectors = tuple(dict.fromkeys(tool.connector for tool in selected))
    return Exposure(
        tools=selected,
        selected_connectors=connectors,
        required_tools=task.required_tools,
        oracle=True,
    )
