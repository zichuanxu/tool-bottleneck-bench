#!/usr/bin/env python3
"""Descriptive model-versus-controller comparison on the pooled structural holdout."""

from __future__ import annotations

import argparse
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
from statistics import mean
import sys
from typing import Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

import analyze_model_system_decisions as decisions
from build_release_analysis_manifest import plan_configurations
from bottleneckbench.analysis import is_infrastructure_failure
from bottleneckbench.core.schema import JSON, canonical_json, stable_hash


# The frozen structural plans, their row directories and their model sets are read
# from the released input ledger, so this view covers exactly the configurations
# `structural-repair-pooled.json` pools rather than one plan's subset.
MANIFEST = ROOT / "results/analysis/release-analysis-inputs.json"
SOURCE_GROUPS = ("structural_holdout", "structural_additional")
OUTPUT = ROOT / "results/analysis/structural-model-versus-system.json"
PROTOCOL = HERE / "model-versus-system-decision-protocol.md"
PANEL = HERE / "eligible-analysis-panel.json"
OPENROUTER_PRICES = HERE / "openrouter-endpoint-snapshot.json"
GOOGLE_PRICES = HERE / "google-cloud-price-snapshot.json"
# The configurations added after the first snapshot are priced from the released
# frozen price files, exactly as analyze_model_system_decisions.py prices them.
CANDIDATE_PRICES = (
    ROOT / "configs/release-candidate-pricing-2026-09-02.json",
    ROOT / "configs/frontier-active-route-pricing-2026-09-02.json",
)
ROWS_PER_CONFIGURATION = 216
METRICS = (
    "success",
    "total_tokens",
    "model_requests",
    "tool_schema_exposures",
    "wall_time_seconds",
    "executed_tool_calls",
    "cost_usd",
)
RESOURCE_METRICS = (
    "total_tokens",
    "model_requests",
    "tool_schema_exposures",
    "wall_time_seconds",
    "cost_usd",
)


def _object(path: Path) -> JSON:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _sources(manifest: Mapping[str, object]) -> list[tuple[Path, Path, frozenset[str]]]:
    """The frozen structural plans the release ledger binds, one entry per plan."""

    groups = manifest.get("plan_sources")
    if not isinstance(groups, dict):
        raise ValueError("release manifest lacks plan sources")
    sources: list[tuple[Path, Path, frozenset[str]]] = []
    covered: set[str] = set()
    for group in SOURCE_GROUPS:
        for item in groups.get(group, []):
            root = ROOT / str(item["input_root"])
            models = frozenset(str(model) for model in item["models"])
            if covered & models:
                raise ValueError("a structural configuration is bound to two frozen plans")
            covered |= models
            sources.append((
                ROOT / str(item["plan"]),
                root if root.name == "active" or "configuration" in _object(ROOT / str(item["plan"])) else root / "active",
                models,
            ))
    if not sources:
        raise ValueError("release manifest binds no structural plan source")
    return sorted(sources, key=lambda item: str(item[0]))


def _plan_rows(plan: Mapping[str, object], root: Path, models: frozenset[str]) -> list[JSON]:
    """Rows for one frozen plan, restricted to the configurations the ledger binds."""

    configurations = [
        item for item in plan_configurations(plan)
        if str(item["model"]) in models
    ]
    if {str(item["model"]) for item in configurations} != set(models):
        raise ValueError("frozen structural plan does not carry every bound configuration")
    # Row weight is the plan's own declared weight: the first frozen plan declares 1
    # and the plans pooled with it declare 0, and a row must match its own plan.
    weight = int(plan.get("confirmatory_weight", 1))
    rows: list[JSON] = []
    for configuration in configurations:
        path = root / f"{configuration['slug']}.jsonl"
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"non-object row at {path}:{number}")
            rows.append(row)
    planned = {
        stable_hash(item): item
        for item in plan["conditions"]  # type: ignore[index]
        if str(item["model"]) in models
    }
    seen: set[str] = set()
    for row in rows:
        condition = row.get("condition")
        condition_id = row.get("condition_id")
        if (
            not isinstance(condition, dict)
            or condition_id != stable_hash(condition)
            or condition_id not in planned
            or condition != planned[condition_id]
            or row.get("plan_hash") != plan["plan_hash"]
            or row.get("attempt") != 1
            or row.get("confirmatory_weight") != weight
            or is_infrastructure_failure(row)
            or condition_id in seen
        ):
            raise ValueError(f"invalid structural row: {condition_id}")
        seen.add(str(condition_id))
    if len(rows) != ROWS_PER_CONFIGURATION * len(configurations) or seen != set(planned):
        raise ValueError("structural decision input is not estimate-complete")
    return rows


