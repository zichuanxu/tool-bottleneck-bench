#!/usr/bin/env python3
"""Fail-closed analysis for the unified workflow-frontier panel."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bottleneckbench.analysis import is_infrastructure_failure  # noqa: E402
from bottleneckbench.workflow_boundary import (  # noqa: E402
    WORKFLOW_BOUNDARY_LOCATOR_LEVELS,
    WORKFLOW_BOUNDARY_STUDY,
)
from bottleneckbench.workflow_boundary_analysis import (  # noqa: E402
    analyze_workflow_boundary,
    load_workflow_boundary_rows,
)
from bottleneckbench.core.schema import canonical_json, stable_hash  # noqa: E402


DEFAULT_INPUT = ROOT / "results/prospective-execution/frontier"
DEFAULT_PANEL = ROOT / "results/analysis/release-panel.json"
DEFAULT_OUTPUT = ROOT / "results/analysis/workflow-frontier-panel.json"


def _validate(
    rows: Sequence[Mapping[str, object]], expected_models: set[str]
) -> dict[str, object]:
    expected_levels = set(WORKFLOW_BOUNDARY_LOCATOR_LEVELS)
    if not expected_models or len(rows) != len(expected_models) * 4 * 4:
        raise ValueError("workflow frontier must close at the panel size by 4 families by 4 levels")
    seen_ids: set[str] = set()
    crossing: dict[tuple[str, str], set[int]] = {}
    for row in rows:
        condition = row.get("condition")
        condition_id = row.get("condition_id")
        if (
            not isinstance(condition, dict)
            or condition.get("study") != WORKFLOW_BOUNDARY_STUDY
            or condition_id != stable_hash(condition)
            or not isinstance(condition_id, str)
            or condition_id in seen_ids
            or is_infrastructure_failure(row)
        ):
            raise ValueError(f"invalid workflow-frontier row: {condition_id}")
        seen_ids.add(condition_id)
        model = str(condition["model"])
        family = str(condition["family_id"])
        if model not in expected_models:
            raise ValueError(f"unexpected workflow-frontier model: {model}")
        crossing.setdefault((model, family), set()).add(int(condition["locator_nodes"]))
    families = {family for _, family in crossing}
    if len(families) != 4 or set(crossing) != {
        (model, family) for model in expected_models for family in families
    } or any(levels != expected_levels for levels in crossing.values()):
        raise ValueError("workflow-frontier model-family-level crossing is incomplete")
    return {
        "rows": len(rows),
        "models": len(expected_models),
        "families": len(families),
        "levels": sorted(expected_levels),
        "unique_condition_ids": len(seen_ids),
    }


def _self_check() -> None:
    models = {f"model-{index}" for index in range(3)}
    rows = []
    for model in sorted(models):
        for family in range(4):
            for level in WORKFLOW_BOUNDARY_LOCATOR_LEVELS:
                condition = {
                    "study": WORKFLOW_BOUNDARY_STUDY,
                    "model": model,
                    "family_id": f"family-{family}",
                    "locator_nodes": level,
                }
                rows.append(
                    {
                        "condition_id": stable_hash(condition),
                        "condition": condition,
                        "status": "verified_success",
                        "verdict": {"verified_success": level == 0},
                    }
                )
    assert _validate(rows, models)["rows"] == 48
    result = analyze_workflow_boundary(rows, bootstrap_draws=20)
    assert result["row_count"] == 48 and len(result["models"]) == 3


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--input", type=Path, action="append")
    parser.add_argument("--panel", type=Path, default=DEFAULT_PANEL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--bootstrap-draws", type=int, default=10_000)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        _self_check()
        print('{"passed":true}')
        return 0
    paths = sorted(args.input or args.input_root.glob("*.jsonl"))
    rows = load_workflow_boundary_rows(paths)
    panel = json.loads(args.panel.read_text(encoding="utf-8"))
    expected_models = {str(model) for model in panel["auxiliary_models"]}
    closure = _validate(rows, expected_models)
    result = analyze_workflow_boundary(rows, bootstrap_draws=args.bootstrap_draws)
    result["complete_panel_contract"] = closure
    result["inputs"] = [
        {
            "path": str(path.resolve().relative_to(ROOT)),
            "sha256": sha256(path.read_bytes()).hexdigest(),
        }
        for path in [*paths, args.panel]
    ]
    result["analysis_source_sha256"] = sha256(Path(__file__).read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(canonical_json(result) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
