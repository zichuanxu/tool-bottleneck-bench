#!/usr/bin/env python3
"""Registered repeated-request analysis for the prospective sentinel."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import defaultdict
from pathlib import Path
from statistics import fmean, variance
from typing import Iterable, Mapping, Sequence

from bottleneckbench.analysis import is_infrastructure_failure
from bottleneckbench.sentinel import analyze_sentinel_rows


JSON = dict[str, object]
REPEATS = (1, 2, 3)


def _load_jsonl(paths: Sequence[Path]) -> list[JSON]:
    rows: list[JSON] = []
    seen: set[str] = set()
    for path in paths:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"non-object row at {path}:{number}")
            condition_id = value.get("condition_id")
            if not isinstance(condition_id, str) or not condition_id:
                raise ValueError(f"missing condition_id at {path}:{number}")
            if condition_id in seen:
                raise ValueError(f"duplicate condition_id: {condition_id}")
            if is_infrastructure_failure(value):
                raise ValueError(f"infrastructure failure in active input: {condition_id}")
            seen.add(condition_id)
            rows.append(value)
    return rows


def _condition(row: Mapping[str, object]) -> Mapping[str, object]:
    value = row.get("condition")
    if not isinstance(value, dict):
        raise ValueError("sentinel row is missing condition")
    return value


def _success(row: Mapping[str, object]) -> bool:
    verdict = row.get("verdict")
    return isinstance(verdict, dict) and verdict.get("verified_success") is True


def _bootstrap(values: Sequence[float], *, samples: int, seed: int) -> JSON:
    if not values or samples <= 0:
        raise ValueError("bootstrap requires values and positive samples")
    rng = random.Random(seed)
    draws = sorted(fmean(rng.choices(values, k=len(values))) for _ in range(samples))
    return {
        "estimate": fmean(values),
        "ci95": [
            draws[int(0.025 * (samples - 1))],
            draws[int(0.975 * (samples - 1))],
        ],
        "clusters": len(values),
        "bootstrap_samples": samples,
    }


def _beta_binomial_mom(success_counts: Sequence[int], *, trials: int = 3) -> JSON:
    """Method-of-moments beta-binomial intraclass correlation."""

    if len(success_counts) < 2 or any(value < 0 or value > trials for value in success_counts):
        raise ValueError("invalid beta-binomial success counts")
    probability = fmean(success_counts) / trials
    if probability in {0.0, 1.0}:
        return {
            "mean_success_probability": probability,
            "rho_raw": None,
            "rho_constrained": None,
            "groups": len(success_counts),
            "trials_per_group": trials,
            "status": "not_identified_at_boundary",
        }
    raw = (
        variance(success_counts) / (trials * probability * (1.0 - probability)) - 1.0
    ) / (trials - 1)
    return {
        "mean_success_probability": probability,
        "rho_raw": raw,
        "rho_constrained": min(1.0, max(0.0, raw)),
        "groups": len(success_counts),
        "trials_per_group": trials,
        "status": "identified",
    }


def analyze(
    rows: Sequence[Mapping[str, object]],
    *,
    expected_models: int = 12,
    expected_model_ids: set[str] | None = None,
    bootstrap_samples: int = 10_000,
    seed: int = 20260813,
) -> JSON:
    groups: dict[tuple[str, str, str, str], dict[int, bool]] = defaultdict(dict)
    for row in rows:
        condition = _condition(row)
        key = tuple(
            str(condition[name]) for name in ("model", "family_id", "pair", "arm")
        )
        repeat = int(condition["repeat"])
        if repeat not in REPEATS or repeat in groups[key]:
            raise ValueError(f"invalid or duplicate repeat for {key}")
        groups[key][repeat] = _success(row)
    if not groups or any(set(repeats) != set(REPEATS) for repeats in groups.values()):
        raise ValueError("sentinel does not close every three-repeat condition")

    models = sorted({key[0] for key in groups})
    if len(models) != expected_models:
        raise ValueError(f"expected {expected_models} models, observed {len(models)}")
    if expected_model_ids is not None and set(models) != expected_model_ids:
        raise ValueError("sentinel input does not match the frozen auxiliary model set")

    profiles: JSON = {}
    pair_family_gains: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    pair_model_gains: dict[str, list[float]] = defaultdict(list)
    for model_index, model in enumerate(models):
        model_groups = {key: value for key, value in groups.items() if key[0] == model}
        families = sorted({key[1] for key in model_groups})
        family_rates: list[float] = []
        family_disagreements: list[float] = []
        for family in families:
            values = [
                value
                for key, repeats in model_groups.items()
                if key[1] == family
                for value in repeats.values()
            ]
            family_rates.append(fmean(values))
            family_keys = [key for key in model_groups if key[1] == family]
            family_disagreements.append(
                fmean(len(set(model_groups[key].values())) > 1 for key in family_keys)
            )

        pair_profiles: JSON = {}
        for pair_index, pair in enumerate(sorted({key[2] for key in model_groups})):
            family_gains: list[float] = []
            arms: JSON = {}
            for family in families:
                gain = fmean(
                    float(model_groups[(model, family, pair, "repair")][repeat])
                    - float(model_groups[(model, family, pair, "stress")][repeat])
                    for repeat in REPEATS
                )
                family_gains.append(gain)
                pair_family_gains[pair][family].append(gain)
            for arm in ("stress", "repair"):
                arm_counts = [
                    sum(model_groups[(model, family, pair, arm)].values())
                    for family in families
                ]
                arms[arm] = {
                    "pass_at_one": sum(arm_counts) / (len(arm_counts) * len(REPEATS)),
                    "disagreement_rate": fmean(value not in {0, len(REPEATS)} for value in arm_counts),
                    "beta_binomial_overdispersion": _beta_binomial_mom(arm_counts),
                }
            gain_summary = _bootstrap(
                family_gains,
                samples=bootstrap_samples,
                seed=seed + model_index * 101 + pair_index,
            )
            pair_model_gains[pair].append(float(gain_summary["estimate"]))
            pair_profiles[pair] = {
                "repair_minus_stress": gain_summary,
                "arms": arms,
            }

        counts = [sum(value.values()) for value in model_groups.values()]
        profiles[model] = {
            "families": len(families),
            "condition_groups": len(model_groups),
            "pass_at_one": _bootstrap(
                family_rates,
                samples=bootstrap_samples,
                seed=seed + model_index * 101 + 97,
            ),
            "disagreement_rate": _bootstrap(
                family_disagreements,
                samples=bootstrap_samples,
                seed=seed + model_index * 101 + 98,
            ),
            "pass_all_three": fmean(value == len(REPEATS) for value in counts),
            "pass_at_least_one": fmean(value > 0 for value in counts),
            "beta_binomial_overdispersion": _beta_binomial_mom(counts),
            "pairs": pair_profiles,
        }

    panel_pairs: JSON = {}
    for pair_index, pair in enumerate(sorted(pair_family_gains)):
        family_values = [
            fmean(values) for _, values in sorted(pair_family_gains[pair].items())
        ]
        panel_pairs[pair] = {
            "finite_panel_family_clustered": _bootstrap(
                family_values,
                samples=bootstrap_samples,
                seed=seed + 50_000 + pair_index,
            ),
            "configuration_bootstrap_sensitivity": _bootstrap(
                pair_model_gains[pair],
                samples=bootstrap_samples,
                seed=seed + 60_000 + pair_index,
            ),
        }

    return {
        "analysis": "prospective-stochasticity-sentinel-v2",
        "rows": len(rows),
        "configurations": len(models),
        "base_analysis": analyze_sentinel_rows(rows),
        "configuration_profiles": profiles,
        "panel_repair_gains": panel_pairs,
        "inference_note": (
            "Configuration intervals resample semantic families. Panel repair estimates "
            "resample families; configuration bootstraps are finite-panel sensitivity checks, "
            "not random-model population inference. Beta-binomial method-of-moments rho is "
            "descriptive extra-binomial dispersion and includes task heterogeneity."
        ),
    }


def _self_check() -> None:
    rows: list[JSON] = []
    for model in ("m1", "m2"):
        for family_index in range(4):
            family = f"f{family_index}"
            for pair in ("visible_routing", "workflow_verification", "interface_guide"):
                for arm in ("stress", "repair"):
                    for repeat in REPEATS:
                        success = arm == "repair" or (family_index + repeat) % 3 != 0
                        rows.append(
                            {
                                "condition_id": f"{model}-{family}-{pair}-{arm}-{repeat}",
                                "status": "verified_success" if success else "agent_failure",
                                "condition": {
                                    "model": model,
                                    "family_id": family,
                                    "pair": pair,
                                    "arm": arm,
                                    "repeat": repeat,
                                },
                                "verdict": {"verified_success": success},
                            }
                        )
    result = analyze(rows, expected_models=2, bootstrap_samples=200)
    assert result["rows"] == 144
    assert result["configurations"] == 2
    assert set(result["panel_repair_gains"]) == {
        "interface_guide",
        "visible_routing",
        "workflow_verification",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, action="append")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--expected-models", type=int)
    parser.add_argument("--panel", type=Path)
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=20260813)
    parser.add_argument("--self-check", action="store_true")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.self_check:
        _self_check()
        return 0
    if not args.input or args.output is None:
        raise SystemExit("--input and --output are required")
    rows = _load_jsonl(args.input)
    expected_ids = None
    if args.panel is not None:
        panel = json.loads(args.panel.read_text(encoding="utf-8"))
        expected_ids = {str(model) for model in panel["auxiliary_models"]}
    expected_models = args.expected_models if args.expected_models is not None else len(expected_ids or set())
    if expected_models < 1:
        raise ValueError("expected model count must come from --panel or --expected-models")
    result = analyze(
        rows,
        expected_models=expected_models,
        expected_model_ids=expected_ids,
        bootstrap_samples=args.bootstrap_samples,
        seed=args.seed,
    )
    result["inputs"] = [
        {
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in args.input
    ]
    result["analysis_source_sha256"] = hashlib.sha256(
        Path(__file__).read_bytes()
    ).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
