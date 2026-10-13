#!/usr/bin/env python3
"""Freeze the complete release panel and evidence manifest."""

from __future__ import annotations

import argparse
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from typing import Mapping, Sequence

from bottleneckbench.core.schema import canonical_json, stable_hash
from bottleneckbench.analysis import is_infrastructure_failure

try:
    from .build_final_analysis_manifest import _entries, _model
    from .build_frontier_release_source_map import (
        validate_archival_launch as _validate_archival_frontier_launch,
        validate_archival_source_map as _validate_archival_frontier_source_map,
        validate_source_map as _validate_frontier_source_map,
    )
except ImportError:  # Direct script execution.
    from build_final_analysis_manifest import _entries, _model
    from build_frontier_release_source_map import (
        validate_archival_launch as _validate_archival_frontier_launch,
        validate_archival_source_map as _validate_archival_frontier_source_map,
        validate_source_map as _validate_frontier_source_map,
    )


ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_MANIFEST = ROOT / "experiments/final-analysis-inputs.json"
# Frozen provenance for the twelve first-acquired configurations: display names,
# providers, access class, auxiliary set and the layer-activation set.  It is an
# input record, not an analysis stratum; the released core panel is all 20.
HISTORICAL_PANEL = ROOT / "experiments/eligible-analysis-panel.json"
CANDIDATE_PANEL = ROOT / "results/candidate-expansion/frozen-panel.json"
TOOLSANDBOX_PLAN = ROOT / "results/candidate-expansion/plans/toolsandbox-evaluation/plan.json"
STRUCTURAL_PLAN = ROOT / "results/candidate-expansion/plans/structural-evaluation/plan.json"
FRONTIER_PANEL = ROOT / "results/frontier-model-extension/frozen-panel.json"
FRONTIER_LAUNCH = ROOT / "results/frontier-model-extension/plans/manifest.json"
FRONTIER_ROOT = ROOT / "results/frontier-model-extension"
INFRASTRUCTURE_AMENDMENT = ROOT / "protocols/candidate-infrastructure-analysis-amendment-2026-09-01.md"
TARGETED_VW_GATE = (
    ROOT
    / "results/execution-governance/targeted-vw-full-panel-repeat-recovery-1-v3"
    / "sol-post-probe-budget-handoff.json"
)
TARGETED_VW_PLAN = (
    ROOT / "results/prospective-plans/targeted-vw-full-panel-repeat-recovery-1/plan.json"
)
TARGETED_VW_ANALYSIS = (
    ROOT / "results/prospective-execution/targeted-vw-full-panel-repeat-recovery-1/analysis.json"
)
RELEASE_SEED_ROOT = ROOT / "results/opus-5-acquisition/plans/release-seed-20"
RELEASE_SEED_HASHES = {
    "release-analysis-inputs.json": "7a3fb222009d0f29955035e940d682a3b6b40941797d8755ea22768fc246a5ed",
    "release-panel.json": "9d04349ec277526add827416552279cae17c40af1bdb24c3de76a1e0b48281dc",
}

