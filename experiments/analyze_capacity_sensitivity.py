#!/usr/bin/env python3
"""Clustering, synthesis, and equivalence sensitivities for the capacity contrasts."""

from __future__ import annotations

import argparse
from collections import defaultdict
from hashlib import sha256
import json
from math import atan, cos, exp, fsum, log, pi, sin, sqrt
from pathlib import Path
import random
from statistics import fmean, stdev
import sys
from typing import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bottleneckbench.analysis import _bootstrap  # noqa: E402
from bottleneckbench.core.analysis import holm_adjust  # noqa: E402
from bottleneckbench.core.schema import canonical_json, stable_hash  # noqa: E402
from bottleneckbench.study_design import PRIMARY_CONTRASTS, contrast_value  # noqa: E402


DEFAULT_MANIFEST = ROOT / "results/analysis/release-analysis-inputs.json"
DEFAULT_PANEL = ROOT / "results/analysis/release-panel.json"
DEFAULT_EXTERNAL = ROOT / "results/analysis/toolsandbox-external-validation.json"
DEFAULT_PROTOCOL = ROOT / "protocols/capacity-cluster-sensitivity-2026-09-02.md"
DEFAULT_OUTPUT = ROOT / "results/analysis/capacity-cluster-sensitivity.json"

CAPACITY_BLOCKS = ("capacity", "candidate_capacity", "frontier_capacity")
DISCOVERY_BLOCKS = ("discovery", "candidate_discovery", "frontier_discovery")
REPAIR_BLOCKS = ("repair", "candidate_repair", "frontier_repair")
INTERACTION_CELLS = ((8, "low"), (8, "high"), (128, "low"), (128, "high"))
INTERACTION_REPRESENTATIONS = ("raw", "linked")
FORCED_SIGN_PATTERNS = {
    (1.0, 0.0, 0.0): "both_single_stresses_at_floor",
    (0.0, 1.0, 1.0): "both_single_stresses_at_ceiling",
}
REPAIR_SPECIFICATIONS = {
    "workflow_prompt": ("verification_repair", (8, "low", "raw"), (8, "high", "raw")),
    "returned_state_interface": ("interface_guide", (8, "low", "raw"), (8, "low", "linked")),
}
RETRIEVAL_STRESS_REGISTRY = 2048
RETRIEVAL_BASE_REGISTRY = 32
JEFFREYS = "(successes + 0.5) / (trials + 1)"


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _condition(row: Mapping[str, object]) -> Mapping[str, object]:
    condition = row.get("condition")
    if not isinstance(condition, dict):
        raise ValueError("released row is missing a condition object")
    return condition


def _success(row: Mapping[str, object]) -> float:
    verdict = row.get("verdict")
    return float(isinstance(verdict, dict) and verdict.get("verified_success") is True)


