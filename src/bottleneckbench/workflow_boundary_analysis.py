"""Exact and family-paired analysis for the workflow-capacity boundary."""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import json
from pathlib import Path
import random
from typing import Iterable, Mapping, Sequence

from bottleneckbench.workflow_boundary import (
    WORKFLOW_BOUNDARY_CERTIFIED_CALL_LEVELS,
    WORKFLOW_BOUNDARY_LOCATOR_LEVELS,
    WORKFLOW_BOUNDARY_STUDY,
)
from bottleneckbench.workflow_boundary_experiments import (
    WorkflowBoundaryPlan,
)
from bottleneckbench.core.schema import JSON, canonical_json, stable_hash


def load_workflow_boundary_rows(paths: Iterable[Path]) -> tuple[JSON, ...]:
    rows: list[JSON] = []
    for path in paths:
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"non-object row at {path}:{line_number}")
            rows.append(row)
    return tuple(rows)


def _condition(row: Mapping[str, object]) -> Mapping[str, object]:
    condition = row.get("condition")
    if not isinstance(condition, dict):
        raise ValueError("workflow-boundary row has no condition object")
    if condition.get("study") != WORKFLOW_BOUNDARY_STUDY:
        raise ValueError("row is not a workflow-capacity-frontier observation")
    return condition


def _verified(row: Mapping[str, object]) -> int:
    verdict = row.get("verdict")
    if not isinstance(verdict, dict) or not isinstance(
        verdict.get("verified_success"), bool
    ):
        raise ValueError("workflow-boundary row has no exact success verdict")
    return int(verdict["verified_success"])


def _usage(row: Mapping[str, object]) -> tuple[int, int, int]:
    usage = row.get("usage")
    if not isinstance(usage, dict):
        return 0, 0, 0
    return (
        int(usage.get("prompt_tokens", 0)),
        int(usage.get("completion_tokens", 0)),
        int(usage.get("total_tokens", 0)),
    )


def _efficiency(row: Mapping[str, object]) -> Mapping[str, object]:
    payload = row.get("efficiency")
    return payload if isinstance(payload, dict) else {}


def _response_usage_counts(row: Mapping[str, object]) -> tuple[int, int]:
    response_ids = row.get("response_ids")
    response_count = len(response_ids) if isinstance(response_ids, list) else 0
    sources = row.get("usage_sources")
    missing = (
        sum(source == "stream_usage_missing" for source in sources)
        if isinstance(sources, list) and len(sources) == response_count
        else 0
    )
    return response_count, missing


def _row_usage_contract(row: Mapping[str, object]) -> bool:
    response_ids = row.get("response_ids")
    if not isinstance(response_ids, list):
        return False
    if not response_ids:
        return row.get("status") == "provider_failure"
    sources = row.get("usage_sources")
    if sources is None:
        return _usage(row)[2] > 0
    if not isinstance(sources, list) or len(sources) != len(response_ids):
        return False
    return (
        all(source != "stream_usage_missing" for source in sources)
        and _usage(row)[2] > 0
    )


def _percentile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise ValueError("cannot take a percentile of an empty sample")
    ordered = sorted(values)
    position = probability * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _family_cluster_interval(
    paired: Mapping[str, Sequence[float]], *, draws: int, seed: int
) -> tuple[float, float]:
    families = sorted(paired)
    if not families:
        return float("nan"), float("nan")
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(draws):
        values: list[float] = []
        for _ in families:
            values.extend(paired[rng.choice(families)])
        samples.append(sum(values) / len(values))
    return _percentile(samples, 0.025), _percentile(samples, 0.975)


