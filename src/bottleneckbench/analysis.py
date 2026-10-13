"""Design-based summaries for the counterfactual capacity surface."""

from __future__ import annotations

from collections import Counter, defaultdict
import json
from math import sqrt
import random
from statistics import NormalDist, mean, stdev
from typing import Callable, Mapping, Sequence

from bottleneckbench.core.analysis import holm_adjust
from bottleneckbench.core.schema import JSON, stable_hash
from bottleneckbench.study_design import (
    EXPLORATORY_CONTRASTS,
    PRIMARY_CONTRASTS,
    StudyRole,
    contrast_value,
)


_PAIRED_SPECIFICATIONS = {
    "visible_128_minus_8": ("visible_tools", 128, 8),
    "workflow_high_minus_low": ("workflow", "high", "low"),
    "compact_minus_raw": ("representation", "generic_compact", "raw"),
    "typed_minus_raw": ("representation", "typed", "raw"),
    "linked_minus_raw": ("representation", "linked", "raw"),
}
_INTERACTION_SPECIFICATIONS = {
    "visible_by_workflow": ("visible_tools", 128, 8, "workflow", "high", "low"),
    "visible_by_linked": ("visible_tools", 128, 8, "representation", "linked", "raw"),
    "workflow_by_linked": ("workflow", "high", "low", "representation", "linked", "raw"),
}
_ACTION_INTENTS = {
    "draft_followup",
    "mark_reviewed",
    "schedule_review",
    "create_case_note",
    "create_meeting_brief",
    "draft_reconciliation_query",
}
_MALFORMED_TOOL_ARGUMENTS = "provider_failure:tool call arguments are invalid JSON"


def _condition(row: Mapping[str, object]) -> Mapping[str, object]:
    value = row.get("condition")
    if not isinstance(value, dict):
        raise ValueError("run row is missing a condition object")
    return value


def _validate_unique(rows: Sequence[Mapping[str, object]]) -> None:
    identifiers = [row.get("condition_id") for row in rows]
    if any(not isinstance(value, str) or not value for value in identifiers):
        raise ValueError("every run row requires a condition_id")
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("analysis input contains duplicate condition IDs")


def _success(row: Mapping[str, object]) -> float:
    verdict = row.get("verdict")
    if not isinstance(verdict, dict):
        raise ValueError("capacity row is missing a verdict")
    return float(verdict.get("verified_success") is True)


def is_infrastructure_failure(row: Mapping[str, object]) -> bool:
    """Separate transport/service failures from malformed model tool output."""

    errors = row.get("errors")
    malformed_arguments = isinstance(errors, list) and _MALFORMED_TOOL_ARGUMENTS in errors
    return row.get("status") == "provider_failure" and not malformed_arguments


def _analysis_status(row: Mapping[str, object]) -> str:
    if row.get("status") == "provider_failure" and not is_infrastructure_failure(row):
        return "agent_failure"
    return str(row.get("status"))


def _failure_stage(row: Mapping[str, object]) -> str:
    verdict = row.get("verdict")
    if isinstance(verdict, dict) and verdict.get("verified_success") is True:
        return "success"
    if row.get("status") == "provider_failure" and not is_infrastructure_failure(row):
        return "arguments"
    attempts = row.get("call_attempts")
    if isinstance(attempts, list):
        for attempt in attempts:
            if not isinstance(attempt, dict):
                continue
            outcome = attempt.get("outcome")
            error = attempt.get("error")
            if outcome == "unexposed":
                return "selection"
            if outcome == "repeat_blocked":
                return "completion"
            if outcome == "budget_blocked":
                return "budget"
            if outcome == "tool_error":
                return (
                    "arguments"
                    if error in {"InvalidArguments", "RecordNotFound"}
                    else "execution"
                )
    errors = row.get("errors")
    if isinstance(errors, list):
        contract_errors = [
            str(error)
            for error in errors
            if str(error).startswith("model_response_contract:")
        ]
        if any("arguments_" in error for error in contract_errors):
            return "arguments"
        if contract_errors:
            return "selection"
    if isinstance(errors, list) and any(
        error in {"model_finish_length", "turn_limit_exceeded", "repeated_tool_call"}
        for error in errors
    ):
        return "completion"
    return str(verdict.get("failure_stage") or "success") if isinstance(verdict, dict) else "unknown"


def _answer_wrapper_normalized(row: Mapping[str, object]) -> bool:
    answer = row.get("answer")
    verdict = row.get("verdict")
    if not isinstance(answer, str) or not isinstance(verdict, dict) or verdict.get("answer") is not True:
        return False
    try:
        json.loads(answer)
    except json.JSONDecodeError:
        return True
    return False


def _tokens(row: Mapping[str, object]) -> float:
    usage = row.get("usage")
    value = usage.get("total_tokens") if isinstance(usage, dict) else None
    if not isinstance(value, (int, float)):
        raise ValueError("run row is missing total token usage")
    return float(value)


def _response_usage_counts(row: Mapping[str, object]) -> tuple[int, int]:
    response_ids = row.get("response_ids")
    if isinstance(response_ids, list):
        response_count = len(response_ids)
        raw_sources = row.get("usage_sources")
        sources = raw_sources if isinstance(raw_sources, list) else []
    else:
        response_id = row.get("response_id")
        response_count = int(isinstance(response_id, str) and bool(response_id))
        raw_source = row.get("usage_source")
        sources = [raw_source] if response_count and raw_source is not None else []
    missing = sum(source == "stream_usage_missing" for source in sources)
    return response_count, missing


def _token_telemetry_complete(row: Mapping[str, object]) -> bool:
    _, missing = _response_usage_counts(row)
    return missing == 0


