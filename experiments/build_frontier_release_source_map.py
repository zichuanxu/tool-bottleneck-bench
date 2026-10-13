#!/usr/bin/env python3
"""Build the deterministic release map for completed frontier recoveries.

This is an archival/release operation.  It deliberately does not call either
hosted runner or its runtime validator: completion, selection, plans, and rows
are checked from their frozen artifacts and then bound byte-for-byte.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from typing import Mapping

from bottleneckbench.core.schema import canonical_json, stable_hash


ROOT = Path(__file__).resolve().parents[1]
GENERATOR = Path(__file__).resolve()
DEFAULT_LAUNCH = ROOT / "results/frontier-model-extension/plans/manifest.json"
DEFAULT_CLAUDE_ROOT = (
    ROOT / "results/frontier-model-extension/claude-full-condition-scoped-recovery"
)
DEFAULT_SOL_ROOT = ROOT / "results/frontier-model-extension/sol-fast-route-recovery"
DEFAULT_CLAUDE_COMPOSITE_PLAN = (
    ROOT
    / "results/frontier-model-extension"
    / "claude-full-condition-scoped-recovery-continuation-2"
    / "plan.json"
)
DEFAULT_PRICING = ROOT / "configs/frontier-active-route-pricing-2026-09-02.json"
DEFAULT_OUTPUT = ROOT / "results/frontier-model-extension/release-source-map.json"

SCHEMA = "bottleneckbench-frontier-release-source-map-v1"
MODELS = {
    "anthropic/claude-sonnet-5": {
        "selection_schema": "bottleneckbench-claude-full-recovery-source-selection-v1",
        "completion_schema": "bottleneckbench-claude-full-condition-scoped-recovery-v1",
        "composite_selection_schema": "bottleneckbench-claude-full-recovery-composite-selection-v1",
        "composite_completion_schema": "bottleneckbench-claude-full-recovery-composite-completion-v1",
        "provider": "OpenRouter / Anthropic",
        "provider_only": ["anthropic"],
        "input": 2.0,
        "output": 10.0,
    },
    "openai/gpt-5.6-sol": {
        "selection_schema": "bottleneckbench-sol-fast-route-source-selection-v1",
        "completion_schema": "bottleneckbench-sol-fast-route-recovery-completion-v1",
        "provider": "OpenRouter / OpenAI fast",
        "provider_only": ["openai/fast"],
        "input": 4.0,
        "output": 20.0,
    },
}
BLOCK_ROWS = {
    "core": 792,
    "repair": 144,
    "sentinel": 72,
    "frontier": 16,
    "structural": 216,
    "toolsandbox": 96,
}
CLAUDE_COMPOSITES = {
    "bottleneckbench-claude-full-recovery-continuation-plan-v1": {
        "completion": "bottleneckbench-claude-full-recovery-composite-completion-v1",
        "selection": "bottleneckbench-claude-full-recovery-composite-selection-v1",
        "remaining_rows": 904,
        "segment_rows": {"immutable_original": 432, "snapshot_continuation": 904},
    },
    "bottleneckbench-claude-full-recovery-continuation-plan-v2": {
        "completion": "bottleneckbench-claude-full-recovery-three-segment-completion-v1",
        "selection": "bottleneckbench-claude-full-recovery-three-segment-selection-v1",
        "remaining_rows": 328,
        "segment_rows": {
            "immutable_original": 432,
            "snapshot_continuation_1": 576,
            "snapshot_continuation_2": 328,
        },
    },
}


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _resolve(root: Path, value: object) -> Path:
    path = Path(str(value))
    resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"release source escapes the repository: {path}") from exc
    return resolved


def _portable(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError as exc:
        raise ValueError(f"release source escapes the repository: {path}") from exc


def _unhash(value: Mapping[str, object], field: str) -> dict[str, object]:
    result = dict(value)
    result.pop(field, None)
    return result


def _condition_id(condition: Mapping[str, object]) -> str:
    explicit = condition.get("condition_id")
    return str(explicit) if isinstance(explicit, str) else stable_hash(condition)


def _row_model(row: Mapping[str, object]) -> str:
    condition = row.get("condition")
    value = condition.get("model") if isinstance(condition, dict) else row.get("model")
    if not isinstance(value, str):
        raise ValueError("frontier row lacks a model identifier")
    return value


def _validate_plan(path: Path, expected_sha: object | None = None) -> dict[str, object]:
    if expected_sha is not None and expected_sha != _sha(path):
        raise ValueError(f"recovery plan changed: {path}")
    plan = _read(path)
    supplied = plan.get("plan_hash")
    if not isinstance(supplied, str) or supplied != stable_hash(_unhash(plan, "plan_hash")):
        raise ValueError(f"recovery plan hash mismatch: {path}")
    conditions = plan.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        raise ValueError(f"recovery plan has no conditions: {path}")
    identifiers = [_condition_id(item) for item in conditions if isinstance(item, dict)]
    if len(identifiers) != len(conditions) or len(set(identifiers)) != len(identifiers):
        raise ValueError(f"recovery plan condition IDs are not unique: {path}")
    return plan


def validate_archival_launch(launch: Mapping[str, object], root: Path = ROOT) -> None:
    """Validate immutable launch denominators without consulting mutable config files."""

    if (
        launch.get("schema") != "bottleneckbench-frontier-model-extension-launch-v1"
        or launch.get("manifest_hash") != stable_hash(_unhash(launch, "manifest_hash"))
        or set(map(str, launch.get("models", []))) != set(MODELS)
        or launch.get("confirmatory_weight") != 0
        or launch.get("planned_rows") != 2672
        or launch.get("block_rows")
        != {key: value * 2 for key, value in BLOCK_ROWS.items()}
    ):
        raise ValueError("archival frontier launch identity or denominator changed")
    entries = launch.get("plans")
    if not isinstance(entries, list) or len(entries) != 16:
        raise ValueError("archival frontier launch plan inventory changed")
    total = 0
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("archival frontier launch plan entry is malformed")
        path = _resolve(root, entry["path"])
        if entry.get("sha256") != _sha(path):
            raise ValueError(f"archival frontier launch plan changed: {path}")
        plan = _read(path)
        conditions = plan.get("conditions")
        if not isinstance(conditions, list) or int(entry["conditions"]) != len(conditions):
            raise ValueError(f"archival frontier launch plan denominator changed: {path}")
        total += len(conditions)
    if total != 2672:
        raise ValueError("archival frontier launch rows do not close")


def _output_for(root: Path, entry: Mapping[str, object], plan: Mapping[str, object]) -> Path:
    block = str(entry["block"])
    if block in {"structural", "toolsandbox"}:
        configurations = plan.get("configurations")
        if not isinstance(configurations, list) or len(configurations) != 1:
            raise ValueError(f"{block} recovery plan must have one configuration")
        slug = str(configurations[0]["slug"])
        if block == "structural":
            return root / "execution/structural/active" / f"{slug}.jsonl"
        return root / "execution/toolsandbox" / f"{slug.removesuffix('-primary')}.jsonl"
    return root / "execution" / block / f"{Path(str(entry['path'])).stem}.jsonl"


def _provider_only(plan: Mapping[str, object]) -> set[tuple[str, ...]]:
    profiles: list[Mapping[str, object]] = []
    if isinstance(plan.get("execution"), dict):
        profiles.append(plan["execution"])  # type: ignore[arg-type]
    for item in plan.get("configurations", []):  # type: ignore[union-attr]
        if isinstance(item, dict):
            profile = item.get("execution") if isinstance(item.get("execution"), dict) else item
            profiles.append(profile)
    return {
        tuple(map(str, profile["provider_only"]))
        for profile in profiles
        if isinstance(profile.get("provider_only"), list)
    }


def _archival_config_binding(
    manifest: Mapping[str, object], model: str, root: Path
) -> dict[str, object]:
    """Preserve the recovery's frozen config commitment without following it.

    The active route is independently byte-bound by the release pricing snapshot.
    This archival binding may name an earlier frozen config revision whose path
    later became the active mutable config, so release validation must not compare
    that historical digest with today's bytes.
    """

    if model == "openai/gpt-5.6-sol":
        raw_path = manifest.get("config_path", "configs/frontier-sol-fast-route.json")
        expected = manifest.get("config_sha256")
    else:
        candidates = [
            item
            for item in manifest.get("source_files", [])  # type: ignore[union-attr]
            if isinstance(item, dict)
            and str(item.get("path")) == "configs/frontier-model-extension.json"
        ]
        if len(candidates) != 1:
            raise ValueError("Claude recovery manifest lacks its frozen config binding")
        raw_path, expected = candidates[0]["path"], candidates[0]["sha256"]
    path = _resolve(root, raw_path)
    if not isinstance(expected, str) or len(expected) != 64:
        raise ValueError("recovery manifest lacks a frozen config digest")
    return {
        "archival_reference": _portable(root, path),
        "sha256_digest": expected,
    }


def _validate_recovery(
    model: str,
    recovery_root: Path,
    launch: Mapping[str, object],
    root: Path,
    *,
    claude_composite_plan: Path | None = None,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    expected = MODELS[model]
    manifest_path = recovery_root / "plans/manifest.json"
    completion_path = recovery_root / "execution/completion.json"
    selection_path = recovery_root / "execution/active-source-selection.json"
    for path in (manifest_path, completion_path, selection_path):
        if not path.exists():
            raise ValueError(f"completed recovery artifact is missing: {path}")
    manifest = _read(manifest_path)
    if manifest.get("manifest_hash") != stable_hash(_unhash(manifest, "manifest_hash")):
        raise ValueError(f"recovery manifest hash mismatch: {manifest_path}")
    frozen_weight = manifest.get(
        "confirmatory_weight", manifest.get("scientific_weight_until_complete")
    )
    if (
        manifest.get("planned_rows", manifest.get("planned_conditions")) != 1336
        or frozen_weight != 0
    ):
        raise ValueError(f"recovery manifest denominator or weight changed: {manifest_path}")
    base_hash = manifest.get("base_launch_manifest_hash")
    if base_hash is not None and base_hash != launch.get("manifest_hash"):
        raise ValueError("recovery manifest is bound to another launch")
    plan_entries = manifest.get("plans")
    if not isinstance(plan_entries, list) or len(plan_entries) != 9:
        raise ValueError("recovery manifest must contain nine plan entries")

    completion = _read(completion_path)
    selection = _read(selection_path)
    composite_schema = next(
        (
            schema
            for schema, specification in CLAUDE_COMPOSITES.items()
            if model.startswith("anthropic/")
            and completion.get("schema") == specification["completion"]
        ),
        None,
    )
    composite = composite_schema is not None
    composite_spec = CLAUDE_COMPOSITES.get(str(composite_schema))
    composite_plan: dict[str, object] | None = None
    active_plan_entries = plan_entries
    if composite:
        if claude_composite_plan is None or not claude_composite_plan.exists():
            raise ValueError("Claude composite completion lacks its continuation plan")
        composite_plan = _read(claude_composite_plan)
        if (
            composite_plan.get("schema") != composite_schema
            or composite_plan.get("plan_hash")
            != stable_hash(_unhash(composite_plan, "plan_hash"))
            or composite_plan.get("composite_rows") != 1336
            or composite_plan.get("remaining_rows") != composite_spec["remaining_rows"]  # type: ignore[index]
            or composite_plan.get("scientific_weight_until_composite_complete") != 0
            or composite_plan.get("scientific_weight_after_all_gates") != 1
        ):
            raise ValueError("Claude continuation plan identity changed")
        original_binding = composite_plan.get("original_manifest")
        if (
            not isinstance(original_binding, dict)
            or _resolve(root, original_binding.get("path")) != manifest_path.resolve()
            or original_binding.get("sha256") != _sha(manifest_path)
            or original_binding.get("manifest_hash") != manifest.get("manifest_hash")
        ):
            raise ValueError("Claude continuation does not close the original recovery")
        if composite_schema.endswith("plan-v1"):
            if (
                composite_plan.get("continuation_entries") != plan_entries[1:]
                or composite_plan.get("completed_segment", {}).get("rows") != 432  # type: ignore[union-attr]
            ):
                raise ValueError("Claude two-segment continuation inventory changed")
        else:
            completed = composite_plan.get("completed_segments")
            active_plan_entries = composite_plan.get("composite_entries")
            if (
                not isinstance(completed, list)
                or [item.get("rows") for item in completed if isinstance(item, dict)]
                != [432, 576]
                or not isinstance(active_plan_entries, list)
                or len(active_plan_entries) != 9
                or composite_plan.get("segment_rows") != composite_spec["segment_rows"]  # type: ignore[index]
            ):
                raise ValueError("Claude three-segment continuation inventory changed")

    expected_completion_schema = (
        composite_spec["completion"]  # type: ignore[index]
        if composite
        else expected["completion_schema"]
    )
    expected_selection_schema = (
        composite_spec["selection"]  # type: ignore[index]
        if composite
        else expected["selection_schema"]
    )
    expected_manifest_hash = (
        composite_plan["plan_hash"] if composite_plan is not None else manifest["manifest_hash"]
    )
    expected_tranche_hash = (
        composite_plan["composite_tranche_hash"]
        if composite_plan is not None
        else manifest["tranche_hash"]
    )
    completion_prior_incidents = completion.get(
        "prior_quarantined_incidents_included",
        completion.get("prior_incidents_included"),
    )
    if (
        completion.get("schema") != expected_completion_schema
        or completion.get("status") != "complete"
        or completion.get("rows") != 1336
        or completion.get("manifest_hash") != expected_manifest_hash
        or completion.get("tranche_hash") != expected_tranche_hash
        or completion.get("confirmatory_weight", 0) != 0
        or completion.get("infrastructure_failures") != 0
        or completion_prior_incidents is not False
        or (composite and completion.get("scientific_weight") != 1)
        or (
            composite_plan is not None
            and completion.get("original_manifest_hash") != manifest.get("manifest_hash")
        )
        or (
            composite_plan is not None
            and completion.get("segment_rows") != composite_spec["segment_rows"]  # type: ignore[index]
        )
    ):
        raise ValueError(f"recovery completion is not release eligible: {completion_path}")
    selection_prior_incidents = selection.get(
        "prior_quarantined_incidents_included",
        selection.get("prior_incidents_included"),
    )
    if (
        selection.get("schema") != expected_selection_schema
        or selection.get("status") != "eligible"
        or selection.get("model") != model
        or selection.get("manifest_hash") != expected_manifest_hash
        or selection.get("tranche_hash") != expected_tranche_hash
        or selection.get("rows") != 1336
        or selection_prior_incidents is not False
        or (composite and selection.get("scientific_weight") != 1)
    ):
        raise ValueError(f"recovery source selection is not eligible: {selection_path}")

    selected = {
        _portable(root, _resolve(root, item)) for item in selection.get("sources", [])
    }
    selected_rows_by_plan_hash: dict[str, Path] = {}
    if model.startswith("anthropic/"):
        if set(map(str, completion.get("active_paths", []))) != selected:
            raise ValueError("Claude completion and selection active paths differ")
        for raw_path in sorted(selected):
            row_path = _resolve(root, raw_path)
            rows = [
                json.loads(line)
                for line in row_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            plan_hashes = {str(row.get("plan_hash")) for row in rows}
            if not rows or len(plan_hashes) != 1:
                raise ValueError("Claude selected source does not bind exactly one plan")
            plan_hash = next(iter(plan_hashes))
            if plan_hash in selected_rows_by_plan_hash:
                raise ValueError("Claude selection repeats a frozen plan")
            selected_rows_by_plan_hash[plan_hash] = row_path

    sources: list[dict[str, object]] = []
    block_rows: dict[str, int] = {}
    all_ids: set[str] = set()
    row_paths: list[str] = []
    plan_paths: list[str] = []
    for entry in active_plan_entries:
        if not isinstance(entry, dict):
            raise ValueError("recovery plan entry is malformed")
        block = str(entry["block"])
        path = _resolve(root, entry["path"])
        plan = _validate_plan(path, entry.get("sha256"))
        conditions = [item for item in plan["conditions"] if isinstance(item, dict)]  # type: ignore[index]
        models = {_row_model({"condition": item}) for item in conditions}
        if models != {model} or len(conditions) != int(entry["conditions"]):
            raise ValueError(f"recovery plan model or denominator changed: {path}")
        providers = _provider_only(plan)
        if providers and providers != {tuple(expected["provider_only"])}:
            raise ValueError(f"recovery plan uses another provider route: {path}")
        identifiers = {_condition_id(item) for item in conditions}
        if all_ids & identifiers:
            raise ValueError("recovery plans overlap")
        all_ids |= identifiers
        block_rows[block] = block_rows.get(block, 0) + len(identifiers)
        output = (
            selected_rows_by_plan_hash.get(str(plan["plan_hash"]))
            if model.startswith("anthropic/")
            else _output_for(recovery_root, entry, plan)
        )
        if output is None:
            raise ValueError(f"Claude selection omits a frozen recovery plan: {path}")
        if not output.exists():
            raise ValueError(f"completed recovery row file is missing: {output}")
        rows = [
            json.loads(line)
            for line in output.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        observed = [str(row.get("condition_id")) for row in rows]
        if (
            len(rows) != len(identifiers)
            or len(set(observed)) != len(observed)
            or set(observed) != identifiers
            or any(_row_model(row) != model for row in rows)
            or any(row.get("plan_hash") != plan["plan_hash"] for row in rows)
            or any(row.get("confirmatory_weight", 0) != 0 for row in rows)
            or any(row.get("attempt", 1) != 1 for row in rows)
        ):
            raise ValueError(f"completed recovery rows do not match their plan: {output}")
        source: dict[str, object] = {
            "block": block,
            "plan": _portable(root, path),
            "plan_sha256": _sha(path),
            "plan_artifact": {
                "path": _portable(root, path),
                "sha256": _sha(path),
            },
            "rows": [_portable(root, output)],
            "rows_sha256": [_sha(output)],
            "row_artifacts": [{
                "path": _portable(root, output),
                "sha256": _sha(output),
            }],
            "models": [model],
        }
        if block in {"structural", "toolsandbox"}:
            source["input_root"] = _portable(
                root, output.parent.parent if block == "structural" else output.parent
            )
        sources.append(source)
        row_paths.append(_portable(root, output))
        plan_paths.append(_portable(root, path))
    if (
        block_rows != BLOCK_ROWS
        or len(all_ids) != 1336
        or all_ids != set(map(str, manifest.get("condition_ids", [])))
    ):
        raise ValueError(f"recovery blocks do not close 1,336 rows: {block_rows}")

    expected_selection = set(row_paths if model.startswith("anthropic/") else plan_paths)
    if selected != expected_selection:
        raise ValueError("recovery source selection does not name the complete active tranche")
    if model.startswith("anthropic/") and set(map(str, completion.get("active_paths", []))) != set(row_paths):
        raise ValueError("Claude completion active paths changed")
    if model.startswith("openai/"):
        plan_hashes = completion.get("plan_hashes")
        if not isinstance(plan_hashes, dict) or set(map(str, plan_hashes)) != set(plan_paths):
            raise ValueError("Sol completion plan inventory changed")
        expected_hashes = {
            str(source["plan"]): _read(_resolve(root, source["plan"]))["plan_hash"]
            for source in sources
        }
        if {str(key): str(value) for key, value in plan_hashes.items()} != expected_hashes:
            raise ValueError("Sol completion plan hashes changed")
    binding = {
        "manifest": {
            "archival_reference": _portable(root, manifest_path),
            "sha256_digest": _sha(manifest_path),
            "manifest_hash": manifest["manifest_hash"],
            "tranche_hash": manifest["tranche_hash"],
        },
        "completion": {
            "archival_reference": _portable(root, completion_path),
            "sha256_digest": _sha(completion_path),
            "status": "complete",
            "rows": 1336,
        },
        "selection": {
            "archival_reference": _portable(root, selection_path),
            "sha256_digest": _sha(selection_path),
            "status": "eligible",
            "rows": 1336,
        },
        "recovery_config": _archival_config_binding(manifest, model, root),
        "route": {
            "provider": expected["provider"],
            "provider_only": expected["provider_only"],
        },
    }
    if composite_plan is not None and claude_composite_plan is not None:
        binding["composite_plan"] = {
            "archival_reference": _portable(root, claude_composite_plan),
            "sha256_digest": _sha(claude_composite_plan),
            "plan_hash": composite_plan["plan_hash"],
            "composite_tranche_hash": composite_plan["composite_tranche_hash"],
        }
    return sorted(sources, key=lambda item: (str(item["block"]), str(item["plan"]))), binding


def _validate_pricing(
    path: Path,
    root: Path,
    config_bindings: Mapping[str, object] | None = None,
) -> dict[str, object]:
    pricing = _read(path)
    if pricing.get("schema") != "bottleneckbench-pricing-v1":
        raise ValueError("active-route pricing snapshot schema changed")
    rows = pricing.get("models")
    if not isinstance(rows, dict) or set(rows) != set(MODELS):
        raise ValueError("active-route pricing snapshot model inventory changed")
    for model, expected in MODELS.items():
        row = rows[model]
        if (
            not isinstance(row, dict)
            or float(row.get("input", -1)) != expected["input"]
            or float(row.get("output", -1)) != expected["output"]
            or row.get("provider") != expected["provider"]
            or row.get("provider_only") != expected["provider_only"]
        ):
            raise ValueError(f"active-route pricing changed for {model}")
        route_config = _resolve(root, row.get("route_config_path"))
        binding = (config_bindings or {}).get(model)
        frozen_config = (
            binding.get("recovery_config")
            if isinstance(binding, dict)
            and isinstance(binding.get("recovery_config"), dict)
            else None
        )
        route_digest = (
            frozen_config["sha256_digest"]
            if isinstance(frozen_config, dict)
            and _resolve(root, frozen_config.get("archival_reference")) == route_config
            else _sha(route_config)
        )
        if row.get("route_config_sha256") != route_digest:
            raise ValueError(f"active-route config binding changed for {model}")
    return {"path": _portable(root, path), "sha256": _sha(path)}


def build_source_map(
    *,
    launch_path: Path = DEFAULT_LAUNCH,
    recovery_roots: Mapping[str, Path] | None = None,
    pricing_path: Path = DEFAULT_PRICING,
    generator_path: Path = GENERATOR,
    claude_composite_plan: Path | None = None,
    root: Path = ROOT,
) -> dict[str, object]:
    roots = dict(recovery_roots or {
        "anthropic/claude-sonnet-5": DEFAULT_CLAUDE_ROOT,
        "openai/gpt-5.6-sol": DEFAULT_SOL_ROOT,
    })
    if set(roots) != set(MODELS):
        raise ValueError("release source map requires exactly Claude Sonnet 5 and GPT-5.6 Sol")
    launch = _read(launch_path)
    validate_archival_launch(launch, root)
    sources: list[dict[str, object]] = []
    bindings: dict[str, object] = {}
    if claude_composite_plan is None:
        candidate = root / DEFAULT_CLAUDE_COMPOSITE_PLAN.relative_to(ROOT)
        claude_composite_plan = candidate if candidate.exists() else None
    for model in sorted(MODELS):
        current, binding = _validate_recovery(
            model,
            roots[model],
            launch,
            root,
            claude_composite_plan=(
                claude_composite_plan if model.startswith("anthropic/") else None
            ),
        )
        sources.extend(current)
        bindings[model] = binding
    pricing = _validate_pricing(pricing_path, root, bindings)
    if len(sources) != 18:
        raise ValueError("release source map must contain exactly 18 source entries")
    payload: dict[str, object] = {
        "schema": SCHEMA,
        "launch_archival_reference": _portable(root, launch_path),
        "launch_sha256_digest": _sha(launch_path),
        "launch_manifest_hash": launch["manifest_hash"],
        "confirmatory_weight": 0,
        "models": sorted(MODELS),
        "active_route_pricing": pricing,
        "generator": {
            "path": _portable(root, generator_path),
            "sha256": _sha(generator_path),
        },
        "recovery_bindings": bindings,
        "sources": sorted(sources, key=lambda item: (str(item["block"]), str(item["plan"]))),
    }
    payload["artifact_hash"] = stable_hash(payload)
    return payload


def validate_source_map(path: Path, launch: Mapping[str, object], root: Path = ROOT) -> dict[str, object]:
    supplied = _read(path)
    if (
        supplied.get("schema") != SCHEMA
        or supplied.get("artifact_hash") != stable_hash(_unhash(supplied, "artifact_hash"))
        or supplied.get("launch_manifest_hash") != launch.get("manifest_hash")
        or supplied.get("confirmatory_weight") != 0
    ):
        raise ValueError("frontier release source map identity changed")
    bindings = supplied.get("recovery_bindings")
    if not isinstance(bindings, dict) or set(bindings) != set(MODELS):
        raise ValueError("frontier release source map recovery bindings changed")
    roots: dict[str, Path] = {}
    for model, raw in bindings.items():
        if not isinstance(raw, dict) or not isinstance(raw.get("manifest"), dict):
            raise ValueError("frontier release recovery binding is malformed")
        manifest = _resolve(
            root, raw["manifest"]["archival_reference"]  # type: ignore[index]
        )
        if raw["manifest"].get("sha256_digest") != _sha(manifest):  # type: ignore[index]
            raise ValueError("frontier release recovery manifest digest changed")
        roots[str(model)] = manifest.parent.parent
    pricing = supplied.get("active_route_pricing")
    if not isinstance(pricing, dict):
        raise ValueError("frontier release source map lacks active-route pricing")
    generator = supplied.get("generator")
    if not isinstance(generator, dict):
        raise ValueError("frontier release source map lacks its generator binding")
    rebuilt = build_source_map(
        launch_path=_resolve(root, supplied["launch_archival_reference"]),
        recovery_roots=roots,
        pricing_path=_resolve(root, pricing["path"]),
        generator_path=_resolve(root, generator["path"]),
        claude_composite_plan=(
            _resolve(root, bindings["anthropic/claude-sonnet-5"]["composite_plan"]["archival_reference"])  # type: ignore[index]
            if isinstance(bindings["anthropic/claude-sonnet-5"], dict)  # type: ignore[index]
            and isinstance(bindings["anthropic/claude-sonnet-5"].get("composite_plan"), dict)  # type: ignore[index,union-attr]
            else None
        ),
        root=root,
    )
    if rebuilt != supplied:
        raise ValueError("frontier release source map is not the deterministic recovery projection")
    return supplied


def validate_archival_source_map(
    path: Path, launch: Mapping[str, object], root: Path = ROOT
) -> dict[str, object]:
    """Validate the public release projection without following recovery history."""

    supplied = _read(path)
    if (
        supplied.get("schema") != SCHEMA
        or supplied.get("artifact_hash") != stable_hash(_unhash(supplied, "artifact_hash"))
        or supplied.get("launch_manifest_hash") != launch.get("manifest_hash")
        or supplied.get("confirmatory_weight") != 0
        or set(map(str, supplied.get("models", []))) != set(MODELS)
    ):
        raise ValueError("archival frontier source map identity changed")
    pricing = supplied.get("active_route_pricing")
    if not isinstance(pricing, dict):
        raise ValueError("archival frontier source map lacks active-route pricing")
    pricing_path = _resolve(root, pricing.get("path"))
    if pricing.get("sha256") != _sha(pricing_path):
        raise ValueError("archival frontier active-route pricing changed")
    bindings = supplied.get("recovery_bindings")
    if not isinstance(bindings, dict) or set(bindings) != set(MODELS):
        raise ValueError("archival frontier recovery bindings changed")
    _validate_pricing(pricing_path, root, bindings)
    generator = supplied.get("generator")
    if not isinstance(generator, dict):
        raise ValueError("archival frontier source map lacks its generator binding")
    generator_path = _resolve(root, generator.get("path"))
    if generator.get("sha256") != _sha(generator_path):
        raise ValueError("archival frontier source-map generator changed")
    sources = supplied.get("sources")
    if not isinstance(sources, list) or len(sources) != 18:
        raise ValueError("archival frontier source inventory changed")
    block_rows: dict[tuple[str, str], int] = {}
    seen_plans: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("archival frontier source entry is malformed")
        block = str(source.get("block"))
        models = list(map(str, source.get("models", [])))
        if block not in BLOCK_ROWS or len(models) != 1 or models[0] not in MODELS:
            raise ValueError("archival frontier source block or model changed")
        plan_path = _resolve(root, source.get("plan"))
        if source.get("plan_sha256") != _sha(plan_path):
            raise ValueError(f"archival frontier plan changed: {plan_path}")
        if source.get("plan_artifact") != {
            "path": _portable(root, plan_path),
            "sha256": _sha(plan_path),
        }:
            raise ValueError("archival frontier plan closure binding changed")
        plan = _validate_plan(plan_path)
        plan_key = _portable(root, plan_path)
        if plan_key in seen_plans:
            raise ValueError("archival frontier source repeats a plan")
        seen_plans.add(plan_key)
        row_paths = source.get("rows")
        row_hashes = source.get("rows_sha256")
        if not isinstance(row_paths, list) or not isinstance(row_hashes, list) or len(row_paths) != len(row_hashes):
            raise ValueError("archival frontier row binding is malformed")
        if source.get("row_artifacts") != [
            {"path": str(raw), "sha256": str(digest)}
            for raw, digest in zip(row_paths, row_hashes, strict=True)
        ]:
            raise ValueError("archival frontier row closure binding changed")
        conditions = {
            _condition_id(item): item
            for item in plan["conditions"]  # type: ignore[index]
            if isinstance(item, dict)
        }
        observed: set[str] = set()
        for raw_path, expected_sha in zip(row_paths, row_hashes, strict=True):
            row_path = _resolve(root, raw_path)
            if expected_sha != _sha(row_path):
                raise ValueError(f"archival frontier rows changed: {row_path}")
            rows = [
                json.loads(line)
                for line in row_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            for row in rows:
                identifier = str(row.get("condition_id"))
                if (
                    identifier not in conditions
                    or identifier in observed
                    or _row_model(row) != models[0]
                    or row.get("plan_hash") != plan["plan_hash"]
                    or row.get("confirmatory_weight", 0) != 0
                    or row.get("attempt", 1) != 1
                ):
                    raise ValueError(f"archival frontier rows do not match their plan: {row_path}")
                observed.add(identifier)
        if observed != set(conditions):
            raise ValueError(f"archival frontier rows do not close their plan: {plan_path}")
        block_rows[(models[0], block)] = block_rows.get((models[0], block), 0) + len(observed)
        if block in {"structural", "toolsandbox"}:
            _resolve(root, source.get("input_root"))
    expected_blocks = {
        (model, block): rows
        for model in MODELS
        for block, rows in BLOCK_ROWS.items()
    }
    if block_rows != expected_blocks:
        raise ValueError("archival frontier source rows do not close both model tranches")
    return supplied


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--launch", type=Path, default=DEFAULT_LAUNCH)
    parser.add_argument("--claude-root", type=Path, default=DEFAULT_CLAUDE_ROOT)
    parser.add_argument("--sol-root", type=Path, default=DEFAULT_SOL_ROOT)
    parser.add_argument("--pricing", type=Path, default=DEFAULT_PRICING)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError("refusing to overwrite a frozen frontier release source map")
    payload = build_source_map(
        launch_path=args.launch.resolve(),
        recovery_roots={
            "anthropic/claude-sonnet-5": args.claude_root.resolve(),
            "openai/gpt-5.6-sol": args.sol_root.resolve(),
        },
        pricing_path=args.pricing.resolve(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(canonical_json(payload) + "\n", encoding="utf-8")
    print(canonical_json({"passed": True, "sources": 18, "rows": 2672, "output": _portable(ROOT, args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
