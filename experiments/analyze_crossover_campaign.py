#!/usr/bin/env python3
"""Score the diagnosis x repair crossover campaign.

Answers the question frozen in `protocols/diagnosis-repair-crossover-2026-09-04.md`:
does the recorded failure signature of a configuration predict which repair
recovers its visible-menu loss?

The primary quantity is the **budget x menu interaction**: the paired
within-family visible-menu contrast (V=128 minus V=8) at a 4,096-token completion
budget minus the same contrast at 1,024, estimated separately in each stratum.
Both arms are acquired in this campaign, so the contrast never compares a new
treatment against an old baseline.

Every assigned run enters the contrast.  Truncation is an *outcome* of the
assigned menu level, so conditioning the comparison on episodes that avoided it
would condition on a post-treatment variable; the truncation rate is reported
separately as the manipulation check instead.

Three protocol rules are applied here rather than left to the reader:

* **section 6, manipulation gate** --- the *primary* stratum-B reading uses only
  configurations whose V=128 truncation share fell below 5% in the cap4096 arm;
  every configuration is still reported in the descriptive blocks;
* **section 7.3, quarantine** --- the runner's per-job transport-failure rule is
  reconstructed from released rows, without its private runtime ledger. A
  configuration with a quarantined arm leaves pooled contrasts; its available
  rows enter a sensitivity block whose missingness bias is not identified;
* **section 7, row exclusion** --- provider-failure rows (including interrupted
  trajectories with earlier usage), or rows reporting no usage, are excluded and counted; no
  exclusion may reference a row's verdict.

Offline and free.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import random
from statistics import fmean
from typing import Any, Iterable, Mapping, Sequence

if __package__:
    from .run_crossover_campaign import (
        CEILING_USD, TRANSPORT_QUARANTINE_MIN_ROWS, TRANSPORT_QUARANTINE_SHARE, job_cost,
    )
else:
    from run_crossover_campaign import (
        CEILING_USD, TRANSPORT_QUARANTINE_MIN_ROWS, TRANSPORT_QUARANTINE_SHARE, job_cost,
    )

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = "diagnosis-repair-crossover-1"
EXECUTION = ROOT / "results/prospective-execution" / CAMPAIGN
ARMS = ("cap1024", "cap4096")

# Stratum assignment is frozen in protocol section 2 from the *released* panel,
# which is independent data from a prior campaign.  It is not re-derived here, so
# no outcome of this campaign can move a configuration between strata.
STRATA = {
    "inclusionai/ling-3.0-flash": "B",
    "meta/muse-glimmer-30b": "B",
    "qwen/qwen3.8-flash": "B",
    "glm-5.3-flash": "B",
    "z-ai/glm-5.2": "S",
    "deepseek/deepseek-v4-flash-0731": "S",
    "openai/gpt-5.6-luna": "N",
}
STRATA_BASIS = {
    "B": "released V=128 truncation >= 0.20 and completion_no_call signature",
    "S": "released V=128 truncation <= 0.15 and visible-menu effect <= -8 points",
    "N": "released V=128 truncation <= 0.05 and visible-menu effect > -5 points",
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
SLUG_OF = {model: slug for slug, model in SLUGS.items()}


def select_reporting_models(models: set[str]) -> None:
    """Restrict acquisition inputs before computing any pooled quantity."""
    global STRATA, SLUGS, SLUG_OF
    STRATA = {model: stratum for model, stratum in STRATA.items() if model in models}
    SLUGS = {slug: model for slug, model in SLUGS.items() if model in models}
    SLUG_OF = {model: slug for slug, model in SLUGS.items()}
# The interaction threshold fixed in protocol section 6, in percentage points,
# and the manipulation-check ceiling on V=128 truncation in the cap4096 arm.
DECISION_MARGIN = 5.0
MANIPULATION_CEILING = 0.05


def _excluded(row: Mapping[str, Any]) -> bool:
    """Executable exclusion: provider failure (possibly after earlier calls), or no usage."""
    if str(row.get("status")) == "provider_failure":
        return True
    usage = row.get("usage") or {}
    return not usage or usage.get("prompt_tokens") in (None, 0)


def _success(row: Mapping[str, Any]) -> bool:
    verdict = row.get("verdict")
    return isinstance(verdict, dict) and verdict.get("verified_success") is True


def _truncated(row: Mapping[str, Any]) -> bool:
    return "model_finish_length" in [
        str(item).split(":")[0] for item in (row.get("errors") or [])
    ]


def _execution_accounting() -> dict[str, Any]:
    """Replay costs and completed-job quarantine checks from retained raw rows."""
    jobs = []
    quarantined = set()
    for path in sorted(EXECUTION.glob("repeat-*/*.jsonl")):
        slug, arm = path.stem.rsplit("-", 1)
        if slug not in SLUGS or arm not in ARMS:
            continue
        usd, count, failures = job_cost(path, SLUGS[slug])
        jobs.append({"model": SLUGS[slug], "arm": arm, "usd": usd,
                     "rows": count, "transport_failures": failures})
        if count >= TRANSPORT_QUARANTINE_MIN_ROWS and failures / count > TRANSPORT_QUARANTINE_SHARE:
            quarantined.add((slug, arm))
    return {"jobs": jobs, "quarantined": sorted(quarantined),
            "spent_usd": round(sum(job["usd"] for job in jobs), 4),
            "ceiling_usd": CEILING_USD}


def load_rows(quarantined: set[tuple[str, str]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows: list[dict[str, Any]] = []
    counts: dict[str, int] = defaultdict(int)
    for repeat_dir in sorted(EXECUTION.glob("repeat-*")):
        repeat = int(repeat_dir.name.split("-")[1])
        for path in sorted(repeat_dir.glob("*.jsonl")):
            slug, arm = path.stem.rsplit("-", 1)
            if slug not in SLUGS or arm not in ARMS:
                continue
            in_quarantine = (slug, arm) in quarantined
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                counts["read"] += 1
                if in_quarantine:
                    counts["read_in_quarantined_arm"] += 1
                if _excluded(row):
                    counts[f"excluded:{slug}"] += 1
                    counts["excluded"] += 1
                    continue
                condition = row.get("condition") or {}
                usage = row.get("usage") or {}
                rows.append({
                    "model": SLUGS[slug], "slug": slug, "arm": arm, "repeat": repeat,
                    "stratum": STRATA[SLUGS[slug]],
                    "quarantined": in_quarantine,
                    "family": str(condition.get("family_id")),
                    "intent": str(condition.get("intent")),
                    "visible": int(condition.get("visible_tools", 0)),
                    "workflow": str(condition.get("workflow")),
                    "representation": str(condition.get("representation")),
                    "success": _success(row),
                    "truncated": _truncated(row),
                    "calls": len(row.get("trace") or []),
                    "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                    "completion_tokens": int(usage.get("completion_tokens") or 0),
                    "condition_id": row.get("condition_id"),
                })
                counts["kept"] += 1
                if in_quarantine:
                    counts["kept_in_quarantined_arm"] += 1
    return rows, dict(counts)


def _visible_pairs(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One paired observation per (model, arm, repeat, family, workflow, representation).

    The visible-menu contrast is identified only where both menu levels were run on
    the otherwise identical cell, which is what pairing on the remaining condition
    fields enforces.
    """
    index: dict[tuple, dict[int, Mapping[str, Any]]] = defaultdict(dict)
    for row in rows:
        if row["visible"] not in (8, 128):
            continue
        key = (row["model"], row["arm"], row["repeat"], row["family"],
               row["workflow"], row["representation"])
        index[key][row["visible"]] = row
    pairs = []
    for key, sides in index.items():
        if 8 in sides and 128 in sides:
            model, arm, repeat, family, workflow, representation = key
            pairs.append({
                "model": model, "arm": arm, "repeat": repeat, "family": family,
                "workflow": workflow, "representation": representation,
                "stratum": STRATA[model],
                "delta": float(sides[128]["success"]) - float(sides[8]["success"]),
            })
    return pairs


