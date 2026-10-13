"""Deterministic event-sourced connector world used by ConnectorScale."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Mapping

from bottleneckbench.core.schema import JSON, canonical_json, stable_hash


class ToolExecutionError(RuntimeError):
    """Base error for deterministic connector execution."""


class ToolNotFound(ToolExecutionError):
    """Raised when an unknown tool is requested."""


class InvalidArguments(ToolExecutionError):
    """Raised when arguments do not satisfy the closed tool schema."""


class PermissionDenied(ToolExecutionError):
    """Raised when a connector permission is unavailable."""


class RecordNotFound(ToolExecutionError):
    """Raised when a record identifier is absent."""


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Public function schema and semantic capability."""

    name: str
    connector: str
    capability: str
    description: str
    input_schema: JSON
    side_effect: bool = False
    distractor_kind: str = "core"
    executable: bool = True

    def to_openai(self) -> JSON:
        """Return an OpenAI-compatible function-tool object."""

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": deepcopy(self.input_schema),
            },
        }


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Deterministic result returned by one connector call."""

    ok: bool
    tool: str
    output: JSON
    source_ids: tuple[str, ...] = ()
    mutated: bool = False
    receipt_id: str | None = None


@dataclass(frozen=True, slots=True)
class WorldEvent:
    """One committed state mutation."""

    sequence: int
    tool: str
    arguments_hash: str
    changed_paths: tuple[str, ...]
    receipt_id: str


@dataclass(frozen=True, slots=True)
class DiffEntry:
    """One exact structural difference."""

    path: str
    operation: str
    before: object
    after: object


@dataclass(slots=True)
class World:
    """Mutable run-local world restored from an immutable seed."""

    seed: int
    records: dict[str, dict[str, JSON]]
    permissions: set[str]
    events: list[WorldEvent] = field(default_factory=list)
    idempotency: dict[tuple[str, str], ToolResult] = field(default_factory=dict)

    def snapshot(self) -> JSON:
        """Return the state that determines benchmark outcomes."""

        return {
            "seed": self.seed,
            "records": deepcopy(self.records),
            "permissions": sorted(self.permissions),
        }

    @property
    def snapshot_hash(self) -> str:
        """Return the content hash of the current state."""

        return stable_hash(self.snapshot())

    def clone(self) -> World:
        """Create an isolated run state with no shared mutable objects."""

        return deepcopy(self)


def _record(record_id: str, title: str, **fields: object) -> JSON:
    return {"record_id": record_id, "version": 1, "title": title, **fields}


def make_world(seed: int) -> World:
    """Construct a deterministic synthetic world spanning four connectors."""

    invoice_id = f"INV-{seed:03d}"
    amount = 1000 + seed
    records = {
        "email": {
            f"email-receipt-{seed}": _record(
                f"email-receipt-{seed}",
                f"Receipt for {invoice_id}",
                sender="billing@northstar.example",
                recipient="owner@example.test",
                body=f"Receipt confirms {invoice_id} for USD {amount}.",
                invoice_id=invoice_id,
                vendor="Northstar Labs",
                kind="receipt",
            ),
            f"email-commitment-{seed}": _record(
                f"email-commitment-{seed}",
                f"Payment commitment for {invoice_id}",
                sender="finance@northstar.example",
                recipient="owner@example.test",
                body=f"We will resolve {invoice_id} by 2026-09-{(seed % 20) + 1:02d}.",
                invoice_id=invoice_id,
                vendor="Northstar Labs",
                kind="commitment",
            ),
            f"email-client-{seed}": _record(
                f"email-client-{seed}",
                "Client onboarding notes",
                sender="client@example.test",
                recipient="owner@example.test",
                body="Kickoff requires the signed agreement and calendar slot.",
                kind="client",
            ),
            f"email-noise-{seed}": _record(
                f"email-noise-{seed}",
                "Weekly newsletter",
                sender="news@example.test",
                recipient="owner@example.test",
                body="General product updates.",
                kind="noise",
            ),
        },
        "files": {
            f"file-policy-{seed}": _record(
                f"file-policy-{seed}",
                "Accounts payable policy",
                text="Overdue invoices require evidence and a draft reminder; never auto-send.",
                kind="policy",
            ),
            f"file-receipt-{seed}": _record(
                f"file-receipt-{seed}",
                f"Scanned receipt {invoice_id}",
                text=f"Northstar Labs {invoice_id} USD {amount}",
                invoice_id=invoice_id,
                vendor="Northstar Labs",
                amount=amount,
                kind="receipt",
            ),
            f"file-agreement-{seed}": _record(
                f"file-agreement-{seed}",
                "Signed client agreement",
                text="Agreement signed; kickoff may be scheduled.",
                kind="agreement",
            ),
            f"file-noise-{seed}": _record(
                f"file-noise-{seed}",
                "Office handbook",
                text="General office information.",
                kind="noise",
            ),
        },
        "calendar": {
            f"cal-promise-{seed}": _record(
                f"cal-promise-{seed}",
                f"Northstar payment promise for {invoice_id}",
                start=f"2026-09-{(seed % 20) + 1:02d}T10:00:00Z",
                end=f"2026-09-{(seed % 20) + 1:02d}T10:30:00Z",
                invoice_id=invoice_id,
                vendor="Northstar Labs",
                kind="promise",
            ),
            f"cal-kickoff-{seed}": _record(
                f"cal-kickoff-{seed}",
                "Client kickoff",
                start="2026-09-15T14:00:00Z",
                end="2026-09-15T15:00:00Z",
                kind="kickoff",
            ),
            f"cal-focus-{seed}": _record(
                f"cal-focus-{seed}",
                "Focus time",
                start="2026-09-15T13:00:00Z",
                end="2026-09-15T14:00:00Z",
                kind="focus",
            ),
            f"cal-noise-{seed}": _record(
                f"cal-noise-{seed}",
                "Team social",
                start="2026-09-19T17:00:00Z",
                end="2026-09-19T18:00:00Z",
                kind="noise",
            ),
        },
        "accounting": {
            f"invoice-target-{seed}": _record(
                f"invoice-target-{seed}",
                f"Northstar Labs {invoice_id}",
                invoice_id=invoice_id,
                vendor="Northstar Labs",
                contact="finance@northstar.example",
                amount=amount,
                currency="USD",
                due_date="2026-08-01",
                status="overdue",
                kind="invoice",
            ),
            f"invoice-paid-{seed}": _record(
                f"invoice-paid-{seed}",
                f"Northstar Labs PAID-{seed:03d}",
                invoice_id=f"PAID-{seed:03d}",
                vendor="Northstar Labs",
                contact="finance@northstar.example",
                amount=amount + 200,
                currency="USD",
                due_date="2026-07-01",
                status="paid",
                kind="invoice",
            ),
            f"invoice-other-{seed}": _record(
                f"invoice-other-{seed}",
                f"Contoso INV-X{seed:03d}",
                invoice_id=f"INV-X{seed:03d}",
                vendor="Contoso",
                contact="billing@contoso.example",
                amount=500 + seed,
                currency="USD",
                due_date="2026-10-01",
                status="open",
                kind="invoice",
            ),
            f"account-noise-{seed}": _record(
                f"account-noise-{seed}",
                "Quarterly budget note",
                text="Forecast only.",
                kind="note",
            ),
        },
    }
    permissions = {
        "email.read",
        "email.write",
        "files.read",
        "files.write",
        "calendar.read",
        "calendar.write",
        "accounting.read",
        "accounting.write",
    }
    return World(seed=seed, records=records, permissions=permissions)


def make_transfer_world(seed: int) -> World:
    """Extend the controlled core with exploratory CRM and Tasks connectors."""

    world = make_world(seed)
    invoice_id = f"INV-{seed:03d}"
    world.records["crm"] = {
        f"crm-northstar-{seed}": _record(
            f"crm-northstar-{seed}",
            "Northstar Labs finance contact",
            organization="Northstar Labs",
            name="Finance Team",
            email="finance@northstar.example",
            invoice_id=invoice_id,
            status="active",
            kind="contact",
        ),
        f"crm-client-{seed}": _record(
            f"crm-client-{seed}",
            "Client onboarding contact",
            organization="Example Client",
            name="Client Owner",
            email="client@example.test",
            status="onboarding",
            kind="contact",
        ),
        f"crm-noise-{seed}": _record(
            f"crm-noise-{seed}",
            "Archived contact",
            organization="Archived Co",
            email="archive@example.test",
            status="archived",
            kind="noise",
        ),
    }
    world.records["tasks"] = {
        f"task-invoice-{seed}": _record(
            f"task-invoice-{seed}",
            f"Review overdue invoice {invoice_id}",
            invoice_id=invoice_id,
            owner="finance@northstar.example",
            due_date="2026-09-20",
            status="blocked",
            kind="task",
        ),
        f"task-onboarding-{seed}": _record(
            f"task-onboarding-{seed}",
            "Complete client onboarding",
            owner="client@example.test",
            due_date="2026-09-30",
            status="open",
            kind="task",
        ),
        f"task-noise-{seed}": _record(
            f"task-noise-{seed}",
            "Archive old notes",
            owner="owner@example.test",
            due_date="2026-12-01",
            status="open",
            kind="noise",
        ),
    }
    world.permissions.update({"crm.read", "crm.write", "tasks.read", "tasks.write"})
    return world


def _object_schema(properties: JSON, required: tuple[str, ...]) -> JSON:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _string(description: str) -> JSON:
    return {"type": "string", "description": description}


def core_tools() -> tuple[ToolSpec, ...]:
    """Return the 12 executable controlled-core tools."""

    tools: list[ToolSpec] = []
    connector_names = {
        "email": "email records",
        "files": "documents and files",
        "calendar": "calendar events",
        "accounting": "invoices and accounting records",
    }
    for connector, noun in connector_names.items():
        tools.append(
            ToolSpec(
                name=f"search_{'invoices' if connector == 'accounting' else connector}",
                connector=connector,
                capability=f"search_{connector}",
                description=f"Search {noun} using query terms.",
                input_schema=_object_schema({"query": _string("Query terms")}, ("query",)),
            )
        )
        tools.append(
            ToolSpec(
                name=f"get_{'invoice' if connector == 'accounting' else connector.rstrip('s')}",
                connector=connector,
                capability=f"get_{connector}",
                description=f"Read one {noun} record by exact record ID.",
                input_schema=_object_schema({"record_id": _string("Exact source record ID")}, ("record_id",)),
            )
        )

    tools.extend(
        [
            ToolSpec(
                name="create_email_draft",
                connector="email",
                capability="create_email_draft",
                description="Create an email draft without sending it.",
                input_schema=_object_schema(
                    {
                        "recipient": _string("Recipient address"),
                        "subject": _string("Draft subject"),
                        "body": _string("Draft body"),
                        "idempotency_key": _string("Stable unique write key"),
                    },
                    ("recipient", "subject", "body", "idempotency_key"),
                ),
                side_effect=True,
            ),
            ToolSpec(
                name="create_file",
                connector="files",
                capability="create_file",
                description="Create a new document record.",
                input_schema=_object_schema(
                    {
                        "title": _string("Document title"),
                        "text": _string("Document text"),
                        "idempotency_key": _string("Stable unique write key"),
                    },
                    ("title", "text", "idempotency_key"),
                ),
                side_effect=True,
            ),
            ToolSpec(
                name="create_calendar_event",
                connector="calendar",
                capability="create_calendar_event",
                description="Create a calendar event.",
                input_schema=_object_schema(
                    {
                        "title": _string("Event title"),
                        "start": _string("ISO-8601 start time"),
                        "end": _string("ISO-8601 end time"),
                        "idempotency_key": _string("Stable unique write key"),
                    },
                    ("title", "start", "end", "idempotency_key"),
                ),
                side_effect=True,
            ),
            ToolSpec(
                name="update_invoice_status",
                connector="accounting",
                capability="update_invoice_status",
                description="Update an invoice workflow status without issuing payment.",
                input_schema=_object_schema(
                    {
                        "record_id": _string("Invoice source record ID"),
                        "status": _string("New workflow status"),
                        "idempotency_key": _string("Stable unique write key"),
                    },
                    ("record_id", "status", "idempotency_key"),
                ),
                side_effect=True,
            ),
        ]
    )
    return tuple(tools)


def transfer_tools() -> tuple[ToolSpec, ...]:
    """Return six exploratory CRM and Tasks tools."""

    return (
        ToolSpec(
            name="search_crm",
            connector="crm",
            capability="search_crm",
            description="Search current CRM contacts and organizations.",
            input_schema=_object_schema({"query": _string("Query terms")}, ("query",)),
        ),
        ToolSpec(
            name="get_crm",
            connector="crm",
            capability="get_crm",
            description="Read one CRM record by exact source ID.",
            input_schema=_object_schema({"record_id": _string("CRM record ID")}, ("record_id",)),
        ),
        ToolSpec(
            name="update_crm_status",
            connector="crm",
            capability="update_crm_status",
            description="Update a CRM workflow status.",
            input_schema=_object_schema(
                {
                    "record_id": _string("CRM record ID"),
                    "status": _string("New workflow status"),
                    "idempotency_key": _string("Stable unique write key"),
                },
                ("record_id", "status", "idempotency_key"),
            ),
            side_effect=True,
        ),
        ToolSpec(
            name="search_tasks",
            connector="tasks",
            capability="search_tasks",
            description="Search current project tasks.",
            input_schema=_object_schema({"query": _string("Query terms")}, ("query",)),
        ),
        ToolSpec(
            name="get_task",
            connector="tasks",
            capability="get_tasks",
            description="Read one task by exact source ID.",
            input_schema=_object_schema({"record_id": _string("Task record ID")}, ("record_id",)),
        ),
        ToolSpec(
            name="create_task",
            connector="tasks",
            capability="create_task",
            description="Create a reversible project task.",
            input_schema=_object_schema(
                {
                    "title": _string("Task title"),
                    "owner": _string("Task owner"),
                    "due_date": _string("ISO date"),
                    "idempotency_key": _string("Stable unique write key"),
                },
                ("title", "owner", "due_date", "idempotency_key"),
            ),
            side_effect=True,
        ),
    )


_TOOL_MAP = {tool.name: tool for tool in core_tools() + transfer_tools()}
_SEARCH_CONNECTORS = {
    "search_email": "email",
    "search_files": "files",
    "search_calendar": "calendar",
    "search_invoices": "accounting",
    "search_crm": "crm",
    "search_tasks": "tasks",
}
_GET_CONNECTORS = {
    "get_email": "email",
    "get_file": "files",
    "get_calendar": "calendar",
    "get_invoice": "accounting",
    "get_crm": "crm",
    "get_task": "tasks",
}


def _validate_arguments(tool: ToolSpec, arguments: Mapping[str, object]) -> None:
    schema = tool.input_schema
    required = set(schema["required"])
    properties = set(schema["properties"])
    keys = set(arguments)
    missing = required - keys
    extras = keys - properties
    if missing:
        raise InvalidArguments(f"{tool.name} missing required arguments: {sorted(missing)}")
    if extras:
        raise InvalidArguments(f"{tool.name} received extra arguments: {sorted(extras)}")
    for key in keys:
        if not isinstance(arguments[key], str):
            raise InvalidArguments(f"{tool.name}.{key} must be a string")


def _require_permission(world: World, connector: str, write: bool) -> None:
    permission = f"{connector}.{'write' if write else 'read'}"
    if permission not in world.permissions:
        raise PermissionDenied(f"missing permission {permission}")


def _search(world: World, connector: str, query: str) -> tuple[JSON, ...]:
    terms = [term for term in query.lower().split() if term]
    matches: list[JSON] = []
    for record in world.records[connector].values():
        text = canonical_json(record).lower()
        if all(term in text for term in terms):
            matches.append(deepcopy(record))
    matches.sort(key=lambda record: str(record["record_id"]))
    return tuple(matches)


def _read(world: World, connector: str, record_id: str) -> JSON:
    try:
        return deepcopy(world.records[connector][record_id])
    except KeyError as exc:
        raise RecordNotFound(f"{connector} record not found: {record_id}") from exc


def _write_id(arguments: Mapping[str, object]) -> str:
    return str(arguments["idempotency_key"])


def _commit(
    world: World,
    tool: ToolSpec,
    arguments: Mapping[str, object],
    before: JSON,
    output: JSON,
) -> ToolResult:
    key = _write_id(arguments)
    scoped_key = (tool.name, key)
    receipt_id = f"receipt-{stable_hash({'tool': tool.name, 'key': key})[:16]}"
    changed_paths = tuple(entry.path for entry in deep_diff(before, world.snapshot()))
    event = WorldEvent(
        sequence=len(world.events) + 1,
        tool=tool.name,
        arguments_hash=stable_hash(dict(arguments)),
        changed_paths=changed_paths,
        receipt_id=receipt_id,
    )
    world.events.append(event)
    result = ToolResult(
        ok=True,
        tool=tool.name,
        output=deepcopy(output),
        source_ids=tuple(
            str(record_id)
            for record_id in output.get("source_ids", [])
            if isinstance(record_id, str)
        ),
        mutated=True,
        receipt_id=receipt_id,
    )
    world.idempotency[scoped_key] = result
    return result


def execute_tool(world: World, tool_name: str, arguments: Mapping[str, object]) -> ToolResult:
    """Execute a closed-schema core tool against a run-local world."""

    try:
        tool = _TOOL_MAP[tool_name]
    except KeyError as exc:
        raise ToolNotFound(f"unknown tool: {tool_name}") from exc
    _validate_arguments(tool, arguments)
    _require_permission(world, tool.connector, tool.side_effect)

    if tool.side_effect:
        key = (tool.name, _write_id(arguments))
        if key in world.idempotency:
            previous = world.idempotency[key]
            return ToolResult(
                ok=previous.ok,
                tool=previous.tool,
                output=deepcopy(previous.output),
                source_ids=previous.source_ids,
                mutated=False,
                receipt_id=previous.receipt_id,
            )

    if tool_name in _SEARCH_CONNECTORS:
        connector = _SEARCH_CONNECTORS[tool_name]
        records = _search(world, connector, str(arguments["query"]))
        return ToolResult(
            ok=True,
            tool=tool_name,
            output={"records": list(records)},
            source_ids=tuple(str(record["record_id"]) for record in records),
        )

    if tool_name in _GET_CONNECTORS:
        connector = _GET_CONNECTORS[tool_name]
        record = _read(world, connector, str(arguments["record_id"]))
        return ToolResult(
            ok=True,
            tool=tool_name,
            output={"record": record},
            source_ids=(str(record["record_id"]),),
        )

    before = world.snapshot()
    if tool_name == "create_email_draft":
        record_id = f"email-draft-{stable_hash(dict(arguments))[:12]}"
        record = _record(
            record_id,
            str(arguments["subject"]),
            recipient=str(arguments["recipient"]),
            subject=str(arguments["subject"]),
            body=str(arguments["body"]),
            status="draft",
            kind="draft",
        )
        world.records["email"][record_id] = record
        return _commit(world, tool, arguments, before, {"record": deepcopy(record), "source_ids": [record_id]})

    if tool_name == "create_file":
        record_id = f"file-created-{stable_hash(dict(arguments))[:12]}"
        record = _record(
            record_id,
            str(arguments["title"]),
            text=str(arguments["text"]),
            kind="created",
        )
        world.records["files"][record_id] = record
        return _commit(world, tool, arguments, before, {"record": deepcopy(record), "source_ids": [record_id]})

    if tool_name == "create_calendar_event":
        record_id = f"cal-created-{stable_hash(dict(arguments))[:12]}"
        record = _record(
            record_id,
            str(arguments["title"]),
            start=str(arguments["start"]),
            end=str(arguments["end"]),
            kind="created",
        )
        world.records["calendar"][record_id] = record
        return _commit(world, tool, arguments, before, {"record": deepcopy(record), "source_ids": [record_id]})

    if tool_name == "update_invoice_status":
        record_id = str(arguments["record_id"])
        record = world.records["accounting"].get(record_id)
        if record is None:
            raise RecordNotFound(f"accounting record not found: {record_id}")
        record["status"] = str(arguments["status"])
        record["version"] = int(record["version"]) + 1
        return _commit(world, tool, arguments, before, {"record": deepcopy(record), "source_ids": [record_id]})

    if tool_name == "update_crm_status":
        record_id = str(arguments["record_id"])
        record = world.records["crm"].get(record_id)
        if record is None:
            raise RecordNotFound(f"crm record not found: {record_id}")
        record["status"] = str(arguments["status"])
        record["version"] = int(record["version"]) + 1
        return _commit(world, tool, arguments, before, {"record": deepcopy(record), "source_ids": [record_id]})

    if tool_name == "create_task":
        record_id = f"task-created-{stable_hash(dict(arguments))[:12]}"
        record = _record(
            record_id,
            str(arguments["title"]),
            owner=str(arguments["owner"]),
            due_date=str(arguments["due_date"]),
            status="open",
            kind="created",
        )
        world.records["tasks"][record_id] = record
        return _commit(world, tool, arguments, before, {"record": deepcopy(record), "source_ids": [record_id]})

    raise ToolNotFound(f"unimplemented tool: {tool_name}")


def deep_diff(before: object, after: object, path: str = "") -> tuple[DiffEntry, ...]:
    """Return an exact recursive structural diff, including added containers."""

    if before == after:
        return ()
    entries: list[DiffEntry] = []
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        before_keys = set(before)
        after_keys = set(after)
        for key in sorted(before_keys | after_keys, key=str):
            child_path = f"{path}/{key}"
            if key not in before:
                value = deepcopy(after[key])
                entries.append(DiffEntry(child_path, "add", None, value))
                if isinstance(value, Mapping):
                    entries.extend(deep_diff({}, value, child_path))
            elif key not in after:
                entries.append(DiffEntry(child_path, "remove", deepcopy(before[key]), None))
            else:
                entries.extend(deep_diff(before[key], after[key], child_path))
        return tuple(entries)
    if isinstance(before, list) and isinstance(after, list):
        maximum = max(len(before), len(after))
        for index in range(maximum):
            child_path = f"{path}/{index}"
            if index >= len(before):
                entries.append(DiffEntry(child_path, "add", None, deepcopy(after[index])))
            elif index >= len(after):
                entries.append(DiffEntry(child_path, "remove", deepcopy(before[index]), None))
            else:
                entries.extend(deep_diff(before[index], after[index], child_path))
        return tuple(entries)
    return (DiffEntry(path or "/", "replace", deepcopy(before), deepcopy(after)),)