def _load(manifest: Mapping[str, object], blocks: Sequence[str]) -> list[dict[str, object]]:
    inputs = manifest["inputs"]
    rows: list[dict[str, object]] = []
    for block in blocks:
        for entry in inputs[block]:  # type: ignore[index]
            path = ROOT / str(entry["path"])
            if _sha(path) != entry["sha256"]:
                raise ValueError(f"manifest-bound release input changed: {path}")
            rows.extend(
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
    return rows


# ------------------------------------------------------------- distributions


def student_t_cdf(value: float, degrees: int) -> float:
    """Exact finite-series Student-t distribution function for integer degrees."""

    if degrees < 1:
        raise ValueError("Student-t requires at least one degree of freedom")
    theta = atan(abs(value) / sqrt(degrees))
    squared = cos(theta) ** 2
    if degrees % 2:
        term, coefficient, total = cos(theta), 1.0, 0.0
        for index in range(1, (degrees - 1) // 2 + 1):
            if index > 1:
                coefficient *= (2 * index - 2) / (2 * index - 1)
                term *= squared
            total += coefficient * term
        area = 2.0 / pi * (theta + sin(theta) * total)
    else:
        term, coefficient, total = 1.0, 1.0, 0.0
        for index in range(1, degrees // 2 + 1):
            if index > 1:
                coefficient *= (2 * index - 3) / (2 * index - 2)
                term *= squared
            total += coefficient * term
        area = sin(theta) * total
    return 0.5 + 0.5 * area if value >= 0 else 0.5 - 0.5 * area


def student_t_ppf(probability: float, degrees: int) -> float:
    if not 0.0 < probability < 1.0:
        raise ValueError("Student-t quantile requires a probability in (0, 1)")
    low, high = -1e6, 1e6
    for _ in range(200):
        middle = (low + high) / 2.0
        if student_t_cdf(middle, degrees) < probability:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


def _logit(value: float) -> float:
    return log(value / (1.0 - value))


# ----------------------------------------------------- contrast bookkeeping


def configuration_family_contrasts(
    rows: Sequence[Mapping[str, object]],
    terms: Sequence[tuple[tuple[int, str, str], float]],
) -> tuple[dict[tuple[str, str], float], dict[str, str]]:
    """One contrast value per (family, configuration) cell, plus the family-to-intent map."""

    required = {coordinate for coordinate, _ in terms}
    cells: dict[tuple[str, str], dict[tuple[int, str, str], float]] = defaultdict(dict)
    family_intents: dict[str, str] = {}
    for row in rows:
        if row.get("study_role") != "primary":
            continue
        condition = _condition(row)
        coordinate = (
            int(condition["visible_tools"]),
            str(condition["workflow"]),
            str(condition["representation"]),
        )
        if coordinate not in required:
            continue
        key = (str(condition["family_id"]), str(condition["model"]))
        if coordinate in cells[key]:
            raise ValueError("duplicate registered contrast cell")
        cells[key][coordinate] = _success(row)
        family_intents[key[0]] = str(condition["intent"])
    values: dict[tuple[str, str], float] = {}
    for key, observed in sorted(cells.items()):
        if not required <= set(observed):
            raise ValueError(f"incomplete registered contrast cell: {key}")
        values[key] = contrast_value(observed, terms)
    return values, family_intents


def _grouped(values: Mapping[tuple[str, str], float], index: int) -> dict[str, float]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for key, value in values.items():
        grouped[key[index]].append(value)
    return {name: fmean(items) for name, items in sorted(grouped.items())}


def configuration_cluster_bootstrap(
    rows: Sequence[Mapping[str, object]], *, samples: int, seed: int
) -> dict[str, object]:
    effects: dict[str, object] = {}
    raw_p: dict[str, float] = {}
    for name, terms in PRIMARY_CONTRASTS.items():
        values, _ = configuration_family_contrasts(rows, terms)
        by_configuration = _grouped(values, 1)
        summary = _bootstrap(list(by_configuration.values()), samples=samples, seed=seed)
        raw_p[name] = float(summary["bootstrap_p_two_sided"])  # type: ignore[index]
        effects[name] = {**summary, "seed": seed, "by_configuration": by_configuration}
    for name, adjusted in holm_adjust(raw_p).items():
        effects[name]["holm_p"] = adjusted  # type: ignore[index]
    return effects


def clustering_variance(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    report: dict[str, object] = {}
    for name, terms in PRIMARY_CONTRASTS.items():
        values, family_intents = configuration_family_contrasts(rows, terms)
        families = sorted({family for family, _ in values})
        configurations = sorted({model for _, model in values})
        by_family = _grouped(values, 0)
        groups: dict[str, list[float]] = defaultdict(list)
        for family, value in by_family.items():
            groups[family_intents[family]].append(value)
        sizes = {len(items) for items in groups.values()}
        if len(sizes) != 1:
            raise ValueError("intent clusters are unbalanced; the moment ICC assumes balance")
        per_group = sizes.pop()
        grand = fmean(by_family.values())
        # fsum throughout: builtins.sum gained Neumaier summation in CPython 3.12,
        # which moves these accumulations by ~1 ulp between interpreter versions.
        between = fsum(
            per_group * (fmean(items) - grand) ** 2 for items in groups.values()
        ) / (len(groups) - 1)
        within = fsum(
            fsum((value - fmean(items)) ** 2 for value in items) for items in groups.values()
        ) / (len(groups) * (per_group - 1))
        denominator = between + (per_group - 1) * within
        icc = (between - within) / denominator if denominator > 0 else 0.0
        design_effect = 1.0 + (per_group - 1) * max(icc, 0.0)

        # Crossed, balanced two-way random-effects decomposition over family x configuration.
        matrix = [[values[(family, model)] for model in configurations] for family in families]
        family_count, configuration_count = len(families), len(configurations)
        row_means = [fmean(row) for row in matrix]
        column_means = [
            fmean(matrix[index][column] for index in range(family_count))
            for column in range(configuration_count)
        ]
        overall = fmean(row_means)
        ss_family = configuration_count * fsum((value - overall) ** 2 for value in row_means)
        ss_configuration = family_count * fsum((value - overall) ** 2 for value in column_means)
        ss_residual = fsum(
            (matrix[i][j] - row_means[i] - column_means[j] + overall) ** 2
            for i in range(family_count)
            for j in range(configuration_count)
        )
        ms_family = ss_family / (family_count - 1)
        ms_configuration = ss_configuration / (configuration_count - 1)
        ms_residual = ss_residual / ((family_count - 1) * (configuration_count - 1))
        var_family = max((ms_family - ms_residual) / configuration_count, 0.0)
        var_configuration = max((ms_configuration - ms_residual) / family_count, 0.0)
        se_family_only = sqrt(
            var_family / family_count + ms_residual / (family_count * configuration_count)
        )
        se_crossed = sqrt(
            var_family / family_count
            + var_configuration / configuration_count
            + ms_residual / (family_count * configuration_count)
        )
        report[name] = {
            "estimate": grand,
            "families": family_count,
            "configurations": configuration_count,
            "intent_clusters": len(groups),
            "families_per_intent": per_group,
            "icc_family_within_intent": icc,
            "design_effect": design_effect,
            "effective_families": family_count / design_effect,
            "variance_components": {
                "family": var_family,
                "configuration": var_configuration,
                "residual": ms_residual,
                "se_family_random_only": se_family_only,
                "se_family_and_configuration_random": se_crossed,
                "ci95_halfwidth_family_random_only": 1.96 * se_family_only,
                "ci95_halfwidth_crossed": 1.96 * se_crossed,
            },
        }
    return report


def random_effects_synthesis(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    report: dict[str, object] = {}
    for name, terms in PRIMARY_CONTRASTS.items():
        values, _ = configuration_family_contrasts(rows, terms)
        grouped: dict[str, list[float]] = defaultdict(list)
        for (_, model), value in values.items():
            grouped[model].append(value)
        estimates, variances = [], []
        for model in sorted(grouped):
            items = grouped[model]
            estimates.append(fmean(items))
            variances.append(stdev(items) ** 2 / len(items))
        count = len(estimates)
        degenerate = [
            model
            for model, value in zip(sorted(grouped), variances, strict=True)
            if value == 0.0
        ]
        if degenerate:
            # Inverse-variance weighting is undefined when a configuration's family
            # contrasts are all identical; say so rather than perturbing the weight.
            report[name] = {
                "configurations": count,
                "status": "undefined",
                "reason": "inverse-variance weights are undefined for configurations with zero between-family variance",
                "degenerate_configurations": degenerate,
                "by_configuration": {model: fmean(grouped[model]) for model in sorted(grouped)},
            }
            continue
        weights = [1.0 / value for value in variances]
        fixed = fsum(w * y for w, y in zip(weights, estimates, strict=True)) / fsum(weights)
        cochran = fsum(
            w * (y - fixed) ** 2 for w, y in zip(weights, estimates, strict=True)
        )
        scaling = fsum(weights) - fsum(w**2 for w in weights) / fsum(weights)
        tau_squared = max(0.0, (cochran - (count - 1)) / scaling)
        adjusted = [1.0 / (value + tau_squared) for value in variances]
        pooled = fsum(w * y for w, y in zip(adjusted, estimates, strict=True)) / fsum(adjusted)
        standard_error = sqrt(1.0 / fsum(adjusted))
        critical = student_t_ppf(0.975, count - 1)
        prediction_error = sqrt(tau_squared + standard_error**2)
        report[name] = {
            "configurations": count,
            "cochran_q": cochran,
            "degrees_of_freedom": count - 1,
            "tau_squared": tau_squared,
            "tau": sqrt(tau_squared),
            "i_squared": max(0.0, (cochran - (count - 1)) / cochran) if cochran > 0 else 0.0,
            "fixed_effect": fixed,
            "estimate": pooled,
            "standard_error": standard_error,
            "ci95": [pooled - critical * standard_error, pooled + critical * standard_error],
            "prediction_interval": [
                pooled - critical * prediction_error,
                pooled + critical * prediction_error,
            ],
            "critical_value": critical,
            "by_configuration": {
                model: fmean(grouped[model]) for model in sorted(grouped)
            },
        }
    return report


# ---------------------------------------------------------------- interaction


def _interaction_units(rows: Sequence[Mapping[str, object]]) -> dict[tuple[str, str, str], dict[tuple[int, str], float]]:
    units: dict[tuple[str, str, str], dict[tuple[int, str], float]] = defaultdict(dict)
    for row in rows:
        if row.get("study_role") != "primary":
            continue
        condition = _condition(row)
        representation = str(condition["representation"])
        cell = (int(condition["visible_tools"]), str(condition["workflow"]))
        if representation not in INTERACTION_REPRESENTATIONS or cell not in INTERACTION_CELLS:
            continue
        key = (str(condition["model"]), str(condition["family_id"]), representation)
        if cell in units[key]:
            raise ValueError("duplicate interaction cell")
        units[key][cell] = _success(row)
    if any(set(cells) != set(INTERACTION_CELLS) for cells in units.values()):
        raise ValueError("interaction units are incomplete")
    return units


def interaction_decomposition(
    rows: Sequence[Mapping[str, object]], *, samples: int, seed: int
) -> dict[str, object]:
    units = _interaction_units(rows)
    pooled: dict[tuple[int, str], list[float]] = defaultdict(list)
    for cells in units.values():
        for cell, value in cells.items():
            pooled[cell].append(value)
    risks = {cell: fmean(values) for cell, values in pooled.items()}
    p00, p01 = risks[(8, "low")], risks[(8, "high")]
    p10, p11 = risks[(128, "low")], risks[(128, "high")]
    independence = (p00 - p10) * (p00 - p01) / p00

    patterns: dict[tuple[float, float, float, float], int] = defaultdict(int)
    for cells in units.values():
        patterns[(cells[(8, "low")], cells[(8, "high")], cells[(128, "low")], cells[(128, "high")])] += 1
    total_units = len(units)
    observed = sum(
        (pattern[3] - pattern[2] - pattern[1] + pattern[0]) * count
        for pattern, count in patterns.items()
    ) / total_units
    floor_pattern = (1.0, 0.0, 0.0, 0.0)
    floor_units = patterns.get(floor_pattern, 0)

    families_rr: dict[str, dict[tuple[int, str], list[float]]] = defaultdict(lambda: defaultdict(list))
    for (_, family, _), cells in units.items():
        for cell, value in cells.items():
            families_rr[family][cell].append(value)
    log_risk_ratio, log_odds_ratio = [], []
    for family in sorted(families_rr):
        corrected = {
            cell: (sum(values) + 0.5) / (len(values) + 1.0)
            for cell, values in families_rr[family].items()
        }
        log_risk_ratio.append(
            log(corrected[(128, "high")])
            - log(corrected[(128, "low")])
            - log(corrected[(8, "high")])
            + log(corrected[(8, "low")])
        )
        log_odds_ratio.append(
            _logit(corrected[(128, "high")])
            - _logit(corrected[(128, "low")])
            - _logit(corrected[(8, "high")])
            + _logit(corrected[(8, "low")])
        )

    # Assigned stress is monotone under any deterministic capacity model: a unit that
    # fails a single stress cannot verify under both.  Count the units that do.
    non_monotone = {
        key
        for key, cells in units.items()
        if cells[(128, "high")] == 1.0
        and (cells[(8, "high")] == 0.0 or cells[(128, "low")] == 0.0)
    }
    monotone_cells = {
        cell: fmean([cells[cell] for key, cells in units.items() if key not in non_monotone])
        for cell in INTERACTION_CELLS
    }
    all_stressed_fail = sum(
        1
        for cells in units.values()
        if not (cells[(8, "high")] or cells[(128, "low")] or cells[(128, "high")])
    )

    # The Jeffreys correction is what carries a cell with no successes into the
    # log scale.  Restrict to units that verified at least one stressed cell and
    # recompute the family-clustered log risk ratio without those units.
    stressed = ((8, "high"), (128, "low"), (128, "high"))
    non_zero_units = {
        key: cells for key, cells in units.items() if any(cells[cell] for cell in stressed)
    }
    non_zero_families: dict[str, dict[tuple[int, str], list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for (_, family, _), cells in non_zero_units.items():
        for cell, value in cells.items():
            non_zero_families[family][cell].append(value)
    non_zero_log_risk_ratio = []
    for family in sorted(non_zero_families):
        corrected = {
            cell: (sum(values) + 0.5) / (len(values) + 1.0)
            for cell, values in non_zero_families[family].items()
        }
        non_zero_log_risk_ratio.append(
            log(corrected[(128, "high")])
            - log(corrected[(128, "low")])
            - log(corrected[(8, "high")])
            + log(corrected[(8, "low")])
        )
    dropped_patterns: dict[str, int] = defaultdict(int)
    for key, cells in units.items():
        if key not in non_zero_units:
            dropped_patterns[
                "".join(str(int(cells[cell])) for cell in INTERACTION_CELLS)
            ] += 1

    retained: dict[str, list[float]] = defaultdict(list)
    every: dict[str, list[float]] = defaultdict(list)
    dropped: dict[str, int] = defaultdict(int)
    for (_, family, _), cells in units.items():
        y00, y10, y01, y11 = (
            cells[(8, "low")],
            cells[(128, "low")],
            cells[(8, "high")],
            cells[(128, "high")],
        )
        value = y11 - y10 - y01 + y00
        every[family].append(value)
        reason = FORCED_SIGN_PATTERNS.get((y00, y10, y01))
        if reason is None:
            retained[family].append(value)
        else:
            dropped[reason] += 1
    return {
        "units": total_units,
        "cell_risks": {f"V{visible}_{workflow}": risks[(visible, workflow)] for visible, workflow in INTERACTION_CELLS},
        "risk_difference": p11 - p10 - p01 + p00,
        "risk_difference_from_units": observed,
        "independence_benchmark": independence,
        "independence_benchmark_note": "risk-difference interaction implied by statistically independent failure mechanisms, (p00-p10)(p00-p01)/p00; it is positive whenever both single stresses lose",
        "pattern_decomposition": {
            "ordering": "(V8_low, V8_high, V128_low, V128_high)",
            "counts": {
                "".join(str(int(value)) for value in pattern): count
                for pattern, count in sorted(patterns.items())
            },
            "contributions": {
                "".join(str(int(value)) for value in pattern): (
                    pattern[3] - pattern[2] - pattern[1] + pattern[0]
                )
                * count
                / total_units
                for pattern, count in sorted(patterns.items())
            },
        },
        "floor_pattern": {
            "pattern": "1000",
            "units": floor_units,
            "unit_share": floor_units / total_units,
            "contribution": floor_units / total_units,
            "share_of_observed_interaction": (floor_units / total_units) / observed,
        },
        "monotonicity": {
            "units": total_units,
            "non_monotone_units": len(non_monotone),
            "non_monotone_unit_share": len(non_monotone) / total_units,
            "monotone_units": total_units - len(non_monotone),
            "risk_difference_dropping_non_monotone": (
                monotone_cells[(128, "high")]
                - monotone_cells[(128, "low")]
                - monotone_cells[(8, "high")]
                + monotone_cells[(8, "low")]
            ),
            "all_stressed_cells_fail_units": all_stressed_fail,
            "all_stressed_cells_fail_unit_share": all_stressed_fail / total_units,
            "non_monotone_definition": "the unit verifies under joint stress while failing under at least one single stress, which no deterministic monotone-capacity model admits",
            "all_stressed_cells_fail_definition": "the unit fails all three stressed cells; patterns 1000 and 0000, i.e. a superset of the floor pattern",
            "claim_boundary": "a run-level consistency tally over the single hosted realization of each cell; it bounds how much of the interaction survives a monotone reading and is not an estimate of run-to-run reproducibility",
        },
        "family_cluster_log_risk_ratio": {
            **_bootstrap(log_risk_ratio, samples=samples, seed=seed),
            "seed": seed,
            "jeffreys_correction": JEFFREYS,
        },
        "family_cluster_log_odds_ratio": {
            **_bootstrap(log_odds_ratio, samples=samples, seed=seed + 1),
            "seed": seed + 1,
            "jeffreys_correction": JEFFREYS,
        },
        "non_zero_unit_family_cluster_log_risk_ratio": {
            **_bootstrap(non_zero_log_risk_ratio, samples=samples, seed=seed + 4),
            "seed": seed + 4,
            "jeffreys_correction": JEFFREYS,
            "units": len(non_zero_units),
            "units_dropped": total_units - len(non_zero_units),
            "units_dropped_by_pattern": dict(sorted(dropped_patterns.items())),
            "selection_rule": "keep the units that verified at least one stressed cell; the dropped units contribute no stressed-cell success and are where the Jeffreys correction does the work",
        },
        "non_floor_reestimate": {
            **_bootstrap(
                [fmean(values) for _, values in sorted(retained.items())],
                samples=samples,
                seed=seed + 2,
            ),
            "seed": seed + 2,
            "units_dropped": sum(dropped.values()),
            "units_dropped_by_reason": dict(sorted(dropped.items())),
            "dropped_share": sum(dropped.values()) / total_units,
            "families_retained": len(retained),
            "selection_rule": "drop units whose interaction sign is arithmetically forced by the single-stress cells; descriptive, outcome-derived",
        },
        "all_unit_family_cluster": {
            **_bootstrap(
                [fmean(values) for _, values in sorted(every.items())],
                samples=samples,
                seed=seed + 3,
            ),
            "seed": seed + 3,
        },
    }


# ---------------------------------------------------------- repair equivalence


def repair_equivalence(
    capacity: Sequence[Mapping[str, object]],
    repair: Sequence[Mapping[str, object]],
    repair_models: Sequence[str],
    *,
    samples: int,
    seed: int,
) -> dict[str, object]:
    models = set(repair_models)
    report: dict[str, object] = {}
    for name, (role, base, stress) in REPAIR_SPECIFICATIONS.items():
        cells: dict[tuple[str, str], dict[tuple[str, tuple[int, str, str]], float]] = defaultdict(dict)
        for row in [*capacity, *repair]:
            condition = _condition(row)
            if str(condition["model"]) not in models:
                continue
            study_role = str(row.get("study_role", "primary"))
            if study_role not in {"primary", role}:
                continue
            coordinate = (
                int(condition["visible_tools"]),
                str(condition["workflow"]),
                str(condition["representation"]),
            )
            if coordinate not in {base, stress}:
                continue
            cells[(str(condition["family_id"]), str(condition["model"]))][
                (study_role, coordinate)
            ] = _success(row)
        gains: dict[str, list[float]] = defaultdict(list)
        losses: dict[str, list[float]] = defaultdict(list)
        selectivity: dict[str, list[float]] = defaultdict(list)
        for (family, _), observed in sorted(cells.items()):
            if len(observed) != 4:
                raise ValueError(f"incomplete repair quadruple: {name} {family}")
            base_low = observed[("primary", base)]
            base_stress = observed[("primary", stress)]
            repair_low = observed[(role, base)]
            repair_stress = observed[(role, stress)]
            gains[family].append(repair_stress - base_stress)
            losses[family].append(base_low - base_stress)
            selectivity[family].append(
                (repair_stress - base_stress) - (repair_low - base_low)
            )
        gain_values = [fmean(values) for _, values in sorted(gains.items())]
        clusters = len(gain_values)
        standard_error = stdev(gain_values) / sqrt(clusters)
        critical = student_t_ppf(0.95, clusters - 1)
        margin = abs(fmean(gain_values)) + critical * standard_error
        report[name] = {
            "configurations": len(models),
            "family_clusters": clusters,
            "stress_repair_gain": {
                **_bootstrap(gain_values, samples=samples, seed=seed),
                "seed": seed,
            },
            "assigned_loss": {
                **_bootstrap(
                    [fmean(values) for _, values in sorted(losses.items())],
                    samples=samples,
                    seed=seed + 1,
                ),
                "seed": seed + 1,
            },
            "off_target_selectivity": {
                **_bootstrap(
                    [fmean(values) for _, values in sorted(selectivity.items())],
                    samples=samples,
                    seed=seed + 2,
                ),
                "seed": seed + 2,
            },
            "equivalence": {
                "test": "two one-sided t tests on the family-clustered stress-repair gain",
                "alpha": 0.05,
                "standard_error": standard_error,
                "critical_value": critical,
                "smallest_rejectable_margin": margin,
                "interpretation": "the data reject gains larger than this margin in absolute value; smaller effects are not excluded",
            },
        }
    return report


# ------------------------------------------------- retrieval availability


def retrieval_availability(discovery: Sequence[Mapping[str, object]]) -> dict[str, object]:
    cells: dict[tuple[str, str], dict[tuple[str, int], tuple[float, bool]]] = defaultdict(dict)
    for row in discovery:
        condition = _condition(row)
        exposure = row.get("exposure")
        available = bool(exposure.get("required_available")) if isinstance(exposure, dict) else False
        cells[(str(condition["case_id"]), str(condition["model"]))][
            (str(condition["retriever"]), int(condition["registry_size"]))
        ] = (float(row.get("success") is True), available)
    stress = ("flat", RETRIEVAL_STRESS_REGISTRY)
    repaired = ("hierarchical", RETRIEVAL_STRESS_REGISTRY)
    itt: dict[str, list[float]] = defaultdict(list)
    conditional: dict[str, list[float]] = defaultdict(list)
    availability: dict[str, list[float]] = defaultdict(list)
    for (_, model), observed in sorted(cells.items()):
        if not {stress, repaired} <= set(observed):
            raise ValueError("incomplete retrieval availability cell")
        itt[model].append(observed[repaired][0] - observed[stress][0])
        availability[f"{stress[0]}_{stress[1]}"].append(float(observed[stress][1]))
        availability[f"{repaired[0]}_{repaired[1]}"].append(float(observed[repaired][1]))
        if observed[stress][1] and observed[repaired][1]:
            conditional[model].append(observed[repaired][0] - observed[stress][0])
    itt_means = {model: fmean(values) for model, values in sorted(itt.items())}
    conditional_means = {
        model: fmean(values) for model, values in sorted(conditional.items()) if values
    }
    return {
        "configurations": len(itt_means),
        "availability_rate": {name: fmean(values) for name, values in sorted(availability.items())},
        "intention_to_treat_gain": itt_means,
        "intention_to_treat_success_superior_configurations": sum(
            value > 0 for value in itt_means.values()
        ),
        "availability_conditional_configurations": len(conditional_means),
        "availability_conditional_cases_per_configuration": {
            model: len(values) for model, values in sorted(conditional.items())
        },
        "availability_conditional_gain": conditional_means,
        "availability_conditional_success_superior_configurations": sum(
            value > 0 for value in conditional_means.values()
        ),
        "caveat": "conditioning on required-tool availability conditions on a post-treatment variable: the retriever is the treatment and availability is its immediate consequence, so the conditional contrast is descriptive only",
    }


# ------------------------------------------------------- ToolSandbox ranking


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
    left_mean, right_mean = fmean(left), fmean(right)
    left_ss = fsum((value - left_mean) ** 2 for value in left)
    right_ss = fsum((value - right_mean) ** 2 for value in right)
    if left_ss == 0 or right_ss == 0:
        return None
    return fsum(
        (a - left_mean) * (b - right_mean) for a, b in zip(left, right, strict=True)
    ) / sqrt(left_ss * right_ss)


def _spearman(left: Sequence[float], right: Sequence[float]) -> float | None:
    return _pearson(_rank(left), _rank(right))


def _kendall_tau_b(left: Sequence[float], right: Sequence[float]) -> float | None:
    concordant = discordant = left_ties = right_ties = 0
    for i in range(len(left)):
        for j in range(i + 1, len(left)):
            a = left[i] - left[j]
            b = right[i] - right[j]
            if a == 0 and b == 0:
                left_ties += 1
                right_ties += 1
            elif a == 0:
                left_ties += 1
            elif b == 0:
                right_ties += 1
            elif a * b > 0:
                concordant += 1
            else:
                discordant += 1
    denominator = sqrt(
        (concordant + discordant + left_ties) * (concordant + discordant + right_ties)
    )
    return (concordant - discordant) / denominator if denominator else None


def toolsandbox_rank_agreement(
    external: Mapping[str, object], *, samples: int, seed: int
) -> dict[str, object]:
    profiles = external["profiles"]
    similarity = [float(row["mean_similarity"]) for row in profiles]  # type: ignore[index]
    core = [float(row["mean_core_vts"]) for row in profiles]  # type: ignore[index]
    count = len(similarity)
    rng = random.Random(seed)
    draws: list[float] = []
    undefined = 0
    for _ in range(samples):
        indices = [rng.randrange(count) for _ in range(count)]
        value = _spearman([similarity[i] for i in indices], [core[i] for i in indices])
        if value is None:
            undefined += 1
        else:
            draws.append(value)
    draws.sort()
    return {
        "configurations": count,
        "spearman": _spearman(similarity, core),
        "kendall_tau_b": _kendall_tau_b(similarity, core),
        "configuration_resampled_ci95": [
            draws[int(0.025 * (len(draws) - 1))],
            draws[int(0.975 * (len(draws) - 1))],
        ],
        "configuration_resampled_defined_draws": len(draws),
        "configuration_resampled_undefined_draws": undefined,
        "bootstrap": {"samples": samples, "seed": seed, "cluster": "configuration"},
        "note": f"the released interval resamples ToolSandbox scenarios and Capacity Surface families; this interval resamples the {count} configurations, the unit the rank claim is about",
    }


# --------------------------------------------------------------- assembly


def analyze(
    manifest: Mapping[str, object],
    panel: Mapping[str, object],
    external: Mapping[str, object],
    *,
    samples: int,
    seed: int,
) -> dict[str, object]:
    core = _load(manifest, CAPACITY_BLOCKS)
    repair = _load(manifest, REPAIR_BLOCKS)
    repair_models = sorted({str(_condition(row)["model"]) for row in repair})
    discovery = _load(manifest, DISCOVERY_BLOCKS)
    activation = [str(model) for model in panel["activation_models"]]
    core_models = {str(_condition(row)["model"]) for row in core}
    panel_models = {str(entry["model"]) for entry in panel["configurations"]}
    if core_models != panel_models or panel_models != set(manifest["models"]) or not set(activation) <= panel_models:
        raise ValueError("capacity sensitivity requires the complete manifest-bound reporting panel and in-panel activation configurations")
    return {
        "schema": "bottleneckbench-capacity-cluster-sensitivity-v1",
        "scientific_role": f"sensitivity analysis for the primary {len(panel_models)}-configuration capacity contrasts; it reports how the family-clustered risk differences move under other clustering, synthesis and equivalence choices and amends none of them",
        "protocol_deviation": f"The hashed protocol protocols/capacity-cluster-sensitivity-2026-09-02.md scoped the clustering, interaction and equivalence sections to a frozen 12-configuration subset of the core panel. This release computes every section over the full {len(panel_models)}-configuration core panel; the eligibility set was widened after the subset's outcomes were known, and the protocol file is retained unmodified.",
        "confirmatory_weight": 0,
        "frozen_configurations": len(panel_models),
        "panel_configurations": len(panel_models),
        "activation_configurations": activation,
        "repair_configurations": repair_models,
        "configuration_cluster_bootstrap": configuration_cluster_bootstrap(
            core, samples=samples, seed=seed
        ),
        "clustering_variance": clustering_variance(core),
        "random_effects_panel": random_effects_synthesis(core),
        "interaction": interaction_decomposition(core, samples=samples, seed=seed),
        "repair_equivalence": repair_equivalence(
            core, repair, repair_models, samples=samples, seed=seed
        ),
        "retrieval_availability": retrieval_availability(discovery),
        "toolsandbox_rank_agreement": toolsandbox_rank_agreement(
            external, samples=samples, seed=seed
        ),
        "bootstrap_resolution": {
            "samples": samples,
            "declared_seed": seed,
            "seed_note": "the declared seed of every bootstrap in this artifact; blocks that draw more than one interval offset it by a fixed integer and each payload records its effective seed",
            "minimum_attainable_p": 1.0 / (samples + 1),
            "contrast_family_size": len(PRIMARY_CONTRASTS),
            "minimum_attainable_holm_p": len(PRIMARY_CONTRASTS) / (samples + 1),
            "note": "a centred percentile bootstrap cannot resolve a p-value below one draw; report the floor rather than an exact-looking value",
        },
        "interpretation_boundary": f"finite-panel sensitivities; the {len(panel_models)} configurations are not a random sample of deployable systems, and the equivalence margins bound only the configurations that carry a complete repair block",
    }


def self_check() -> None:
    assert abs(student_t_cdf(0.0, 5) - 0.5) < 1e-12
    assert abs(student_t_ppf(0.975, 1) - 12.7062047362) < 1e-6
    assert abs(student_t_ppf(0.975, 2) - 4.30265272991) < 1e-6
    assert abs(student_t_ppf(0.95, 35) - 1.68957245631) < 1e-6
    assert abs(student_t_ppf(0.975, 19) - 2.09302405441) < 1e-6
    assert _spearman([1.0, 2.0, 3.0], [3.0, 2.0, 1.0]) == -1.0
    assert _kendall_tau_b([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == 1.0
    assert abs(_kendall_tau_b([1.0, 2.0, 3.0, 4.0], [1.0, 3.0, 2.0, 4.0]) - 2 / 3) < 1e-12
    assert abs(_logit(0.5)) < 1e-12 and abs(exp(log(0.25)) - 0.25) < 1e-12


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--panel", type=Path, default=DEFAULT_PANEL)
    parser.add_argument("--external", type=Path, default=DEFAULT_EXTERNAL)
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
        json.loads(args.external.read_text(encoding="utf-8")),
        samples=args.bootstrap_samples,
        seed=args.seed,
    )
    result["inputs"] = [
        {"path": str(path.resolve().relative_to(ROOT)), "sha256": _sha(path)}
        for path in [args.manifest, args.panel, args.external, args.protocol]
    ]
    result["analysis_source_sha256"] = _sha(Path(__file__))
    result["artifact_hash"] = stable_hash(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(canonical_json(result) + "\n", encoding="utf-8")
    print(canonical_json({"output": str(args.output), "passed": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
