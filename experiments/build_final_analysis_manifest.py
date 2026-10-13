#!/usr/bin/env python3
"""Bind the reviewer-facing analyses to the final unified model panel."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from typing import Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "experiments/eligible-analysis-panel.json"
OUTPUT = ROOT / "experiments/final-analysis-inputs.json"
def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _rows(path: Path) -> list[dict[str, object]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    if not rows or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"empty or malformed JSONL: {path}")
    return rows


def _model(row: Mapping[str, object]) -> str:
    condition = row.get("condition")
    value = condition.get("model") if isinstance(condition, dict) else row.get("model")
    if not isinstance(value, str) or not value:
        raise ValueError("analysis row lacks a model identifier")
    return value


def _entries(paths: Iterable[Path], eligible: set[str]) -> list[dict[str, object]]:
    entries = []
    seen: set[str] = set()
    for path in sorted(set(paths)):
        rows = _rows(path)
        models = {_model(row) for row in rows}
        if len(models) != 1 or not models <= eligible:
            continue
        identifiers = {str(row.get("condition_id")) for row in rows}
        if "None" in identifiers or len(identifiers) != len(rows) or seen & identifiers:
            raise ValueError(f"duplicate or missing condition ID: {path}")
        seen.update(identifiers)
        entries.append(
            {
                "path": str(path.relative_to(ROOT)),
                "sha256": _sha(path),
                "rows": len(rows),
                "model": next(iter(models)),
            }
        )
    return entries


def build() -> dict[str, object]:
    panel = json.loads(PANEL.read_text(encoding="utf-8"))
    models = {str(item["model"]) for item in panel["configurations"]}
    auxiliary = {str(model) for model in panel["auxiliary_models"]}
    expected_rows = {
        "capacity": len(models) * 576,
        "discovery": len(models) * 216,
        "repair": len(auxiliary) * 144,
        "sentinel": len(auxiliary) * 72,
        "workflow_frontier": len(auxiliary) * 16,
        "external": len(auxiliary) * 32,
        "structural_holdout": 864,
    }
    core_paths = [
        path
        for directory in (
            ROOT / "results/main/pipelined",
            ROOT / "results/model-generalization/main",
            ROOT / "results/prospective-execution/core",
        )
        for path in directory.glob("*.jsonl")
    ]
    categories = {
        "capacity": _entries(
            (path for path in core_paths if "discovery" not in path.name), models
        ),
        "discovery": _entries(
            (path for path in core_paths if "discovery" in path.name), models
        ),
        "repair": _entries(
            (ROOT / "results/prospective-execution/repair").glob("*.jsonl"),
            auxiliary,
        ),
        "sentinel": _entries(
            (ROOT / "results/prospective-execution/sentinel").glob("*.jsonl"),
            auxiliary,
        ),
        "workflow_frontier": _entries(
            (ROOT / "results/prospective-execution/frontier").glob("*.jsonl"),
            auxiliary,
        ),
        "external": _entries(
            (ROOT / "results/prospective-execution/external-validation").glob("*.jsonl"),
            auxiliary,
        ),
        "structural_holdout": _entries(
            (ROOT / "results/prospective-execution/structural-repair-holdout/active").glob("*.jsonl"),
            models,
        ),
    }
    observed = {
        category: sum(int(item["rows"]) for item in entries)
        for category, entries in categories.items()
    }
    if observed != expected_rows:
        raise ValueError(f"final analysis inputs do not close: {observed}")
    payload: dict[str, object] = {
        "schema": "bottleneckbench-final-analysis-inputs-v1",
        "panel_path": str(PANEL.relative_to(ROOT)),
        "panel_sha256": _sha(PANEL),
        "models": [str(item["model"]) for item in panel["configurations"]],
        "auxiliary_models": [str(model) for model in panel["auxiliary_models"]],
        "expected_rows": expected_rows,
        "inputs": categories,
        "generator_sha256": _sha(Path(__file__)),
    }
    payload["manifest_hash"] = sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return payload


def self_check() -> None:
    assert _model({"condition": {"model": "m"}}) == "m"
    assert _model({"model": "m"}) == "m"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        print('{"passed":true}')
        return 0
    if args.output.exists():
        raise RuntimeError(f"refusing to overwrite frozen manifest: {args.output}")
    payload = build()
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"output": str(args.output), "rows": payload["expected_rows"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
