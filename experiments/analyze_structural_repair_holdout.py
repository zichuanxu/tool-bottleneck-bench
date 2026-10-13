#!/usr/bin/env python3
"""Frozen analysis for the structural diagnosis-to-repair holdout."""

from __future__ import annotations

import argparse
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
from statistics import fmean
import sys
from tempfile import NamedTemporaryFile
from typing import Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bottleneckbench.analysis import (  # noqa: E402
    _bootstrap,
    _sign_flip_p_one_sided,
    holm_adjust,
    is_infrastructure_failure,
)
from bottleneckbench.core.schema import JSON, canonical_json, stable_hash  # noqa: E402


def _released_plan(directory: Path, name: str) -> Path:
    """Prefer the redacted public copy of a frozen plan; fall back to the original."""

    redacted = directory / f"{name}.redacted.json"
    return redacted if redacted.exists() else directory / f"{name}.json"


DEFAULT_PLAN = _released_plan(ROOT / "results/prospective-plans/structural-repair-holdout", "plan")
DEFAULT_INPUT = ROOT / "results/prospective-execution/structural-repair-holdout"
DEFAULT_OUTPUT = ROOT / "results/analysis/structural-repair-holdout.json"
METRICS = (
    "diagnosed_loss",
    "stress_repair_gain",
    "low_pressure_repair_gain",
    "repair_selectivity",
    "off_target_selectivity",
    "matched_specificity",
    "residual_loss",
)
COMPONENTS = (
    "diagnosed_loss",
    "stress_repair_gain",
    "repair_selectivity",
    "matched_specificity",
)
CELLS = (
    (2, "raw", "baseline", "native"),
    (30, "raw", "baseline", "native"),
    (2, "raw", "trace_state", "native"),
    (30, "raw", "trace_state", "native"),
    (2, "raw", "baseline", "canonical"),
    (30, "raw", "baseline", "canonical"),
    (2, "generic_compact", "baseline", "native"),
    (2, "generic_compact", "baseline", "canonical"),
    (2, "generic_compact", "trace_state", "native"),
)
LAYER_CELLS = {
    "workflow": (
        (2, "raw", "baseline", "native"),
        (30, "raw", "baseline", "native"),
        (2, "raw", "trace_state", "native"),
        (30, "raw", "trace_state", "native"),
        (2, "raw", "baseline", "canonical"),
        (30, "raw", "baseline", "canonical"),
    ),
    "returned_state_interface": (
        (2, "raw", "baseline", "native"),
        (2, "generic_compact", "baseline", "native"),
        (2, "raw", "baseline", "canonical"),
        (2, "generic_compact", "baseline", "canonical"),
        (2, "raw", "trace_state", "native"),
        (2, "generic_compact", "trace_state", "native"),
    ),
}


def _object(path: Path) -> JSON:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root is not an object: {path}")
    return value


def _load_rows(plan: Mapping[str, object], input_root: Path) -> list[JSON]:
    rows: list[JSON] = []
    for configuration in plan["configurations"]:  # type: ignore[index]
        path = input_root / "active" / f"{configuration['slug']}.jsonl"
        if not path.exists():
            raise ValueError(f"missing structural result file: {path}")
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"non-object row at {path}:{number}")
            rows.append(value)
    return rows


def _cell(condition: Mapping[str, object]) -> tuple[int, str, str, str]:
    return (
        int(condition["certified_calls"]),
        str(condition["representation"]),
        str(condition["orchestration"]),
        str(condition["delivery"]),
    )


def _success(row: Mapping[str, object]) -> float:
    verdict = row.get("verdict")
    return float(isinstance(verdict, dict) and verdict.get("verified_success") is True)


def _metrics(values: Mapping[tuple[int, str, str, str], float], layer: str) -> dict[str, float]:
    base_low, base_stress, repair_low, repair_stress, off_low, off_stress = (
        values[cell] for cell in LAYER_CELLS[layer]
    )
    repair_selectivity = (
        repair_stress - base_stress - repair_low + base_low
    )
    off_target_selectivity = off_stress - base_stress - off_low + base_low
    return {
        "diagnosed_loss": base_low - base_stress,
        "stress_repair_gain": repair_stress - base_stress,
        "low_pressure_repair_gain": repair_low - base_low,
        "repair_selectivity": repair_selectivity,
        "off_target_selectivity": off_target_selectivity,
        "matched_specificity": repair_selectivity - off_target_selectivity,
        "residual_loss": base_low - repair_stress,
    }


