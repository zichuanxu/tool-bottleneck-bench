#!/usr/bin/env python3
"""Validate and analyze the final unified Capacity Surface core panel."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from statistics import fmean

from bottleneckbench.analysis import (
    _registered_contrast_values,
    analyze_capacity_rows,
    analyze_discovery_rows,
    is_infrastructure_failure,
)
from bottleneckbench.study_design import PRIMARY_CONTRASTS


JSON = dict[str, object]
ROOT = Path(__file__).resolve().parents[1]
RETAINED_ITT_INFRASTRUCTURE_IDS = frozenset(
    {"fbeab4aed2d7e60bc703cb7b51e1190d13abdbb08b044f2a050f17940adb9db9"}
)


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _object(path: Path) -> JSON:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _rows(paths: Sequence[Path]) -> list[JSON]:
    rows: list[JSON] = []
    for path in paths:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"non-object row at {path}:{number}")
            rows.append(value)
    condition_ids = [row.get("condition_id") for row in rows]
    if any(not isinstance(value, str) or not value for value in condition_ids):
        raise ValueError("core input contains a missing condition ID")
    if len(set(condition_ids)) != len(condition_ids):
        raise ValueError("core input contains duplicate condition IDs")
    return sorted(rows, key=lambda row: str(row["condition_id"]))


def _model(row: Mapping[str, object]) -> str:
    condition = row.get("condition")
    if not isinstance(condition, dict) or not isinstance(condition.get("model"), str):
        raise ValueError("core row is missing condition.model")
    return str(condition["model"])


def _unit(row: Mapping[str, object], key: str) -> str:
    condition = row.get("condition")
    value = condition.get(key) if isinstance(condition, dict) else None
    if not isinstance(value, str) or not value:
        raise ValueError(f"core row is missing condition.{key}")
    return value


def _validate_matrix(
    rows: Sequence[Mapping[str, object]],
    models: Sequence[str],
    *,
    rows_per_model: int,
    unit_key: str,
    units_per_model: int,
    expected_infrastructure_ids: frozenset[str],
) -> None:
    expected_models = set(models)
    counts = Counter(_model(row) for row in rows)
    if set(counts) != expected_models or any(
        counts[model] != rows_per_model for model in expected_models
    ):
        raise ValueError(f"{unit_key} matrix does not close at the frozen model-by-row counts")
    unit_sets = {
        model: {_unit(row, unit_key) for row in rows if _model(row) == model}
        for model in models
    }
    reference = unit_sets[models[0]]
    if len(reference) != units_per_model or any(
        units != reference for units in unit_sets.values()
    ):
        raise ValueError(f"{unit_key} matrix does not share the frozen semantic units")
    observed_infrastructure = {
        str(row["condition_id"]) for row in rows if is_infrastructure_failure(row)
    }
    if observed_infrastructure != set(expected_infrastructure_ids):
        raise ValueError(f"{unit_key} infrastructure set differs from the frozen ITT contract")


def _registered_estimates(rows: Sequence[Mapping[str, object]]) -> dict[str, float]:
    primary = [row for row in rows if str(row.get("study_role")) == "primary"]
    return {
        name: fmean(_registered_contrast_values(primary, terms, cluster_unit="family")[0])
        for name, terms in PRIMARY_CONTRASTS.items()
    }


def retained_itt_infrastructure_sensitivity(
    rows: Sequence[Mapping[str, object]], condition_ids: frozenset[str]
) -> JSON:
    """Re-derive every registered contrast with the retained ITT rows scored as successes.

    The ITT rule keeps a frozen infrastructure failure as a failure.  Appendix B
    claims the numerical impact of that rule is zero on every reported contrast;
    this recodes the retained rows the other way and reports what actually moves.
    """

    as_scored = _registered_estimates(rows)
    recoded = [
        {**row, "verdict": {**row["verdict"], "verified_success": True}}  # type: ignore[dict-item]
        if str(row.get("condition_id")) in condition_ids
        else row
        for row in rows
    ]
    flipped = _registered_estimates(recoded)
    return {
        "confirmatory_weight": 0,
        "recoded_condition_ids": sorted(condition_ids),
        "policy": "score every retained ITT infrastructure row as a verified success and recompute the registered family-mean contrasts; nothing else changes",
        "contrasts": {
            name: {
                "as_scored": as_scored[name],
                "infrastructure_recoded_as_success": flipped[name],
                "delta": flipped[name] - as_scored[name],
            }
            for name in sorted(as_scored)
        },
        "contrasts_that_move": sorted(
            name for name in as_scored if flipped[name] != as_scored[name]
        ),
        "claim_boundary": "a descriptive bound on how much the ITT retention rule can move a printed contrast; it is not an alternative estimate and does not replace the registered analysis",
    }


def _panel_models(panel: Mapping[str, object]) -> list[str]:
    configurations = panel.get("configurations")
    if not isinstance(configurations, list):
        raise ValueError("panel has no configurations")
    models = [
        item.get("model") if isinstance(item, dict) else None for item in configurations
    ]
    if not models or any(not isinstance(model, str) for model in models):
        raise ValueError("unified core panel must contain model configurations")
    if len(set(models)) != len(models):
        raise ValueError("unified core panel contains duplicate models")
    return [str(model) for model in models]


def analyze(
    capacity_rows: Sequence[Mapping[str, object]],
    discovery_rows: Sequence[Mapping[str, object]],
    panel: Mapping[str, object],
    *,
    bootstrap_samples: int,
    seed: int,
    expected_capacity_infrastructure_ids: frozenset[str] = RETAINED_ITT_INFRASTRUCTURE_IDS,
    expected_discovery_infrastructure_ids: frozenset[str] = frozenset(),
) -> JSON:
    models = _panel_models(panel)
    _validate_matrix(
        capacity_rows,
        models,
        rows_per_model=576,
        unit_key="family_id",
        units_per_model=36,
        expected_infrastructure_ids=expected_capacity_infrastructure_ids,
    )
    roles = Counter(str(row.get("study_role")) for row in capacity_rows)
    if roles != {"primary": len(models) * 432, "robustness": len(models) * 144}:
        raise ValueError("capacity study-role counts do not match the frozen panel")
    _validate_matrix(
        discovery_rows,
        models,
        rows_per_model=216,
        unit_key="case_id",
        units_per_model=36,
        expected_infrastructure_ids=expected_discovery_infrastructure_ids,
    )
    report = {
        "analysis": "unified-capacity-surface-core-v1",
        "models": models,
        "capacity_rows": len(capacity_rows),
        "discovery_rows": len(discovery_rows),
        "retained_itt_infrastructure_condition_ids": sorted(
            expected_capacity_infrastructure_ids
        ),
        "retained_discovery_infrastructure_condition_ids": sorted(
            expected_discovery_infrastructure_ids
        ),
        "retained_itt_infrastructure_sensitivity": retained_itt_infrastructure_sensitivity(
            capacity_rows, expected_capacity_infrastructure_ids
        ),
        "capacity": analyze_capacity_rows(
            capacity_rows, bootstrap_samples=bootstrap_samples, seed=seed
        ),
        "discovery": analyze_discovery_rows(
            discovery_rows, bootstrap_samples=bootstrap_samples, seed=seed
        ),
    }
    if (
        expected_capacity_infrastructure_ids != RETAINED_ITT_INFRASTRUCTURE_IDS
        or expected_discovery_infrastructure_ids
    ):
        report["infrastructure_excluded_sensitivity"] = {
            "capacity": analyze_capacity_rows(
                [row for row in capacity_rows if not is_infrastructure_failure(row)],
                bootstrap_samples=bootstrap_samples,
                seed=seed,
            ),
            "discovery": analyze_discovery_rows(
                [row for row in discovery_rows if not is_infrastructure_failure(row)],
                bootstrap_samples=bootstrap_samples,
                seed=seed,
            ),
        }
    return report


def self_check() -> None:
    models = ["m0", "m1"]
    rows = [
        {
            "condition_id": f"{model}-{unit}",
            "condition": {"model": model, "unit": unit},
            "status": "provider_failure" if model == "m0" and unit == "u0" else "complete",
        }
        for model in models
        for unit in ("u0", "u1")
    ]
    _validate_matrix(
        rows,
        models,
        rows_per_model=2,
        unit_key="unit",
        units_per_model=2,
        expected_infrastructure_ids=frozenset({"m0-u0"}),
    )


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capacity-input", type=Path, action="append")
    parser.add_argument("--discovery-input", type=Path, action="append")
    parser.add_argument("--panel", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=20260805)
    parser.add_argument("--expected-capacity-infrastructure-id", action="append")
    parser.add_argument("--expected-discovery-infrastructure-id", action="append")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)
    if args.self_check:
        self_check()
        print('{"passed":true}')
        return 0
    if not args.capacity_input or not args.discovery_input or args.panel is None or args.output is None:
        parser.error("analysis requires capacity inputs, discovery inputs, a panel, and an output")
    capacity_paths = sorted(args.capacity_input, key=str)
    discovery_paths = sorted(args.discovery_input, key=str)
    payload = analyze(
        _rows(capacity_paths),
        _rows(discovery_paths),
        _object(args.panel),
        bootstrap_samples=args.bootstrap_samples,
        seed=args.seed,
        expected_capacity_infrastructure_ids=frozenset(
            args.expected_capacity_infrastructure_id
            if args.expected_capacity_infrastructure_id is not None
            else RETAINED_ITT_INFRASTRUCTURE_IDS
        ),
        expected_discovery_infrastructure_ids=frozenset(
            args.expected_discovery_infrastructure_id or ()
        ),
    )
    payload["bootstrap"] = {"samples": args.bootstrap_samples, "seed": args.seed}
    payload["inputs"] = [
        {
            "role": role,
            "path": str(path.resolve().relative_to(ROOT)),
            "sha256": _file_hash(path),
        }
        for role, paths in (
            ("capacity", capacity_paths),
            ("discovery", discovery_paths),
            ("panel", [args.panel]),
        )
        for path in paths
    ]
    payload["analysis_source_sha256"] = _file_hash(Path(__file__))
    payload["artifact_hash"] = hashlib.sha256(_canonical_json(payload).encode()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(_canonical_json(payload) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
