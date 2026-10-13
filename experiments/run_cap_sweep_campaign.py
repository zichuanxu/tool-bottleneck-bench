#!/usr/bin/env python3
"""Execute the completion-cap sweep campaign under a hard spending ceiling.

Reads the frozen plans written by ``build_cap_sweep_campaign.py`` and executes
them with ``bottleneckbench run``, which is resumable: a row already in an
output file is skipped.  What this wrapper adds, all required by
``protocols/completion-cap-sweep-2026-10-12.md``:

* a hard USD ceiling (section 0) metered from provider-reported usage against
  the section 5 price table, checked before every job and after every job;
* the pilot gate (section 7, amendment 1): ``--pilot`` starts the high-arm
  repeat-1 jobs of the two pilot configurations, reads truncation from the first
  36 V=128 episodes each completes, stops both jobs if either exceeds 5%, and
  otherwise lets them run on as repeat 1;
* both arms of a configuration run concurrently and all configuration lanes run
  in parallel (section 4); repeats are sequential within a lane;
* transport quarantine (section 8) and a cost ledger written after every job;
* a single-writer lock, because two runners over one output file duplicate rows.

Credentials: the runner reads the environment and, if present, an untracked
``.env`` file at the repository root (``KEY=value`` lines); values go only into
the child process environment and are never printed or written.

Usage:
  PYTHONPATH=src .venv/bin/python experiments/run_cap_sweep_campaign.py --pilot
  PYTHONPATH=src .venv/bin/python experiments/run_cap_sweep_campaign.py --repeats 2
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
# Amendment 2 runs the three added configurations as a second campaign directory
# with its own protocol hash, ledger, lock and ceiling; CAP_SWEEP_CAMPAIGN selects it.
CAMPAIGNS: dict[str, dict[str, object]] = {
    "completion-cap-sweep-1": {"protocol": "protocols/completion-cap-sweep-2026-10-12.md", "ceiling_usd": 100.00,
                               "per_configuration_gate": False},
    "completion-cap-sweep-2": {"protocol": "protocols/completion-cap-sweep-2026-10-12-amendment-2.md",
                               "ceiling_usd": 230.00, "per_configuration_gate": True},
}
CEILINGS: dict[str, float] = {name: float(spec["ceiling_usd"]) for name, spec in CAMPAIGNS.items()}
CAMPAIGN = os.environ.get("CAP_SWEEP_CAMPAIGN", "completion-cap-sweep-1")
if CAMPAIGN not in CAMPAIGNS:
    raise SystemExit(f"unknown campaign {CAMPAIGN!r}; known: {', '.join(CAMPAIGNS)}")
PROTOCOL = ROOT / str(CAMPAIGNS[CAMPAIGN]["protocol"])
PLAN_DIR = ROOT / "results/prospective-plans" / CAMPAIGN
OUT_DIR = ROOT / "results/prospective-execution" / CAMPAIGN
LEDGER = OUT_DIR / "cost-ledger.json"
LOCK = OUT_DIR / ".campaign.lock"

CEILING_USD = CEILINGS[CAMPAIGN]
# Amendment 2 (A2.5): a configuration failing the gate is excluded on its own; the others run on.
PER_CONFIGURATION_GATE = bool(CAMPAIGNS[CAMPAIGN]["per_configuration_gate"])
PILOT_EPISODES = 36
PILOT_MAX_TRUNCATION = 0.05
PILOT_POLL_SECONDS = 20

# USD per million tokens, frozen in section 5 of the protocol.
PRICES: dict[str, tuple[float, float]] = {
    "google/gemini-3.8-flash": (0.75, 3.75),
    "qwen/qwen3.7-flash": (0.03, 0.10),
    "qwen/qwen3.8-flash": (0.15, 0.47),
    "glm-5.3-flash": (0.10, 0.30),
    "anthropic/claude-haiku-5.5": (0.08, 0.40),
    "openai/gpt-6-luna": (0.20, 0.50),
    # amendment 2, section A2.4
    "glm-5.3": (1.40, 4.40),
    "deepseek/deepseek-v4-pro-0813": (1.31, 3.96),
}
TRANSPORT_QUARANTINE_SHARE = 0.20
TRANSPORT_QUARANTINE_MIN_ROWS = 50

MANIFEST = json.loads((PLAN_DIR / "campaign.json").read_text()) if (PLAN_DIR / "campaign.json").exists() else {}
SLUGS: dict[str, str] = {c["slug"]: c["model"] for c in MANIFEST.get("configurations", [])}
KEY_ENV: dict[str, str] = {c["slug"]: c["api_key_env"] for c in MANIFEST.get("configurations", [])}
ARMS: tuple[str, ...] = tuple(MANIFEST.get("arms", {}))
HIGH_ARM: str = str(MANIFEST.get("high_arm", ""))
PILOT_SLUGS: tuple[str, ...] = tuple(MANIFEST.get("pilot_slugs", ()))

_lock = threading.Lock()
_state: dict[str, object] = {"spent": 0.0, "jobs": [], "quarantined": [], "halted": False}
_procs: dict[tuple[str, str, int], subprocess.Popen] = {}


def child_env() -> dict[str, str]:
    """Environment for the CLI: the process environment plus an untracked .env, values never logged."""
    env = {**os.environ, "PYTHONPATH": "src"}
    dotenv = ROOT / ".env"
    if dotenv.exists():
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and value and not env.get(key):
                env[key] = value
    return env


def missing_credentials(env: dict[str, str], slugs: list[str]) -> list[str]:
    return sorted({KEY_ENV[s] for s in slugs if not env.get(KEY_ENV[s])})


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:  # a row still being written
                continue
    return out


def _condition(row: dict) -> dict:
    cond = row.get("condition")
    if not isinstance(cond, dict):
        cond = (row.get("episode") or {}).get("condition") or {}
    return cond


def _truncated(row: dict) -> bool:
    return "model_finish_length" in [str(e).split(":")[0] for e in (row.get("errors") or [])]


def _transport_failure(row: dict) -> bool:
    return any(str(e).startswith(("transport_", "provider_")) for e in (row.get("errors") or []))


def job_cost(path: Path, model: str) -> tuple[float, int, int, int]:
    """USD, rows, transport-failure rows and completion-cap-truncated rows for one output file."""
    prompt_price, completion_price = PRICES[model]
    usd = 0.0
    rows = failures = truncated = 0
    for row in _rows(path):
        rows += 1
        usage = row.get("usage") or {}
        usd += (float(usage.get("prompt_tokens") or 0) * prompt_price / 1e6
                + float(usage.get("completion_tokens") or 0) * completion_price / 1e6)
        failures += int(_transport_failure(row))
        truncated += int(_truncated(row))
    return usd, rows, failures, truncated


def recompute_spent() -> float:
    total = 0.0
    for path in OUT_DIR.glob("repeat-*/*.jsonl"):
        slug = path.stem.rsplit("-", 1)[0]
        model = SLUGS.get(slug)
        if model is not None:
            total += job_cost(path, model)[0]
    return total


def write_ledger() -> None:
    with _lock:
        payload = {
            "schema": "bottleneckbench-cap-sweep-cost-ledger-v1",
            "campaign": CAMPAIGN,
            "ceiling_usd": CEILING_USD,
            "spent_usd": round(float(_state["spent"]), 4),
            "role": "descriptive cost accounting against the frozen section 5 price table; an accounting, not an invoice",
            "prices_usd_per_million_tokens": {m: {"prompt": p, "completion": c} for m, (p, c) in PRICES.items()},
            "jobs": _state["jobs"],
            "quarantined": _state["quarantined"],
            "halted_at_ceiling": _state["halted"],
            "written_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        LEDGER.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")


def run_job(slug: str, arm: str, repeat: int, env: dict[str, str], max_conditions: int | None,
            label_suffix: str = "") -> None:
    model = SLUGS[slug]
    plan = PLAN_DIR / f"{slug}-{arm}-capacity.json"
    out = OUT_DIR / f"repeat-{repeat}" / f"{slug}-{arm}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    label = f"r{repeat}{label_suffix}"

    with _lock:
        if _state["halted"] or [slug, arm] in _state["quarantined"]:
            return
        if float(_state["spent"]) >= CEILING_USD:
            _state["halted"] = True
            print(f"  HALT ceiling reached before {slug} {arm} {label}", flush=True)
            return

    cmd = [sys.executable, "-m", "bottleneckbench.cli", "run", "--kind", "capacity",
           "--plan", str(plan), "--output", str(out), "--protocol", str(PROTOCOL),
           "--model-panel", str(PLAN_DIR / f"panel-{arm}.json"), "--allow-paid"]
    if max_conditions:
        cmd += ["--max-conditions", str(max_conditions)]

    started = time.time()
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    with _lock:
        _procs[(slug, arm, repeat)] = proc
    _, stderr = proc.communicate()
    stopped = proc.returncode in (-15, -9)
    usd, rows, failures, truncated = job_cost(out, model)
    share = failures / rows if rows else 0.0
    ok = proc.returncode == 0

    with _lock:
        _procs.pop((slug, arm, repeat), None)
        _state["spent"] = recompute_spent()
        _state["jobs"].append({
            "slug": slug, "model": model, "arm": arm, "repeat": repeat, "kind": label,
            "rows": rows, "transport_failures": failures, "transport_failure_share": round(share, 4),
            "truncated_rows": truncated, "usd": round(usd, 4), "seconds": round(time.time() - started, 1),
            "exit_code": proc.returncode, "stopped_by_gate": stopped,
            "stderr_tail": ("" if ok or stopped else stderr.strip()[-300:]),
        })
        if share > TRANSPORT_QUARANTINE_SHARE and rows >= TRANSPORT_QUARANTINE_MIN_ROWS:
            _state["quarantined"].append([slug, arm])
            print(f"  QUARANTINE {slug} {arm}: transport failure share {share:.1%}", flush=True)
        if float(_state["spent"]) >= CEILING_USD:
            _state["halted"] = True
            print(f"  HALT ceiling USD {CEILING_USD:.2f} reached", flush=True)
        spent = float(_state["spent"])

    status = "ok  " if ok else ("STOP" if stopped else "FAIL")
    print(f"  {status} {slug:<18} {arm:<9} {label}: {rows:>4} rows  ${usd:6.3f}  "
          f"trunc={truncated}  fail={share:.1%}  [total ${spent:.2f}]", flush=True)
    if not ok and not stopped:
        print(f"       {stderr.strip()[-200:]}", flush=True)
    write_ledger()


def take_lock() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
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


def release_lock() -> None:
    if LOCK.exists() and LOCK.read_text().strip() == str(os.getpid()):
        LOCK.unlink()


def _gate_reading(slug: str) -> tuple[int, int]:
    """(V=128 episodes completed, of which truncated) in the high-arm repeat-1 file."""
    rows = [r for r in _rows(OUT_DIR / "repeat-1" / f"{slug}-{HIGH_ARM}.jsonl")
            if int(_condition(r).get("visible_tools") or 0) == 128]
    first = rows[:PILOT_EPISODES]
    return len(first), sum(_truncated(r) for r in first)


def run_pilot(env: dict[str, str], keep_running: bool = False):
    """Run the gate; returns (exit code, pool, verdict).  With ``keep_running`` the
    passed configurations' high-arm repeat-1 jobs are left running in ``pool`` so the
    caller can schedule the remaining jobs at once (amendment 2) without a second writer."""
    print(f"pilot gate: {', '.join(PILOT_SLUGS)} at {HIGH_ARM}; verdict from the first "
          f"{PILOT_EPISODES} V=128 episodes of each high-arm repeat-1 job", flush=True)
    pool = ThreadPoolExecutor(max_workers=len(PILOT_SLUGS))
    futures = {slug: pool.submit(run_job, slug, HIGH_ARM, 1, env, None, " (pilot)") for slug in PILOT_SLUGS}
    verdict: dict[str, dict] = {}
    decided = False
    while not decided:
        time.sleep(PILOT_POLL_SECONDS)
        for slug in PILOT_SLUGS:
            n, truncated = _gate_reading(slug)
            done = futures[slug].done()
            if n >= PILOT_EPISODES or done:
                share = truncated / n if n else float("nan")
                verdict[slug] = {"v128_episodes": n, "truncated": truncated, "share": share,
                                 "passed": n >= PILOT_EPISODES and share <= PILOT_MAX_TRUNCATION,
                                 "job_exited_early": done and n < PILOT_EPISODES}
        progress = ", ".join(f"{s}: {_gate_reading(s)[1]}/{_gate_reading(s)[0]}" for s in PILOT_SLUGS)
        print(f"  gate progress {time.strftime('%H:%M:%S')}  {progress}", flush=True)
        failed = [s for s, v in verdict.items() if not v["passed"]]
        if PER_CONFIGURATION_GATE:
            for slug in failed:  # stop only that configuration's job, once
                with _lock:
                    proc = _procs.get((slug, HIGH_ARM, 1))
                if proc is not None and proc.poll() is None:
                    proc.terminate()
                    print(f"  stopping {slug}: excluded by the gate (amendment 2, A2.5)", flush=True)
            decided = len(verdict) == len(PILOT_SLUGS)
        elif failed or len(verdict) == len(PILOT_SLUGS):
            decided = True
    all_passed = len(verdict) == len(PILOT_SLUGS) and all(v["passed"] for v in verdict.values())
    for slug, v in verdict.items():
        print(f"  gate {slug:<18}: {v['truncated']}/{v['v128_episodes']} truncated ({v['share']:.1%}) -> "
              f"{'PASS' if v['passed'] else 'FAIL'}{' (job exited early)' if v['job_exited_early'] else ''}", flush=True)
    if not all_passed and not PER_CONFIGURATION_GATE:
        with _lock:
            procs = list(_procs.values())
        for proc in procs:
            proc.terminate()
        print("  stopping the pilot jobs", flush=True)
    if not keep_running:
        pool.shutdown(wait=True)
    payload = {"schema": "bottleneckbench-cap-sweep-pilot-gate-v2", "high_arm": HIGH_ARM,
               "episodes": PILOT_EPISODES, "ceiling": PILOT_MAX_TRUNCATION, "verdict": verdict,
               "all_passed": all_passed, "decided_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    (OUT_DIR / "pilot-gate.json").write_text(json.dumps(payload, indent=1) + "\n")
    any_passed = any(v["passed"] for v in verdict.values())
    if all_passed:
        print("pilot gate: PASSED; the pilot jobs ran on as repeat 1 of the high arm", flush=True)
    elif PER_CONFIGURATION_GATE:
        print("pilot gate: PARTIAL; failing configurations are excluded, the others ran on as repeat 1" if any_passed
              else "pilot gate: FAILED for every configuration", flush=True)
    else:
        print("pilot gate: FAILED; rebuild the high arm at the route maximum and run one repeat", flush=True)
    return (0 if all_passed or (PER_CONFIGURATION_GATE and any_passed) else 2), pool, verdict


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pilot", action="store_true", help="run the pilot gate only")
    parser.add_argument("--then-full", action="store_true",
                        help="with --pilot: once the gate decides, schedule every remaining job of the passed "
                             "configurations in this same process (the pilot jobs keep running as high-arm repeat 1)")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--slugs", nargs="*", default=None, help="subset of configurations (default: all)")
    parser.add_argument("--max-conditions", type=int, default=None, help="cap conditions per job (smoke testing only)")
    parser.add_argument("--lane-group", default=None,
                        help="name of a disjoint lane group; lets two runners over DIFFERENT slugs run at once")
    parser.add_argument("--parallel-repeats", action="store_true",
                        help="run all repeats of a lane concurrently (amendment 2, A2.5) instead of sequentially")
    args = parser.parse_args()
    if not SLUGS:
        raise SystemExit("no campaign manifest; run build_cap_sweep_campaign.py --freeze-plans first")
    slugs = args.slugs or list(SLUGS)
    gate_path = OUT_DIR / "pilot-gate.json"
    if not args.pilot and gate_path.exists():
        gate = json.loads(gate_path.read_text())
        excluded = [s for s, v in gate.get("verdict", {}).items() if not v.get("passed")]
        if excluded:
            print(f"excluded by the pilot gate: {', '.join(excluded)}", flush=True)
            slugs = [s for s in slugs if s not in excluded]
    if args.lane_group:
        global LOCK, LEDGER
        LOCK = OUT_DIR / f".campaign-{args.lane_group}.lock"
        LEDGER = OUT_DIR / f"cost-ledger-{args.lane_group}.json"  # one ledger per runner; the analyzer recomputes from rows
    env = child_env()
    missing = missing_credentials(env, list(PILOT_SLUGS) if args.pilot else slugs)
    if missing:
        raise SystemExit(f"missing credentials in the environment or .env: {', '.join(missing)}")

    take_lock()
    try:
        _state["spent"] = recompute_spent()
        print(f"campaign {CAMPAIGN}: ceiling ${CEILING_USD:.2f}, already spent ${float(_state['spent']):.2f}", flush=True)
        running: set[tuple[str, str, int]] = set()
        pilot_pool = None
        if args.pilot:
            code, pilot_pool, verdict = run_pilot(env, keep_running=args.then_full)
            if not args.then_full:
                return code
            passed = [s for s, v in verdict.items() if v["passed"]]
            if not passed:
                pilot_pool.shutdown(wait=True)
                return code
            slugs = [s for s in slugs if s in passed]
            running = {(s, HIGH_ARM, 1) for s in passed}  # already running in pilot_pool
            print(f"continuing into the full run for {', '.join(slugs)}", flush=True)

        def lane(slug: str) -> None:
            repeats = list(range(1, args.repeats + 1))
            waves = [repeats] if args.parallel_repeats else [[r] for r in repeats]
            for wave in waves:
                jobs = [(repeat, arm) for repeat in wave for arm in ARMS if (slug, arm, repeat) not in running]
                if not jobs:
                    continue
                with ThreadPoolExecutor(max_workers=len(jobs)) as arms_pool:
                    futures = [arms_pool.submit(run_job, slug, arm, repeat, env, args.max_conditions)
                               for repeat, arm in jobs]
                    for future in as_completed(futures):
                        future.result()

        with ThreadPoolExecutor(max_workers=len(slugs)) as pool:
            futures = {pool.submit(lane, slug): slug for slug in slugs}
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as exc:  # a lane crash must not lose the ledger
                    print(f"  LANE FAILED {futures[future]}: {exc}", flush=True)
        if pilot_pool is not None:
            pilot_pool.shutdown(wait=True)  # the high-arm repeat-1 jobs started by the gate
        write_ledger()
        print(f"\ncampaign finished: spent ${float(_state['spent']):.2f} of ${CEILING_USD:.2f}; "
              f"{len(_state['jobs'])} jobs; {len(_state['quarantined'])} quarantined", flush=True)
        return 0
    finally:
        release_lock()


if __name__ == "__main__":
    raise SystemExit(main())
