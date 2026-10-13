#!/usr/bin/env python3
"""Analyze the completed ToolSandbox menu intervention after transport exclusions."""

from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
from statistics import median
from typing import Mapping

import toolsandbox_menu_intervention as menu
from analyze_model_system_decisions import _prices
from bottleneckbench.core.schema import JSON, stable_hash


AMENDMENTS = (
    menu.ROOT / "results/external-menu-intervention/ling-transport-exclusion-amendment.json",
    menu.ROOT / "results/external-menu-intervention/glm-transport-exclusion-amendment.json",
)
OUTPUT = menu.ROOT / "results/external-menu-intervention/analysis.json"


def _object(path: Path) -> JSON:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _validate_frozen_plan(plan: Mapping[str, object]) -> None:
    baseline_rows = plan.get("baseline_rows")
    conditions = plan.get("conditions")
    configurations = plan.get("configurations")
    if not isinstance(baseline_rows, list) or not isinstance(conditions, list) or not isinstance(configurations, list):
        raise ValueError("menu plan collections are malformed")
    available = 0
    for entry in baseline_rows:
        path = menu.ROOT / str(entry["path"])
        if not path.exists():
            continue
        available += 1
        if entry.get("sha256") != menu._sha(path) or int(entry["rows"]) != len(menu._rows(path)):
            raise ValueError(f"baseline rows changed after menu plan freeze: {path}")
    if len(conditions) != 768 or len(configurations) != 12 or available < 1:
        raise ValueError("menu plan panel is incomplete")


def _similarity_sensitivity(
    models: list[str],
    scenarios: list[str],
    base: Mapping[tuple[str, str], Mapping[str, object]],
    new: Mapping[tuple[str, str, str], Mapping[str, object]],
    *,
    arm: str,
) -> JSON:
    differences = {
        model: [
            menu._value(new[(model, scenario, arm)], "similarity")
            - menu._value(base[(model, scenario)], "similarity")
            for scenario in scenarios
        ]
        for model in models
    }
    model_means = {model: menu._mean(values) for model, values in differences.items()}
    leave_one_out: JSON = {}
    for index, omitted in enumerate(models):
        retained = [model for model in models if model != omitted]
        by_scenario = {
            scenario: [differences[model][scenario_index] for model in retained]
            for scenario_index, scenario in enumerate(scenarios)
        }
        leave_one_out[omitted] = menu._cluster_summary(
            by_scenario,
            seed=menu.ANALYSIS_SEED + 2_000 + index,
        )

    two_most_negative = sorted(models, key=lambda model: (model_means[model], model))[:2]
    retained = [model for model in models if model not in two_most_negative]
    reduced_by_scenario = {
        scenario: [differences[model][scenario_index] for model in retained]
        for scenario_index, scenario in enumerate(scenarios)
    }
    return {
        "by_model": model_means,
        "configuration_signs": {
            "negative": sum(value < 0 for value in model_means.values()),
            "zero": sum(value == 0 for value in model_means.values()),
            "positive": sum(value > 0 for value in model_means.values()),
        },
        "configuration_median": median(model_means.values()),
        "leave_one_configuration_out": leave_one_out,
        "leave_one_configuration_out_estimate_range": [
            min(float(row["estimate"]) for row in leave_one_out.values()),
            max(float(row["estimate"]) for row in leave_one_out.values()),
        ],
        "two_most_negative_omitted": {
            "omitted_models": two_most_negative,
            "selection_rule": "two smallest observed configuration means; descriptive outcome-derived sensitivity",
            **menu._cluster_summary(reduced_by_scenario, seed=menu.ANALYSIS_SEED + 3_000),
        },
    }


def analyze(plan: Mapping[str, object], output_root: Path, *, reporting_models: set[str] | None = None) -> JSON:
    _validate_frozen_plan(plan)
    amendments = [_object(path) for path in AMENDMENTS]
    excluded = {str(amendment["excluded_model"]) for amendment in amendments}
    if len(excluded) != len(amendments) or any(
        amendment.get("parent_plan_hash") != plan.get("plan_hash")
        or amendment.get("task_outcome_used") is not False
        or amendment.get("score_used") is not False
        for amendment in amendments
    ):
        raise ValueError("transport exclusions are not bound outcome-blind to the frozen plan")

    new_rows = [
        row
        for path in sorted(output_root.glob("*.jsonl"))
        for row in menu._rows(path)
        if row.get("model") not in excluded
        and (reporting_models is None or row.get("model") in reporting_models)
    ]
    release_models = {str(row["model"]) for row in new_rows}
    baseline = [
        row
        for entry in plan["baseline_rows"]
        if (path := menu.ROOT / str(entry["path"])).exists()
        for row in menu._rows(path)
        if str(row.get("model")) in release_models
    ]
    return analyze_rows(baseline, new_rows, plan["conditions"], plan["plan_hash"], amendments)


