"""Content-addressed plans for the prospective workflow-capacity boundary."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from threading import Lock
from typing import Mapping, Sequence

from bottleneckbench.design import Split, WorkflowLoad
from bottleneckbench.experiments import (
    ExecutionProfile,
    _balanced_order,
)
from bottleneckbench.runtime import CompletionClient, run_episode
from bottleneckbench.workflow_boundary import (
    WORKFLOW_BOUNDARY_EVIDENCE_NODES,
    WORKFLOW_BOUNDARY_LOCATOR_LEVELS,
    WORKFLOW_BOUNDARY_STUDY,
    WORKFLOW_BOUNDARY_VISIBLE_TOOLS,
    WorkflowBoundaryTask,
    workflow_boundary_manifest,
    workflow_boundary_tasks,
)
from bottleneckbench.core.experiments import ExperimentStore
from bottleneckbench.core.schema import JSON, Representation, canonical_json, stable_hash


WORKFLOW_BOUNDARY_PROTOCOL_VERSION = "workflow-capacity-frontier-2026-08-v1"


class ProviderFailureBudgetExceeded(RuntimeError):
    """Raised once the prospective infrastructure gate cannot pass."""


@dataclass(slots=True)
class ProviderFailureBudget:
    """Thread-safe shared stop gate across all model lanes in one block."""

    maximum_failures: int
    _failures: int = 0
    _stopped: bool = False
    _lock: Lock = field(default_factory=Lock, repr=False)

    def __post_init__(self) -> None:
        if self.maximum_failures < 0:
            raise ValueError("maximum provider failures must be nonnegative")

    @property
    def failures(self) -> int:
        with self._lock:
            return self._failures

    @property
    def stopped(self) -> bool:
        with self._lock:
            return self._stopped

    def observe(self, status: object) -> None:
        with self._lock:
            if status == "provider_failure":
                self._failures += 1
                if self._failures > self.maximum_failures:
                    self._stopped = True

    def require_open(self) -> None:
        with self._lock:
            if self._stopped:
                raise ProviderFailureBudgetExceeded(
                    "workflow-boundary provider-failure gate is mathematically "
                    f"unattainable: {self._failures} observed exceeds the frozen "
                    f"maximum of {self.maximum_failures}"
                )


@dataclass(frozen=True, slots=True)
class WorkflowBoundaryCondition:
    """One paired family-by-locator-depth cell."""

    task_id: str
    family_id: str
    intent: str
    outcome_variant: int
    prompt_variant: int
    branching: bool
    action_required: bool
    model: str
    harness: str
    locator_nodes: int
    total_nodes: int
    certified_minimum_calls: int
    seed: int
    workflow: WorkflowLoad = WorkflowLoad.HIGH
    representation: Representation = Representation.RAW
    visible_tools: int = WORKFLOW_BOUNDARY_VISIBLE_TOOLS

    def __post_init__(self) -> None:
        if not all((self.task_id, self.family_id, self.intent, self.model, self.harness)):
            raise ValueError("workflow-boundary condition identity is incomplete")
        if self.locator_nodes not in WORKFLOW_BOUNDARY_LOCATOR_LEVELS:
            raise ValueError("unregistered workflow-boundary locator level")
        if self.total_nodes != self.locator_nodes + WORKFLOW_BOUNDARY_EVIDENCE_NODES:
            raise ValueError("workflow-boundary condition node count is inconsistent")
        expected_calls = 2 * self.total_nodes + int(self.action_required)
        if self.certified_minimum_calls != expected_calls:
            raise ValueError("workflow-boundary condition call target is inconsistent")
        if self.branching:
            raise ValueError("workflow-boundary graph must be linear")

    def to_json(self) -> JSON:
        return {
            "study": WORKFLOW_BOUNDARY_STUDY,
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
            "locator_nodes": self.locator_nodes,
            "total_nodes": self.total_nodes,
            "certified_minimum_calls": self.certified_minimum_calls,
            "seed": self.seed,
        }

    @property
    def condition_id(self) -> str:
        return stable_hash(self.to_json())

    @classmethod
    def from_json(cls, payload: object) -> WorkflowBoundaryCondition:
        if not isinstance(payload, dict):
            raise ValueError("workflow-boundary condition must be an object")
        return cls(
            task_id=str(payload["task_id"]),
            family_id=str(payload["family_id"]),
            intent=str(payload["intent"]),
            outcome_variant=int(payload["outcome_variant"]),
            prompt_variant=int(payload["prompt_variant"]),
            branching=bool(payload["branching"]),
            action_required=bool(payload["action_required"]),
            model=str(payload["model"]),
            harness=str(payload["harness"]),
            locator_nodes=int(payload["locator_nodes"]),
            total_nodes=int(payload["total_nodes"]),
            certified_minimum_calls=int(payload["certified_minimum_calls"]),
            seed=int(payload["seed"]),
            workflow=WorkflowLoad(str(payload["workflow"])),
            representation=Representation(str(payload["representation"])),
            visible_tools=int(payload["visible_tools"]),
        )


@dataclass(frozen=True, slots=True)
class WorkflowBoundaryPlan:
    protocol_version: str
    split: Split
    model: str
    harness: str
    frozen_at: str
    source_hash: str
    task_manifest_hash: str
    protocol_artifact_hash: str
    model_panel_hash: str
    static_audit_hash: str
    admission_audit_hash: str | None
    execution: ExecutionProfile
    conditions: tuple[WorkflowBoundaryCondition, ...]
    family_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.protocol_version != WORKFLOW_BOUNDARY_PROTOCOL_VERSION:
            raise ValueError("workflow-boundary protocol version mismatch")
        if self.split not in {Split.DEVELOPMENT, Split.MAIN}:
            raise ValueError("workflow-boundary plans are limited to development and main")
        if not self.model or not self.harness or not self.frozen_at:
            raise ValueError("workflow-boundary plan identity is incomplete")
        hashes = (
            self.source_hash,
            self.task_manifest_hash,
            self.protocol_artifact_hash,
            self.model_panel_hash,
            self.static_audit_hash,
        )
        if any(len(value) != 64 for value in hashes):
            raise ValueError("workflow-boundary plan hashes must be SHA-256 digests")
        if self.admission_audit_hash is not None and len(self.admission_audit_hash) != 64:
            raise ValueError("workflow-boundary admission hash must be a SHA-256 digest")
        if self.split is Split.MAIN and self.admission_audit_hash is None:
            raise ValueError("main workflow-boundary plan requires development admission")
        if any(
            condition.model != self.model or condition.harness != self.harness
            for condition in self.conditions
        ):
            raise ValueError("workflow-boundary condition configuration mismatch")
        if len({condition.condition_id for condition in self.conditions}) != len(
            self.conditions
        ):
            raise ValueError("workflow-boundary plan has duplicate conditions")
        if self.family_ids and (
            len(set(self.family_ids)) != len(self.family_ids)
            or {condition.family_id for condition in self.conditions} != set(self.family_ids)
        ):
            raise ValueError("workflow-boundary family subset is malformed")

    def to_json(self) -> JSON:
        payload: JSON = {
            "protocol_version": self.protocol_version,
            "split": self.split.value,
            "model": self.model,
            "harness": self.harness,
            "frozen_at": self.frozen_at,
            "source_hash": self.source_hash,
            "task_manifest_hash": self.task_manifest_hash,
            "protocol_artifact_hash": self.protocol_artifact_hash,
            "model_panel_hash": self.model_panel_hash,
            "static_audit_hash": self.static_audit_hash,
            "admission_audit_hash": self.admission_audit_hash,
            "execution": self.execution.to_json(),
            "conditions": [condition.to_json() for condition in self.conditions],
        }
        if self.family_ids:
            payload["family_ids"] = list(self.family_ids)
        return payload

    @property
    def plan_hash(self) -> str:
        return stable_hash(self.to_json())

    @classmethod
    def from_json(cls, payload: object) -> WorkflowBoundaryPlan:
        if not isinstance(payload, dict):
            raise ValueError("workflow-boundary plan must be an object")
        raw_conditions = payload.get("conditions")
        if not isinstance(raw_conditions, list):
            raise ValueError("workflow-boundary plan conditions must be an array")
        return cls(
            protocol_version=str(payload["protocol_version"]),
            split=Split(str(payload["split"])),
            model=str(payload["model"]),
            harness=str(payload["harness"]),
            frozen_at=str(payload["frozen_at"]),
            source_hash=str(payload["source_hash"]),
            task_manifest_hash=str(payload["task_manifest_hash"]),
            protocol_artifact_hash=str(payload["protocol_artifact_hash"]),
            model_panel_hash=str(payload["model_panel_hash"]),
            static_audit_hash=str(payload["static_audit_hash"]),
            admission_audit_hash=(
                str(payload["admission_audit_hash"])
                if payload.get("admission_audit_hash") is not None
                else None
            ),
            execution=ExecutionProfile.from_json(payload["execution"]),
            conditions=tuple(
                WorkflowBoundaryCondition.from_json(item) for item in raw_conditions
            ),
            family_ids=tuple(str(value) for value in payload.get("family_ids", ())),
        )


def _source_hash() -> str:
    root = Path(__file__).parents[1]
    paths = tuple(
        root / relative
        for relative in (
            "bottleneckbench/workflow_boundary.py",
            "bottleneckbench/workflow_boundary_experiments.py",
            "bottleneckbench/design.py",
            "bottleneckbench/runtime.py",
            "bottleneckbench/providers.py",
            "bottleneckbench/experiments.py",
            "bottleneckbench/core/data.py",
            "bottleneckbench/core/schema.py",
            "bottleneckbench/core/world.py",
        )
    )
    return stable_hash(
        {
            str(path.relative_to(root)): stable_hash(path.read_text(encoding="utf-8"))
            for path in paths
        }
    )


def workflow_boundary_conditions(
    split: Split | str,
    model: str,
    harness: str,
    family_ids: Sequence[str] | None = None,
) -> tuple[WorkflowBoundaryCondition, ...]:
    normalized = Split(split)
    if normalized not in {Split.DEVELOPMENT, Split.MAIN}:
        raise ValueError("workflow-boundary conditions are limited to development and main")
    selected = tuple(family_ids or ())
    tasks = workflow_boundary_tasks(normalized)
    if selected:
        available = {task.family.family_id for task in tasks}
        if len(set(selected)) != len(selected) or not set(selected) <= available:
            raise ValueError("workflow-boundary family subset is invalid")
        tasks = tuple(task for task in tasks if task.family.family_id in set(selected))
    conditions = tuple(
        WorkflowBoundaryCondition(
            task_id=task.task_id,
            family_id=task.family.family_id,
            intent=task.family.intent,
            outcome_variant=task.family.outcome_variant,
            prompt_variant=task.family.prompt_variant,
            branching=task.family.branching,
            action_required=task.family.action_required,
            model=model,
            harness=harness,
            locator_nodes=task.locator_nodes,
            total_nodes=task.total_nodes,
            certified_minimum_calls=task.certified_call_target,
            seed=task.family.seed,
        )
        for task in tasks
    )
    return _balanced_order(
        conditions,
        group_key=lambda condition: condition.family_id,
        cell_key=lambda condition: (condition.locator_nodes,),
        schedule_key={
            "kind": WORKFLOW_BOUNDARY_STUDY,
            "split": normalized.value,
            "model": model,
            "harness": harness,
        },
    )


def _audit_hash(audit: Mapping[str, object], expected_name: str) -> str:
    if audit.get("audit") != expected_name or audit.get("passed") is not True:
        raise ValueError(f"passing {expected_name} audit required")
    return stable_hash(dict(audit))


def build_workflow_boundary_plan(
    *,
    split: Split | str,
    model: str,
    harness: str,
    frozen_at: str,
    protocol_artifact_hash: str,
    model_panel_hash: str,
    execution: ExecutionProfile,
    static_audit: Mapping[str, object],
    development_admission: Mapping[str, object] | None = None,
    family_ids: Sequence[str] | None = None,
) -> WorkflowBoundaryPlan:
    normalized = Split(split)
    if static_audit.get("split") != normalized.value:
        raise ValueError("workflow-boundary static audit split mismatch")
    static_hash = _audit_hash(
        static_audit, "workflow-capacity-frontier-static-construct-v1"
    )
    admission_hash: str | None = None
    if normalized is Split.MAIN:
        if development_admission is None:
            raise ValueError("development admission is required before main freeze")
        if (
            development_admission.get("audit")
            == "workflow-capacity-frontier-extension-admission-v1"
            and development_admission.get("admission_scope")
            == "descriptive candidate-model extension of an already-observed frontier"
        ):
            admission_hash = _audit_hash(
                development_admission,
                "workflow-capacity-frontier-extension-admission-v1",
            )
        else:
            if development_admission.get("split") != Split.DEVELOPMENT.value:
                raise ValueError("workflow-boundary admission must come from development")
            admission_hash = _audit_hash(
                development_admission,
                "workflow-capacity-frontier-development-admission-v1",
            )
    manifest_hash = stable_hash(json.loads(workflow_boundary_manifest(normalized)))
    if static_audit.get("task_manifest_hash") != manifest_hash:
        raise ValueError("workflow-boundary static audit manifest mismatch")
    return WorkflowBoundaryPlan(
        protocol_version=WORKFLOW_BOUNDARY_PROTOCOL_VERSION,
        split=normalized,
        model=model,
        harness=harness,
        frozen_at=frozen_at,
        source_hash=_source_hash(),
        task_manifest_hash=manifest_hash,
        protocol_artifact_hash=protocol_artifact_hash,
        model_panel_hash=model_panel_hash,
        static_audit_hash=static_hash,
        admission_audit_hash=admission_hash,
        execution=execution,
        conditions=workflow_boundary_conditions(
            normalized, model, harness, family_ids
        ),
        family_ids=tuple(family_ids or ()),
    )


def _atomic_write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(canonical_json(payload) + "\n", encoding="utf-8")
    temporary.replace(path)


def freeze_workflow_boundary_plan(path: Path, plan: WorkflowBoundaryPlan) -> None:
    _atomic_write(path, plan.to_json())


def load_workflow_boundary_plan(path: Path) -> WorkflowBoundaryPlan:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load workflow-boundary plan: {path}") from exc
    return WorkflowBoundaryPlan.from_json(payload)


def _validate_live_plan(plan: WorkflowBoundaryPlan) -> None:
    if plan.source_hash != _source_hash():
        raise ValueError("live source hash does not match workflow-boundary plan")
    manifest_hash = stable_hash(json.loads(workflow_boundary_manifest(plan.split)))
    if plan.task_manifest_hash != manifest_hash:
        raise ValueError("live task manifest does not match workflow-boundary plan")
    if plan.conditions != workflow_boundary_conditions(
        plan.split,
        plan.model,
        plan.harness,
        plan.family_ids or None,
    ):
        raise ValueError("live condition matrix does not match workflow-boundary plan")


def execute_workflow_boundary_plan(
    plan: WorkflowBoundaryPlan,
    client: CompletionClient,
    output_path: Path,
    *,
    max_conditions: int | None = None,
    workers: int | None = None,
    failure_budget: ProviderFailureBudget | None = None,
) -> JSON:
    """Execute once with plan-hash and condition-ID resume protection."""

    _validate_live_plan(plan)
    workers = plan.execution.workers if workers is None else workers
    if workers != plan.execution.workers:
        raise ValueError("runtime workers do not match frozen execution profile")
    tasks: dict[str, WorkflowBoundaryTask] = {
        task.task_id: task for task in workflow_boundary_tasks(plan.split)
    }
    selected: Sequence[WorkflowBoundaryCondition] = plan.conditions
    if max_conditions is not None:
        if max_conditions <= 0:
            raise ValueError("max_conditions must be positive")
        selected = selected[:max_conditions]
    store = ExperimentStore(output_path)
    planned_ids = {condition.condition_id for condition in plan.conditions}
    for row in store.rows:
        if row.get("plan_hash") != plan.plan_hash:
            raise ValueError("existing workflow-boundary row belongs to another plan")
        if row.get("condition_id") not in planned_ids:
            raise ValueError("existing workflow-boundary row is absent from the plan")
        if row.get("attempt") != 1:
            raise ValueError("existing workflow-boundary row attempt must equal one")
    pending = [
        condition
        for condition in selected
        if condition.condition_id not in store.completed_condition_ids
    ]
    skipped = len(selected) - len(pending)
    policy = plan.execution.episode_policy
    if policy is None:
        raise ValueError("workflow-boundary execution requires an explicit episode policy")
    inflight_lock = Lock()
    inflight = 0
    peak_inflight = 0

    def run_scheduled(condition: WorkflowBoundaryCondition):
        nonlocal inflight, peak_inflight
        if failure_budget is not None:
            failure_budget.require_open()
        started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with inflight_lock:
            inflight += 1
            inflight_at_start = inflight
            peak_inflight = max(peak_inflight, inflight)
        try:
            episode = run_episode(
                condition,  # type: ignore[arg-type]
                tasks[condition.task_id],  # type: ignore[arg-type]
                client,
                max_turns=None,
                max_tool_calls=None,
                tool_call_slack=policy.tool_call_slack,
                turn_slack=policy.turn_slack,
                max_total_tokens=policy.max_total_tokens,
                max_completion_tokens=policy.max_completion_tokens,
                dynamic_shortlist=policy.dynamic_shortlist,
                early_stop_infeasible=policy.early_stop_infeasible,
            )
        finally:
            with inflight_lock:
                inflight -= 1
        completed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with inflight_lock:
            observed_peak = peak_inflight
        return episode, {
            "started_at": started_at,
            "completed_at": completed_at,
            "worker_limit": workers,
            "inflight_at_start": inflight_at_start,
            "observed_peak_inflight": observed_peak,
        }

    executed = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for offset in range(0, len(pending), workers):
            if failure_budget is not None:
                failure_budget.require_open()
            batch = pending[offset : offset + workers]
            futures = {executor.submit(run_scheduled, condition) for condition in batch}
            batch_error: BaseException | None = None
            for future in as_completed(futures):
                try:
                    episode, executor_metadata = future.result()
                except BaseException as exc:
                    batch_error = batch_error or exc
                    continue
                row = {
                    **episode.to_json(),
                    "executor": executor_metadata,
                    "plan_hash": plan.plan_hash,
                    "study_role": WORKFLOW_BOUNDARY_STUDY,
                    "attempt": 1,
                }
                store.append(row)
                executed += 1
                if failure_budget is not None:
                    failure_budget.observe(row.get("status"))
            if batch_error is not None:
                raise batch_error
            if failure_budget is not None:
                failure_budget.require_open()
    return {"planned": len(selected), "executed": executed, "skipped": skipped}
