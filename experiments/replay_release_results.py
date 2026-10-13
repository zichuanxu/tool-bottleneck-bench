#!/usr/bin/env python3
"""Fresh, offline replay of the public BottleneckBench release.

The runner consumes only the final release ledger/panel, the selected frontier
source map, active-route pricing, active plans/rows, and release analyzers.  It
does not follow recovery bindings, incident ledgers, or transport history.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Mapping

from bottleneckbench.core.schema import canonical_json, stable_hash


ROOT = Path(__file__).resolve().parents[1]
ANALYSIS = ROOT / "results/analysis"
DEFAULT_MANIFEST = ANALYSIS / "release-analysis-inputs.json"
DEFAULT_PANEL = ANALYSIS / "release-panel.json"
DEFAULT_SOURCE_MAP = ROOT / "results/frontier-model-extension/release-source-map.json"
ACTIVE_ROUTE_PRICES = ROOT / "configs/frontier-active-route-pricing-2026-09-02.json"
RUN_ANALYSIS = ROOT / "experiments/run_release_analysis.py"
DEFAULT_REPORT = ROOT / "results/release-reproducibility.json"
ACTIVE_ROUTES = {
    "anthropic/claude-sonnet-5": {
        "input": 2.0,
        "output": 10.0,
        "provider": "OpenRouter / Anthropic",
        "provider_only": ["anthropic"],
    },
    "openai/gpt-5.6-sol": {
        "input": 4.0,
        "output": 20.0,
        "provider": "OpenRouter / OpenAI fast",
        "provider_only": ["openai/fast"],
    },
}
BASE_OUTPUTS = (
    "release-core-panel.json",
    "release-layer-repairs.json",
    "release-sentinel.json",
    "release-workflow-frontier.json",
    "structural-repair-pooled.json",
    "candidate-toolsandbox-menu.json",
    "release-model-versus-system.json",
    "release-panel-cost.json",
    "release-failure-localization.json",
    "release-failure-localization-v2.json",
    "crossover-campaign.json",
    "revision-evidence-audit.json",
    "interaction-scale-sensitivity.json",
    "structural-repair-holdout.json",
    "structural-model-versus-system.json",
    "failure-mechanism-decomposition.json",
    "capacity-cluster-sensitivity.json",
)
FRONTIER_GROUPS = {
    "core": ("frontier_capacity", "frontier_discovery"),
    "repair": ("frontier_repair",),
    "sentinel": ("frontier_sentinel",),
    "frontier": ("frontier_workflow_frontier",),
    "structural": ("frontier_structural_additional",),
    "toolsandbox": ("frontier_menu_intervention",),
}


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _resolve(value: object) -> Path:
    path = Path(str(value))
    resolved = path.resolve() if path.is_absolute() else (ROOT / path).resolve()
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"release dependency escapes the repository: {path}") from exc
    return resolved


def _portable(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve()))


def _model(row: Mapping[str, object]) -> str:
    condition = row.get("condition")
    value = condition.get("model") if isinstance(condition, dict) else row.get("model")
    if not isinstance(value, str):
        raise ValueError("release row lacks a model identifier")
    return value


def _condition_id(condition: Mapping[str, object]) -> str:
    explicit = condition.get("condition_id")
    return str(explicit) if isinstance(explicit, str) else stable_hash(condition)


def _validate_manifest(manifest: Mapping[str, object], panel_path: Path) -> None:
    unhashed = dict(manifest)
    supplied = unhashed.pop("manifest_hash", None)
    if supplied != stable_hash(unhashed):
        raise ValueError("release manifest hash mismatch")
    if manifest.get("panel_sha256") != _sha(panel_path):
        raise ValueError("release manifest panel binding changed")
    inputs = manifest.get("inputs")
    expected = manifest.get("expected_rows")
    if not isinstance(inputs, dict) or not isinstance(expected, dict):
        raise ValueError("release manifest lacks input denominators")
    observed: dict[str, int] = {}
    seen: set[tuple[str, str]] = set()
    for name, entries in inputs.items():
        if not isinstance(entries, list):
            raise ValueError(f"release input group is malformed: {name}")
        observed[str(name)] = 0
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError(f"release input entry is malformed: {name}")
            path = _resolve(entry["path"])
            if entry.get("sha256") != _sha(path):
                raise ValueError(f"release input changed: {path}")
            rows = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if len(rows) != int(entry["rows"]):
                raise ValueError(f"release input row count changed: {path}")
            field = str(entry.get("identifier_field", "condition_id"))
            for row in rows:
                identifier = str(row.get(field))
                key = (field, identifier)
                if identifier == "None" or key in seen:
                    raise ValueError(f"duplicate release {field}: {identifier}")
                seen.add(key)
            observed[str(name)] += len(rows)
    if observed != {str(key): int(value) for key, value in expected.items()}:
        raise ValueError("release input groups do not match their frozen denominators")
    if manifest.get("total_rows") != sum(observed.values()):
        raise ValueError("release evidence ledger total changed")


def _frontier_entries(source_map: Mapping[str, object]) -> dict[str, list[dict[str, object]]]:
    unhashed = dict(source_map)
    supplied = unhashed.pop("artifact_hash", None)
    if (
        source_map.get("schema") != "bottleneckbench-frontier-release-source-map-v1"
        or supplied != stable_hash(unhashed)
        or source_map.get("confirmatory_weight") != 0
        or set(map(str, source_map.get("models", []))) != set(ACTIVE_ROUTES)
    ):
        raise ValueError("frontier release source map identity changed")
    pricing = source_map.get("active_route_pricing")
    if (
        not isinstance(pricing, dict)
        or _resolve(pricing.get("path")) != ACTIVE_ROUTE_PRICES.resolve()
        or pricing.get("sha256") != _sha(ACTIVE_ROUTE_PRICES)
    ):
        raise ValueError("frontier source map is not bound to active-route pricing")
    pricing_payload = _read(ACTIVE_ROUTE_PRICES)
    prices = pricing_payload.get("models")
    if pricing_payload.get("schema") != "bottleneckbench-pricing-v1" or not isinstance(
        prices, dict
    ) or set(prices) != set(ACTIVE_ROUTES):
        raise ValueError("active-route pricing inventory changed")
    for model, expected in ACTIVE_ROUTES.items():
        row = prices.get(model)
        if (
            not isinstance(row, dict)
            or float(row.get("input", -1)) != expected["input"]
            or float(row.get("output", -1)) != expected["output"]
            or row.get("provider") != expected["provider"]
            or row.get("provider_only") != expected["provider_only"]
        ):
            raise ValueError(f"active-route pricing changed for {model}")
        config_path = _resolve(row.get("route_config_path"))
        if row.get("route_config_sha256") != _sha(config_path):
            raise ValueError(f"active-route config binding changed for {model}")
    generator = source_map.get("generator")
    if not isinstance(generator, dict):
        raise ValueError("frontier source map lacks its generator binding")
    generator_path = _resolve(generator.get("path"))
    if generator.get("sha256") != _sha(generator_path):
        raise ValueError("frontier source-map generator changed")
    sources = source_map.get("sources")
    if not isinstance(sources, list) or len(sources) != 18:
        raise ValueError("frontier source map must contain 18 selected plan entries")
    groups: dict[str, list[dict[str, object]]] = {
        name: [] for names in FRONTIER_GROUPS.values() for name in names
    }
    seen_plans: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("frontier source entry is malformed")
        block = str(source.get("block"))
        if block not in FRONTIER_GROUPS:
            raise ValueError(f"frontier source block changed: {block}")
        plan_path = _resolve(source.get("plan"))
        if source.get("plan_sha256") != _sha(plan_path):
            raise ValueError(f"frontier active plan changed: {plan_path}")
        if source.get("plan_artifact") != {
            "path": _portable(plan_path),
            "sha256": _sha(plan_path),
        }:
            raise ValueError("frontier plan closure binding changed")
        plan = _read(plan_path)
        plan_hash = plan.get("plan_hash")
        if plan_hash != stable_hash({key: value for key, value in plan.items() if key != "plan_hash"}):
            raise ValueError(f"frontier active plan hash changed: {plan_path}")
        if _portable(plan_path) in seen_plans:
            raise ValueError("frontier source map repeats a plan")
        seen_plans.add(_portable(plan_path))
        conditions = {
            _condition_id(item): item
            for item in plan.get("conditions", [])  # type: ignore[union-attr]
            if isinstance(item, dict)
        }
        models = list(map(str, source.get("models", [])))
        row_paths = source.get("rows")
        row_hashes = source.get("rows_sha256")
        if (
            len(models) != 1
            or not isinstance(row_paths, list)
            or not isinstance(row_hashes, list)
            or len(row_paths) != len(row_hashes)
            or source.get("row_artifacts")
            != [
                {"path": str(path), "sha256": str(digest)}
                for path, digest in zip(row_paths, row_hashes, strict=True)
            ]
        ):
            raise ValueError("frontier selected row binding is malformed")
        observed: set[str] = set()
        for raw, expected_sha in zip(row_paths, row_hashes, strict=True):
            path = _resolve(raw)
            if expected_sha != _sha(path):
                raise ValueError(f"frontier selected rows changed: {path}")
            rows = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            for row in rows:
                identifier = str(row.get("condition_id"))
                if (
                    identifier not in conditions
                    or identifier in observed
                    or _model(row) != models[0]
                    or row.get("plan_hash") != plan_hash
                    or row.get("confirmatory_weight", 0) != 0
                    or row.get("attempt", 1) != 1
                ):
                    raise ValueError(f"frontier selected rows do not match {plan_path}")
                observed.add(identifier)
            group = (
                "frontier_discovery"
                if block == "core" and "discovery" in plan_path.name
                else FRONTIER_GROUPS[block][0]
            )
            groups[group].append({
                "path": _portable(path),
                "sha256": _sha(path),
                "rows": len(rows),
                "model": models[0],
                "plan_path": _portable(plan_path),
                "plan_sha256": _sha(plan_path),
                "plan_hash": plan_hash,
            })
        if observed != set(conditions):
            raise ValueError(f"frontier selected rows do not close {plan_path}")
    # The manifest builder consumes the source map's hash-bound order. Sorting
    # by row path would place continuation rows before their original plan.
    return groups


def rebuild_release_inputs(
    directory: Path, manifest_path: Path, panel_path: Path, source_map_path: Path
) -> tuple[Path, Path]:
    manifest = _read(manifest_path)
    panel = _read(panel_path)
    _validate_manifest(manifest, panel_path)
    source_map = _read(source_map_path)
    frontier = _frontier_entries(source_map)
    if "anthropic/claude-opus-5" in manifest["models"]:
        from build_opus_release_source_map import DEFAULT_OUTPUT as OPUS_MAP, validate_source_map
        from build_release_analysis_manifest import validate_plan_sources
        opus = validate_source_map(OPUS_MAP, ROOT)
        if {"path": _portable(OPUS_MAP), "sha256": _sha(OPUS_MAP)} not in manifest["source_manifests"]:
            raise ValueError("release manifest does not bind its complete Opus source map")
        for source in opus["sources"]:
            entries = validate_plan_sources([{"plan": _resolve(source["plan"]),
                "rows": [_resolve(path) for path in source["rows"]], "models": {"anthropic/claude-opus-5"}}],
                verify_plan_inputs=False)
            frontier[source["ledger_group"]].extend(entries)
    rebuilt = dict(manifest)
    inputs = {key: list(value) for key, value in manifest["inputs"].items()}  # type: ignore[union-attr]
    for name, entries in frontier.items():
        inputs[name] = entries
    rebuilt["inputs"] = inputs
    expected = dict(manifest["expected_rows"])  # type: ignore[arg-type]
    for name, entries in frontier.items():
        expected[name] = sum(int(item["rows"]) for item in entries)
    rebuilt["expected_rows"] = expected
    rebuilt["total_rows"] = sum(int(value) for value in expected.values())
    rebuilt.pop("manifest_hash", None)
    rebuilt["manifest_hash"] = stable_hash(rebuilt)
    if rebuilt != manifest:
        raise RuntimeError("final release manifest is not reproducible from selected frontier sources")
    if "anthropic/claude-opus-5" in manifest["models"]:
        from build_release_analysis_manifest import build_from_release_seed
        generated_panel, generated_manifest = build_from_release_seed(OPUS_MAP)
        generated_manifest["panel_path"] = manifest["panel_path"]
        generated_manifest["panel_sha256"] = _sha(panel_path)
        generated_manifest["manifest_hash"] = stable_hash(generated_manifest)
        if generated_panel != panel or generated_manifest != manifest:
            raise RuntimeError("unified panel or manifest differs from complete-inventory regeneration")
    panel_output = directory / "release-panel.json"
    manifest_output = directory / "release-analysis-inputs.json"
    panel_output.write_text(canonical_json(panel) + "\n", encoding="utf-8")
    manifest_output.write_text(canonical_json(rebuilt) + "\n", encoding="utf-8")
    if _read(panel_output) != panel or _read(manifest_output) != manifest:
        raise RuntimeError("temporary release input serialization changed semantics")
    return panel_output, manifest_output


def _equivalent_json(left: Path, right: Path) -> bool:
    return _read(left) == _read(right)


def replay(
    *, manifest_path: Path, panel_path: Path, source_map_path: Path
) -> list[dict[str, object]]:
    with tempfile.TemporaryDirectory(prefix="bottleneckbench-release-replay-") as raw:
        directory = Path(raw)
        rebuilt_panel, rebuilt_manifest = rebuild_release_inputs(
            directory, manifest_path, panel_path, source_map_path
        )
        output = directory / "analysis"
        command = [
            sys.executable,
            str(RUN_ANALYSIS),
            "--manifest",
            _portable(manifest_path),
            "--panel",
            _portable(panel_path),
            "--output-root",
            str(output),
        ]
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
            capture_output=True,
            text=True,
        )
        if completed.returncode:
            raise RuntimeError(
                f"release analysis replay failed:\n{completed.stderr or completed.stdout}"
            )
        names = list(BASE_OUTPUTS)
        inputs = _read(manifest_path).get("inputs")
        if isinstance(inputs, dict) and "targeted_vw_replication" in inputs:
            names.append("targeted-vw-replication.json")
        checks = [
            {
                "name": "release-panel-rebuild",
                "passed": _read(rebuilt_panel) == _read(panel_path),
                "expected_sha256": _sha(panel_path),
                "replay_sha256": _sha(rebuilt_panel),
                "comparison": "semantic_json",
            },
            {
                "name": "release-manifest-rebuild",
                "passed": _read(rebuilt_manifest) == _read(manifest_path),
                "expected_sha256": _sha(manifest_path),
                "replay_sha256": _sha(rebuilt_manifest),
                "comparison": "semantic_json",
            },
        ]
        checks.extend({
            "name": f"release:{name}",
            "passed": _equivalent_json(ANALYSIS / name, output / name),
            "expected_sha256": _sha(ANALYSIS / name),
            "replay_sha256": _sha(output / name),
            "comparison": "semantic_json",
        } for name in names)
        from replay_policy_pilot import replay as replay_policy
        checks.extend(replay_policy())
        return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--panel", type=Path, default=DEFAULT_PANEL)
    parser.add_argument("--source-map", type=Path, default=DEFAULT_SOURCE_MAP)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        manifest = _read(args.manifest)
        _validate_manifest(manifest, args.panel)
        print('{"passed":true}')
        return 0
    checks = replay(
        manifest_path=args.manifest,
        panel_path=args.panel,
        source_map_path=args.source_map,
    )
    report = {
        "schema": "bottleneckbench-release-reproducibility-v1",
        "passed": all(bool(item["passed"]) for item in checks),
        "checks": checks,
        "release_manifest_sha256": _sha(args.manifest),
        "release_panel_sha256": _sha(args.panel),
        "active_route_pricing_sha256": _sha(ACTIVE_ROUTE_PRICES),
        "source_map_sha256": _sha(args.source_map),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(canonical_json({"passed": report["passed"], "checks": len(checks)}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
