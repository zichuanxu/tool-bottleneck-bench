#!/usr/bin/env python3
"""Freeze, execute, and analyze matched ToolSandbox menu-pressure twins."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import random
import sys
from typing import Any, Iterable, Mapping, Sequence

import dill


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

import toolsandbox_anchor as anchor
import toolsandbox_external_validation as external
from bottleneckbench.core.experiments import ExperimentStore
from bottleneckbench.core.schema import JSON, canonical_json, stable_hash


PROTOCOL = HERE / "toolsandbox-menu-intervention-protocol.md"
BUDGET = HERE / "toolsandbox-menu-budget.json"
BASE_PLAN = ROOT / "results/prospective-plans/external-validation/eligible-plan.json"
BASE_ROWS = ROOT / "results/prospective-execution/external-validation"
def _released_plan(directory: Path, name: str) -> Path:
    """Prefer the redacted public copy of a frozen plan; fall back to the original."""

    redacted = directory / f"{name}.redacted.json"
    return redacted if redacted.exists() else directory / f"{name}.json"


PLAN = _released_plan(ROOT / "results/external-menu-intervention", "plan")
AUDIT = ROOT / "results/external-menu-intervention/construct-audit.json"
OUTPUT = ROOT / "results/external-menu-intervention/execution"
ANALYSIS = ROOT / "results/external-menu-intervention/analysis.json"
GOOGLE_PRICES = HERE / "google-cloud-price-snapshot.json"
SOURCE_INTEGRITY = ROOT / "results/external-menu-intervention/toolsandbox-source-integrity.json"

ARMS = {
    "ten_distractors": "_10_distraction_tools",
    "all_tools": "_all_tools",
}
ARM_CATEGORIES = {
    "ten_distractors": "TEN_DISTRACTION_TOOLS",
    "all_tools": "ALL_TOOLS_AVAILABLE",
}
MENU_CATEGORIES = {
    "NO_DISTRACTION_TOOLS",
    "THREE_DISTRACTION_TOOLS",
    "TEN_DISTRACTION_TOOLS",
    "ALL_TOOLS_AVAILABLE",
}
BOOTSTRAP_SAMPLES = 10_000
SIGN_FLIP_SAMPLES = 100_000
ANALYSIS_SEED = 20260815


def _object(path: Path) -> JSON:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _rows(path: Path) -> list[JSON]:
    if not path.exists():
        return []
    values = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    if not all(isinstance(value, dict) for value in values):
        raise ValueError(f"non-object JSONL row: {path}")
    return values


def _baseline_entries() -> list[JSON]:
    entries = [
        {
            "path": str(path.relative_to(ROOT)),
            "sha256": _sha(path),
            "rows": len(_rows(path)),
        }
        for path in sorted(BASE_ROWS.glob("*.jsonl"))
    ]
    if len(entries) != 12 or sum(int(item["rows"]) for item in entries) != 384:
        raise ValueError("completed scoped ToolSandbox panel is not 12 x 32")
    return entries


def _source_scope_hash() -> tuple[str, int]:
    roots = (
        anchor.UPSTREAM / "tool_sandbox",
        anchor.UPSTREAM / "pyproject.toml",
        anchor.UPSTREAM / "README.md",
        anchor.UPSTREAM / "LICENSE",
        anchor.UPSTREAM / "ACKNOWLEDGEMENTS",
    )
    files = sorted(
        path
        for root in roots
        for path in (root.rglob("*") if root.is_dir() else (root,))
        if path.is_file() and "__pycache__" not in path.parts
    )
    return stable_hash(
        [
            {"path": str(path.relative_to(anchor.UPSTREAM)), "sha256": _sha(path)}
            for path in files
        ]
    ), len(files)


def _validate_base_plan(plan: Mapping[str, object]) -> None:
    unhashed = dict(plan)
    supplied_hash = unhashed.pop("plan_hash", None)
    if supplied_hash != stable_hash(unhashed):
        raise ValueError("base external-validation plan hash mismatch")
    if plan.get("adapter_sha256") != _sha(Path(external.__file__).resolve()):
        raise ValueError("base external-validation adapter changed")
    if plan.get("base_adapter_sha256") != _sha(Path(anchor.__file__).resolve()):
        raise ValueError("ToolSandbox adapter changed")
    integrity = _object(SOURCE_INTEGRITY)
    scope_hash, scope_files = _source_scope_hash()
    if not (
        integrity.get("source_scope_matches_pinned_archive") is True
        and integrity.get("commit") == anchor.UPSTREAM_COMMIT
        and integrity.get("source_scope_hash") == scope_hash
        and integrity.get("source_scope_files") == scope_files
    ):
        raise ValueError("pinned ToolSandbox executable source integrity failed")
    selection_path = ROOT / str(plan["selection_manifest_path"])
    if plan.get("selection_manifest_hash") != stable_hash(_object(selection_path)):
        raise ValueError("base external-validation selection changed")
    for prefix in ("protocol", "model_panel", "eligible_panel"):
        path = ROOT / str(plan[f"{prefix}_path"])
        if plan.get(f"{prefix}_sha256") != _sha(path):
            raise ValueError(f"base external-validation {prefix} changed")
    amendments = plan.get("transport_amendments")
    if not isinstance(amendments, list) or len(amendments) != 4:
        raise ValueError("base transport amendments are incomplete")
    for amendment in amendments:
        path = ROOT / str(amendment["path"])
        if amendment.get("sha256") != _sha(path):
            raise ValueError("base transport amendment changed")
    diagnostic = plan.get("usage_diagnostic")
    if not isinstance(diagnostic, dict):
        raise ValueError("base usage diagnostic is missing")
    if diagnostic.get("sha256") != _sha(ROOT / str(diagnostic["path"])):
        raise ValueError("base usage diagnostic changed")
    conditions = plan.get("conditions")
    configurations = plan.get("configurations")
    if not isinstance(conditions, list) or len(conditions) != 384:
        raise ValueError("base external-validation conditions are incomplete")
    if not isinstance(configurations, list) or len(configurations) != 12:
        raise ValueError("base external-validation configurations are incomplete")


def _context_signature(context: Any) -> str:
    payload = context.to_dict(serialize_console=False)
    payload.pop("tool_allow_list", None)
    payload.pop("preferred_tool_backend", None)
    return stable_hash(payload)


def construct_audit(base_plan: Mapping[str, object]) -> JSON:
    _validate_base_plan(base_plan)
    selection = _object(ROOT / str(base_plan["selection_manifest_path"]))
    catalog = anchor._scenario_catalog()
    checks: list[JSON] = []
    for selected in selection["scenarios"]:
        base_id = str(selected["scenario_id"])
        base = catalog[base_id]
        base_tools = list(base.starting_context.tool_allow_list or [])
        base_categories = {str(value) for value in base.categories}
        for arm, suffix in ARMS.items():
            variant = catalog[base_id + suffix]
            variant_tools = list(variant.starting_context.tool_allow_list or [])
            variant_categories = {str(value) for value in variant.categories}
            category_delta = variant_categories - base_categories
            checks.append(
                {
                    "base_scenario_id": base_id,
                    "scenario_id": base_id + suffix,
                    "menu_arm": arm,
                    "visible_tools": len(variant_tools),
                    "base_tools_nested": set(base_tools) <= set(variant_tools),
                    "tool_names_unique": len(variant_tools) == len(set(variant_tools)),
                    "context_except_menu_identical": _context_signature(base.starting_context)
                    == _context_signature(variant.starting_context),
                    "evaluation_identical": dill.dumps(base.evaluation) == dill.dumps(variant.evaluation),
                    "max_messages_identical": base.max_messages == variant.max_messages,
                    "category_delta_exact": category_delta == {ARM_CATEGORIES[arm]},
                    "category_base_preserved": {
                        value for value in base_categories if value not in MENU_CATEGORIES
                    }
                    == {value for value in variant_categories if value not in MENU_CATEGORIES},
                }
            )
    passed = len(checks) == 64 and all(
        all(value is True for key, value in item.items() if key.endswith(("identical", "nested", "unique", "exact", "preserved")))
        for item in checks
    )
    payload: JSON = {
        "schema": "toolsandbox-menu-construct-audit-v1",
        "passed": passed,
        "upstream_commit": anchor.UPSTREAM_COMMIT,
        "scenarios": 32,
        "scenario_arm_pairs": len(checks),
        "visible_tool_ranges": {
            arm: {
                "minimum": min(int(item["visible_tools"]) for item in checks if item["menu_arm"] == arm),
                "maximum": max(int(item["visible_tools"]) for item in checks if item["menu_arm"] == arm),
            }
            for arm in ARMS
        },
        "checks": checks,
    }
    payload["artifact_hash"] = stable_hash(payload)
    if not passed:
        failures = [
            {
                "scenario_id": item["scenario_id"],
                "checks": {
                    key: value
                    for key, value in item.items()
                    if key.endswith(("identical", "nested", "unique", "exact", "preserved"))
                    and value is not True
                },
            }
            for item in checks
            if any(
                value is not True
                for key, value in item.items()
                if key.endswith(("identical", "nested", "unique", "exact", "preserved"))
            )
        ]
        raise RuntimeError(f"ToolSandbox menu construct audit failed: {failures[:3]}")
    return payload


def build_plan(*, frozen_at: str) -> JSON:
    base_plan = _object(BASE_PLAN)
    audit = construct_audit(base_plan)
    visible = {
        (str(item["base_scenario_id"]), str(item["menu_arm"])): int(item["visible_tools"])
        for item in audit["checks"]
    }
    conditions: list[JSON] = []
    for base in base_plan["conditions"]:
        for arm, suffix in ARMS.items():
            identity = {
                "design": "toolsandbox-menu-intervention-v1",
                "model": str(base["model"]),
                "base_scenario_id": str(base["scenario_id"]),
                "scenario_id": str(base["scenario_id"]) + suffix,
                "menu_arm": arm,
                "upstream_commit": anchor.UPSTREAM_COMMIT,
            }
            conditions.append(
                {
                    **identity,
                    "condition_id": stable_hash(identity),
                    "base_condition_id": str(base["condition_id"]),
                    "stratum": str(base["stratum"]),
                    "visible_tools": visible[(str(base["scenario_id"]), arm)],
                    "inference_seed": int(base["inference_seed"]),
                }
            )
    payload: JSON = {
        "schema": "toolsandbox-menu-intervention-plan-v1",
        "frozen_at": frozen_at,
        "scientific_role": "prospective matched external mechanism transfer",
        "confirmatory_weight": 0,
        "protocol_path": str(PROTOCOL.relative_to(ROOT)),
        "protocol_sha256": _sha(PROTOCOL),
        "budget_path": str(BUDGET.relative_to(ROOT)),
        "budget_sha256": _sha(BUDGET),
        "adapter_path": str(Path(__file__).relative_to(ROOT)),
        "adapter_sha256": _sha(Path(__file__)),
        "base_adapter_path": str(Path(anchor.__file__).resolve().relative_to(ROOT)),
        "base_adapter_sha256": _sha(Path(anchor.__file__).resolve()),
        "external_adapter_path": str(Path(external.__file__).resolve().relative_to(ROOT)),
        "external_adapter_sha256": _sha(Path(external.__file__).resolve()),
        "base_plan_path": str(BASE_PLAN.relative_to(ROOT)),
        "base_plan_sha256": _sha(BASE_PLAN),
        "base_plan_hash": str(base_plan["plan_hash"]),
        "selection_manifest_path": str(base_plan["selection_manifest_path"]),
        "selection_manifest_sha256": _sha(ROOT / str(base_plan["selection_manifest_path"])),
        "source_integrity_path": str(SOURCE_INTEGRITY.relative_to(ROOT)),
        "source_integrity_sha256": _sha(SOURCE_INTEGRITY),
        "baseline_rows": _baseline_entries(),
        "construct_audit_hash": str(audit["artifact_hash"]),
        "upstream": dict(base_plan["upstream"]),
        "execution_policy": dict(base_plan["execution_policy"]),
        "configurations": list(base_plan["configurations"]),
        "arms": {"scoped": "immutable completed input", **ARMS},
        "conditions": conditions,
        "expected_new_rows": 768,
        "scoring": "official ToolSandbox milestone and minefield evaluator; no LLM judge",
        "sampling": "one new trajectory per model-scenario-arm; matched frozen seed when supported",
    }
    payload["plan_hash"] = stable_hash(payload)
    if len(conditions) != 768 or len({item["condition_id"] for item in conditions}) != 768:
        raise ValueError("menu intervention must contain 768 unique new conditions")
    return payload


def validate_plan(plan: Mapping[str, object]) -> None:
    unhashed = dict(plan)
    supplied_hash = unhashed.pop("plan_hash", None)
    if supplied_hash != stable_hash(unhashed):
        raise ValueError("menu intervention plan hash mismatch")
    for prefix, current in (
        ("adapter", Path(__file__)),
        ("base_adapter", Path(anchor.__file__).resolve()),
        ("external_adapter", Path(external.__file__).resolve()),
    ):
        if plan.get(f"{prefix}_sha256") != _sha(current):
            raise ValueError(f"{prefix} changed after menu plan freeze")
    for prefix in ("protocol", "budget", "base_plan", "selection_manifest", "source_integrity"):
        path = ROOT / str(plan[f"{prefix}_path"])
        if plan.get(f"{prefix}_sha256") != _sha(path):
            raise ValueError(f"{prefix} changed after menu plan freeze")
    base_plan = _object(ROOT / str(plan["base_plan_path"]))
    _validate_base_plan(base_plan)
    if plan.get("base_plan_hash") != base_plan.get("plan_hash"):
        raise ValueError("base plan binding changed")
    if plan.get("upstream", {}).get("commit") != anchor.UPSTREAM_COMMIT:
        raise ValueError("ToolSandbox pinned commit changed")
    if plan.get("baseline_rows") != _baseline_entries():
        raise ValueError("scoped ToolSandbox rows changed after menu plan freeze")
    audit = construct_audit(base_plan)
    if plan.get("construct_audit_hash") != audit.get("artifact_hash"):
        raise ValueError("menu construct changed after plan freeze")
    conditions = plan.get("conditions")
    configurations = plan.get("configurations")
    if not isinstance(conditions, list) or len(conditions) != 768:
        raise ValueError("menu condition set is incomplete")
    if not isinstance(configurations, list) or len(configurations) != 12:
        raise ValueError("menu model panel is incomplete")


def _google_new_spend(output_root: Path) -> float:
    prices = _object(GOOGLE_PRICES)["models"]
    total = 0.0
    for path in output_root.glob("*.jsonl"):
        for row in _rows(path):
            price = prices.get(row.get("model"))
            usage = row.get("usage")
            if not isinstance(price, dict) or not isinstance(usage, dict):
                continue
            total += (
                float(usage.get("prompt_tokens", 0)) * float(price["input"])
                + float(usage.get("completion_tokens", 0)) * float(price["output"])
            ) / 1_000_000
    return total


def _budget_check(configuration: Mapping[str, object], output_root: Path) -> JSON:
    budget = _object(BUDGET)
    if configuration.get("api_key_env") == "OPENROUTER_API_KEY":
        policy = budget["openrouter"]
        runner_budget = {
            "openrouter_usage_baseline_usd": policy["usage_baseline_usd"],
            "openrouter_hard_new_spend_cap_usd": policy["hard_new_spend_cap_usd"],
            "openrouter_maximum_inflight_reserve_usd": policy["maximum_inflight_reserve_usd"],
            "openrouter_minimum_cash_usd": policy["minimum_cash_usd"],
        }
        return external._prospective_runner()._openrouter_credit({"budget": runner_budget})
    policy = budget["google_cloud"]
    spend = _google_new_spend(output_root)
    if spend + float(policy["maximum_inflight_reserve_usd"]) > float(policy["hard_new_spend_cap_usd"]):
        raise RuntimeError("Google Cloud menu-intervention budget guard closed")
    return {
        "new_spend_usd": spend,
        "hard_cap_remaining_usd": float(policy["hard_new_spend_cap_usd"]) - spend,
    }


def run(args: argparse.Namespace) -> JSON:
    if not args.allow_paid:
        raise RuntimeError("paid menu intervention requires --allow-paid")
    plan = _object(args.plan)
    validate_plan(plan)
    configurations = {str(item["model"]): item for item in plan["configurations"]}
    if args.model not in configurations:
        raise ValueError(f"model is not in the menu intervention plan: {args.model}")
    configuration = configurations[args.model]
    conditions = [item for item in plan["conditions"] if item["model"] == args.model]
    if args.max_conditions is not None:
        if args.max_conditions <= 0:
            raise ValueError("max conditions must be positive")
        conditions = conditions[: args.max_conditions]
    slug = str(configuration["slug"]).removesuffix("-sentinel-routed")
    output = args.output_root / f"{slug}.jsonl"
    store = ExperimentStore(output)
    quarantine = ExperimentStore(args.output_root / "quarantine" / f"{slug}.jsonl")
    planned_ids = {str(item["condition_id"]) for item in conditions}
    for row in store.rows:
        if row.get("plan_hash") != plan["plan_hash"] or row.get("condition_id") not in planned_ids:
            raise ValueError("existing menu row is outside the frozen subset")
    client = anchor._client_for_configuration(configuration, plan["execution_policy"])
    executed = 0
    for condition in conditions:
        if condition["condition_id"] in store.completed_condition_ids:
            continue
        _budget_check(configuration, args.output_root)
        row = anchor._run_scenario(
            scenario_id=str(condition["scenario_id"]),
            condition=condition,
            configuration=configuration,
            execution_policy=plan["execution_policy"],
            plan_hash=str(plan["plan_hash"]),
            output_root=args.output_root / "trajectories" / slug,
            client=client,
        )
        row.update(
            {
                "schema": "toolsandbox-menu-intervention-row-v1",
                "base_scenario_id": condition["base_scenario_id"],
                "base_condition_id": condition["base_condition_id"],
                "menu_arm": condition["menu_arm"],
                "visible_tools": condition["visible_tools"],
                "confirmatory_weight": 0,
            }
        )
        if row["status"] == "failed_closed" and "AgnesError" in str(row.get("error")):
            quarantine.append(row)
            raise RuntimeError(f"menu-intervention transport quarantined: {row['condition_id']}")
        store.append(row)
        executed += 1
        _budget_check(configuration, args.output_root)
    return {
        "model": args.model,
        "planned": len(conditions),
        "executed": executed,
        "output": str(output),
        "budget": _budget_check(configuration, args.output_root),
    }


def _mean(values: Sequence[float]) -> float:
    # math.fsum is exact and version-stable; builtins.sum gained Neumaier
    # summation in CPython 3.12, which changed this result by ~1 ulp.
    return math.fsum(values) / len(values)


def _quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    position = probability * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _cluster_summary(values: Mapping[str, Sequence[float]], *, seed: int) -> JSON:
    scenarios = sorted(values)
    point = _mean([value for scenario in scenarios for value in values[scenario]])
    rng = random.Random(seed)
    draws = [
        _mean([value for _ in scenarios for value in values[rng.choice(scenarios)]])
        for _ in range(BOOTSTRAP_SAMPLES)
    ]
    cluster_means = [_mean(values[scenario]) for scenario in scenarios]
    flip_rng = random.Random(seed + 1)
    exceed = 0
    for _ in range(SIGN_FLIP_SAMPLES):
        statistic = _mean([value * (-1 if flip_rng.getrandbits(1) else 1) for value in cluster_means])
        exceed += abs(statistic) >= abs(_mean(cluster_means))
    return {
        "estimate": point,
        "ci95": [_quantile(draws, 0.025), _quantile(draws, 0.975)],
        "sign_flip_p": (exceed + 1) / (SIGN_FLIP_SAMPLES + 1),
        "degraded": sum(value < 0 for scenario in scenarios for value in values[scenario]),
        "tied": sum(value == 0 for scenario in scenarios for value in values[scenario]),
        "improved": sum(value > 0 for scenario in scenarios for value in values[scenario]),
    }


def _holm(raw: Mapping[str, float]) -> dict[str, float]:
    ordered = sorted(raw, key=raw.get)
    adjusted: dict[str, float] = {}
    running = 0.0
    count = len(ordered)
    for rank, name in enumerate(ordered):
        running = max(running, min(1.0, (count - rank) * raw[name]))
        adjusted[name] = running
    return adjusted


def _value(row: Mapping[str, object], metric: str) -> float:
    if metric == "full_success":
        return float(row["score"]["similarity"] == 1.0)
    if metric in {"similarity", "milestone_similarity", "minefield_similarity"}:
        return float(row["score"][metric])
    if metric == "tokens":
        return float(row["usage"]["total_tokens"])
    if metric == "requests":
        return float(row["usage"]["requests"])
    if metric == "duration_seconds":
        return float(row[metric])
    raise KeyError(metric)


def analyze(plan: Mapping[str, object], output_root: Path) -> JSON:
    validate_plan(plan)
    baseline = [row for entry in plan["baseline_rows"] for row in _rows(ROOT / str(entry["path"]))]
    new_rows = [row for path in sorted(output_root.glob("*.jsonl")) for row in _rows(path)]
    if len(baseline) != 384 or len(new_rows) != 768:
        raise ValueError(f"menu analysis requires 384 scoped and 768 new rows, got {len(baseline)} and {len(new_rows)}")
    if any(row.get("plan_hash") != plan["plan_hash"] for row in new_rows):
        raise ValueError("menu analysis row has the wrong plan hash")
    base_by_key = {(str(row["model"]), str(row["scenario_id"])): row for row in baseline}
    new_by_key = {
        (str(row["model"]), str(row["base_scenario_id"]), str(row["menu_arm"])): row
        for row in new_rows
    }
    if len(base_by_key) != 384 or len(new_by_key) != 768:
        raise ValueError("menu analysis contains duplicate matched cells")
    models = sorted({key[0] for key in base_by_key})
    scenarios = sorted({key[1] for key in base_by_key})
    strata = {
        str(condition["base_scenario_id"]): str(condition["stratum"])
        for condition in plan["conditions"]
    }
    metrics: JSON = {}
    for metric in ("similarity", "full_success", "milestone_similarity", "minefield_similarity", "tokens", "requests", "duration_seconds"):
        arm_reports: JSON = {}
        for arm_index, arm in enumerate(ARMS):
            by_scenario: dict[str, list[float]] = defaultdict(list)
            by_model: dict[str, list[float]] = defaultdict(list)
            by_stratum: dict[str, list[float]] = defaultdict(list)
            for model in models:
                for scenario in scenarios:
                    difference = _value(new_by_key[(model, scenario, arm)], metric) - _value(base_by_key[(model, scenario)], metric)
                    by_scenario[scenario].append(difference)
                    by_model[model].append(difference)
                    by_stratum[strata[scenario]].append(difference)
            report = _cluster_summary(by_scenario, seed=ANALYSIS_SEED + 100 * arm_index + len(metric))
            report["by_model"] = {
                model: {
                    "estimate": _mean(values),
                    "degraded": sum(value < 0 for value in values),
                    "tied": sum(value == 0 for value in values),
                    "improved": sum(value > 0 for value in values),
                }
                for model, values in sorted(by_model.items())
            }
            report["by_stratum"] = {name: _mean(values) for name, values in sorted(by_stratum.items())}
            arm_reports[arm] = report
        metrics[metric] = arm_reports
    raw_p = {arm: float(metrics["similarity"][arm]["sign_flip_p"]) for arm in ARMS}
    adjusted = _holm(raw_p)
    for arm in ARMS:
        metrics["similarity"][arm]["holm_p"] = adjusted[arm]
    ten = metrics["similarity"]["ten_distractors"]
    all_tools = metrics["similarity"]["all_tools"]
    payload: JSON = {
        "analysis": "toolsandbox-menu-intervention-v1",
        "plan_hash": plan["plan_hash"],
        "rows": {"scoped_immutable": 384, "new": 768, "total": 1152},
        "models": len(models),
        "scenarios": len(scenarios),
        "menu_pressure_supported": float(all_tools["estimate"]) < 0
        and float(all_tools["ci95"][1]) < 0
        and float(all_tools["holm_p"]) < 0.05,
        "graded_response_supported": float(all_tools["estimate"]) < float(ten["estimate"]) < 0,
        "metrics": metrics,
        "provider_failures": sum("AgnesError" in str(row.get("error")) for row in new_rows),
        "failed_closed_rows": sum(row.get("status") != "complete" for row in new_rows),
        "inference": "scenario-cluster bootstrap and sign-flip; models treated as the fixed eligible panel",
        "claim_boundary": "matched mechanism transfer at 2-34 visible ToolSandbox tools; scoped menu is an authored-scope upper bound",
    }
    payload["artifact_hash"] = stable_hash(payload)
    return payload


def self_check() -> JSON:
    synthetic = {f"s{index}": [-1.0, -1.0] for index in range(4)}
    summary = _cluster_summary(synthetic, seed=1)
    if summary["estimate"] != -1.0 or summary["ci95"] != [-1.0, -1.0]:
        raise AssertionError("cluster summary self-check failed")
    audit = construct_audit(_object(BASE_PLAN))
    return {
        "passed": audit["passed"] is True,
        "scenario_arm_pairs": audit["scenario_arm_pairs"],
        "visible_tool_ranges": audit["visible_tool_ranges"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("self-check")
    freeze = commands.add_parser("freeze")
    freeze.add_argument("--frozen-at", required=True)
    freeze.add_argument("--plan-output", type=Path, default=PLAN)
    freeze.add_argument("--audit-output", type=Path, default=AUDIT)
    check = commands.add_parser("check")
    check.add_argument("--plan", type=Path, default=PLAN)
    execute = commands.add_parser("run")
    execute.add_argument("--plan", type=Path, default=PLAN)
    execute.add_argument("--model", required=True)
    execute.add_argument("--output-root", type=Path, default=OUTPUT)
    execute.add_argument("--max-conditions", type=int)
    execute.add_argument("--allow-paid", action="store_true")
    analysis = commands.add_parser("analyze")
    analysis.add_argument("--plan", type=Path, default=PLAN)
    analysis.add_argument("--output-root", type=Path, default=OUTPUT)
    analysis.add_argument("--analysis-output", type=Path, default=ANALYSIS)
    args = parser.parse_args()
    if args.command == "self-check":
        result = self_check()
    elif args.command == "freeze":
        if args.plan_output.exists() or args.audit_output.exists():
            raise RuntimeError("refusing to overwrite frozen menu-intervention artifacts")
        audit = construct_audit(_object(BASE_PLAN))
        plan = build_plan(frozen_at=args.frozen_at)
        args.plan_output.parent.mkdir(parents=True, exist_ok=True)
        args.audit_output.parent.mkdir(parents=True, exist_ok=True)
        args.audit_output.write_text(canonical_json(audit) + "\n", encoding="utf-8")
        args.plan_output.write_text(canonical_json(plan) + "\n", encoding="utf-8")
        validate_plan(plan)
        result = {"conditions": len(plan["conditions"]), "plan_hash": plan["plan_hash"], "audit_passed": True}
    elif args.command == "check":
        plan = _object(args.plan)
        validate_plan(plan)
        result = {"passed": True, "plan_hash": plan["plan_hash"]}
    elif args.command == "run":
        result = run(args)
    else:
        plan = _object(args.plan)
        result = analyze(plan, args.output_root)
        args.analysis_output.parent.mkdir(parents=True, exist_ok=True)
        args.analysis_output.write_text(canonical_json(result) + "\n", encoding="utf-8")
    print(canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
