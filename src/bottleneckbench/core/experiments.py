"""Immutable condition plans, freezes, and append-only experiment storage."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping, Sequence

from bottleneckbench.core.evaluation import ConstructAudit
from bottleneckbench.core.harness import CompletionClient, run_episode
from bottleneckbench.core.schema import (
    Condition,
    JSON,
    Representation,
    RunStatus,
    Track,
    canonical_json,
    stable_hash,
)
from bottleneckbench.core.tasks import (
    candidate_templates,
    development_templates,
    sample_panel,
    transfer_templates,
)


PROTOCOL_VERSION = "connectorscale-v3.1-development-corrective"


@dataclass(frozen=True, slots=True)
class RunPlan:
    """Content-addressed model-response plan frozen after construct audit."""

    protocol_version: str
    panel: str
    model: str
    selector_seed: int
    frozen_at: str
    construct_audit_hash: str
    source_hash: str
    task_manifest_hash: str
    conditions: tuple[Condition, ...]

    def hash_payload(self) -> JSON:
        return {
            "protocol_version": self.protocol_version,
            "panel": self.panel,
            "model": self.model,
            "selector_seed": self.selector_seed,
            "frozen_at": self.frozen_at,
            "construct_audit_hash": self.construct_audit_hash,
            "source_hash": self.source_hash,
            "task_manifest_hash": self.task_manifest_hash,
            "conditions": [condition.to_json() for condition in self.conditions],
        }

    @property
    def plan_hash(self) -> str:
        return stable_hash(self.hash_payload())

    def to_json(self) -> JSON:
        return {**self.hash_payload(), "plan_hash": self.plan_hash}

    @classmethod
    def from_json(cls, payload: Mapping[str, object]) -> RunPlan:
        raw_conditions = payload.get("conditions")
        if not isinstance(raw_conditions, list):
            raise ValueError("run plan conditions must be a list")
        conditions: list[Condition] = []
        for raw in raw_conditions:
            if not isinstance(raw, dict):
                raise ValueError("run plan condition is not an object")
            conditions.append(
                Condition(
                    task_id=str(raw["task_id"]),
                    track=Track(str(raw["track"])),
                    model=str(raw["model"]),
                    harness=str(raw["harness"]),
                    representation=Representation(str(raw["representation"])),
                    registry_size=int(raw["registry_size"]),
                    exposure_size=int(raw["exposure_size"]),
                    seed=int(raw["seed"]),
                )
            )
        plan = cls(
            protocol_version=str(payload["protocol_version"]),
            panel=str(payload["panel"]),
            model=str(payload["model"]),
            selector_seed=int(payload["selector_seed"]),
            frozen_at=str(payload["frozen_at"]),
            construct_audit_hash=str(payload["construct_audit_hash"]),
            source_hash=str(payload["source_hash"]),
            task_manifest_hash=str(payload["task_manifest_hash"]),
            conditions=tuple(conditions),
        )
        supplied_hash = payload.get("plan_hash")
        if supplied_hash != plan.plan_hash:
            raise ValueError("run plan hash mismatch")
        return plan


def _source_hash() -> str:
    package = Path(__file__).parent
    sources = {
        path.name: stable_hash(path.read_text(encoding="utf-8"))
        for path in sorted(package.glob("*.py"))
    }
    return stable_hash(sources)


def _pilot_conditions(model: str) -> tuple[Condition, ...]:
    conditions: list[Condition] = []
    tasks = development_templates()
    for task_index, task in enumerate(tasks):
        world_seed = task_index + 1
        if task.track is Track.DISCOVERY:
            for registry_size in (32, 128):
                for harness in ("flat-retrieval", "hierarchical-retrieval"):
                    conditions.append(
                        Condition(
                            task_id=task.template_id,
                            track=Track.DISCOVERY,
                            model=model,
                            harness=harness,
                            representation=Representation.RAW,
                            registry_size=registry_size,
                            exposure_size=8,
                            seed=world_seed,
                        )
                    )
        else:
            for harness in ("oracle-react", "plan-then-execute"):
                for representation in (
                    Representation.RAW,
                    Representation.GENERIC_COMPACT,
                    Representation.TYPED,
                    Representation.LINKED,
                ):
                    conditions.append(
                        Condition(
                            task_id=task.template_id,
                            track=Track.COMPOSITION,
                            model=model,
                            harness=harness,
                            representation=representation,
                            registry_size=128,
                            exposure_size=16,
                            seed=world_seed,
                        )
                    )
    if len({condition.condition_id for condition in conditions}) != len(conditions):
        raise AssertionError("pilot plan contains duplicate conditions")
    return tuple(conditions)


def _confirmatory_conditions(model: str) -> tuple[Condition, ...]:
    conditions: list[Condition] = []
    tasks = candidate_templates()
    for task_index, task in enumerate(tasks):
        world_seed = task_index + 101
        if task.track is Track.DISCOVERY:
            for registry_size in (32, 128, 512, 2048):
                for harness in ("flat-retrieval", "hierarchical-retrieval"):
                    conditions.append(
                        Condition(
                            task_id=task.template_id,
                            track=Track.DISCOVERY,
                            model=model,
                            harness=harness,
                            representation=Representation.RAW,
                            registry_size=registry_size,
                            exposure_size=8,
                            seed=world_seed,
                        )
                    )
        else:
            for harness in ("oracle-react", "plan-then-execute"):
                for representation in (
                    Representation.RAW,
                    Representation.GENERIC_COMPACT,
                    Representation.TYPED,
                    Representation.LINKED,
                ):
                    conditions.append(
                        Condition(
                            task_id=task.template_id,
                            track=Track.COMPOSITION,
                            model=model,
                            harness=harness,
                            representation=representation,
                            registry_size=128,
                            exposure_size=16,
                            seed=world_seed,
                        )
                    )
    if len({condition.condition_id for condition in conditions}) != len(conditions):
        raise AssertionError("confirmatory plan contains duplicate conditions")
    return tuple(conditions)


def _transfer_conditions(model: str) -> tuple[Condition, ...]:
    conditions: list[Condition] = []
    for task_index, task in enumerate(transfer_templates()):
        world_seed = task_index + 201
        for harness in ("oracle-react", "plan-then-execute"):
            for representation in (Representation.RAW, Representation.LINKED):
                conditions.append(
                    Condition(
                        task_id=task.template_id,
                        track=Track.COMPOSITION,
                        model=model,
                        harness=harness,
                        representation=representation,
                        registry_size=128,
                        exposure_size=20,
                        seed=world_seed,
                    )
                )
    return tuple(conditions)


def build_pilot_plan(
    audit: ConstructAudit,
    *,
    model: str,
    selector_seed: int,
    frozen_at: str = "development-unfrozen",
) -> RunPlan:
    """Construct the bounded 96-cell S pilot after a passing audit."""

    if not audit.passed:
        raise ValueError("passing construct audit required before building a run plan")
    if audit.panel != "S":
        raise ValueError("construct audit panel does not match S run plan")
    manifest = sample_panel("S", selector_seed)
    return RunPlan(
        protocol_version=PROTOCOL_VERSION,
        panel="S",
        model=model,
        selector_seed=selector_seed,
        frozen_at=frozen_at,
        construct_audit_hash=stable_hash(audit.to_json()),
        source_hash=_source_hash(),
        task_manifest_hash=manifest.manifest_hash,
        conditions=_pilot_conditions(model),
    )


def build_confirmatory_plan(
    audit: ConstructAudit,
    *,
    model: str,
    selector_seed: int,
    frozen_at: str,
) -> RunPlan:
    """Construct the 384-cell response-sealed T plan."""

    if not audit.passed:
        raise ValueError("passing construct audit required before building a run plan")
    if audit.panel != "T":
        raise ValueError("construct audit panel does not match T run plan")
    manifest = sample_panel("T", selector_seed)
    return RunPlan(
        protocol_version=PROTOCOL_VERSION,
        panel="T",
        model=model,
        selector_seed=selector_seed,
        frozen_at=frozen_at,
        construct_audit_hash=stable_hash(audit.to_json()),
        source_hash=_source_hash(),
        task_manifest_hash=manifest.manifest_hash,
        conditions=_confirmatory_conditions(model),
    )


def build_transfer_plan(
    audit: ConstructAudit,
    *,
    model: str,
    selector_seed: int,
    frozen_at: str,
) -> RunPlan:
    """Construct the 48-cell post-confirmatory CRM/Tasks transfer slice."""

    if not audit.passed:
        raise ValueError("passing construct audit required before building a run plan")
    if audit.panel != "U":
        raise ValueError("construct audit panel does not match U run plan")
    manifest = sample_panel("U", selector_seed)
    return RunPlan(
        protocol_version=PROTOCOL_VERSION,
        panel="U",
        model=model,
        selector_seed=selector_seed,
        frozen_at=frozen_at,
        construct_audit_hash=stable_hash(audit.to_json()),
        source_hash=_source_hash(),
        task_manifest_hash=manifest.manifest_hash,
        conditions=_transfer_conditions(model),
    )


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(canonical_json(payload) + "\n", encoding="utf-8")
    temporary.replace(path)


def freeze_plan(
    path: Path,
    *,
    panel: str,
    audit: ConstructAudit,
    model: str,
    selector_seed: int,
    frozen_at: str,
) -> RunPlan:
    """Freeze an S plan atomically and fail closed on construct defects."""

    normalized_panel = panel.upper()
    if normalized_panel == "S":
        plan = build_pilot_plan(
            audit,
            model=model,
            selector_seed=selector_seed,
            frozen_at=frozen_at,
        )
    elif normalized_panel == "T":
        plan = build_confirmatory_plan(
            audit,
            model=model,
            selector_seed=selector_seed,
            frozen_at=frozen_at,
        )
    elif normalized_panel == "U":
        plan = build_transfer_plan(
            audit,
            model=model,
            selector_seed=selector_seed,
            frozen_at=frozen_at,
        )
    else:
        raise ValueError(f"unsupported panel: {panel}")
    _atomic_write_json(path, plan.to_json())
    return plan


def load_plan(path: Path) -> RunPlan:
    """Load and hash-validate a frozen run plan."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load run plan: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("run plan root must be an object")
    return RunPlan.from_json(payload)


