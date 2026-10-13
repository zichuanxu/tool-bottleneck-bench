#!/usr/bin/env python3
"""Decomposition of released failures into harness limits and decisions."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
from math import fsum, sqrt
from pathlib import Path
import re
from statistics import fmean, median
import sys
from typing import Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

import analyze_failure_localization as localization  # noqa: E402
from bottleneckbench.analysis import _failure_stage  # noqa: E402
from bottleneckbench.core.schema import (  # noqa: E402
    Representation,
    canonical_json,
    stable_hash,
)
from bottleneckbench.design import (  # noqa: E402
    build_world,
    is_distractor,
    tasks_for_split,
    visible_menu,
)
from bottleneckbench.runtime import (  # noqa: E402
    _remember_records,
    compile_visible_observation,
    execute_capacity_tool,
    reference_execution,
    runtime_construct_audit,
)
from bottleneckbench.workflow_boundary import (  # noqa: E402
    WORKFLOW_BOUNDARY_CERTIFIED_CALL_LEVELS,
    WORKFLOW_BOUNDARY_LOCATOR_LEVELS,
)
from bottleneckbench.workflow_boundary_analysis import (  # noqa: E402
    _family_cluster_interval,
)


DEFAULT_MANIFEST = ROOT / "results/analysis/release-analysis-inputs.json"
DEFAULT_PANEL = ROOT / "results/analysis/release-panel.json"
DEFAULT_PROTOCOL = ROOT / "protocols/failure-mechanism-decomposition-2026-09-02.md"
DEFAULT_OUTPUT = ROOT / "results/analysis/failure-mechanism-decomposition.json"
PROBE_SOURCES = (
    ROOT / "src/bottleneckbench/design.py",
    ROOT / "src/bottleneckbench/runtime.py",
)

CAPACITY_BLOCKS = ("capacity", "candidate_capacity", "frontier_capacity")
FRONTIER_BLOCKS = (
    "workflow_frontier",
    "candidate_workflow_frontier",
    "frontier_workflow_frontier",
)
STRUCTURAL_BLOCKS = (
    "structural_holdout",
    "candidate_structural_additional",
    "frontier_structural_additional",
)
SCORED_BLOCKS = (
    *CAPACITY_BLOCKS,
    "repair",
    "candidate_repair",
    "frontier_repair",
    "sentinel",
    "candidate_sentinel",
    "frontier_sentinel",
    *FRONTIER_BLOCKS,
    *STRUCTURAL_BLOCKS,
)

HARNESS_LIMIT_ERRORS = (
    "budget_exceeded",
    "model_finish_length",
    "success_infeasible_under_call_budget",
    "turn_limit_exceeded",
)
REVIEW_HARNESS_LIMIT_ERRORS = (
    "budget_exceeded",
    "model_finish_length",
    "turn_limit_exceeded",
)
WRONG_CONCLUSION_ERRORS = ("answer_facts_invalid", "decision_invalid")
EMITTED_STAGES = (
    "availability",
    "selection",
    "workflow",
    "answer",
    "evidence",
    "safety",
    "governance",
    "budget",
    "completion",
)
COMPLETION_NO_CALL = "completion_no_call"
EPISODE_TOKEN_BUDGET = 128_000
COMPLETION_TOKEN_CAP = 1_024
EXTREME_VISIBLE = (8, 128)
EXTREME_WORKFLOW = ("low", "high")
EXTREME_REPRESENTATION = ("raw", "linked")
CONTROLLER_CALL_PREFIX = "structural-controller-"
FRONTIER_BOOTSTRAP_DRAWS = 10_000
# Every coordinate of the routed extreme cell except the visible-menu size; two rows
# sharing this key are the V=8 and V=128 twins of one assigned unit.
VISIBLE_TWIN_KEY_FIELDS = (
    "model",
    "family_id",
    "workflow",
    "representation",
    "outcome_variant",
    "prompt_variant",
)
FRONTIER_REACHABILITY_REFERENCE_CALLS = 18
ROUTE_PATTERN = re.compile(r"workspace=(\S+?), region=(\S+?), collection=(\S+?)[.\s]")
OPERATIONAL_READ_PREFIXES = ("search_", "get_")


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _condition(row: Mapping[str, object]) -> Mapping[str, object]:
    condition = row.get("condition")
    if not isinstance(condition, dict):
        raise ValueError("released row is missing a condition object")
    return condition


def _success(row: Mapping[str, object]) -> bool:
    verdict = row.get("verdict")
    return isinstance(verdict, dict) and verdict.get("verified_success") is True


def _errors(row: Mapping[str, object]) -> set[str]:
    errors = row.get("errors")
    return {str(error) for error in errors} if isinstance(errors, list) else set()


def _executed_calls(row: Mapping[str, object]) -> int:
    trace = row.get("trace")
    return len(trace) if isinstance(trace, list) else 0


def _attempted_calls(row: Mapping[str, object]) -> int:
    """Calls the episode paid for, refused attempts included; the token-cost unit."""

    attempts = row.get("call_attempts")
    return len(attempts) if isinstance(attempts, list) else 0


def _truncated(row: Mapping[str, object]) -> bool:
    return "model_finish_length" in _errors(row)


def _harness_limited(row: Mapping[str, object]) -> bool:
    return bool(_errors(row) & set(HARNESS_LIMIT_ERRORS))


def _wrong_conclusion(row: Mapping[str, object]) -> bool:
    return bool(_errors(row) & set(WRONG_CONCLUSION_ERRORS)) and not _harness_limited(row)


def _failure_accounting_label(row: Mapping[str, object]) -> str:
    """Assign one descriptive terminal bucket; the order is part of the label."""

    errors = _errors(row)
    for error, label in (
        ("model_finish_length", "completion_cap"),
        ("budget_exceeded", "token_or_call_budget"),
        ("turn_limit_exceeded", "turn_limit"),
        ("success_infeasible_under_call_budget", "call_budget_early_stop"),
    ):
        if error in errors:
            return label
    if errors & set(WRONG_CONCLUSION_ERRORS):
        return "wrong_conclusion"
    return "other_contract_failure"


def corrected_stage(row: Mapping[str, object]) -> str:
    """Analysis-layer stage plus an explicit no-call label for truncated openings."""

    if _success(row):
        return "success"
    if _truncated(row) and _executed_calls(row) == 0:
        return COMPLETION_NO_CALL
    return _failure_stage(row)


def _usage(row: Mapping[str, object], field: str) -> int:
    usage = row.get("usage")
    value = usage.get(field) if isinstance(usage, dict) else None
    return int(value) if isinstance(value, (int, float)) else 0


def _efficiency(row: Mapping[str, object], field: str) -> object:
    payload = row.get("efficiency")
    return payload.get(field) if isinstance(payload, dict) else None


def _quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    position = probability * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _rank(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        for index in order[start:end]:
            ranks[index] = (start + end - 1) / 2
        start = end
    return ranks


def _pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 3:
        raise ValueError("correlation requires paired vectors of length at least three")
    left_mean, right_mean = fmean(left), fmean(right)
    # fsum throughout: builtins.sum gained Neumaier summation in CPython 3.12,
    # which moves these accumulations by ~1 ulp between interpreter versions.
    left_ss = fsum((value - left_mean) ** 2 for value in left)
    right_ss = fsum((value - right_mean) ** 2 for value in right)
    if left_ss == 0 or right_ss == 0:
        return None
    covariance = fsum(
        (a - left_mean) * (b - right_mean) for a, b in zip(left, right, strict=True)
    )
    return covariance / sqrt(left_ss * right_ss)


def _spearman(left: Sequence[float], right: Sequence[float]) -> float | None:
    return _pearson(_rank(left), _rank(right))


def _load(manifest: Mapping[str, object], blocks: Sequence[str]) -> list[dict[str, object]]:
    """Load manifest-bound rows, refusing any input whose digest moved."""

    inputs = manifest.get("inputs")
    if not isinstance(inputs, dict):
        raise ValueError("release manifest lacks input groups")
    rows: list[dict[str, object]] = []
    for block in blocks:
        for entry in inputs[block]:
            path = ROOT / str(entry["path"])
            if _sha(path) != entry["sha256"]:
                raise ValueError(f"manifest-bound release input changed: {path}")
            rows.extend(
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
    return rows


# --------------------------------------------------------------- (a) census


def release_failure_census(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    failures = [row for row in rows if not _success(row)]
    if not failures:
        raise ValueError("failure census requires failed episodes")
    harness = sum(map(_harness_limited, failures))
    review_harness = sum(
        bool(_errors(row) & set(REVIEW_HARNESS_LIMIT_ERRORS)) for row in failures
    )
    zero_call = sum(_executed_calls(row) == 0 for row in failures)
    first_response_truncated = sum(
        _truncated(row) and _executed_calls(row) == 0 for row in failures
    )
    wrong = sum(map(_wrong_conclusion, failures))
    any_wrong = sum(bool(_errors(row) & set(WRONG_CONCLUSION_ERRORS)) for row in failures)
    accounting = Counter(_failure_accounting_label(row) for row in failures)
    raw_groups = (
        {"model_finish_length"},
        {"budget_exceeded"},
        {"turn_limit_exceeded"},
        {"success_infeasible_under_call_budget"},
        set(WRONG_CONCLUSION_ERRORS),
    )
    overlap = sum(
        sum(bool(_errors(row) & group) for group in raw_groups) > 1
        for row in failures
    )
    return {
        "scored_rows": len(rows),
        "failures": len(failures),
        "harness_limit_failures": harness,
        "harness_limit_share": harness / len(failures),
        "harness_limit_failures_without_call_budget_infeasibility": review_harness,
        "harness_limit_share_without_call_budget_infeasibility": review_harness / len(failures),
        "zero_call_failures": zero_call,
        "zero_call_share": zero_call / len(failures),
        "first_response_truncated_failures": first_response_truncated,
        "first_response_truncated_share": first_response_truncated / len(failures),
        "wrong_conclusion_failures": wrong,
        "wrong_conclusion_share": wrong / len(failures),
        "wrong_conclusion_failures_including_harness_limited": any_wrong,
        "wrong_conclusion_share_including_harness_limited": any_wrong / len(failures),
        "harness_limit_error_counts": {
            error: sum(error in _errors(row) for row in failures)
            for error in HARNESS_LIMIT_ERRORS
        },
        "wrong_conclusion_error_counts": {
            error: sum(error in _errors(row) for row in failures)
            for error in WRONG_CONCLUSION_ERRORS
        },
        "exhaustive_accounting": {
            "precedence": [
                "completion_cap",
                "token_or_call_budget",
                "turn_limit",
                "call_budget_early_stop",
                "wrong_conclusion",
                "other_contract_failure",
            ],
            "categories": {
                label: {
                    "count": accounting[label],
                    "share": accounting[label] / len(failures),
                }
                for label in (
                    "completion_cap",
                    "token_or_call_budget",
                    "turn_limit",
                    "call_budget_early_stop",
                    "wrong_conclusion",
                    "other_contract_failure",
                )
            },
            "raw_flag_overlap_rows": overlap,
            "definition": "mutually exclusive descriptive terminal flags in declared precedence; categories are neither causal sources nor repair assignments",
        },
    }


# ------------------------------------------- (b) visible-load extreme cells


def _extreme(rows: Sequence[Mapping[str, object]], models: set[str]) -> list[Mapping[str, object]]:
    selected = []
    for row in rows:
        condition = _condition(row)
        if (
            row.get("study_role") != "primary"
            or str(condition.get("model")) not in models
            or "routed" not in str(condition.get("harness"))
            or str(condition.get("representation")) not in EXTREME_REPRESENTATION
            or int(condition.get("visible_tools", 0)) not in EXTREME_VISIBLE
            or str(condition.get("workflow")) not in EXTREME_WORKFLOW
        ):
            continue
        selected.append(row)
    return selected


def _cell_summary(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    failures = [row for row in rows if not _success(row)]
    stages = Counter(corrected_stage(row) for row in failures)
    non_truncated = [row for row in rows if not _truncated(row)]
    return {
        "rows": len(rows),
        "verified_success_rate": fmean(float(_success(row)) for row in rows),
        "failures": len(failures),
        "truncated_before_first_call": sum(
            _truncated(row) and _executed_calls(row) == 0 for row in failures
        ),
        "truncated_failures": sum(map(_truncated, failures)),
        "zero_call_failures": sum(_executed_calls(row) == 0 for row in failures),
        "harness_limit_failures": sum(map(_harness_limited, failures)),
        "wrong_conclusion_failures": sum(map(_wrong_conclusion, failures)),
        "distractor_call_episodes": sum(
            any(
                isinstance(event, dict) and event.get("distractor") is True
                for event in row.get("trace", [])  # type: ignore[union-attr]
            )
            for row in rows
        ),
        "truncation_rate": fmean(float(_truncated(row)) for row in rows),
        "non_truncated_rows": len(non_truncated),
        "verified_success_rate_non_truncated": (
            fmean(float(_success(row)) for row in non_truncated) if non_truncated else None
        ),
        "corrected_first_failure_stages": dict(sorted(stages.items())),
    }


def _contrast(
    rows: Sequence[Mapping[str, object]], axis: str, low: object, high: object
) -> dict[str, object]:
    def side(value: object, keep: Callable[[Mapping[str, object]], bool]) -> list[Mapping[str, object]]:
        return [
            row
            for row in rows
            if _condition(row).get(axis) == value and keep(row)
        ]

    everything = lambda row: True  # noqa: E731
    intact = lambda row: not _truncated(row)  # noqa: E731
    low_all, high_all = side(low, everything), side(high, everything)
    low_intact, high_intact = side(low, intact), side(high, intact)
    return {
        "estimate": fmean(float(_success(row)) for row in high_all)
        - fmean(float(_success(row)) for row in low_all),
        "estimate_non_truncated": fmean(float(_success(row)) for row in high_intact)
        - fmean(float(_success(row)) for row in low_intact),
        "rows": {"low": len(low_all), "high": len(high_all)},
        "non_truncated_rows": {"low": len(low_intact), "high": len(high_intact)},
    }


def _configuration_rows(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    table: dict[str, object] = {}
    for model in sorted({str(_condition(row).get("model")) for row in rows}):
        selected = [row for row in rows if str(_condition(row).get("model")) == model]
        low = [row for row in selected if int(_condition(row)["visible_tools"]) == 8]
        high = [row for row in selected if int(_condition(row)["visible_tools"]) == 128]
        failures = [row for row in selected if not _success(row)]
        stages = Counter(corrected_stage(row) for row in failures)
        table[model] = {
            "rows": len(selected),
            "visible_effect": fmean(float(_success(row)) for row in high)
            - fmean(float(_success(row)) for row in low),
            "truncation_rate_visible_128": fmean(float(_truncated(row)) for row in high),
            "truncation_rate_visible_8": fmean(float(_truncated(row)) for row in low),
            "truncated_failures_visible_128": sum(map(_truncated, [row for row in high if not _success(row)])),
            "zero_call_failures_visible_128": sum(
                _executed_calls(row) == 0 for row in high if not _success(row)
            ),
            "failures_visible_128": sum(1 for row in high if not _success(row)),
            "dominant_corrected_stage": (
                min(stages, key=lambda name: (-stages[name], name)) if stages else None
            ),
        }
    return table


def _visible_twins(
    rows: Sequence[Mapping[str, object]],
) -> list[tuple[Mapping[str, object], Mapping[str, object]]]:
    """Pair each routed extreme cell with its V twin on every other coordinate."""

    grouped: dict[tuple[str, ...], dict[int, Mapping[str, object]]] = defaultdict(dict)
    for row in rows:
        condition = _condition(row)
        key = tuple(str(condition[field]) for field in VISIBLE_TWIN_KEY_FIELDS)
        visible = int(condition["visible_tools"])
        if visible in grouped[key]:
            raise ValueError("duplicate visible-load twin cell")
        grouped[key][visible] = row
    pairs = [
        (cells[EXTREME_VISIBLE[0]], cells[EXTREME_VISIBLE[1]])
        for _, cells in sorted(grouped.items())
        if set(cells) == set(EXTREME_VISIBLE)
    ]
    if 2 * len(pairs) != len(rows):
        raise ValueError("routed extreme cells do not close into complete visible-load twins")
    return pairs


def visible_twin_contrast(
    pairs: Sequence[tuple[Mapping[str, object], Mapping[str, object]]],
) -> dict[str, object]:
    """Exact matched-twin decomposition, its Manski bounds, and the intact-pair contrast.

    The paper's marginal non-truncated contrast drops V=128 episodes at many times
    the V=8 rate, so it is unmatched.  These three quantities replace it: the
    decomposition attributes each discordant pair, the bounds assume only that
    each truncated failure could have resolved to either outcome -- independently
    in each arm -- under a relaxed completion budget, and the intact-pair contrast
    keeps the matching.  ``common_resolution_interval`` narrows the same quantity
    under the *additional* assumption that truncated failures resolve alike in
    both arms; it is a strictly stronger assumption and a strictly shorter
    interval, so it is reported alongside the bounds rather than in place of them.
    """

    total = len(pairs)
    if not total:
        raise ValueError("visible-load twin decomposition requires matched pairs")
    lost = [pair for pair in pairs if _success(pair[0]) and not _success(pair[1])]
    gained = [pair for pair in pairs if not _success(pair[0]) and _success(pair[1])]
    lost_truncated = [pair for pair in lost if _truncated(pair[1])]
    intact = [pair for pair in pairs if not _truncated(pair[0]) and not _truncated(pair[1])]
    low_successes = sum(_success(low) for low, _ in pairs)
    high_successes = sum(_success(high) for _, high in pairs)
    low_truncated_failures = sum(
        _truncated(low) and not _success(low) for low, _ in pairs
    )
    high_truncated_failures = sum(
        _truncated(high) and not _success(high) for _, high in pairs
    )
    observed = (high_successes - low_successes) / total
    return {
        "matched_pair_decomposition": {
            "pairs": total,
            "pair_key": list(VISIBLE_TWIN_KEY_FIELDS),
            "lost": len(lost),
            "lost_points": len(lost) / total,
            "lost_with_truncated_visible_128_twin": len(lost_truncated),
            "lost_with_truncated_visible_128_twin_points": len(lost_truncated) / total,
            "lost_without_truncated_visible_128_twin": len(lost) - len(lost_truncated),
            "lost_without_truncated_visible_128_twin_points": (
                len(lost) - len(lost_truncated)
            ) / total,
            "gained": len(gained),
            "gained_points": len(gained) / total,
            "net": observed,
            "definition": "a pair is lost when the V=8 twin verifies and the V=128 twin does not, and gained in the reverse case; the split of the lost pairs is by whether the V=128 twin carries model_finish_length",
        },
        "manski_bounds": {
            "lower": (high_successes - (low_successes + low_truncated_failures)) / total,
            "upper": ((high_successes + high_truncated_failures) - low_successes) / total,
            "truncated_failures_visible_8": low_truncated_failures,
            "truncated_failures_visible_128": high_truncated_failures,
            "name": "truncation-resolution bound",
            "assumption": "holds every currently non-truncated trajectory and outcome fixed while allowing each currently truncated failure to resolve to either outcome independently in each arm; it is not an estimate from a larger-cap intervention",
        },
        "common_resolution_interval": {
            "lower": observed,
            "upper": (
                (high_successes + high_truncated_failures)
                - (low_successes + low_truncated_failures)
            ) / total,
            "assumption": "narrower interval under the additional assumption that truncated failures resolve alike in both arms",
        },
        "paired_non_truncated": {
            "pairs": len(intact),
            "estimate": (
                sum(_success(high) for _, high in intact)
                - sum(_success(low) for low, _ in intact)
            ) / len(intact),
            "definition": "the contrast over the pairs in which neither twin truncated; unlike the marginal non-truncated contrast it keeps the matching, but it still conditions on a post-treatment variable",
        },
    }


def visible_load_decomposition(
    capacity: Sequence[Mapping[str, object]],
    panel_models: set[str],
) -> dict[str, object]:
    panel = _extreme(capacity, panel_models)
    if len(panel) != len(panel_models) * 288:
        raise ValueError("routed extreme-cell matrix does not close at 288 rows per configuration")
    # The core panel is the only panel: the ``frozen_*`` keys are retained names for
    # the same full-panel quantities so the released schema stays stable.
    frozen = panel
    frozen_models = panel_models
    cells = {
        f"V{visible}_{workflow}": _cell_summary(
            [
                row
                for row in frozen
                if int(_condition(row)["visible_tools"]) == visible
                and str(_condition(row)["workflow"]) == workflow
            ]
        )
        for visible in EXTREME_VISIBLE
        for workflow in EXTREME_WORKFLOW
    }
    per_arm = len(frozen) // 2
    truncated_high = sum(
        _truncated(row)
        for row in frozen
        if int(_condition(row)["visible_tools"]) == 128 and not _success(row)
    )
    truncated_low = sum(
        _truncated(row)
        for row in frozen
        if int(_condition(row)["visible_tools"]) == 8 and not _success(row)
    )
    panel_table = _configuration_rows(panel)
    frozen_table = panel_table
    zero_truncation = sorted(
        model
        for model, row in frozen_table.items()
        if row["truncation_rate_visible_128"] == 0 and row["truncation_rate_visible_8"] == 0  # type: ignore[index]
    )
    panel_zero_truncation = sorted(
        model
        for model, row in panel_table.items()
        if row["truncation_rate_visible_128"] == 0 and row["truncation_rate_visible_8"] == 0  # type: ignore[index]
    )
    effects = [float(row["visible_effect"]) for row in panel_table.values()]  # type: ignore[index]
    rates = [float(row["truncation_rate_visible_128"]) for row in panel_table.values()]  # type: ignore[index]
    return {
        "frozen_configurations": len(frozen_models),
        "panel_configurations": len(panel_models),
        "frozen_rows": len(frozen),
        "panel_rows": len(panel),
        "rows_per_visible_arm": per_arm,
        "cells": cells,
        **visible_twin_contrast(_visible_twins(frozen)),
        "contrasts": {
            "visible_128_minus_8": _contrast(frozen, "visible_tools", 8, 128),
            "workflow_high_minus_low": _contrast(frozen, "workflow", "low", "high"),
        },
        "truncation_attributable_points": (truncated_high - truncated_low) / per_arm,
        "truncated_failures_visible_128": truncated_high,
        "truncated_failures_visible_8": truncated_low,
        "truncation_rate_visible_8": fmean(
            float(_truncated(row)) for row in frozen if int(_condition(row)["visible_tools"]) == 8
        ),
        "truncation_rate_visible_128": fmean(
            float(_truncated(row)) for row in frozen if int(_condition(row)["visible_tools"]) == 128
        ),
        "by_frozen_configuration": frozen_table,
        "by_panel_configuration": panel_table,
        "zero_truncation_frozen_configurations": zero_truncation,
        "zero_truncation_frozen_visible_effects": {
            model: frozen_table[model]["visible_effect"] for model in zero_truncation  # type: ignore[index]
        },
        "zero_truncation_panel_configurations": panel_zero_truncation,
        "visible_effect_versus_truncation_rate": {
            "configurations": len(effects),
            "pearson": _pearson(effects, rates) if len(effects) >= 3 else None,
            "spearman": _spearman(effects, rates) if len(effects) >= 3 else None,
        },
        "completion_token_cap": COMPLETION_TOKEN_CAP,
    }


# ------------------------------------------------------- (c) corrected stages


def corrected_stage_analysis(
    manifest: Mapping[str, object],
    scored: Sequence[Mapping[str, object]],
    *,
    bootstrap_samples: int,
    seed: int,
) -> dict[str, object]:
    emitted = Counter()
    for row in scored:
        verdict = row.get("verdict")
        stage = verdict.get("failure_stage") if isinstance(verdict, dict) else None
        emitted[str(stage) if isinstance(stage, str) and stage else "success"] += 1
    unexpected = sorted(set(emitted) - {*EMITTED_STAGES, "success"})
    if unexpected:
        raise ValueError(f"released rows emit an unregistered failure stage: {unexpected}")

    # Replicate analyze_failure_localization exactly, swapping in the corrected labeler.
    original = localization._stage
    localization._stage = corrected_stage
    try:
        packets, excluded, multiple = localization._packets(localization._paths(manifest))
    finally:
        localization._stage = original
    predictions = localization._predict(packets, "intent")
    reports = {}
    for name, field in (
        ("terminal_only", "terminal_prediction"),
        ("first_failure_stage", "first_stage_prediction"),
    ):
        report = localization._metrics(predictions, field)
        report["intent_cluster_bootstrap_ci95"] = localization._bootstrap(
            predictions, field, bootstrap_samples, seed
        )
        reports[name] = report
    by_pressure: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for packet in packets:
        by_pressure[packet["label"]][packet["stage"]] += 1
    corrected = Counter(corrected_stage(row) for row in scored)
    return {
        "diagnostic_label_version": "analysis-failure-stage-v1",
        "emitted_failure_stage_counts": dict(sorted(emitted.items())),
        "emitted_failure_stages_observed": sorted(set(emitted) - {"success"}),
        "registered_emitted_stages": list(EMITTED_STAGES),
        "release_wide": {
            # Release-wide tally of the corrected labeller against the evaluator's own
            # labels: the two disagree wholesale, so the corrected column is a different
            # taxonomy rather than a relabelling of the registered stages.
            "scored_rows": len(scored),
            "corrected_stage_counts": dict(sorted(corrected.items())),
            "stages_the_evaluator_never_emits": sorted(set(corrected) - set(emitted)),
            "registered_stages_the_evaluator_emits_zero_times": sorted(
                stage for stage in EMITTED_STAGES if not emitted[stage]
            ),
            "definition": "bottleneckbench.analysis._failure_stage over every scored row, with completion_no_call substituted for a truncation before the first executed call; its budget and arguments categories are diagnostic and are not the registered budget obligation",
        },
        "packets": len(packets),
        "intent_clusters": len({packet["intent"] for packet in packets}),
        "labels": dict(sorted(Counter(packet["label"] for packet in packets).items())),
        "excluded_infrastructure_clusters": excluded,
        "excluded_multi_failure_clusters": multiple,
        "corrected_stage_by_assigned_pressure": {
            label: dict(sorted(counts.items())) for label, counts in sorted(by_pressure.items())
        },
        "methods": reports,
        "accuracy_gain": float(reports["first_failure_stage"]["accuracy"])
        - float(reports["terminal_only"]["accuracy"]),
        "bootstrap": {"samples": bootstrap_samples, "seed": seed, "cluster": "intent"},
    }


# ----------------------------------------------------------- (d) frontier


def _least_squares_quadratic(
    calls: Sequence[float], tokens: Sequence[float]
) -> tuple[float, float]:
    """Ordinary least squares for t = a n^2 + b n through the origin.

    Two normal equations, solved in closed form; fsum keeps the accumulation
    identical between CPython 3.11 and 3.12.
    """

    fourth = fsum(value**4 for value in calls)
    third = fsum(value**3 for value in calls)
    second = fsum(value**2 for value in calls)
    weighted_square = fsum(n * n * t for n, t in zip(calls, tokens, strict=True))
    weighted = fsum(n * t for n, t in zip(calls, tokens, strict=True))
    determinant = fourth * second - third * third
    if determinant == 0:
        raise ValueError("quadratic token fit is singular")
    return (
        (weighted_square * second - weighted * third) / determinant,
        (fourth * weighted - third * weighted_square) / determinant,
    )


def frontier_reachability(
    rows: Sequence[Mapping[str, object]],
    level_rows: Sequence[Mapping[str, object]],
    *,
    target_calls: int,
) -> dict[str, object]:
    """Was the top call level reachable at all under the frozen episode budget?

    Three independent routes, all from released rows: the observed maximum, a
    flat-rate lower bound at the cheapest observed cost per call, and a
    least-squares projection of the token cost of a call program.  Calls are
    counted as attempts, because an attempt is what the episode pays for; the
    executed-call fit is reported beside it so the choice is visible.
    """

    cheapest: dict[int, int] = {}
    for row in rows:
        calls = _attempted_calls(row)
        total = _usage(row, "total_tokens")
        cheapest[calls] = min(cheapest.get(calls, total), total)
    reference = FRONTIER_REACHABILITY_REFERENCE_CALLS
    highest = max(cheapest)
    attempted = [float(_attempted_calls(row)) for row in level_rows]
    executed = [float(_executed_calls(row)) for row in level_rows]
    tokens = [float(_usage(row, "total_tokens")) for row in level_rows]
    rates = [t / n for n, t in zip(attempted, tokens, strict=True) if n >= reference]
    if not rates or reference not in cheapest or highest <= reference:
        raise ValueError("frontier reachability requires episodes at the reference call count")
    attempted_a, attempted_b = _least_squares_quadratic(attempted, tokens)
    executed_a, executed_b = _least_squares_quadratic(executed, tokens)
    return {
        "target_calls": target_calls,
        "rows": len(level_rows),
        "executed_calls_max": int(max(executed)),
        "attempted_calls_max": highest,
        "reference_calls": reference,
        "cheapest_tokens_per_call_at_reference_or_above": min(rates),
        "flat_rate_lower_bound_tokens_at_target": min(rates) * target_calls,
        "cheapest_total_tokens_at_reference_calls": cheapest[reference],
        "episode_token_budget_left_after_reference_calls": (
            EPISODE_TOKEN_BUDGET - cheapest[reference]
        ),
        "cheapest_marginal_tokens_per_call_above_reference": (
            (cheapest[highest] - cheapest[reference]) / (highest - reference)
        ),
        "quadratic_fit": {
            "form": "t = a n^2 + b n, ordinary least squares over the rows of this level",
            "attempted_calls": {
                "a": attempted_a,
                "b": attempted_b,
                "projected_tokens_at_target": attempted_a * target_calls**2
                + attempted_b * target_calls,
            },
            "executed_calls": {
                "a": executed_a,
                "b": executed_b,
                "projected_tokens_at_target": executed_a * target_calls**2
                + executed_b * target_calls,
            },
            "reported_unit": "attempted_calls",
        },
        "reachable_within_episode_token_budget": (
            min(rates) * target_calls <= EPISODE_TOKEN_BUDGET
        ),
        "claim_boundary": "an ex-ante feasibility statement about the frozen protocol: no released episode executed the target number of calls, and the cheapest observed cost per call already exceeds the episode token budget at that count, so the level's success rate is a property of the budget rather than of any configuration",
    }


def frontier_budget_boundary(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    calls_by_locator = dict(
        zip(WORKFLOW_BOUNDARY_LOCATOR_LEVELS, WORKFLOW_BOUNDARY_CERTIFIED_CALL_LEVELS, strict=True)
    )
    grouped: dict[int, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[int(_condition(row)["locator_nodes"])].append(row)
    levels: dict[str, object] = {}
    for locator in WORKFLOW_BOUNDARY_LOCATOR_LEVELS:
        selected = grouped[locator]
        calls = [_executed_calls(row) for row in selected]
        prompt = [_usage(row, "prompt_tokens") for row in selected]
        total = [_usage(row, "total_tokens") for row in selected]
        levels[str(calls_by_locator[locator])] = {
            "locator_nodes": locator,
            "rows": len(selected),
            "verified_success_rate": fmean(float(_success(row)) for row in selected),
            "status_counts": dict(sorted(Counter(str(row.get("status")) for row in selected).items())),
            "budget_exceeded": sum("budget_exceeded" in _errors(row) for row in selected),
            "success_infeasible_under_call_budget": sum(
                "success_infeasible_under_call_budget" in _errors(row) for row in selected
            ),
            "executed_calls_median": median(calls),
            "executed_calls_iqr": [_quantile(calls, 0.25), _quantile(calls, 0.75)],
            "executed_calls_range": [min(calls), max(calls)],
            "prompt_tokens_median": median(prompt),
            "prompt_tokens_max": max(prompt),
            "total_tokens_median": median(total),
            "total_tokens_max": max(total),
            "episodes_over_episode_token_budget": sum(
                value > EPISODE_TOKEN_BUDGET for value in total
            ),
            "episodes_over_episode_token_budget_share": fmean(
                float(value > EPISODE_TOKEN_BUDGET) for value in total
            ),
        }
    baseline = WORKFLOW_BOUNDARY_LOCATOR_LEVELS[0]
    contrasts: dict[str, object] = {}
    for locator in WORKFLOW_BOUNDARY_LOCATOR_LEVELS[1:]:
        paired: dict[str, list[float]] = defaultdict(list)
        indexed = {
            (str(_condition(row)["model"]), str(_condition(row)["family_id"]), int(_condition(row)["locator_nodes"])): row
            for row in rows
        }
        for model, family, level in sorted(indexed):
            if level != locator:
                continue
            lower = indexed.get((model, family, baseline))
            if lower is not None:
                paired[family].append(
                    float(_success(indexed[(model, family, level)])) - float(_success(lower))
                )
        values = [value for family_values in paired.values() for value in family_values]
        # The effective seed is derived, not declared: record it so the interval is
        # reproducible from the artifact alone (protocol note "Seeds").
        derived_seed = int(
            stable_hash(
                {
                    "study": "workflow-boundary",
                    "level": locator,
                    "draws": FRONTIER_BOOTSTRAP_DRAWS,
                }
            )[:16],
            16,
        )
        low, high = _family_cluster_interval(
            paired, draws=FRONTIER_BOOTSTRAP_DRAWS, seed=derived_seed
        )
        contrasts[f"calls_{calls_by_locator[locator]}_minus_calls_{calls_by_locator[baseline]}"] = {
            "estimate": fmean(values),
            "ci95_family_cluster": [low, high],
            "bootstrap": {
                "draws": FRONTIER_BOOTSTRAP_DRAWS,
                "cluster": "family",
                "seed": derived_seed,
                "seed_source": 'stable_hash({"study": "workflow-boundary", "level": <locator_nodes>, "draws": <draws>})[:16] read as hexadecimal',
            },
            "family_clusters": len(paired),
            "matched_model_family_pairs": len(values),
            "family_values": {
                family: fmean(family_values) for family, family_values in sorted(paired.items())
            },
        }
    return {
        "rows": len(rows),
        "configurations": len({str(_condition(row)["model"]) for row in rows}),
        "episode_token_budget": EPISODE_TOKEN_BUDGET,
        "levels": levels,
        "paired_contrasts": contrasts,
        "reachability": frontier_reachability(
            rows,
            grouped[WORKFLOW_BOUNDARY_LOCATOR_LEVELS[-1]],
            target_calls=calls_by_locator[WORKFLOW_BOUNDARY_LOCATOR_LEVELS[-1]],
        ),
        "bootstrap": {
            "draws": FRONTIER_BOOTSTRAP_DRAWS,
            "cluster": "family",
            "seed_source": "derived per level; each paired contrast records its effective seed",
        },
    }


# --------------------------------------------------------- (e) structural


def _structural_arms(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    grouped: dict[tuple[str, str, int], list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        condition = _condition(row)
        grouped[
            (
                str(condition["orchestration"]),
                str(condition["delivery"]),
                int(condition["certified_calls"]),
            )
        ].append(row)
    arms: dict[str, object] = {}
    for (orchestration, delivery, certified), selected in sorted(grouped.items()):
        controller = [
            sum(
                str(attempt.get("call_id", "")).startswith(CONTROLLER_CALL_PREFIX)
                for attempt in row.get("call_attempts", [])  # type: ignore[union-attr]
            )
            for row in selected
        ]
        model_issued = [
            len(row.get("call_attempts", [])) - value  # type: ignore[arg-type]
            for row, value in zip(selected, controller, strict=True)
        ]
        arms[f"{orchestration}|{delivery}|calls_{certified}"] = {
            "orchestration": orchestration,
            "delivery": delivery,
            "certified_calls": certified,
            "rows": len(selected),
            "verified_success_rate": fmean(float(_success(row)) for row in selected),
            "mean_model_requests": fmean(
                float(_efficiency(row, "model_requests_attempted") or 0) for row in selected
            ),
            "mean_executed_tool_calls": fmean(float(_executed_calls(row)) for row in selected),
            "mean_total_tokens": fmean(float(_usage(row, "total_tokens")) for row in selected),
            "episodes_over_episode_token_budget": sum(
                _usage(row, "total_tokens") > EPISODE_TOKEN_BUDGET for row in selected
            ),
            "mean_controller_issued_calls": fmean(map(float, controller)),
            "mean_model_issued_calls": fmean(map(float, model_issued)),
        }
    return arms


def structural_call_attribution(
    rows: Sequence[Mapping[str, object]], confirmatory_models: set[str]
) -> dict[str, object]:
    confirmatory = [
        row for row in rows if str(_condition(row)["model"]) in confirmatory_models
    ]
    if not confirmatory:
        raise ValueError("structural attribution requires the confirmatory holdout rows")
    return {
        "rows": len(rows),
        "configurations": len({str(_condition(row)["model"]) for row in rows}),
        "controller_call_id_prefix": CONTROLLER_CALL_PREFIX,
        "episode_token_budget": EPISODE_TOKEN_BUDGET,
        "arms": _structural_arms(rows),
        "confirmatory_only": {
            # The pooled arms mix the four-configuration confirmatory holdout with the
            # zero-weight seven-configuration auxiliary extension; a sentence inside the
            # confirmatory paragraph must quote this restriction instead.
            "configurations": sorted(confirmatory_models),
            "rows": len(confirmatory),
            "arms": _structural_arms(confirmatory),
            "claim_boundary": "the same descriptive resource means restricted to the pre-registered structural holdout; no contrast is re-tested and the class of the block is unchanged",
        },
    }


# ------------------------------------------------------ (f) context confound


def context_confound(capacity: Sequence[Mapping[str, object]]) -> dict[str, object]:
    by_policy: dict[str, object] = {}
    for policy in ("routed", "fixed"):
        levels: dict[str, object] = {}
        for visible in sorted({int(_condition(row)["visible_tools"]) for row in capacity}):
            selected = [
                row
                for row in capacity
                if policy in str(_condition(row)["harness"])
                and int(_condition(row)["visible_tools"]) == visible
            ]
            if not selected:
                continue
            exposure = [
                int(value[0])
                for row in selected
                if isinstance(value := _efficiency(row, "request_tool_schema_bytes"), list) and value
            ]
            reported = [
                _usage(row, "prompt_tokens")
                for row in selected
                if _usage(row, "prompt_tokens") > 0
            ]
            levels[str(visible)] = {
                "rows": len(selected),
                "mean_single_exposure_schema_bytes": fmean(map(float, exposure)),
                "median_single_exposure_schema_bytes": median(exposure),
                "mean_cumulative_schema_bytes": fmean(
                    float(_efficiency(row, "tool_schema_bytes") or 0) for row in selected
                ),
                "mean_prompt_tokens": fmean(float(_usage(row, "prompt_tokens")) for row in selected),
                "mean_prompt_tokens_reported": fmean(map(float, reported)),
                "rows_without_reported_prompt_tokens": len(selected) - len(reported),
                "mean_tool_schema_exposures": fmean(
                    float(_efficiency(row, "tool_schema_exposures") or 0) for row in selected
                ),
            }
        by_policy[policy] = levels
    return by_policy


def representation_sizes() -> dict[str, object]:
    tasks = tasks_for_split("main")
    blocks = ("objects", "provenance", "field_dictionary", "type_dictionary", "links", "relation_dictionary")
    sizes: dict[str, list[int]] = defaultdict(list)
    block_sizes: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for task in tasks:
        menu = {tool.name: tool for tool in visible_menu(task, 8)}
        for representation in Representation:
            world = build_world(task)
            seen: dict[str, object] = {}
            for step in task.program:
                tool = menu[step.tool_name]
                result = execute_capacity_tool(world, task, tool, step.argument_map)
                _remember_records(seen, tool.connector, result)
                observation = compile_visible_observation(
                    result, tool.connector, seen, representation, world.snapshot_hash
                )
                sizes[representation.value].append(len(canonical_json(observation)))
                for block in blocks:
                    block_sizes[representation.value][block].append(
                        len(canonical_json(observation.get(block)))
                    )
    raw_mean = fmean(map(float, sizes["raw"]))
    return {
        "observations_per_representation": len(sizes["raw"]),
        "mean_observation_bytes": {
            name: fmean(map(float, values)) for name, values in sorted(sizes.items())
        },
        "mean_block_bytes": {
            name: {block: fmean(map(float, values)) for block, values in sorted(blocks_.items())}
            for name, blocks_ in sorted(block_sizes.items())
        },
        "relative_to_raw": {
            name: fmean(map(float, values)) / raw_mean - 1.0
            for name, values in sorted(sizes.items())
        },
    }


# ----------------------------------------------------- (g) mutation census


def mutation_census() -> dict[str, object]:
    """Enumerate the shipped mutation suite by class from the runtime contract."""

    tasks = tasks_for_split("main")
    census: Counter[str] = Counter()
    for task in tasks:
        _, _, trace, _ = reference_execution(task)
        census["flag_availability_completion_budget"] += 3
        census["missing_program_step"] += len(trace)
        census["dependency_order_swap"] += sum(len(step.depends_on) for step in task.program)
        census["trace_state_hash"] += 1 if trace else 0
        census["evidence_version"] += 1 if any(
            event.evidence and event.tool.startswith("get_") for event in trace
        ) else 0
        census["answer_payload"] += 6
        census["ambiguous_doubled_answer"] += 1
        census["unsafe_state"] += 1
        census["unrequested_mutation_event"] += 1
    harness_supplied = (
        "flag_availability_completion_budget",
        "trace_state_hash",
        "evidence_version",
        "unrequested_mutation_event",
    )
    total = sum(census.values())
    inaccessible = sum(census[name] for name in harness_supplied)
    # The shipped construct audit is deterministic and offline; it independently
    # counts the same mutation suite, so it also cross-checks the census above.
    audit = runtime_construct_audit("main")
    orders = {
        check["name"]: check["details"]["valid_alternative_orders"]
        for check in audit["checks"]
        if "valid_alternative_orders" in check.get("details", {})
    }
    evaluation = next(check for check in audit["checks"] if check["name"] == "exact_evaluation")
    if len(set(orders.values())) != 1 or int(evaluation["details"]["mutations"]) != total:
        raise ValueError("construct audit disagrees with the mutation census")
    return {
        "tasks": len(tasks),
        "accepted_certified_orders": next(iter(orders.values())),
        "construct_audit": audit["audit"],
        "construct_audit_passed": bool(audit["passed"]),
        "by_class": dict(sorted(census.items())),
        "harness_supplied_classes": list(harness_supplied),
        "mutations": total,
        "harness_supplied_mutations": inaccessible,
        "model_producible_mutations": total - inaccessible,
        "model_producible_share": (total - inaccessible) / total,
    }


# ------------------------------------------------------- (h) leakage probes


def route_string_recoverability() -> dict[str, object]:
    tasks = tasks_for_split("main")
    sizes = (8, 32, 128)
    recovered = 0
    with_route = 0
    mismatches: list[str] = []
    for task in tasks:
        match = ROUTE_PATTERN.search(task.request)
        if match is None:
            mismatches.append(f"{task.task_id}:no_route_in_request")
            continue
        with_route += 1
        needle = (
            f"workspace={match.group(1)}, region={match.group(2)}, collection={match.group(3)}"
        )
        operational = set(task.base_tool_names)
        exact = True
        for size in sizes:
            menu = visible_menu(task, size)
            matched = {tool.name for tool in menu if needle in tool.description}
            readers = {
                tool.name
                for tool in menu
                if tool.name in operational
                and tool.name.startswith(OPERATIONAL_READ_PREFIXES)
            }
            exact = exact and matched == readers
        recovered += int(exact)
        if not exact:
            mismatches.append(task.task_id)
    return {
        "tasks": len(tasks),
        "tasks_with_route_triple_in_request": with_route,
        "visible_menu_sizes": list(sizes),
        "tasks_with_exact_operational_read_set_recovery": recovered,
        "mismatched_tasks": sorted(mismatches),
    }


def distractor_payloads() -> dict[str, object]:
    tasks = tasks_for_split("main")
    checked = 0
    empty = 0
    skipped = 0
    payload_keys: Counter[str] = Counter()
    for task in tasks:
        world = build_world(task)
        for tool in visible_menu(task, 128):
            if not is_distractor(tool):
                continue
            arguments = (
                {"query": "anything"}
                if tool.name.startswith("search_")
                else {"record_id": task.nodes[0].record_id}
            )
            try:
                result = execute_capacity_tool(world, task, tool, arguments)
            except Exception:  # noqa: BLE001 - a refused distractor returns no records either
                skipped += 1
                continue
            checked += 1
            payload_keys[canonical_json(sorted(result.output))] += 1
            empty += int(not result.output.get("records"))
    return {
        "tasks": len(tasks),
        "distractor_executions": checked,
        "distractor_executions_returning_no_records": empty,
        "distractor_executions_refused": skipped,
        "payload_shapes": dict(sorted(payload_keys.items())),
    }


# --------------------------------------------------------------- assembly


def token_telemetry(
    rows: Sequence[Mapping[str, object]],
    by_block: Mapping[str, Sequence[Mapping[str, object]]],
) -> dict[str, object]:
    def has_response(row: Mapping[str, object]) -> bool:
        response_ids = row.get("response_ids")
        return bool(response_ids) or bool(row.get("response_id"))

    def summary(group: Sequence[Mapping[str, object]]) -> dict[str, object]:
        zero_rows = [
            row
            for row in group
            if _usage(row, "prompt_tokens") == 0
            and _usage(row, "completion_tokens") == 0
        ]
        return {
            "rows": len(group),
            "zero_token_rows": len(zero_rows),
            "zero_token_rows_with_provider_response": sum(map(has_response, zero_rows)),
            "zero_token_rows_without_provider_response": sum(
                not has_response(row) for row in zero_rows
            ),
            "zero_token_share": len(zero_rows) / len(group) if group else None,
            "zero_token_rows_by_status": dict(sorted(Counter(
                str(row.get("status")) for row in zero_rows
            ).items())),
        }

    by_model: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        condition = row.get("condition")
        model = condition.get("model") if isinstance(condition, dict) else row.get("model")
        by_model[str(model)].append(row)
    return {
        **summary(rows),
        "definition": "a released row whose usage object reports both prompt_tokens and completion_tokens as zero; reported-usage coverage counts the presence of the usage object, not its contents",
        "budget_enforcement": "reported tokens drive the episode threshold; zero-token status and response-ID counts below distinguish pre-request availability stops from provider or execution failures. No zero-token row has a recorded provider response. Presence of a usage object does not establish complete provider telemetry, and reasoning/cache breakdowns are not retained in the normalized historical rows",
        "by_block": {name: summary(group) for name, group in sorted(by_block.items())},
        "by_configuration": {
            name: summary(group) for name, group in sorted(by_model.items())
        },
    }


def analyze(
    manifest: Mapping[str, object],
    panel: Mapping[str, object],
    *,
    bootstrap_samples: int,
    seed: int,
) -> dict[str, object]:
    inputs = manifest["inputs"]
    panel_models = {str(entry["model"]) for entry in panel["configurations"]}
    if panel_models != set(manifest["models"]) or not panel_models:
        raise ValueError("failure-mechanism decomposition requires the complete manifest-bound core panel")
    scored = _load(manifest, SCORED_BLOCKS)
    capacity = _load(manifest, CAPACITY_BLOCKS)
    frontier = _load(manifest, FRONTIER_BLOCKS)
    structural = _load(manifest, STRUCTURAL_BLOCKS)
    rows_by_block = {name: _load(manifest, (name,)) for name in sorted(inputs)}
    every_block = [row for rows in rows_by_block.values() for row in rows]
    return {
        "schema": "bottleneckbench-failure-mechanism-decomposition-v2",
        "scientific_role": "descriptive decomposition of the released failures; it reports which episodes met which frozen harness limit and does not estimate the effect of changing any limit",
        "protocol_deviation": f"The hashed protocol protocols/failure-mechanism-decomposition-2026-09-02.md scoped the visible-load decomposition to a frozen 12-configuration subset of the core panel. This release computes it over the full {len(panel_models)}-configuration core panel; the eligibility set was widened after the subset's outcomes were known, and the protocol file is retained unmodified. The blocks the protocol names candidate_structural_auxiliary and frontier_structural_auxiliary are released as candidate_structural_additional and frontier_structural_additional, and the panel key the protocol names structural_confirmatory_models is released as structural_registered_models.",
        "confirmatory_weight": 0,
        "bootstrap_policy": {
            "declared_seed": seed,
            "draws": bootstrap_samples,
            "note": "every bootstrap payload in this artifact records its effective seed; the frontier paired contrasts derive theirs per level and record the derivation",
        },
        "definitions": {
            "harness_limit_errors": list(HARNESS_LIMIT_ERRORS),
            "wrong_conclusion_errors": list(WRONG_CONCLUSION_ERRORS),
            "wrong_conclusion": "a scored failure carrying decision_invalid or answer_facts_invalid and no harness-limit error",
            "truncated": "the episode carries model_finish_length, the frozen 1,024-token per-request completion cap",
            "truncated_before_first_call": "model_finish_length with zero executed tool calls",
            "corrected_first_failure_stage": "bottleneckbench.analysis._failure_stage, overridden to completion_no_call when the episode truncated before executing any tool call",
            "scored_blocks": list(SCORED_BLOCKS),
            "scored_population": "released episode rows carrying an exact verdict (capacity, repair, sentinel, workflow-frontier, structural holdout); Discovery and ToolSandbox rows use different outcome schemas and are excluded",
        },
        "release_failure_census": release_failure_census(scored),
        "visible_load_decomposition": visible_load_decomposition(
            capacity, panel_models
        ),
        "corrected_failure_stages": corrected_stage_analysis(
            manifest, scored, bootstrap_samples=bootstrap_samples, seed=seed
        ),
        "frontier_budget_boundary": frontier_budget_boundary(frontier),
        "structural_call_attribution": structural_call_attribution(
            structural, {str(model) for model in panel["structural_registered_models"]}
        ),
        "context_confound": {
            "capacity_rows": len(capacity),
            "by_policy": context_confound(capacity),
            "representation_bytes": representation_sizes(),
        },
        "mutation_census": mutation_census(),
        "token_telemetry": token_telemetry(every_block, rows_by_block),
        "route_string_recoverability": route_string_recoverability(),
        "distractor_payloads": distractor_payloads(),
        "interpretation_boundary": "descriptive attribution of released failures under the frozen protocol; the conditional non-truncated contrasts condition on a post-treatment variable and are diagnostics, not estimands",
    }


def self_check() -> None:
    truncated_no_call = {
        "verdict": {"verified_success": False, "failure_stage": "selection"},
        "errors": ["model_finish_length"],
        "trace": [],
        "call_attempts": [],
    }
    assert corrected_stage(truncated_no_call) == COMPLETION_NO_CALL
    assert _harness_limited(truncated_no_call) and not _wrong_conclusion(truncated_no_call)
    truncated_after_call = {
        "verdict": {"verified_success": False, "failure_stage": "workflow"},
        "errors": ["model_finish_length"],
        "trace": [{"tool": "search_x"}],
        "call_attempts": [],
    }
    assert corrected_stage(truncated_after_call) == "completion"
    wrong = {
        "verdict": {"verified_success": False, "failure_stage": "answer"},
        "errors": ["answer_facts_invalid"],
        "trace": [{"tool": "search_x"}],
    }
    assert corrected_stage(wrong) == "answer" and _wrong_conclusion(wrong)
    assert _failure_accounting_label(wrong) == "wrong_conclusion"
    assert _failure_accounting_label(truncated_no_call) == "completion_cap"
    assert _failure_accounting_label({"errors": ["budget_exceeded"]}) == "token_or_call_budget"
    census = release_failure_census([
        {"errors": [error]} for error in (*HARNESS_LIMIT_ERRORS, "decision_invalid", "other")
    ])
    assert sum(bucket["count"] for bucket in census["exhaustive_accounting"]["categories"].values()) == 6
    assert corrected_stage({"verdict": {"verified_success": True}}) == "success"
    assert _quantile([1, 2, 3, 4], 0.5) == 2.5
    assert _spearman([1.0, 2.0, 3.0], [2.0, 4.0, 9.0]) == 1.0
    intact_pair = {"verdict": {"verified_success": True}, "errors": [], "trace": [{}]}
    twin = visible_twin_contrast([(intact_pair, truncated_no_call), (intact_pair, intact_pair)])
    decomposition = twin["matched_pair_decomposition"]
    assert decomposition["lost"] == 1 and decomposition["gained"] == 0
    assert decomposition["lost_with_truncated_visible_128_twin"] == 1
    assert twin["manski_bounds"] == {
        **twin["manski_bounds"],
        "lower": -0.5,
        "upper": 0.0,
    }
    assert twin["common_resolution_interval"] == {
        **twin["common_resolution_interval"],
        "lower": -0.5,
        "upper": 0.0,
    }
    # A truncated failure in each arm is what separates the two intervals: it widens
    # the arm-independent bounds and cancels under common resolution.
    split = visible_twin_contrast(
        [(truncated_no_call, truncated_no_call), (intact_pair, intact_pair)]
    )
    assert split["manski_bounds"]["lower"] == -0.5 and split["manski_bounds"]["upper"] == 0.5
    assert split["common_resolution_interval"]["lower"] == 0.0
    assert split["common_resolution_interval"]["upper"] == 0.0
    assert twin["paired_non_truncated"]["pairs"] == 1
    assert twin["paired_non_truncated"]["estimate"] == 0.0
    slope, intercept = _least_squares_quadratic([1.0, 2.0], [3.0, 8.0])
    assert abs(slope - 1.0) < 1e-9 and abs(intercept - 2.0) < 1e-9


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--panel", type=Path, default=DEFAULT_PANEL)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=20260902)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        print('{"passed":true}')
        return 0
    result = analyze(
        json.loads(args.manifest.read_text(encoding="utf-8")),
        json.loads(args.panel.read_text(encoding="utf-8")),
        bootstrap_samples=args.bootstrap_samples,
        seed=args.seed,
    )
    result["inputs"] = [
        {"path": str(path.resolve().relative_to(ROOT)), "sha256": _sha(path)}
        for path in [args.manifest, args.panel, args.protocol, *PROBE_SOURCES]
    ]
    result["analysis_source_sha256"] = _sha(Path(__file__))
    result["artifact_hash"] = stable_hash(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(canonical_json(result) + "\n", encoding="utf-8")
    print(
        canonical_json(
            {
                "failures": result["release_failure_census"]["failures"],
                "output": str(args.output),
                "passed": True,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