def _nonincreasing_isotonic(
    rates: Sequence[float], weights: Sequence[int]
) -> tuple[float, ...]:
    if len(rates) != len(weights) or not rates or any(weight <= 0 for weight in weights):
        raise ValueError("isotonic rates and positive weights must align")
    blocks: list[dict[str, object]] = [
        {"start": index, "end": index, "weight": weight, "value": rate}
        for index, (rate, weight) in enumerate(zip(rates, weights, strict=True))
    ]
    index = 0
    while index + 1 < len(blocks):
        left = blocks[index]
        right = blocks[index + 1]
        if float(left["value"]) >= float(right["value"]):
            index += 1
            continue
        weight = int(left["weight"]) + int(right["weight"])
        value = (
            float(left["value"]) * int(left["weight"])
            + float(right["value"]) * int(right["weight"])
        ) / weight
        blocks[index : index + 2] = [
            {
                "start": int(left["start"]),
                "end": int(right["end"]),
                "weight": weight,
                "value": value,
            }
        ]
        index = max(0, index - 1)
    fitted = [0.0] * len(rates)
    for block in blocks:
        for position in range(int(block["start"]), int(block["end"]) + 1):
            fitted[position] = float(block["value"])
    return tuple(fitted)


def _capacity_summary(rates: Sequence[float], counts: Sequence[int]) -> JSON:
    locator_levels = list(WORKFLOW_BOUNDARY_LOCATOR_LEVELS)
    x = list(WORKFLOW_BOUNDARY_CERTIFIED_CALL_LEVELS)
    fitted = _nonincreasing_isotonic(rates, counts)
    width = x[-1] - x[0]
    auc = sum(
        (fitted[index] + fitted[index + 1])
        * (x[index + 1] - x[index])
        / 2.0
        for index in range(len(x) - 1)
    ) / width
    if fitted[0] < 0.5:
        boundary: JSON = {
            "status": "left_censored_at_baseline",
            "estimated_minimum_calls_at_50_percent": None,
            "upper_bound_calls": float(x[0]),
        }
    elif fitted[-1] >= 0.5:
        boundary = {
            "status": "right_censored_after_maximum",
            "estimated_minimum_calls_at_50_percent": None,
            "lower_bound_calls": float(x[-1]),
        }
    else:
        crossing = next(
            index
            for index in range(len(fitted) - 1)
            if fitted[index] >= 0.5 and fitted[index + 1] < 0.5
        )
        fraction = (0.5 - fitted[crossing]) / (
            fitted[crossing + 1] - fitted[crossing]
        )
        estimate = x[crossing] + fraction * (x[crossing + 1] - x[crossing])
        boundary = {
            "status": "interpolated",
            "estimated_minimum_calls_at_50_percent": estimate,
            "bracketing_call_interval": [
                float(x[crossing]),
                float(x[crossing + 1]),
            ],
        }
    return {
        "observed_success_rates": list(rates),
        "isotonic_success_rates": list(fitted),
        "locator_levels": locator_levels,
        "certified_call_levels": x,
        "normalized_capacity_auc": auc,
        "boundary_50": boundary,
    }


