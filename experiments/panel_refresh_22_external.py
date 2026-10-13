"""Pure row-level external analyses for the selected October panel.

Inputs must already be selected and independently verified against their frozen
plans. This module neither reads evidence nor grants source/panel admission. It
does not rewrite plans, fill missing freeze metadata, rescore responses, execute
tools, or write outputs. The registered structural sub-analysis stays separate.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import math
from pathlib import Path
from statistics import fmean
import sys
from typing import Mapping, Sequence

HERE = Path(__file__).resolve().parent
for directory in (HERE.parent, HERE):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from experiments import analyze_structural_repair_holdout as structural
from experiments import analyze_candidate_toolsandbox_release as concurrent
from bottleneckbench.core.schema import normalize_floats, stable_hash

QWEN = "qwen/qwen3.8-max-0902"
CONTINUATION_ID = "8fc2eeda01db08db69e08ff7f19b486ef8658327414f7d9dea50279d5bd652a9"
CONTINUATION_SCENARIO = "find_days_till_holiday"
ARMS = ("scoped", "ten_distractors", "all_tools")


def _require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def _number(value: object, message: str, *, integer: bool = False) -> float:
    _require(type(value) in ((int,) if integer else (int, float))
             and math.isfinite(value) and value >= 0, message)
    return float(value)


def _provenance(models: set[str], records: Sequence[Mapping]) -> dict[str, str]:
    _require(bool(models) and all(isinstance(m, str) and m for m in models), "invalid model inventory")
    _require(len(models) >= 2, "pooled analyses require at least two configurations")
    hashes = {}
    for record in records:
        # Accept existing multi-configuration provenance or the new literal
        # single-model metadata. Never manufacture either record's other keys.
        declared = record.get("configurations")
        if declared is None:
            declared = [record.get("model")]
        _require(isinstance(declared, (list, tuple)) and bool(declared)
                 and all(isinstance(m, str) and m in models for m in declared)
                 and len(set(declared)) == len(declared), "invalid provenance model scope")
        _require("model" not in record or declared == [record["model"]], "conflicting provenance model scopes")
        digest = record.get("plan_hash")
        _require(isinstance(digest, str) and len(digest) == 64
                 and all(c in "0123456789abcdef" for c in digest), "invalid provenance plan hash")
        for model in declared:
            _require(model not in hashes, "duplicate provenance model")
            hashes[model] = digest
    _require(set(hashes) == models, "provenance model coverage differs")
    return hashes


def _identities(rows: Sequence[Mapping], models: set[str], hashes: Mapping[str, str],
                count: int, *, nested: bool) -> None:
    observed, identities = Counter(), set()
    for row in rows:
        payload = row.get("condition") if nested else row
        _require(isinstance(payload, Mapping), "missing condition fields")
        model, identity = payload.get("model"), row.get("condition_id")
        _require(model in models and isinstance(identity, str) and bool(identity)
                 and identity not in identities, "foreign or duplicate condition")
        _require(row.get("plan_hash") == hashes[model], "row plan provenance differs")
        _require("model" not in row or row["model"] == model, "conflicting row model")
        if nested:
            _require(identity == stable_hash(payload), "structural condition identity differs")
        identities.add(identity)
        observed[model] += 1
    _require(observed == Counter({m: count for m in models}), "partial or extra model block")


def analyze_structural(rows: Sequence[Mapping], models: set[str], *,
                       plan_provenance: Sequence[Mapping],
                       bootstrap_samples: int = 20_000,
                       randomization_samples: int = 100_000,
                       seed: int = 20260814) -> dict:
    """Pool validated complete rows using the unchanged held-out formulas."""
    models = set(models)
    hashes = _provenance(models, plan_provenance)
    _identities(rows, models, hashes, 216, nested=True)
    for value in (bootstrap_samples, randomization_samples):
        _require(type(value) is int and value > 0, "invalid resampling count")
    _require(type(seed) is int, "invalid resampling seed")
    inventories = defaultdict(dict)
    for row in rows:
        _require(not structural.is_infrastructure_failure(row), "infrastructure row is not a scored outcome")
        _require(isinstance(row.get("verdict"), Mapping)
                 and type(row["verdict"].get("verified_success")) is bool, "invalid structural success verdict")
        _require(isinstance(row.get("usage"), Mapping) and isinstance(row.get("efficiency"), Mapping)
                 and isinstance(row.get("trace"), list), "missing structural resource fields")
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            _number(row["usage"].get(key), "invalid structural token count", integer=True)
        for key in ("tool_schema_bytes", "tool_schema_exposures", "model_requests_attempted"):
            _number(row["efficiency"].get(key), "invalid structural resource count", integer=True)
        _number(row["efficiency"].get("wall_time_seconds"), "invalid structural wall time")
        c = row["condition"]
        _require(type(c["certified_calls"]) is int, "noninteger structural cell")
        family, model = c["family_id"], c["model"]
        _require(all(isinstance(c[k], str) and c[k] for k in ("family_id", "intent", "stratum")),
                 "invalid structural family metadata")
        metadata = (c["intent"], c["stratum"])
        _require(family not in inventories[model] or inventories[model][family] == metadata,
                 "structural family metadata differs")
        inventories[model][family] = metadata
    reference = inventories[sorted(models)[0]]
    _require(len(reference) == 24 and all(inventories[m] == reference for m in models),
             "structural family inventories differ")
    extracted = structural._extract(rows)
    layers = {layer: structural._summarize_layer(values, layer=layer,
        bootstrap_samples=bootstrap_samples, randomization_samples=randomization_samples, seed=seed)
        for layer, values in extracted.items()}
    adjusted = structural.holm_adjust({layer: float(value["activation_test"]["raw_p"])
                                       for layer, value in layers.items()})
    for layer, probability in adjusted.items():
        test = layers[layer]["activation_test"]
        test["holm_p"] = probability
        test["activated_at_0.05"] = test["all_point_directions_positive"] is True and probability < .05
    return {"schema": "panel-refresh-22-structural-row-analysis-v1", "release_admitted": False,
        "input_scope": "caller-verified frozen plan rows; no source admission performed here",
        "status": "complete", "confirmatory_weight": 0, "models": sorted(models),
        "rows": len(rows), "analysis_rows": len(rows), "provider_failures": 0,
        "plan_hashes": sorted(set(hashes.values())), "plan_provenance": deepcopy(list(plan_provenance)),
        "layers": layers, "resource_profile_by_cell": structural._resource_profile(rows),
        "bootstrap_samples": bootstrap_samples, "randomization_samples": randomization_samples,
        "complete_case_policy": "each included model is complete against its own frozen plan",
        "registered_analysis": "separate, not replaced by this pooled descriptive view"}


def _tool_matrix(rows, models):
    matrix, strata = {}, {}
    for row in rows:
        _require(not concurrent._infrastructure_failure(row), "infrastructure row is not a scored outcome")
        model, scenario, arm = row["model"], row.get("base_scenario_id"), row.get("menu_arm")
        _require(isinstance(scenario, str) and bool(scenario) and arm in ARMS,
                 "invalid scenario or menu arm")
        key = (model, scenario, arm)
        _require(key not in matrix, "duplicate matched menu cell")
        stratum = row.get("stratum")
        _require(isinstance(stratum, str) and bool(stratum), "missing scenario stratum")
        _require(scenario not in strata or strata[scenario] == stratum, "scenario stratum differs")
        strata[scenario], matrix[key] = stratum, row
        _require(isinstance(row.get("score"), Mapping) and isinstance(row.get("usage"), Mapping),
                 "missing score or usage")
        for metric in ("similarity", "milestone_similarity", "minefield_similarity"):
            _require(_number(row["score"].get(metric), "invalid score") <= 1, "score exceeds one")
        for metric in ("total_tokens", "requests"):
            _number(row["usage"].get(metric), "invalid resource count", integer=True)
        _number(row.get("duration_seconds"), "invalid recorded duration")
    scenarios = sorted(strata)
    _require(len(scenarios) == 32 and set(matrix) ==
             {(model, scenario, arm) for model in models for scenario in scenarios for arm in ARMS},
             "incomplete balanced menu matrix")
    return matrix, strata, scenarios


def _duration_scopes(rows, records):
    by_id = {r["condition_id"]: r for r in rows}
    seen, excluded = set(), set()
    for record in records:
        identity = record.get("condition_id")
        _require(identity in by_id and identity not in seen, "foreign or duplicate duration provenance")
        seen.add(identity)
        row = by_id[identity]
        _require("model" not in record or record["model"] == row["model"], "duration provenance model differs")
        scope = record.get("duration_scope")
        _require(scope in {"unchanged", "continuation_segment_only"}, "unknown duration scope")
        if scope == "continuation_segment_only":
            _require(identity == CONTINUATION_ID and row["model"] == QWEN
                     and row["base_scenario_id"] == CONTINUATION_SCENARIO and row["menu_arm"] == "all_tools",
                     "unsupported continuation duration exception")
            excluded.add(identity)
    if any(r["model"] == QWEN for r in rows):
        _require(excluded == {CONTINUATION_ID}, "Qwen continuation duration qualification required")
    return excluded


def analyze_toolsandbox(rows: Sequence[Mapping], models: set[str], *,
                        plan_provenance: Sequence[Mapping],
                        row_provenance: Sequence[Mapping] = ()) -> dict:
    """Keep every scored outcome; do not estimate an incomplete duration contrast.

