#!/usr/bin/env python3
"""Execute the diagnosis x repair crossover campaign under a hard spending ceiling.

Reads the frozen plans written by ``build_crossover_campaign.py`` and executes
them with ``bottleneckbench run``, which is itself resumable: a row already in an
output file is skipped, so an interrupted campaign continues where it stopped.

What this wrapper adds, all required by
``protocols/diagnosis-repair-crossover-2026-09-04.md``:

* a **hard USD ceiling** (section 0) metered from provider-reported usage against
  the section 4 price table, checked before every job and after every job;
* **arm interleaving** (section 3), so neither completion budget is systematically
  earlier in wall-clock time;
* **transport quarantine** (section 7.3): a configuration arm whose transport or
  provider failure share exceeds 20% is dropped and reported, and the campaign
  continues rather than ending --- the failure mode that stopped the 2026-08
  replication at 97 of 768 rows;
* a **cost ledger** written after every job.

Nothing here changes an experimental policy: the caps, routes, conditions and
seeds all come from the frozen plans.  No credential value is read or written;
the runner only passes the environment through to the CLI.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "protocols/diagnosis-repair-crossover-2026-09-04.md"
CAMPAIGN = "diagnosis-repair-crossover-1"
PLAN_DIR = ROOT / "results/prospective-plans" / CAMPAIGN
OUT_DIR = ROOT / "results/prospective-execution" / CAMPAIGN
LEDGER = OUT_DIR / "cost-ledger.json"

CEILING_USD = 100.00

# USD per million tokens, frozen in section 4 of the protocol on 2026-09-04.
PRICES: dict[str, tuple[float, float]] = {
    "inclusionai/ling-3.0-flash": (0.021, 0.063),
    "meta/muse-glimmer-30b": (0.300, 1.100),
    "qwen/qwen3.8-flash": (0.150, 0.470),
    "glm-5.3-flash": (0.100, 0.300),
    "z-ai/glm-5.2": (0.500, 3.150),
    "deepseek/deepseek-v4-flash-0731": (0.065, 0.180),
    "openai/gpt-5.6-luna": (0.100, 0.600),
}

SLUGS = {
    "ling-30-flash": "inclusionai/ling-3.0-flash",
    "muse-glimmer-30b": "meta/muse-glimmer-30b",
    "qwen38-flash": "qwen/qwen3.8-flash",
    "glm-53-flash": "glm-5.3-flash",
    "glm-52": "z-ai/glm-5.2",
    "deepseek-v4-flash-0731": "deepseek/deepseek-v4-flash-0731",
    "gpt-56-luna": "openai/gpt-5.6-luna",
}

ARMS = ("cap1024", "cap4096")
LOCK = OUT_DIR / ".campaign.lock"
TRANSPORT_QUARANTINE_SHARE = 0.20
# A share is only meaningful once enough rows exist; below this a single provider
# hiccup would otherwise quarantine a whole configuration arm.
TRANSPORT_QUARANTINE_MIN_ROWS = 50

_lock = threading.Lock()
_state: dict[str, object] = {
    "spent": 0.0, "jobs": [], "quarantined": [], "halted": False,
}


def job_cost(path: Path, model: str) -> tuple[float, int, int]:
    """USD, rows and transport-failure rows for one output file."""

    if not path.exists():
        return 0.0, 0, 0
    prompt_price, completion_price = PRICES[model]
    usd = 0.0
    rows = 0
    failures = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        rows += 1
        usage = row.get("usage") or {}
        usd += (
            float(usage.get("prompt_tokens") or 0) * prompt_price / 1e6
            + float(usage.get("completion_tokens") or 0) * completion_price / 1e6
        )
        errors = row.get("errors") or []
        if any(str(item).startswith(("transport_", "provider_")) for item in errors):
            failures += 1
    return usd, rows, failures


def recompute_spent() -> float:
    """Total metered spend over every row this campaign has written so far."""

    total = 0.0
    for path in sorted(OUT_DIR.glob("repeat-*/*.jsonl")):
        slug = path.stem.rsplit("-", 1)[0]
        model = SLUGS.get(slug)
        if model is not None:
            total += job_cost(path, model)[0]
    return total


def write_ledger() -> None:
    with _lock:
        payload = {
            "schema": "bottleneckbench-crossover-cost-ledger-v1",
            "campaign": CAMPAIGN,
            "ceiling_usd": CEILING_USD,
            "spent_usd": round(float(_state["spent"]), 4),
            "role": (
                "descriptive cost accounting against the frozen section 4 price "
                "table; an accounting, not an invoice"
            ),
            "prices_usd_per_million_tokens": {
                model: {"prompt": prompt, "completion": completion}
                for model, (prompt, completion) in PRICES.items()
            },
            "jobs": _state["jobs"],
            "quarantined": _state["quarantined"],
            "halted_at_ceiling": _state["halted"],
        }
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        LEDGER.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")


def run_job(slug: str, arm: str, repeat: int, max_conditions: int | None) -> None:
    model = SLUGS[slug]
    out = OUT_DIR / f"repeat-{repeat}" / f"{slug}-{arm}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)

    with _lock:
        if _state["halted"]:
            return
        if [slug, arm] in _state["quarantined"]:
            return
        if float(_state["spent"]) >= CEILING_USD:
            _state["halted"] = True
            print(f"  HALT ceiling reached before {slug} {arm} repeat-{repeat}",
                  flush=True)
            return

    cmd = [
        sys.executable, "-m", "bottleneckbench.cli", "run",
        "--kind", "capacity",
        "--plan", str(PLAN_DIR / f"{slug}-{arm}-capacity.json"),
        "--output", str(out),
        "--protocol", str(PROTOCOL),
        "--model-panel", str(PLAN_DIR / f"panel-{arm}.json"),
        "--allow-paid",
    ]
    if max_conditions:
        cmd += ["--max-conditions", str(max_conditions)]

    started = time.time()
    proc = subprocess.run(
        cmd, cwd=ROOT, capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": "src"},
    )
    usd, rows, failures = job_cost(out, model)
    share = failures / rows if rows else 0.0
    ok = proc.returncode == 0

    with _lock:
        _state["spent"] = recompute_spent()
        _state["jobs"].append({
            "slug": slug, "model": model, "arm": arm, "repeat": repeat,
            "rows": rows, "transport_failures": failures,
            "transport_failure_share": round(share, 4),
            "usd": round(usd, 4), "seconds": round(time.time() - started, 1),
            "exit_code": proc.returncode,
            "stderr_tail": ("" if ok else proc.stderr.strip()[-300:]),
        })
        if share > TRANSPORT_QUARANTINE_SHARE and rows >= TRANSPORT_QUARANTINE_MIN_ROWS:
            _state["quarantined"].append([slug, arm])
            print(f"  QUARANTINE {slug} {arm}: transport failure share "
                  f"{share:.1%} > {TRANSPORT_QUARANTINE_SHARE:.0%}", flush=True)
        if float(_state["spent"]) >= CEILING_USD:
            _state["halted"] = True
            print(f"  HALT ceiling USD {CEILING_USD:.2f} reached", flush=True)
        spent = float(_state["spent"])

    status = "ok  " if ok else "FAIL"
    print(f"  {status} {slug:<24} {arm} r{repeat}: {rows:>4} rows  "
          f"${usd:6.3f}  fail={share:.1%}  [total ${spent:.2f}]", flush=True)
    if not ok:
        print(f"       {proc.stderr.strip()[-200:]}", flush=True)
    write_ledger()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--max-conditions", type=int, default=None,
                        help="cap conditions per job (smoke testing only)")
    parser.add_argument("--configs", type=int, default=7,
                        help="number of configurations to run concurrently")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Single-writer lock.  `bottleneckbench run` resumes by reading the completed
    # condition ids once at start, so two runners over the same output file each
    # see the same pending set and both execute it: the file gains duplicate rows
    # and the campaign pays twice.  That happened on 2026-09-04 (1,056 duplicate
    # rows, removed before any analysis), so the lock is not hypothetical.
    if LOCK.exists():
        stale = LOCK.read_text().strip()
        try:
            os.kill(int(stale), 0)
        except (ValueError, ProcessLookupError):
            print(f"clearing stale lock from pid {stale}", flush=True)
            LOCK.unlink()
        except PermissionError:
            raise SystemExit(f"another campaign runner (pid {stale}) holds {LOCK}")
        else:
            raise SystemExit(f"another campaign runner (pid {stale}) holds {LOCK}")
    LOCK.write_text(str(os.getpid()))
    try:
        return _run_campaign(args)
    finally:
        if LOCK.exists() and LOCK.read_text().strip() == str(os.getpid()):
            LOCK.unlink()


def _run_campaign(args: argparse.Namespace) -> int:
    _state["spent"] = recompute_spent()
    print(f"campaign {CAMPAIGN}: ceiling ${CEILING_USD:.2f}, "
          f"already spent ${float(_state['spent']):.2f}", flush=True)

    # One lane per configuration.  Within a lane the arms alternate between
    # repeats, so neither completion budget is systematically earlier.
    def config_lane(slug: str) -> None:
        for repeat in range(1, args.repeats + 1):
            order = ARMS if repeat % 2 else tuple(reversed(ARMS))
            for arm in order:
                run_job(slug, arm, repeat, args.max_conditions)

    with ThreadPoolExecutor(max_workers=args.configs) as pool:
        futures = {pool.submit(config_lane, slug): slug for slug in SLUGS}
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as exc:  # a lane crash must not lose the ledger
                print(f"  LANE FAILED {futures[future]}: {exc}", flush=True)

    write_ledger()
    print(f"\ncampaign finished: spent ${float(_state['spent']):.2f} of "
          f"${CEILING_USD:.2f}; {len(_state['jobs'])} jobs; "
          f"{len(_state['quarantined'])} quarantined")
    print(f"ledger: {LEDGER.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