def _boot_family(values_by_family: Mapping[str, list[float]], samples: int,
                 seed: int) -> tuple[float | None, float | None, float | None]:
    """Point estimate and 95% interval in points, resampling semantic families."""
    families = sorted(values_by_family)
    if not families:
        return None, None, None
    point = fmean([v for f in families for v in values_by_family[f]])
    rng = random.Random(seed)
    draws = []
    for _ in range(samples):
        picked = rng.choices(families, k=len(families))
        pooled = [v for f in picked for v in values_by_family[f]]
        if pooled:
            draws.append(fmean(pooled))
    draws.sort()
    lo = draws[int(0.025 * (len(draws) - 1))]
    hi = draws[int(0.975 * (len(draws) - 1))]
    return point * 100.0, lo * 100.0, hi * 100.0


def _by_family(pairs: Iterable[Mapping[str, Any]]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = defaultdict(list)
    for pair in pairs:
        out[str(pair["family"])].append(float(pair["delta"]))
    return dict(out)


def _selected(pairs: Iterable[Mapping[str, Any]], stratum: str | None,
              models: set[str] | None) -> list[Mapping[str, Any]]:
    return [p for p in pairs
            if (stratum in (None, "all") or p["stratum"] == stratum)
            and (models is None or p["model"] in models)]


def _arm_contrast(pairs: Sequence[Mapping[str, Any]], arm: str, samples: int, seed: int,
                  stratum: str | None = None, models: set[str] | None = None) -> dict[str, Any]:
    chosen = [p for p in _selected(pairs, stratum, models) if p["arm"] == arm]
    point, lo, hi = _boot_family(_by_family(chosen), samples, seed)
    return {"estimate_pp": point, "ci95": [lo, hi], "pairs": len(chosen)}


def _interaction(pairs: Sequence[Mapping[str, Any]], samples: int, seed: int,
                 stratum: str | None = None, models: set[str] | None = None) -> dict[str, Any]:
    """The arm-to-arm difference of the visible contrast, paired on the same cell."""
    keyed: dict[tuple, dict[str, float]] = defaultdict(dict)
    for pair in _selected(pairs, stratum, models):
        key = (pair["model"], pair["repeat"], pair["family"],
               pair["workflow"], pair["representation"])
        keyed[key][pair["arm"]] = float(pair["delta"])
    diffs: dict[str, list[float]] = defaultdict(list)
    for key, arms in keyed.items():
        if len(arms) == 2:
            diffs[key[2]].append(arms["cap4096"] - arms["cap1024"])
    point, lo, hi = _boot_family(dict(diffs), samples, seed)
    return {"estimate_pp": point, "ci95": [lo, hi],
            "paired_cells": sum(len(v) for v in diffs.values())}


def _reading(block: Mapping[str, Any]) -> str:
    point, (lo, hi) = block["estimate_pp"], block["ci95"]
    if point is None:
        return "not estimable"
    if lo > DECISION_MARGIN:
        return "budget repair helps"
    if hi < DECISION_MARGIN:
        return "below the +5 pp decision margin"
    return "inconclusive at this precision"


def _stratum_block(pairs: Sequence[Mapping[str, Any]], samples: int, seed: int,
                   stratum: str, models: set[str] | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {
        arm: _arm_contrast(pairs, arm, samples, seed, stratum, models) for arm in ARMS
    }
    entry["interaction_pp"] = _interaction(pairs, samples, seed, stratum, models)
    entry["reading"] = _reading(entry["interaction_pp"])
    entry["configurations"] = sorted({p["model"] for p in _selected(pairs, stratum, models)})
    return entry


def _crossover_verdict(primary: Mapping[str, Any]) -> str:
    """Protocol section 6 table: manipulation-gated B and non-quarantined S."""
    b, s = primary["B"], primary["S"]
    if not b["configurations"]:
        return ("not readable as designed: no stratum-B configuration passed the "
                "manipulation check")
    if not s["configurations"]:
        return "not readable as designed: no stratum-S configuration keeps both arms"
    b_lo, b_hi = b["interaction_pp"]["ci95"]
    s_lo, s_hi = s["interaction_pp"]["ci95"]
    if b_lo > DECISION_MARGIN and s_hi < DECISION_MARGIN:
        return "crossover supported"
    if b_lo > DECISION_MARGIN and s_lo > DECISION_MARGIN:
        return "uniform budget effect"
    if b_hi < DECISION_MARGIN and s_hi < DECISION_MARGIN:
        return "no budget effect"
    return "inconclusive"


def _mean(values: Sequence[float]) -> float | None:
    return fmean(values) if values else None


def analyze(samples: int, seed: int) -> dict[str, Any]:
    ledger = _execution_accounting()
    quarantined = {(str(slug), str(arm)) for slug, arm in ledger.get("quarantined", [])}
    rows, counts = load_rows(quarantined)
    quarantined_models = {SLUGS[slug] for slug, _ in quarantined if slug in SLUGS}
    # Rows outside the quarantined arm: the within-arm quantities (repeatability,
    # representation, telemetry) keep a quarantined configuration's other arm.
    rows_primary = [r for r in rows if not r["quarantined"]]
    # The paired population: a configuration with a quarantined arm leaves the
    # stratum contrasts entirely. Each arm uses its available V pairs; the
    # interaction requires both pairs, so missing responses can change its base.
    pairs_all = _visible_pairs(rows)
    pairs = _visible_pairs([r for r in rows_primary if r["model"] not in quarantined_models])

    # --- descriptive: every non-quarantined configuration, per stratum --------
    contrasts = {s: _stratum_block(pairs, samples, seed, s) for s in ("B", "S", "N", "all")}
    sensitivity = ({s: _stratum_block(pairs_all, samples, seed, s) for s in ("B", "S", "N", "all")}
                   if quarantined else None)

    # --- manipulation check: truncation must fall in the cap4096 arm ----------
    manipulation: dict[str, Any] = {}
    for model in sorted({r["model"] for r in rows}):
        entry: dict[str, Any] = {}
        for arm in ARMS:
            at128 = [r for r in rows if r["model"] == model and r["arm"] == arm
                     and r["visible"] == 128]
            entry[arm] = {
                "rows": len(at128),
                "quarantined": (SLUG_OF[model], arm) in quarantined,
                "truncation_rate": _mean([float(r["truncated"]) for r in at128]),
                "zero_call_share": _mean([float(r["calls"] == 0) for r in at128]),
                "verified_success": _mean([float(r["success"]) for r in at128]),
            }
        entry["stratum"] = STRATA[model]
        rate = entry["cap4096"]["truncation_rate"]
        entry["manipulation_passed"] = rate is not None and rate < MANIPULATION_CEILING
        manipulation[model] = entry
    passed = {m for m, e in manipulation.items() if e["manipulation_passed"]
              and not any((SLUG_OF[m], arm) in quarantined for arm in ARMS)}

    # --- primary reading (section 6): manipulation-gated strata ---------------
    primary: dict[str, Any] = {}
    # Section 6 gates stratum B on the manipulation check; S and N are read on
    # every configuration that keeps both arms.
    for stratum in ("B", "S", "N"):
        in_stratum = {m for m in manipulation
                      if STRATA[m] == stratum and m not in quarantined_models}
        eligible = in_stratum & passed if stratum == "B" else in_stratum
        if eligible:
            primary[stratum] = _stratum_block(pairs, samples, seed, stratum, eligible)
        else:
            primary[stratum] = {
                "configurations": [],
                "interaction_pp": {"estimate_pp": None, "ci95": [None, None],
                                   "paired_cells": 0},
                "reading": ("no configuration passed the manipulation check"
                            if stratum == "B" else "no configuration keeps both arms"),
            }
    primary["verdict"] = _crossover_verdict(primary)

    # --- per configuration: both arms and the interaction ---------------------
    per_configuration: dict[str, Any] = {}
    for model in sorted({r["model"] for r in rows}):
        block = {arm: _arm_contrast(pairs_all, arm, samples, seed, None, {model}) for arm in ARMS}
        block["interaction_pp"] = _interaction(pairs_all, samples, seed, None, {model})
        block["stratum"] = STRATA[model]
        block["quarantined_arms"] = [arm for arm in ARMS if (SLUG_OF[model], arm) in quarantined]
        block["manipulation_passed"] = manipulation[model]["manipulation_passed"]
        per_configuration[model] = block

    # --- run-to-run variability: the quantity one realization cannot give -----
    repeat_index: dict[tuple, dict[int, bool]] = defaultdict(dict)
    for row in rows_primary:
        key = (row["model"], row["arm"], row["condition_id"])
        repeat_index[key][row["repeat"]] = row["success"]
    both = {k: v for k, v in repeat_index.items() if len(v) == 2}
    disagreement = _mean([float(v[1] != v[2]) for v in both.values()])
    by_model_disagreement = {
        model: _mean([float(v[1] != v[2]) for k, v in both.items() if k[0] == model])
        for model in sorted({k[0] for k in both})
    }
    by_arm_disagreement = {
        arm: _mean([float(v[1] != v[2]) for k, v in both.items() if k[1] == arm])
        for arm in ARMS
    }

    # --- representation contrasts, at the released completion budget ----------
    representation: dict[str, Any] = {}
    base = {(r["model"], r["arm"], r["repeat"], r["family"]): r
            for r in rows_primary if r["visible"] == 8 and r["workflow"] == "low"
            and r["representation"] == "raw"}
    for encoding in ("generic_compact", "typed", "linked"):
        deltas: dict[str, list[float]] = defaultdict(list)
        for row in rows_primary:
            if row["arm"] != "cap1024":
                continue
            if (row["visible"], row["workflow"], row["representation"]) != (8, "low", encoding):
                continue
            anchor = base.get((row["model"], row["arm"], row["repeat"], row["family"]))
            if anchor is not None:
                deltas[row["family"]].append(float(row["success"]) - float(anchor["success"]))
        point, lo, hi = _boot_family(dict(deltas), samples, seed)
        representation[encoding] = {"estimate_pp": point, "ci95": [lo, hi],
                                    "pairs": sum(len(v) for v in deltas.values())}

    # --- cost and telemetry per arm (secondary quantity 5) --------------------
    usd_by_model: dict[str, float] = defaultdict(float)
    usd_by_arm: dict[str, float] = defaultdict(float)
    for job in ledger.get("jobs", []):
        usd_by_model[str(job.get("model"))] += float(job.get("usd") or 0.0)
        usd_by_arm[str(job.get("arm"))] += float(job.get("usd") or 0.0)
    tokens: dict[str, Any] = {}
    for arm in ARMS:
        chosen = [r for r in rows_primary if r["arm"] == arm]
        tokens[arm] = {
            "rows": len(chosen),
            "mean_prompt_tokens": _mean([r["prompt_tokens"] for r in chosen]),
            "mean_completion_tokens": _mean([r["completion_tokens"] for r in chosen]),
            "usd": round(usd_by_arm.get(arm, 0.0), 4),
        }
    quarantine_report = [
        {"slug": slug, "model": SLUGS.get(slug, slug), "arm": arm,
         "rows_kept": sum(1 for r in rows if r["slug"] == slug and r["arm"] == arm)}
        for slug, arm in sorted(quarantined)
    ]

    return {
        "schema": "bottleneckbench-diagnosis-repair-crossover-v3",
        "campaign": CAMPAIGN,
        "protocol": "protocols/diagnosis-repair-crossover-2026-09-04.md",
        "decision_margin_pp": DECISION_MARGIN,
        "manipulation_ceiling": MANIPULATION_CEILING,
        "strata": STRATA,
        "strata_basis": STRATA_BASIS,
        "rows": counts,
        "configurations": sorted({r["model"] for r in rows}),
        "families": len({r["family"] for r in rows}),
        "repeats_observed": sorted({r["repeat"] for r in rows}),
        "primary_reading": primary,
        "visible_menu": contrasts,
        "visible_menu_including_quarantined": sensitivity,
        "per_configuration": per_configuration,
        "manipulation_check": manipulation,
        "quarantined_arms": quarantine_report,
        "run_to_run": {
            "conditions_with_two_runs": len(both),
            "disagreement_rate": disagreement,
            "by_configuration": by_model_disagreement,
            "by_arm": by_arm_disagreement,
            "note": "share of conditions whose verified success differs between repeats",
        },
        "representation_cap1024": representation,
        "cost": {
            "spent_usd": ledger.get("spent_usd"),
            "ceiling_usd": ledger.get("ceiling_usd"),
            "by_model_usd": {k: round(v, 4) for k, v in sorted(usd_by_model.items())},
            "by_arm": tokens,
            "note": ("spent_usd is retained-row usage priced against frozen section 4 rates, "
                     "not an invoice or a complete campaign charge. It excludes discarded "
                     "duplicate rows (historically estimated at about USD 3), preflight calls "
                     "and any unrecorded usage. The private runtime ledger is not required."),
        },
        "bootstrap": {"samples": samples, "seed": seed, "cluster": "semantic family"},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path,
                        default=ROOT / "results/analysis/crossover-campaign.json")
    parser.add_argument("--bootstrap-samples", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=20260904)
    parser.add_argument("--panel", type=Path)
    args = parser.parse_args()
    if args.panel:
        select_reporting_models({row["model"] for row in json.loads(args.panel.read_text())["configurations"]})

    report = analyze(args.bootstrap_samples, args.seed)
    args.output.write_text(json.dumps(report, indent=1, sort_keys=True, allow_nan=False) + "\n")

    print(f"rows kept {report['rows'].get('kept', 0)} of {report['rows'].get('read', 0)} "
          f"(excluded {report['rows'].get('excluded', 0)}), "
          f"{report['families']} families, repeats {report['repeats_observed']}")
    if report["quarantined_arms"]:
        print("quarantined arms:", ", ".join(
            f"{q['slug']}/{q['arm']} ({q['rows_kept']} usable rows)" for q in report["quarantined_arms"]))

    def fmt(block: Mapping[str, Any]) -> str:
        if block["estimate_pp"] is None:
            return "       n/a       "
        return (f"{block['estimate_pp']:+6.1f} [{block['ci95'][0]:+5.1f},"
                f"{block['ci95'][1]:+5.1f}]")

    print(f"\nDESCRIPTIVE (quarantine excluded)\n{'stratum':<8} {'V@1024':>19} "
          f"{'V@4096':>19} {'interaction':>19}  reading")
    for stratum, entry in report["visible_menu"].items():
        print(f"{stratum:<8} {fmt(entry['cap1024']):>19} {fmt(entry['cap4096']):>19} "
              f"{fmt(entry['interaction_pp']):>19}  {entry['reading']}")

    print("\nPRIMARY (section 6, manipulation-gated)")
    for stratum in ("B", "S", "N"):
        entry = report["primary_reading"][stratum]
        print(f"  {stratum}: configs={entry['configurations'] or '-'}  "
              f"{fmt(entry['interaction_pp'])}  {entry['reading']}")
    print(f"  verdict: {report['primary_reading']['verdict']}")

    print(f"\n{'configuration':<34} {'str':<4} {'V@1024':>19} {'V@4096':>19} "
          f"{'interaction':>19} {'trunc@4096':>10}  manip")
    for model, entry in report["per_configuration"].items():
        rate = report["manipulation_check"][model]["cap4096"]["truncation_rate"]
        flag = "pass" if entry["manipulation_passed"] else "FAIL"
        if entry["quarantined_arms"]:
            flag += f" (quarantined {','.join(entry['quarantined_arms'])})"
        print(f"{model:<34} {entry['stratum']:<4} {fmt(entry['cap1024']):>19} "
              f"{fmt(entry['cap4096']):>19} {fmt(entry['interaction_pp']):>19} "
              f"{(100 * rate if rate is not None else float('nan')):9.1f}%  {flag}")

    rr = report["run_to_run"]
    if rr["disagreement_rate"] is not None:
        print(f"\nrun-to-run disagreement: {100 * rr['disagreement_rate']:.1f}% over "
              f"{rr['conditions_with_two_runs']} twice-run conditions; by arm "
              + ", ".join(f"{a}={100 * v:.1f}%" for a, v in rr["by_arm"].items() if v is not None))
    cost = report["cost"]
    print(f"spend: USD {cost['spent_usd']} of {cost['ceiling_usd']}; by model "
          + ", ".join(f"{k.split('/')[-1]}={v:.2f}" for k, v in cost["by_model_usd"].items()))
    print(f"\nwrote {args.output.relative_to(ROOT) if args.output.is_relative_to(ROOT) else args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
