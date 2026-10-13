#!/usr/bin/env python3
"""Analyze the registered ToolSandbox external-validation panel."""

from __future__ import annotations

import argparse
from collections import defaultdict
from hashlib import sha256
import json
import math
from pathlib import Path
import random
from statistics import mean
from typing import Iterable, Mapping, Sequence


JSON = dict[str, object]
RETAINED_ITT_INFRASTRUCTURE_IDS = frozenset(
    {"fbeab4aed2d7e60bc703cb7b51e1190d13abdbb08b044f2a050f17940adb9db9"}
)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _object(path: Path) -> JSON:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _rows(paths: Sequence[Path]) -> list[JSON]:
    rows: list[JSON] = []
    for path in paths:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"non-object row at {path}:{line_number}")
            rows.append(value)
    identifiers = [str(row.get("condition_id")) for row in rows]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("analysis input contains duplicate condition IDs")
    return rows


def _rank(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        tied_rank = (start + end - 1) / 2
        for index in order[start:end]:
            ranks[index] = tied_rank
        start = end
    return ranks


def _spearman(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 3:
        raise ValueError("Spearman correlation requires paired vectors of length at least three")
    x, y = _rank(left), _rank(right)
    x_mean, y_mean = mean(x), mean(y)
    x_ss = sum((value - x_mean) ** 2 for value in x)
    y_ss = sum((value - y_mean) ** 2 for value in y)
    if x_ss == 0 or y_ss == 0:
        return None
    return sum((a - x_mean) * (b - y_mean) for a, b in zip(x, y)) / math.sqrt(x_ss * y_ss)


def _metric(row: Mapping[str, object], container: str, key: str) -> float | None:
    value = row.get(container)
    if not isinstance(value, dict):
        return None
    number = value.get(key)
    return float(number) if isinstance(number, (int, float)) and not isinstance(number, bool) else None


def _supplement_hashes(
    plan: Mapping[str, object], supplements: Sequence[Mapping[str, object]]
) -> dict[str, str]:
    parent_conditions = plan.get("conditions")
    if not isinstance(parent_conditions, list):
        raise ValueError("external-validation parent plan has no conditions")
    hashes: dict[str, str] = {}
    for supplement in supplements:
        unhashed = dict(supplement)
        supplied = unhashed.pop("plan_hash", None)
        if not isinstance(supplied, str) or not supplied:
            raise ValueError("external-validation supplement lacks its source plan hash")
        model = str(supplement.get("transport_scope"))
        expected = [item for item in parent_conditions if item.get("model") == model]
        if (
            supplement.get("parent_plan_hash") != plan.get("plan_hash")
            or supplement.get("confirmatory_weight") != 0
            or supplement.get("conditions") != expected
            or len(expected) != 32
            or model in hashes
        ):
            raise ValueError("external-validation supplement does not match its parent model slice")
        hashes[model] = str(supplied)
    return hashes


def _external_matrix(
    rows: Sequence[Mapping[str, object]],
    plan: Mapping[str, object],
    supplements: Sequence[Mapping[str, object]],
) -> tuple[list[str], list[str], dict[str, str], dict[str, dict[str, float]]]:
    if any(row.get("status") != "complete" for row in rows):
        raise ValueError("external validation contains a non-complete infrastructure row")
    expected_hashes = {
        str(item["model"]): str(plan["plan_hash"])
        for item in plan["configurations"]  # type: ignore[index]
    }
    expected_hashes.update(_supplement_hashes(plan, supplements))
    if any(row.get("plan_hash") != expected_hashes.get(str(row.get("model"))) for row in rows):
        raise ValueError("external validation contains a row from another plan")
    observed_models = {str(row.get("model")) for row in rows}
    models = sorted(
        str(item["model"])
        for item in plan["configurations"]  # type: ignore[index]
        if str(item["model"]) in observed_models
    )
    scenarios = sorted({str(item["scenario_id"]) for item in plan["conditions"]})  # type: ignore[index]
    strata = {
        str(item["scenario_id"]): str(item["stratum"])
        for item in plan["conditions"]  # type: ignore[index]
    }
    if not models or len(scenarios) != 32 or len(rows) != len(models) * 32:
        raise ValueError("external validation does not close at the observed panel by 32 scenarios")
    matrix: dict[str, dict[str, float]] = defaultdict(dict)
    for row in rows:
        model, scenario = str(row["model"]), str(row["scenario_id"])
        similarity = _metric(row, "score", "similarity")
        if model not in models or scenario not in scenarios or similarity is None:
            raise ValueError("external-validation row has an invalid model, scenario, or score")
        matrix[model][scenario] = similarity
    if any(set(matrix[model]) != set(scenarios) for model in models):
        raise ValueError("external-validation model-scenario crossing is incomplete")
    return models, scenarios, strata, dict(matrix)


def _core_matrix(
    rows: Sequence[Mapping[str, object]], models: Sequence[str]
) -> tuple[list[str], dict[str, dict[str, float]]]:
    observed_infrastructure = {
        str(row.get("condition_id"))
        for row in rows
        if row.get("status") in {"provider_failure", "harness_failure", "validator_uncertain"}
    }
    if observed_infrastructure != set(RETAINED_ITT_INFRASTRUCTURE_IDS):
        raise ValueError("routed core ITT infrastructure set differs from its frozen condition IDs")
    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in rows:
        condition = row.get("condition")
        verdict = row.get("verdict")
        if not isinstance(condition, dict) or not isinstance(verdict, dict):
            raise ValueError("core row lacks a condition or verdict")
        model = str(condition["model"])
        if model not in models:
            raise ValueError(f"unexpected model in core input: {model}")
        family = str(condition["family_id"])
        success = float(row.get("status") == "verified_success" and verdict.get("verified_success") is True)
        grouped[(model, family)].append(success)
    families = sorted({family for _, family in grouped})
    if not models or len(families) != 36 or len(rows) != len(models) * 36 * 12:
        raise ValueError("routed core input does not close at the observed panel by 36 families by 12 cells")
    matrix: dict[str, dict[str, float]] = defaultdict(dict)
    for model in models:
        for family in families:
            values = grouped.get((model, family), [])
            if len(values) != 12:
                raise ValueError(f"core family is incomplete: {model} {family}")
            matrix[model][family] = mean(values)
    return families, dict(matrix)


def _profile(
    model: str,
    scenarios: Sequence[str],
    rows_by_pair: Mapping[tuple[str, str], Mapping[str, object]],
    matrix: Mapping[str, Mapping[str, float]],
) -> JSON:
    rows = [rows_by_pair[(model, scenario)] for scenario in scenarios]
    milestones = [_metric(row, "score", "milestone_similarity") for row in rows]
    minefields = [_metric(row, "score", "minefield_similarity") for row in rows]
    requests = [_metric(row, "usage", "requests") for row in rows]
    if any(value is None for value in [*milestones, *minefields, *requests]):
        raise ValueError("external-validation profile contains missing official score telemetry")
    reported_tokens = [
        value for row in rows if (value := _metric(row, "usage", "total_tokens")) is not None
    ]
    return {
        "model": model,
        "scenarios": len(rows),
        "mean_similarity": mean(matrix[model][scenario] for scenario in scenarios),
        "mean_milestone_similarity": mean(value for value in milestones if value is not None),
        "mean_minefield_similarity": mean(value for value in minefields if value is not None),
        "full_success_rate": mean(
            float(math.isclose(matrix[model][scenario], 1.0)) for scenario in scenarios
        ),
        "mean_model_requests": mean(value for value in requests if value is not None),
        "tokens_reported": len(reported_tokens),
        "mean_total_tokens_when_reported": mean(reported_tokens) if reported_tokens else None,
        "mean_wall_time_seconds": mean(float(row["duration_seconds"]) for row in rows),
    }


def _bootstrap(
    *,
    models: Sequence[str],
    scenarios: Sequence[str],
    families: Sequence[str],
    external: Mapping[str, Mapping[str, float]],
    core: Mapping[str, Mapping[str, float]],
    samples: int,
    seed: int,
) -> tuple[dict[str, list[float]], dict[str, list[float]], list[float]]:
    rng = random.Random(seed)
    external_draws: dict[str, list[float]] = {model: [] for model in models}
    core_draws: dict[str, list[float]] = {model: [] for model in models}
    correlations: list[float] = []
    for _ in range(samples):
        sampled_scenarios = rng.choices(scenarios, k=len(scenarios))
        sampled_families = rng.choices(families, k=len(families))
        external_means = [mean(external[model][scenario] for scenario in sampled_scenarios) for model in models]
        core_means = [mean(core[model][family] for family in sampled_families) for model in models]
        for model, external_value, core_value in zip(models, external_means, core_means):
            external_draws[model].append(external_value)
            core_draws[model].append(core_value)
        correlation = _spearman(external_means, core_means)
        if correlation is not None:
            correlations.append(correlation)
    if len(correlations) < 0.95 * samples:
        raise ValueError("too many bootstrap correlations are undefined because of tied model summaries")
    return external_draws, core_draws, correlations


def _interval(values: Sequence[float]) -> list[float]:
    ordered = sorted(values)
    return [ordered[int(0.025 * (len(ordered) - 1))], ordered[int(0.975 * (len(ordered) - 1))]]


def analyze(
    external_rows: Sequence[Mapping[str, object]],
    core_rows: Sequence[Mapping[str, object]],
    plan: Mapping[str, object],
    supplements: Sequence[Mapping[str, object]] = (),
    *,
    samples: int,
    seed: int,
) -> JSON:
    models, scenarios, strata, external = _external_matrix(
        external_rows, plan, supplements
    )
    families, core = _core_matrix(core_rows, models)
    return analyze_matrices(external_rows, models, scenarios, strata, external, families, core,
                            samples=samples, seed=seed, plan_bundle={
                                "base": plan["plan_hash"],
                                "transport_supplements": _supplement_hashes(plan, supplements),
                            })


def analyze_matrices(external_rows, models, scenarios, strata, external, families, core, *, samples, seed, plan_bundle=None):
    """Analyze validated matrices, also usable for a hash-bound reporting subset."""
    external_draws, core_draws, correlations = _bootstrap(
        models=models,
        scenarios=scenarios,
        families=families,
        external=external,
        core=core,
        samples=samples,
        seed=seed,
    )
    external_means = [mean(external[model].values()) for model in models]
    core_means = [mean(core[model].values()) for model in models]
    point = _spearman(external_means, core_means)
    if point is None:
        raise ValueError("point Spearman correlation is undefined")
    rows_by_pair = {(str(row["model"]), str(row["scenario_id"])): row for row in external_rows}
    profiles = []
    for model in models:
        profile = _profile(model, scenarios, rows_by_pair, external)
        profile["mean_similarity_ci95"] = _interval(external_draws[model])
        profile["mean_core_vts"] = mean(core[model].values())
        profile["mean_core_vts_ci95"] = _interval(core_draws[model])
        profiles.append(profile)
    leave_one_out: dict[str, float | None] = {}
    for stratum in sorted(set(strata.values())):
        retained = [scenario for scenario in scenarios if strata[scenario] != stratum]
        leave_one_out[stratum] = _spearman(
            [mean(external[model][scenario] for scenario in retained) for model in models],
            core_means,
        )
    correlation_ci = _interval(correlations)
    supported = point > 0 and correlation_ci[0] > 0 and all(
        value is not None and value > 0 for value in leave_one_out.values()
    )
    return {
        "analysis": "toolsandbox-external-validation-v1",
        "confirmatory_weight": 0,
        "plan_bundle": plan_bundle,
        "models": models,
        "external_scenarios": len(scenarios),
        "core_semantic_families": len(families),
        "bootstrap": {
            "samples_requested": samples,
            "defined_correlation_samples": len(correlations),
            "seed": seed,
            "resampling": "ToolSandbox scenarios and Capacity Surface semantic families independently",
        },
        "cross_environment_agreement": {
            "spearman": point,
            "ci95": correlation_ci,
            "leave_one_external_stratum_out": leave_one_out,
            "registered_support_rule_met": supported,
        },
        "profiles": profiles,
        "core_itt_infrastructure_condition_ids": sorted(
            RETAINED_ITT_INFRASTRUCTURE_IDS
        ),
        "claim_boundary": (
            "Public ToolSandbox tasks provide a descriptive external criterion only; this analysis "
            "cannot activate or repair any primary failure-layer claim."
        ),
    }


def self_check() -> None:
    models = [f"m{index:02d}" for index in range(12)]
    scenarios = [f"s{index:02d}" for index in range(32)]
    families = [f"f{index:02d}" for index in range(36)]
    external = {model: {scenario: index / 12 for scenario in scenarios} for index, model in enumerate(models)}
    core = {model: {family: index / 12 for family in families} for index, model in enumerate(models)}
    _, _, correlations = _bootstrap(
        models=models,
        scenarios=scenarios,
        families=families,
        external=external,
        core=core,
        samples=20,
        seed=1,
    )
    if len(correlations) != 20 or not all(math.isclose(value, 1.0) for value in correlations):
        raise AssertionError("external-validation bootstrap self-check failed")
    parent = {
        "plan_hash": "parent",
        "conditions": [{"model": "m00", "condition_id": str(index)} for index in range(32)],
    }
    supplement: JSON = {
        "parent_plan_hash": "parent",
        "confirmatory_weight": 0,
        "transport_scope": "m00",
        "conditions": parent["conditions"],
    }
    supplement["plan_hash"] = sha256(_canonical_json(supplement).encode()).hexdigest()
    if _supplement_hashes(parent, [supplement]) != {"m00": supplement["plan_hash"]}:
        raise AssertionError("external-validation plan-bundle self-check failed")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--external-input", type=Path, action="append")
    parser.add_argument("--core-input", type=Path, action="append")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--plan-supplement", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=20260815)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)
    if args.self_check:
        self_check()
        print('{"passed":true}')
        return 0
    if not args.external_input or not args.core_input or args.plan is None or args.output is None:
        parser.error("analysis requires external inputs, core inputs, a plan, and an output")
    plan = _object(args.plan)
    supplements = [_object(path) for path in args.plan_supplement]
    payload = analyze(
        _rows(args.external_input),
        _rows(args.core_input),
        plan,
        supplements,
        samples=args.bootstrap_samples,
        seed=args.seed,
    )
    payload["inputs"] = [
        {"path": str(path), "sha256": _file_hash(path)}
        for path in [
            *args.external_input,
            *args.core_input,
            args.plan,
            *args.plan_supplement,
        ]
    ]
    payload["analysis_source_sha256"] = _file_hash(Path(__file__))
    payload["artifact_hash"] = sha256(_canonical_json(payload).encode()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(_canonical_json(payload) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