def _efficiency_summary(rows: Sequence[Mapping[str, object]]) -> JSON:
    """Summarize provider-neutral work while retaining legacy-row support."""

    requests: list[float] = []
    exposures: list[float] = []
    schema_bytes: list[float] = []
    wall_seconds: list[float] = []
    worker_limits: set[int] = set()
    observed_peaks: list[int] = []
    early_stops = 0
    for row in rows:
        efficiency = row.get("efficiency")
        if isinstance(efficiency, dict):
            request_value = efficiency.get("model_requests_attempted")
            exposure_value = efficiency.get("tool_schema_exposures")
            schema_value = efficiency.get("tool_schema_bytes")
            wall_value = efficiency.get("wall_time_seconds")
            early_stops += efficiency.get("early_stop_reason") is not None
        else:
            request_value = None
            exposure_value = None
            schema_value = None
            wall_value = None
        response_ids = row.get("response_ids")
        request_count = (
            float(request_value)
            if isinstance(request_value, (int, float))
            else float(len(response_ids))
            if isinstance(response_ids, list)
            else 0.0
        )
        requests.append(request_count)
        if isinstance(exposure_value, (int, float)):
            exposures.append(float(exposure_value))
        else:
            visible = _condition(row).get("visible_tools")
            exposures.append(
                request_count * float(visible)
                if isinstance(visible, (int, float))
                else 0.0
            )
        if isinstance(schema_value, (int, float)):
            schema_bytes.append(float(schema_value))
        if isinstance(wall_value, (int, float)):
            wall_seconds.append(float(wall_value))
        executor = row.get("executor")
        if isinstance(executor, dict):
            worker_limit = executor.get("worker_limit")
            observed_peak = executor.get("observed_peak_inflight")
            if isinstance(worker_limit, int):
                worker_limits.add(worker_limit)
            if isinstance(observed_peak, int):
                observed_peaks.append(observed_peak)
    total_tokens = sum(_tokens(row) for row in rows)
    response_usage = [_response_usage_counts(row) for row in rows]
    response_count = sum(total for total, _ in response_usage)
    missing_usage = sum(missing for _, missing in response_usage)
    token_complete = missing_usage == 0
    observed_token_efficiency = (
        sum(_success(row) for row in rows) * 1_000_000 / total_tokens
        if total_tokens > 0
        else None
    )
    return {
        "mean_model_requests": mean(requests),
        "mean_tool_schema_exposures": mean(exposures),
        "mean_tool_schema_bytes": mean(schema_bytes) if schema_bytes else None,
        "mean_wall_time_seconds": mean(wall_seconds) if wall_seconds else None,
        "executor_worker_limits": sorted(worker_limits),
        "maximum_observed_inflight": max(observed_peaks) if observed_peaks else None,
        "early_stop_rate": early_stops / len(rows),
        "token_telemetry_complete": token_complete,
        "response_token_telemetry_coverage": (
            (response_count - missing_usage) / response_count
            if response_count
            else 1.0
        ),
        "responses_missing_usage": missing_usage,
        "verified_successes_per_million_tokens": (
            observed_token_efficiency if token_complete else None
        ),
        "verified_successes_per_million_observed_tokens": observed_token_efficiency,
    }


def _row_work_metric(row: Mapping[str, object], name: str) -> float:
    efficiency = row.get("efficiency")
    value = efficiency.get(name) if isinstance(efficiency, dict) else None
    if isinstance(value, (int, float)):
        return float(value)
    response_ids = row.get("response_ids")
    requests = float(len(response_ids)) if isinstance(response_ids, list) else 0.0
    if name == "model_requests_attempted":
        return requests
    if name == "tool_schema_exposures":
        visible = _condition(row).get("visible_tools")
        return requests * float(visible) if isinstance(visible, (int, float)) else 0.0
    raise ValueError(f"row does not report efficiency metric: {name}")


def _repeated_attempts(row: Mapping[str, object]) -> int:
    seen: set[tuple[str, str]] = set()
    repeated = 0
    attempts = row.get("call_attempts")
    if not isinstance(attempts, list):
        return 0
    for attempt in attempts:
        if not isinstance(attempt, dict):
            continue
        signature = (
            str(attempt.get("tool")),
            json.dumps(attempt.get("arguments"), sort_keys=True, separators=(",", ":")),
        )
        repeated += signature in seen
        seen.add(signature)
    return repeated


def _bootstrap(
    values: Sequence[float], *, samples: int, seed: int
) -> JSON:
    if not values or samples <= 0:
        raise ValueError("bootstrap requires values and a positive sample count")
    observed = mean(values)
    rng = random.Random(seed)
    size = len(values)
    draws = sorted(
        mean(rng.choices(values, k=size)) for _ in range(samples)
    )
    centered = [value - observed for value in values]
    extreme = sum(
        abs(mean(rng.choices(centered, k=size))) >= abs(observed)
        for _ in range(samples)
    )
    return {
        "estimate": observed,
        "ci95": [
            draws[int(0.025 * (samples - 1))],
            draws[int(0.975 * (samples - 1))],
        ],
        "bootstrap_p_two_sided": (extreme + 1) / (samples + 1),
        "clusters": size,
        "bootstrap_samples": samples,
    }


def _sign_flip_p_one_sided(
    values: Sequence[float], *, samples: int, seed: int
) -> float:
    if not values or samples <= 0:
        raise ValueError("randomization test requires values and positive samples")
    observed = mean(values)
    rng = random.Random(seed)
    extreme = sum(
        mean(value if rng.getrandbits(1) else -value for value in values)
        >= observed
        for _ in range(samples)
    )
    return (extreme + 1) / (samples + 1)


_REPAIR_SPECIFICATIONS = {
    "verification": (
        StudyRole.VERIFICATION_REPAIR,
        (8, "low", "raw"),
        (8, "high", "raw"),
    ),
    "interface_guide": (
        StudyRole.INTERFACE_GUIDE,
        (8, "low", "raw"),
        (8, "low", "linked"),
    ),
}


