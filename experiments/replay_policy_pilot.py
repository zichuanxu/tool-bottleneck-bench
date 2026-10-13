"""Offline replay of the complete policy pilot from its public projection.

The frozen estimator is unchanged. The projection replaces private receipt
parsing with a hash-bound per-episode uncached-cost export, not a new estimator.
Transport forensics remain in the private mother repository.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from hashlib import sha256
import io
import json
from pathlib import Path
import shutil
import socket
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze_policy_pilot as estimator

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "results/policy-pilot"
PRIVATE = ROOT / "results/prospective-execution/nonoracle-policy-pilot-1"
FILES = ("rows.jsonl", "analysis.json", "policy-decisions.json", "accounting.json", "costs.json")


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def prepare():
    """Publish scored rows and sufficient cost data; never publish raw receipts."""
    view = PRIVATE / "analysis-input"
    if estimator.read(view / "status.json")["status"] != "complete":
        raise ValueError("Pilot is incomplete")
    with patch.object(estimator, "CAMPAIGN", view):
        estimator.load_complete()
        costs = dict(estimator.uncached_costs(estimator.read(estimator.PLAN)))
    PUBLIC.mkdir(exist_ok=True)
    for name in FILES[:3]:
        shutil.copyfile(view / name, PUBLIC / name)
    ledger = estimator.read(view / "ledger.json")
    estimator.atomic(PUBLIC / "accounting.json", {key: ledger[key] for key in
        ("reported_usd", "accounted_usd", "unverified_charge_reservations")})
    estimator.atomic(PUBLIC / "costs.json", costs)
    estimator.atomic(PUBLIC / "provenance.json", {
        "schema": "bottleneckbench-policy-projection-v1",
        "evidence_weight": "exploratory_development",
        "inputs": [{"path": str(p.relative_to(ROOT)), "sha256": digest(p)}
                   for p in (estimator.PLAN, Path(estimator.__file__),
                             ROOT / "experiments/policy_pilot.py")],
        "files": {name: digest(PUBLIC / name) for name in FILES},
        "canonical_source_map": estimator.read(view / "source-map.json"),
        "receipt_count": len(list((PRIVATE / "receipts").glob("*.json"))),
        "cost_basis": "Per-request frozen uncached input/output prices including failed generations; "
                      "empty-429 reservations are acquisition liability, not invented token usage.",
        "scope": "Replays scored outcomes and the frozen policy analysis. Does not reproduce hosted "
                 "generations or independently verify provider billing; raw receipts and transport "
                 "snapshots stay private. One transport-missing row has a hash-linked recovery.",
    })


def validate_inputs(directory=PUBLIC):
    manifest = estimator.read(directory / "provenance.json")
    if set(manifest["files"]) != set(FILES):
        raise ValueError("Pilot file inventory changed")
    for name, expected in manifest["files"].items():
        if digest(directory / name) != expected:
            raise ValueError(f"Pilot hash mismatch: {name}")
    for entry in manifest["inputs"]:
        path = Path(entry["path"])
        if path.is_absolute() or ".." in path.parts or digest(ROOT / path) != entry["sha256"]:
            raise ValueError("Pilot frozen input changed")
    rows = [json.loads(line) for line in (directory / "rows.jsonl").read_text().splitlines()]
    costs = estimator.read(directory / "costs.json")
    if set(costs) != {row["episode_id"] for row in rows} or any(v < 0 for v in costs.values()):
        raise ValueError("Pilot episode-cost inventory changed")
    return costs


def replay(directory=PUBLIC):
    costs = validate_inputs(directory)
    with tempfile.TemporaryDirectory(prefix="bottleneckbench-policy-replay-") as temporary:
        output = Path(temporary)
        shutil.copyfile(directory / "rows.jsonl", output / "rows.jsonl")
        estimator.atomic(output / "ledger.json", estimator.read(directory / "accounting.json"))
        estimator.atomic(output / "status.json", {"status": "complete"})
        with patch.object(estimator, "CAMPAIGN", output), \
             patch.object(estimator, "uncached_costs", return_value=costs), \
             patch.object(socket, "create_connection", side_effect=AssertionError("Offline replay")), \
             redirect_stdout(io.StringIO()):
            estimator.analyze()
        return [{"name": f"policy-pilot:{name}", "passed": digest(output / name) == digest(directory / name),
                 "expected_sha256": digest(directory / name), "replay_sha256": digest(output / name),
                 "comparison": "byte_identical"}
                for name in ("analysis.json", "policy-decisions.json")]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true", help="export from complete private canonical data")
    args = parser.parse_args()
    if args.prepare:
        prepare()
    checks = replay()
    print(json.dumps({"passed": all(item["passed"] for item in checks), "checks": checks}, indent=2))
    raise SystemExit(0 if all(item["passed"] for item in checks) else 1)
