#!/usr/bin/env python3
"""Regenerate the complete release analyses without hosted calls."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_PRICES = ROOT / "configs/release-candidate-pricing-2026-09-02.json"
ACTIVE_ROUTE_PRICES = ROOT / "configs/frontier-active-route-pricing-2026-09-02.json"
OPUS_PRICES = ROOT / "configs/opus-5-active-route-pricing-2026-09-03.json"


def _repeat(flag: str, values: list[str]) -> list[str]:
    return [item for value in values for item in (flag, value)]


def _stamp(
    path: Path, *, weight: int, role: str | None = None, scientific_role: str | None = None
) -> None:
    """Write the evidence class of a released analysis into the analysis itself.

    Every released artifact carries `confirmatory_weight` and a prose class, so a
    reader who only has the JSON can tell the classes apart without the paper.
    An artifact that did not already self-hash is not given one here.
    """

    payload = json.loads(path.read_text(encoding="utf-8"))
    if role is not None:
        payload["inference_role"] = role
    if scientific_role is not None:
        payload["scientific_role"] = scientific_role
    payload["confirmatory_weight"] = weight
    if payload.pop("artifact_hash", None) is not None:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        payload["artifact_hash"] = hashlib.sha256(encoded.encode()).hexdigest()
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--panel", type=Path, required=True)
    parser.add_argument("--structural-plan", type=Path)
    parser.add_argument("--toolsandbox-plan", type=Path)
    parser.add_argument("--structural-source", nargs=2, action="append", metavar=("PLAN", "INPUT_ROOT"))
    parser.add_argument("--toolsandbox-source", nargs=2, action="append", metavar=("PLAN", "INPUT_ROOT"))
    parser.add_argument("--output-root", type=Path, default=ROOT / "results/analysis")
    parser.add_argument("--reporting-subset", action="store_true",
                        help="Use prerecomputed subset external/registered analyses in output-root")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    panel = json.loads(args.panel.read_text(encoding="utf-8"))
    blocks = {
        name: [str(item["path"]) for item in entries]
        for name, entries in manifest["inputs"].items()
    }
    infrastructure = manifest["infrastructure_condition_ids"]
    models_by_path = {
        str(item["path"]): str(item["model"])
        for entries in manifest["inputs"].values()
        for item in entries
        if "model" in item
    }
    candidate_structural = set(panel["structural_additional_models"])
    activation = set(panel["activation_models"])
    historical_capacity = blocks["capacity"]
    historical_discovery = blocks["discovery"]
    capacity = historical_capacity + blocks["candidate_capacity"] + blocks.get("frontier_capacity", [])
    discovery = historical_discovery + blocks["candidate_discovery"] + blocks.get("frontier_discovery", [])
    prospective_capacity = [p for p in historical_capacity if models_by_path[p] in activation]
    prospective_discovery = [p for p in historical_discovery if models_by_path[p] in activation]
    retained_capacity = [p for p in capacity if models_by_path[p] not in activation]
    retained_discovery = [p for p in discovery if models_by_path[p] not in activation]
    repair = blocks["repair"] + blocks["candidate_repair"] + blocks.get("frontier_repair", [])
    sentinel = blocks["sentinel"] + blocks["candidate_sentinel"] + blocks.get("frontier_sentinel", [])
    frontier = blocks["workflow_frontier"] + blocks["candidate_workflow_frontier"] + blocks.get("frontier_workflow_frontier", [])
    panel_models = {str(item["model"]) for item in panel["configurations"]}
    route_price_args = _repeat("--candidate-prices", [str(CANDIDATE_PRICES), str(ACTIVE_ROUTE_PRICES)]
        + ([str(OPUS_PRICES)] if "anthropic/claude-opus-5" in panel_models else []))
    if panel_models != {models_by_path[path] for path in capacity}:
        raise ValueError("release panel configurations differ from the capacity inputs")
    extension_models = {
        models_by_path[path]
        for path in blocks["candidate_capacity"] + blocks.get("frontier_capacity", [])
    }
    candidate_menu = {
        str(item["model"])
        for item in panel["configurations"]
        if str(item["model"]) in extension_models and "menu_matched" in item["coverage"]
    }

    frozen_sources = manifest.get("plan_sources", {})
    structural_sources: list[tuple[str, str, tuple[str, ...] | None]] = [
        (str(plan), str(root), None) for plan, root in (args.structural_source or [])
    ]
    toolsandbox_sources: list[tuple[str, str, tuple[str, ...] | None]] = [
        (str(plan), str(root), None) for plan, root in (args.toolsandbox_source or [])
    ]
    if isinstance(frozen_sources, dict):
        structural_sources += [
            (
                str(ROOT / item["plan"]),
                str(ROOT / item["input_root"]),
                tuple(sorted(map(str, item["models"]))),
            )
            for item in frozen_sources.get("structural_additional", [])
        ]
        toolsandbox_sources += [
            (
                str(ROOT / item["plan"]),
                str(ROOT / item["input_root"]),
                tuple(sorted(map(str, item["models"]))),
            )
            for item in frozen_sources.get("menu_intervention", [])
        ]
    if args.structural_plan is not None:
        structural_sources.append((str(args.structural_plan), str(ROOT / "results/candidate-expansion/execution/structural"), None))
    if args.toolsandbox_plan is not None:
        toolsandbox_sources.append((str(args.toolsandbox_plan), str(ROOT / "results/candidate-expansion/execution/toolsandbox"), None))

    def deduplicate_sources(
        sources: list[tuple[str, str, tuple[str, ...] | None]],
    ) -> list[tuple[str, str, tuple[str, ...] | None]]:
        by_path: dict[tuple[str, str], tuple[str, str, tuple[str, ...] | None]] = {}
        for plan, input_root, source_models in sources:
            key = (str(Path(plan).resolve()), str(Path(input_root).resolve()))
            current = by_path.get(key)
            if current is None or (current[2] is None and source_models is not None):
                by_path[key] = (key[0], key[1], source_models)
            elif current[2] is not None and source_models is not None and current[2] != source_models:
                raise ValueError("release manifest assigns conflicting models to one plan source")
        return list(by_path.values())

    holdout_sources: list[tuple[str, str, tuple[str, ...] | None]] = []
    if isinstance(frozen_sources, dict):
        for item in frozen_sources.get("structural_holdout", []):
            root = ROOT / str(item["input_root"])
            holdout_sources.append((
                str(ROOT / item["plan"]),
                str(root.parent if root.name == "active" else root),
                tuple(sorted(map(str, item["models"]))),
            ))
    structural_sources = deduplicate_sources(structural_sources)
    toolsandbox_sources = deduplicate_sources(toolsandbox_sources)
    if not structural_sources or not toolsandbox_sources:
        raise ValueError("release analysis requires structural and ToolSandbox plan sources")
    def source_cli(sources: list[tuple[str, str, tuple[str, ...] | None]]) -> list[str]:
        result: list[str] = []
        for plan, input_root, source_models in sources:
            if source_models is None:
                result.extend(("--source", plan, input_root))
            else:
                result.extend(("--source-spec", plan, input_root, ",".join(source_models)))
        return result

    pooled_structural = deduplicate_sources([*structural_sources, *holdout_sources])
    pooled_structural_models = candidate_structural | {
        str(model) for model in panel["structural_registered_models"]
    }
    structural_cli = source_cli(pooled_structural)
    toolsandbox_cli = source_cli(toolsandbox_sources)

    python = sys.executable
    script = lambda name: str(ROOT / "experiments" / name)  # noqa: E731
    args.output_root.mkdir(parents=True, exist_ok=True)
    commands = [
        [python, script("analyze_unified_core_panel.py"), *_repeat("--capacity-input", capacity),
         *_repeat("--discovery-input", discovery), "--panel", str(args.panel),
         *_repeat("--expected-capacity-infrastructure-id", infrastructure["capacity"]),
         *_repeat("--expected-discovery-infrastructure-id", infrastructure["discovery"]),
         "--output", str(args.output_root / "release-core-panel.json")],
        [python, script("analyze_release_layer_repairs.py"), *_repeat("--prospective-capacity", prospective_capacity),
         *_repeat("--prospective-discovery", prospective_discovery),
         *_repeat("--retained-capacity", retained_capacity), *_repeat("--retained-discovery", retained_discovery),
         *_repeat("--repair", repair), "--panel", str(args.panel),
         *_repeat("--expected-prospective-capacity-infrastructure-id", infrastructure["confirmatory_layer_capacity"]),
         *_repeat("--expected-prospective-discovery-infrastructure-id", infrastructure["confirmatory_layer_discovery"]),
         *_repeat("--expected-retained-capacity-infrastructure-id", infrastructure["retained_layer_capacity"]),
         *_repeat("--expected-retained-discovery-infrastructure-id", infrastructure["retained_layer_discovery"]),
         *_repeat("--expected-repair-infrastructure-id", infrastructure["repair"]),
         "--output", str(args.output_root / "release-layer-repairs.json")],
        [python, script("analyze_prospective_sentinel.py"), *_repeat("--input", sentinel),
         "--panel", str(args.panel), "--output", str(args.output_root / "release-sentinel.json")],
        [python, script("analyze_workflow_frontier_panel.py"), *_repeat("--input", frontier),
         "--panel", str(args.panel), "--output", str(args.output_root / "release-workflow-frontier.json")],
        [python, script("analyze_candidate_structural_release.py"), "--portable-release",
         "--allow-declared-weights", *structural_cli,
         *_repeat("--model", sorted(pooled_structural_models)),
         "--output", str(args.output_root / "structural-repair-pooled.json")],
        [python, script("analyze_candidate_toolsandbox_release.py"), "--portable-release", *toolsandbox_cli,
         *_repeat("--model", sorted(candidate_menu)),
         "--output", str(args.output_root / "candidate-toolsandbox-menu.json")],
        [python, script("analyze_model_system_decisions.py"), *_repeat("--capacity-input", capacity),
         *_repeat("--discovery-input", discovery), *_repeat("--repair-input", repair),
         "--panel", str(args.panel),
         *route_price_args,
         *_repeat("--expected-capacity-infrastructure-id", infrastructure["capacity"]),
         *_repeat("--expected-discovery-infrastructure-id", infrastructure["discovery"]),
         *_repeat("--expected-repair-infrastructure-id", infrastructure["repair"]),
         "--output", str(args.output_root / "release-model-versus-system.json")],
        [python, script("analyze_final_panel_cost.py"), "--manifest", str(args.manifest),
         "--panel", str(args.panel),
         *route_price_args,
         "--output", str(args.output_root / "release-panel-cost.json")],
        [python, script("analyze_failure_localization.py"), "--manifest", str(args.manifest),
         "--output", str(args.output_root / "release-failure-localization.json")],
        [python, script("analyze_failure_localization_v2.py"), "--manifest", str(args.manifest),
         "--output", str(args.output_root / "release-failure-localization-v2.json")],
        [python, script("analyze_crossover_campaign.py"),
         *(["--panel", str(args.panel)] if args.reporting_subset else []),
         "--output", str(args.output_root / "crossover-campaign.json")],
        [python, script("analyze_revision_audit.py"), "--manifest", str(args.manifest),
         *(["--panel", str(args.panel)] if args.reporting_subset else []),
         "--output", str(args.output_root / "revision-evidence-audit.json")],
        [python, script("analyze_interaction_scale_sensitivity.py"),
         *_repeat("--capacity-input", capacity),
         "--panel", str(args.panel),
         "--protocol", str(ROOT / "protocols/interaction-scale-sensitivity-2026-09-01.md"),
         "--output", str(args.output_root / "interaction-scale-sensitivity.json")],
        [python, script("analyze_structural_repair_holdout.py"),
         "--output", str(args.output_root / "structural-repair-holdout.json")],
        [python, script("analyze_structural_model_system_decisions.py"),
         "--manifest", str(args.manifest),
         *route_price_args,
         "--output", str(args.output_root / "structural-model-versus-system.json")],
        [python, script("analyze_failure_mechanisms.py"),
         "--manifest", str(args.manifest), "--panel", str(args.panel),
         "--protocol", str(ROOT / "protocols/failure-mechanism-decomposition-2026-09-02.md"),
         "--output", str(args.output_root / "failure-mechanism-decomposition.json")],
        [python, script("analyze_capacity_sensitivity.py"),
         "--manifest", str(args.manifest), "--panel", str(args.panel),
         "--external", str((args.output_root if args.reporting_subset else ROOT / "results/analysis") / "toolsandbox-external-validation.json"),
         "--protocol", str(ROOT / "protocols/capacity-cluster-sensitivity-2026-09-02.md"),
         "--output", str(args.output_root / "capacity-cluster-sensitivity.json")],
    ]
    if args.reporting_subset:
        commands = [command for command in commands if command[1] != script("analyze_structural_repair_holdout.py")]
    targeted = blocks.get("targeted_vw_replication", [])
    if targeted:
        studies = manifest.get("prospective_studies")
        study = studies.get("targeted_vw", {}) if isinstance(studies, dict) else {}
        if (
            len(targeted) != 1
            or not isinstance(study, dict)
            or study.get("status") != "complete"
            or study.get("scientific_rows") != 1152
            or not isinstance(study.get("plan_path"), str)
        ):
            raise ValueError("release manifest has an invalid complete targeted VxW attachment")
        commands.append([
            python,
            script("targeted_vw_replication.py"),
            "analyze",
            "--plan",
            str(ROOT / str(study["plan_path"])),
            "--rows",
            targeted[0],
            "--output",
            str(args.output_root / "targeted-vw-replication.json"),
        ])
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    for command in commands:
        subprocess.run(command, cwd=ROOT, env=environment, check=True)
    _stamp(
        args.output_root / "release-core-panel.json",
        role=f"primary capacity analysis on the {len(panel['configurations'])}-configuration core panel",
        scientific_role=(
            f"the release's primary capacity analysis: every core contrast is estimated on "
            f"all {len(panel['configurations'])} released configurations under one contrast "
            "family, one Holm correction and one clustering unit"
        ),
        weight=0,
    )
    # The one block that still weighs 1.  It carried the weight but neither prose
    # field, the very fields B6 added to its sibling, so a reader holding only the
    # JSON could not tell why it weighs 1 (review N-15).
    _stamp(
        args.output_root / "structural-repair-pooled.json",
        role=(
            f"held-out structural repair test pooled over all "
            f"{len(pooled_structural_models)} configurations that ran the 24 content-hashed "
            "held-out semantic intents across the same nine cells"
        ),
        scientific_role=(
            f"the release's held-out structural test: a public-handoff controller against a "
            f"matched baseline on 24 content-hashed held-out intents, estimated once over all "
            f"{len(pooled_structural_models)} configurations whose arms and intents are "
            "identical; plan_provenance records, per frozen plan, the declared row weight, "
            "whether the structural outcomes were unseen at freeze, and whether the "
            "configurations were selected on their core outcomes"
        ),
        weight=0,
    )
    _stamp(
        args.output_root / "structural-repair-holdout.json",
        role=(
            f"frozen-plan record for the {len(panel['structural_registered_models'])} reported configurations whose structural plan was "
            "frozen before any configuration was selected on its core outcomes; estimand, "
            "activation rule, clustering unit and the 24 held-out semantic intents were "
            "fixed in a released plan before the first row was acquired"
        ),
        scientific_role=(
            f"the frozen-plan sub-analysis of the pooled held-out structural test: the {len(panel['structural_registered_models'])} reported "
            "configurations whose released rows carry declared weight 1 because their plan "
            "was frozen before the first row and before any configuration was chosen on its "
            f"core outcomes; the pooled {len(pooled_structural_models)}-configuration estimate is the reported test"
        ),
        weight=1,
    )
    for name, scientific_role in (
        (
            "release-sentinel.json",
            "descriptive sentinel audit of scoring stability; no estimand and no hypothesis test",
        ),
        (
            "release-workflow-frontier.json",
            "descriptive workflow-frontier panel; the paired call-level contrasts are auxiliary and are reported outside the core contrast family",
        ),
        (
            "release-layer-repairs.json",
            "layer activation tests estimated once over every released configuration whose arms are complete; layer_configurations records how many configurations entered each layer and configurations_without_repair_arms names the ones a layer could not use, and every sensitivity in this file is descriptive",
        ),
        (
            "release-panel-cost.json",
            "descriptive resource ledger for the released panel; a cost accounting, not an estimand",
        ),
    ):
        _stamp(args.output_root / name, weight=0, scientific_role=scientific_role)
    print(json.dumps({"passed": True, "analyses": len(commands), "rows": manifest["total_rows"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