def analyze_repair_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    bootstrap_samples: int = 10000,
    randomization_samples: int = 10000,
    seed: int = 20260813,
) -> JSON:
    """Estimate prospective stress-by-matched-repair interactions."""

    _validate_unique(rows)
    effects: JSON = {}
    for name, (repair_role, low_coordinate, stress_coordinate) in _REPAIR_SPECIFICATIONS.items():
        cells: dict[tuple[StudyRole, str, str, tuple[int, str, str]], Mapping[str, object]] = {}
        for row in rows:
            role = _study_role(row)
            if role not in {StudyRole.PRIMARY, repair_role}:
                continue
            condition = _condition(row)
            coordinate = (
                int(condition.get("visible_tools", 0)),
                str(condition.get("workflow")),
                str(condition.get("representation")),
            )
            if coordinate not in {low_coordinate, stress_coordinate}:
                continue
            if is_infrastructure_failure(row):
                raise ValueError(f"{name} contains an infrastructure failure")
            model = condition.get("model")
            family = condition.get("family_id")
            if not all(isinstance(value, str) and value for value in (model, family)):
                raise ValueError(f"{name} row is missing model or family identity")
            key = (role, model, family, coordinate)
            if key in cells:
                raise ValueError(f"{name} contains a duplicate repair cell")
            cells[key] = row

        observed_pairs = {
            (model, family)
            for _, model, family, _ in cells
        }
        metrics_by_family: dict[str, dict[str, list[float]]] = defaultdict(
            lambda: defaultdict(list)
        )
        metrics_by_model: dict[str, dict[str, list[float]]] = defaultdict(
            lambda: defaultdict(list)
        )
        missing: list[str] = []
        for model, family in sorted(observed_pairs):
            required = {
                (role, model, family, coordinate)
                for role in (StudyRole.PRIMARY, repair_role)
                for coordinate in (low_coordinate, stress_coordinate)
            }
            if not required <= set(cells):
                missing.append(f"{model}|{family}")
                continue
            base_low = _success(cells[(StudyRole.PRIMARY, model, family, low_coordinate)])
            base_stress = _success(cells[(StudyRole.PRIMARY, model, family, stress_coordinate)])
            repair_low = _success(cells[(repair_role, model, family, low_coordinate)])
            repair_stress = _success(cells[(repair_role, model, family, stress_coordinate)])
            metrics = {
                "diagnosed_loss": base_low - base_stress,
                "stress_repair_gain": repair_stress - base_stress,
                "low_pressure_repair_gain": repair_low - base_low,
                "repair_selectivity": (repair_stress - base_stress) - (repair_low - base_low),
                "residual_loss": base_low - repair_stress,
            }
            for metric, value in metrics.items():
                metrics_by_family[family][metric].append(value)
                metrics_by_model[model][metric].append(value)
        if missing or not metrics_by_family:
            raise ValueError(f"{name} repair matrix is incomplete: {missing[:5]}")

        summaries: JSON = {}
        for metric in (
            "diagnosed_loss",
            "stress_repair_gain",
            "low_pressure_repair_gain",
            "repair_selectivity",
            "residual_loss",
        ):
            family_values = [
                mean(values[metric]) for _, values in sorted(metrics_by_family.items())
            ]
            summaries[metric] = _bootstrap(
                family_values,
                samples=bootstrap_samples,
                seed=seed + int(stable_hash(f"{name}:{metric}")[:8], 16),
            )
            if metric == "repair_selectivity":
                summaries[metric]["randomization_p_one_sided"] = _sign_flip_p_one_sided(
                    family_values,
                    samples=randomization_samples,
                    seed=seed + int(stable_hash(f"{name}:sign-flip")[:8], 16),
                )
        diagnosed = float(summaries["diagnosed_loss"]["estimate"])
        effects[name] = {
            "repair_role": repair_role.value,
            "low_pressure_cell": list(low_coordinate),
            "stress_cell": list(stress_coordinate),
            "models": len(metrics_by_model),
            "families": len(metrics_by_family),
            "configuration_family_pairs": sum(
                len(values["repair_selectivity"])
                for values in metrics_by_model.values()
            ),
            "metrics": summaries,
            "recoverable_loss_fraction": (
                float(summaries["stress_repair_gain"]["estimate"]) / diagnosed
                if diagnosed > 0
                else None
            ),
            "by_model": {
                model: {
                    metric: mean(values[metric])
                    for metric in summaries
                }
                for model, values in sorted(metrics_by_model.items())
            },
        }
    return {
        "analysis": "layer-selective-repair-v1",
        "rows": len(rows),
        "effects": effects,
        "multiplicity_note": (
            "The two one-sided p-values remain unadjusted here and must enter the "
            "registered four-repair Holm family with retrieval and routing."
        ),
    }


def _other_factors(*excluded: str) -> tuple[str, ...]:
    return tuple(
        name
        for name in ("visible_tools", "workflow", "representation")
        if name not in excluded
    )


def _paired_family_values(
    rows: Sequence[Mapping[str, object]],
    *,
    factor: str,
    treatment: object,
    control: object,
    outcome: Callable[[Mapping[str, object]], float] = _success,
) -> tuple[list[float], int, int, int]:
    cells: dict[tuple[object, ...], dict[object, float]] = defaultdict(dict)
    family_intents: dict[str, str] = {}
    for row in rows:
        condition = _condition(row)
        level = condition.get(factor)
        if level not in {treatment, control}:
            continue
        key = (
            condition.get("family_id"),
            condition.get("model"),
            condition.get("harness"),
            *(condition.get(name) for name in _other_factors(factor)),
        )
        if level in cells[key]:
            raise ValueError("duplicate paired cell")
        cells[key][level] = outcome(row)
        family_id = condition.get("family_id")
        intent = condition.get("intent")
        if not isinstance(family_id, str) or not isinstance(intent, str):
            raise ValueError("capacity condition is missing family_id or intent")
        if family_id in family_intents and family_intents[family_id] != intent:
            raise ValueError("capacity family maps to multiple intents")
        family_intents[family_id] = intent
    contributions: dict[str, list[float]] = defaultdict(list)
    incomplete = 0
    for key, cell in cells.items():
        family_id = key[0]
        if not isinstance(family_id, str):
            raise ValueError("capacity condition is missing family_id")
        if treatment in cell and control in cell:
            contributions[family_id].append(cell[treatment] - cell[control])
        else:
            incomplete += 1
    family_values = {
        family_id: mean(items) for family_id, items in sorted(contributions.items())
    }
    clustered: dict[str, list[float]] = defaultdict(list)
    for family_id, value in family_values.items():
        clustered[family_intents[family_id]].append(value)
    values = [mean(items) for _, items in sorted(clustered.items())]
    return (
        values,
        sum(len(items) for items in contributions.values()),
        incomplete,
        len(family_values),
    )