def analyze_workflow_boundary(
    rows: Sequence[Mapping[str, object]], *, bootstrap_draws: int = 5000
) -> JSON:
    """Report all levels, paired losses, failure stages, and efficiency."""

    if not rows:
        raise ValueError("workflow-boundary analysis requires rows")
    indexed: dict[tuple[str, str, int], Mapping[str, object]] = {}
    for row in rows:
        condition = _condition(row)
        key = (
            str(condition["model"]),
            str(condition["family_id"]),
            int(condition["locator_nodes"]),
        )
        if key in indexed:
            raise ValueError(f"duplicate workflow-boundary cell: {key}")
        indexed[key] = row
    models = sorted({key[0] for key in indexed})
    families = sorted({key[1] for key in indexed})

    call_by_locator = dict(
        zip(
            WORKFLOW_BOUNDARY_LOCATOR_LEVELS,
            WORKFLOW_BOUNDARY_CERTIFIED_CALL_LEVELS,
            strict=True,
        )
    )
    summaries: list[JSON] = []
    capacity: dict[str, JSON] = {}
    for model in models:
        rates: list[float] = []
        counts: list[int] = []
        for level in WORKFLOW_BOUNDARY_LOCATOR_LEVELS:
            selected = [
                row
                for (item_model, _, item_level), row in indexed.items()
                if item_model == model and item_level == level
            ]
            successes = sum(_verified(row) for row in selected)
            prompt = sum(_usage(row)[0] for row in selected)
            completion = sum(_usage(row)[1] for row in selected)
            schema_bytes = sum(
                int(_efficiency(row).get("tool_schema_bytes", 0)) for row in selected
            )
            wall_time = sum(
                float(_efficiency(row).get("wall_time_seconds", 0.0))
                for row in selected
            )
            stage_counts = Counter(
                str(
                    row.get("verdict", {}).get("failure_stage")
                    if isinstance(row.get("verdict"), dict)
                    else "missing"
                )
                for row in selected
                if not _verified(row)
            )
            count = len(selected)
            response_usage = [_response_usage_counts(row) for row in selected]
            response_count = sum(total for total, _ in response_usage)
            missing_usage = sum(missing for _, missing in response_usage)
            rate = successes / count if count else float("nan")
            rates.append(rate)
            counts.append(count)
            summaries.append(
                {
                    "model": model,
                    "locator_nodes": level,
                    "certified_minimum_calls": call_by_locator[level],
                    "count": count,
                    "verified_successes": successes,
                    "verified_success_rate": rate,
                    "mean_prompt_tokens": prompt / count if count else None,
                    "mean_completion_tokens": completion / count if count else None,
                    "response_token_telemetry_coverage": (
                        (response_count - missing_usage) / response_count
                        if response_count
                        else 1.0
                    ),
                    "responses_missing_usage": missing_usage,
                    "mean_tool_schema_bytes": schema_bytes / count if count else None,
                    "mean_wall_time_seconds": wall_time / count if count else None,
                    "failure_stages": dict(sorted(stage_counts.items())),
                }
            )
        if all(count > 0 for count in counts):
            capacity[model] = _capacity_summary(rates, counts)

    contrasts: list[JSON] = []
    baseline = WORKFLOW_BOUNDARY_LOCATOR_LEVELS[0]
    for level in WORKFLOW_BOUNDARY_LOCATOR_LEVELS[1:]:
        paired: dict[str, list[float]] = defaultdict(list)
        for model in models:
            for family in families:
                lower = indexed.get((model, family, baseline))
                higher = indexed.get((model, family, level))
                if lower is not None and higher is not None:
                    paired[family].append(_verified(higher) - _verified(lower))
        values = [value for family_values in paired.values() for value in family_values]
        estimate = sum(values) / len(values) if values else float("nan")
        low, high = _family_cluster_interval(
            paired,
            draws=bootstrap_draws,
            seed=int(
                stable_hash(
                    {"study": "workflow-boundary", "level": level, "draws": bootstrap_draws}
                )[:16],
                16,
            ),
        )
        contrasts.append(
            {
                "contrast": (
                    f"calls_{call_by_locator[level]}_minus_"
                    f"calls_{call_by_locator[baseline]}"
                ),
                "locator_contrast": f"locator_{level}_minus_locator_{baseline}",
                "estimate": estimate,
                "ci95_family_cluster": [low, high],
                "matched_model_family_pairs": len(values),
                "family_clusters": len(paired),
            }
        )

    return {
        "analysis": "workflow-capacity-frontier-v1",
        "row_count": len(rows),
        "models": models,
        "family_count": len(families),
        "level_summaries": summaries,
        "paired_contrasts": contrasts,
        "capacity_boundaries": capacity,
        "bootstrap_draws": bootstrap_draws,
    }


