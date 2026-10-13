"""Deterministic template-aware summaries for ConnectorScale run rows."""

from __future__ import annotations

from collections import Counter, defaultdict
import math
from statistics import mean
from typing import Mapping, Sequence

from bottleneckbench.core.schema import JSON


def _condition(row: Mapping[str, object]) -> Mapping[str, object]:
    condition = row.get("condition")
    if not isinstance(condition, dict):
        raise ValueError("run row is missing a condition object")
    return condition


def _strict(row: Mapping[str, object]) -> float:
    verdict = row.get("verdict")
    if not isinstance(verdict, dict):
        return 0.0
    return float(verdict.get("strict_vts") is True)


def _outcome(row: Mapping[str, object], outcome: str) -> float:
    if outcome == "strict_vts":
        return _strict(row)
    if outcome == "capability_availability":
        exposure = row.get("exposure")
        if not isinstance(exposure, dict):
            return 0.0
        value = exposure.get("required_available", 0.0)
        return float(value) if isinstance(value, (int, float)) else 0.0
    verdict = row.get("verdict")
    if not isinstance(verdict, dict):
        return 0.0
    return float(verdict.get(outcome) is True)


def template_weighted_rate(
    rows: Sequence[Mapping[str, object]], outcome: str
) -> float:
    """Average within template first so seeds and repeats do not inflate weight."""

    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        task_id = _condition(row).get("task_id")
        if not isinstance(task_id, str):
            raise ValueError("condition is missing task_id")
        grouped[task_id].append(_outcome(row, outcome))
    if not grouped:
        return 0.0
    return mean(mean(values) for values in grouped.values())


def _pair_key(row: Mapping[str, object]) -> tuple[object, ...]:
    condition = _condition(row)
    return (
        condition.get("task_id"),
        condition.get("model"),
        condition.get("harness"),
        condition.get("registry_size"),
        condition.get("exposure_size"),
        condition.get("seed"),
    )


def paired_risk_difference(
    rows: Sequence[Mapping[str, object]], *, treatment: str, control: str
) -> JSON:
    """Compute a complete-cell paired StrictVTS risk difference."""

    cells: dict[tuple[object, ...], dict[str, float]] = defaultdict(dict)
    for row in rows:
        representation = _condition(row).get("representation")
        if representation in {treatment, control}:
            cells[_pair_key(row)][str(representation)] = _strict(row)
    differences = [
        cell[treatment] - cell[control]
        for cell in cells.values()
        if treatment in cell and control in cell
    ]
    return {
        "treatment": treatment,
        "control": control,
        "pairs": len(differences),
        "risk_difference": mean(differences) if differences else None,
        "improved": sum(value > 0 for value in differences),
        "degraded": sum(value < 0 for value in differences),
        "unchanged": sum(value == 0 for value in differences),
    }


def capacity_threshold(
    levels: Sequence[int], rates: Sequence[float], *, threshold: float
) -> JSON:
    """Return an observed threshold or explicit boundary censoring."""

    if len(levels) != len(rates) or not levels:
        raise ValueError("levels and rates must have equal non-zero length")
    ordered = sorted(zip(levels, rates, strict=True))
    if ordered[0][1] < threshold:
        return {"status": "below_minimum", "minimum_level": ordered[0][0]}
    below_index = next(
        (index for index, (_, rate) in enumerate(ordered) if rate < threshold),
        None,
    )
    if below_index is None:
        return {"status": "right_censored", "maximum_level": ordered[-1][0]}
    return {"status": "observed", "maximum_level": ordered[below_index - 1][0]}


def scaling_auc(levels: Sequence[int], rates: Sequence[float]) -> float:
    """Integrate rates over observed log2 scale and normalize the domain width."""

    if len(levels) != len(rates) or len(levels) < 2:
        raise ValueError("scaling AUC requires at least two paired levels")
    ordered = sorted((math.log2(level), rate) for level, rate in zip(levels, rates, strict=True))
    area = sum(
        (right_x - left_x) * (left_y + right_y) / 2.0
        for (left_x, left_y), (right_x, right_y) in zip(ordered[:-1], ordered[1:], strict=True)
    )
    width = ordered[-1][0] - ordered[0][0]
    return area / width