def _validate_plan_context(plan: RunPlan) -> None:
    if plan.protocol_version != PROTOCOL_VERSION:
        raise ValueError("run plan protocol version does not match the active runtime")
    if _source_hash() != plan.source_hash:
        raise ValueError("live source hash does not match the run plan")
    manifest = sample_panel(plan.panel, plan.selector_seed)
    if manifest.manifest_hash != plan.task_manifest_hash:
        raise ValueError("live task manifest hash does not match the run plan")
    if len({condition.condition_id for condition in plan.conditions}) != len(plan.conditions):
        raise ValueError("run plan contains duplicate condition IDs")
    if plan.panel == "S":
        expected = _pilot_conditions(plan.model)
    elif plan.panel == "T":
        expected = _confirmatory_conditions(plan.model)
    elif plan.panel == "U":
        expected = _transfer_conditions(plan.model)
    else:
        raise ValueError(f"unsupported execution panel: {plan.panel}")
    if plan.conditions != expected:
        raise ValueError("run plan condition set does not match the generated panel")


def _valid_evidence_item(value: object) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("connector"), str)
        and isinstance(value.get("record_id"), str)
        and isinstance(value.get("version"), int)
    )


def _result_row_schema_valid(row: Mapping[str, object]) -> bool:
    if not isinstance(row.get("answer"), str):
        return False
    for name in ("citations", "errors", "response_ids", "trace"):
        if not isinstance(row.get(name), list):
            return False
    if not all(_valid_evidence_item(item) for item in row["citations"]):
        return False
    if not all(isinstance(item, str) for item in row["errors"]):
        return False
    if not all(isinstance(item, str) for item in row["response_ids"]):
        return False
    exposure = row.get("exposure")
    if not isinstance(exposure, dict):
        return False
    if not all(
        isinstance(exposure.get(name), list)
        and all(isinstance(item, str) for item in exposure[name])
        for name in ("tool_names", "selected_connectors", "required_tools")
    ):
        return False
    if not isinstance(exposure.get("required_available"), (int, float)):
        return False
    if not isinstance(exposure.get("oracle"), bool):
        return False
    usage = row.get("usage")
    if not isinstance(usage, dict) or not all(
        isinstance(usage.get(name), int) and usage[name] >= 0
        for name in ("prompt_tokens", "completion_tokens", "total_tokens")
    ):
        return False
    verdict = row.get("verdict")
    if not isinstance(verdict, dict) or not all(
        isinstance(verdict.get(name), bool)
        for name in (
            "outcome",
            "evidence",
            "safety",
            "governance",
            "budget",
            "strict_vts",
        )
    ):
        return False
    if not isinstance(verdict.get("errors"), list) or not all(
        isinstance(item, str) for item in verdict["errors"]
    ):
        return False
    for event in row["trace"]:
        if not isinstance(event, dict):
            return False
        if not (
            isinstance(event.get("tool"), str)
            and isinstance(event.get("arguments"), dict)
            and isinstance(event.get("before_hash"), str)
            and isinstance(event.get("after_hash"), str)
            and isinstance(event.get("evidence"), list)
            and all(_valid_evidence_item(item) for item in event["evidence"])
            and isinstance(event.get("result_records"), list)
            and all(isinstance(item, dict) for item in event["result_records"])
            and isinstance(event.get("mutated"), bool)
            and (
                event.get("receipt_id") is None
                or isinstance(event.get("receipt_id"), str)
            )
        ):
            return False
    return True


