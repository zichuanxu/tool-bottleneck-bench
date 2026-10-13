#!/usr/bin/env python3
"""Build the frozen panels and plans for the diagnosis x repair crossover campaign.

The campaign is specified by ``protocols/diagnosis-repair-crossover-2026-09-04.md``;
this script only mechanises what that document already fixes.  It writes two model
panels that differ in exactly one field --- ``episode_policy.max_completion_tokens``
--- and then freezes one capacity plan per (configuration, arm).

Offline and free: no hosted request is issued here.  ``bottleneckbench run`` is the
only step that spends money and it refuses to without ``--allow-paid``.

Only the *name* of each credential environment variable is written into a panel,
never a value; that matches the released plans and keeps the artefacts publishable.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "protocols/diagnosis-repair-crossover-2026-09-04.md"
CAMPAIGN = "diagnosis-repair-crossover-1"
PLAN_DIR = ROOT / "results/prospective-plans" / CAMPAIGN
AUDIT_DIR = ROOT / "build/r3-audits"
FROZEN_AT = "2026-09-04T00:00:00Z"

OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"
ZAI = "https://api.z.ai/api/paas/v4/chat/completions"

# Stratum, model, harness and route, as fixed by section 2 of the protocol.  The
# served build ids were verified by the 2026-09-04 pre-flight probe, and
# ``omit_temperature`` records the section 2.2 parameter-support finding.
CONFIGURATIONS: tuple[dict[str, object], ...] = (
    # --- stratum B: budget-limited (truncation >= .20, completion_no_call dominant)
    {"stratum": "B", "model": "inclusionai/ling-3.0-flash", "provider_only": ["novita"],
     "slug": "ling-30-flash"},
    {"stratum": "B", "model": "meta/muse-glimmer-30b", "provider_only": ["phala"],
     "slug": "muse-glimmer-30b"},
    {"stratum": "B", "model": "qwen/qwen3.8-flash", "provider_only": ["alibaba"],
     "slug": "qwen38-flash"},
    {"stratum": "B", "model": "glm-5.3-flash", "provider_only": None, "slug": "glm-53-flash",
     "harness": "react-routed@zai", "endpoint": ZAI, "api_key_env": "ZAI_API_KEY"},
    # --- stratum S: loss without dominant truncation (truncation <= .15,
    #     released V effect <= -.08)
    {"stratum": "S", "model": "z-ai/glm-5.2", "provider_only": ["sail-research/fp8"],
     "slug": "glm-52"},
    {"stratum": "S", "model": "deepseek/deepseek-v4-flash-0731",
     "provider_only": ["sail-research/fp4"], "slug": "deepseek-v4-flash-0731"},
    # --- stratum N: null (truncation <= .05, released V effect > -.05)
    {"stratum": "N", "model": "openai/gpt-5.6-luna", "provider_only": ["openai/flex"],
     "slug": "gpt-56-luna", "omit_temperature": True},
)

ARMS = {"cap1024": 1024, "cap4096": 4096}


def _configuration(spec: dict[str, object]) -> dict[str, object]:
    """One frozen panel configuration, with the campaign's shared route policy."""

    model = str(spec["model"])
    configuration: dict[str, object] = {
        "model": model,
        "harness": str(spec.get("harness", "react-routed@openrouter")),
        "endpoint": str(spec.get("endpoint", OPENROUTER)),
        # The variable NAME only; the value never enters a tracked artefact.
        "api_key_env": str(spec.get("api_key_env", "OPENROUTER_API_KEY")),
        "api_key_header": "Authorization",
        # Pinned to the build the pre-flight probe actually served, so a silent
        # substitution fails the row instead of entering the panel.
        "accepted_response_models": [model],
        "provider_allow_fallbacks": False,
        "provider_require_parameters": True,
        "provider_data_collection": "deny",
        "require_usage": True,
        "omit_parallel_tool_calls": True,
        "study_role": "primary",
        "discovery_enabled": False,
        "slug": f"{spec['slug']}-crossover",
        "stratum": str(spec["stratum"]),
        "workers_per_job": 4,
        "provider_concurrency_cap": 4,
        "minimum_request_interval_seconds": 0.5,
    }
    if spec.get("provider_only"):
        configuration["provider_only"] = list(spec["provider_only"])  # type: ignore[arg-type]
    if spec.get("omit_temperature"):
        configuration["omit_temperature"] = True
    return configuration


