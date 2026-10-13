#!/usr/bin/env python3
"""Retrospective audit of terminal-only versus first-failure localization."""

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
CELLS = {
    "baseline": (8, "low", "raw"),
    "visible_menu": (128, "low", "raw"),
    "workflow": (8, "high", "raw"),
    "returned_state": (8, "low", "linked"),
}


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


def _packets(paths: Sequence[Path]) -> tuple[list[dict[str, str]], int, int]:
    cells: dict[tuple[str, str], dict[tuple[int, str, str], Mapping[str, object]]] = defaultdict(dict)
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
    packets: list[dict[str, str]] = []
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
            "stage": _stage(stress_rows[label]),
        })
    return packets, excluded_infrastructure, excluded_multiple


def _predict(packets: Sequence[Mapping[str, str]], fold_key: str) -> list[dict[str, str]]:
    predictions: list[dict[str, str]] = []
    for fold in sorted({row[fold_key] for row in packets}):
        train = [row for row in packets if row[fold_key] != fold]
        test = [row for row in packets if row[fold_key] == fold]
        prior = _majority(row["label"] for row in train)
        stage_labels: dict[str, str] = {}
        for stage in {row["stage"] for row in train}:
            stage_labels[stage] = _majority(
                (row["label"] for row in train if row["stage"] == stage), prior
            )
        for row in test:
            predictions.append({
                **row,
                "terminal_prediction": prior,
                "first_stage_prediction": stage_labels.get(row["stage"], prior),
            })
    return predictions


def _metrics(rows: Sequence[Mapping[str, str]], prediction: str) -> dict[str, object]:
    confusion = {label: {guess: 0 for guess in LABELS} for label in LABELS}
    for row in rows:
        confusion[row["label"]][row[prediction]] += 1
    recall: dict[str, float] = {}
    f1: dict[str, float] = {}
    for label in LABELS:
        tp = confusion[label][label]
        actual = sum(confusion[label].values())
        predicted = sum(confusion[truth][label] for truth in LABELS)
        recall[label] = tp / actual if actual else 0.0
        precision = tp / predicted if predicted else 0.0
        f1[label] = 2 * precision * recall[label] / (precision + recall[label]) if precision + recall[label] else 0.0
    return {
        "accuracy": sum(confusion[label][label] for label in LABELS) / len(rows),
        "balanced_accuracy": fmean(recall.values()),
        "macro_f1": fmean(f1.values()),
        "per_layer_recall": recall,
        "confusion": confusion,
    }


def _bootstrap(rows: Sequence[Mapping[str, str]], prediction: str, samples: int, seed: int) -> dict[str, list[float]]:
    intents = sorted({row["intent"] for row in rows})
    grouped = {intent: [row for row in rows if row["intent"] == intent] for intent in intents}
    rng = random.Random(seed)
    draws: dict[str, list[float]] = {"accuracy": [], "balanced_accuracy": [], "macro_f1": []}
    for _ in range(samples):
        sample = [row for intent in rng.choices(intents, k=len(intents)) for row in grouped[intent]]
        metrics = _metrics(sample, prediction)
        for key in draws:
            draws[key].append(float(metrics[key]))
    return {
        key: [sorted(values)[int(0.025 * (samples - 1))], sorted(values)[int(0.975 * (samples - 1))]]
        for key, values in draws.items()
    }


def analyze(manifest: Mapping[str, object], *, bootstrap_samples: int, seed: int) -> dict[str, object]:
    packets, excluded, multiple = _packets(_paths(manifest))
    predictions = _predict(packets, "intent")
    reports = {}
    for name, field in (("terminal_only", "terminal_prediction"), ("first_failure_stage", "first_stage_prediction")):
        report = _metrics(predictions, field)
        report["intent_cluster_bootstrap_ci95"] = _bootstrap(predictions, field, bootstrap_samples, seed)
        reports[name] = report
    return {
        "schema": "bottleneckbench-exploratory-failure-localization-v1",
        "status": "exploratory",
        "confirmatory_weight": 0,
        "packets": len(packets),
        "intent_clusters": len({row["intent"] for row in packets}),
        "configuration_family_clusters": len({(row["model"], row["family"]) for row in packets}),
        "labels": dict(Counter(row["label"] for row in packets)),
        "excluded_infrastructure_clusters": excluded,
        "excluded_multi_failure_clusters": multiple,
        "methods": reports,
        "accuracy_gain": float(reports["first_failure_stage"]["accuracy"]) - float(reports["terminal_only"]["accuracy"]),
        "leave_one_organization_out": "not computed: provider organization is intentionally unavailable to the diagnostic",
        "bootstrap": {"samples": bootstrap_samples, "seed": seed, "cluster": "intent"},
    }


def self_check() -> None:
    assert _majority(["workflow", "visible_menu", "workflow"]) == "workflow"
    rows = [
        {"label": label, "prediction": guess}
        for label, guess in (("visible_menu", "visible_menu"), ("workflow", "visible_menu"), ("returned_state", "returned_state"))
    ]
    report = _metrics(rows, "prediction")
    assert report["accuracy"] == 2 / 3 and report["per_layer_recall"]["workflow"] == 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=20260901)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        print('{"passed":true}')
        return 0
    if args.manifest is None or args.output is None:
        parser.error("--manifest and --output are required")
    result = analyze(json.loads(args.manifest.read_text(encoding="utf-8")), bootstrap_samples=args.bootstrap_samples, seed=args.seed)
    result["inputs"] = {"manifest": str(args.manifest), "sha256": sha256(args.manifest.read_bytes()).hexdigest()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps({"packets": result["packets"], "status": result["status"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
