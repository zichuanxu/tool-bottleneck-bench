#!/usr/bin/env python3
"""Build the frozen panels and plans for the completion-cap sweep campaign.

The campaign is specified by ``protocols/completion-cap-sweep-2026-10-12.md``;
this script only mechanises what that document fixes.  It writes two model
panels that differ in exactly one field, ``episode_policy.max_completion_tokens``,
freezes one full main-split capacity plan per (configuration, arm) with
``bottleneckbench plan`` exactly as the 2026-09-04 crossover did, restricts each
plan to the eight endpoint cells the protocol names, re-freezes it with its
recomputed ``plan_hash``, and writes the two pilot-gate plans.

Offline and free: no hosted request is issued here.  Only the NAME of each
credential environment variable is written into a panel, never a value.

Usage:
  PYTHONPATH=src .venv/bin/python experiments/build_cap_sweep_campaign.py --freeze-plans
  PYTHONPATH=src .venv/bin/python experiments/build_cap_sweep_campaign.py --freeze-plans --high-cap 65536
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "protocols/completion-cap-sweep-2026-10-12.md"
CAMPAIGN = "completion-cap-sweep-1"
PLAN_DIR = ROOT / "results/prospective-plans" / CAMPAIGN
# Amendment 2 adds the remaining three stratum-B configurations as a second
# campaign directory bound to the amendment's hash (``--extension``).
EXT_PROTOCOL = ROOT / "protocols/completion-cap-sweep-2026-10-12-amendment-2.md"
EXT_CAMPAIGN = "completion-cap-sweep-2"
AUDIT_DIR = ROOT / "build/r3-audits"
FROZEN_AT = "2026-10-12T00:00:00Z"

OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"
ZAI = "https://api.z.ai/api/paas/v4/chat/completions"

# Stratum, model, route and pins, copied from each configuration's released
# primary capacity plan (section 3 of the protocol); Qwen3.7 Flash's released
# rows served the plain id only.  ``workers`` is the declared max-speed setting:
# eight on OpenRouter routes, four on Z.ai.
CONFIGURATIONS: tuple[dict[str, object], ...] = (
    {"stratum": "B", "slug": "gemini-38-flash", "model": "google/gemini-3.8-flash",
     "provider_only": ["google-ai-studio"], "omit_seed": True,
     "accepted": ["google/gemini-3.8-flash", "google/gemini-3.8-flash-20260902"], "workers": 8},
    {"stratum": "B", "slug": "qwen37-flash", "model": "qwen/qwen3.7-flash",
     "provider_only": ["alibaba"], "accepted": ["qwen/qwen3.7-flash"], "workers": 8},
    {"stratum": "B", "slug": "qwen38-flash", "model": "qwen/qwen3.8-flash",
     "provider_only": ["alibaba"],
     "accepted": ["qwen/qwen3.8-flash", "qwen/qwen3.8-flash-20260826"], "workers": 8},
    {"stratum": "B", "slug": "glm-53-flash", "model": "glm-5.3-flash",
     "harness": "react-routed@zai", "endpoint": ZAI, "api_key_env": "ZAI_API_KEY",
     "omit_seed": True, "accepted": ["glm-5.3-flash"], "workers": 4},
    {"stratum": "M", "slug": "claude-haiku-55", "model": "anthropic/claude-haiku-5.5",
     "provider_only": ["anthropic"], "omit_temperature": True, "omit_seed": True,
     "accepted": ["anthropic/claude-haiku-5.5", "anthropic/claude-haiku-5.5-20261007"], "workers": 8},
    {"stratum": "N", "slug": "gpt-6-luna", "model": "openai/gpt-6-luna",
     "provider_only": ["openai"], "omit_temperature": True, "omit_seed": True,
     "accepted": ["openai/gpt-6-luna", "openai/gpt-6-luna-20260922"], "workers": 8},
)
PILOT_SLUGS = ("gemini-38-flash", "qwen37-flash")
# Amendment 2, section A2.3: released route pins of the added configurations.
EXT_CONFIGURATIONS: tuple[dict[str, object], ...] = (
    {"stratum": "B", "slug": "glm-53", "model": "glm-5.3",
     "harness": "react-routed@zai", "endpoint": ZAI, "api_key_env": "ZAI_API_KEY",
     "omit_seed": True, "accepted": ["glm-5.3"], "workers": 4},
    {"stratum": "B", "slug": "deepseek-v4-pro-0813", "model": "deepseek/deepseek-v4-pro-0813",
     "provider_only": ["coreweave/fp8"], "omit_seed": True,
     "accepted": ["deepseek/deepseek-v4-pro-0813", "deepseek/deepseek-v4-pro-20260813"], "workers": 8},
)
EXT_PILOT_SLUGS = tuple(str(c["slug"]) for c in EXT_CONFIGURATIONS)  # A2.5: gate on every added configuration
LOW_CAP = 1024

# The eight endpoint cells of the registered main design (protocol section 4).
CELL_VISIBLE = {8, 128}
CELL_WORKFLOW = {"low", "high"}
CELL_REPRESENTATION = {"raw", "linked"}


def arms(high_cap: int) -> dict[str, int]:
    return {f"cap{LOW_CAP}": LOW_CAP, f"cap{high_cap}": high_cap}


def _configuration(spec: dict[str, object]) -> dict[str, object]:
    model = str(spec["model"])
    configuration: dict[str, object] = {
        "model": model,
        "harness": str(spec.get("harness", "react-routed@openrouter")),
        "endpoint": str(spec.get("endpoint", OPENROUTER)),
        "api_key_env": str(spec.get("api_key_env", "OPENROUTER_API_KEY")),
        "api_key_header": "Authorization",
        "accepted_response_models": list(spec["accepted"]),  # type: ignore[arg-type]
        "provider_allow_fallbacks": False,
        "provider_require_parameters": True,
        "provider_data_collection": "deny",
        "require_usage": True,
        "omit_parallel_tool_calls": True,
        "study_role": "primary",
        "discovery_enabled": False,
        "slug": f"{spec['slug']}-capsweep",
        "stratum": str(spec["stratum"]),
        "workers_per_job": int(spec["workers"]),
        "provider_concurrency_cap": int(spec["workers"]),
        "minimum_request_interval_seconds": 0.5,
    }
    if spec.get("provider_only"):
        configuration["provider_only"] = list(spec["provider_only"])  # type: ignore[arg-type]
    for flag in ("omit_temperature", "omit_seed"):
        if spec.get(flag):
            configuration[flag] = True
    return configuration


def build_panel(arm: str, cap: int) -> dict[str, object]:
    return {
        "frozen_for_study": True,
        "frozen_at": FROZEN_AT,
        "panel": f"{CAMPAIGN}-{arm}",
        "scientific_role": (
            "frozen acquisition panel for the completion-cap sweep campaign; "
            f"the {arm} arm differs from its companion in max_completion_tokens alone"
        ),
        "arm": arm,
        "configurations": [_configuration(spec) for spec in CONFIGURATIONS],
        "execution_policy": {
            "episode_policy": {
                "dynamic_shortlist": True,
                "early_stop_infeasible": True,
                "max_completion_tokens": cap,
                "max_total_tokens": 128000,
                "name": f"task-adaptive-routed-v1-{arm}",
                "tool_call_slack": 2,
                "turn_slack": 2,
            },
            "minimum_request_interval_seconds": 0.5,
            "model_lane_count": len(CONFIGURATIONS),
            "provider_concurrency_cap": 8,
            "timeout_seconds": 900,
            "transport_retries": 5,
            "workers_per_job": 8,
        },
    }


def _in_cells(condition) -> bool:
    return (condition.visible_tools in CELL_VISIBLE
            and condition.workflow.value in CELL_WORKFLOW
            and condition.representation.value in CELL_REPRESENTATION)


def _is_pilot(condition) -> bool:
    return (condition.visible_tools == 128 and condition.workflow.value == "high"
            and condition.representation.value == "raw")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--freeze-plans", action="store_true",
                        help="also invoke `bottleneckbench plan` for every (configuration, arm)")
    parser.add_argument("--high-cap", type=int, default=16384,
                        help="completion cap of the high arm (16384, or the route maximum after a failed pilot gate)")
    parser.add_argument("--extension", action="store_true",
                        help="build completion-cap-sweep-2 (amendment 2: GLM-5.3, DeepSeek V4 Pro 0813)")
    args = parser.parse_args()
    global CAMPAIGN, PLAN_DIR, PROTOCOL, CONFIGURATIONS, PILOT_SLUGS
    if args.extension:
        CAMPAIGN, PROTOCOL, CONFIGURATIONS, PILOT_SLUGS = EXT_CAMPAIGN, EXT_PROTOCOL, EXT_CONFIGURATIONS, EXT_PILOT_SLUGS
        PLAN_DIR = ROOT / "results/prospective-plans" / CAMPAIGN
    if args.high_cap <= LOW_CAP:
        raise SystemExit("the high cap must exceed the released cap")

    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    if not PROTOCOL.exists():
        raise SystemExit(f"missing frozen protocol: {PROTOCOL}")
    arm_caps = arms(args.high_cap)
    high_arm = f"cap{args.high_cap}"

    panels: dict[str, Path] = {}
    for arm, cap in arm_caps.items():
        path = PLAN_DIR / f"panel-{arm}.json"
        path.write_text(json.dumps(build_panel(arm, cap), indent=1, sort_keys=True) + "\n")
        panels[arm] = path
        print(f"panel  {path.relative_to(ROOT)}  ({len(CONFIGURATIONS)} configurations, cap={cap})")

    manifest = {
        "schema": "bottleneckbench-cap-sweep-campaign-v1",
        "campaign": CAMPAIGN,
        "protocol": str(PROTOCOL.relative_to(ROOT)),
        "arms": arm_caps,
        "high_arm": high_arm,
        "low_arm": f"cap{LOW_CAP}",
        "pilot_slugs": list(PILOT_SLUGS),
        "paper_campaign": "completion-cap-sweep",  # amendment 2: both directories are one sweep
        "sibling_campaigns": ["completion-cap-sweep-1", EXT_CAMPAIGN],
        "configurations": [{"slug": s["slug"], "model": s["model"], "stratum": s["stratum"],
                            "harness": s.get("harness", "react-routed@openrouter"),
                            "api_key_env": s.get("api_key_env", "OPENROUTER_API_KEY")}
                           for s in CONFIGURATIONS],
    }
    (PLAN_DIR / "campaign.json").write_text(json.dumps(manifest, indent=1) + "\n")

    if not args.freeze_plans:
        print("\n(pass --freeze-plans to freeze the per-configuration plans)")
        return 0

    sys.path.insert(0, str(ROOT / "src"))
    from bottleneckbench.core.schema import stable_hash  # noqa: E402
    from bottleneckbench.experiments import freeze_plan, load_plan  # noqa: E402

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
            "basis": ("completion-cap arm of an already admitted frozen main design, restricted to "
                      "its eight endpoint cells; split, families and every condition field are "
                      "unchanged and only episode_policy.max_completion_tokens differs between arms"),
            "arm": arm,
            "max_completion_tokens": arm_caps[arm],
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
            full = PLAN_DIR / f"{spec['slug']}-{arm}-full.json"
            out = PLAN_DIR / f"{spec['slug']}-{arm}-capacity.json"
            cmd = [sys.executable, "-m", "bottleneckbench.cli", "plan",
                   "--kind", "capacity", "--split", "main",
                   "--model", str(spec["model"]), "--harness", harness,
                   "--frozen-at", FROZEN_AT, "--audit-dir", str(AUDIT_DIR),
                   "--protocol", str(PROTOCOL), "--model-panel", str(panel_path),
                   "--admission", str(admissions[arm]), "--output", str(full)]
            proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, env=env)
            if proc.returncode != 0:
                failures += 1
                detail = (proc.stderr.strip().splitlines() or ["(no stderr)"])[-1]
                print(f"  FAIL {spec['slug']:<18} {arm}: {detail[:160]}")
                continue
            # Amendment 1: the runner validates the complete frozen matrix, so the
            # plan is kept whole; the eight endpoint cells are selected at analysis.
            plan = load_plan(full)
            full.rename(out)
            check = load_plan(out)
            assert len(check.conditions) == 432, (spec["slug"], arm, len(check.conditions))
            endpoint = sum(1 for c in check.conditions if _in_cells(c))
            print(f"  ok   {spec['slug']:<18} {arm}: {len(check.conditions)} conditions "
                  f"({endpoint} endpoint-cell), plan_hash={check.plan_hash[:12]}")
    total = len(CONFIGURATIONS) * len(arm_caps)
    print(f"\n{total - failures} of {total} plans frozen; protocol_hash={protocol_hash[:12]}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