def analyze_rows(baseline, new_rows, conditions, plan_hash, amendments):
    """Analyze hash-checked rows without requiring an unavailable source plan."""
    release_models = {str(row["model"]) for row in new_rows}
    expected_base = len(release_models) * 32
    expected_new = len(release_models) * 64
    if len(baseline) != expected_base or len(new_rows) != expected_new:
        raise ValueError(f"analysis requires {expected_base} baseline and {expected_new} new rows; got {len(baseline)} and {len(new_rows)}")
    if len({str(row.get("plan_hash")) for row in new_rows}) != 1:
        raise ValueError("menu analysis rows do not share one frozen plan hash")

    base = {(str(row["model"]), str(row["scenario_id"])): row for row in baseline}
    new = {
        (str(row["model"]), str(row["base_scenario_id"]), str(row["menu_arm"])): row
        for row in new_rows
    }
    if len(base) != expected_base or len(new) != expected_new:
        raise ValueError("menu analysis contains duplicate matched cells")
    models = sorted({key[0] for key in base})
    scenarios = sorted({key[1] for key in base})
    strata = {str(row["base_scenario_id"]): str(row["stratum"]) for row in conditions}

    metrics: JSON = {}
    for metric in ("similarity", "full_success", "milestone_similarity", "minefield_similarity", "tokens", "requests", "duration_seconds"):
        arm_reports: JSON = {}
        for arm_index, arm in enumerate(menu.ARMS):
            by_scenario: dict[str, list[float]] = defaultdict(list)
            by_model: dict[str, list[float]] = defaultdict(list)
            by_stratum: dict[str, list[float]] = defaultdict(list)
            for model in models:
                for scenario in scenarios:
                    difference = menu._value(new[(model, scenario, arm)], metric) - menu._value(base[(model, scenario)], metric)
                    by_scenario[scenario].append(difference)
                    by_model[model].append(difference)
                    by_stratum[strata[scenario]].append(difference)
            report = menu._cluster_summary(by_scenario, seed=menu.ANALYSIS_SEED + 100 * arm_index + len(metric))
            report["by_model"] = {
                model: {
                    "estimate": menu._mean(values),
                    "degraded": sum(value < 0 for value in values),
                    "tied": sum(value == 0 for value in values),
                    "improved": sum(value > 0 for value in values),
                }
                for model, values in sorted(by_model.items())
            }
            report["by_stratum"] = {name: menu._mean(values) for name, values in sorted(by_stratum.items())}
            arm_reports[arm] = report
        metrics[metric] = arm_reports

    adjusted = menu._holm({arm: float(metrics["similarity"][arm]["sign_flip_p"]) for arm in menu.ARMS})
    for arm in menu.ARMS:
        metrics["similarity"][arm]["holm_p"] = adjusted[arm]
    ten = metrics["similarity"]["ten_distractors"]
    all_tools = metrics["similarity"]["all_tools"]
    similarity_sensitivity = {
        arm: _similarity_sensitivity(models, scenarios, base, new, arm=arm)
        for arm in menu.ARMS
    }
    stress_by_scenario: dict[str, list[float]] = defaultdict(list)
    stress_by_model: dict[str, list[float]] = defaultdict(list)
    for model in models:
        for scenario in scenarios:
            difference = menu._value(new[(model, scenario, "all_tools")], "similarity") - menu._value(
                new[(model, scenario, "ten_distractors")], "similarity"
            )
            stress_by_scenario[scenario].append(difference)
            stress_by_model[model].append(difference)
    stress_arm_contrast = menu._cluster_summary(stress_by_scenario, seed=menu.ANALYSIS_SEED + 999)
    stress_arm_contrast["by_model"] = {
        model: menu._mean(values) for model, values in sorted(stress_by_model.items())
    }
    level_rows = {
        "scoped": baseline,
        **{arm: [row for row in new_rows if row["menu_arm"] == arm] for arm in menu.ARMS},
    }
    scoped_visible_tools = {
        str(condition["base_scenario_id"]): int(condition["visible_tools"]) - 10
        for condition in conditions
        if condition["menu_arm"] == "ten_distractors"
    }
    levels = {
        arm: {
            "visible_tools_mean": (
                sum(int(row.get("visible_tools", 0)) for row in rows) / len(rows)
                if arm != "scoped"
                else menu._mean([
                    scoped_visible_tools[str(row["scenario_id"])]
                    for row in rows
                ])
            ),
            **{
                metric: menu._mean([menu._value(row, metric) for row in rows])
                for metric in ("similarity", "full_success", "tokens", "requests", "duration_seconds")
            },
            "by_model_similarity": {
                model: menu._mean([menu._value(row, "similarity") for row in rows if row["model"] == model])
                for model in models
            },
        }
        for arm, rows in level_rows.items()
    }
    prices = _prices(
        _object(menu.HERE / "eligible-analysis-panel.json"),
        _object(menu.HERE / "openrouter-endpoint-snapshot.json"),
        _object(menu.HERE / "google-cloud-price-snapshot.json"),
    )
    incremental_cost = sum(
        float(row["usage"]["prompt_tokens"]) * prices[str(row["model"])][0]
        + float(row["usage"]["completion_tokens"]) * prices[str(row["model"])][1]
        for row in new_rows
    )
    payload: JSON = {
        "analysis": "toolsandbox-menu-intervention-v1",
        "evidence_role": "prospectively specified auxiliary mechanism evidence; confirmatory_weight=0 in the frozen plan",
        "plan_hash": plan_hash,
        "transport_exclusions": amendments,
        "rows": {"scoped_immutable": expected_base, "new": expected_new, "total": expected_base + expected_new},
        "models": len(models),
        "scenarios": len(scenarios),
        "menu_pressure_supported": float(all_tools["estimate"]) < 0 and float(all_tools["ci95"][1]) < 0 and float(all_tools["holm_p"]) < 0.05,
        "ordered_point_estimates": float(all_tools["estimate"]) < float(ten["estimate"]) < 0,
        "stress_arm_separation_supported": (
            float(stress_arm_contrast["estimate"]) < 0
            and float(stress_arm_contrast["ci95"][1]) < 0
            and float(stress_arm_contrast["sign_flip_p"]) < 0.05
        ),
        "stress_arm_similarity_contrast": stress_arm_contrast,
        "similarity_sensitivity": similarity_sensitivity,
        "levels": levels,
        "incremental_estimated_cost_usd": incremental_cost,
        "metrics": metrics,
        "provider_failures_in_scored_rows": sum(
            any(marker in str(row.get("error")) for marker in ("provider HTTP", "provider transport failure"))
            for row in new_rows
        ),
        "model_response_contract_failures": sum(
            "model response contract" in str(row.get("error")) for row in new_rows
        ),
        "failed_closed_scored_rows": sum(row.get("status") != "complete" for row in new_rows),
        "inference": "scenario-cluster bootstrap and sign-flip; models treated as the fixed transport-eligible panel",
        "baseline_timing": "the scoped trajectories predate the two stress arms and were not rerun concurrently",
        "claim_boundary": "auxiliary matched mechanism evidence at 2-34 visible ToolSandbox tools; scoped menu is an authored-scope upper bound, not a deployed router",
    }
    payload["artifact_hash"] = stable_hash(payload)
    return payload