``row_provenance`` accepts the row adapter's selected-row provenance records.
Only the exact known Qwen saved-prefix continuation can have a nonstandard
duration scope. Missing qualification fails closed, without dropping its score.
"""
    models = set(models)
    hashes = _provenance(models, plan_provenance)
    _identities(rows, models, hashes, 96, nested=False)
    matrix, strata, scenarios = _tool_matrix(rows, models)
    excluded = _duration_scopes(rows, row_provenance)
    blocks = [(m, s) for m in sorted(models) for s in scenarios]
    contrasts = {}
    for arm in ARMS[1:]:
        report = {}
        for metric in concurrent.METRICS:
            invalid = sorted({matrix[(m, s, a)]["condition_id"]
                for m, s in blocks for a in (arm, "scoped")
                if metric == "duration_seconds" and matrix[(m, s, a)]["condition_id"] in excluded})
            if invalid:
                report[metric] = {"status": "not_estimated", "estimate": None, "ci95": None,
                    "planned_pairs": len(blocks), "eligible_pairs": len(blocks) - len(invalid),
                    "ineligible_pairs": len(invalid), "excluded_condition_ids": invalid,
                    "planned_scenario_clusters": len(scenarios), "planned_configurations": len(models),
                    "reason": "continuation-only segment is not a complete episode duration",
                    "estimand_policy": "full balanced-panel contrast unavailable; no available-case substitute"}
                continue
            values, per_model, per_stratum = defaultdict(list), defaultdict(list), defaultdict(list)
            for model, scenario in blocks:
                difference = concurrent.menu._value(matrix[(model, scenario, arm)], metric) - concurrent.menu._value(
                    matrix[(model, scenario, "scoped")], metric)
                values[scenario].append(difference)
                per_model[model].append(difference)
                per_stratum[strata[scenario]].append(difference)
            summary = concurrent.menu._cluster_summary(values,
                seed=concurrent.menu.ANALYSIS_SEED + int(stable_hash(f"{arm}:{metric}")[:8], 16))
            summary["by_model"] = {m: fmean(v) for m, v in sorted(per_model.items())}
            summary["by_stratum"] = {s: fmean(v) for s, v in sorted(per_stratum.items())}
            summary["leave_one_model_out"] = {
                omitted: concurrent._mean([v for m, observed in per_model.items() if m != omitted for v in observed])
                for omitted in sorted(models)}
            summary.update(status="estimated", planned_pairs=len(blocks), eligible_pairs=len(blocks),
                           ineligible_pairs=0, scenario_clusters=len(scenarios), configurations=len(models))
            report[metric] = summary
        contrasts[arm] = report
    for arm, probability in concurrent.menu._holm({arm: float(contrasts[arm]["similarity"]["sign_flip_p"])
                                                  for arm in contrasts}).items():
        contrasts[arm]["similarity"]["holm_p"] = probability
    differences = [concurrent.menu._value(matrix[(m, s, "all_tools")], "similarity") -
                   concurrent.menu._value(matrix[(m, s, "ten_distractors")], "similarity") for m, s in blocks]
    result = normalize_floats({"schema": "panel-refresh-22-toolsandbox-row-analysis-v1", "release_admitted": False,
        "input_scope": "caller-verified frozen plan rows; no source admission performed here",
        "status": "complete", "confirmatory_weight": 0, "rows": len(rows), "matched_blocks": len(blocks),
        "models": sorted(models), "scenarios": len(scenarios), "provider_failures": 0,
        "plan_hashes": sorted(set(hashes.values())),
        "complete_case_policy": "all complete scored configuration blocks retained; duration eligibility is metric-specific",
        "contrasts": contrasts,
        "all_tools_minus_ten_distractors_similarity": {"estimate": fmean(differences),
            "degraded": sum(v < 0 for v in differences), "tied": sum(v == 0 for v in differences),
            "improved": sum(v > 0 for v in differences)},
        "inference": f"scenario-cluster bootstrap and sign flip over {len(models)} transport-complete frozen configurations"})
    result["plan_provenance"] = deepcopy(list(plan_provenance))
    result["duration_qualifications"] = deepcopy(list(row_provenance))
    return result
