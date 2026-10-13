"""Sealed repeated-request sentinel for layer-selective repair stability."""

from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
from threading import Lock
from typing import Mapping, Sequence

from bottleneckbench import design
from bottleneckbench.design import (
    CapacityCondition,
    SemanticFamily,
    Split,
    WorkflowLoad,
    build_families,
    compile_task,
    visible_menu,
)
from bottleneckbench.experiments import ExecutionProfile, _source_hash
from bottleneckbench.runtime import CompletionClient, reference_execution, run_episode
from bottleneckbench.core.experiments import ExperimentStore
from bottleneckbench.core.schema import JSON, Representation, stable_hash
from bottleneckbench.request_scope import completion_request_scope


SENTINEL_PROTOCOL_VERSION = "capacity-surface-stochasticity-sentinel-v1"
_SELECTED_INTENT_INDICES = (1, 4, 8, 9)
_REPEATS = 3
_CELLS = (
    ("visible_routing", "stress", "fixed", WorkflowLoad.LOW, Representation.RAW, 128),
    ("visible_routing", "repair", "routed", WorkflowLoad.LOW, Representation.RAW, 128),
    ("workflow_verification", "stress", "routed", WorkflowLoad.HIGH, Representation.RAW, 8),
    ("workflow_verification", "repair", "verification", WorkflowLoad.HIGH, Representation.RAW, 8),
    ("interface_guide", "stress", "routed", WorkflowLoad.LOW, Representation.LINKED, 8),
    ("interface_guide", "repair", "interface", WorkflowLoad.LOW, Representation.LINKED, 8),
)
_HARNESS_KEYS = {cell[2] for cell in _CELLS}


def sentinel_families() -> tuple[SemanticFamily, ...]:
    families = []
    for intent_index in _SELECTED_INTENT_INDICES:
        intent = design._INTENTS[intent_index]
        seed = 900 + intent_index
        outcome_variant = intent_index % 3
        prompt_variant = (intent_index + 1) % 3
        facts = design._facts(intent, seed, outcome_variant)
        families.append(
            SemanticFamily(
                family_id=f"sentinel-{stable_hash({'intent': intent, 'seed': seed})[:12]}",
                split=Split.MAIN,
                seed=seed,
                case_id=f"sealed-{stable_hash({'sentinel': intent_index})[:12]}",
                intent=intent,
                connectors=tuple(design._CORE_CONNECTORS_BY_INTENT[intent]),
                facts=facts,
                decision=design._decision(intent, dict(facts)),
                outcome_variant=outcome_variant,
                prompt_variant=prompt_variant,
                branching=intent_index % 2 == 1,
                action_name=design._ACTION_BY_INTENT.get(intent),
            )
        )
    return tuple(families)


def sentinel_tasks() -> tuple[object, ...]:
    return tuple(
        compile_task(family, workflow)
        for family in sentinel_families()
        for workflow in (WorkflowLoad.LOW, WorkflowLoad.HIGH)
    )


def sentinel_task_manifest_hash() -> str:
    return stable_hash(
        {
            "protocol": SENTINEL_PROTOCOL_VERSION,
            "selected_intent_indices": list(_SELECTED_INTENT_INDICES),
            "tasks": [task.hidden_payload() for task in sentinel_tasks()],
            "cells": [
                [pair, arm, harness, workflow.value, representation.value, visible]
                for pair, arm, harness, workflow, representation, visible in _CELLS
            ],
        }
    )


def _inference_seed(family_id: str, repeat: int) -> int:
    return int(stable_hash([SENTINEL_PROTOCOL_VERSION, family_id, repeat])[:8], 16) & 0x7FFFFFFF


