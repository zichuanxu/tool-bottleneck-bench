#!/usr/bin/env python3
"""Run layer-repair analysis on the released core panel."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from analyze_layer_repairs import _load_jsonl, analyze


def _paths(parser: argparse.ArgumentParser, name: str) -> None:
    parser.add_argument(name, type=Path, action="append", required=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    for name in (
        "--prospective-capacity", "--prospective-discovery", "--retained-capacity",
        "--retained-discovery", "--repair",
    ):
        _paths(parser, name)
    parser.add_argument("--panel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-prospective-capacity-infrastructure-id", action="append")
    parser.add_argument("--expected-prospective-discovery-infrastructure-id", action="append")
    parser.add_argument("--expected-retained-capacity-infrastructure-id", action="append")
    parser.add_argument("--expected-retained-discovery-infrastructure-id", action="append")
    parser.add_argument("--expected-repair-infrastructure-id", action="append")
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    parser.add_argument("--randomization-samples", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=20260813)
    args = parser.parse_args()

    def load(name: str, ids_name: str) -> list[dict[str, object]]:
        return _load_jsonl(
            getattr(args, name),
            expected_infrastructure_ids=frozenset(getattr(args, ids_name) or ()),
        )

    prospective_capacity = load(
        "prospective_capacity", "expected_prospective_capacity_infrastructure_id"
    )
    prospective_discovery = load(
        "prospective_discovery", "expected_prospective_discovery_infrastructure_id"
    )
    retained_capacity = load(
        "retained_capacity", "expected_retained_capacity_infrastructure_id"
    )
    retained_discovery = load(
        "retained_discovery", "expected_retained_discovery_infrastructure_id"
    )
    repair_rows = load("repair", "expected_repair_infrastructure_id")
    panel = json.loads(args.panel.read_text(encoding="utf-8"))
    organizations = {
        str(item["model"]): str(item["organization"])
        for item in panel["configurations"]
    }
    result = analyze(
        prospective_capacity=prospective_capacity,
        prospective_discovery=prospective_discovery,
        all_capacity=[*retained_capacity, *prospective_capacity],
        all_discovery=[*retained_discovery, *prospective_discovery],
        repair_rows=repair_rows,
        organizations=organizations,
        eligible_models=set(organizations),
        activation_models=set(panel["activation_models"]),
        auxiliary_models=set(panel["auxiliary_models"]),
        bootstrap_samples=args.bootstrap_samples,
        randomization_samples=args.randomization_samples,
        seed=args.seed,
    )
    result["analysis"] = "layer-selective-diagnosis-to-repair-release-v1"
    layer_counts = result["layer_configurations"]
    result["inference_roles"] = {
        "primary_activation": (
            "layer activation tests over every released configuration whose arms are complete: "
            + ", ".join(
                f"{count} for {layer.replace('_', ' ')}"
                for layer, count in sorted(layer_counts.items(), key=lambda item: -item[1])
            )
        ),
        "candidate_extension_confirmatory_weight": 0,
        "frontier_extension_confirmatory_weight": 0,
    }
    result["inputs"] = [
        {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for paths in (
            args.prospective_capacity, args.prospective_discovery, args.retained_capacity,
            args.retained_discovery, args.repair, [args.panel],
        )
        for path in paths
    ]
    result["analysis_source_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    payload = json.dumps(result, sort_keys=True, separators=(",", ":"))
    result["artifact_hash"] = hashlib.sha256(payload.encode()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
