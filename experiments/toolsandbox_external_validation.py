#!/usr/bin/env python3
"""Freeze and run a stratified ToolSandbox external-validation panel."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import importlib.util
from inspect import getmodule
import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

import toolsandbox_anchor as anchor
from bottleneckbench.core.experiments import ExperimentStore
from bottleneckbench.core.schema import JSON, canonical_json, stable_hash


SELECTION_SEED = 20260814
PER_STRATUM = 8
STRATA: tuple[tuple[str, str], ...] = (
    ("state_dependency", "STATE_DEPENDENCY"),
    ("insufficient_information", "INSUFFICIENT_INFORMATION"),
    ("canonicalization", "CANONICALIZATION"),
    ("multi_tool", "MULTIPLE_TOOL_CALL"),
)
def _object(path: Path) -> JSON:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _selection_key(stratum: str, scenario_id: str) -> str:
    return sha256(f"{SELECTION_SEED}:{stratum}:{scenario_id}".encode()).hexdigest()


def _eligible_scenarios() -> list[JSON]:
    from tool_sandbox.common.execution_context import DatabaseNamespace
    from tool_sandbox.common.tool_discovery import ToolBackend, get_all_tools

    catalog = anchor._scenario_catalog()
    all_tools = get_all_tools(ToolBackend.DEFAULT)
    eligible: list[JSON] = []
    for scenario_id, scenario in catalog.items():
        categories = sorted(str(value) for value in scenario.categories)
        category_set = set(categories)
        tools = list(scenario.starting_context.tool_allow_list or [])
        modules = sorted(
            {
                str(getmodule(all_tools[tool]).__name__)
                for tool in tools
                if tool in all_tools and getmodule(all_tools[tool]) is not None
            }
        )
        rows = scenario.starting_context.get_database(
            DatabaseNamespace.SANDBOX,
            get_all_history_snapshots=True,
            drop_sandbox_message_index=False,
        ).to_dicts()
        prompts = [
            str(row["content"])
            for row in rows
            if str(row["sender"]) == "USER"
            and str(row["recipient"]) == "AGENT"
            and (
                row.get("visible_to") is None
                or any(str(role) == "AGENT" for role in row["visible_to"])
            )
        ]
        if (
            len(prompts) != 1
            or "NO_DISTRACTION_TOOLS" not in category_set
            or "MULTIPLE_USER_TURN" in category_set
            or any(module.endswith("rapid_api_search_tools") for module in modules)
        ):
            continue
        eligible.append(
            {
                "scenario_id": str(scenario_id),
                "categories": categories,
                "tool_allow_list": tools,
                "tool_modules": modules,
                "initial_user_request": prompts[0],
                "max_messages": int(scenario.max_messages),
                "milestones": len(scenario.evaluation.milestone_matcher.milestones),
                "minefields": len(scenario.evaluation.minefield_matcher.milestones),
            }
        )
    return eligible


def build_selection_manifest() -> JSON:
    eligible = _eligible_scenarios()
    selected_names: set[str] = set()
    selected_prompts: set[str] = set()
    selected: list[JSON] = []
    pool_counts: dict[str, int] = {}
    for stratum, category in STRATA:
        pool = [item for item in eligible if category in item["categories"]]
        pool_counts[stratum] = len(pool)
        candidates = sorted(
            (
                item
                for item in pool
                if item["scenario_id"] not in selected_names
                and item["initial_user_request"] not in selected_prompts
            ),
            key=lambda item: _selection_key(stratum, str(item["scenario_id"])),
        )
        chosen: list[JSON] = []
        local_prompts: set[str] = set()
        for item in candidates:
            prompt = str(item["initial_user_request"])
            if prompt in local_prompts:
                continue
            chosen.append({**item, "stratum": stratum})
            local_prompts.add(prompt)
            if len(chosen) == PER_STRATUM:
                break
        if len(chosen) != PER_STRATUM:
            raise ValueError(f"external-validation stratum is undersized: {stratum}")
        selected.extend(chosen)
        selected_names.update(str(item["scenario_id"]) for item in chosen)
        selected_prompts.update(str(item["initial_user_request"]) for item in chosen)
    if len(selected) != 32 or len(selected_names) != 32 or len(selected_prompts) != 32:
        raise ValueError("external-validation selection must have 32 unique scenarios and prompts")
    return {
        "schema": "toolsandbox-external-validation-selection-v1",
        "selection_seed": SELECTION_SEED,
        "selection_basis": "upstream task metadata only; no model outcomes",
        "selection_rule": (
            "Filter static single-request no-distraction scenarios without RapidAPI; "
            "visit strata from rarest to broadest; order each overlapping category pool "
            "by SHA-256(seed:stratum:scenario_id); take eight unused scenario IDs with "
            "distinct initial requests."
        ),
        "sampling_frame": {
            "eligible_scenarios": len(eligible),
            "unique_initial_requests": len({str(item["initial_user_request"]) for item in eligible}),
            "overlapping_category_pool_counts": pool_counts,
        },
        "upstream_repository": anchor.UPSTREAM_REPOSITORY,
        "upstream_commit": anchor.UPSTREAM_COMMIT,
        "upstream_tree_hash": anchor._tree_hash(anchor.UPSTREAM),
        "public_task_contamination_risk": "present; external validation only, not a held-out claim",
        "official_environment_and_evaluator_unchanged": True,
        "scenarios": selected,
    }


def _active_configurations(
    panel: Mapping[str, object],
    eligible_panel: Mapping[str, object],
    diagnostic: Mapping[str, object],
) -> list[JSON]:
    raw = panel.get("configurations")
    if not isinstance(raw, list):
        raise ValueError("external-validation panel has no configurations")
    eligible = {str(model) for model in eligible_panel.get("auxiliary_models", [])}
    if len(eligible) != 12:
        raise ValueError("eligible panel must name 12 auxiliary configurations")
    selected = [
        dict(item)
        for item in raw
        if isinstance(item, dict)
        and item.get("study_role") == "primary"
        and str(item.get("model")) in eligible
    ]
    if len(selected) != 12 or len({str(item["model"]) for item in selected}) != 12:
        raise ValueError("external validation requires 12 active primary configurations")
    diagnostic_model = str(diagnostic.get("model"))
    if not (
        diagnostic.get("audit") == "usage-missing-condition-diagnostic-v1"
        and diagnostic.get("confirmatory_weight") == 0
        and diagnostic.get("eligible_for_transport_wide_telemetry_amendment") is True
        and diagnostic.get("task_outcome_redacted") is True
        and diagnostic_model == "google/gemini-3.6-flash"
    ):
        raise ValueError("optional-usage diagnostic is not eligible for external validation")
    for item in selected:
        if item["model"] == diagnostic_model:
            item["require_usage"] = False
            item["usage_telemetry_amendment"] = "response-level usage may be missing; task scoring is unchanged"
    return sorted(selected, key=lambda item: str(item["model"]))


def build_plan(
    *,
    frozen_at: str,
    protocol_path: Path,
    panel_path: Path,
    eligible_panel_path: Path,
    amendment_paths: list[Path],
    diagnostic_path: Path,
    selection_path: Path,
) -> JSON:
    panel = _object(panel_path)
    eligible_panel = _object(eligible_panel_path)
    diagnostic = _object(diagnostic_path)
    manifest = build_selection_manifest()
    configurations = _active_configurations(panel, eligible_panel, diagnostic)
    conditions: list[JSON] = []
    for configuration in configurations:
        model = str(configuration["model"])
        for scenario in manifest["scenarios"]:
            identity = {
                "design": "toolsandbox-external-validation-v1",
                "model": model,
                "scenario_id": scenario["scenario_id"],
                "upstream_commit": anchor.UPSTREAM_COMMIT,
            }
            conditions.append(
                {
                    **identity,
                    "condition_id": stable_hash(identity),
                    "stratum": scenario["stratum"],
                    "inference_seed": int(
                        stable_hash({**identity, "sampling": 1})[:8], 16
                    )
                    & 0x7FFFFFFF,
                }
            )
    payload: JSON = {
        "schema": "toolsandbox-external-validation-plan-v1",
        "frozen_at": frozen_at,
        "confirmatory_weight": 0,
        "scientific_role": "external ecological agreement and model-ranking sensitivity",
        "adapter_path": str(Path(__file__).relative_to(ROOT)),
        "adapter_sha256": _sha256(Path(__file__)),
        "base_adapter_path": str(Path(anchor.__file__).resolve().relative_to(ROOT)),
        "base_adapter_sha256": _sha256(Path(anchor.__file__).resolve()),
        "selection_manifest_path": str(selection_path.relative_to(ROOT)),
        "selection_manifest_hash": stable_hash(manifest),
        "protocol_path": str(protocol_path.relative_to(ROOT)),
        "protocol_sha256": _sha256(protocol_path),
        "model_panel_path": str(panel_path.relative_to(ROOT)),
        "model_panel_sha256": _sha256(panel_path),
        "eligible_panel_path": str(eligible_panel_path.relative_to(ROOT)),
        "eligible_panel_sha256": _sha256(eligible_panel_path),
        "transport_amendments": [
            {"path": str(path.relative_to(ROOT)), "sha256": _sha256(path)}
            for path in amendment_paths
        ],
        "usage_diagnostic": {
            "path": str(diagnostic_path.relative_to(ROOT)),
            "sha256": _sha256(diagnostic_path),
            "model": diagnostic["model"],
        },
        "upstream": {
            "repository": anchor.UPSTREAM_REPOSITORY,
            "commit": anchor.UPSTREAM_COMMIT,
            "snapshot_path": str(anchor.UPSTREAM.relative_to(ROOT)),
            "tree_hash": manifest["upstream_tree_hash"],
        },
        "execution_policy": dict(panel["execution_policy"]),
        "configurations": configurations,
        "conditions": conditions,
        "scoring": "official ToolSandbox milestone and minefield evaluator; no LLM judge",
        "sampling": "one trajectory per model-scenario condition; temperature 0 and frozen seed when supported",
        "inference_seed_domain": "signed 31-bit integer for cross-provider compatibility",
    }
    payload["plan_hash"] = stable_hash(payload)
    if len(conditions) != 384 or len({item["condition_id"] for item in conditions}) != 384:
        raise ValueError("external-validation plan must contain 384 unique conditions")
    return payload


def validate_plan(plan: Mapping[str, object]) -> None:
    unhashed = dict(plan)
    plan_hash = unhashed.pop("plan_hash", None)
    if plan_hash != stable_hash(unhashed):
        raise ValueError("external-validation plan hash mismatch")
    if plan.get("adapter_sha256") != _sha256(Path(__file__)):
        raise ValueError("external-validation adapter changed after freeze")
    if plan.get("base_adapter_sha256") != _sha256(Path(anchor.__file__).resolve()):
        raise ValueError("ToolSandbox base adapter changed after freeze")
    upstream = plan.get("upstream")
    if not isinstance(upstream, dict) or upstream.get("tree_hash") != anchor._tree_hash(anchor.UPSTREAM):
        raise ValueError("ToolSandbox source snapshot changed after freeze")
    selection_path = ROOT / str(plan["selection_manifest_path"])
    manifest = build_selection_manifest()
    if _object(selection_path) != manifest or plan.get("selection_manifest_hash") != stable_hash(manifest):
        raise ValueError("external-validation selection changed after freeze")
    for key in ("protocol", "model_panel"):
        path = ROOT / str(plan[f"{key}_path"])
        if plan.get(f"{key}_sha256") != _sha256(path):
            raise ValueError(f"external-validation {key} changed after freeze")
    eligible_path = ROOT / str(plan["eligible_panel_path"])
    if plan.get("eligible_panel_sha256") != _sha256(eligible_path):
        raise ValueError("external-validation eligible panel changed")
    amendments = plan.get("transport_amendments")
    if not isinstance(amendments, list) or len(amendments) != 4:
        raise ValueError("external-validation transport amendments are incomplete")
    for amendment in amendments:
        if not isinstance(amendment, dict):
            raise ValueError("invalid external-validation amendment record")
        path = ROOT / str(amendment["path"])
        if amendment.get("sha256") != _sha256(path):
            raise ValueError("external-validation transport amendment changed")
    diagnostic = plan.get("usage_diagnostic")
    if not isinstance(diagnostic, dict):
        raise ValueError("external-validation usage diagnostic is missing")
    diagnostic_path = ROOT / str(diagnostic["path"])
    if diagnostic.get("sha256") != _sha256(diagnostic_path):
        raise ValueError("external-validation usage diagnostic changed")
    conditions = plan.get("conditions")
    configurations = plan.get("configurations")
    if not isinstance(conditions, list) or len(conditions) != 384:
        raise ValueError("external-validation condition set is incomplete")
    if not isinstance(configurations, list) or len(configurations) != 12:
        raise ValueError("external-validation configuration set is incomplete")
    if any(
        not isinstance(item.get("inference_seed"), int)
        or isinstance(item.get("inference_seed"), bool)
        or not 0 <= int(item["inference_seed"]) <= 0x7FFFFFFF
        for item in conditions
    ):
        raise ValueError("external-validation seed is outside the signed 31-bit domain")


def _prospective_runner() -> Any:
    path = HERE / "run_prospective_experiment.py"
    spec = importlib.util.spec_from_file_location("external_validation_budget", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load prospective budget guard")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _budget_check(runner: Any, schedule: Mapping[str, object], configuration: Mapping[str, object]) -> None:
    if configuration.get("api_key_env") == "OPENROUTER_API_KEY":
        runner._openrouter_credit(schedule)
        return
    observed = runner._google_spend()
    cap = float(schedule["budget"]["google_cloud_hard_new_spend_cap_usd"])
    reserve = float(schedule["budget"]["google_external_per_condition_reserve_usd"])
    if observed + reserve > cap:
        raise RuntimeError("Google Cloud external-validation budget guard closed")


def run(args: argparse.Namespace) -> JSON:
    if not args.allow_paid:
        raise RuntimeError("paid external validation requires --allow-paid")
    plan = _object(args.plan)
    validate_plan(plan)
    schedule = _object(args.schedule)
    runner = _prospective_runner()
    runner.validate_schedule(schedule)
    configurations = {str(item["model"]): item for item in plan["configurations"]}
    if args.model not in configurations:
        raise ValueError(f"model is not in the external-validation plan: {args.model}")
    configuration = configurations[args.model]
    conditions = [item for item in plan["conditions"] if item["model"] == args.model]
    if args.max_conditions is not None:
        if args.max_conditions <= 0:
            raise ValueError("max conditions must be positive")
        conditions = conditions[: args.max_conditions]
    slug = str(configuration["slug"]).removesuffix("-sentinel-routed")
    output = args.output_root / f"{slug}.jsonl"
    quarantine = ExperimentStore(args.output_root / "quarantine" / f"{slug}.jsonl")
    store = ExperimentStore(output)
    planned_ids = {str(item["condition_id"]) for item in conditions}
    for row in store.rows:
        if row.get("plan_hash") != plan["plan_hash"] or row.get("condition_id") not in planned_ids:
            raise ValueError("existing external-validation row is outside the frozen subset")
    client = anchor._client_for_configuration(configuration, plan["execution_policy"])
    executed = 0
    for condition in conditions:
        if condition["condition_id"] in store.completed_condition_ids:
            continue
        _budget_check(runner, schedule, configuration)
        row = anchor._run_scenario(
            scenario_id=str(condition["scenario_id"]),
            condition=condition,
            configuration=configuration,
            execution_policy=plan["execution_policy"],
            plan_hash=str(plan["plan_hash"]),
            output_root=args.output_root / "trajectories" / slug,
            client=client,
        )
        row["external_validation_design"] = "stratified-32-v1"
        row["usage_telemetry_amendment"] = configuration.get("usage_telemetry_amendment")
        row["confirmatory_weight"] = 0
        if row["status"] == "failed_closed" and "AgnesError" in str(row.get("error")):
            quarantine.append(row)
            raise RuntimeError(f"external-validation transport quarantined: {row['condition_id']}")
        store.append(row)
        executed += 1
        _budget_check(runner, schedule, configuration)
    return {"model": args.model, "planned": len(conditions), "executed": executed, "output": str(output)}


def self_check() -> JSON:
    manifest = build_selection_manifest()
    if manifest["sampling_frame"]["eligible_scenarios"] != 71:
        raise AssertionError("ToolSandbox eligibility frame changed")
    if len(manifest["scenarios"]) != 32:
        raise AssertionError("external-validation selection is incomplete")
    reference = anchor.self_test()
    return {
        "passed": reference["status"] == "complete" and reference["score"]["similarity"] == 1.0,
        "eligible": manifest["sampling_frame"]["eligible_scenarios"],
        "selected": len(manifest["scenarios"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("self-check")
    freeze = commands.add_parser("freeze")
    freeze.add_argument("--frozen-at", required=True)
    freeze.add_argument("--protocol", type=Path, required=True)
    freeze.add_argument("--panel", type=Path, required=True)
    freeze.add_argument("--eligible-panel", type=Path, required=True)
    freeze.add_argument("--amendment", type=Path, action="append", required=True)
    freeze.add_argument("--usage-diagnostic", type=Path, required=True)
    freeze.add_argument("--selection-output", type=Path, required=True)
    freeze.add_argument("--plan-output", type=Path, required=True)
    check = commands.add_parser("check")
    check.add_argument("--plan", type=Path, required=True)
    execute = commands.add_parser("run")
    execute.add_argument("--plan", type=Path, required=True)
    execute.add_argument("--schedule", type=Path, required=True)
    execute.add_argument("--model", required=True)
    execute.add_argument("--max-conditions", type=int)
    execute.add_argument("--output-root", type=Path, required=True)
    execute.add_argument("--allow-paid", action="store_true")
    args = parser.parse_args()
    if args.command == "self-check":
        print(canonical_json(self_check()))
        return 0
    if args.command == "check":
        plan = _object(args.plan)
        validate_plan(plan)
        print(canonical_json({"passed": True, "plan_hash": plan["plan_hash"]}))
        return 0
    if args.command == "freeze":
        if args.plan_output.exists():
            raise RuntimeError("refusing to overwrite a frozen external-validation plan")
        manifest = build_selection_manifest()
        plan = build_plan(
            frozen_at=args.frozen_at,
            protocol_path=args.protocol.resolve(),
            panel_path=args.panel.resolve(),
            eligible_panel_path=args.eligible_panel.resolve(),
            amendment_paths=[path.resolve() for path in args.amendment],
            diagnostic_path=args.usage_diagnostic.resolve(),
            selection_path=args.selection_output.resolve(),
        )
        args.selection_output.parent.mkdir(parents=True, exist_ok=True)
        args.plan_output.parent.mkdir(parents=True, exist_ok=True)
        if args.selection_output.exists():
            if _object(args.selection_output) != manifest:
                raise RuntimeError("existing external-validation selection changed")
        else:
            args.selection_output.write_text(canonical_json(manifest) + "\n", encoding="utf-8")
        args.plan_output.write_text(canonical_json(plan) + "\n", encoding="utf-8")
        validate_plan(plan)
        print(canonical_json({"conditions": len(plan["conditions"]), "plan_hash": plan["plan_hash"]}))
        return 0
    print(canonical_json(run(args)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