def sentinel_conditions(model: str, harnesses: Mapping[str, str]) -> tuple[JSON, ...]:
    prefixes = {
        "routed": "react-routed@",
        "fixed": "react-fixed-menu@",
        "verification": "deliberate-verify-v1@",
        "interface": "interface-guide-v1@",
    }
    if set(harnesses) != _HARNESS_KEYS or any(
        not str(harnesses[key]).startswith(prefix) for key, prefix in prefixes.items()
    ):
        raise ValueError("sentinel requires routed, fixed, verification, and interface harnesses")
    tasks = {
        (task.family.family_id, task.workflow): task for task in sentinel_tasks()
    }
    conditions: list[JSON] = []
    for family in sentinel_families():
        for repeat in range(1, _REPEATS + 1):
            inference_seed = _inference_seed(family.family_id, repeat)
            for pair, arm, harness_key, workflow, representation, visible in _CELLS:
                task = tasks[(family.family_id, workflow)]
                base = CapacityCondition(
                    task_id=task.task_id,
                    family_id=family.family_id,
                    intent=family.intent,
                    outcome_variant=family.outcome_variant,
                    prompt_variant=family.prompt_variant,
                    branching=family.branching,
                    action_required=family.action_required,
                    model=model,
                    harness=harnesses[harness_key],
                    workflow=workflow,
                    representation=representation,
                    visible_tools=visible,
                    seed=family.seed,
                )
                condition = {
                    **base.to_json(),
                    "pair": pair,
                    "arm": arm,
                    "harness_key": harness_key,
                    "repeat": repeat,
                    "inference_seed": inference_seed,
                }
                condition["condition_id"] = stable_hash(condition)
                conditions.append(condition)
    if len(conditions) != 72 or len({item["condition_id"] for item in conditions}) != 72:
        raise AssertionError("sentinel condition matrix must contain 72 unique rows")
    return tuple(conditions)


def sentinel_construct_audit() -> JSON:
    families = sentinel_families()
    tasks = sentinel_tasks()
    existing = [family for split in Split for family in build_families(split)]
    hidden_terms = {
        term.casefold()
        for task in tasks
        for term in (
            task.task_id,
            task.family.family_id,
            *(node.record_id for node in task.nodes),
            *(query for node in task.nodes for query in node.next_queries),
        )
    }
    conditions = sentinel_conditions(
        "audit-model",
        {
            "routed": "react-routed@audit",
            "fixed": "react-fixed-menu@audit",
            "verification": "deliberate-verify-v1@audit",
            "interface": "interface-guide-v1@audit",
        },
    )
    pair_counts: dict[tuple[str, str, int], int] = defaultdict(int)
    for condition in conditions:
        pair_counts[(str(condition["family_id"]), str(condition["pair"]), int(condition["repeat"]))] += 1
    checks = [
        {
            "name": "family_isolation",
            "passed": not ({family.family_id for family in families} & {family.family_id for family in existing})
            and not ({family.case_id for family in families} & {family.case_id for family in existing}),
            "details": {"families": len(families)},
        },
        {
            "name": "static_balance",
            "passed": sum(family.positive_outcome for family in families) == 2
            and sum(family.action_required for family in families) == 2
            and sum(family.branching for family in families) == 2
            and {family.outcome_variant for family in families} == {0, 1, 2}
            and {family.prompt_variant for family in families} == {0, 1, 2},
            "details": {"intent_indices": list(_SELECTED_INTENT_INDICES)},
        },
        {
            "name": "semantic_twin_and_reference_validity",
            "passed": all(
                len({task.family.semantic_signature for task in tasks if task.family.family_id == family.family_id}) == 1
                for family in families
            )
            and all(reference_execution(task)[3].verified_success for task in tasks),
            "details": {"tasks": len(tasks)},
        },
        {
            "name": "menu_and_gold_isolation",
            "passed": all(
                set(tool.name for tool in visible_menu(task, 8))
                < set(tool.name for tool in visible_menu(task, 128))
                for task in tasks
            )
            and not any(term in task.request.casefold() for task in tasks for term in hidden_terms),
            "details": {"visible_levels": [8, 128]},
        },
        {
            "name": "paired_repeat_closure",
            "passed": len(conditions) == 72
            and len(pair_counts) == 36
            and set(pair_counts.values()) == {2}
            and all(
                len(
                    {
                        int(item["inference_seed"])
                        for item in conditions
                        if item["family_id"] == family.family_id
                    }
                )
                == 3
                for family in families
            ),
            "details": {"conditions_per_model": len(conditions), "repeats": _REPEATS},
        },
    ]
    return {
        "audit": "capacity-surface-stochasticity-sentinel-construct-v1",
        "passed": all(check["passed"] is True for check in checks),
        "task_manifest_hash": sentinel_task_manifest_hash(),
        "checks": checks,
    }