def _extract(rows: Sequence[Mapping[str, object]]) -> dict[str, list[JSON]]:
    cells: dict[tuple[str, str], dict[tuple[int, str, str, str], float]] = defaultdict(dict)
    metadata: dict[tuple[str, str], tuple[str, str]] = {}
    for row in rows:
        condition = row.get("condition")
        if not isinstance(condition, dict):
            raise ValueError("structural row lacks a condition")
        key = (str(condition["family_id"]), str(condition["model"]))
        coordinate = _cell(condition)
        if coordinate in cells[key]:
            raise ValueError(f"duplicate structural cell: {key}|{coordinate}")
        cells[key][coordinate] = _success(row)
        observed = (str(condition["intent"]), str(condition["stratum"]))
        if key in metadata and metadata[key] != observed:
            raise ValueError(f"structural metadata drift: {key}")
        metadata[key] = observed
    output = {layer: [] for layer in LAYER_CELLS}
    for (family, model), values in sorted(cells.items()):
        if set(values) != set(CELLS):
            raise ValueError(f"incomplete structural family-model block: {family}|{model}")
        intent, stratum = metadata[(family, model)]
        for layer in output:
            output[layer].append(
                {
                    "family": family,
                    "intent": intent,
                    "stratum": stratum,
                    "model": model,
                    "metrics": _metrics(values, layer),
                }
            )
    return output


def _cluster_values(contributions: Sequence[Mapping[str, object]]) -> dict[str, list[float]]:
    clustered: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for item in contributions:
        metrics = item["metrics"]
        for metric in METRICS:
            clustered[str(item["family"])][metric].append(float(metrics[metric]))  # type: ignore[index]
    return {
        metric: [fmean(values[metric]) for _, values in sorted(clustered.items())]
        for metric in METRICS
    }


def _point_estimates(contributions: Sequence[Mapping[str, object]]) -> dict[str, float]:
    values = _cluster_values(contributions)
    return {metric: fmean(values[metric]) for metric in METRICS}


def _group_estimates(
    contributions: Sequence[Mapping[str, object]], key: str
) -> dict[str, dict[str, float]]:
    groups: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for item in contributions:
        groups[str(item[key])].append(item)
    return {name: _point_estimates(items) for name, items in sorted(groups.items())}


def _summarize_layer(
    contributions: Sequence[Mapping[str, object]],
    *,
    layer: str,
    bootstrap_samples: int,
    randomization_samples: int,
    seed: int,
) -> JSON:
    values = _cluster_values(contributions)
    if any(len(values[metric]) != 24 for metric in METRICS):
        raise ValueError(f"{layer} does not contain 24 complete intent clusters")
    summaries = {
        metric: _bootstrap(
            values[metric],
            samples=bootstrap_samples,
            seed=seed + int(stable_hash(f"{layer}:{metric}:bootstrap")[:8], 16),
        )
        for metric in METRICS
    }
    component_p = {
        metric: _sign_flip_p_one_sided(
            values[metric],
            samples=randomization_samples,
            seed=seed + int(stable_hash(f"{layer}:{metric}:sign")[:8], 16),
        )
        for metric in COMPONENTS
    }
    models = sorted({str(item["model"]) for item in contributions})
    strata = sorted({str(item["stratum"]) for item in contributions})
    return {
        "cluster_unit": "unique_semantic_intent",
        "clusters": 24,
        "configurations": len(models),
        "metrics": summaries,
        "activation_test": {
            "test": "four-component intersection-union with one-sided intent-cluster sign flips",
            "component_p_one_sided": component_p,
            "raw_p": max(component_p.values()),
            "all_point_directions_positive": all(
                float(summaries[metric]["estimate"]) > 0 for metric in COMPONENTS
            ),
        },
        "by_configuration": _group_estimates(contributions, "model"),
        "by_semantic_stratum": _group_estimates(contributions, "stratum"),
        "leave_one_organization_out": {
            model: _point_estimates(
                [item for item in contributions if item["model"] != model]
            )
            for model in models
        },
        "leave_one_semantic_stratum_out": {
            stratum: _point_estimates(
                [item for item in contributions if item["stratum"] != stratum]
            )
            for stratum in strata
        },
    }