def execute_plan(
    plan: RunPlan,
    client: CompletionClient,
    output_path: Path,
    *,
    max_conditions: int | None = None,
) -> JSON:
    """Execute only cells bound to the live source, tasks, and plan hash."""

    _validate_plan_context(plan)
    if plan.panel == "S":
        tasks = development_templates()
    elif plan.panel == "T":
        tasks = candidate_templates()
    elif plan.panel == "U":
        tasks = transfer_templates()
    else:
        raise ValueError(f"unsupported execution panel: {plan.panel}")
    task_by_id = {task.template_id: task for task in tasks}
    selected = plan.conditions
    if max_conditions is not None:
        if max_conditions <= 0:
            raise ValueError("max_conditions must be positive")
        selected = selected[:max_conditions]
    store = ExperimentStore(output_path)
    planned_ids = {condition.condition_id for condition in plan.conditions}
    required_row_fields = {
        "answer",
        "attempt",
        "citations",
        "condition",
        "condition_id",
        "errors",
        "exposure",
        "plan_hash",
        "response_ids",
        "status",
        "trace",
        "usage",
        "verdict",
    }
    valid_statuses = {status.value for status in RunStatus}
    for row in store.rows:
        if row.get("plan_hash") != plan.plan_hash:
            raise ValueError("existing result row plan hash does not match the run plan")
        if not required_row_fields <= set(row):
            raise ValueError("existing result row is incomplete")
        if row.get("attempt") != 1:
            raise ValueError("existing result row has an invalid attempt value")
        if row.get("status") not in valid_statuses:
            raise ValueError("existing result row has an invalid terminal status")
        if not _result_row_schema_valid(row):
            raise ValueError("existing result row is schema-invalid")
        condition_id = row.get("condition_id")
        if condition_id not in planned_ids:
            raise ValueError("existing result row is absent from the run plan")
        condition = row.get("condition")
        if not isinstance(condition, dict) or stable_hash(condition) != condition_id:
            raise ValueError("existing result row condition identity is invalid")
    executed = 0
    skipped = 0
    for condition in selected:
        if condition.condition_id in store.completed_condition_ids:
            skipped += 1
            continue
        try:
            task = task_by_id[condition.task_id]
        except KeyError as exc:
            raise ValueError(f"plan references unknown task: {condition.task_id}") from exc
        if condition.track is not task.track or condition.model != plan.model:
            raise ValueError("condition semantics do not match the task and plan")
        episode = run_episode(condition, task, client)
        row = {
            **episode.to_json(),
            "plan_hash": plan.plan_hash,
            "attempt": 1,
        }
        store.append(row)
        executed += 1
    return {"planned": len(selected), "executed": executed, "skipped": skipped}