def build_sentinel_plan(
    *,
    model: str,
    harnesses: Mapping[str, str],
    frozen_at: str,
    protocol_artifact_hash: str,
    model_panel_hash: str,
    execution: ExecutionProfile,
    construct_audit: Mapping[str, object],
    transport_artifact_hashes: Mapping[str, str],
) -> JSON:
    if (
        construct_audit.get("audit") != "capacity-surface-stochasticity-sentinel-construct-v1"
        or construct_audit.get("passed") is not True
        or construct_audit.get("task_manifest_hash") != sentinel_task_manifest_hash()
    ):
        raise ValueError("passing sentinel construct audit required")
    if any(len(value) != 64 for value in (protocol_artifact_hash, model_panel_hash, *transport_artifact_hashes.values())):
        raise ValueError("sentinel plan hashes must be SHA-256 digests")
    payload: JSON = {
        "protocol_version": SENTINEL_PROTOCOL_VERSION,
        "model": model,
        "harnesses": dict(sorted(harnesses.items())),
        "provider_seed_controlled": not execution.omit_seed,
        "frozen_at": frozen_at,
        "source_hash": _source_hash(),
        "protocol_artifact_hash": protocol_artifact_hash,
        "model_panel_hash": model_panel_hash,
        "task_manifest_hash": sentinel_task_manifest_hash(),
        "construct_audit_hash": stable_hash(dict(construct_audit)),
        "transport_artifact_hashes": dict(sorted(transport_artifact_hashes.items())),
        "execution": execution.to_json(),
        "conditions": list(sentinel_conditions(model, harnesses)),
    }
    payload["plan_hash"] = stable_hash(payload)
    return payload


def validate_sentinel_plan(plan: Mapping[str, object]) -> None:
    unhashed = dict(plan)
    plan_hash = unhashed.pop("plan_hash", None)
    harnesses = plan.get("harnesses")
    conditions = plan.get("conditions")
    if (
        plan_hash != stable_hash(unhashed)
        or plan.get("protocol_version") != SENTINEL_PROTOCOL_VERSION
        or plan.get("source_hash") != _source_hash()
        or plan.get("task_manifest_hash") != sentinel_task_manifest_hash()
        or not isinstance(harnesses, dict)
        or not isinstance(conditions, list)
        or conditions != list(sentinel_conditions(str(plan.get("model")), harnesses))
    ):
        raise ValueError("sentinel plan does not match its frozen generator")
    ExecutionProfile.from_json(plan.get("execution"))


def load_sentinel_plan(path: Path) -> JSON:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("sentinel plan root must be an object")
    validate_sentinel_plan(value)
    return value


def _capacity_condition(condition: Mapping[str, object]) -> CapacityCondition:
    return CapacityCondition(
        task_id=str(condition["task_id"]),
        family_id=str(condition["family_id"]),
        intent=str(condition["intent"]),
        outcome_variant=int(condition["outcome_variant"]),
        prompt_variant=int(condition["prompt_variant"]),
        branching=bool(condition["branching"]),
        action_required=bool(condition["action_required"]),
        model=str(condition["model"]),
        harness=str(condition["harness"]),
        workflow=WorkflowLoad(str(condition["workflow"])),
        representation=Representation(str(condition["representation"])),
        visible_tools=int(condition["visible_tools"]),
        seed=int(condition["seed"]),
    )