def self_check() -> None:
    _validate_frozen_plan(_object(menu.PLAN))
    amendments = [_object(path) for path in AMENDMENTS]
    assert len({str(amendment["excluded_model"]) for amendment in amendments}) == len(amendments)
    amendment = amendments[-1]
    assert int(amendment["expected_complete_models"]) * 32 == int(amendment["expected_baseline_rows"])
    assert int(amendment["expected_complete_models"]) * 64 == int(amendment["expected_new_rows"])
    models, scenarios = ["a", "b", "c"], ["s1", "s2"]
    base = {(model, scenario): {"score": {"similarity": 0.5}} for model in models for scenario in scenarios}
    new = {
        (model, scenario, "all_tools"): {"score": {"similarity": 0.5 + delta}}
        for model, delta in zip(models, (-0.3, -0.1, 0.2))
        for scenario in scenarios
    }
    check = _similarity_sensitivity(models, scenarios, base, new, arm="all_tools")
    assert check["configuration_signs"] == {"negative": 2, "zero": 0, "positive": 1}
    assert check["two_most_negative_omitted"]["omitted_models"] == ["a", "b"]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.self_check:
        self_check()
        print('{"passed":true}')
    else:
        report = analyze(_object(menu.PLAN), menu.OUTPUT)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({key: report[key] for key in ("models", "rows", "menu_pressure_supported", "ordered_point_estimates", "stress_arm_separation_supported")}, sort_keys=True))
