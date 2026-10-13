"""Content-addressed plans and checkpointed joint-cube execution."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from threading import Lock
from typing import Callable, Mapping, Sequence, TypeVar

from bottleneckbench.design import (
    CapacityCondition,
    Split,
    WorkflowLoad,
    canonical_task_manifest,
    tasks_for_split,
)
from bottleneckbench.discovery import (
    DiscoveryCondition,
    Retriever,
    discovery_cases,
    run_discovery_episode,
)
from bottleneckbench.runtime import CompletionClient, run_episode
from bottleneckbench.study_design import (
    DISCOVERY_REGISTRY_SIZES,
    StudyRole,
    capacity_cells,
)
from bottleneckbench.core.experiments import ExperimentStore
from bottleneckbench.core.schema import JSON, Representation, canonical_json, stable_hash


PROTOCOL_VERSION = "factorized-capacity-surface-2026-08"
_Scheduled = TypeVar("_Scheduled")


def string_sequence(
    payload: Mapping[str, object], key: str
) -> tuple[str, ...]:
    """Read a JSON string array without silently iterating a scalar string."""

    value = payload.get(key, ())
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{key} must be an array of strings")
    if any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{key} must contain nonempty strings")
    return tuple(value)


def _balanced_order(
    items: Sequence[_Scheduled],
    *,
    group_key: Callable[[_Scheduled], str],
    cell_key: Callable[[_Scheduled], tuple[object, ...]],
    schedule_key: Mapping[str, object],
) -> tuple[_Scheduled, ...]:
    """Interleave complete cells in factor-balanced, content-addressed blocks."""

    groups = sorted(
        {group_key(item) for item in items},
        key=lambda value: stable_hash(
            {"schedule": dict(schedule_key), "axis": "group", "value": value}
        ),
    )
    cells = sorted(
        {cell_key(item) for item in items},
        key=lambda value: stable_hash(
            {"schedule": dict(schedule_key), "axis": "cell", "value": value}
        ),
    )
    group_index = {value: index for index, value in enumerate(groups)}
    cell_index = {value: index for index, value in enumerate(cells)}

    def order_key(item: _Scheduled) -> tuple[int, str]:
        block = (
            group_index[group_key(item)] - cell_index[cell_key(item)]
        ) % len(groups)
        return block, stable_hash(
            {
                "schedule": dict(schedule_key),
                "block": block,
                "condition_id": getattr(item, "condition_id"),
            }
        )

    ordered = tuple(sorted(items, key=order_key))
    for start in range(0, len(ordered), len(cells)):
        block = ordered[start : start + len(cells)]
        if len(block) != len(cells) or len({cell_key(item) for item in block}) != len(cells):
            raise AssertionError("execution block does not cover every factor cell exactly once")
    return ordered


@dataclass(frozen=True, slots=True)
class EpisodePolicy:
    """Outcome-affecting episode limits frozen separately from transport settings."""

    name: str
    tool_call_slack: int
    turn_slack: int
    max_total_tokens: int
    max_completion_tokens: int
    dynamic_shortlist: bool
    early_stop_infeasible: bool

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("episode policy name is required")
        if self.tool_call_slack < 0 or self.turn_slack < 1:
            raise ValueError("episode policy slacks are invalid")
        if self.max_total_tokens <= 0 or self.max_completion_tokens <= 0:
            raise ValueError("episode token limits must be positive")

    def to_json(self) -> JSON:
        return {
            "name": self.name,
            "tool_call_slack": self.tool_call_slack,
            "turn_slack": self.turn_slack,
            "max_total_tokens": self.max_total_tokens,
            "max_completion_tokens": self.max_completion_tokens,
            "dynamic_shortlist": self.dynamic_shortlist,
            "early_stop_infeasible": self.early_stop_infeasible,
        }

    @classmethod
    def from_json(cls, payload: object) -> EpisodePolicy:
        if not isinstance(payload, dict):
            raise ValueError("episode policy must be an object")
        return cls(
            name=str(payload["name"]),
            tool_call_slack=int(payload["tool_call_slack"]),
            turn_slack=int(payload["turn_slack"]),
            max_total_tokens=int(payload["max_total_tokens"]),
            max_completion_tokens=int(payload["max_completion_tokens"]),
            dynamic_shortlist=bool(payload["dynamic_shortlist"]),
            early_stop_infeasible=bool(payload["early_stop_infeasible"]),
        )


@dataclass(frozen=True, slots=True)
class ExecutionProfile:
    """Provider settings that are frozen into every hosted run plan."""

    endpoint: str
    api_key_env: str
    timeout_seconds: float
    transport_retries: int
    workers: int
    provider_concurrency_cap: int
    minimum_request_interval_seconds: float = 0.0
    api_key_header: str = "Authorization"
    episode_policy: EpisodePolicy | None = None
    provider_only: tuple[str, ...] = ()
    provider_allow_fallbacks: bool = True
    provider_data_collection: str | None = None
    provider_require_parameters: bool = False
    provider_quantizations: tuple[str, ...] = ()
    service_tier: str | None = None
    accepted_response_models: tuple[str, ...] = ()
    proxy_url: str | None = None
    omit_temperature: bool = False
    omit_seed: bool = False
    omit_parallel_tool_calls: bool = False
    require_usage: bool = True
    stream: bool = False
    disable_response_cache: bool = False

    def __post_init__(self) -> None:
        if not self.endpoint or not self.api_key_env:
            raise ValueError("execution endpoint and API-key environment name are required")
        if self.timeout_seconds <= 0 or self.transport_retries < 0:
            raise ValueError("execution timeout must be positive and retries nonnegative")
        if self.workers <= 0 or self.provider_concurrency_cap < self.workers:
            raise ValueError("execution workers must fit the provider concurrency cap")
        if self.minimum_request_interval_seconds < 0:
            raise ValueError("minimum request interval must be nonnegative")
        if self.api_key_header not in {"Authorization", "x-goog-api-key"}:
            raise ValueError("unsupported API-key header")
        if any(not value for value in self.provider_only):
            raise ValueError("provider_only entries must be nonempty")
        if len(set(self.provider_only)) != len(self.provider_only):
            raise ValueError("provider_only entries must be unique")
        if self.provider_data_collection not in {None, "allow", "deny"}:
            raise ValueError("unsupported provider data-collection policy")
        if any(not value for value in self.provider_quantizations):
            raise ValueError("provider quantizations must be nonempty")
        if len(set(self.provider_quantizations)) != len(self.provider_quantizations):
            raise ValueError("provider quantizations must be unique")
        if self.service_tier not in {None, "default", "flex", "priority"}:
            raise ValueError("unsupported service tier")
        if any(not value for value in self.accepted_response_models):
            raise ValueError("accepted response models must be nonempty")
        if len(set(self.accepted_response_models)) != len(
            self.accepted_response_models
        ):
            raise ValueError("accepted response models must be unique")
        if self.proxy_url is not None and not self.proxy_url.startswith(
            ("http://", "https://")
        ):
            raise ValueError("proxy URL must be HTTP(S)")

    @property
    def transport_identity(self) -> tuple[object, ...]:
        """Settings that must be identical within one model's paired lane."""

        return (
            self.endpoint,
            self.api_key_env,
            self.timeout_seconds,
            self.transport_retries,
            self.minimum_request_interval_seconds,
            self.api_key_header,
            self.provider_only,
            self.provider_allow_fallbacks,
            self.provider_data_collection,
            self.provider_require_parameters,
            self.provider_quantizations,
            self.service_tier,
            self.accepted_response_models,
            self.proxy_url,
            self.omit_temperature,
            self.omit_seed,
            self.omit_parallel_tool_calls,
            self.require_usage,
            self.stream,
            self.disable_response_cache,
        )

    def client_kwargs(self) -> dict[str, object]:
        """Return the exact transport constructor arguments frozen by the plan."""

        return {
            "endpoint": self.endpoint,
            "api_key_env": self.api_key_env,
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.transport_retries,
            "minimum_request_interval_seconds": self.minimum_request_interval_seconds,
            "api_key_header": self.api_key_header,
            "provider_only": self.provider_only,
            "provider_allow_fallbacks": self.provider_allow_fallbacks,
            "provider_data_collection": self.provider_data_collection,
            "provider_require_parameters": self.provider_require_parameters,
            "provider_quantizations": self.provider_quantizations,
            "service_tier": self.service_tier,
            "accepted_response_models": self.accepted_response_models,
            "proxy_url": self.proxy_url,
            "omit_temperature": self.omit_temperature,
            "omit_seed": self.omit_seed,
            "omit_parallel_tool_calls": self.omit_parallel_tool_calls,
            "require_usage": self.require_usage,
            "stream": self.stream,
            "disable_response_cache": self.disable_response_cache,
        }

    def to_json(self) -> JSON:
        payload: JSON = {
            "endpoint": self.endpoint,
            "api_key_env": self.api_key_env,
            "timeout_seconds": self.timeout_seconds,
            "transport_retries": self.transport_retries,
            "workers": self.workers,
            "provider_concurrency_cap": self.provider_concurrency_cap,
            "api_key_header": self.api_key_header,
        }
        if self.minimum_request_interval_seconds:
            payload["minimum_request_interval_seconds"] = (
                self.minimum_request_interval_seconds
            )
        if self.episode_policy is not None:
            payload["episode_policy"] = self.episode_policy.to_json()
        if self.provider_only:
            payload["provider_only"] = list(self.provider_only)
        if not self.provider_allow_fallbacks:
            payload["provider_allow_fallbacks"] = False
        if self.provider_data_collection is not None:
            payload["provider_data_collection"] = self.provider_data_collection
        if self.provider_require_parameters:
            payload["provider_require_parameters"] = True
        if self.provider_quantizations:
            payload["provider_quantizations"] = list(self.provider_quantizations)
        if self.service_tier is not None:
            payload["service_tier"] = self.service_tier
        if self.accepted_response_models:
            payload["accepted_response_models"] = list(
                self.accepted_response_models
            )
        if self.proxy_url is not None:
            payload["proxy_url"] = self.proxy_url
        if self.omit_temperature:
            payload["omit_temperature"] = True
        if self.omit_seed:
            payload["omit_seed"] = True
        if self.omit_parallel_tool_calls:
            payload["omit_parallel_tool_calls"] = True
        payload["require_usage"] = self.require_usage
        if self.stream:
            payload["stream"] = True
        if self.disable_response_cache:
            payload["disable_response_cache"] = True
        return payload

    @classmethod
    def from_json(cls, payload: object) -> ExecutionProfile:
        if not isinstance(payload, dict):
            raise ValueError("run plan execution profile must be an object")
        return cls(
            endpoint=str(payload["endpoint"]),
            api_key_env=str(payload["api_key_env"]),
            timeout_seconds=float(payload["timeout_seconds"]),
            transport_retries=int(payload["transport_retries"]),
            workers=int(payload["workers"]),
            provider_concurrency_cap=int(payload["provider_concurrency_cap"]),
            minimum_request_interval_seconds=float(
                payload.get("minimum_request_interval_seconds", 0.0)
            ),
            api_key_header=str(payload.get("api_key_header", "Authorization")),
            episode_policy=(
                EpisodePolicy.from_json(payload["episode_policy"])
                if payload.get("episode_policy") is not None
                else None
            ),
            provider_only=string_sequence(payload, "provider_only"),
            provider_allow_fallbacks=bool(
                payload.get("provider_allow_fallbacks", True)
            ),
            provider_data_collection=(
                str(payload["provider_data_collection"])
                if payload.get("provider_data_collection") is not None
                else None
            ),
            provider_require_parameters=bool(
                payload.get("provider_require_parameters", False)
            ),
            provider_quantizations=string_sequence(
                payload, "provider_quantizations"
            ),
            service_tier=(
                str(payload["service_tier"])
                if payload.get("service_tier") is not None
                else None
            ),
            accepted_response_models=string_sequence(
                payload, "accepted_response_models"
            ),
            proxy_url=(
                str(payload["proxy_url"])
                if payload.get("proxy_url") is not None
                else None
            ),
            omit_temperature=bool(payload.get("omit_temperature", False)),
            omit_seed=bool(payload.get("omit_seed", False)),
            omit_parallel_tool_calls=bool(
                payload.get("omit_parallel_tool_calls", False)
            ),
            require_usage=bool(payload.get("require_usage", True)),
            stream=bool(payload.get("stream", False)),
            disable_response_cache=bool(
                payload.get("disable_response_cache", False)
            ),
        )


