#!/usr/bin/env python3
"""Pool complete model blocks from the frozen structural plans into one held-out test."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import analyze_structural_repair_holdout as analysis
from build_release_analysis_manifest import _matrix_source, plan_configurations, validate_plan_artifact, validate_plan_sources


def analyze_sources(
    sources: list[tuple[Path, Path, set[str] | None]], models: set[str], *,
    verify_plan_inputs: bool = True,
    require_zero_weight: bool = True,
) -> dict[str, object]:
    if not sources or not models:
        raise ValueError("structural release view requires plan sources and complete models")
    rows: list[dict[str, object]] = []
    plan_hashes: list[str] = []
    plan_provenance: list[dict[str, object]] = []
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
            raise ValueError("structural source selects a model outside its frozen plan")
        plan_models = available & models if declared_models is None else declared_models & models
        if not plan_models:
            continue
        if covered & plan_models:
            raise ValueError("structural model is selected from more than one frozen plan")
        # Row files are named after the plan's own slugs.  Plans whose slugs already
        # carry the "-structural" suffix take it once; the rest are used verbatim.
        flat_slugs = not all(
            str(item["slug"]).endswith("-structural")
            for item in plan_configurations(plan)
            if str(item["model"]) in plan_models
        )
        validation_sources.append(
            _matrix_source(plan_path, input_root if "configuration" in plan else input_root / "active", plan_models, toolsandbox=flat_slugs)
        )
        selected_sources.append((plan, input_root, plan_models))
        covered |= plan_models
    validate_plan_sources(
        validation_sources,
        verify_plan_inputs=verify_plan_inputs,
        require_zero_weight=require_zero_weight,
    )
    for (plan, input_root, plan_models), source in zip(selected_sources, validation_sources):
        view = {
            **plan,
            "configurations": [item for item in plan_configurations(plan) if item["model"] in plan_models],
            "conditions": [item for item in plan["conditions"] if item["model"] in plan_models],
        }
        if "configuration" in plan:
            # Source validation above binds the unmodified single-model plan and
            # exact row file. No compatibility plan or rewritten rows are created.
            rows.extend(json.loads(line) for path in source["rows"] for line in path.read_text().splitlines())
        else:
            rows.extend(analysis._load_rows(view, input_root))
        plan_hashes.append(str(plan["plan_hash"]))
        plan_provenance.append({
            "plan_hash": str(plan["plan_hash"]),
            "frozen_at": str(plan["frozen_at"]),
            "configurations": sorted(plan_models),
            "declared_row_weight": int(plan.get("confirmatory_weight", 1)),
            "structural_outcomes_seen_before_freeze": bool(
                plan.get("structural_outcomes_access_before_freeze",
                         plan.get("outcome_access_before_freeze", False))
            ),
            "configurations_selected_on_core_outcomes": bool(
                plan.get("candidate_core_outcomes_inspected_before_freeze", False)
            ),
        })
    if covered != models:
        raise ValueError("structural plan sources do not cover the requested complete models")

    extracted = analysis._extract(rows)
    layers = {
        layer: analysis._summarize_layer(
            contributions,
            layer=layer,
            bootstrap_samples=20_000,
            randomization_samples=100_000,
            seed=20260814,
        )
        for layer, contributions in extracted.items()
    }
    adjusted = analysis.holm_adjust({
        layer: float(summary["activation_test"]["raw_p"])
        for layer, summary in layers.items()
    })
    for layer, holm_p in adjusted.items():
        test = layers[layer]["activation_test"]
        test["holm_p"] = holm_p
        test["activated_at_0.05"] = test["all_point_directions_positive"] is True and holm_p < 0.05
    return {
        "schema": "structural-repair-holdout-multi-plan-analysis-v1",
        "plan_hashes": sorted(plan_hashes),
        "plan_provenance": sorted(plan_provenance, key=lambda item: str(item["plan_hash"])),
        "status": "complete",
        "confirmatory_weight": 0,
        "rows": len(rows),
        "analysis_rows": len(rows),
        "provider_failures": 0,
        "models": sorted(models),
        "layers": layers,
        "resource_profile_by_cell": analysis._resource_profile(rows),
        "bootstrap_samples": 20_000,
        "randomization_samples": 100_000,
        "complete_case_policy": "each included model is complete against its own frozen plan",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--input-root", type=Path)
    parser.add_argument("--source", nargs=2, action="append", metavar=("PLAN", "INPUT_ROOT"))
    parser.add_argument("--source-spec", nargs=3, action="append", metavar=("PLAN", "INPUT_ROOT", "MODELS_CSV"))
    parser.add_argument("--model", action="append", default=[])
    parser.add_argument("--portable-release", action="store_true")
    parser.add_argument(
        "--allow-declared-weights",
        action="store_true",
        help="pool plans whose released rows carry different declared weights",
    )
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
        sources,
        models,
        verify_plan_inputs=not args.portable_release,
        require_zero_weight=not args.allow_declared_weights,
    )
    analysis._atomic(args.output, result)
    print(json.dumps({"passed": True, "models": len(models), "rows": result["rows"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