def build_panel(arm: str) -> dict[str, object]:
    return {
        "frozen_for_study": True,
        "frozen_at": FROZEN_AT,
        "panel": f"{CAMPAIGN}-{arm}",
        "scientific_role": (
            "frozen acquisition panel for the diagnosis x repair crossover campaign; "
            f"the {arm} arm differs from its companion in max_completion_tokens alone"
        ),
        "arm": arm,
        "configurations": [_configuration(spec) for spec in CONFIGURATIONS],
        "execution_policy": {
            "episode_policy": {
                "dynamic_shortlist": True,
                "early_stop_infeasible": True,
                "max_completion_tokens": ARMS[arm],
                "max_total_tokens": 128000,
                "name": f"task-adaptive-routed-v1-{arm}",
                "tool_call_slack": 2,
                "turn_slack": 2,
            },
            "minimum_request_interval_seconds": 0.5,
            "model_lane_count": len(CONFIGURATIONS),
            "provider_concurrency_cap": 4,
            "timeout_seconds": 300,
            "transport_retries": 5,
            "workers_per_job": 4,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--freeze-plans",
        action="store_true",
        help="also invoke `bottleneckbench plan` for every (configuration, arm)",
    )
    args = parser.parse_args()

    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    if not PROTOCOL.exists():
        raise SystemExit(f"missing frozen protocol: {PROTOCOL}")

    panels: dict[str, Path] = {}
    for arm in ARMS:
        path = PLAN_DIR / f"panel-{arm}.json"
        path.write_text(json.dumps(build_panel(arm), indent=1, sort_keys=True) + "\n")
        panels[arm] = path
        print(
            f"panel  {path.relative_to(ROOT)}  "
            f"({len(CONFIGURATIONS)} configurations, cap={ARMS[arm]})"
        )

    if not args.freeze_plans:
        print("\n(pass --freeze-plans to freeze the per-configuration plans)")
        return 0

    # Freezing a main-split plan is gated on a passing admission audit.  This
    # campaign re-executes an already admitted frozen main design at a second
    # completion budget: the split, the families and every condition field except
    # the episode policy are unchanged, and no outcome of this campaign is used to
    # justify running it.  That is the prospective-admission case, so the audit is
    # a declaration bound to this protocol and this panel rather than a fresh
    # development run.
    sys.path.insert(0, str(ROOT / "src"))
    from bottleneckbench.core.schema import stable_hash  # noqa: E402

    protocol_hash = stable_hash(PROTOCOL.read_text(encoding="utf-8"))
    admissions: dict[str, Path] = {}
    for arm, panel_path in panels.items():
        panel_hash = stable_hash(json.loads(panel_path.read_text()))
        admission = {
            "audit": "prospective-capacity-execution-admission-v1",
            "passed": True,
            "target_split": "main",
            "protocol_artifact_hash": protocol_hash,
            "model_panel_hash": panel_hash,
            "basis": (
                "completion-budget arm of an already admitted frozen main design; "
                "split, families and every condition field are unchanged and only "
                "episode_policy.max_completion_tokens differs between arms"
            ),
            "arm": arm,
            "max_completion_tokens": ARMS[arm],
            "uses_campaign_outcomes": False,
        }
        path = PLAN_DIR / f"admission-{arm}.json"
        path.write_text(json.dumps(admission, indent=1, sort_keys=True) + "\n")
        admissions[arm] = path
        print(f"admit  {path.relative_to(ROOT)}  (panel_hash={panel_hash[:12]})")

    env = {**os.environ, "PYTHONPATH": "src"}
    failures = 0
    for arm, panel_path in panels.items():
        for spec in CONFIGURATIONS:
            harness = str(spec.get("harness", "react-routed@openrouter"))
            out = PLAN_DIR / f"{spec['slug']}-{arm}-capacity.json"
            cmd = [
                sys.executable, "-m", "bottleneckbench.cli", "plan",
                "--kind", "capacity", "--split", "main",
                "--model", str(spec["model"]), "--harness", harness,
                "--frozen-at", FROZEN_AT,
                "--audit-dir", str(AUDIT_DIR),
                "--protocol", str(PROTOCOL),
                "--model-panel", str(panel_path),
                "--admission", str(admissions[arm]),
                "--output", str(out),
            ]
            proc = subprocess.run(
                cmd, cwd=ROOT, capture_output=True, text=True, env=env
            )
            if proc.returncode != 0:
                failures += 1
                detail = (proc.stderr.strip().splitlines() or ["(no stderr)"])[-1]
                print(f"  FAIL {spec['slug']:<24} {arm}: {detail[:160]}")
            else:
                plan = json.loads(out.read_text())
                print(
                    f"  ok   {spec['slug']:<24} {arm}: "
                    f"{len(plan['conditions'])} conditions, "
                    f"plan_hash={plan['plan_hash'][:12]}"
                )
    total = len(CONFIGURATIONS) * len(ARMS)
    print(f"\n{total - failures} of {total} plans frozen")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
