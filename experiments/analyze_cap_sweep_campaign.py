#!/usr/bin/env python3
"""Score the completion-cap sweep campaign.

Answers the question frozen in `protocols/completion-cap-sweep-2026-10-12.md`:
does a per-request completion cap large enough to stop truncation change the
visible-menu contrast of the truncation-heavy configurations, and what fills the
responses that still truncate?

Primary quantity (section 6): the paired within-family visible-menu contrast
(V=128 minus V=8) in each arm over the four endpoint W x D cells, per stratum,
and the arm difference (cap by menu interaction).  Both arms are acquired in this
campaign.  Every assigned run enters the contrast; truncation is an outcome of
the assigned menu level, so it is reported as the manipulation check rather than
conditioned on.

Protocol rules applied here:

* section 7, manipulation gate: the primary stratum-B reading uses only
  configurations whose V=128 truncation share in the high arm is below 5%;
* section 7, decision table: read from the stratum-B interaction interval and
  the high-arm V contrast interval;
* section 8, quarantine and row exclusion: reconstructed from released rows
  without the runtime ledger; provider-failure rows or rows without usage are
  excluded and counted, never by verdict;
* amendment 1: the checkpoint cells (V=32, medium W, compact, typed) are
  acquired and reported descriptively but never enter the primary pairs;
* amendment 2: the campaign directories `completion-cap-sweep-1` and
  `completion-cap-sweep-2` are one sweep; rows of both are read as one dataset,
  the primary reading pools every stratum-B configuration that passes the
  manipulation check, and the pooled reading over the four originally
  registered configurations is retained as `registered_subset`.

Offline and free.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import random
from statistics import fmean, median
from typing import Any, Iterable, Mapping, Sequence

if __package__:
    from .run_cap_sweep_campaign import (
        CAMPAIGNS as RUNNER_CAMPAIGNS, CEILINGS, TRANSPORT_QUARANTINE_MIN_ROWS, TRANSPORT_QUARANTINE_SHARE, job_cost,
    )
else:
    from run_cap_sweep_campaign import (
        CAMPAIGNS as RUNNER_CAMPAIGNS, CEILINGS, TRANSPORT_QUARANTINE_MIN_ROWS, TRANSPORT_QUARANTINE_SHARE, job_cost,
    )

ROOT = Path(__file__).resolve().parents[1]
# Amendment 2: the two campaign directories are one sweep.  Each has its own
# manifest, protocol hash, rows and ledger; this analyzer reads them together.
CAMPAIGNS: tuple[str, ...] = tuple(c for c in RUNNER_CAMPAIGNS
                                   if (ROOT / "results/prospective-plans" / c / "campaign.json").exists())
CAMPAIGN = "completion-cap-sweep"  # the sweep as the paper reports it
REGISTERED_CAMPAIGN = "completion-cap-sweep-1"
PROTOCOL = [str(RUNNER_CAMPAIGNS[c]["protocol"]) for c in CAMPAIGNS]
PLAN_DIRS = {c: ROOT / "results/prospective-plans" / c for c in CAMPAIGNS}
EXECUTIONS = {c: ROOT / "results/prospective-execution" / c for c in CAMPAIGNS}
MANIFESTS = {c: json.loads((PLAN_DIRS[c] / "campaign.json").read_text()) for c in CAMPAIGNS}
MANIFEST = MANIFESTS[CAMPAIGNS[0]]
for _c, _m in MANIFESTS.items():
    if _m["arms"] != MANIFEST["arms"]:
        raise SystemExit(f"{_c}: arms differ from {CAMPAIGNS[0]}; the directories are not one sweep")
ARMS: tuple[str, ...] = tuple(MANIFEST["arms"])
LOW_ARM, HIGH_ARM = str(MANIFEST["low_arm"]), str(MANIFEST["high_arm"])
RELEASED = ROOT / "results/reporting-panel-2026-10-13-final21/capacity-cluster-sensitivity.json"
PANEL = ROOT / "results/reporting-panel-2026-10-13-final21/release-panel.json"

# Stratum assignment is frozen in protocol section 3 and amendment 2 from the released panel.
STRATA = {c["model"]: c["stratum"] for m in MANIFESTS.values() for c in m["configurations"]}
CAMPAIGN_OF = {c["model"]: name for name, m in MANIFESTS.items() for c in m["configurations"]}
REGISTERED_MODELS = {c["model"] for c in MANIFESTS.get(REGISTERED_CAMPAIGN, {"configurations": []})["configurations"]}
STRATA_BASIS = {
    "B": "released V=128 truncation >= 20%",
    "M": "released V=128 truncation 5 to 20%",
    "N": "released V=128 truncation 0 and visible-menu effect within 3 pp of zero",
}
SLUGS = {c["slug"]: c["model"] for m in MANIFESTS.values() for c in m["configurations"]}
SLUG_OF = {model: slug for slug, model in SLUGS.items()}
DECISION_MARGIN = 5.0
MANIPULATION_CEILING = 0.05
ENDPOINT_WORKFLOW = {"low", "high"}
ENDPOINT_REPRESENTATION = {"raw", "linked"}


def _excluded(row: Mapping[str, Any]) -> bool:
    if str(row.get("status")) == "provider_failure":
        return True
    usage = row.get("usage") or {}
    return not usage or usage.get("prompt_tokens") in (None, 0)


def _success(row: Mapping[str, Any]) -> bool:
    verdict = row.get("verdict")
    return isinstance(verdict, dict) and verdict.get("verified_success") is True


def _truncated(row: Mapping[str, Any]) -> bool:
    return "model_finish_length" in [str(item).split(":")[0] for item in (row.get("errors") or [])]


def _execution_accounting(models: set[str] | None = None) -> dict[str, Any]:
    jobs = []
    quarantined = set()
    for campaign, execution in EXECUTIONS.items():
        for path in sorted(execution.glob("repeat-*/*.jsonl")):
            slug, arm = path.stem.rsplit("-", 1)
            if slug not in SLUGS or arm not in ARMS or (models is not None and SLUGS[slug] not in models):
                continue
            usd, count, failures, truncated = job_cost(path, SLUGS[slug])
            jobs.append({"campaign": campaign, "model": SLUGS[slug], "arm": arm,
                         "repeat": int(path.parent.name.split("-")[1]),
                         "usd": usd, "rows": count, "transport_failures": failures, "truncated": truncated})
            if count >= TRANSPORT_QUARANTINE_MIN_ROWS and failures / count > TRANSPORT_QUARANTINE_SHARE:
                quarantined.add((slug, arm))
    return {"jobs": jobs, "quarantined": sorted(quarantined),
            "spent_usd": round(sum(job["usd"] for job in jobs), 4),
            "spent_usd_by_campaign": {c: round(sum(j["usd"] for j in jobs if j["campaign"] == c), 4) for c in CAMPAIGNS},
            "ceiling_usd": round(sum(CEILINGS[c] for c in CAMPAIGNS), 2),
            "ceiling_usd_by_campaign": {c: CEILINGS[c] for c in CAMPAIGNS}}


def load_rows(quarantined: set[tuple[str, str]],
              models: set[str] | None = None) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows: list[dict[str, Any]] = []
    counts: dict[str, int] = defaultdict(int)
    for repeat_dir in sorted(d for execution in EXECUTIONS.values() for d in execution.glob("repeat-*")):
        repeat = int(repeat_dir.name.split("-")[1])
        for path in sorted(repeat_dir.glob("*.jsonl")):
            slug, arm = path.stem.rsplit("-", 1)
            if slug not in SLUGS or arm not in ARMS or (models is not None and SLUGS[slug] not in models):
                continue
            in_quarantine = (slug, arm) in quarantined
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                counts["read"] += 1
                if _excluded(row):
                    counts["excluded"] += 1
                    counts[f"excluded:{slug}"] += 1
                    continue
                condition = row.get("condition") or {}
                usage = row.get("usage") or {}
                efficiency = row.get("efficiency") or {}
                rows.append({
                    "model": SLUGS[slug], "slug": slug, "arm": arm, "repeat": repeat,
                    "stratum": STRATA[SLUGS[slug]], "quarantined": in_quarantine,
                    "family": str(condition.get("family_id")), "intent": str(condition.get("intent")),
                    "visible": int(condition.get("visible_tools", 0)),
                    "workflow": str(condition.get("workflow")),
                    "representation": str(condition.get("representation")),
                    "endpoint_cell": (str(condition.get("workflow")) in ENDPOINT_WORKFLOW
                                      and str(condition.get("representation")) in ENDPOINT_REPRESENTATION),
                    "success": _success(row), "truncated": _truncated(row),
                    "calls": len(row.get("trace") or []),
                    "call_attempts": len(row.get("call_attempts") or []),
                    "answer_empty": not str(row.get("answer") or "").strip(),
                    "requests": int(efficiency.get("model_requests_attempted") or 0),
                    "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                    "completion_tokens": int(usage.get("completion_tokens") or 0),
                    "condition_id": row.get("condition_id"),
                })
                counts["kept"] += 1
    return rows, dict(counts)


def _visible_pairs(rows: Sequence[Mapping[str, Any]], endpoint_only: bool) -> list[dict[str, Any]]:
    index: dict[tuple, dict[int, Mapping[str, Any]]] = defaultdict(dict)
    for row in rows:
        if row["visible"] not in (8, 128) or (endpoint_only and not row["endpoint_cell"]):
            continue
        key = (row["model"], row["arm"], row["repeat"], row["family"], row["workflow"], row["representation"])
        index[key][row["visible"]] = row
    pairs = []
    for key, sides in index.items():
        if 8 in sides and 128 in sides:
            model, arm, repeat, family, workflow, representation = key
            pairs.append({"model": model, "arm": arm, "repeat": repeat, "family": family,
                          "workflow": workflow, "representation": representation, "stratum": STRATA[model],
                          "delta": float(sides[128]["success"]) - float(sides[8]["success"])})
    return pairs


def _boot_family(values_by_family: Mapping[str, list[float]], samples: int, seed: int):
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
    return point * 100.0, draws[int(0.025 * (len(draws) - 1))] * 100.0, draws[int(0.975 * (len(draws) - 1))] * 100.0


def _by_family(pairs: Iterable[Mapping[str, Any]]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = defaultdict(list)
    for pair in pairs:
        out[str(pair["family"])].append(float(pair["delta"]))
    return dict(out)


def _selected(pairs, stratum, models):
    return [p for p in pairs if (stratum in (None, "all") or p["stratum"] == stratum)
            and (models is None or p["model"] in models)]


def _arm_contrast(pairs, arm, samples, seed, stratum=None, models=None) -> dict[str, Any]:
    chosen = [p for p in _selected(pairs, stratum, models) if p["arm"] == arm]
    point, lo, hi = _boot_family(_by_family(chosen), samples, seed)
    return {"estimate_pp": point, "ci95": [lo, hi], "pairs": len(chosen)}


def _interaction(pairs, samples, seed, stratum=None, models=None) -> dict[str, Any]:
    keyed: dict[tuple, dict[str, float]] = defaultdict(dict)
    for pair in _selected(pairs, stratum, models):
        key = (pair["model"], pair["repeat"], pair["family"], pair["workflow"], pair["representation"])
        keyed[key][pair["arm"]] = float(pair["delta"])
    diffs: dict[str, list[float]] = defaultdict(list)
    for key, arms in keyed.items():
        if LOW_ARM in arms and HIGH_ARM in arms:
            diffs[key[2]].append(arms[HIGH_ARM] - arms[LOW_ARM])
    point, lo, hi = _boot_family(dict(diffs), samples, seed)
    return {"estimate_pp": point, "ci95": [lo, hi], "paired_cells": sum(len(v) for v in diffs.values())}


def _stratum_block(pairs, samples, seed, stratum, models=None) -> dict[str, Any]:
    entry: dict[str, Any] = {arm: _arm_contrast(pairs, arm, samples, seed, stratum, models) for arm in ARMS}
    entry["interaction_pp"] = _interaction(pairs, samples, seed, stratum, models)
    entry["configurations"] = sorted({p["model"] for p in _selected(pairs, stratum, models)})
    return entry


def _verdict(block: Mapping[str, Any]) -> str:
    """Protocol section 7 table, read on the manipulation-gated stratum-B block."""
    if not block["configurations"]:
        return "inconclusive: no stratum-B configuration passed the manipulation check"
    lo, hi = block["interaction_pp"]["ci95"]
    h_lo, h_hi = block[HIGH_ARM]["ci95"]
    if lo is None:
        return "not estimable"
    if lo > DECISION_MARGIN:
        if h_lo <= 0.0 <= h_hi:
            return "budget artifact"
        return "budget inflates but does not create the loss"
    if hi < DECISION_MARGIN:
        return "no budget effect"
    return "inconclusive"


def _mean(values: Sequence[float]) -> float | None:
    return fmean(values) if values else None


def _quantiles(values: Sequence[int]) -> dict[str, float | None]:
    if not values:
        return {"n": 0, "median": None, "p95": None, "max": None}
    v = sorted(values)
    return {"n": len(v), "median": float(median(v)), "p95": float(v[int(0.95 * (len(v) - 1))]), "max": float(v[-1])}


def analyze(samples: int, seed: int, panel: Path | None = None, released_path: Path = RELEASED) -> dict[str, Any]:
    # --panel: keep only sweep configurations of that reporting panel; every row, job and gate
    # verdict of another model is dropped before any statistic (default: all sweep configurations).
    models = None if panel is None else {c["model"] for c in json.loads(panel.read_text())["configurations"]}
    keep = (lambda model: True) if models is None else (lambda model: model in models)
    ledger = _execution_accounting(models)
    quarantined = {(str(s), str(a)) for s, a in ledger.get("quarantined", [])}
    rows, counts = load_rows(quarantined, models)
    quarantined_models = {SLUGS[s] for s, _ in quarantined if s in SLUGS}
    rows_primary = [r for r in rows if not r["quarantined"]]
    pairs = _visible_pairs([r for r in rows_primary if r["model"] not in quarantined_models], endpoint_only=True)
    pairs_all_cells = _visible_pairs([r for r in rows_primary if r["model"] not in quarantined_models], endpoint_only=False)

    contrasts = {s: _stratum_block(pairs, samples, seed, s) for s in ("B", "M", "N", "all")}
    all_cells = {s: _stratum_block(pairs_all_cells, samples, seed, s) for s in ("B", "M", "N", "all")}

    # --- manipulation check: V=128 truncation in the high arm, pooled over repeats
    manipulation: dict[str, Any] = {}
    for model in sorted({r["model"] for r in rows}):
        entry: dict[str, Any] = {}
        for arm in ARMS:
            at128 = [r for r in rows if r["model"] == model and r["arm"] == arm and r["visible"] == 128]
            entry[arm] = {
                "rows": len(at128), "quarantined": (SLUG_OF[model], arm) in quarantined,
                "truncation_rate": _mean([float(r["truncated"]) for r in at128]),
                "truncation_by_repeat": {str(rep): _mean([float(r["truncated"]) for r in at128 if r["repeat"] == rep])
                                         for rep in sorted({r["repeat"] for r in at128})},
                "zero_call_share": _mean([float(r["calls"] == 0) for r in at128]),
                "verified_success": _mean([float(r["success"]) for r in at128]),
            }
        entry["stratum"] = STRATA[model]
        rate = entry[HIGH_ARM]["truncation_rate"]
        entry["manipulation_passed"] = rate is not None and rate < MANIPULATION_CEILING
        manipulation[model] = entry
    passed = {m for m, e in manipulation.items() if e["manipulation_passed"]
              and not any((SLUG_OF[m], arm) in quarantined for arm in ARMS)}

    primary: dict[str, Any] = {}
    for stratum in ("B", "M", "N"):
        in_stratum = {m for m in manipulation if STRATA[m] == stratum and m not in quarantined_models}
        eligible = in_stratum & passed if stratum == "B" else in_stratum
        primary[stratum] = (_stratum_block(pairs, samples, seed, stratum, eligible) if eligible else
                            {"configurations": [], "interaction_pp": {"estimate_pp": None, "ci95": [None, None], "paired_cells": 0},
                             **{arm: {"estimate_pp": None, "ci95": [None, None], "pairs": 0} for arm in ARMS}})
    primary["verdict"] = _verdict(primary["B"])
    # amendment 2: the pooled reading over the four originally registered stratum-B
    # configurations, kept for the record (not a second primary)
    registered = {m for m in manipulation if STRATA[m] == "B" and m in REGISTERED_MODELS} & passed
    primary["registered_subset"] = ({**_stratum_block(pairs, samples, seed, "B", registered),
                                     "campaign": REGISTERED_CAMPAIGN} if registered else
                                    {"configurations": [], "campaign": REGISTERED_CAMPAIGN})

    per_configuration: dict[str, Any] = {}
    released = json.loads(released_path.read_text())["configuration_cluster_bootstrap"]["visible_128_minus_8"]["by_configuration"]
    for model in sorted({r["model"] for r in rows}):
        block = {arm: _arm_contrast(pairs, arm, samples, seed, None, {model}) for arm in ARMS}
        block["interaction_pp"] = _interaction(pairs, samples, seed, None, {model})
        block["stratum"] = STRATA[model]
        block["campaign"] = CAMPAIGN_OF[model]
        block["quarantined_arms"] = [arm for arm in ARMS if (SLUG_OF[model], arm) in quarantined]
        block["manipulation_passed"] = manipulation[model]["manipulation_passed"]
        block["released_visible_effect_pp"] = (float(released[model]) * 100.0 if model in released else None)
        per_configuration[model] = block

    # --- what the truncated episodes look like (secondary 2 and 3, from rows) --
    truncated_shape: dict[str, Any] = {}
    for model in sorted({r["model"] for r in rows_primary}):
        for arm in ARMS:
            chosen = [r for r in rows_primary if r["model"] == model and r["arm"] == arm and r["truncated"]]
            truncated_shape.setdefault(model, {})[arm] = {
                "truncated_rows": len(chosen),
                "no_call_no_answer": sum(1 for r in chosen if r["call_attempts"] == 0 and r["answer_empty"]),
                "some_call": sum(1 for r in chosen if r["call_attempts"] > 0),
                "single_request": sum(1 for r in chosen if r["requests"] <= 1),
            }

    # --- completion tokens when not cut off (secondary 4, from rows) ----------
    completion_use: dict[str, Any] = {}
    for model in sorted({r["model"] for r in rows_primary}):
        for arm in ARMS:
            ok = [r["completion_tokens"] for r in rows_primary
                  if r["model"] == model and r["arm"] == arm and r["visible"] == 128 and not r["truncated"]]
            completion_use.setdefault(model, {})[arm] = _quantiles(ok)

    # --- run-to-run disagreement (secondary 5) --------------------------------
    repeat_index: dict[tuple, dict[int, bool]] = defaultdict(dict)
    for row in rows_primary:
        repeat_index[(row["model"], row["arm"], row["condition_id"])][row["repeat"]] = row["success"]
    both = {k: v for k, v in repeat_index.items() if len(v) == 2}
    run_to_run = {
        "conditions_with_two_runs": len(both),
        "disagreement_rate": _mean([float(v[1] != v[2]) for v in both.values()]),
        "by_arm": {arm: _mean([float(v[1] != v[2]) for k, v in both.items() if k[1] == arm]) for arm in ARMS},
        "by_configuration": {m: _mean([float(v[1] != v[2]) for k, v in both.items() if k[0] == m])
                             for m in sorted({k[0] for k in both})},
    }

    # --- cost and telemetry (secondary 6) --------------------------------------
    usd_by_model: dict[str, float] = defaultdict(float)
    usd_by_arm: dict[str, float] = defaultdict(float)
    for job in ledger["jobs"]:
        usd_by_model[str(job["model"])] += float(job["usd"])
        usd_by_arm[str(job["arm"])] += float(job["usd"])
    tokens = {arm: {"rows": len([r for r in rows_primary if r["arm"] == arm]),
                    "mean_prompt_tokens": _mean([r["prompt_tokens"] for r in rows_primary if r["arm"] == arm]),
                    "mean_completion_tokens": _mean([r["completion_tokens"] for r in rows_primary if r["arm"] == arm]),
                    "usd": round(usd_by_arm.get(arm, 0.0), 4)} for arm in ARMS}

    gate = {c: (json.loads((e / "pilot-gate.json").read_text()) if (e / "pilot-gate.json").exists() else None)
            for c, e in EXECUTIONS.items()}
    if models is not None:
        for record in gate.values():
            if record and isinstance(record.get("verdict"), dict):
                record["verdict"] = {s: v for s, v in record["verdict"].items() if keep(SLUGS.get(s))}
                record["all_passed"] = all(v.get("passed") for v in record["verdict"].values())

    report = {
        "schema": "bottleneckbench-completion-cap-sweep-v2",
        "campaign": CAMPAIGN, "campaign_directories": list(CAMPAIGNS), "protocol": PROTOCOL,
        "campaign_of_configuration": {m: c for m, c in CAMPAIGN_OF.items() if keep(m)},
        "arms": MANIFEST["arms"], "decision_margin_pp": DECISION_MARGIN,
        "manipulation_ceiling": MANIPULATION_CEILING,
        "strata": {m: s for m, s in STRATA.items() if keep(m)}, "strata_basis": STRATA_BASIS,
        "rows": counts, "configurations": sorted({r["model"] for r in rows}),
        "families": len({r["family"] for r in rows}), "repeats_observed": sorted({r["repeat"] for r in rows}),
        "pilot_gate": gate,
        "primary_reading": primary,
        "visible_menu_endpoint_cells": contrasts,
        "visible_menu_all_cells": all_cells,
        "per_configuration": per_configuration,
        "manipulation_check": manipulation,
        "truncated_episode_shape": truncated_shape,
        "completion_tokens_not_truncated_v128": completion_use,
        "quarantined_arms": [{"slug": s, "model": SLUGS.get(s, s), "arm": a} for s, a in sorted(quarantined)],
        "run_to_run": run_to_run,
        "cost": {"spent_usd": ledger["spent_usd"], "ceiling_usd": ledger["ceiling_usd"],
                 "spent_usd_by_campaign": ledger["spent_usd_by_campaign"],
                 "ceiling_usd_by_campaign": ledger["ceiling_usd_by_campaign"],
                 "by_model_usd": {k: round(v, 4) for k, v in sorted(usd_by_model.items())}, "by_arm": tokens,
                 "note": "retained-row usage priced at the frozen section 5 rates; an accounting, not an invoice"},
        "bootstrap": {"samples": samples, "seed": seed, "cluster": "semantic family"},
        "notes": ["the standard runner stores no raw receipts and no reasoning-token field; truncated-episode "
                  "shape is read from call attempts and the answer, and reasoning tokens are not available here",
                  "endpoint cells = {8,128} x {low,high} x {raw,linked}; checkpoint cells enter only visible_menu_all_cells"],
    }
    if models is not None:
        report["reporting_panel_filter"] = {
            "panel": _display(panel), "released_visible_effects": _display(released_path),
            "dropped_models": sorted(set(STRATA) - models),
            "rule": "sweep configurations outside the reporting panel are dropped (rows, ledger jobs, pilot-gate "
                    "verdicts) before any statistic; strata and the registered subset are recomputed on the rest"}
    return report


def _display(path: Path) -> str:
    path = path.resolve()
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path,
                        default=EXECUTIONS[REGISTERED_CAMPAIGN] / "analysis" / "cap-sweep-campaign.json")
    parser.add_argument("--bootstrap-samples", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=20261012)
    parser.add_argument("--panel", type=Path, default=PANEL,
                        help="release-panel.json; restrict the sweep to its configurations (default: the reporting panel)")
    parser.add_argument("--released-sensitivity", type=Path, default=RELEASED,
                        help="capacity-cluster-sensitivity.json giving the released per-configuration V effect")
    args = parser.parse_args()
    report = analyze(args.bootstrap_samples, args.seed, args.panel, args.released_sensitivity)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=1, sort_keys=True, allow_nan=False) + "\n")

    def fmt(block):
        if block.get("estimate_pp") is None:
            return "n/a"
        lo, hi = block["ci95"]
        return f"{block['estimate_pp']:+.1f} [{lo:+.1f}, {hi:+.1f}]"

    print(f"rows kept {report['rows'].get('kept', 0)} of {report['rows'].get('read', 0)}, "
          f"{report['families']} families, repeats {report['repeats_observed']}, spent ${report['cost']['spent_usd']}")
    for model, entry in report["manipulation_check"].items():
        print(f"  {model:30s} [{entry['stratum']}] trunc {LOW_ARM}={entry[LOW_ARM]['truncation_rate']} "
              f"{HIGH_ARM}={entry[HIGH_ARM]['truncation_rate']} -> {'pass' if entry['manipulation_passed'] else 'FAIL'}")
    for stratum in ("B", "M", "N", "all"):
        b = report["visible_menu_endpoint_cells"][stratum]
        print(f"  stratum {stratum}: {LOW_ARM} {fmt(b[LOW_ARM])}  {HIGH_ARM} {fmt(b[HIGH_ARM])}  "
              f"interaction {fmt(b['interaction_pp'])}  cfg={len(b['configurations'])}")
    print("primary verdict:", report["primary_reading"]["verdict"])
    print("wrote", args.output.relative_to(ROOT) if args.output.is_relative_to(ROOT) else args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
