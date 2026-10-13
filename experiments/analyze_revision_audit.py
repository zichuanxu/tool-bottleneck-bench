#!/usr/bin/env python3
"""Exploratory paired diagnostics and missing-outcome bounds, using saved rows.

No prior analysis, frozen plan or raw row is changed. The full verdict vector
describes observed contract satisfaction, not nine independent causal faults.
Bootstrap intervals condition on the existing leave-one-intent-out predictions;
they do not refit the decoder or represent new configurations/environments.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import random
from statistics import fmean

if __package__:
    from . import analyze_failure_localization_v2 as loc
    from . import analyze_crossover_campaign as cross
else:
    import analyze_failure_localization_v2 as loc
    import analyze_crossover_campaign as cross

ROOT = Path(__file__).resolve().parents[1]
OBLIGATIONS = ("availability", "selection", "workflow", "answer", "evidence",
               "safety", "governance", "budget", "completion")
METHODS = (*loc.FEATURE_SETS, "trace_only", "calls_only", "verdict_vector")
COMPARISONS = (
    ("obligation_termination", "obligation"),
    ("with_trace_shape", "obligation"),
    ("with_trace_shape", "obligation_termination"),
    ("with_trace_shape", "trace_only"),
    ("with_trace_shape", "calls_only"),
)


def extra_features(row: dict) -> dict[str, str]:
    shape = loc._trace_shape(row)
    verdict = row.get("verdict") or {}
    # '?' is unavailable, never silently imputed as a failed obligation.
    vector = "".join("1" if verdict.get(k) is True else
                     "0" if verdict.get(k) is False else "?" for k in OBLIGATIONS)
    return {"trace_only": shape, "calls_only": shape.split(";")[0],
            "verdict_vector": vector}


def interval(values: list[float], alpha: float = .05) -> list[float]:
    ordered = sorted(values)
    return [ordered[int(alpha / 2 * (len(ordered) - 1))],
            ordered[int((1 - alpha / 2) * (len(ordered) - 1))]]


def paired_metrics(packets: list[dict], predictions: dict[str, list[str]],
                   samples: int, seed: int) -> tuple[dict, dict]:
    """Resample identical intent clusters for every method, never independent CIs."""
    intents = sorted({p["intent"] for p in packets})
    truth = [p["label"] for p in packets]
    support = {i: Counter(p["label"] for p in packets if p["intent"] == i)
               for i in intents}
    correct = {m: {i: Counter(p["label"] for p, guess in zip(packets, guesses)
                             if p["intent"] == i and p["label"] == guess)
                   for i in intents} for m, guesses in predictions.items()}
    metrics = {m: loc._metrics(truth, guesses) for m, guesses in predictions.items()}
    draws = {m: [] for m in predictions}
    rng = random.Random(seed)
    missing_class_draws = 0
    for _ in range(samples):
        chosen = rng.choices(intents, k=len(intents))
        n = {label: sum(support[i][label] for i in chosen) for label in loc.LABELS}
        if any(v == 0 for v in n.values()):
            missing_class_draws += 1
            continue
        for m in predictions:
            draws[m].append(fmean(sum(correct[m][i][label] for i in chosen) / n[label]
                                 for label in loc.LABELS))
    if not draws[next(iter(draws))]:
        raise ValueError("no bootstrap draw contains every diagnostic class")
    for m in metrics:
        metrics[m]["balanced_accuracy_ci95"] = interval(draws[m])
    contrasts = {}
    for a, b in COMPARISONS:
        deltas = [100 * (x - y) for x, y in zip(draws[a], draws[b])]
        contrasts[f"{a}_minus_{b}"] = {
            "estimate_pp": 100 * (metrics[a]["balanced_accuracy"] - metrics[b]["balanced_accuracy"]),
            "paired_ci95_pp": interval(deltas),
            "bonferroni_ci99_pp": interval(deltas, .05 / len(COMPARISONS)),
        }
    return metrics, {"contrasts": contrasts, "bootstrap_samples": samples,
                     "seed": seed, "missing_class_draws": missing_class_draws,
                     "cluster": "intent", "predictions_refitted": False,
                     "multiplicity": "five exploratory comparisons; 99% percentile intervals use Bonferroni nominal 95% familywise coverage, subject to bootstrap validity"}


def diagnostics(manifest: dict, samples: int, seed: int) -> dict:
    paths = loc._paths(manifest)
    packets, _, _ = loc._packets(paths)
    cells = defaultdict(dict)
    for path in paths:
        for line in path.read_text().splitlines():
            row = json.loads(line)
            c = row["condition"]
            cell = (int(c["visible_tools"]), c["workflow"], c["representation"])
            if row.get("study_role") == "primary" and cell in loc.CELLS.values():
                key = (c["model"], c["family_id"])
                if cell in cells[key]:
                    raise ValueError(f"duplicate diagnostic cell: {key}|{cell}")
                cells[key][cell] = row
    categories = Counter()
    challenge = []
    for (model, family), rows in sorted(cells.items()):
        if set(rows) != set(loc.CELLS.values()):
            raise ValueError("incomplete diagnostic packet")
        base = rows[loc.CELLS["baseline"]]
        if any(loc.is_infrastructure_failure(r) for r in rows.values()):
            category = "infrastructure"
        elif not loc._success(base):
            category = "baseline_failed"
        else:
            failed = [label for label in loc.LABELS if not loc._success(rows[loc.CELLS[label]])]
            category = "all_stresses_passed" if not failed else "single_failure" if len(failed) == 1 else "multiple_failures"
            if len(failed) > 1:
                for label in failed:
                    row = rows[loc.CELLS[label]]
                    challenge.append({"model": model, "family": family,
                                      "intent": base["condition"]["intent"], "label": label,
                                      "features": {**loc._features(row), **extra_features(row)}})
        categories[category] += 1
    for p in packets:
        row = cells[p["model"], p["family"]][loc.CELLS[p["label"]]]
        p["features"].update(extra_features(row))
    predictions = {m: loc._predict(packets, m) for m in METHODS}
    methods, paired = paired_metrics(packets, predictions, samples, seed)
    # Challenge generalization: train only on single-failure packets, with the
    # entire challenge intent absent. Multiple rows share a packet; no iid CI.
    challenge_predictions = {m: [] for m in METHODS}
    for p in challenge:
        train = [r for r in packets if r["intent"] != p["intent"]]
        prior = loc._majority(r["label"] for r in train)
        for m in METHODS:
            labels = [r["label"] for r in train if r["features"][m] == p["features"][m]]
            challenge_predictions[m].append(loc._majority(labels, prior))
    return {
        "population": dict(sorted(categories.items())), "total_packets": len(cells),
        "selected_packets": len(packets), "labels": dict(Counter(p["label"] for p in packets)),
        "intent_clusters": len({p["intent"] for p in packets}), "methods": methods, **paired,
        "decoder": "Exact-feature-tuple training majority; ties V then W then D; unseen tuple falls back to training majority. Leave one entire intent out; no tuning.",
        "verdict_vector_note": "Observed nine-bit contract satisfaction; downstream zeros after early termination are unmet obligations, not distinct causal faults; '?' denotes absent values.",
        "oof_predictions": [{**{k: p[k] for k in ("model", "family", "intent", "label")},
                             "features": p["features"],
                             "predictions": {m: predictions[m][i] for m in METHODS}}
                            for i, p in enumerate(packets)],
        "multiple_failure_challenge": {
            "packets": categories["multiple_failures"], "failed_stress_rows": len(challenge),
            "labels": dict(Counter(p["label"] for p in challenge)),
            "methods": {m: loc._metrics([p["label"] for p in challenge], v)
                        for m, v in challenge_predictions.items()},
            "interpretation": "Descriptive out-of-selected-population test on each failed stress row; assigned pressure labels, not causal ground truth or multilabel repair decisions.",
        },
    }


def outcome_bounds(cells: list[tuple[float, bool | None]]) -> list[float]:
    """Sharp finite-sample linear bounds: each missing binary outcome is in [0,1]."""
    known = sum(w * y for w, y in cells if y is not None)
    return [known + sum(min(w, 0) for w, y in cells if y is None),
            known + sum(max(w, 0) for w, y in cells if y is None)]


def crossover_audit(samples: int, seed: int) -> dict:
    accounting = cross._execution_accounting()
    quarantine = set(map(tuple, accounting["quarantined"]))
    removed_models = {cross.SLUGS[slug] for slug, _ in quarantine}
    missingness = defaultdict(Counter)
    blocks = defaultdict(dict)
    sources = []
    for path in sorted(cross.EXECUTION.glob("repeat-*/*.jsonl")):
        slug, arm = path.stem.rsplit("-", 1)
        if slug not in cross.SLUGS:
            continue
        model, repeat = cross.SLUGS[slug], int(path.parent.name.split("-")[1])
        sources.append({"path": str(path.relative_to(ROOT)), "sha256": sha256(path.read_bytes()).hexdigest()})
        seen = set()
        for line in path.read_text().splitlines():
            row = json.loads(line)
            c = row["condition"]
            cell = (int(c["visible_tools"]), c["workflow"], c["representation"])
            identity = (c["family_id"], *cell)
            if identity in seen:
                raise ValueError(f"duplicate crossover condition in {path.name}")
            seen.add(identity)
            excluded = cross._excluded(row)
            key = (model, arm, repeat, *cell)
            missingness[key]["observed"] += 1
            missingness[key]["excluded"] += int(excluded)
            # Eight corner cells only: four equal-weight V contrasts per family.
            if cell[0] in (8, 128) and cell[1] in ("low", "high") and cell[2] in ("raw", "linked"):
                block_key = (model, repeat, c["family_id"], c["intent"], cell[1], cell[2])
                blocks[block_key][arm, cell[0]] = None if excluded else cross._success(row)
    summary = []
    for stratum in ("B", "S", "N"):
        models = sorted(m for m, s in cross.STRATA.items() if s == stratum and m not in removed_models)
        chosen = {k: v for k, v in blocks.items() if k[0] in models}
        n = len(chosen)
        if not n:
            continue
        expected_keys = {(a, v) for a in cross.ARMS for v in (8, 128)}
        if any(set(b) != expected_keys for b in chosen.values()):
            raise ValueError("non-quarantined crossover block has an unacquired arm")
        estimates = {}
        weights = {"cap1024": {("cap1024", 128): 1, ("cap1024", 8): -1},
                   "cap4096": {("cap4096", 128): 1, ("cap4096", 8): -1},
                   "interaction": {("cap4096", 128): 1, ("cap4096", 8): -1,
                                   ("cap1024", 128): -1, ("cap1024", 8): 1}}
        for estimand, coefficients in weights.items():
            terms = [(100 * w / n, b[key]) for b in chosen.values() for key, w in coefficients.items()]
            complete = defaultdict(list)
            for block_key, b in chosen.items():
                if all(b[key] is not None for key in coefficients):
                    complete[block_key[3]].append(sum(w * b[key] for key, w in coefficients.items()))
            point, lo, hi = cross._boot_family(complete, samples, seed)
            estimates[estimand] = {"finite_sample_bounds_pp": outcome_bounds(terms),
                                  "missing_outcomes": sum(y is None for _, y in terms),
                                  "available_pair_intent_bootstrap": {"estimate_pp": point, "ci95": [lo, hi]}}
        summary.append({"stratum": stratum, "configurations": models, "planned_paired_cells": n,
                        "estimands": estimates})
    return {"sources": sources,
            "cell_missingness": [dict(zip(("model", "arm", "repeat", "visible", "workflow", "representation"), key),
                                      **counts, quarantined_arm=(cross.SLUG_OF[key[0]], key[1]) in quarantine)
                                 for key, counts in sorted(missingness.items())],
            "nonquarantined_strata": summary,
            "note": "Bounds hold observed binary outcomes fixed and range excluded outcomes independently over [0,1]. They are identification intervals for the acquired finite design, not confidence intervals, model-failure imputations, or a correction for whole-configuration quarantine. Intent bootstrap resamples available pairs and does not repair informative missingness."}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=ROOT / "results/analysis/release-analysis-inputs.json")
    parser.add_argument("--output", type=Path, default=ROOT / "results/analysis/revision-evidence-audit.json")
    parser.add_argument("--samples", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=20260905)
    parser.add_argument("--panel", type=Path)
    args = parser.parse_args()
    if args.panel:
        cross.select_reporting_models({row["model"] for row in json.loads(args.panel.read_text())["configurations"]})
    if args.samples < 100:
        parser.error("at least 100 bootstrap samples required")
    report = {"schema": "bottleneckbench-revision-evidence-audit-v1", "status": "exploratory",
              "confirmatory_weight": 0, "analysis_date": "2026-09-05",
              "manifest_sha256": sha256(args.manifest.read_bytes()).hexdigest(),
              "diagnostics": diagnostics(json.loads(args.manifest.read_text()), args.samples, args.seed),
              "crossover": crossover_audit(args.samples, args.seed)}
    args.output.write_text(json.dumps(report, sort_keys=True, indent=1, allow_nan=False) + "\n")
    print(f"Wrote {args.output.name}; offline follow-up, not preregistered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