def _pooled_prices(
    panel: Mapping[str, object],
    openrouter: Mapping[str, object],
    google: Mapping[str, object],
    candidates: Sequence[Mapping[str, object]],
    models: frozenset[str],
    *, row_costs: decisions.RowCosts | None = None,
) -> dict[str, tuple[float, float]]:
    """Snapshot prices for the first-acquired panel, frozen prices for the rest."""

    frozen: dict[str, Mapping[str, object]] = {}
    cost_models = decisions._row_cost_models(row_costs, models)
    for price_set in candidates:
        rows = price_set.get("models")
        if not isinstance(rows, dict):
            raise ValueError("frozen price file lacks models")
        for model, price in rows.items():
            prior = frozen.get(str(model))
            if prior is not None and (
                float(prior["input"]), float(prior["output"])  # type: ignore[index]
            ) != (float(price["input"]), float(price["output"])):  # type: ignore[index]
                raise ValueError(f"conflicting frozen prices for {model}")
            frozen[str(model)] = price
    snapshot_panel = {
        **panel,
        "configurations": [
            item for item in panel["configurations"]  # type: ignore[index]
            if str(item["model"]) not in frozen and str(item["model"]) not in cost_models
        ],
    }
    prices = decisions._prices(snapshot_panel, openrouter, google)
    for model, price in frozen.items():
        if model in cost_models:
            continue
        prices[model] = (
            float(price["input"]) / 1_000_000,  # type: ignore[index]
            float(price["output"]) / 1_000_000,  # type: ignore[index]
        )
    missing = sorted(models - set(prices) - cost_models)
    if missing:
        raise ValueError(f"price snapshots do not cover the pooled structural panel: {missing}")
    return prices


def _number(container: object, key: str) -> float:
    if not isinstance(container, dict):
        raise ValueError(f"missing metric container for {key}")
    value = container.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"missing numeric metric: {key}")
    return float(value)


def _metrics(
    row: Mapping[str, object], prices: Mapping[str, tuple[float, float]],
    *, row_costs: decisions.RowCosts | None = None,
) -> dict[str, float]:
    condition = row["condition"]
    model = str(condition["model"])  # type: ignore[index]
    prompt = _number(row.get("usage"), "prompt_tokens")
    completion = _number(row.get("usage"), "completion_tokens")
    verdict = row.get("verdict")
    trace = row.get("trace")
    if not isinstance(trace, list):
        raise ValueError("structural row lacks an execution trace")
    bound_cost = decisions._bound_row_cost(row, model, row_costs)
    if bound_cost is not None and (decisions._number(row.get("efficiency"), "usage_missing_responses") or 0) > 0:
        raise ValueError("bound row cost requires complete row usage")
    return {
        "success": float(isinstance(verdict, dict) and verdict.get("verified_success") is True),
        "total_tokens": _number(row.get("usage"), "total_tokens"),
        "model_requests": _number(row.get("efficiency"), "model_requests_attempted"),
        "tool_schema_exposures": _number(row.get("efficiency"), "tool_schema_exposures"),
        "wall_time_seconds": _number(row.get("efficiency"), "wall_time_seconds"),
        "executed_tool_calls": float(len(trace)),
        "cost_usd": bound_cost if bound_cost is not None else prompt * prices[model][0] + completion * prices[model][1],
    }


def _difference(left: Mapping[str, float], right: Mapping[str, float]) -> dict[str, float]:
    return {metric: float(left[metric]) - float(right[metric]) for metric in METRICS}


def _summary(values: Sequence[Mapping[str, float]], *, samples: int, seed: int) -> JSON:
    output: JSON = {"clusters": len(values), "metrics": {}}
    for metric in METRICS:
        observed = [float(item[metric]) for item in values]
        report = (
            decisions._bootstrap(
                observed,
                samples=samples,
                seed=seed + int(sha256(metric.encode()).hexdigest()[:8], 16),
            )
            if metric in {"success", "cost_usd"}
            else {"estimate": mean(observed), "ci95": None}
        )
        output["metrics"][metric] = report  # type: ignore[index]
    return output