class ExperimentStore:
    """Append-only JSONL store with conflict-detecting resume semantics."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._rows: dict[str, str] = {}
        if path.exists():
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"invalid JSONL at {path}:{line_number}"
                    ) from exc
                if not isinstance(row, dict):
                    raise ValueError(f"non-object row at {path}:{line_number}")
                self._remember(row)

    @property
    def completed_condition_ids(self) -> frozenset[str]:
        return frozenset(self._rows)

    @property
    def rows(self) -> tuple[JSON, ...]:
        return tuple(json.loads(encoded) for encoded in self._rows.values())

    def _remember(self, row: Mapping[str, object]) -> bool:
        condition_id = row.get("condition_id")
        if not isinstance(condition_id, str) or not condition_id:
            raise ValueError("experiment row requires a non-empty condition_id")
        encoded = canonical_json(dict(row))
        previous = self._rows.get(condition_id)
        if previous is not None:
            if previous != encoded:
                raise ValueError(f"conflicting row for condition_id {condition_id}")
            return False
        self._rows[condition_id] = encoded
        return True

    def append(self, row: Mapping[str, object]) -> bool:
        """Append a new row, skip an identical duplicate, and reject conflicts."""

        if not self._remember(row):
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = self._rows[str(row["condition_id"])]
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
            handle.flush()
        return True
