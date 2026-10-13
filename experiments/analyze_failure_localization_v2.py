#!/usr/bin/env python3
"""Held-out failure localization from joint obligation-and-termination records.

Version 2 of ``analyze_failure_localization.py``.  The v1 audit asked whether the
recorded *first unmet obligation* alone recovers the assigned layer, and answered
no.  That is a statement about one decoder on one feature, not about how much
diagnostic information the traces carry, so v1's negative result was stated more
strongly than its procedure supports.

This version keeps v1's packet population and its leave-one-intent-out protocol
exactly, and varies only the feature set, so the numbers are directly comparable:

    F0  constant            the majority label; v1's "terminal_only" baseline
    F1  obligation          the first unmet obligation; v1's headline decoder
    F2  termination         which configured limit fired, if any
    F3  obligation+termination     the two jointly
    F4  F3 + trace shape    calls executed, zero-call flag, early-stop reason
    F5  F4 + menu-size proxy       REPORTED SEPARATELY AND NOT AS A RESULT

F5 exists only to bound the ceiling.  Serialized schema bytes are a near-direct
readout of the assigned visible-menu level, so a decoder that sees them is partly
reading the treatment rather than diagnosing the failure.  It is reported as an
instrumented upper bound and is never compared against the others as if it were
a diagnostic method.

The decoder family is v1's: a majority vote over the feature tuple, backing off to
the training-fold prior for tuples unseen in training.  Holding the decoder fixed
and moving only the features is what makes "do joint records beat either component
alone?" a question about the records rather than about model choice.

Original v1 outputs are not modified; this writes a new versioned artifact.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import random
from statistics import fmean
from typing import Iterable, Mapping, Sequence

from bottleneckbench.analysis import is_infrastructure_failure

ROOT = Path(__file__).resolve().parents[1]
LABELS = ("visible_menu", "workflow", "returned_state")

# The four cells of a localization packet, identical to v1.
CELLS = {
    "baseline": (8, "low", "raw"),
    "visible_menu": (128, "low", "raw"),
    "workflow": (8, "high", "raw"),
    "returned_state": (8, "low", "linked"),
}

# Configured limits.  These are harness stopping conditions, not obligations, and
# the released grader records them outside the obligation conjunction.
TERMINAL_ERRORS = (
    "model_finish_length",
    "budget_exceeded",
    "success_infeasible_under_call_budget",
    "turn_limit_exceeded",
)

FEATURE_SETS = ("constant", "obligation", "termination",
                "obligation_termination", "with_trace_shape")
INSTRUMENTED = "with_menu_size"


def _majority(values: Iterable[str], fallback: str | None = None) -> str:
    counts = Counter(values)
    if not counts:
        if fallback is None:
            raise ValueError("majority requires observations or a fallback")
        return fallback
    return min(counts, key=lambda value: (-counts[value], LABELS.index(value)))


def _success(row: Mapping[str, object]) -> bool:
    verdict = row.get("verdict")
    return isinstance(verdict, dict) and verdict.get("verified_success") is True


def _stage(row: Mapping[str, object]) -> str:
    verdict = row.get("verdict")
    stage = verdict.get("failure_stage") if isinstance(verdict, dict) else None
    if not isinstance(stage, str) or not stage:
        raise ValueError("failed Capacity row lacks an exact failure stage")
    return stage


def _terminal(row: Mapping[str, object]) -> str:
    """Which configured limit fired.  'none' when the episode stopped on its own."""

    errors = [str(item).split(":")[0] for item in (row.get("errors") or [])]
    fired = [name for name in TERMINAL_ERRORS if name in errors]
    return "+".join(fired) if fired else "none"


def _trace_shape(row: Mapping[str, object]) -> str:
    calls = len(row.get("trace") or [])
    efficiency = row.get("efficiency")
    early = ""
    if isinstance(efficiency, dict):
        early = str(efficiency.get("early_stop_reason") or "")
    bucket = "0" if calls == 0 else "1-2" if calls <= 2 else "3-5" if calls <= 5 else "6+"
    return f"calls={bucket};early={early}"


def _menu_size(row: Mapping[str, object]) -> str:
    """Serialized schema volume, bucketed.  A near-direct readout of the treatment."""

    efficiency = row.get("efficiency")
    payload = 0
    if isinstance(efficiency, dict):
        payload = int(efficiency.get("tool_schema_bytes") or 0)
    return "small" if payload < 12000 else "large"


def _features(row: Mapping[str, object]) -> dict[str, str]:
    obligation = _stage(row)
    termination = _terminal(row)
    return {
        "constant": "",
        "obligation": obligation,
        "termination": termination,
        "obligation_termination": f"{obligation}|{termination}",
        "with_trace_shape": f"{obligation}|{termination}|{_trace_shape(row)}",
        INSTRUMENTED: (
            f"{obligation}|{termination}|{_trace_shape(row)}|{_menu_size(row)}"
        ),
    }


def _paths(manifest: Mapping[str, object]) -> list[Path]:
    inputs = manifest.get("inputs")
    if not isinstance(inputs, dict):
        raise ValueError("manifest lacks input groups")
    entries = [
        *inputs.get("capacity", []),
        *inputs.get("candidate_capacity", []),
        *inputs.get("frontier_capacity", []),
    ]
    paths = []
    for item in entries:
        path = ROOT / str(item["path"])
        if sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError(f"manifest-bound Capacity input changed: {path}")
        paths.append(path)
    return paths


def _packets(paths: Sequence[Path]) -> tuple[list[dict[str, object]], int, int]:
    cells: dict[tuple[str, str], dict[tuple[int, str, str], Mapping[str, object]]]
    cells = defaultdict(dict)
    excluded_infrastructure = 0
    excluded_multiple = 0
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("study_role") != "primary":
                continue
            condition = row.get("condition")
            if not isinstance(condition, dict):
                raise ValueError("Capacity row lacks condition metadata")
            coordinate = (
                int(condition["visible_tools"]),
                str(condition["workflow"]),
                str(condition["representation"]),
            )
            if coordinate not in CELLS.values():
                continue
            key = (str(condition["model"]), str(condition["family_id"]))
            if coordinate in cells[key]:
                raise ValueError(f"duplicate localization cell: {key}|{coordinate}")
            cells[key][coordinate] = row
    packets: list[dict[str, object]] = []
    for (model, family), rows in sorted(cells.items()):
        if set(rows) != set(CELLS.values()):
            raise ValueError(f"incomplete localization packet: {model}|{family}")
        baseline = rows[CELLS["baseline"]]
        condition = baseline["condition"]
        assert isinstance(condition, dict)
        stress_rows = {label: rows[CELLS[label]] for label in LABELS}
        if is_infrastructure_failure(baseline) or any(
            is_infrastructure_failure(row) for row in stress_rows.values()
        ):
            excluded_infrastructure += 1
            continue
        failed = [label for label, row in stress_rows.items() if not _success(row)]
        if not _success(baseline) or len(failed) != 1:
            excluded_multiple += int(_success(baseline) and len(failed) > 1)
            continue
        label = failed[0]
        packets.append({
            "model": model,
            "family": family,
            "intent": str(condition["intent"]),
            "label": label,
            "features": _features(stress_rows[label]),
        })
    return packets, excluded_infrastructure, excluded_multiple


def _predict(packets: Sequence[Mapping[str, object]], feature: str) -> list[str]:
    """Leave-one-intent-out majority vote over one feature, in packet order."""

    predictions: dict[int, str] = {}
    intents = sorted({str(row["intent"]) for row in packets})
    for fold in intents:
        train = [r for r in packets if r["intent"] != fold]
        prior = _majority(str(r["label"]) for r in train)
        table: dict[str, str] = {}
        keys = {str(r["features"][feature]) for r in train}  # type: ignore[index]
        for key in keys:
            table[key] = _majority(
                (str(r["label"]) for r in train
                 if str(r["features"][feature]) == key),  # type: ignore[index]
                prior,
            )
        for index, row in enumerate(packets):
            if row["intent"] != fold:
                continue
            predictions[index] = table.get(
                str(row["features"][feature]), prior  # type: ignore[index]
            )
    return [predictions[i] for i in range(len(packets))]


def _metrics(labels: Sequence[str], predicted: Sequence[str]) -> dict[str, object]:
    confusion = {label: {guess: 0 for guess in LABELS} for label in LABELS}
    for truth, guess in zip(labels, predicted):
        confusion[truth][guess] += 1
    recall: dict[str, float] = {}
    f1: dict[str, float] = {}
    for label in LABELS:
        tp = confusion[label][label]
        actual = sum(confusion[label].values())
        predicted_count = sum(confusion[t][label] for t in LABELS)
        recall[label] = tp / actual if actual else 0.0
        precision = tp / predicted_count if predicted_count else 0.0
        denominator = precision + recall[label]
        f1[label] = 2 * precision * recall[label] / denominator if denominator else 0.0
    correct = sum(confusion[label][label] for label in LABELS)
    return {
        "accuracy": correct / len(labels) if labels else 0.0,
        "balanced_accuracy": fmean(recall.values()),
        "macro_f1": fmean(f1.values()),
        "per_layer_recall": recall,
        "predicted_label_support": {
            label: sum(confusion[t][label] for t in LABELS) for label in LABELS
        },
        "confusion": confusion,
    }


def _bootstrap(packets: Sequence[Mapping[str, object]], predicted: Sequence[str],
               samples: int, seed: int) -> dict[str, list[float]]:
    intents = sorted({str(r["intent"]) for r in packets})
    grouped: dict[str, list[int]] = {
        intent: [i for i, r in enumerate(packets) if r["intent"] == intent]
        for intent in intents
    }
    rng = random.Random(seed)
    draws: dict[str, list[float]] = {
        "accuracy": [], "balanced_accuracy": [], "macro_f1": []
    }
    for _ in range(samples):
        index = [i for intent in rng.choices(intents, k=len(intents))
                 for i in grouped[intent]]
        metrics = _metrics([str(packets[i]["label"]) for i in index],
                           [predicted[i] for i in index])
        for key in draws:
            draws[key].append(float(metrics[key]))
    lo, hi = int(0.025 * (samples - 1)), int(0.975 * (samples - 1))
    return {key: [sorted(v)[lo], sorted(v)[hi]] for key, v in draws.items()}


def analyze(manifest: Mapping[str, object], *, bootstrap_samples: int,
            seed: int) -> dict[str, object]:
    packets, excluded, multiple = _packets(_paths(manifest))
    labels = [str(r["label"]) for r in packets]
    label_counts = Counter(labels)

    methods: dict[str, object] = {}
    for feature in (*FEATURE_SETS, INSTRUMENTED):
        predicted = _predict(packets, feature)
        report = dict(_metrics(labels, predicted))
        report["intent_cluster_bootstrap_ci95"] = _bootstrap(
            packets, predicted, bootstrap_samples, seed
        )
        report["distinct_feature_values"] = len(
            {str(r["features"][feature]) for r in packets}  # type: ignore[index]
        )
        methods[feature] = report

    majority_share = max(label_counts.values()) / len(labels)
    return {
        "schema": "bottleneckbench-exploratory-failure-localization-v2",
        "status": "exploratory",
        "confirmatory_weight": 0,
        "supersedes": "bottleneckbench-exploratory-failure-localization-v1",
        "scientific_role": (
            "held-out feature ablation for failure localization: does the joint "
            "obligation-and-termination record recover the assigned layer better "
            "than either component alone, under one fixed decoder family?"
        ),
        "packets": len(packets),
        "intent_clusters": len({r["intent"] for r in packets}),
        "configuration_family_clusters": len({(r["model"], r["family"]) for r in packets}),
        "labels": dict(label_counts),
        "excluded_infrastructure_clusters": excluded,
        "excluded_multi_failure_clusters": multiple,
        "population_note": (
            "packets require a succeeding baseline and exactly one failing stress "
            "cell, so this is a selected population: multi-failure packets are "
            "excluded and the reported accuracies do not describe failures at large"
        ),
        "baselines": {
            "chance_balanced_accuracy": 1.0 / len(LABELS),
            "majority_class_share": majority_share,
            "majority_class": max(label_counts, key=lambda k: label_counts[k]),
        },
        "methods": methods,
        "instrumented_upper_bound": INSTRUMENTED,
        "instrumented_note": (
            "serialized schema volume is close to a direct readout of the assigned "
            "visible-menu level, so this row bounds what the traces could support "
            "and is not a diagnostic method"
        ),
        "bootstrap": {"samples": bootstrap_samples, "seed": seed, "cluster": "intent"},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path,
                        default=ROOT / "results/analysis/release-analysis-inputs.json")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "results/analysis/release-failure-localization-v2.json")
    parser.add_argument("--bootstrap-samples", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=20260904)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    report = analyze(manifest, bootstrap_samples=args.bootstrap_samples, seed=args.seed)
    args.output.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")

    print(f"packets {report['packets']}  labels {report['labels']}")
    print(f"chance balanced accuracy {report['baselines']['chance_balanced_accuracy']:.3f}"
          f"   majority share {report['baselines']['majority_class_share']:.3f}")
    print(f"\n{'feature set':<26} {'acc':>6} {'bal.acc':>8} {'macroF1':>8} "
          f"{'bal.acc 95% CI':>18}  {'vals':>5}  per-layer recall")
    methods = report["methods"]
    assert isinstance(methods, dict)
    for name in (*FEATURE_SETS, INSTRUMENTED):
        m = methods[name]
        ci = m["intent_cluster_bootstrap_ci95"]["balanced_accuracy"]
        recall = m["per_layer_recall"]
        flag = "  <- instrumented bound" if name == INSTRUMENTED else ""
        print(f"{name:<26} {m['accuracy']:>6.3f} {m['balanced_accuracy']:>8.3f} "
              f"{m['macro_f1']:>8.3f}  [{ci[0]:.3f},{ci[1]:.3f}] {m['distinct_feature_values']:>5}  "
              + " ".join(f"{k[:4]}={v:.2f}" for k, v in recall.items()) + flag)
    print(f"\nwrote {args.output.relative_to(ROOT) if args.output.is_relative_to(ROOT) else args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
