#!/usr/bin/env python3
"""Scale and exact-intent sensitivity for the core-panel V-by-W effect."""

from __future__ import annotations

import argparse
from collections import defaultdict
from hashlib import sha256
from itertools import product
import json
from math import log
from pathlib import Path
from statistics import fmean
from typing import Mapping, Sequence

from bottleneckbench.analysis import _registered_contrast_values
from bottleneckbench.core.analysis import holm_adjust
from bottleneckbench.core.schema import canonical_json, stable_hash
from bottleneckbench.study_design import PRIMARY_CONTRASTS, StudyRole


ROOT = Path(__file__).resolve().parents[1]


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _success(row: Mapping[str, object]) -> float:
    verdict = row.get("verdict")
    return float(isinstance(verdict, dict) and verdict.get("verified_success") is True)


def exact_sign_flip(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("exact sign flip requires observations")
    observed = abs(fmean(values))
    extreme = sum(
        abs(fmean(sign * value for sign, value in zip(signs, values))) >= observed - 1e-15
        for signs in product((-1.0, 1.0), repeat=len(values))
    )
    return extreme / (2 ** len(values))


def _scale_effect(risks: Mapping[tuple[int, str], float], scale: str) -> float:
    p00, p10 = risks[(8, "low")], risks[(128, "low")]
    p01, p11 = risks[(8, "high")], risks[(128, "high")]
    if scale == "risk_difference":
        return p11 - p10 - p01 + p00
    if scale == "log_risk_ratio":
        return log(p11) - log(p10) - log(p01) + log(p00)
    if scale == "log_odds_ratio":
        logit = lambda value: log(value / (1.0 - value))  # noqa: E731
        return logit(p11) - logit(p10) - logit(p01) + logit(p00)
    raise ValueError(scale)


def analyze(rows: Sequence[Mapping[str, object]], models: Sequence[str]) -> dict[str, object]:
    primary = [row for row in rows if row.get("study_role") == StudyRole.PRIMARY.value]
    if not models or len(primary) != len(models) * 432:
        raise ValueError("interaction sensitivity requires a complete primary matrix for every configuration")
    observed_models = {
        str(row.get("condition", {}).get("model"))  # type: ignore[union-attr]
        for row in primary
    }
    if observed_models != set(models):
        raise ValueError("interaction sensitivity model panel differs from the frozen panel")

    intent_cells: dict[str, dict[tuple[int, str], list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in primary:
        condition = row.get("condition")
        if not isinstance(condition, dict) or condition.get("representation") not in {"raw", "linked"}:
            continue
        coordinate = (int(condition["visible_tools"]), str(condition["workflow"]))
        if coordinate in {(8, "low"), (128, "low"), (8, "high"), (128, "high")}:
            intent_cells[str(condition["intent"])][coordinate].append(_success(row))
    if len(intent_cells) != 12 or any(set(cells) != {(8, "low"), (128, "low"), (8, "high"), (128, "high")} for cells in intent_cells.values()):
        raise ValueError("interaction sensitivity lacks a complete 12-intent four-cell matrix")

    pooled: dict[tuple[int, str], list[float]] = defaultdict(list)
    contributions = {scale: [] for scale in ("risk_difference", "log_risk_ratio", "log_odds_ratio")}
    cell_rows: dict[str, object] = {}
    for intent, cells in sorted(intent_cells.items()):
        raw_risks = {}
        corrected_risks = {}
        for coordinate, values in cells.items():
            pooled[coordinate].extend(values)
            raw_risks[coordinate] = fmean(values)
            corrected_risks[coordinate] = (sum(values) + 0.5) / (len(values) + 1.0)
        contributions["risk_difference"].append(
            _scale_effect(raw_risks, "risk_difference")
        )
        for scale in ("log_risk_ratio", "log_odds_ratio"):
            contributions[scale].append(_scale_effect(corrected_risks, scale))
        cell_rows[intent] = {
            f"V{visible}_{workflow}": {"successes": int(sum(values)), "rows": len(values)}
            for (visible, workflow), values in sorted(cells.items())
        }
    pooled_risks = {coordinate: fmean(values) for coordinate, values in pooled.items()}
    scales = {
        scale: {
            "estimate": _scale_effect(
                pooled_risks if scale == "risk_difference" else {
                    coordinate: (sum(values) + 0.5) / (len(values) + 1.0)
                    for coordinate, values in pooled.items()
                },
                scale,
            ),
            "intent_mean": fmean(values),
            "exact_two_sided_sign_flip_p": exact_sign_flip(values),
            "intents_positive": sum(value > 0 for value in values),
            "intents_negative": sum(value < 0 for value in values),
            "intents_tied": sum(value == 0 for value in values),
        }
        for scale, values in contributions.items()
    }

    exact: dict[str, object] = {}
    raw_p: dict[str, float] = {}
    for name, terms in PRIMARY_CONTRASTS.items():
        values, pairs, incomplete, families = _registered_contrast_values(
            primary, terms, cluster_unit="intent"
        )
        if len(values) != 12 or incomplete:
            raise ValueError(f"registered exact sensitivity is incomplete: {name}")
        value = exact_sign_flip(values)
        raw_p[name] = value
        exact[name] = {
            "estimate": fmean(values),
            "exact_two_sided_sign_flip_p": value,
            "intents": len(values),
            "families": families,
            "configuration_family_contrasts": pairs,
            "intents_positive": sum(item > 0 for item in values),
            "intents_negative": sum(item < 0 for item in values),
            "intents_tied": sum(item == 0 for item in values),
        }
    for name, adjusted in holm_adjust(raw_p).items():
        exact[name]["holm_p"] = adjusted  # type: ignore[index]

    return {
        "schema": "bottleneckbench-interaction-scale-sensitivity-v1",
        "scientific_role": "sensitivity analysis for the primary V-by-W interaction contrast",
        "protocol_deviation": f"The hashed protocol protocols/interaction-scale-sensitivity-2026-09-01.md scoped this analysis to a frozen 12-configuration subset of the core panel. This release computes it over the full {len(models)}-configuration core panel; the eligibility set was widened after the subset's outcomes were known, and the protocol file is retained unmodified.",
        "confirmatory_weight": 0,
        "models": list(models),
        "primary_rows": len(primary),
        "intent_clusters": 12,
        "visible_by_workflow": {
            "cell_risks": {
                f"V{visible}_{workflow}": {"successes": int(sum(values)), "rows": len(values), "risk": fmean(values)}
                for (visible, workflow), values in sorted(pooled.items())
            },
            "scales": scales,
            "jeffreys_correction_for_log_scales": "(successes + 0.5) / (rows + 1)",
        },
        "exact_12_intent_sign_flip": exact,
        "intent_cell_counts": cell_rows,
        "interpretation_boundary": "scale and clustering sensitivity; the primary family-cluster risk-difference analysis on the core panel is unchanged by it",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capacity-input", type=Path, action="append")
    parser.add_argument("--panel", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.capacity_input:
        parser.error("at least one --capacity-input is required")
    panel = json.loads(args.panel.read_text(encoding="utf-8"))
    rows = [
        json.loads(line)
        for path in args.capacity_input
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    result = analyze(rows, [str(item["model"]) for item in panel["configurations"]])
    result["inputs"] = [
        {"path": str(path.resolve().relative_to(ROOT)), "sha256": _sha(path)}
        for path in [*args.capacity_input, args.panel, args.protocol]
    ]
    result["analysis_source_sha256"] = _sha(Path(__file__))
    result["artifact_hash"] = stable_hash(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(canonical_json(result) + "\n", encoding="utf-8")
    print(canonical_json({"passed": True, "rows": result["primary_rows"], "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