def _interaction_family_values(
    rows: Sequence[Mapping[str, object]],
    *,
    factor_a: str,
    high_a: object,
    low_a: object,
    factor_b: str,
    high_b: object,
    low_b: object,
) -> tuple[list[float], int, int, int]:
    levels_a = {high_a, low_a}
    levels_b = {high_b, low_b}
    cells: dict[tuple[object, ...], dict[tuple[object, object], float]] = defaultdict(dict)
    remaining = _other_factors(factor_a, factor_b)
    family_intents: dict[str, str] = {}
    for row in rows:
        condition = _condition(row)
        level_a = condition.get(factor_a)
        level_b = condition.get(factor_b)
        if level_a not in levels_a or level_b not in levels_b:
            continue
        key = (
            condition.get("family_id"),
            condition.get("model"),
            condition.get("harness"),
            *(condition.get(name) for name in remaining),
        )
        coordinate = (level_a, level_b)
        if coordinate in cells[key]:
            raise ValueError("duplicate interaction cell")
        cells[key][coordinate] = _success(row)
        family_id = condition.get("family_id")
        intent = condition.get("intent")
        if not isinstance(family_id, str) or not isinstance(intent, str):
            raise ValueError("capacity condition is missing family_id or intent")
        if family_id in family_intents and family_intents[family_id] != intent:
            raise ValueError("capacity family maps to multiple intents")
        family_intents[family_id] = intent
    required = {
        (high_a, high_b),
        (high_a, low_b),
        (low_a, high_b),
        (low_a, low_b),
    }
    contributions: dict[str, list[float]] = defaultdict(list)
    incomplete = 0
    for key, cell in cells.items():
        family_id = key[0]
        if not isinstance(family_id, str):
            raise ValueError("capacity condition is missing family_id")
        if required <= set(cell):
            value = (
                cell[(high_a, high_b)]
                - cell[(low_a, high_b)]
                - cell[(high_a, low_b)]
                + cell[(low_a, low_b)]
            )
            contributions[family_id].append(value)
        else:
            incomplete += 1
    family_values = {
        family_id: mean(items) for family_id, items in sorted(contributions.items())
    }
    clustered: dict[str, list[float]] = defaultdict(list)
    for family_id, value in family_values.items():
        clustered[family_intents[family_id]].append(value)
    values = [mean(items) for _, items in sorted(clustered.items())]
    return (
        values,
        sum(len(items) for items in contributions.values()),
        incomplete,
        len(family_values),
    )


def _study_role(row: Mapping[str, object]) -> StudyRole:
    return StudyRole(str(row.get("study_role", StudyRole.PRIMARY.value)))


def _registered_contrast_values(
    rows: Sequence[Mapping[str, object]],
    terms: Sequence[tuple[tuple[int, str, str], float]],
    *,
    outcome: Callable[[Mapping[str, object]], float] = _success,
    cluster_unit: str = "family",
) -> tuple[list[float], int, int, int]:
    """Evaluate one sparse-design contrast at the declared estimation unit."""

    if cluster_unit not in {"family", "intent"}:
        raise ValueError("cluster_unit must be family or intent")

    required = {coordinate for coordinate, _ in terms}
    cells: dict[
        tuple[str, str, str], dict[tuple[int, str, str], float]
    ] = defaultdict(dict)
    family_intents: dict[str, str] = {}
    for row in rows:
        if _study_role(row) is not StudyRole.PRIMARY:
            continue
        condition = _condition(row)
        coordinate = (
            int(condition.get("visible_tools", 0)),
            str(condition.get("workflow")),
            str(condition.get("representation")),
        )
        if coordinate not in required:
            continue
        family_id = condition.get("family_id")
        model = condition.get("model")
        harness = condition.get("harness")
        intent = condition.get("intent")
        if not all(isinstance(value, str) and value for value in (family_id, model, harness, intent)):
            raise ValueError("capacity contrast row is missing identity metadata")
        key = (family_id, model, harness)
        if coordinate in cells[key]:
            raise ValueError("duplicate registered contrast cell")
        cells[key][coordinate] = outcome(row)
        if family_id in family_intents and family_intents[family_id] != intent:
            raise ValueError("capacity family maps to multiple intents")
        family_intents[family_id] = intent

    contributions: dict[str, list[float]] = defaultdict(list)
    incomplete = 0
    for (family_id, _, _), values in cells.items():
        if required <= set(values):
            contributions[family_id].append(contrast_value(values, terms))
        else:
            incomplete += 1
    family_values = {
        family_id: mean(items) for family_id, items in sorted(contributions.items())
    }
    if cluster_unit == "family":
        values = list(family_values.values())
    else:
        clustered: dict[str, list[float]] = defaultdict(list)
        for family_id, value in family_values.items():
            clustered[family_intents[family_id]].append(value)
        values = [mean(items) for _, items in sorted(clustered.items())]
    return values, sum(map(len, contributions.values())), incomplete, len(family_values)


def _effect_summary(
    name: str,
    values: Sequence[float],
    pairs: int,
    incomplete: int,
    families: int,
    *,
    bootstrap_samples: int,
    seed: int,
) -> JSON:
    result = _bootstrap(
        values,
        samples=bootstrap_samples,
        seed=seed + int(stable_hash(name)[:8], 16),
    )
    result.update(
        {
            "pairs": pairs,
            "incomplete_cells": incomplete,
            "families": families,
            "clusters_improved": sum(value > 0 for value in values),
            "clusters_degraded": sum(value < 0 for value in values),
            "clusters_unchanged": sum(value == 0 for value in values),
        }
    )
    return result


def _descriptive_effects(rows: Sequence[Mapping[str, object]]) -> JSON:
    """Return registered sparse-design contrasts without per-config tests."""

    effects: JSON = {}
    for name, terms in PRIMARY_CONTRASTS.items():
        values, pairs, incomplete, families = _registered_contrast_values(rows, terms)
        effects[name] = (
            {
                "estimate": mean(values),
                "intent_clusters": len(values),
                "families": families,
                "configuration_family_contrasts": pairs,
                "incomplete_cells": incomplete,
            }
            if values
            else {
                "status": "not_identified_in_registered_role",
                "intent_clusters": 0,
                "families": families,
                "configuration_family_contrasts": pairs,
                "incomplete_cells": incomplete,
            }
        )
    return effects


