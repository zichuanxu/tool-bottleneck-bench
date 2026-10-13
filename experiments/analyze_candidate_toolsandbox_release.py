#!/usr/bin/env python3
"""Analyze transport-complete candidate ToolSandbox configuration blocks."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
from statistics import fmean
from typing import Mapping, Sequence

import toolsandbox_menu_intervention as menu
from bottleneckbench.experiments import _atomic_write
from bottleneckbench.core.schema import JSON, normalize_floats, stable_hash
from build_release_analysis_manifest import _matrix_source, plan_configurations, validate_plan_artifact, validate_plan_sources


METRICS = (
    "similarity", "full_success", "milestone_similarity", "minefield_similarity",
    "tokens", "requests", "duration_seconds",
)


def _infrastructure_failure(row: Mapping[str, object]) -> bool:
    error = str(row.get("error", ""))
    return (
        row.get("status") == "failed_closed"
        and "AgnesError" in error
        and "model response contract" not in error
    )


def _validate_plan(plan: Mapping[str, object]) -> None:
    unhashed = dict(plan)
    supplied = unhashed.pop("plan_hash", None)
    if supplied != stable_hash(unhashed):
        raise ValueError("candidate ToolSandbox frozen plan hash mismatch")


def _rows(plan: Mapping[str, object], input_root: Path, models: set[str]) -> list[JSON]:
    values: list[JSON] = []
    for configuration in plan_configurations(plan):
        if str(configuration["model"]) not in models:
            continue
        slug = str(configuration["slug"]).removesuffix("-primary")
        path = input_root / f"{slug}.jsonl"
        values.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    return values


def _mean(values: Sequence[float]) -> float:
    return fmean(values) if values else float("nan")


def _analyze_rows(rows: Sequence[Mapping[str, object]], models: set[str], strata: Mapping[str, str], plan_hashes: list[str]) -> JSON:
    if not models or len(rows) != 96 * len(models):
        raise ValueError("candidate ToolSandbox release view is not block-complete")
    infrastructure = [row for row in rows if _infrastructure_failure(row)]
    if infrastructure:
        raise ValueError("candidate ToolSandbox release view contains an incomplete configuration block")
    by_key = {
        (str(row["model"]), str(row["base_scenario_id"]), str(row["menu_arm"])): row
        for row in rows
    }
    scenarios = sorted({key[1] for key in by_key})
    blocks = [(model, scenario) for model in sorted(models) for scenario in scenarios]
    contrasts: JSON = {}
    for arm in ("ten_distractors", "all_tools"):
        report: JSON = {}
        for metric in METRICS:
            values: dict[str, list[float]] = defaultdict(list)
            per_model: dict[str, list[float]] = defaultdict(list)
            per_stratum: dict[str, list[float]] = defaultdict(list)
            for model, scenario in blocks:
                difference = menu._value(by_key[(model, scenario, arm)], metric) - menu._value(
                    by_key[(model, scenario, "scoped")], metric
                )
                values[scenario].append(difference)
                per_model[model].append(difference)
                per_stratum[strata[scenario]].append(difference)
            summary = menu._cluster_summary(
                values,
                seed=menu.ANALYSIS_SEED + int(stable_hash(f"{arm}:{metric}")[:8], 16),
            )
            summary["by_model"] = {model: _mean(observed) for model, observed in sorted(per_model.items())}
            summary["by_stratum"] = {name: _mean(observed) for name, observed in sorted(per_stratum.items())}
            summary["leave_one_model_out"] = {
                omitted: _mean([value for model, observed in per_model.items() if model != omitted for value in observed])
                for omitted in models
            }
            report[metric] = summary
        contrasts[arm] = report
    adjusted = menu._holm(
        {arm: float(contrasts[arm]["similarity"]["sign_flip_p"]) for arm in contrasts}
    )
    for arm, value in adjusted.items():
        contrasts[arm]["similarity"]["holm_p"] = value
    all_minus_ten = [
        menu._value(by_key[(model, scenario, "all_tools")], "similarity")
        - menu._value(by_key[(model, scenario, "ten_distractors")], "similarity")
        for model, scenario in blocks
    ]
    return normalize_floats({
        "schema": "candidate-toolsandbox-matched-menu-release-analysis-v1",
        "plan_hashes": sorted(plan_hashes),
        "status": "complete",
        "rows": len(rows),
        "matched_blocks": len(blocks),
        "models": sorted(models),
        "scenarios": len(scenarios),
        "provider_failures": 0,
        "complete_case_policy": "only complete frozen 96-row configuration blocks enter release contrasts",
        "contrasts": contrasts,
        "all_tools_minus_ten_distractors_similarity": {
            "estimate": _mean(all_minus_ten),
            "degraded": sum(value < 0 for value in all_minus_ten),
            "tied": sum(value == 0 for value in all_minus_ten),
            "improved": sum(value > 0 for value in all_minus_ten),
        },
        "inference": f"scenario-cluster bootstrap and sign flip over {len(models)} transport-complete frozen configurations",
        "confirmatory_weight": 0,
    })  # type: ignore[return-value]


def analyze(plan: Mapping[str, object], rows: Sequence[Mapping[str, object]], models: set[str]) -> JSON:
    _validate_plan(plan)
    planned = {
        str(item["condition_id"]): item
        for item in plan["conditions"]  # type: ignore[index]
        if str(item["model"]) in models
    }
    if {str(row.get("condition_id")) for row in rows} != set(planned):
        raise ValueError("candidate ToolSandbox release view is not block-complete")
    strata = {
        str(item["base_scenario_id"]): str(item["stratum"])
        for item in planned.values()
    }
    return _analyze_rows(rows, models, strata, [str(plan["plan_hash"])])


def analyze_sources(
    sources: list[tuple[Path, Path, set[str] | None]], models: set[str], *,
    verify_plan_inputs: bool = True,
) -> JSON:
    rows: list[JSON] = []
    strata: dict[str, str] = {}
    hashes: list[str] = []
    covered: set[str] = set()
    validation_sources: list[dict[str, object]] = []
    selected_sources: list[tuple[dict[str, object], Path, set[str]]] = []
    for plan_path, input_root, declared_models in sources:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        validate_plan_artifact(plan_path, plan, verify_inputs=verify_plan_inputs)
        available = {
            str(item["model"]) for item in plan_configurations(plan)
        }
        if declared_models is not None and not declared_models <= available:
            raise ValueError("ToolSandbox source selects a model outside its frozen plan")
        plan_models = available & models if declared_models is None else declared_models & models
        if not plan_models:
            continue
        if covered & plan_models:
            raise ValueError("ToolSandbox model is selected from more than one frozen plan")
        validation_sources.append(
            _matrix_source(plan_path, input_root, plan_models, toolsandbox=True)
        )
        selected_sources.append((plan, input_root, plan_models))
        covered |= plan_models
    validate_plan_sources(
        validation_sources, verify_plan_inputs=verify_plan_inputs
    )
    for plan, input_root, plan_models in selected_sources:
        rows.extend(_rows(plan, input_root, plan_models))
        for item in plan["conditions"]:  # type: ignore[index]
            if str(item["model"]) in plan_models:
                scenario = str(item["base_scenario_id"])
                value = str(item["stratum"])
                if scenario in strata and strata[scenario] != value:
                    raise ValueError("ToolSandbox stratum changed across frozen plans")
                strata[scenario] = value
        hashes.append(str(plan["plan_hash"]))
    if covered != models:
        raise ValueError("ToolSandbox plan sources do not cover the requested complete models")
    return _analyze_rows(rows, models, strata, hashes)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--input-root", type=Path)
    parser.add_argument("--source", nargs=2, action="append", metavar=("PLAN", "INPUT_ROOT"))
    parser.add_argument("--source-spec", nargs=3, action="append", metavar=("PLAN", "INPUT_ROOT", "MODELS_CSV"))
    parser.add_argument("--model", action="append", default=[])
    parser.add_argument("--portable-release", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    models = set(args.model)
    sources = [(Path(plan), Path(root), None) for plan, root in (args.source or [])]
    sources += [
        (Path(plan), Path(root), set(models_csv.split(",")))
        for plan, root, models_csv in (args.source_spec or [])
    ]
    if args.plan is not None or args.input_root is not None:
        if args.plan is None or args.input_root is None:
            parser.error("--plan and --input-root must be supplied together")
        sources.append((args.plan, args.input_root, None))
    result = analyze_sources(
        sources, models, verify_plan_inputs=not args.portable_release
    )
    _atomic_write(args.output, result)
    print(json.dumps({"passed": True, "models": len(models), "rows": result["rows"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
