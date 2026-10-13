#!/usr/bin/env python3
"""Estimate token cost for every final-panel evidence block."""

from __future__ import annotations

import argparse
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
from typing import Mapping

from analyze_model_system_decisions import RowCosts, _bound_row_cost, _prices, _row_cost_models
from build_final_analysis_manifest import _model
from bottleneckbench.core.schema import normalize_floats


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
MANIFEST = ROOT / "results/analysis/release-analysis-inputs.json"
PANEL = ROOT / "results/analysis/release-panel.json"
OPENROUTER_PRICES = HERE / "openrouter-endpoint-snapshot.json"
GOOGLE_PRICES = HERE / "google-cloud-price-snapshot.json"
CANDIDATE_PRICES = ROOT / "configs/pricing-2026-08-30.json"
OUTPUT = ROOT / "results/analysis/final-panel-cost.json"


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _usage(row: Mapping[str, object]) -> tuple[float, float] | None:
    usage = row.get("usage")
    efficiency = row.get("efficiency")
    if isinstance(efficiency, dict) and efficiency.get("usage_missing_responses"):
        return None
    if not isinstance(usage, dict):
        return None
    prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in (prompt, completion)):
        return None
    return float(prompt), float(completion)


def _blank() -> dict[str, float]:
    return {
        "rows": 0.0,
        "rows_with_usage": 0.0,
        "prompt_tokens": 0.0,
        "completion_tokens": 0.0,
        "estimated_cost_usd": 0.0,
    }


def analyze(
    manifest: Mapping[str, object], prices: Mapping[str, tuple[float, float]],
    *, row_costs: RowCosts | None = None,
) -> dict[str, object]:
    categories: dict[str, object] = {}
    totals: dict[str, dict[str, float]] = defaultdict(_blank)
    for category, entries in manifest["inputs"].items():  # type: ignore[union-attr]
        report: dict[str, dict[str, float]] = defaultdict(_blank)
        for entry in entries:
            path = ROOT / str(entry["path"])
            if _sha(path) != entry["sha256"]:
                raise ValueError(f"analysis input changed after manifest freeze: {path}")
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            if len(rows) != entry["rows"]:
                raise ValueError(f"analysis input row count changed: {path}")
            for row in rows:
                model = _model(row)
                for target in (report[model], totals[model]):
                    target["rows"] += 1
                usage = _usage(row)
                bound_cost = _bound_row_cost(row, model, row_costs)
                if bound_cost is not None and usage is None:
                    raise ValueError("bound row cost requires complete row usage")
                if usage is None:
                    continue
                prompt, completion = usage
                cost = bound_cost if bound_cost is not None else prompt * prices[model][0] + completion * prices[model][1]
                for target in (report[model], totals[model]):
                    target["rows_with_usage"] += 1
                    target["prompt_tokens"] += prompt
                    target["completion_tokens"] += completion
                    target["estimated_cost_usd"] += cost
        rows = sum(item["rows"] for item in report.values())
        covered = sum(item["rows_with_usage"] for item in report.values())
        categories[str(category)] = {
            "rows": int(rows),
            "rows_with_usage": int(covered),
            "usage_coverage": covered / rows,
            "estimated_cost_usd": sum(item["estimated_cost_usd"] for item in report.values()),
            "models": dict(sorted(report.items())),
        }
    rows = sum(item["rows"] for item in totals.values())
    covered = sum(item["rows_with_usage"] for item in totals.values())
    cost_models = _row_cost_models(row_costs, frozenset(totals))
    if row_costs is not None and set(totals) - set(prices) - cost_models:
        raise ValueError("price snapshots and row costs do not cover the analysis panel")
    return normalize_floats({
        "analysis": "capacity-surface-final-panel-cost-v1",
        "role": "descriptive token accounting",
        "rows": int(rows),
        "rows_with_usage": int(covered),
        "usage_coverage": covered / rows,
        "estimated_cost_usd": sum(item["estimated_cost_usd"] for item in totals.values()),
        "cost_is_lower_bound": covered < rows,
        "categories": categories,
        "models": dict(sorted(totals.items())),
    })  # type: ignore[return-value]


def self_check() -> None:
    assert _usage({"usage": {"prompt_tokens": 2, "completion_tokens": 3}}) == (2.0, 3.0)
    assert _usage({"usage": {"prompt_tokens": 2}}) is None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--panel", type=Path, default=PANEL)
    parser.add_argument("--openrouter-prices", type=Path, default=OPENROUTER_PRICES)
    parser.add_argument("--google-prices", type=Path, default=GOOGLE_PRICES)
    parser.add_argument("--candidate-prices", type=Path, action="append")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        print('{"passed":true}')
        return 0
    manifest = _object(args.manifest)
    unhashed = dict(manifest)
    supplied_hash = unhashed.pop("manifest_hash", None)
    if supplied_hash != sha256(json.dumps(unhashed, sort_keys=True, separators=(",", ":")).encode()).hexdigest():
        raise ValueError("final analysis manifest hash mismatch")
    panel = _object(args.panel)
    candidate_paths = args.candidate_prices or [CANDIDATE_PRICES]
    candidate_price_rows: dict[str, object] = {}
    for path in candidate_paths:
        for model, price in _object(path)["models"].items():  # type: ignore[index,union-attr]
            prior = candidate_price_rows.get(str(model))
            if prior is not None and (
                float(prior["input"]), float(prior["output"])  # type: ignore[index]
            ) != (float(price["input"]), float(price["output"])):  # type: ignore[index]
                raise ValueError(f"conflicting frozen prices for {model}")
            candidate_price_rows[str(model)] = price
    historical_panel = {
        **panel,
        "configurations": [
            item for item in panel["configurations"]  # type: ignore[index]
            if item["model"] not in candidate_price_rows
        ],
    }
    prices = _prices(historical_panel, _object(args.openrouter_prices), _object(args.google_prices))
    expected_models = {str(item["model"]) for item in panel["configurations"]}  # type: ignore[index]
    for model, price in candidate_price_rows.items():  # type: ignore[union-attr]
        if model in expected_models:
            prices[str(model)] = (float(price["input"]) / 1_000_000, float(price["output"]) / 1_000_000)
    if set(prices) != expected_models:
        raise ValueError("combined price snapshots do not cover the release panel")
    result = analyze(manifest, prices)
    result["manifest_hash"] = supplied_hash
    result["inputs"] = [
        {"path": str(path.resolve().relative_to(ROOT)), "sha256": _sha(path)}
        for path in (
            args.manifest, args.panel, args.openrouter_prices,
            args.google_prices, *candidate_paths,
        )
    ]
    result["analysis_source_sha256"] = _sha(Path(__file__))
    result["artifact_hash"] = sha256(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