def _profile(
    rows: Sequence[Mapping[str, object]], factor: str
) -> list[JSON]:
    grouped: dict[tuple[str, str, object], list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        condition = _condition(row)
        model = condition.get("model")
        harness = condition.get("harness")
        if not isinstance(model, str) or not isinstance(harness, str):
            raise ValueError("capacity condition is missing model or harness")
        grouped[(model, harness, condition.get(factor))].append(row)
    return [
        {
            "model": model,
            "harness": harness,
            factor: level,
            "rows": len(group),
            "verified_success": mean(_success(row) for row in group),
            "mean_total_tokens": mean(_tokens(row) for row in group),
            "token_complete_row_rate": mean(
                float(_token_telemetry_complete(row)) for row in group
            ),
        }
        for (model, harness, level), group in sorted(
            grouped.items(), key=lambda item: tuple(str(value) for value in item[0])
        )
    ]


def _joint_profile(rows: Sequence[Mapping[str, object]]) -> list[JSON]:
    grouped: dict[tuple[str, str, int, str, str], list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        condition = _condition(row)
        key = (
            str(condition.get("model")),
            str(condition.get("harness")),
            int(condition.get("visible_tools", 0)),
            str(condition.get("workflow")),
            str(condition.get("representation")),
        )
        grouped[key].append(row)
    return [
        {
            "model": key[0],
            "harness": key[1],
            "visible_tools": key[2],
            "workflow": key[3],
            "representation": key[4],
            "rows": len(group),
            "verified_success": mean(_success(row) for row in group),
            "provider_failure": mean(is_infrastructure_failure(row) for row in group),
            "mean_total_tokens": mean(_tokens(row) for row in group),
            **_efficiency_summary(group),
            "mean_attempted_calls": mean(len(row.get("call_attempts", [])) for row in group),
            "mean_repeated_calls": mean(_repeated_attempts(row) for row in group),
            "failure_stage_counts": dict(
                sorted(
                    Counter(
                        _failure_stage(row) for row in group
                    ).items()
                )
            ),
        }
        for key, group in sorted(grouped.items())
    ]


def _action_profile(rows: Sequence[Mapping[str, object]]) -> list[JSON]:
    grouped: dict[tuple[str, str, str], list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        condition = _condition(row)
        intent = str(condition.get("intent"))
        obligation = (
            "read_only"
            if intent not in _ACTION_INTENTS
            else "required_action"
            if condition.get("action_required") is True
            else "forbidden_action"
        )
        grouped[(str(condition.get("model")), str(condition.get("harness")), obligation)].append(row)
    return [
        {
            "model": model,
            "harness": harness,
            "action_obligation": obligation,
            "rows": len(group),
            "verified_success": mean(_success(row) for row in group),
            "safety_success": mean(
                isinstance(row.get("verdict"), dict) and row["verdict"].get("safety") is True
                for row in group
            ),
            "governance_success": mean(
                isinstance(row.get("verdict"), dict)
                and row["verdict"].get("governance") is True
                for row in group
            ),
        }
        for (model, harness, obligation), group in sorted(grouped.items())
    ]


def _transitions(
    rows: Sequence[Mapping[str, object]],
    *,
    factor: str,
    high: object,
    low: object,
) -> JSON:
    cells: dict[tuple[object, ...], dict[object, Mapping[str, object]]] = defaultdict(dict)
    for row in rows:
        condition = _condition(row)
        level = condition.get(factor)
        if level not in {high, low}:
            continue
        key = (
            condition.get("family_id"),
            condition.get("model"),
            condition.get("harness"),
            *(condition.get(name) for name in _other_factors(factor)),
        )
        cells[key][level] = row
    failures: Counter[str] = Counter()
    recoveries = unchanged_success = unchanged_failure = incomplete = 0
    for cell in cells.values():
        if high not in cell or low not in cell:
            incomplete += 1
            continue
        low_success = bool(_success(cell[low]))
        high_success = bool(_success(cell[high]))
        if low_success and not high_success:
            failures[_failure_stage(cell[high])] += 1
        elif not low_success and high_success:
            recoveries += 1
        elif low_success:
            unchanged_success += 1
        else:
            unchanged_failure += 1
    return {
        "failure_transitions": dict(sorted(failures.items())),
        "recoveries": recoveries,
        "unchanged_success": unchanged_success,
        "unchanged_failure": unchanged_failure,
        "incomplete_cells": incomplete,
    }


def _routing_robustness(
    primary_rows: Sequence[Mapping[str, object]],
    robustness_rows: Sequence[Mapping[str, object]],
    *,
    bootstrap_samples: int,
    seed: int,
) -> JSON:
    """Pair the routed primary policy with the fixed-menu robustness policy."""

    if not robustness_rows:
        return {"status": "not_run", "pairs": 0}

    def key(row: Mapping[str, object]) -> tuple[str, str, int, str, str]:
        condition = _condition(row)
        return (
            str(condition.get("model")),
            str(condition.get("family_id")),
            int(condition.get("visible_tools", 0)),
            str(condition.get("workflow")),
            str(condition.get("representation")),
        )

    primary = {key(row): row for row in primary_rows}
    fixed = {key(row): row for row in robustness_rows}
    if len(primary) != len(primary_rows) or len(fixed) != len(robustness_rows):
        raise ValueError("duplicate routing-robustness cell")
    shared = sorted(set(primary) & set(fixed))
    success_by_intent: dict[str, list[float]] = defaultdict(list)
    discordance = Counter()
    token_deltas: list[float] = []
    request_deltas: list[float] = []
    exposure_deltas: list[float] = []
    for cell in shared:
        routed_row = primary[cell]
        fixed_row = fixed[cell]
        routed_success = bool(_success(routed_row))
        fixed_success = bool(_success(fixed_row))
        label = (
            "both_success"
            if routed_success and fixed_success
            else "routed_only_success"
            if routed_success
            else "fixed_only_success"
            if fixed_success
            else "both_failure"
        )
        discordance[label] += 1
        intent = str(_condition(routed_row).get("intent"))
        success_by_intent[intent].append(float(fixed_success) - float(routed_success))
        if _token_telemetry_complete(fixed_row) and _token_telemetry_complete(
            routed_row
        ):
            token_deltas.append(_tokens(fixed_row) - _tokens(routed_row))
        request_deltas.append(
            _row_work_metric(fixed_row, "model_requests_attempted")
            - _row_work_metric(routed_row, "model_requests_attempted")
        )
        exposure_deltas.append(
            _row_work_metric(fixed_row, "tool_schema_exposures")
            - _row_work_metric(routed_row, "tool_schema_exposures")
        )
    clustered = [mean(values) for _, values in sorted(success_by_intent.items())]
    return {
        "status": "complete" if set(fixed) <= set(primary) else "incomplete",
        "pairs": len(shared),
        "missing_primary_pairs": len(set(fixed) - set(primary)),
        "intent_clusters": len(clustered),
        "fixed_minus_routed_verified_success": _bootstrap(
            clustered,
            samples=bootstrap_samples,
            seed=seed + int(stable_hash("routing-robustness")[:8], 16),
        ),
        "discordance": dict(sorted(discordance.items())),
        "token_complete_pairs": len(token_deltas),
        "mean_fixed_minus_routed_total_tokens": (
            mean(token_deltas) if token_deltas else None
        ),
        "mean_fixed_minus_routed_model_requests": mean(request_deltas),
        "mean_fixed_minus_routed_tool_schema_exposures": mean(exposure_deltas),
        "interpretation": (
            "Positive success differences favor the fixed full-menu policy. Positive work "
            "differences mean the fixed policy consumed more resources. This robustness layer "
            "is descriptive and is excluded from the Holm family."
        ),
    }


def analyze_capacity_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    bootstrap_samples: int = 10000,
    seed: int = 20260805,
) -> JSON:
    """Analyze the registered extreme factorial plus axis checkpoints."""

    _validate_unique(rows)
    if not rows:
        raise ValueError("capacity analysis requires rows")
    primary_rows = [
        row for row in rows if _study_role(row) is StudyRole.PRIMARY
    ]
    robustness_rows = [
        row for row in rows if _study_role(row) is StudyRole.ROBUSTNESS
    ]
    if not primary_rows:
        raise ValueError("capacity analysis requires primary-role rows")
    effects: JSON = {}
    intent_sensitivity: JSON = {}
    for name, terms in PRIMARY_CONTRASTS.items():
        values, pairs, incomplete, families = _registered_contrast_values(
            primary_rows, terms, cluster_unit="family"
        )
        effects[name] = _effect_summary(
            name,
            values,
            pairs,
            incomplete,
            families,
            bootstrap_samples=bootstrap_samples,
            seed=seed,
        )
        intent_values, intent_pairs, intent_incomplete, intent_families = (
            _registered_contrast_values(
                primary_rows, terms, cluster_unit="intent"
            )
        )
        intent_sensitivity[name] = _effect_summary(
            f"{name}-intent-sensitivity",
            intent_values,
            intent_pairs,
            intent_incomplete,
            intent_families,
            bootstrap_samples=bootstrap_samples,
            seed=seed,
        )
    exploratory: JSON = {}
    for name, terms in EXPLORATORY_CONTRASTS.items():
        values, pairs, incomplete, families = _registered_contrast_values(
            primary_rows, terms
        )
        exploratory[name] = _effect_summary(
            name,
            values,
            pairs,
            incomplete,
            families,
            bootstrap_samples=bootstrap_samples,
            seed=seed,
        )
    adjusted = holm_adjust(
        {name: float(effect["bootstrap_p_two_sided"]) for name, effect in effects.items()}
    )
    for name, value in adjusted.items():
        effects[name]["holm_p"] = value
    sensitivity_adjusted = holm_adjust(
        {
            name: float(effect["bootstrap_p_two_sided"])
            for name, effect in intent_sensitivity.items()
        }
    )
    for name, value in sensitivity_adjusted.items():
        intent_sensitivity[name]["holm_p"] = value

    by_configuration: list[JSON] = []
    grouped: dict[tuple[str, str], list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        condition = _condition(row)
        grouped[(str(condition.get("model")), str(condition.get("harness")))].append(row)
    for (model, harness), group in sorted(grouped.items()):
        stages = Counter(
            _failure_stage(row) for row in group
        )
        by_configuration.append(
            {
                "model": model,
                "harness": harness,
                "rows": len(group),
                "families": len({str(_condition(row).get("family_id")) for row in group}),
                "intents": len({str(_condition(row).get("intent")) for row in group}),
                "verified_success": mean(_success(row) for row in group),
                "answer_wrappers_normalized": sum(_answer_wrapper_normalized(row) for row in group),
                "provider_failure": mean(is_infrastructure_failure(row) for row in group),
                "mean_total_tokens": mean(_tokens(row) for row in group),
                **_efficiency_summary(group),
                "failure_stage_counts": dict(sorted(stages.items())),
                "study_roles": sorted({_study_role(row).value for row in group}),
                "descriptive_effects": _descriptive_effects(group),
            }
        )
    return {
        "analysis": "factorized-capacity-surface",
        "rows": len(rows),
        "primary_rows": len(primary_rows),
        "robustness_rows": len(robustness_rows),
        "families": len({str(_condition(row).get("family_id")) for row in rows}),
        "intent_clusters": len({str(_condition(row).get("intent")) for row in rows}),
        "configurations": len(grouped),
        "verified_success": mean(_success(row) for row in primary_rows),
        "answer_wrappers_normalized": sum(_answer_wrapper_normalized(row) for row in rows),
        "status_counts": dict(sorted(Counter(_analysis_status(row) for row in rows).items())),
        "raw_status_counts": dict(sorted(Counter(str(row.get("status")) for row in rows).items())),
        "by_configuration": by_configuration,
        "profiles": {
            factor: _profile(primary_rows, factor)
            for factor in ("visible_tools", "workflow", "representation")
        },
        "joint_profile": _joint_profile(primary_rows),
        "action_profile": _action_profile(primary_rows),
        "routing_policy_robustness": _routing_robustness(
            primary_rows,
            robustness_rows,
            bootstrap_samples=bootstrap_samples,
            seed=seed,
        ),
        "primary_effects": effects,
        "intent_cluster_sensitivity": intent_sensitivity,
        "exploratory_effects": exploratory,
        "transitions": {
            "visible_8_to_128": _transitions(primary_rows, factor="visible_tools", high=128, low=8),
            "workflow_low_to_high": _transitions(primary_rows, factor="workflow", high="high", low="low"),
        },
        "inference_note": (
            "Primary percentile bootstrap intervals and centered-bootstrap p-values treat disjoint "
            "semantic families as the finite-benchmark estimation units and Holm correction covers "
            "eight registered sparse-design effects. A separate intent-cluster analysis averages "
            "semantic variants within each of the twelve fixed intent templates and is reported as "
            "a conservative sensitivity analysis. The second harness is a stratified robustness "
            "diagnostic and is not pooled into primary effects."
        ),
    }


def _simulated_detection_power(
    values: Sequence[float],
    *,
    target: float,
    clusters: int,
    alpha: float,
    simulations: int,
    seed: int,
) -> float:
    if len(values) < 2 or clusters < 2 or simulations <= 0:
        raise ValueError("power simulation requires at least two clusters and positive draws")
    centered = [value - mean(values) for value in values]
    critical = NormalDist().inv_cdf(1.0 - alpha / 2.0)
    rng = random.Random(seed)
    detected = 0
    for _ in range(simulations):
        sample = [value + target for value in rng.choices(centered, k=clusters)]
        spread = stdev(sample)
        statistic = float("inf") if spread == 0 else abs(mean(sample)) / (spread / sqrt(clusters))
        detected += statistic >= critical
    return detected / simulations


def prospective_power(
    rows: Sequence[Mapping[str, object]],
    *,
    main_intent_clusters: int = 12,
    simulations: int = 5000,
    seed: int = 20260805,
    familywise_alpha: float = 0.05,
) -> JSON:
    """Simulate conservative intent-cluster power from development contrasts."""

    _validate_unique(rows)
    if not 0 < familywise_alpha < 1:
        raise ValueError("familywise_alpha must lie between zero and one")
    per_effect_alpha = familywise_alpha / len(PRIMARY_CONTRASTS)
    effects: JSON = {}
    interaction_names = {
        "visible_by_workflow",
        "visible_by_linked",
        "workflow_by_linked",
    }
    for name, terms in PRIMARY_CONTRASTS.items():
        values, _, incomplete, families = _registered_contrast_values(
            rows, terms, cluster_unit="intent"
        )
        target = 0.10 if name in interaction_names else 0.08
        effects[name] = {
            "target_risk_difference": target,
            "estimated_power": _simulated_detection_power(
                values,
                target=target,
                clusters=main_intent_clusters,
                alpha=per_effect_alpha,
                simulations=simulations,
                seed=seed + int(stable_hash(name)[:8], 16),
            ),
            "development_intent_clusters": len(values),
            "development_families": families,
            "incomplete_cells": incomplete,
        }
    passed = all(
        effect["estimated_power"] >= 0.80
        and effect["development_intent_clusters"] >= main_intent_clusters
        and effect["incomplete_cells"] == 0
        for effect in effects.values()
    )
    return {
        "analysis": "factorized-capacity-prospective-power",
        "passed": passed,
        "main_intent_clusters": main_intent_clusters,
        "simulations": simulations,
        "familywise_alpha": familywise_alpha,
        "per_effect_alpha": per_effect_alpha,
        "effects": effects,
        "note": (
            "Centered development intent contrasts are resampled at the planned main intent count. "
            "The Bonferroni first-step threshold is conservative for the frozen Holm family."
        ),
    }


def prospective_precision(
    rows: Sequence[Mapping[str, object]],
    *,
    main_families: int = 36,
    familywise_alpha: float = 0.05,
    maximum_simultaneous_half_width: float = 0.15,
) -> JSON:
    """Project simultaneous main-study interval precision from development."""

    _validate_unique(rows)
    if main_families < 2:
        raise ValueError("prospective precision requires at least two main families")
    if not 0 < familywise_alpha < 1:
        raise ValueError("familywise_alpha must lie between zero and one")
    if not 0 < maximum_simultaneous_half_width < 1:
        raise ValueError("maximum simultaneous half width must lie between zero and one")
    simultaneous_critical = NormalDist().inv_cdf(
        1.0 - familywise_alpha / (2.0 * len(PRIMARY_CONTRASTS))
    )
    ordinary_critical = NormalDist().inv_cdf(0.975)
    effects: JSON = {}
    for name, terms in PRIMARY_CONTRASTS.items():
        values, pairs, incomplete, families = _registered_contrast_values(
            rows, terms, cluster_unit="family"
        )
        if len(values) < 2:
            raise ValueError("prospective precision requires at least two development families")
        estimated_sd = stdev(values)
        standard_error = estimated_sd / sqrt(main_families)
        simultaneous_half_width = simultaneous_critical * standard_error
        effects[name] = {
            "development_family_units": len(values),
            "development_families": families,
            "configuration_family_contrasts": pairs,
            "incomplete_cells": incomplete,
            "estimated_family_sd": estimated_sd,
            "planned_main_families": main_families,
            "projected_ordinary_95_half_width": ordinary_critical * standard_error,
            "projected_simultaneous_95_half_width": simultaneous_half_width,
            "maximum_simultaneous_half_width": maximum_simultaneous_half_width,
            "passed": (
                len(values) >= 12
                and incomplete == 0
                and simultaneous_half_width <= maximum_simultaneous_half_width
            ),
        }
    passed = all(effect["passed"] is True for effect in effects.values())
    return {
        "analysis": "factorized-capacity-prospective-precision",
        "passed": passed,
        "planned_main_families": main_families,
        "familywise_alpha": familywise_alpha,
        "simultaneous_critical_value": simultaneous_critical,
        "maximum_simultaneous_half_width": maximum_simultaneous_half_width,
        "effects": effects,
        "note": (
            "Development family-level contrast dispersion projects the precision of the 36-family "
            "sealed main benchmark. The threshold bounds Bonferroni-simultaneous 95% interval half "
            "width for all eight registered effects; ordinary intervals, Holm tests, and a more "
            "conservative twelve-intent sensitivity analysis remain mandatory outputs."
        ),
    }


def analyze_discovery_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    bootstrap_samples: int = 10000,
    seed: int = 20260805,
) -> JSON:
    """Decompose registered-catalog ITT success into retrieval and model selection."""

    _validate_unique(rows)
    if not rows:
        raise ValueError("discovery analysis requires rows")
    grouped: dict[tuple[str, str, str, int], list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        condition = _condition(row)
        key = (
            str(condition.get("model")),
            str(condition.get("harness")),
            str(condition.get("retriever")),
            int(condition.get("registry_size", 0)),
        )
        grouped[key].append(row)
    profile: list[JSON] = []
    for (model, harness, retriever, registry_size), group in sorted(grouped.items()):
        available = [
            row
            for row in group
            if isinstance(row.get("exposure"), dict)
            and row["exposure"].get("required_available") is True
        ]
        profile.append(
            {
                "model": model,
                "harness": harness,
                "retriever": retriever,
                "registry_size": registry_size,
                "rows": len(group),
                "availability": len(available) / len(group),
                "conditional_selection_success": (
                    mean(row.get("success") is True for row in available)
                    if available
                    else None
                ),
                "itt_success": mean(row.get("success") is True for row in group),
                "provider_failure": mean(is_infrastructure_failure(row) for row in group),
                "mean_total_tokens": mean(_tokens(row) for row in group),
                "token_complete_row_rate": mean(
                    float(_token_telemetry_complete(row)) for row in group
                ),
            }
        )
    def outcome(row: Mapping[str, object]) -> float:
        return float(row.get("success") is True)

    def availability(row: Mapping[str, object]) -> float:
        exposure = row.get("exposure")
        return float(isinstance(exposure, dict) and exposure.get("required_available") is True)

    def required_tool(row: Mapping[str, object]) -> str:
        exposure = row.get("exposure")
        tool = exposure.get("required_tool") if isinstance(exposure, dict) else None
        if not isinstance(tool, str) or not tool:
            raise ValueError("discovery row is missing required_tool exposure metadata")
        return tool

    def registry_values(
        retriever: str,
        measure: Callable[[Mapping[str, object]], float],
    ) -> tuple[list[float], int, int]:
        cells: dict[tuple[str, str, str], dict[int, float]] = defaultdict(dict)
        case_tools: dict[str, str] = {}
        for row in rows:
            condition = _condition(row)
            if condition.get("retriever") != retriever:
                continue
            key = (
                str(condition.get("case_id")),
                str(condition.get("model")),
                str(condition.get("harness")),
            )
            registry_size = int(condition.get("registry_size", 0))
            if registry_size in cells[key]:
                raise ValueError("duplicate discovery paired cell")
            cells[key][registry_size] = measure(row)
            case_tools[key[0]] = required_tool(row)
        by_case: dict[str, list[float]] = defaultdict(list)
        incomplete = 0
        for (case_id, _, _), cell in cells.items():
            if 32 in cell and 2048 in cell:
                by_case[case_id].append(cell[2048] - cell[32])
            else:
                incomplete += 1
        by_tool: dict[str, list[float]] = defaultdict(list)
        for case_id, values in by_case.items():
            by_tool[case_tools[case_id]].append(mean(values))
        return [mean(values) for _, values in sorted(by_tool.items())], len(by_case), incomplete

    def interaction_values(
        measure: Callable[[Mapping[str, object]], float],
    ) -> tuple[list[float], int, int]:
        cells: dict[tuple[str, str, str], dict[tuple[str, int], float]] = defaultdict(dict)
        case_tools: dict[str, str] = {}
        for row in rows:
            condition = _condition(row)
            key = (
                str(condition.get("case_id")),
                str(condition.get("model")),
                str(condition.get("harness")),
            )
            coordinate = (
                str(condition.get("retriever")),
                int(condition.get("registry_size", 0)),
            )
            if coordinate in cells[key]:
                raise ValueError("duplicate discovery interaction cell")
            cells[key][coordinate] = measure(row)
            case_tools[key[0]] = required_tool(row)
        required = {
            ("flat", 32),
            ("flat", 2048),
            ("hierarchical", 32),
            ("hierarchical", 2048),
        }
        by_case: dict[str, list[float]] = defaultdict(list)
        incomplete = 0
        for (case_id, _, _), cell in cells.items():
            if required <= set(cell):
                value = (
                    cell[("hierarchical", 2048)]
                    - cell[("hierarchical", 32)]
                    - cell[("flat", 2048)]
                    + cell[("flat", 32)]
                )
                by_case[case_id].append(value)
            else:
                incomplete += 1
        by_tool: dict[str, list[float]] = defaultdict(list)
        for case_id, values in by_case.items():
            by_tool[case_tools[case_id]].append(mean(values))
        return [mean(values) for _, values in sorted(by_tool.items())], len(by_case), incomplete

    def discovery_effects(
        measure: Callable[[Mapping[str, object]], float],
        prefix: str,
    ) -> JSON:
        effects: JSON = {}
        for retriever in ("flat", "hierarchical"):
            values, cases, incomplete = registry_values(retriever, measure)
            effects[f"{prefix}_{retriever}_r2048_minus_r32"] = _effect_summary(
                f"{prefix}_{retriever}_r2048_minus_r32",
                values,
                cases,
                incomplete,
                cases,
                bootstrap_samples=bootstrap_samples,
                seed=seed,
            )
        values, cases, incomplete = interaction_values(measure)
        effects[f"{prefix}_hierarchical_attenuation"] = _effect_summary(
            f"{prefix}_hierarchical_attenuation",
            values,
            cases,
            incomplete,
            cases,
            bootstrap_samples=bootstrap_samples,
            seed=seed,
        )
        adjusted = holm_adjust(
            {name: float(effect["bootstrap_p_two_sided"]) for name, effect in effects.items()}
        )
        for name, value in adjusted.items():
            effects[name]["holm_p"] = value
        return effects

    return {
        "analysis": "registered-catalog-discovery-v3",
        "rows": len(rows),
        "cases": len({str(_condition(row).get("case_id")) for row in rows}),
        "profiles": profile,
        "primary_itt_effects": discovery_effects(outcome, "itt"),
        "availability_effects": discovery_effects(availability, "availability"),
        "interaction_orientation": (
            "(hierarchical R2048-R32) minus (flat R2048-R32); positive values mean "
            "hierarchical retrieval attenuates registered-load loss"
        ),
        "status_counts": dict(sorted(Counter(_analysis_status(row) for row in rows).items())),
        "raw_status_counts": dict(sorted(Counter(str(row.get("status")) for row in rows).items())),
    }
