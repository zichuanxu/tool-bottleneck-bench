"""Command-line entry points for construct audit, freeze, pilot, and analysis."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Sequence

from bottleneckbench.core.agnes import AgnesClient
from bottleneckbench.core.analysis import analyze_rows
from bottleneckbench.core.evaluation import run_construct_audit
from bottleneckbench.core.experiments import execute_plan, freeze_plan
from bottleneckbench.core.schema import JSON, canonical_json


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(canonical_json(payload) + "\n", encoding="utf-8")
    temporary.replace(path)


def _read_rows(path: Path) -> list[JSON]:
    rows: list[JSON] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"non-object JSONL row at {path}:{line_number}")
        rows.append(row)
    return rows


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="connectorscale-benchmark")
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit = subparsers.add_parser("audit", help="run all blocking construct gates")
    audit.add_argument("--output", type=Path, required=True)
    audit.add_argument("--panel", choices=("S", "T", "U"), default="S")

    freeze = subparsers.add_parser("freeze", help="freeze a construct-valid plan")
    freeze.add_argument("--output", type=Path, required=True)
    freeze.add_argument("--panel", choices=("S", "T", "U"), default="S")
    freeze.add_argument("--model", required=True)
    freeze.add_argument("--selector-seed", type=int, default=20260803)
    freeze.add_argument("--frozen-at", required=True)

    analyze = subparsers.add_parser("analyze", help="analyze a run JSONL file")
    analyze.add_argument("--input", type=Path, required=True)
    analyze.add_argument("--output", type=Path, required=True)

    pilot = subparsers.add_parser("pilot", help="freeze and execute the S pilot")
    pilot.add_argument("--output", type=Path, required=True)
    pilot.add_argument("--model", default="agnes-2.5-flash")
    pilot.add_argument("--selector-seed", type=int, default=20260803)
    pilot.add_argument("--frozen-at")
    pilot.add_argument("--max-conditions", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "audit":
        audit = run_construct_audit(args.panel)
        _write_json(args.output, audit.to_json())
        return 0 if audit.passed else 2

    if args.command == "freeze":
        audit = run_construct_audit(args.panel)
        freeze_plan(
            args.output,
            panel=args.panel,
            audit=audit,
            model=args.model,
            selector_seed=args.selector_seed,
            frozen_at=args.frozen_at,
        )
        return 0

    if args.command == "analyze":
        analysis = analyze_rows(_read_rows(args.input))
        _write_json(args.output, analysis)
        return 0

    if args.command == "pilot":
        root: Path = args.output
        root.mkdir(parents=True, exist_ok=True)
        audit = run_construct_audit()
        _write_json(root / "construct_audit.json", audit.to_json())
        if not audit.passed:
            return 2
        frozen_at = args.frozen_at or datetime.now(timezone.utc).isoformat()
        plan = freeze_plan(
            root / "run_plan.json",
            panel="S",
            audit=audit,
            model=args.model,
            selector_seed=args.selector_seed,
            frozen_at=frozen_at,
        )
        client = AgnesClient()
        execution = execute_plan(
            plan,
            client,
            root / "runs.jsonl",
            max_conditions=args.max_conditions,
        )
        rows = _read_rows(root / "runs.jsonl")
        analysis = analyze_rows(rows)
        _write_json(root / "analysis.json", analysis)
        _write_json(root / "execution_summary.json", execution)
        print(canonical_json(execution))
        return 0

    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