def execute_sentinel_plan(
    plan: Mapping[str, object],
    client: CompletionClient,
    output_path: Path,
    *,
    max_conditions: int | None = None,
) -> JSON:
    validate_sentinel_plan(plan)
    execution = ExecutionProfile.from_json(plan["execution"])
    policy = execution.episode_policy
    if policy is None or policy.dynamic_shortlist is not True:
        raise ValueError("sentinel requires the frozen routed episode policy")
    conditions = list(plan["conditions"])
    if max_conditions is not None:
        if max_conditions <= 0:
            raise ValueError("max_conditions must be positive")
        conditions = conditions[:max_conditions]
    tasks = {task.task_id: task for task in sentinel_tasks()}
    store = ExperimentStore(output_path)
    planned_ids = {str(item["condition_id"]) for item in plan["conditions"]}
    for row in store.rows:
        if row.get("plan_hash") != plan["plan_hash"] or row.get("condition_id") not in planned_ids:
            raise ValueError("existing sentinel row is outside the frozen plan")
    pending = [item for item in conditions if item["condition_id"] not in store.completed_condition_ids]
    lock = Lock()
    inflight = 0
    peak = 0

    def run(condition: Mapping[str, object]):
        nonlocal inflight, peak
        started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with lock:
            inflight += 1
            at_start = inflight
            peak = max(peak, inflight)
        base = _capacity_condition(condition)
        try:
            with completion_request_scope(
                client,
                condition_id=str(condition["condition_id"]),
                repeat=int(condition["repeat"]),
                attempt=1,
            ):
                episode = run_episode(
                    base,
                    tasks[base.task_id],
                    client,
                    inference_seed=int(condition["inference_seed"]),
                    max_turns=None,
                    max_tool_calls=None,
                    tool_call_slack=policy.tool_call_slack,
                    turn_slack=policy.turn_slack,
                    max_total_tokens=policy.max_total_tokens,
                    max_completion_tokens=policy.max_completion_tokens,
                    dynamic_shortlist=condition["harness_key"] != "fixed",
                    early_stop_infeasible=policy.early_stop_infeasible,
                )
        finally:
            with lock:
                inflight -= 1
        completed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        row = episode.to_json()
        row["base_condition_id"] = row["condition_id"]
        row["condition"] = dict(condition)
        row["condition_id"] = condition["condition_id"]
        row["sampling"] = {
            "repeat": condition["repeat"],
            "inference_seed": condition["inference_seed"],
            "provider_seed_controlled": plan["provider_seed_controlled"],
        }
        row["executor"] = {
            "started_at": started_at,
            "completed_at": completed_at,
            "worker_limit": execution.workers,
            "inflight_at_start": at_start,
            "observed_peak_inflight": peak,
        }
        row["plan_hash"] = plan["plan_hash"]
        row["study_role"] = "stochasticity_sentinel"
        row["attempt"] = 1
        return row

    executed = 0
    with ThreadPoolExecutor(max_workers=execution.workers) as executor:
        for offset in range(0, len(pending), execution.workers):
            futures = [executor.submit(run, item) for item in pending[offset : offset + execution.workers]]
            rows = [future.result() for future in as_completed(futures)]
            for row in rows:
                store.append(row)
                executed += 1
    return {"planned": len(conditions), "executed": executed, "skipped": len(conditions) - len(pending)}


def analyze_sentinel_rows(rows: Sequence[Mapping[str, object]]) -> JSON:
    groups: dict[tuple[str, str, str, str], list[bool]] = defaultdict(list)
    paired: dict[tuple[str, str, int, str], dict[str, bool]] = defaultdict(dict)
    for row in rows:
        condition = row.get("condition")
        verdict = row.get("verdict")
        if not isinstance(condition, dict) or not isinstance(verdict, dict):
            raise ValueError("sentinel row is missing condition or verdict")
        success = verdict.get("verified_success") is True
        key = (
            str(condition["model"]),
            str(condition["family_id"]),
            str(condition["pair"]),
            str(condition["arm"]),
        )
        groups[key].append(success)
        paired[(key[0], key[1], int(condition["repeat"]), key[2])][key[3]] = success
    if not groups or set(map(len, groups.values())) != {_REPEATS} or any(set(value) != {"stress", "repair"} for value in paired.values()):
        raise ValueError("sentinel rows do not close every repeated repair pair")
    by_model_pair: dict[tuple[str, str, int], list[float]] = defaultdict(list)
    for (model, _family, repeat, pair), value in paired.items():
        by_model_pair[(model, pair, repeat)].append(float(value["repair"]) - float(value["stress"]))
    profiles: JSON = {}
    for (model, pair, repeat), values in sorted(by_model_pair.items()):
        profiles.setdefault(model, {}).setdefault(pair, {"repeat_gains": {}})["repeat_gains"][str(repeat)] = sum(values) / len(values)
    for model in profiles:
        for pair, profile in profiles[model].items():
            gains = list(profile["repeat_gains"].values())
            profile["mean_repair_gain"] = sum(gains) / len(gains)
            profile["gain_sign_stable"] = all(value >= 0 for value in gains) or all(value <= 0 for value in gains)
    return {
        "analysis": "capacity-surface-stochasticity-sentinel-v1",
        "rows": len(rows),
        "models": profiles,
        "condition_groups": len(groups),
        "pass_at_one": sum(sum(values) for values in groups.values()) / (len(groups) * _REPEATS),
        "pass_all_three": sum(all(values) for values in groups.values()) / len(groups),
        "pass_at_least_one": sum(any(values) for values in groups.values()) / len(groups),
        "disagreement_rate": sum(len(set(values)) > 1 for values in groups.values()) / len(groups),
    }
