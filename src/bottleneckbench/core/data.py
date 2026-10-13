"""Query-independent Data Readiness transformations with provenance."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from bottleneckbench.core.schema import JSON, Representation, stable_hash
from bottleneckbench.core.world import ToolResult, World


_COMPACT_FIELDS = {
    "record_id": "i",
    "version": "v",
    "title": "t",
    "sender": "se",
    "recipient": "r",
    "subject": "su",
    "body": "b",
    "text": "x",
    "invoice_id": "n",
    "vendor": "ve",
    "contact": "co",
    "amount": "a",
    "currency": "cu",
    "due_date": "d",
    "status": "st",
    "start": "s",
    "end": "e",
    "kind": "k",
}
_REVERSE_COMPACT_FIELDS = {short: long for long, short in _COMPACT_FIELDS.items()}
_TYPE_BY_CONNECTOR = {
    "email": "EmailMessage",
    "files": "Document",
    "calendar": "CalendarEvent",
    "accounting": "AccountingRecord",
    "crm": "CRMContact",
    "tasks": "ProjectTask",
}


@dataclass(frozen=True, slots=True)
class CompiledData:
    """One complete query-independent representation of a world snapshot."""

    representation: Representation
    world_hash: str
    objects: tuple[JSON, ...]
    provenance: dict[str, JSON]
    links: tuple[JSON, ...] = ()
    field_dictionary: dict[str, str] = field(default_factory=dict)

    @property
    def compiled_hash(self) -> str:
        return stable_hash(
            {
                "representation": self.representation.value,
                "world_hash": self.world_hash,
                "objects": self.objects,
                "provenance": self.provenance,
                "links": self.links,
                "field_dictionary": self.field_dictionary,
            }
        )


def _object_type(connector: str, record: JSON) -> str:
    if connector == "accounting" and record.get("kind") == "invoice":
        return "Invoice"
    return _TYPE_BY_CONNECTOR[connector]


def _source(connector: str, record: JSON) -> JSON:
    return {
        "connector": connector,
        "record_id": record["record_id"],
        "version": record["version"],
    }


def _provenance_value(connector: str, record: JSON, field_name: str) -> JSON:
    return {
        "connector": connector,
        "record_id": record["record_id"],
        "version": record["version"],
        "field": field_name,
    }


def _raw_object(connector: str, record: JSON) -> JSON:
    return {"connector": connector, "payload": deepcopy(record)}


def _compact_object(connector: str, record: JSON) -> JSON:
    payload = {
        _COMPACT_FIELDS.get(field_name, f"u_{field_name}"): deepcopy(value)
        for field_name, value in record.items()
    }
    return {"c": connector, "p": payload}


def _typed_object(connector: str, record: JSON) -> JSON:
    fields = {
        field_name: deepcopy(value)
        for field_name, value in record.items()
        if field_name not in {"record_id", "version"}
    }
    return {
        "type": _object_type(connector, record),
        "source": _source(connector, record),
        "fields": fields,
    }


def _links(records: list[tuple[str, JSON]]) -> tuple[JSON, ...]:
    grouped: dict[tuple[str, str], list[tuple[str, JSON]]] = {}
    for connector, record in records:
        invoice_id = record.get("invoice_id")
        if isinstance(invoice_id, str):
            grouped.setdefault(("same_invoice", invoice_id), []).append((connector, record))
    links: list[JSON] = []
    for (relation, value), members in sorted(grouped.items()):
        ordered = sorted(members, key=lambda item: str(item[1]["record_id"]))
        for left_index, (_, left) in enumerate(ordered):
            for _, right in ordered[left_index + 1 :]:
                links.append(
                    {
                        "relation": relation,
                        "value": value,
                        "source_record_ids": [left["record_id"], right["record_id"]],
                        "source_versions": [left["version"], right["version"]],
                    }
                )
    return tuple(links)


def compile_data(world: World, representation: Representation) -> CompiledData:
    """Compile a full snapshot without accepting task or query information."""

    source_records = [
        (connector, deepcopy(record))
        for connector in sorted(world.records)
        for _, record in sorted(world.records[connector].items())
    ]
    objects: list[JSON] = []
    provenance: dict[str, JSON] = {}
    for index, (connector, record) in enumerate(source_records):
        if representation is Representation.RAW:
            compiled = _raw_object(connector, record)
            field_prefix = f"/objects/{index}/payload"
            field_names = {field_name: field_name for field_name in record}
        elif representation is Representation.GENERIC_COMPACT:
            compiled = _compact_object(connector, record)
            field_prefix = f"/objects/{index}/p"
            field_names = {
                field_name: _COMPACT_FIELDS.get(field_name, f"u_{field_name}")
                for field_name in record
            }
        else:
            compiled = _typed_object(connector, record)
            field_prefix = f"/objects/{index}"
            field_names = {}
            for field_name in record:
                if field_name == "record_id":
                    field_names[field_name] = "source/record_id"
                elif field_name == "version":
                    field_names[field_name] = "source/version"
                else:
                    field_names[field_name] = f"fields/{field_name}"
        objects.append(compiled)
        for field_name, compiled_name in field_names.items():
            provenance[f"{field_prefix}/{compiled_name}"] = _provenance_value(
                connector, record, field_name
            )
    links = _links(source_records) if representation is Representation.LINKED else ()
    compiled_data = CompiledData(
        representation=representation,
        world_hash=world.snapshot_hash,
        objects=tuple(objects),
        provenance=provenance,
        links=links,
        field_dictionary=(
            dict(_REVERSE_COMPACT_FIELDS)
            if representation is Representation.GENERIC_COMPACT
            else {}
        ),
    )
    validate_compiled_data(world, compiled_data)
    return compiled_data


def _decode_object(representation: Representation, item: JSON) -> tuple[str, JSON]:
    if representation is Representation.RAW:
        return str(item["connector"]), deepcopy(item["payload"])
    if representation is Representation.GENERIC_COMPACT:
        payload: JSON = {}
        for short_name, value in item["p"].items():
            field_name = _REVERSE_COMPACT_FIELDS.get(short_name)
            if field_name is None:
                if not str(short_name).startswith("u_"):
                    raise ValueError(f"unknown compact field: {short_name}")
                field_name = str(short_name)[2:]
            payload[field_name] = deepcopy(value)
        return str(item["c"]), payload
    source = item["source"]
    payload = {
        "record_id": source["record_id"],
        "version": source["version"],
        **deepcopy(item["fields"]),
    }
    return str(source["connector"]), payload


def source_facts(compiled: CompiledData) -> dict[tuple[str, str], JSON]:
    """Reconstruct exact source field mappings from any representation."""

    facts: dict[tuple[str, str], JSON] = {}
    for item in compiled.objects:
        connector, payload = _decode_object(compiled.representation, item)
        record_id = str(payload["record_id"])
        key = (connector, record_id)
        if key in facts:
            raise ValueError(f"duplicate source record: {key}")
        facts[key] = payload
    return facts


def _world_facts(world: World) -> dict[tuple[str, str], JSON]:
    return {
        (connector, record_id): deepcopy(record)
        for connector, records in world.records.items()
        for record_id, record in records.items()
    }


def validate_compiled_data(world: World, compiled: CompiledData) -> None:
    """Fail closed if representation changes any source field-value mapping."""

    if compiled.world_hash != world.snapshot_hash:
        raise ValueError("compiled data world hash does not match current snapshot")
    if source_facts(compiled) != _world_facts(world):
        raise ValueError("compiled data changed a source field-value mapping")
    if provenance_coverage(compiled) != 1.0:
        raise ValueError("compiled data provenance coverage is incomplete")
    valid_ids = {record_id for _, record_id in _world_facts(world)}
    for link in compiled.links:
        source_ids = link.get("source_record_ids")
        if not isinstance(source_ids, list) or len(source_ids) != 2:
            raise ValueError("linked relation lacks exactly two source records")
        if not set(source_ids) <= valid_ids:
            raise ValueError("linked relation cites an unknown source record")


def provenance_coverage(compiled: CompiledData) -> float:
    """Return field-level provenance coverage over all reconstructed source fields."""

    total_fields = sum(len(record) for record in source_facts(compiled).values())
    if total_fields == 0:
        return 1.0
    covered = {
        (
            value.get("connector"),
            value.get("record_id"),
            value.get("version"),
            value.get("field"),
        )
        for value in compiled.provenance.values()
        if isinstance(value, dict)
    }
    return len(covered) / total_fields


def observation_source_facts(
    representation: Representation, objects: list[JSON] | tuple[JSON, ...]
) -> dict[tuple[str, str], JSON]:
    """Decode only the objects that were actually rendered to the model."""

    facts: dict[tuple[str, str], JSON] = {}
    for item in objects:
        connector, payload = _decode_object(representation, item)
        key = (connector, str(payload["record_id"]))
        if key in facts:
            raise ValueError(f"duplicate rendered source record: {key}")
        facts[key] = payload
    return facts


def _record_id(representation: Representation, item: JSON) -> str:
    if representation is Representation.RAW:
        return str(item["payload"]["record_id"])
    if representation is Representation.GENERIC_COMPACT:
        return str(item["p"]["i"])
    return str(item["source"]["record_id"])


def render_observation(result: ToolResult, compiled: CompiledData) -> JSON:
    """Render only records returned by a tool in the frozen representation."""

    requested = set(result.source_ids)
    objects = [
        deepcopy(item)
        for item in compiled.objects
        if _record_id(compiled.representation, item) in requested
    ]
    visible_ids = {_record_id(compiled.representation, item) for item in objects}
    if visible_ids != requested:
        raise ValueError("compiled representation does not contain every tool source")
    links = [
        deepcopy(link)
        for link in compiled.links
        if set(link["source_record_ids"]) & requested
    ]
    visible_provenance = {
        (
            str(value.get("connector")),
            str(value.get("record_id")),
            int(value.get("version", 0)),
            str(value.get("field")),
        ): deepcopy(value)
        for value in compiled.provenance.values()
        if isinstance(value, dict) and value.get("record_id") in requested
    }
    return {
        "tool": result.tool,
        "representation": compiled.representation.value,
        "source_ids": list(result.source_ids),
        "objects": objects,
        "links": links,
        "field_dictionary": deepcopy(compiled.field_dictionary),
        "provenance": [visible_provenance[key] for key in sorted(visible_provenance)],
        "world_hash": compiled.world_hash,
    }