def _dominates(left: Mapping[str, object], right: Mapping[str, object]) -> bool:
    left_metrics = left["metrics"]  # type: ignore[index]
    right_metrics = right["metrics"]  # type: ignore[index]
    no_worse = (
        left_metrics["success"]["estimate"] >= right_metrics["success"]["estimate"]
        and all(
            left_metrics[metric]["estimate"] <= right_metrics[metric]["estimate"]
            for metric in RESOURCE_METRICS
        )
    )
    strict = (
        left_metrics["success"]["estimate"] > right_metrics["success"]["estimate"]
        or any(
            left_metrics[metric]["estimate"] < right_metrics[metric]["estimate"]
            for metric in RESOURCE_METRICS
        )
    )
    return bool(no_worse and strict)


def analyze(
    plan_hashes: Sequence[str],
    rows: Sequence[Mapping[str, object]],
    prices: Mapping[str, tuple[float, float]],
    expected_models: frozenset[str],
    *,
    samples: int,
    seed: int,
    row_costs: decisions.RowCosts | None = None,
) -> JSON:
    decisions._row_cost_models(row_costs, expected_models)
    index: dict[str, dict[str, dict[str, dict[str, float]]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for row in rows:
        condition = row["condition"]
        if not (
            condition["certified_calls"] == 30  # type: ignore[index]
            and condition["representation"] == "raw"  # type: ignore[index]
            and condition["delivery"] == "native"  # type: ignore[index]
            and condition["orchestration"] in {"baseline", "trace_state"}  # type: ignore[index]
        ):
            continue
        model = str(condition["model"])  # type: ignore[index]
        family = str(condition["family_id"])  # type: ignore[index]
        arm = str(condition["orchestration"])  # type: ignore[index]
        if arm in index[model][family]:
            raise ValueError(f"duplicate structural decision cell: {model}|{family}|{arm}")
        index[model][family][arm] = _metrics(row, prices, row_costs=row_costs)
    models = sorted(index)
    families = sorted({family for model in models for family in index[model]})
    if set(models) != set(expected_models) or len(families) != 24 or any(
        set(index[model]) != set(families)
        or any(set(index[model][family]) != {"baseline", "trace_state"} for family in families)
        for model in models
    ):
        raise ValueError(
            f"structural decision matrix must be {len(expected_models)} models "
            "by 24 families by 2 arms"
        )

    incumbents: JSON = {}
    flags: list[dict[str, bool]] = []
    pooled_by_family: list[dict[str, float]] = []
    for family in families:
        pooled_by_family.append(
            {
                metric: mean(
                    index[model][family]["trace_state"][metric]
                    - index[model][family]["baseline"][metric]
                    for model in models
                )
                for metric in METRICS
            }
        )
    for incumbent in models:
        baseline = [index[incumbent][family]["baseline"] for family in families]
        controller = [index[incumbent][family]["trace_state"] for family in families]
        system_move = [_difference(left, right) for left, right in zip(controller, baseline)]
        comparisons: JSON = {}
        for candidate in models:
            if candidate == incumbent:
                continue
            candidate_baseline = [index[candidate][family]["baseline"] for family in families]
            configuration_move = [
                _difference(left, right) for left, right in zip(candidate_baseline, baseline)
            ]
            system_minus_configuration = [
                _difference(left, right) for left, right in zip(system_move, configuration_move)
            ]
            controller_summary = _summary(controller, samples=samples, seed=seed)
            candidate_summary = _summary(candidate_baseline, samples=samples, seed=seed)
            comparison_summary = _summary(
                system_minus_configuration, samples=samples, seed=seed
            )
            comparisons[candidate] = {
                "configuration_move": _summary(configuration_move, samples=samples, seed=seed),
                "system_minus_configuration": comparison_summary,
                "candidate_baseline_dominates_controller": _dominates(
                    candidate_summary, controller_summary
                ),
                "controller_dominates_candidate_baseline": _dominates(
                    controller_summary, candidate_summary
                ),
            }
        decision_flags = {
            "system_success_superior_to_every_configuration_move": all(
                item["system_minus_configuration"]["metrics"]["success"]["ci95"][0] > 0
                for item in comparisons.values()
            ),
            "system_cost_superior_to_every_configuration_move": all(
                item["system_minus_configuration"]["metrics"]["cost_usd"]["ci95"][1] < 0
                for item in comparisons.values()
            ),
            "controller_pareto_nondominated_by_baseline_configurations": not any(
                item["candidate_baseline_dominates_controller"]
                for item in comparisons.values()
            ),
        }
        flags.append(decision_flags)
        incumbents[incumbent] = {
            "baseline_endpoint": _summary(baseline, samples=samples, seed=seed),
            "controller_endpoint": _summary(controller, samples=samples, seed=seed),
            "system_move": _summary(system_move, samples=samples, seed=seed),
            "configuration_comparisons": comparisons,
            "decision_flags": decision_flags,
        }
    return {
        "analysis": "structural-model-versus-system-decision-extension-v1",
        "confirmatory_weight": 0,
        "status": "cost and decision view of the held-out structural test",
        "structural_plan_hashes": sorted(plan_hashes),
        "models": models,
        "semantic_families": len(families),
        "stress_cell": {
            "certified_calls": 30,
            "representation": "raw",
            "delivery": "native",
            "baseline": "baseline",
            "system_move": "trace_state",
        },
        "pooled_system_move": _summary(pooled_by_family, samples=samples, seed=seed),
        "incumbents": incumbents,
        "finite_panel_fractions": {
            name: mean(float(item[name]) for item in flags) for name in flags[0]
        },
        "bootstrap": {"samples": samples, "seed": seed, "cluster": "semantic family"},
        "dominance_rule": (
            "higher success and no higher tokens, model requests, schema exposures, wall time, "
            "or observed token cost; strict on at least one dimension"
        ),
        "claim_boundary": (
            f"The controller comparison is limited to the {len(models)} model-provider "
            f"configurations pooled into the held-out structural test, {len(families)} "
            "held-out semantic families, and the 30-call stress cell."
        ),
    }


def self_check() -> None:
    better = {metric: -1.0 for metric in METRICS}
    better["success"] = 1.0
    worse = {metric: 0.0 for metric in METRICS}
    left = _summary([better] * 24, samples=200, seed=7)
    right = _summary([worse] * 24, samples=200, seed=7)
    assert left["metrics"]["success"]["estimate"] == 1.0  # type: ignore[index]
    assert _dominates(left, right)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--candidate-prices", type=Path, action="append")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=20260816)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        print('{"passed":true}')
        return 0
    sources = _sources(_object(args.manifest))
    candidate_paths = list(args.candidate_prices or CANDIDATE_PRICES)
    models = frozenset().union(*(item[2] for item in sources))
    prices = _pooled_prices(
        _object(PANEL),
        _object(OPENROUTER_PRICES),
        _object(GOOGLE_PRICES),
        [_object(path) for path in candidate_paths],
        models,
    )
    plans = [(_object(plan_path), root, plan_models) for plan_path, root, plan_models in sources]
    rows = [row for plan, root, plan_models in plans for row in _plan_rows(plan, root, plan_models)]
    result = analyze(
        [str(plan["plan_hash"]) for plan, _, _ in plans],
        rows,
        prices,
        models,
        samples=args.bootstrap_samples,
        seed=args.seed,
    )
    row_files = [
        path
        for plan, root, plan_models in plans
        for path in sorted(
            root / f"{item['slug']}.jsonl"
            for item in plan_configurations(plan)
            if str(item["model"]) in plan_models
        )
    ]
    result["inputs"] = [
        {"path": str(path.resolve().relative_to(ROOT)), "sha256": _sha(path)}
        for path in [
            *(plan_path for plan_path, _, _ in sources),
            *row_files,
            PROTOCOL,
            PANEL,
            OPENROUTER_PRICES,
            GOOGLE_PRICES,
            *candidate_paths,
            args.manifest,
        ]
    ]
    result["analysis_source_sha256"] = _sha(Path(__file__))
    result["artifact_hash"] = stable_hash(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(canonical_json(result) + "\n", encoding="utf-8")
    print(canonical_json({"output": str(args.output), "models": len(result["models"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