CANDIDATE_METADATA = {
    "tencent/hy4-preview": ("HY4 Preview", "Tencent"),
    "qwen/qwen3.8-flash": ("Qwen3.8 Flash", "Alibaba"),
    "glm-5.3-flash": ("GLM-5.3 Flash", "Z.ai"),
    "glm-5.3": ("GLM-5.3", "Z.ai"),
    "deepseek/deepseek-v4-pro-0813": ("DeepSeek V4 Pro 0813", "DeepSeek"),
}
FRONTIER_METADATA = {
    "openai/gpt-5.6-sol": ("GPT-5.6 Sol", "OpenAI"),
    "anthropic/claude-sonnet-5": ("Claude Sonnet 5", "Anthropic"),
}
FRONTIER_PROVIDERS = {
    "openai/gpt-5.6-sol": "OpenRouter / OpenAI fast",
    "anthropic/claude-sonnet-5": "OpenRouter / Anthropic",
}


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _committed_sha(path: Path) -> str | None:
    try:
        relative = path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        return None
    completed = subprocess.run(
        ["git", "show", f"HEAD:{relative.as_posix()}"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    return sha256(completed.stdout).hexdigest() if completed.returncode == 0 else None


def _portable(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def _resolve(raw: object) -> Path:
    path = Path(str(raw))
    return path if path.is_absolute() else ROOT / path


def validate_archival_launch_manifest(manifest: Mapping[str, object]) -> None:
    """Release-only validation that never weakens the hosted runtime validator."""

    _validate_archival_frontier_launch(manifest, ROOT)


def _targeted_vw_attachment(
    gate_path: Path,
    *,
    plan_path: Path = TARGETED_VW_PLAN,
    analysis_path: Path = TARGETED_VW_ANALYSIS,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Return a complete 1,152-row attachment or a zero-row terminal record."""

    gate = _read(gate_path)
    unhashed = dict(gate)
    supplied = unhashed.pop("artifact_hash", None)
    if (
        gate.get("schema") != "bottleneckbench-sol-after-targeted-governance-gate-v2"
        or supplied != stable_hash(unhashed)
        or gate.get("passed") is not True
        or gate.get("scientific_weight") != 0
    ):
        raise ValueError("targeted VxW governance gate identity changed")
    kind = gate.get("targeted_terminal_kind")
    evidence = gate.get("targeted_evidence")
    if not isinstance(evidence, dict):
        raise ValueError("targeted VxW governance gate lacks terminal evidence")
    base_record: dict[str, object] = {
        "gate_archival_reference": _portable(gate_path),
        "gate_sha256_digest": _sha(gate_path),
        "terminal_kind": kind,
        "terminal_event_hash": gate.get("targeted_terminal_event_hash"),
        "confirmatory_weight": 0,
    }
    if kind == "bridge_closed_incomplete":
        if (
            evidence.get("status") != "incomplete"
            or evidence.get("scientific_rows") != 0
            or evidence.get("scientific_weight") != 0
        ):
            raise ValueError("closed-incomplete targeted VxW evidence changed")
        return [], {
            **base_record,
            "status": "closed-incomplete",
            "scientific_rows": 0,
            "interpretation": evidence.get("interpretation"),
        }
    if kind != "bridge_settled" or evidence.get("status") != "complete":
        raise ValueError("targeted VxW gate is not terminal")
    rows_path = _resolve(evidence.get("rows_path"))
    if evidence.get("rows") != 1152 or evidence.get("rows_sha256") != _sha(rows_path):
        raise ValueError("complete targeted VxW rows changed")
    plan = _read(plan_path)
    if plan.get("plan_hash") != stable_hash({
        key: value for key, value in plan.items() if key != "plan_hash"
    }):
        raise ValueError("targeted VxW plan hash mismatch")
    analysis = _read(analysis_path)
    analysis_unhashed = dict(analysis)
    analysis_hash = analysis_unhashed.pop("artifact_hash", None)
    if (
        analysis_hash != stable_hash(analysis_unhashed)
        or analysis.get("schema")
        != "bottleneckbench-targeted-vw-full-panel-repeat-analysis-v1"
        or analysis.get("plan_hash") != plan.get("plan_hash")
        or analysis.get("complete") is not True
        or analysis.get("rows_planned") != 1152
        or analysis.get("rows_observed") != 1152
        or analysis.get("confirmatory_status")
        not in {
            "supported",
            "not_supported_reversed",
            "not_supported_null_or_unstable",
        }
        or analysis.get("scientific_weight") != "targeted_full_panel_slice"
    ):
        raise ValueError("complete targeted VxW analysis changed")
    rows = [
        json.loads(line)
        for line in rows_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    identifiers = [str(row.get("replication_condition_id")) for row in rows]
    expected_ids = {
        str(cell["replication_condition_id"])
        for block in plan.get("schedule", [])  # type: ignore[union-attr]
        if isinstance(block, dict)
        for cell in block.get("cells", [])
        if isinstance(cell, dict)
    }
    if (
        len(rows) != 1152
        or len(set(identifiers)) != 1152
        or set(identifiers) != expected_ids
        or any(row.get("plan_hash") != plan["plan_hash"] for row in rows)
    ):
        raise ValueError("complete targeted VxW rows do not close the frozen plan")
    models = sorted({_model(row) for row in rows})
    entry = {
        "path": _portable(rows_path),
        "sha256": _sha(rows_path),
        "rows": 1152,
        "models": models,
        "identifier_field": "replication_condition_id",
        "plan_path": _portable(plan_path),
        "plan_sha256": _sha(plan_path),
        "plan_hash": plan["plan_hash"],
        "analysis_path": _portable(analysis_path),
        "analysis_sha256": _sha(analysis_path),
    }
    return [entry], {
        **base_record,
        "status": "complete",
        "scientific_rows": 1152,
        "plan_path": _portable(plan_path),
        "plan_sha256": _sha(plan_path),
        "analysis_path": _portable(analysis_path),
        "analysis_sha256": _sha(analysis_path),
    }


def _frontier_source_groups(
    launch: Mapping[str, object], execution_root: Path, source_map_path: Path | None,
    *, archival_only: bool = False,
) -> dict[str, list[dict[str, object]]]:
    if source_map_path is not None:
        validator = (
            _validate_archival_frontier_source_map
            if archival_only
            else _validate_frontier_source_map
        )
        source_map = validator(source_map_path, launch, ROOT)
        groups: dict[str, list[dict[str, object]]] = defaultdict(list)
        for raw in source_map["sources"]:  # type: ignore[index]
            groups[str(raw["block"])].append({
                "plan": _resolve(raw["plan"]),
                "rows": [_resolve(path) for path in raw["rows"]],
                "models": set(map(str, raw["models"])),
                **({"condition_ids": set(map(str, raw["condition_ids"]))} if "condition_ids" in raw else {}),
                **({"input_root": _resolve(raw["input_root"])} if "input_root" in raw else {}),
            })
        if set(groups) != {"core", "repair", "sentinel", "frontier", "structural", "toolsandbox"}:
            raise ValueError("frontier release source map lacks a required evidence block")
        return groups

    plans = list(launch["plans"])  # type: ignore[arg-type]
    groups = {}
    for block in ("core", "repair", "sentinel", "frontier"):
        groups[block] = [
            {
                "plan": ROOT / str(item["path"]),
                "rows": [execution_root / block / f"{Path(str(item['path'])).stem}.jsonl"],
                "models": {_read(ROOT / str(item["path"]))["model"]},
            }
            for item in plans
            if item["block"] == block
        ]
    structural_plan = ROOT / next(str(item["path"]) for item in plans if item["block"] == "structural")
    toolsandbox_plan = ROOT / next(str(item["path"]) for item in plans if item["block"] == "toolsandbox")
    models = set(map(str, launch["models"]))  # type: ignore[index]
    groups["structural"] = [{
        **_matrix_source(structural_plan, execution_root / "structural/active", models),
        "input_root": execution_root / "structural",
    }]
    groups["toolsandbox"] = [{
        **_matrix_source(toolsandbox_plan, execution_root / "toolsandbox", models, toolsandbox=True),
        "input_root": execution_root / "toolsandbox",
    }]
    return groups


def _planned_condition_id(condition: Mapping[str, object]) -> str:
    explicit = condition.get("condition_id")
    return str(explicit) if isinstance(explicit, str) else stable_hash(condition)


def _plan_hash(path: Path, plan: Mapping[str, object]) -> str:
    supplied = plan.get("plan_hash")
    if isinstance(supplied, str):
        return supplied
    from bottleneckbench.workflow_boundary_experiments import load_workflow_boundary_plan

    return load_workflow_boundary_plan(path).plan_hash


def validate_plan_artifact(
    path: Path, plan: Mapping[str, object], *, verify_inputs: bool = True
) -> None:
    supplied = plan.get("plan_hash")
    # A plan that declares a public-release redaction cannot self-verify: the public
    # copy differs from the file the hash was taken over.  Its rows are still bound to
    # the declared plan_hash and to the plan's own conditions by validate_plan_sources.
    if isinstance(supplied, str) and "public_release_redaction" not in plan:
        unhashed = dict(plan)
        unhashed.pop("plan_hash")
        if supplied != stable_hash(unhashed):
            raise ValueError(f"frozen plan hash mismatch: {path}")
    if not verify_inputs:
        return
    for item in plan.get("inputs", []):  # type: ignore[union-attr]
        source = ROOT / str(item["path"])
        expected = item.get("sha256")
        if expected not in {_sha(source), _committed_sha(source)}:
            raise ValueError(f"frozen plan input changed: {source}")


def validate_plan_sources(
    sources: Sequence[Mapping[str, object]], *, require_zero_weight: bool = True,
    expected_condition_ids: set[str] | None = None,
    verify_plan_inputs: bool = True,
) -> list[dict[str, object]]:
    """Bind every included row to one frozen condition across one or more plans."""

    entries: list[dict[str, object]] = []
    global_ids: set[str] = set()
    for source in sources:
        plan_path = Path(source["plan"])
        plan = _read(plan_path)
        validate_plan_artifact(plan_path, plan, verify_inputs=verify_plan_inputs)
        models = {str(model) for model in source["models"]}  # type: ignore[index]
        all_planned = {
            _planned_condition_id(condition): condition
            for condition in plan["conditions"]  # type: ignore[index]
            if str(condition.get("model")) in models
        }
        if not models or len(all_planned) != sum(
            str(condition.get("model")) in models
            for condition in plan["conditions"]  # type: ignore[index]
        ):
            raise ValueError(f"duplicate or missing frozen condition ID: {plan_path}")
        selected = source.get("condition_ids")
        selected_ids = set(map(str, selected)) if selected is not None else set(all_planned)
        if not selected_ids or not selected_ids <= set(all_planned):
            raise ValueError(f"source selects conditions outside its frozen plan: {plan_path}")
        planned = {condition_id: all_planned[condition_id] for condition_id in selected_ids}
        overlap = global_ids & set(planned)
        if overlap:
            raise ValueError(f"duplicate condition ID across frozen plans: {sorted(overlap)[0]}")
        expected_hash = _plan_hash(plan_path, plan)
        observed_ids: set[str] = set()
        for raw_path in source["rows"]:  # type: ignore[index]
            path = Path(raw_path)
            if not path.exists():
                raise ValueError(f"result file does not match its frozen plan: missing {path}")
            try:
                rows = [
                    json.loads(line)
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
            except json.JSONDecodeError as exc:
                raise ValueError(f"result file does not match its frozen plan: {path}") from exc
            if not rows:
                raise ValueError(f"result file does not match its frozen plan: empty {path}")
            row_models = {_model(row) for row in rows}
            if len(row_models) != 1 or not row_models <= models:
                raise ValueError(f"result file does not match its frozen plan: {path}")
            for row in rows:
                condition_id = row.get("condition_id")
                condition = planned.get(str(condition_id))
                if condition is None or str(condition_id) in observed_ids:
                    raise ValueError(f"result file does not match its frozen plan: {path}")
                nested = row.get("condition")
                if isinstance(nested, dict):
                    matched = nested == condition and (
                        condition.get("condition_id") == condition_id
                        if isinstance(condition.get("condition_id"), str)
                        else stable_hash(nested) == condition_id
                    )
                else:
                    matched = (
                        row.get("model") == condition.get("model")
                        and all(row.get(key) == value for key, value in condition.items() if key in row)
                    )
                if not matched or row.get("plan_hash") != expected_hash:
                    raise ValueError(f"result file does not match its frozen plan: {path}")
                if require_zero_weight and row.get("confirmatory_weight", 0) != 0:
                    raise ValueError(f"descriptive extension row has nonzero weight: {path}")
                if row.get("attempt", 1) != 1:
                    raise ValueError(f"descriptive extension row is not the first attempt: {path}")
                observed_ids.add(str(condition_id))
            entries.append({
                "path": _portable(path),
                "sha256": _sha(path),
                "rows": len(rows),
                "model": next(iter(row_models)),
                "plan_path": _portable(plan_path),
                "plan_sha256": _sha(plan_path),
                "plan_hash": expected_hash,
            })
        if observed_ids != set(planned):
            raise ValueError(f"result rows do not match its frozen plan: {plan_path}")
        global_ids |= observed_ids
    if expected_condition_ids is not None and global_ids != expected_condition_ids:
        raise ValueError("multi-plan rows do not close the frozen target condition IDs exactly once")
    return entries


def _manifest_sources(
    manifest_path: Path, execution_root: Path, models: set[str]
) -> list[dict[str, object]]:
    manifest = _read(manifest_path)
    return [
        {
            "plan": ROOT / str(item["path"]),
            "rows": [execution_root / f"{Path(str(item['path'])).stem}.jsonl"],
            "models": {str(item["model"])},
        }
        for item in manifest["plans"]  # type: ignore[index]
        if str(item["model"]) in models
    ]


def _matrix_source(
    plan_path: Path, execution_root: Path, models: set[str], *, toolsandbox: bool = False
) -> dict[str, object]:
    plan = _read(plan_path)
    slugs = {
        str(item["model"]): str(item["slug"]).removesuffix("-primary")
        for item in plan_configurations(plan)
        if str(item["model"]) in models
    }
    suffix = "" if toolsandbox else "-structural"
    return {
        "plan": plan_path,
        "rows": [execution_root / f"{slugs[model]}{suffix if not slugs[model].endswith(suffix) else ''}.jsonl" for model in sorted(models)],
        "models": models,
    }


def plan_configurations(plan: Mapping[str, object]) -> list[dict[str, object]]:
    """Read plural matrix plans or an explicitly frozen single-configuration plan."""
    many, one = plan.get("configurations"), plan.get("configuration")
    if isinstance(many, list) and one is None and all(isinstance(item, dict) for item in many):
        return many
    if isinstance(one, dict) and many is None:
        return [one]
    raise ValueError("plan must declare one unambiguous configuration inventory")


def _assert_globally_unique(inputs: Mapping[str, Sequence[Mapping[str, object]]]) -> None:
    seen: set[tuple[str, str]] = set()
    for entries in inputs.values():
        for entry in entries:
            path = ROOT / str(entry["path"])
            field = str(entry.get("identifier_field", "condition_id"))
            for line in path.read_text(encoding="utf-8").splitlines():
                condition_id = str(json.loads(line).get(field))
                key = (field, condition_id)
                if condition_id == "None" or key in seen:
                    raise ValueError(
                        f"release evidence ledger has duplicate {field}: {condition_id}"
                    )
                seen.add(key)


def _assert_model_rows(
    entries: Sequence[Mapping[str, object]],
    models: set[str],
    rows_per_model: int,
    block: str,
) -> None:
    observed: dict[str, int] = defaultdict(int)
    for entry in entries:
        observed[str(entry["model"])] += int(entry["rows"])
    expected = {model: rows_per_model for model in models}
    if observed != expected:
        raise ValueError(
            f"{block} is not a complete per-configuration block: "
            f"observed={dict(sorted(observed.items()))}, expected={dict(sorted(expected.items()))}"
        )


def _infrastructure_ids(entries: list[dict[str, object]]) -> list[str]:
    observed = []
    for item in entries:
        path = ROOT / str(item["path"])
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if is_infrastructure_failure(row):
                observed.append(str(row["condition_id"]))
    return sorted(observed)


def _for_models(
    entries: list[dict[str, object]], models: set[str]
) -> list[dict[str, object]]:
    return [item for item in entries if str(item["model"]) in models]


def _toolsandbox_infrastructure_failure(row: dict[str, object]) -> bool:
    error = str(row.get("error", ""))
    return (
        row.get("status") == "failed_closed"
        and "AgnesError" in error
        and "model response contract" not in error
    )


def _candidate_block_paths(roots: Path, slug: str, block: str) -> list[Path]:
    if block == "repair":
        return sorted((roots / block).glob(f"{slug}-*.jsonl"))
    if block == "structural_additional":
        return [roots / "structural" / "active" / f"{slug}-structural.jsonl"]
    suffix = {"sentinel": "primary-sentinel", "workflow_frontier": "workflow-frontier"}[block]
    return [roots / ("frontier" if block == "workflow_frontier" else block) / f"{slug}-{suffix}.jsonl"]


def _complete_candidate_block_models(
    roots: Path,
    configurations: list[dict[str, object]],
    block: str,
    expected_rows: int,
) -> tuple[set[str], list[dict[str, object]]]:
    complete: set[str] = set()
    excluded: list[dict[str, object]] = []
    for configuration in configurations:
        model = str(configuration["model"])
        slug = str(configuration["slug"]).removesuffix("-primary")
        paths = _candidate_block_paths(roots, slug, block)
        rows = [
            json.loads(line)
            for path in paths
            if path.exists()
            for line in path.read_text(encoding="utf-8").splitlines()
        ]
        rows = [
            row
            for row in rows
            if str(row.get("model") or row.get("condition", {}).get("model")) == model  # type: ignore[union-attr]
        ]
        infrastructure = [row for row in rows if is_infrastructure_failure(row)]
        ids = [str(row.get("condition_id")) for row in rows]
        if len(rows) == expected_rows and len(set(ids)) == expected_rows and not infrastructure:
            complete.add(model)
            continue
        excluded.append(
            {
                "block": f"candidate_{block}",
                "model": model,
                "paths": [str(path.relative_to(ROOT)) for path in paths],
                "planned_rows": expected_rows,
                "observed_rows": len(rows),
                "unique_condition_ids": len(set(ids)),
                "infrastructure_rows": len(infrastructure),
                "reason": "capability block incomplete after frozen infrastructure-failure and budget policy",
            }
        )
    return complete, excluded


def _complete_candidate_menu_models(
    roots: Path, configurations: list[dict[str, object]]
) -> tuple[set[str], list[dict[str, object]]]:
    plan = _read(TOOLSANDBOX_PLAN)
    candidate_models = [str(item["model"]) for item in configurations]
    planned = {
        model: {
            str(item["condition_id"])
            for item in plan["conditions"]  # type: ignore[index]
            if str(item["model"]) == model
        }
        for model in candidate_models
    }
    slugs = {
        str(item["model"]): str(item["slug"]).removesuffix("-primary")
        for item in plan["configurations"]  # type: ignore[index]
    }
    complete: set[str] = set()
    excluded: list[dict[str, object]] = []
    for model in candidate_models:
        path = roots / "toolsandbox" / f"{slugs[model]}.jsonl"
        rows = (
            [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            if path.exists()
            else []
        )
        ids = [str(row.get("condition_id")) for row in rows]
        infrastructure = [row for row in rows if _toolsandbox_infrastructure_failure(row)]
        if len(rows) == len(planned[model]) and set(ids) == planned[model] and not infrastructure:
            complete.add(model)
            continue
        excluded.append(
            {
                "block": "candidate_menu_intervention",
                "model": model,
                "path": str(path.relative_to(ROOT)),
                "planned_rows": len(planned[model]),
                "observed_rows": len(rows),
                "unique_condition_ids": len(set(ids)),
                "infrastructure_rows": len(infrastructure),
                "reason": "capability block incomplete after frozen infrastructure-failure and budget policy",
            }
        )
    return complete, excluded


def build(
    frontier_source_map: Path | None = None,
    targeted_vw_gate: Path | None = None,
    *,
    archival_source_map_only: bool = False,
    opus_source_map: Path | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    historical_manifest = _read(HISTORICAL_MANIFEST)
    historical_panel = _read(HISTORICAL_PANEL)
    candidate_panel = _read(CANDIDATE_PANEL)
    frontier_panel = _read(FRONTIER_PANEL)
    frontier_launch = _read(FRONTIER_LAUNCH)
    if archival_source_map_only and frontier_source_map is None:
        raise ValueError("archival source-map replay requires --frontier-source-map")
    if frontier_source_map is not None:
        validate_archival_launch_manifest(frontier_launch)
    else:
        try:
            from .frontier_model_extension import validate_launch_manifest
        except ImportError:
            from frontier_model_extension import validate_launch_manifest

        validate_launch_manifest(frontier_launch)
    candidate_models = [str(item["model"]) for item in candidate_panel["configurations"]]  # type: ignore[index]
    frontier_models = [str(item["model"]) for item in frontier_panel["configurations"]]  # type: ignore[index]
    if set(candidate_models) != set(CANDIDATE_METADATA):
        raise ValueError("candidate release metadata does not match the frozen panel")
    if set(frontier_models) != set(FRONTIER_METADATA) or set(frontier_launch["models"]) != set(frontier_models):
        raise ValueError("frontier release metadata does not match the frozen panel")
    opus_groups = None
    if opus_source_map is not None:
        from build_opus_release_source_map import source_groups
        opus_groups = source_groups(opus_source_map, ROOT)
    historical_auxiliary = set(historical_panel["auxiliary_models"])  # type: ignore[arg-type]
    roots = ROOT / "results/candidate-expansion/execution"
    frontier_execution = FRONTIER_ROOT / "execution"
    candidate_configurations = [dict(item) for item in candidate_panel["configurations"]]  # type: ignore[index]
    repair_models, repair_excluded = _complete_candidate_block_models(
        roots, candidate_configurations, "repair", 144
    )
    sentinel_models, sentinel_excluded = _complete_candidate_block_models(
        roots, candidate_configurations, "sentinel", 72
    )
    candidate_frontier_models, frontier_excluded = _complete_candidate_block_models(
        roots, candidate_configurations, "workflow_frontier", 16
    )
    candidate_auxiliary_models = repair_models & sentinel_models & candidate_frontier_models
    structural_models, structural_excluded = _complete_candidate_block_models(
        roots, candidate_configurations, "structural_additional", 216
    )
    candidate_menu_models, menu_excluded = _complete_candidate_menu_models(
        roots, candidate_configurations
    )
    excluded_blocks = [
        *repair_excluded,
        *sentinel_excluded,
        *frontier_excluded,
        *structural_excluded,
        *menu_excluded,
    ]
    historical_structural = {
        "openai/gpt-oss-120b-maas", "google/gemini-3.5-flash-lite",
        "deepseek/deepseek-v4-flash-0731", "qwen/qwen3.7-flash",
    }
    historical_menu = {
        str(json.loads(path.read_text(encoding="utf-8").splitlines()[0])["model"])
        for path in (ROOT / "results/external-menu-intervention/execution").glob("*.jsonl")
    }
    configurations = []
    for raw in historical_panel["configurations"]:  # type: ignore[index]
        item = dict(raw)
        model = str(item["model"])
        item["coverage"] = ["core"] + (["auxiliary", "external_rank"] if model in historical_auxiliary else [])
        if model in historical_structural:
            item["coverage"].append("structural_registered")
        if model in historical_menu:
            item["coverage"].append("menu_matched")
        configurations.append(item)
    for item in candidate_panel["configurations"]:  # type: ignore[index]
        model = str(item["model"])
        display, organization = CANDIDATE_METADATA[model]
        coverage = ["core"]
        if model in candidate_auxiliary_models:
            coverage.append("auxiliary")
        if model in structural_models:
            coverage.append("structural_additional")
        if model in candidate_menu_models:
            coverage.append("menu_matched")
        configurations.append({
            "model": model,
            "display_name": display,
            "organization": organization,
            "provider": str(item["provider"]),
            "access": "managed",
            "coverage": coverage,
        })
    for item in frontier_panel["configurations"]:  # type: ignore[index]
        model = str(item["model"])
        display, organization = FRONTIER_METADATA[model]
        configurations.append({
            "model": model,
            "display_name": display,
            "organization": organization,
            "provider": FRONTIER_PROVIDERS[model],
            "access": "managed",
            "coverage": ["core", "auxiliary", "structural_additional", "menu_matched"],
        })
    if opus_groups is not None:
        configurations.append({"model": "anthropic/claude-opus-5", "display_name": "Claude Opus 5",
            "organization": "Anthropic", "provider": "OpenRouter / Anthropic", "access": "managed",
            "coverage": ["core", "auxiliary", "structural_additional", "menu_matched"]})
        frontier_models.append("anthropic/claude-opus-5")
    panel_size = 20 + int(opus_groups is not None)
    models = [str(item["model"]) for item in configurations]
    if len(models) != panel_size or len(set(models)) != panel_size:
        raise ValueError(f"release panel must contain {panel_size} unique configurations")
    activation_models = {str(model) for model in historical_panel["activation_models"]}
    panel = {
        "schema": "bottleneckbench-release-analysis-panel-v1",
        "configurations": configurations,
        "activation_models": list(historical_panel["activation_models"]),
        "auxiliary_models": [
            *historical_panel["auxiliary_models"],
            *sorted(candidate_auxiliary_models),
            *sorted(frontier_models),
        ],
        "structural_registered_models": sorted(historical_structural),
        "structural_additional_models": sorted({*structural_models, *frontier_models}),
        "reporting_policy": f"unified model reporting; the {panel_size}-configuration core panel is the primary analysis panel and acquisition order appears only in provenance fields",
        "scientific_role": "reviewer-facing declaration of the released evaluation panel and its coverage classes; it fixes denominators and evidence boundaries and estimates nothing",
        "confirmatory_weight": 0,
    }
    historical_inputs = dict(historical_manifest["inputs"])  # type: ignore[arg-type]
    candidate_core_sources = _manifest_sources(
        ROOT / "results/candidate-expansion/plans/core/manifest.json",
        roots / "core",
        set(candidate_models),
    )
    candidate_core_entries = validate_plan_sources(candidate_core_sources)
    candidate_capacity = [entry for entry in candidate_core_entries if "discovery" not in str(entry["path"])]
    candidate_discovery = [entry for entry in candidate_core_entries if "discovery" in str(entry["path"])]
    candidate_repair = validate_plan_sources(_manifest_sources(
        ROOT / "results/candidate-expansion/plans/repair/manifest.json",
        roots / "repair",
        candidate_auxiliary_models,
    ))
    candidate_sentinel = validate_plan_sources(_manifest_sources(
        ROOT / "results/candidate-expansion/plans/sentinel/manifest.json",
        roots / "sentinel",
        candidate_auxiliary_models,
    ))
    candidate_frontier = validate_plan_sources(_manifest_sources(
        ROOT / "results/candidate-expansion/plans/frontier/manifest.json",
        roots / "frontier",
        candidate_auxiliary_models,
    ))
    candidate_structural = validate_plan_sources([
        _matrix_source(STRUCTURAL_PLAN, roots / "structural/active", structural_models)
    ])
    candidate_menu = validate_plan_sources([
        _matrix_source(TOOLSANDBOX_PLAN, roots / "toolsandbox", candidate_menu_models, toolsandbox=True)
    ])

    frontier_sources = _frontier_source_groups(
        frontier_launch,
        frontier_execution,
        frontier_source_map,
        archival_only=archival_source_map_only,
    )
    def validated_group(group):
        original = validate_plan_sources(frontier_sources[group])
        if opus_groups is None:
            return original
        # The Opus source map independently binds its original launch and complete
        # outputs. Its archival plans name historical inputs, not live dependencies.
        return original + validate_plan_sources(opus_groups[group], verify_plan_inputs=False)

    frontier_core_entries = validated_group("core")
    frontier_capacity = [entry for entry in frontier_core_entries if "discovery" not in str(entry["path"])]
    frontier_discovery = [entry for entry in frontier_core_entries if "discovery" in str(entry["path"])]
    frontier_repair = validated_group("repair")
    frontier_sentinel = validated_group("sentinel")
    frontier_workflow = validated_group("frontier")
    frontier_structural = validated_group("structural")
    frontier_menu = validated_group("toolsandbox")
    if opus_groups is not None:
        frontier_sources = {group: [*sources, *opus_groups[group]] for group, sources in frontier_sources.items()}
    _assert_model_rows(frontier_capacity, set(frontier_models), 576, "frontier capacity")
    _assert_model_rows(frontier_discovery, set(frontier_models), 216, "frontier discovery")
    _assert_model_rows(frontier_repair, set(frontier_models), 144, "frontier repair")
    _assert_model_rows(frontier_sentinel, set(frontier_models), 72, "frontier sentinel")
    _assert_model_rows(frontier_workflow, set(frontier_models), 16, "frontier workflow frontier")
    _assert_model_rows(frontier_structural, set(frontier_models), 216, "frontier structural auxiliary")
    _assert_model_rows(frontier_menu, set(frontier_models), 96, "frontier concurrent menu")
    targeted_entries: list[dict[str, object]] = []
    targeted_record: dict[str, object] | None = None
    if targeted_vw_gate is not None:
        targeted_entries, targeted_record = _targeted_vw_attachment(targeted_vw_gate)
    inputs = {
        **historical_inputs,
        "candidate_capacity": candidate_capacity,
        "candidate_discovery": candidate_discovery,
        "candidate_repair": candidate_repair,
        "candidate_sentinel": candidate_sentinel,
        "candidate_workflow_frontier": candidate_frontier,
        "candidate_structural_additional": candidate_structural,
        "frontier_capacity": frontier_capacity,
        "frontier_discovery": frontier_discovery,
        "frontier_repair": frontier_repair,
        "frontier_sentinel": frontier_sentinel,
        "frontier_workflow_frontier": frontier_workflow,
        "frontier_structural_additional": frontier_structural,
        "historical_menu_intervention": _entries(
            (ROOT / "results/external-menu-intervention/execution").glob("*.jsonl"), set(models)
        ),
        "candidate_menu_intervention": candidate_menu,
        "frontier_menu_intervention": frontier_menu,
        **({"targeted_vw_replication": targeted_entries} if targeted_entries else {}),
    }
    expected = {
        **historical_manifest["expected_rows"],  # type: ignore[arg-type]
        "candidate_capacity": 3456,
        "candidate_discovery": 1296,
        "candidate_repair": 144 * len(candidate_auxiliary_models),
        "candidate_sentinel": 72 * len(candidate_auxiliary_models),
        "candidate_workflow_frontier": 16 * len(candidate_auxiliary_models),
        "candidate_structural_additional": 216 * len(structural_models),
        "frontier_capacity": 576 * len(frontier_models),
        "frontier_discovery": 216 * len(frontier_models),
        "frontier_repair": 144 * len(frontier_models),
        "frontier_sentinel": 72 * len(frontier_models),
        "frontier_workflow_frontier": 16 * len(frontier_models),
        "frontier_structural_additional": 216 * len(frontier_models),
        "historical_menu_intervention": 576,
        "candidate_menu_intervention": 96 * len(candidate_menu_models),
        "frontier_menu_intervention": 96 * len(frontier_models),
        **({"targeted_vw_replication": 1152} if targeted_entries else {}),
    }
    observed = {name: sum(int(item["rows"]) for item in entries) for name, entries in inputs.items()}
    if observed != expected:
        raise ValueError(f"release analysis inputs do not close: {observed}")
    _assert_globally_unique(inputs)
    frontier_block_entries = [
        *frontier_capacity, *frontier_discovery, *frontier_repair,
        *frontier_sentinel, *frontier_workflow, *frontier_structural,
    ]
    if _infrastructure_ids(frontier_block_entries):
        raise ValueError("frontier extension contains an infrastructure failure")
    if any(
        _toolsandbox_infrastructure_failure(json.loads(line))
        for entry in frontier_menu
        for line in (ROOT / str(entry["path"])).read_text(encoding="utf-8").splitlines()
    ):
        raise ValueError("frontier ToolSandbox extension contains an infrastructure failure")
    confirmatory_capacity = _for_models(inputs["capacity"], activation_models)
    confirmatory_discovery = _for_models(inputs["discovery"], activation_models)
    retained_capacity = [
        *[item for item in inputs["capacity"] if str(item["model"]) not in activation_models],
        *inputs["candidate_capacity"],
        *inputs["frontier_capacity"],
    ]
    retained_discovery = [
        *[item for item in inputs["discovery"] if str(item["model"]) not in activation_models],
        *inputs["candidate_discovery"],
        *inputs["frontier_discovery"],
    ]
    manifest = {
        "schema": "bottleneckbench-release-analysis-inputs-v1",
        "panel_sha256": None,
        "models": models,
        "expected_rows": expected,
        "inputs": inputs,
        "infrastructure_condition_ids": {
            "historical_capacity": _infrastructure_ids(inputs["capacity"]),
            "historical_discovery": _infrastructure_ids(inputs["discovery"]),
            "capacity": _infrastructure_ids([*inputs["capacity"], *inputs["candidate_capacity"], *inputs["frontier_capacity"]]),
            "discovery": _infrastructure_ids([*inputs["discovery"], *inputs["candidate_discovery"], *inputs["frontier_discovery"]]),
            "repair": _infrastructure_ids([*inputs["repair"], *inputs["candidate_repair"], *inputs["frontier_repair"]]),
            "sentinel": _infrastructure_ids([*inputs["sentinel"], *inputs["candidate_sentinel"], *inputs["frontier_sentinel"]]),
            "workflow_frontier": _infrastructure_ids([*inputs["workflow_frontier"], *inputs["candidate_workflow_frontier"], *inputs["frontier_workflow_frontier"]]),
            "candidate_capacity": _infrastructure_ids(inputs["candidate_capacity"]),
            "candidate_discovery": _infrastructure_ids(inputs["candidate_discovery"]),
            "candidate_repair": _infrastructure_ids(inputs["candidate_repair"]),
            "frontier_capacity": _infrastructure_ids(inputs["frontier_capacity"]),
            "frontier_discovery": _infrastructure_ids(inputs["frontier_discovery"]),
            "frontier_repair": _infrastructure_ids(inputs["frontier_repair"]),
            "confirmatory_layer_capacity": _infrastructure_ids(confirmatory_capacity),
            "confirmatory_layer_discovery": _infrastructure_ids(confirmatory_discovery),
            "retained_layer_capacity": _infrastructure_ids(retained_capacity),
            "retained_layer_discovery": _infrastructure_ids(retained_discovery),
        },
        "total_rows": sum(observed.values()),
        "source_manifests": [
            *([{"path": _portable(opus_source_map), "sha256": _sha(opus_source_map)}] if opus_source_map else []),
            *(
                [{"path": _portable(frontier_source_map), "sha256": _sha(frontier_source_map)}]
                if frontier_source_map
                else [
                    {"path": str(HISTORICAL_MANIFEST.relative_to(ROOT)), "sha256": _sha(HISTORICAL_MANIFEST)},
                    {"path": str(CANDIDATE_PANEL.relative_to(ROOT)), "sha256": _sha(CANDIDATE_PANEL)},
                    {"path": str(FRONTIER_PANEL.relative_to(ROOT)), "sha256": _sha(FRONTIER_PANEL)},
                    {"path": str(FRONTIER_LAUNCH.relative_to(ROOT)), "sha256": _sha(FRONTIER_LAUNCH)},
                ]
            ),
            {"path": str(INFRASTRUCTURE_AMENDMENT.relative_to(ROOT)), "sha256": _sha(INFRASTRUCTURE_AMENDMENT)},
        ],
        "archival_provenance": [
            {
                "archival_reference": str(path.relative_to(ROOT)),
                "sha256_digest": _sha(path),
            }
            for path in (
                HISTORICAL_MANIFEST,
                CANDIDATE_PANEL,
                FRONTIER_PANEL,
                FRONTIER_LAUNCH,
            )
        ],
        "plan_sources": {
            "structural_additional": [
                {"plan": str(STRUCTURAL_PLAN.relative_to(ROOT)), "input_root": str((roots / "structural").relative_to(ROOT)), "models": sorted(structural_models)},
                *[
                    {"plan": _portable(Path(source["plan"])), "input_root": _portable(Path(source["input_root"])), "models": sorted(map(str, source["models"]))}
                    for source in frontier_sources["structural"]
                ],
            ],
            "menu_intervention": [
                {"plan": str(TOOLSANDBOX_PLAN.relative_to(ROOT)), "input_root": str((roots / "toolsandbox").relative_to(ROOT)), "models": sorted(candidate_menu_models)},
                *[
                    {"plan": _portable(Path(source["plan"])), "input_root": _portable(Path(source["input_root"])), "models": sorted(map(str, source["models"]))}
                    for source in frontier_sources["toolsandbox"]
                ],
            ],
        },
        "analysis_policy": {
            "candidate_extension_confirmatory_weight": 0,
            "frontier_extension_confirmatory_weight": 0,
            "layer_activation_models": sorted(activation_models),
            "infrastructure_estimand": "configuration-level ITT with complete-model-by-semantic-unit sensitivity",
            "candidate_menu_estimand": "transport-complete frozen configuration blocks only; no partial model block enters a contrast",
            "targeted_vw_estimand": "exact 1,152-row terminal closure only; closed-incomplete transport evidence has zero scientific rows and zero weight",
        },
        "prospective_studies": {
            **({"targeted_vw": targeted_record} if targeted_record is not None else {})
        },
        "excluded_incomplete_blocks": excluded_blocks,
    }
    if manifest["total_rows"] != sum(expected.values()):
        raise ValueError("release evidence ledger row total does not match its frozen block counts")
    if (
        len(activation_models) != 6
        or len(historical_structural) != 4
        or len(set(panel["auxiliary_models"])) != panel_size - 2  # type: ignore[arg-type]
        or len(set(panel["structural_additional_models"])) != panel_size - 13  # type: ignore[arg-type]
        or len(candidate_menu_models | set(frontier_models)) != panel_size - 16
    ):
        raise ValueError("release analysis changed a frozen confirmatory or descriptive coverage boundary")
    panel["coverage_counts"] = {
        "core_descriptive": panel_size,
        "auxiliary_descriptive": panel_size - 2,
        "structural_additional_descriptive": panel_size - 13,
        "concurrent_menu_descriptive": panel_size - 16,
        "layer_activation_initial": 6,
        "structural_registered": 4,
    }
    panel["episode_counts"] = {
        "analysis_manifest": manifest["total_rows"],
        "core": 14_256 + 792 * len(frontier_models),
        "auxiliary_repair_sentinel_frontier": 2_552 + 232 * (len(candidate_auxiliary_models) + len(frontier_models)),
        "structural_registered": 864,
        "structural_additional": 216 * (len(structural_models) + len(frontier_models)),
        "external_rank": 352,
        "menu_matched": 576 + 96 * (len(candidate_menu_models) + len(frontier_models)),
    }
    return panel, manifest


def build_from_release_seed(opus_source_map: Path) -> tuple[dict[str, object], dict[str, object]]:
    """Extend an exact, complete release without unavailable acquisition files.

    The seed supplies inventory and provenance, never pooled statistics. Every
    selected row is hash checked and all analyses are regenerated downstream.
    """
    from build_opus_release_source_map import MODEL as opus_model, validate_source_map
    from replay_release_results import _validate_manifest

    for name, digest in RELEASE_SEED_HASHES.items():
        if _sha(RELEASE_SEED_ROOT / name) != digest:
            raise ValueError("complete release seed changed")
    panel = _read(RELEASE_SEED_ROOT / "release-panel.json")
    manifest = _read(RELEASE_SEED_ROOT / "release-analysis-inputs.json")
    _validate_manifest(manifest, RELEASE_SEED_ROOT / "release-panel.json")
    for item in manifest["source_manifests"]:
        if _sha(ROOT / item["path"]) != item["sha256"]:
            raise ValueError("complete release seed source changed")
    if (len(panel["configurations"]) != 20 or manifest["total_rows"] != 23704
            or {item["model"] for item in panel["configurations"]} != set(manifest["models"])
            or opus_model in manifest["models"]):
        raise ValueError("expected the exact complete pre-admission release inventory")
    opus = validate_source_map(opus_source_map, ROOT)
    for source in opus["sources"]:
        entries = validate_plan_sources([{"plan": ROOT / source["plan"],
            "rows": [ROOT / path for path in source["rows"]], "models": {opus_model}}],
            verify_plan_inputs=False)
        name = source["ledger_group"]
        manifest["inputs"][name].extend(entries)
        manifest["expected_rows"][name] += sum(entry["rows"] for entry in entries)
        if source["block"] in {"structural", "toolsandbox"}:
            group = "structural_additional" if source["block"] == "structural" else "menu_intervention"
            manifest["plan_sources"][group].append({"plan": source["plan"],
                "input_root": source["input_root"], "models": [opus_model]})
    panel["configurations"].append({"model": opus_model, "display_name": "Claude Opus 5",
        "organization": "Anthropic", "provider": "OpenRouter / Anthropic", "access": "managed",
        "coverage": ["core", "auxiliary", "structural_additional", "menu_matched"]})
    panel["auxiliary_models"].append(opus_model)
    panel["structural_additional_models"] = sorted([*panel["structural_additional_models"], opus_model])
    panel["reporting_policy"] = "unified model reporting; the 21-configuration core panel is the primary analysis panel and acquisition order appears only in provenance fields"
    for key in ("core_descriptive", "auxiliary_descriptive", "structural_additional_descriptive", "concurrent_menu_descriptive"):
        panel["coverage_counts"][key] += 1
    increments = {"analysis_manifest": 1336, "core": 792, "auxiliary_repair_sentinel_frontier": 232,
                  "structural_additional": 216, "menu_matched": 96}
    for key, increment in increments.items():
        panel["episode_counts"][key] += increment
    manifest["models"].append(opus_model)
    manifest["total_rows"] = sum(manifest["expected_rows"].values())
    manifest["source_manifests"].append({"path": _portable(opus_source_map), "sha256": _sha(opus_source_map)})
    manifest["release_seed_artifacts"] = [{"path": _portable(RELEASE_SEED_ROOT / name), "sha256": digest}
        for name, digest in RELEASE_SEED_HASHES.items()]
    manifest["generator"] = {"path": "experiments/build_release_analysis_manifest.py", "sha256": _sha(Path(__file__))}
    manifest.pop("manifest_hash")
    manifest["panel_sha256"] = None
    _assert_globally_unique(manifest["inputs"])
    if (manifest["total_rows"] != 25040 or len(manifest["models"]) != 21
            or len(set(manifest["models"])) != 21 or len(panel["auxiliary_models"]) != 19
            or len(panel["structural_additional_models"]) != 8):
        raise ValueError("complete unified panel admission did not close")
    return panel, manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--panel", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--record-panel-path",
        help="repository-relative archival path to record when writing a temporary replay copy",
    )
    parser.add_argument("--frontier-source-map", type=Path)
    parser.add_argument("--opus-source-map", type=Path)
    parser.add_argument("--release-seed", action="store_true", help="rebuild from the exact complete portable release inventory")
    parser.add_argument(
        "--archival-source-map-only",
        action="store_true",
        help="public replay: validate selected plans/rows without following recovery history",
    )
    parser.add_argument(
        "--targeted-vw-gate",
        type=Path,
        default=TARGETED_VW_GATE if TARGETED_VW_GATE.exists() else None,
    )
    args = parser.parse_args()
    args.panel = args.panel.resolve()
    args.manifest = args.manifest.resolve()
    if args.panel.exists() or args.manifest.exists():
        raise RuntimeError("refusing to overwrite frozen release artifacts")
    if args.release_seed:
        if args.opus_source_map is None:
            parser.error("--release-seed requires a complete --opus-source-map")
        panel, manifest = build_from_release_seed(args.opus_source_map.resolve())
    else:
        panel, manifest = build(
            args.frontier_source_map.resolve() if args.frontier_source_map else None,
            args.targeted_vw_gate.resolve() if args.targeted_vw_gate else None,
            archival_source_map_only=args.archival_source_map_only,
            opus_source_map=args.opus_source_map.resolve() if args.opus_source_map else None,
        )
    args.panel.parent.mkdir(parents=True, exist_ok=True)
    args.panel.write_text(canonical_json(panel) + "\n", encoding="utf-8")
    if args.record_panel_path:
        recorded = _resolve(args.record_panel_path).resolve()
        recorded.relative_to(ROOT.resolve())
        manifest["panel_path"] = str(recorded.relative_to(ROOT.resolve()))
    else:
        manifest["panel_path"] = _portable(args.panel)
    manifest["panel_sha256"] = _sha(args.panel)
    manifest["manifest_hash"] = stable_hash(manifest)
    args.manifest.write_text(canonical_json(manifest) + "\n", encoding="utf-8")
    print(canonical_json({"models": len(panel["configurations"]), "rows": manifest["total_rows"], "manifest_hash": manifest["manifest_hash"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