def _resource_profile(rows: Sequence[Mapping[str, object]]) -> JSON:
    groups: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        condition = row["condition"]
        coordinate = _cell(condition)  # type: ignore[arg-type]
        name = f"c{coordinate[0]}|{coordinate[1]}|{coordinate[2]}|{coordinate[3]}"
        usage = row.get("usage")
        efficiency = row.get("efficiency")
        if not isinstance(usage, dict) or not isinstance(efficiency, dict):
            raise ValueError("structural row lacks usage or efficiency")
        values = groups[name]
        values["prompt_tokens"].append(float(usage["prompt_tokens"]))
        values["completion_tokens"].append(float(usage["completion_tokens"]))
        values["total_tokens"].append(float(usage["total_tokens"]))
        values["wall_time_seconds"].append(float(efficiency["wall_time_seconds"]))
        values["tool_schema_bytes"].append(float(efficiency["tool_schema_bytes"]))
        values["tool_schema_exposures"].append(float(efficiency["tool_schema_exposures"]))
        values["provider_requests"].append(float(efficiency["model_requests_attempted"]))
        values["executed_tool_calls"].append(float(len(row.get("trace", []))))
    return {
        name: {
            "rows": len(next(iter(values.values()))),
            **{metric: fmean(observed) for metric, observed in sorted(values.items())},
        }
        for name, values in sorted(groups.items())
    }


def analyze(
    plan: Mapping[str, object],
    rows: Sequence[Mapping[str, object]],
    *,
    bootstrap_samples: int,
    randomization_samples: int,
    seed: int = 20260814,
) -> JSON:
    planned = {
        stable_hash(condition): condition
        for condition in plan["conditions"]  # type: ignore[index]
    }
    models = {str(item["model"]) for item in plan["configurations"]}  # type: ignore[index]
    expected_rows = len(models) * 24 * len(CELLS)
    if not models or len(planned) != expected_rows or len(rows) != expected_rows:
        raise ValueError("structural analysis is not estimate-complete")
    seen: set[str] = set()
    expected_weight = int(plan.get("confirmatory_weight", 1))
    for row in rows:
        condition = row.get("condition")
        condition_id = row.get("condition_id")
        if (
            row.get("plan_hash") != plan["plan_hash"]
            or row.get("attempt") != 1
            or row.get("confirmatory_weight") != expected_weight
            or not isinstance(condition, dict)
            or stable_hash(condition) != condition_id
            or condition_id not in planned
            or condition != planned[condition_id]
        ):
            raise ValueError(f"invalid active structural row: {condition_id}")
        if condition_id in seen:
            raise ValueError(f"duplicate structural condition: {condition_id}")
        seen.add(str(condition_id))
    if seen != set(planned):
        raise ValueError("active structural results do not match the frozen plan")
    infrastructure = [row for row in rows if is_infrastructure_failure(row)]
    contaminated = {
        (str(row["condition"]["model"]), str(row["condition"]["family_id"]))
        for row in infrastructure
    }
    analysis_rows = [
        row for row in rows
        if (str(row["condition"]["model"]), str(row["condition"]["family_id"])) not in contaminated
    ]
    extracted = _extract(analysis_rows)
    layers = {
        layer: _summarize_layer(
            contributions,
            layer=layer,
            bootstrap_samples=bootstrap_samples,
            randomization_samples=randomization_samples,
            seed=seed,
        )
        for layer, contributions in extracted.items()
    }
    adjusted = holm_adjust(
        {
            layer: float(summary["activation_test"]["raw_p"])
            for layer, summary in layers.items()
        }
    )
    for layer, holm_p in adjusted.items():
        test = layers[layer]["activation_test"]
        test["holm_p"] = holm_p
        test["activated_at_0.05"] = (
            test["all_point_directions_positive"] is True and holm_p < 0.05
        )
    return {
        "schema": "structural-repair-holdout-analysis-v1",
        "plan_hash": plan["plan_hash"],
        "status": "incomplete" if infrastructure else "complete",
        "confirmatory_weight": expected_weight,
        "rows": len(rows),
        "analysis_rows": len(analysis_rows),
        "provider_failures": len(infrastructure),
        "infrastructure_condition_ids": sorted(str(row["condition_id"]) for row in infrastructure),
        "complete_case_policy": (
            "drop the complete model-by-intent cluster containing an infrastructure row"
            if infrastructure else "not needed"
        ),
        "models": sorted(models),
        "layers": layers,
        "resource_profile_by_cell": _resource_profile(analysis_rows),
        "bootstrap_samples": bootstrap_samples,
        "randomization_samples": randomization_samples,
        "generated_from_plan_frozen_at": plan["frozen_at"],
    }