def holm_adjust(p_values: Mapping[str, float]) -> dict[str, float]:
    """Apply Holm's step-down family-wise error correction."""

    ordered = sorted(p_values.items(), key=lambda item: (item[1], item[0]))
    adjusted: dict[str, float] = {}
    previous = 0.0
    total = len(ordered)
    for rank, (name, value) in enumerate(ordered):
        candidate = min(1.0, value * (total - rank))
        previous = max(previous, candidate)
        adjusted[name] = previous
    return {name: adjusted[name] for name in p_values}


def _mean_tokens(rows: Sequence[Mapping[str, object]]) -> float:
    values: list[float] = []
    for row in rows:
        usage = row.get("usage")
        value = usage.get("total_tokens", 0) if isinstance(usage, dict) else 0
        values.append(float(value) if isinstance(value, (int, float)) else 0.0)
    return mean(values) if values else 0.0


def _registry_profile(rows: Sequence[Mapping[str, object]]) -> JSON:
    grouped: dict[int, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        registry_size = _condition(row).get("registry_size")
        if isinstance(registry_size, int):
            grouped[registry_size].append(row)
    levels = sorted(grouped)
    rates = [template_weighted_rate(grouped[level], "strict_vts") for level in levels]
    profile: JSON = {"levels": levels, "strict_vts": rates}
    if len(levels) >= 2:
        profile["scaling_auc"] = scaling_auc(levels, rates)
        profile["threshold_0_8"] = capacity_threshold(levels, rates, threshold=0.8)
    return profile


def analyze_rows(rows: Sequence[Mapping[str, object]]) -> JSON:
    """Generate deterministic track, status, cost, and paired summaries."""

    sorted_rows = sorted(rows, key=lambda row: str(row.get("condition_id", "")))
    condition_ids = [row.get("condition_id") for row in sorted_rows]
    if any(not isinstance(value, str) or not value for value in condition_ids):
        raise ValueError("every run row requires condition_id")
    if len(set(condition_ids)) != len(condition_ids):
        raise ValueError("analysis input contains duplicate condition IDs")
    by_track: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in sorted_rows:
        track = _condition(row).get("track")
        if not isinstance(track, str):
            raise ValueError("condition is missing track")
        by_track[track].append(row)
    track_summary: JSON = {}
    for track, track_rows in sorted(by_track.items()):
        track_summary[track] = {
            "rows": len(track_rows),
            "templates": len({_condition(row)["task_id"] for row in track_rows}),
            "template_weighted_strict_vts": template_weighted_rate(
                track_rows, "strict_vts"
            ),
            "capability_availability": template_weighted_rate(
                track_rows, "capability_availability"
            ),
            "mean_total_tokens": _mean_tokens(track_rows),
            "registry_profile": _registry_profile(track_rows),
        }
    representations = {
        str(_condition(row).get("representation")) for row in sorted_rows
    }
    paired_effects: JSON = {}
    for treatment, control in (
        ("typed", "generic_compact"),
        ("linked", "typed"),
        ("generic_compact", "raw"),
        ("typed", "raw"),
        ("linked", "raw"),
    ):
        if {treatment, control} <= representations:
            paired_effects[f"{treatment}_minus_{control}"] = paired_risk_difference(
                sorted_rows, treatment=treatment, control=control
            )
    status_counts = Counter(str(row.get("status", "unknown")) for row in sorted_rows)
    return {
        "rows": len(sorted_rows),
        "templates": len({_condition(row)["task_id"] for row in sorted_rows}),
        "tracks": track_summary,
        "status_counts": dict(sorted(status_counts.items())),
        "paired_effects": paired_effects,
    }