def workflow_boundary_development_admission(
    plans: Sequence[WorkflowBoundaryPlan],
    rows: Sequence[Mapping[str, object]],
    *,
    minimum_models: int = 4,
    maximum_provider_failure_rate: float = 0.02,
    minimum_active_range: float = 0.25,
    minimum_active_models: int = 2,
    minimum_baseline_rate: float = 0.50,
    minimum_baseline_models: int = 3,
    minimum_uncensored_models: int = 2,
    minimum_pooled_endpoint_drop: float = 0.10,
) -> JSON:
    """Prospective construct-activation gate before main can be frozen."""

    if not plans:
        raise ValueError("workflow-boundary admission requires plans")
    if any(plan.split.value != "development" for plan in plans):
        raise ValueError("workflow-boundary admission accepts development plans only")
    expected: dict[str, WorkflowBoundaryPlan] = {}
    expected_conditions: dict[str, object] = {}
    for plan in plans:
        if plan.model in expected:
            raise ValueError("workflow-boundary admission has duplicate model plans")
        expected[plan.model] = plan
        for condition in plan.conditions:
            if condition.condition_id in expected_conditions:
                raise ValueError("workflow-boundary plans overlap in condition identity")
            expected_conditions[condition.condition_id] = condition
    observed: dict[str, Mapping[str, object]] = {}
    identity_valid = True
    plan_binding_valid = True
    for row in rows:
        condition = _condition(row)
        condition_id = row.get("condition_id")
        if not isinstance(condition_id, str) or stable_hash(dict(condition)) != condition_id:
            identity_valid = False
            continue
        if condition_id in observed:
            identity_valid = False
            continue
        observed[condition_id] = row
        model = str(condition.get("model"))
        plan = expected.get(model)
        if plan is None or row.get("plan_hash") != plan.plan_hash:
            plan_binding_valid = False

    complete_ids = set(observed) == set(expected_conditions)
    models = sorted(expected)
    provider_failures = sum(row.get("status") == "provider_failure" for row in rows)
    provider_rate = provider_failures / len(rows) if rows else 1.0
    per_model_rates: dict[str, dict[int, float]] = {}
    active_models: list[str] = []
    baseline_solvable_models: list[str] = []
    uncensored_models: list[str] = []
    complete_model_matrices = True
    baseline_level = WORKFLOW_BOUNDARY_LOCATOR_LEVELS[0]
    maximum_level = WORKFLOW_BOUNDARY_LOCATOR_LEVELS[-1]
    for model in models:
        rates: dict[int, float] = {}
        for level in WORKFLOW_BOUNDARY_LOCATOR_LEVELS:
            selected = [
                row
                for row in rows
                if str(_condition(row).get("model")) == model
                and int(_condition(row).get("locator_nodes", -1)) == level
            ]
            expected_count = len(expected[model].conditions) // len(
                WORKFLOW_BOUNDARY_LOCATOR_LEVELS
            )
            if len(selected) != expected_count:
                complete_model_matrices = False
            rates[level] = (
                sum(_verified(row) for row in selected) / len(selected)
                if selected
                else float("nan")
            )
        per_model_rates[model] = rates
        finite = [value for value in rates.values() if value == value]
        if len(finite) == len(WORKFLOW_BOUNDARY_LOCATOR_LEVELS) and max(finite) - min(finite) >= minimum_active_range:
            active_models.append(model)
        if rates.get(baseline_level, float("nan")) >= minimum_baseline_rate:
            baseline_solvable_models.append(model)
        if (
            rates.get(baseline_level, float("nan")) >= 0.5
            and rates.get(maximum_level, float("nan")) < 0.5
        ):
            uncensored_models.append(model)

    pooled_rates: dict[int, float] = {}
    for level in WORKFLOW_BOUNDARY_LOCATOR_LEVELS:
        selected = [
            row
            for row in rows
            if int(_condition(row).get("locator_nodes", -1)) == level
        ]
        pooled_rates[level] = (
            sum(_verified(row) for row in selected) / len(selected)
            if selected
            else float("nan")
        )
    finite_pooled = [value for value in pooled_rates.values() if value == value]
    pooled_range = (
        max(finite_pooled) - min(finite_pooled)
        if len(finite_pooled) == len(WORKFLOW_BOUNDARY_LOCATOR_LEVELS)
        else 0.0
    )
    pooled_endpoint_drop = (
        pooled_rates[baseline_level] - pooled_rates[maximum_level]
        if all(
            pooled_rates[level] == pooled_rates[level]
            for level in (baseline_level, maximum_level)
        )
        else float("nan")
    )
    token_reporting = all(_row_usage_contract(row) for row in rows)
    response_usage = [_response_usage_counts(row) for row in rows]
    response_count = sum(total for total, _ in response_usage)
    missing_usage = sum(missing for _, missing in response_usage)
    worst_case_level_n = len(models) * (
        len(next(iter(plans)).conditions) // len(WORKFLOW_BOUNDARY_LOCATOR_LEVELS)
    )
    worst_case_half_width = 1.96 * (0.25 / worst_case_level_n) ** 0.5
    checks = [
        {
            "name": "matrix_closure",
            "passed": identity_valid
            and plan_binding_valid
            and complete_ids
            and complete_model_matrices
            and len(models) >= minimum_models,
            "expected_rows": len(expected_conditions),
            "observed_rows": len(rows),
            "model_count": len(models),
        },
        {
            "name": "provider_reliability",
            "passed": provider_rate <= maximum_provider_failure_rate,
            "provider_failures": provider_failures,
            "provider_failure_rate": provider_rate,
            "maximum": maximum_provider_failure_rate,
        },
        {
            "name": "token_reporting",
            "passed": token_reporting,
            "model_responses": response_count,
            "responses_missing_usage": missing_usage,
            "response_token_telemetry_coverage": (
                (response_count - missing_usage) / response_count
                if response_count
                else 1.0
            ),
        },
        {
            "name": "baseline_solvability",
            "passed": len(baseline_solvable_models) >= minimum_baseline_models,
            "baseline_locator_nodes": baseline_level,
            "baseline_certified_calls": WORKFLOW_BOUNDARY_CERTIFIED_CALL_LEVELS[0],
            "minimum_rate": minimum_baseline_rate,
            "solvable_models": baseline_solvable_models,
            "minimum_solvable_models": minimum_baseline_models,
        },
        {
            "name": "workflow_frontier_activation",
            "passed": pooled_endpoint_drop >= minimum_pooled_endpoint_drop
            and len(active_models) >= minimum_active_models
            and len(uncensored_models) >= minimum_uncensored_models,
            "pooled_rates": {str(key): value for key, value in pooled_rates.items()},
            "pooled_range": pooled_range,
            "pooled_endpoint_drop": pooled_endpoint_drop,
            "minimum_pooled_endpoint_drop": minimum_pooled_endpoint_drop,
            "minimum_range": minimum_active_range,
            "active_models": active_models,
            "minimum_active_models": minimum_active_models,
            "uncensored_models": uncensored_models,
            "minimum_uncensored_models": minimum_uncensored_models,
            "per_model_rates": {
                model: {str(key): value for key, value in rates.items()}
                for model, rates in per_model_rates.items()
            },
        },
        {
            "name": "prospective_precision",
            "passed": worst_case_half_width <= 0.21,
            "observations_per_level": worst_case_level_n,
            "worst_case_95_percent_half_width": worst_case_half_width,
            "maximum_half_width": 0.21,
        },
    ]
    return {
        "audit": "workflow-capacity-frontier-development-admission-v1",
        "split": "development",
        "thresholds": {
            "minimum_models": minimum_models,
            "maximum_provider_failure_rate": maximum_provider_failure_rate,
            "minimum_active_range": minimum_active_range,
            "minimum_active_models": minimum_active_models,
            "minimum_baseline_rate": minimum_baseline_rate,
            "minimum_baseline_models": minimum_baseline_models,
            "minimum_uncensored_models": minimum_uncensored_models,
            "minimum_pooled_endpoint_drop": minimum_pooled_endpoint_drop,
        },
        "checks": deepcopy(checks),
        "passed": all(bool(check["passed"]) for check in checks),
        "model_panel_hash": (
            plans[0].model_panel_hash
            if len({plan.model_panel_hash for plan in plans}) == 1
            else None
        ),
        "protocol_artifact_hash": (
            plans[0].protocol_artifact_hash
            if len({plan.protocol_artifact_hash for plan in plans}) == 1
            else None
        ),
        "plan_hashes": sorted(plan.plan_hash for plan in plans),
        "row_set_hash": stable_hash(
            sorted(canonical_json(dict(row)) for row in rows)
        ),
    }