def _atomic(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(canonical_json(dict(payload)) + "\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def _self_check() -> JSON:
    success = {
        (2, "raw", "baseline", "native"): 1,
        (30, "raw", "baseline", "native"): 0,
        (2, "raw", "trace_state", "native"): 1,
        (30, "raw", "trace_state", "native"): 1,
        (2, "raw", "baseline", "canonical"): 1,
        (30, "raw", "baseline", "canonical"): 0,
        (2, "generic_compact", "baseline", "native"): 0,
        (2, "generic_compact", "baseline", "canonical"): 1,
        (2, "generic_compact", "trace_state", "native"): 0,
    }
    rows = []
    for family in range(24):
        for model in range(4):
            for calls, representation, orchestration, delivery in CELLS:
                passed = bool(success[(calls, representation, orchestration, delivery)])
                rows.append(
                    {
                        "condition": {
                            "family_id": f"family-{family}",
                            "intent": f"intent-{family}",
                            "stratum": f"stratum-{family % 4}",
                            "model": f"model-{model}",
                            "certified_calls": calls,
                            "representation": representation,
                            "orchestration": orchestration,
                            "delivery": delivery,
                        },
                        "verdict": {"verified_success": passed},
                    }
                )
    extracted = _extract(rows)
    summaries = {
        layer: _summarize_layer(
            contributions,
            layer=layer,
            bootstrap_samples=200,
            randomization_samples=2_000,
            seed=7,
        )
        for layer, contributions in extracted.items()
    }
    for layer, summary in summaries.items():
        estimates = summary["metrics"]
        if not (
            estimates["diagnosed_loss"]["estimate"] == 1
            and estimates["repair_selectivity"]["estimate"] == 1
            and estimates["matched_specificity"]["estimate"] == 1
            and estimates["off_target_selectivity"]["estimate"] == 0
        ):
            raise AssertionError(f"structural analysis formula failed: {layer}")
    return {"passed": True, "rows": len(rows), "layers": sorted(summaries)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--input-root", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--bootstrap-samples", type=int, default=20_000)
    parser.add_argument("--randomization-samples", type=int, default=100_000)
    args = parser.parse_args()
    if args.check:
        print(canonical_json(_self_check()))
        return 0
    plan = _object(args.plan)
    result = analyze(
        plan,
        _load_rows(plan, args.input_root),
        bootstrap_samples=args.bootstrap_samples,
        randomization_samples=args.randomization_samples,
    )
    result["inputs"] = [
        {
            "path": str(path.resolve().relative_to(ROOT)),
            "sha256": sha256(path.read_bytes()).hexdigest(),
        }
        for path in [args.plan, *sorted((args.input_root / "active").glob("*.jsonl"))]
    ]
    result["analysis_source_sha256"] = sha256(Path(__file__).read_bytes()).hexdigest()
    result["artifact_hash"] = stable_hash(result)
    _atomic(args.output, result)
    print(canonical_json({"output": str(args.output), "rows": result["rows"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