@dataclass(frozen=True, slots=True)
class RunPlan:
    """Immutable per-model plan for one benchmark split."""

    protocol_version: str
    split: Split
    model: str
    harness: str
    study_role: StudyRole
    frozen_at: str
    source_hash: str
    protocol_artifact_hash: str
    model_panel_hash: str
    execution: ExecutionProfile
    task_manifest_hash: str
    static_audit_hash: str
    runtime_audit_hash: str
    admission_audit_hash: str | None
    conditions: tuple[CapacityCondition, ...]

    def hash_payload(self) -> JSON:
        return {
            "protocol_version": self.protocol_version,
            "split": self.split.value,
            "model": self.model,
            "harness": self.harness,
            "study_role": self.study_role.value,
            "frozen_at": self.frozen_at,
            "source_hash": self.source_hash,
            "protocol_artifact_hash": self.protocol_artifact_hash,
            "model_panel_hash": self.model_panel_hash,
            "execution": self.execution.to_json(),
            "task_manifest_hash": self.task_manifest_hash,
            "static_audit_hash": self.static_audit_hash,
            "runtime_audit_hash": self.runtime_audit_hash,
            "admission_audit_hash": self.admission_audit_hash,
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
        conditions: list[CapacityCondition] = []
        for raw in raw_conditions:
            if not isinstance(raw, dict):
                raise ValueError("run plan condition must be an object")
            conditions.append(
                CapacityCondition(
                    task_id=str(raw["task_id"]),
                    family_id=str(raw["family_id"]),
                    intent=str(raw["intent"]),
                    outcome_variant=int(raw["outcome_variant"]),
                    prompt_variant=int(raw["prompt_variant"]),
                    branching=bool(raw["branching"]),
                    action_required=bool(raw["action_required"]),
                    model=str(raw["model"]),
                    harness=str(raw["harness"]),
                    workflow=WorkflowLoad(str(raw["workflow"])),
                    representation=Representation(str(raw["representation"])),
                    visible_tools=int(raw["visible_tools"]),
                    seed=int(raw["seed"]),
                )
            )
        plan = cls(
            protocol_version=str(payload["protocol_version"]),
            split=Split(str(payload["split"])),
            model=str(payload["model"]),
            harness=str(payload["harness"]),
            study_role=StudyRole(str(payload["study_role"])),
            frozen_at=str(payload["frozen_at"]),
            source_hash=str(payload["source_hash"]),
            protocol_artifact_hash=str(payload["protocol_artifact_hash"]),
            model_panel_hash=str(payload["model_panel_hash"]),
            execution=ExecutionProfile.from_json(payload.get("execution")),
            task_manifest_hash=str(payload["task_manifest_hash"]),
            static_audit_hash=str(payload["static_audit_hash"]),
            runtime_audit_hash=str(payload["runtime_audit_hash"]),
            admission_audit_hash=(
                str(payload["admission_audit_hash"])
                if payload.get("admission_audit_hash") is not None
                else None
            ),
            conditions=tuple(conditions),
        )
        if payload.get("plan_hash") != plan.plan_hash:
            raise ValueError("run plan hash mismatch")
        return plan


@dataclass(frozen=True, slots=True)
class DiscoveryRunPlan:
    """Immutable per-model registered-catalog experiment plan."""

    protocol_version: str
    split: Split
    model: str
    harness: str
    frozen_at: str
    source_hash: str
    protocol_artifact_hash: str
    model_panel_hash: str
    execution: ExecutionProfile
    case_manifest_hash: str
    construct_audit_hash: str
    admission_audit_hash: str | None
    conditions: tuple[DiscoveryCondition, ...]

    def hash_payload(self) -> JSON:
        return {
            "protocol_version": self.protocol_version,
            "split": self.split.value,
            "model": self.model,
            "harness": self.harness,
            "frozen_at": self.frozen_at,
            "source_hash": self.source_hash,
            "protocol_artifact_hash": self.protocol_artifact_hash,
            "model_panel_hash": self.model_panel_hash,
            "execution": self.execution.to_json(),
            "case_manifest_hash": self.case_manifest_hash,
            "construct_audit_hash": self.construct_audit_hash,
            "admission_audit_hash": self.admission_audit_hash,
            "conditions": [condition.to_json() for condition in self.conditions],
        }

    @property
    def plan_hash(self) -> str:
        return stable_hash(self.hash_payload())

    def to_json(self) -> JSON:
        return {**self.hash_payload(), "plan_hash": self.plan_hash}

    @classmethod
    def from_json(cls, payload: Mapping[str, object]) -> DiscoveryRunPlan:
        raw_conditions = payload.get("conditions")
        if not isinstance(raw_conditions, list):
            raise ValueError("discovery conditions must be a list")
        conditions = tuple(
            DiscoveryCondition(
                case_id=str(raw["case_id"]),
                split=Split(str(raw["split"])),
                model=str(raw["model"]),
                harness=str(raw["harness"]),
                retriever=Retriever(str(raw["retriever"])),
                registry_size=int(raw["registry_size"]),
                visible_tools=int(raw["visible_tools"]),
                seed=int(raw["seed"]),
            )
            for raw in raw_conditions
            if isinstance(raw, dict)
        )
        if len(conditions) != len(raw_conditions):
            raise ValueError("discovery condition must be an object")
        plan = cls(
            protocol_version=str(payload["protocol_version"]),
            split=Split(str(payload["split"])),
            model=str(payload["model"]),
            harness=str(payload["harness"]),
            frozen_at=str(payload["frozen_at"]),
            source_hash=str(payload["source_hash"]),
            protocol_artifact_hash=str(payload["protocol_artifact_hash"]),
            model_panel_hash=str(payload["model_panel_hash"]),
            execution=ExecutionProfile.from_json(payload.get("execution")),
            case_manifest_hash=str(payload["case_manifest_hash"]),
            construct_audit_hash=str(payload["construct_audit_hash"]),
            admission_audit_hash=(
                str(payload["admission_audit_hash"])
                if payload.get("admission_audit_hash") is not None
                else None
            ),
            conditions=conditions,
        )
        if payload.get("plan_hash") != plan.plan_hash:
            raise ValueError("discovery run plan hash mismatch")
        return plan


def _source_hash() -> str:
    package = Path(__file__).parent
    reused = Path(__file__).parent / "core"
    paths = sorted(package.glob("*.py"))
    paths.extend(
        reused / name
        for name in (
            "agnes.py",
            "analysis.py",
            "data.py",
            "experiments.py",
            "schema.py",
            "world.py",
        )
    )
    return stable_hash(
        {
            str(path.relative_to(Path(__file__).parents[1])): stable_hash(
                path.read_text(encoding="utf-8")
            )
            for path in paths
        }
    )


def _audit_hash(audit: Mapping[str, object], expected_name: str) -> str:
    if audit.get("audit") != expected_name or audit.get("passed") is not True:
        raise ValueError(f"passing {expected_name} audit required")
    return stable_hash(dict(audit))


def _admission_hash(
    audit: Mapping[str, object] | None,
    *,
    split: Split,
    protocol_artifact_hash: str,
    model_panel_hash: str,
) -> str | None:
    if split is Split.DEVELOPMENT:
        return None
    if audit is None:
        raise ValueError("passing admission audit required before main/transfer freeze")
    if audit.get("audit") == "prospective-capacity-execution-admission-v1":
        if (
            audit.get("target_split") != split.value
            or audit.get("protocol_artifact_hash") != protocol_artifact_hash
            or audit.get("model_panel_hash") != model_panel_hash
        ):
            raise ValueError("prospective admission does not bind this plan")
        return _audit_hash(audit, "prospective-capacity-execution-admission-v1")
    if audit.get("split") != Split.DEVELOPMENT.value:
        raise ValueError("admission audit must come from the development split")
    return _audit_hash(audit, "factorized-capacity-development-admission")


def _conditions(
    split: Split,
    model: str,
    harness: str,
    study_role: StudyRole,
) -> tuple[CapacityCondition, ...]:
    tasks = tasks_for_split(split)
    conditions: list[CapacityCondition] = []
    by_family_workflow = {
        (task.family.family_id, task.workflow): task for task in tasks
    }
    family_ids = tuple(dict.fromkeys(task.family.family_id for task in tasks))
    cells = capacity_cells(split, study_role)
    for family_id in family_ids:
        for cell in cells:
            task = by_family_workflow[(family_id, cell.workflow)]
            conditions.append(
                CapacityCondition(
                    task_id=task.task_id,
                    family_id=family_id,
                    intent=task.family.intent,
                    outcome_variant=task.family.outcome_variant,
                    prompt_variant=task.family.prompt_variant,
                    branching=task.family.branching,
                    action_required=task.family.action_required,
                    model=model,
                    harness=harness,
                    workflow=cell.workflow,
                    representation=cell.representation,
                    visible_tools=cell.visible_tools,
                    seed=task.family.seed,
                )
            )
    if len({condition.condition_id for condition in conditions}) != len(conditions):
        raise AssertionError("run plan contains duplicate conditions")
    return _balanced_order(
        conditions,
        group_key=lambda condition: condition.family_id,
        cell_key=lambda condition: (
            condition.visible_tools,
            condition.workflow.value,
            condition.representation.value,
        ),
        schedule_key={
            "kind": "capacity",
            "split": split.value,
            "model": model,
            "harness": harness,
            "study_role": study_role.value,
        },
    )


def _discovery_conditions(
    split: Split, model: str, harness: str
) -> tuple[DiscoveryCondition, ...]:
    conditions = tuple(
        DiscoveryCondition(
            case_id=case.case_id,
            split=split,
            model=model,
            harness=harness,
            retriever=retriever,
            registry_size=registry_size,
            visible_tools=16,
            seed=case.seed,
        )
        for case in discovery_cases(split)
        for registry_size in DISCOVERY_REGISTRY_SIZES
        for retriever in Retriever
    )
    if len({condition.condition_id for condition in conditions}) != len(conditions):
        raise AssertionError("discovery plan contains duplicate conditions")
    return _balanced_order(
        conditions,
        group_key=lambda condition: condition.case_id,
        cell_key=lambda condition: (
            condition.registry_size,
            condition.retriever.value,
        ),
        schedule_key={
            "kind": "discovery",
            "split": split.value,
            "model": model,
            "harness": harness,
        },
    )


def build_plan(
    *,
    split: Split | str,
    model: str,
    harness: str,
    study_role: StudyRole | str = StudyRole.PRIMARY,
    frozen_at: str,
    protocol_artifact_hash: str,
    model_panel_hash: str,
    execution: ExecutionProfile,
    static_audit: Mapping[str, object],
    runtime_audit: Mapping[str, object],
    admission_audit: Mapping[str, object] | None = None,
) -> RunPlan:
    """Build S freely, but require an active-range admission audit for T/U."""

    normalized = Split(split)
    normalized_role = StudyRole(study_role)
    if not model or not harness or not frozen_at:
        raise ValueError("model, harness, and frozen_at are required")
    if (
        normalized_role is StudyRole.VERIFICATION_REPAIR
        and not harness.startswith("deliberate-verify-v1@")
    ):
        raise ValueError("verification repair requires deliberate-verify-v1 harness")
    if (
        normalized_role is StudyRole.INTERFACE_GUIDE
        and not harness.startswith("interface-guide-v1@")
    ):
        raise ValueError("interface guide role requires interface-guide-v1 harness")
    if any(len(value) != 64 for value in (protocol_artifact_hash, model_panel_hash)):
        raise ValueError("protocol and model-panel hashes must be SHA-256 digests")
    static_hash = _audit_hash(
        static_audit, "capacity-surface-static-construct-v4"
    )
    runtime_hash = _audit_hash(
        runtime_audit, "capacity-surface-runtime-construct-v4"
    )
    if static_audit.get("split") != normalized.value:
        raise ValueError("static audit split does not match run plan")
    if runtime_audit.get("split") != normalized.value:
        raise ValueError("runtime audit split does not match run plan")
    admission_hash = _admission_hash(
        admission_audit,
        split=normalized,
        protocol_artifact_hash=protocol_artifact_hash,
        model_panel_hash=model_panel_hash,
    )
    return RunPlan(
        protocol_version=PROTOCOL_VERSION,
        split=normalized,
        model=model,
        harness=harness,
        study_role=normalized_role,
        frozen_at=frozen_at,
        source_hash=_source_hash(),
        protocol_artifact_hash=protocol_artifact_hash,
        model_panel_hash=model_panel_hash,
        execution=execution,
        task_manifest_hash=stable_hash(json.loads(canonical_task_manifest(normalized))),
        static_audit_hash=static_hash,
        runtime_audit_hash=runtime_hash,
        admission_audit_hash=admission_hash,
        conditions=_conditions(normalized, model, harness, normalized_role),
    )


def build_discovery_plan(
    *,
    split: Split | str,
    model: str,
    harness: str,
    frozen_at: str,
    protocol_artifact_hash: str,
    model_panel_hash: str,
    execution: ExecutionProfile,
    construct_audit: Mapping[str, object],
    admission_audit: Mapping[str, object] | None = None,
) -> DiscoveryRunPlan:
    if not model or not harness or not frozen_at:
        raise ValueError("model, harness, and frozen_at are required")
    if any(len(value) != 64 for value in (protocol_artifact_hash, model_panel_hash)):
        raise ValueError("protocol and model-panel hashes must be SHA-256 digests")
    normalized = Split(split)
    if normalized is Split.TRANSFER:
        raise ValueError("discovery supports development and main splits only")
    if construct_audit.get("split") != normalized.value:
        raise ValueError("discovery construct audit split does not match run plan")
    audit_hash = _audit_hash(
        construct_audit, "capacity-surface-discovery-construct-v3"
    )
    admission_hash = _admission_hash(
        admission_audit,
        split=normalized,
        protocol_artifact_hash=protocol_artifact_hash,
        model_panel_hash=model_panel_hash,
    )
    cases = discovery_cases(normalized)
    return DiscoveryRunPlan(
        protocol_version=PROTOCOL_VERSION,
        split=normalized,
        model=model,
        harness=harness,
        frozen_at=frozen_at,
        source_hash=_source_hash(),
        protocol_artifact_hash=protocol_artifact_hash,
        model_panel_hash=model_panel_hash,
        execution=execution,
        case_manifest_hash=stable_hash([case.case_hash for case in cases]),
        construct_audit_hash=audit_hash,
        admission_audit_hash=admission_hash,
        conditions=_discovery_conditions(normalized, model, harness),
    )


def _atomic_write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(canonical_json(payload) + "\n", encoding="utf-8")
    temporary.replace(path)


def freeze_plan(path: Path, plan: RunPlan) -> None:
    _atomic_write(path, plan.to_json())


def freeze_discovery_plan(path: Path, plan: DiscoveryRunPlan) -> None:
    _atomic_write(path, plan.to_json())


def load_plan(path: Path) -> RunPlan:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load run plan: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("run plan root must be an object")
    return RunPlan.from_json(payload)


def load_discovery_plan(path: Path) -> DiscoveryRunPlan:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load discovery run plan: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("discovery run plan root must be an object")
    return DiscoveryRunPlan.from_json(payload)


def _validate_live_plan(plan: RunPlan) -> None:
    if plan.protocol_version != PROTOCOL_VERSION:
        raise ValueError("protocol version mismatch")
    if plan.source_hash != _source_hash():
        raise ValueError("live source hash does not match frozen plan")
    manifest_hash = stable_hash(json.loads(canonical_task_manifest(plan.split)))
    if plan.task_manifest_hash != manifest_hash:
        raise ValueError("live task manifest does not match frozen plan")
    if plan.conditions != _conditions(
        plan.split, plan.model, plan.harness, plan.study_role
    ):
        raise ValueError("condition matrix does not match frozen generator")


def _validate_live_discovery_plan(plan: DiscoveryRunPlan) -> None:
    if plan.protocol_version != PROTOCOL_VERSION:
        raise ValueError("protocol version mismatch")
    if plan.source_hash != _source_hash():
        raise ValueError("live source hash does not match frozen discovery plan")
    case_hash = stable_hash([case.case_hash for case in discovery_cases(plan.split)])
    if plan.case_manifest_hash != case_hash:
        raise ValueError("live discovery case manifest does not match frozen plan")
    if plan.conditions != _discovery_conditions(plan.split, plan.model, plan.harness):
        raise ValueError("discovery condition matrix does not match frozen generator")


def execute_plan(
    plan: RunPlan,
    client: CompletionClient,
    output_path: Path,
    *,
    max_conditions: int | None = None,
    workers: int | None = None,
) -> JSON:
    """Execute plan cells once with conflict-detecting resume semantics."""

    _validate_live_plan(plan)
    workers = plan.execution.workers if workers is None else workers
    if workers != plan.execution.workers:
        raise ValueError("runtime workers do not match the frozen execution profile")
    tasks = {task.task_id: task for task in tasks_for_split(plan.split)}
    selected: Sequence[CapacityCondition] = plan.conditions
    if max_conditions is not None:
        if max_conditions <= 0:
            raise ValueError("max_conditions must be positive")
        selected = selected[:max_conditions]
    store = ExperimentStore(output_path)
    planned_ids = {condition.condition_id for condition in plan.conditions}
    for row in store.rows:
        if row.get("plan_hash") != plan.plan_hash:
            raise ValueError("existing row belongs to a different plan")
        if row.get("condition_id") not in planned_ids:
            raise ValueError("existing row is absent from the run plan")
        if row.get("attempt") != 1:
            raise ValueError("existing row attempt must equal one")
    executed = 0
    skipped = 0
    pending = [
        condition
        for condition in selected
        if condition.condition_id not in store.completed_condition_ids
    ]
    skipped = len(selected) - len(pending)
    policy = plan.execution.episode_policy
    inflight_lock = Lock()
    inflight = 0
    peak_inflight = 0

    def run(condition: CapacityCondition):
        if policy is None:
            # Preserve the semantics of archived plans that predate an explicit
            # episode policy.
            return run_episode(condition, tasks[condition.task_id], client)
        return run_episode(
            condition,
            tasks[condition.task_id],
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

    def run_scheduled(condition: CapacityCondition):
        nonlocal inflight, peak_inflight
        started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with inflight_lock:
            inflight += 1
            inflight_at_start = inflight
            peak_inflight = max(peak_inflight, inflight)
        try:
            episode = run(condition)
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

    with ThreadPoolExecutor(max_workers=workers) as executor:
        for offset in range(0, len(pending), workers):
            batch = pending[offset : offset + workers]
            futures = {executor.submit(run_scheduled, condition) for condition in batch}
            batch_error: BaseException | None = None
            for future in as_completed(futures):
                try:
                    episode, executor_metadata = future.result()
                except BaseException as exc:
                    batch_error = batch_error or exc
                    continue
                store.append(
                    {
                        **episode.to_json(),
                        "executor": executor_metadata,
                        "plan_hash": plan.plan_hash,
                        "study_role": plan.study_role.value,
                        "attempt": 1,
                    }
                )
                executed += 1
            if batch_error is not None:
                raise batch_error
    return {"planned": len(selected), "executed": executed, "skipped": skipped}


def execute_discovery_plan(
    plan: DiscoveryRunPlan,
    client: CompletionClient,
    output_path: Path,
    *,
    max_conditions: int | None = None,
    workers: int | None = None,
) -> JSON:
    _validate_live_discovery_plan(plan)
    workers = plan.execution.workers if workers is None else workers
    if workers != plan.execution.workers:
        raise ValueError("runtime workers do not match the frozen execution profile")
    cases = {case.case_id: case for case in discovery_cases(plan.split)}
    selected: Sequence[DiscoveryCondition] = plan.conditions
    if max_conditions is not None:
        if max_conditions <= 0:
            raise ValueError("max_conditions must be positive")
        selected = selected[:max_conditions]
    store = ExperimentStore(output_path)
    planned_ids = {condition.condition_id for condition in plan.conditions}
    for row in store.rows:
        if row.get("plan_hash") != plan.plan_hash:
            raise ValueError("existing discovery row belongs to a different plan")
        if row.get("condition_id") not in planned_ids:
            raise ValueError("existing discovery row is absent from the run plan")
        if row.get("attempt") != 1:
            raise ValueError("existing discovery row attempt must equal one")
    executed = 0
    skipped = 0
    pending = [
        condition
        for condition in selected
        if condition.condition_id not in store.completed_condition_ids
    ]
    skipped = len(selected) - len(pending)
    inflight_lock = Lock()
    inflight = 0
    peak_inflight = 0

    def run_scheduled(condition: DiscoveryCondition):
        nonlocal inflight, peak_inflight
        started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with inflight_lock:
            inflight += 1
            inflight_at_start = inflight
            peak_inflight = max(peak_inflight, inflight)
        try:
            episode = run_discovery_episode(
                condition, cases[condition.case_id], client
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

    with ThreadPoolExecutor(max_workers=workers) as executor:
        for offset in range(0, len(pending), workers):
            batch = pending[offset : offset + workers]
            futures = {executor.submit(run_scheduled, condition) for condition in batch}
            batch_error: BaseException | None = None
            for future in as_completed(futures):
                try:
                    episode, executor_metadata = future.result()
                except BaseException as exc:
                    batch_error = batch_error or exc
                    continue
                store.append(
                    {
                        **episode.to_json(),
                        "executor": executor_metadata,
                        "plan_hash": plan.plan_hash,
                        "attempt": 1,
                    }
                )
                executed += 1
            if batch_error is not None:
                raise batch_error
    return {"planned": len(selected), "executed": executed, "skipped": skipped}
