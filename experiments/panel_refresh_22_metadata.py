"""Selected-only candidate panel metadata; no evidence or publication admission.

The public entry derives metadata from literal retained-panel anchors and the
prepared eight-configuration source map. It never reads an unfinished new row
journal, writes a file, or copies archival full-panel policy/history objects.
All expected counts are planned denominators until the separate admission gate
has verified the complete selected evidence. Provider routing belongs in the
reproducibility section, never in a new configuration's ordinary display name.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from experiments import build_panel_refresh_22 as selection

ROOT = Path(__file__).resolve().parents[1]
CONFIG_FIELDS = frozenset({"model", "display_name", "organization", "provider", "access", "coverage"})
COVERAGE_TOKENS = frozenset({"core", "auxiliary", "external_rank", "menu_matched",
                             "structural_registered", "structural_additional"})
NEW_COVERAGE = ["core", "auxiliary", "structural_additional", "menu_matched"]
POLICY_SCALARS = (
    "candidate_extension_confirmatory_weight", "frontier_extension_confirmatory_weight",
    "infrastructure_estimand", "candidate_menu_estimand", "targeted_vw_estimand",
)
FREEZE_FIELDS = (
    "frozen_at", "structural_outcomes_access_before_freeze", "outcome_access_before_freeze",
    "candidate_core_outcomes_inspected_before_freeze", "confirmatory_weight",
)
EPISODE_GROUPS = {
    "core": ("capacity", "discovery", "candidate_capacity", "candidate_discovery", "frontier_capacity", "frontier_discovery"),
    "auxiliary_repair_sentinel_frontier": ("repair", "sentinel", "workflow_frontier", "candidate_repair",
        "candidate_sentinel", "candidate_workflow_frontier", "frontier_repair", "frontier_sentinel", "frontier_workflow_frontier"),
    "external_rank": ("external",),
    "menu_matched": ("historical_menu_intervention", "candidate_menu_intervention", "frontier_menu_intervention"),
    "structural_registered": ("structural_holdout",),
    "structural_additional": ("candidate_structural_additional", "frontier_structural_additional"),
}
CONCURRENT_GROUPS = ("candidate_menu_intervention", "frontier_menu_intervention")
REQUIRED_NEW_NAMES = {
    "anthropic/claude-opus-5.5": "Claude Opus 5.5",
    "anthropic/claude-sonnet-5.5": "Claude Sonnet 5.5",
    selection.HAIKU: "Claude Haiku 5.5",
    selection.QWEN: "Qwen3.8 Max (0902)",
    selection.GEMINI: "Gemini 3.8 Flash",
    "openai/gpt-6-luna": "GPT-6 Luna", "openai/gpt-6.1-sol": "GPT-6.1 Sol",
    selection.DEEPSEEK: "DeepSeek V4.1 Flash",
}


def require(value, message):
    if not value:
        raise ValueError(message)


def same(left, right, message):
    require(selection.stable_hash(left) == selection.stable_hash(right), message)


def exact_models(values, expected, label):
    require(isinstance(values, list) and all(isinstance(v, str) for v in values)
            and len(values) == len(set(values)) and set(values) == set(expected), label)


def _source_index(source_map, retained_models):
    require(source_map.get("schema") == "panel-refresh-22-pre-admission-source-selection-v1"
            and source_map.get("status") == "source_selection_validated_only"
            and source_map.get("release_admitted") is False, "not a prepared candidate source map")
    selection.verify_self_hash(source_map, "artifact_hash")
    exact_models(source_map["new_models"], selection.NEW_MODELS, "new source models differ")
    exact_models(source_map["retained_models"], retained_models, "retained source models differ")
    same(source_map["expected_coverage_counts"], selection.FINAL_COVERAGE, "planned coverage differs")
    same(source_map["expected_episode_counts"], selection.FINAL_EPISODES, "planned episodes differ")
    same(source_map["expected_total_rows"], 27912, "planned total differs")
    same(source_map["planned_new_conditions"], 10688, "planned increment differs")
    configurations = source_map["configurations"]
    require(isinstance(configurations, list) and len(configurations) == 8, "not eight prepared configurations")
    exact_models([c["model"] for c in configurations], selection.NEW_MODELS, "duplicate/foreign prepared configuration")
    configs = {c["model"]: c for c in configurations}
    sources = source_map["sources"]
    require(isinstance(sources, list) and len(sources) == 72, "not 72 prepared source plans")
    indexed = {(s["model"], s["block"]): s for s in sources}
    require(len(indexed) == 72 and set(indexed) == {(m, b) for m in selection.NEW_MODELS for b in selection.BLOCKS},
            "missing/extra/duplicate planned model-block coverage")
    for (model, block), source in indexed.items():
        same(source["planned_conditions"], selection.BLOCKS[block], "planned block count differs")
        require(source["ledger_group"] == selection.GROUPS[block]
                and source["configuration_key"] == configs[model]["configuration_key"], "planned block membership differs")
    return configs, indexed


def _plan_provenance(plan, manifest, source, config):
    """Keep missing/nullable facts literal; a preparation time is not a freeze."""
    if source["block"] in {"structural", "toolsandbox"}:
        same(plan.get("confirmatory_weight"), 0, "new external plan is not declared descriptive")
    return {
        "model": source["model"], "block": source["block"],
        "plan": deepcopy(source["plan"]), "plan_hash": source["plan_hash"],
        "manifest": deepcopy(config["manifest"]), "planned_conditions": source["planned_conditions"],
        "plan_frozen_at": plan.get("frozen_at"),
        "manifest_prepared_at_utc": manifest.get("prepared_at_utc"),
        "outcome_access_before_freeze": plan.get("structural_outcomes_access_before_freeze", plan.get("outcome_access_before_freeze")),
        "core_outcome_selection": plan.get("candidate_core_outcomes_inspected_before_freeze"),
        "declared_confirmatory_weight": plan.get("confirmatory_weight"),
        "literal_plan_fields": {k: deepcopy(plan[k]) for k in FREEZE_FIELDS if k in plan},
        "analysis_confirmatory_weight": 0,
        "frozen_plan_rewritten": False,
    }


def _derive(retained_panel, retained_manifest, source_map, configurations, plan_metadata, inputs):
    """Pure projection; production inputs are independently anchored by the caller."""
    retained = retained_panel["configurations"]
    require(isinstance(retained, list) and len(retained) == 14, "not fourteen retained configurations")
    retained_models = {c["model"] for c in retained}
    require(len(retained_models) == 14 and not retained_models & selection.NEW_MODELS,
            "duplicate/foreign retained configuration")
    prepared_configs, sources = _source_index(source_map, retained_models)
    exact_models(retained_manifest["models"], retained_models, "retained manifest models differ")
    require(set(configurations) == selection.NEW_MODELS, "missing/extra normalized configuration metadata")
    expected_keys = {(m, b) for m in selection.NEW_MODELS for b in selection.BLOCKS}
    require(len(plan_metadata) == 72 and {(p["model"], p["block"]) for p in plan_metadata} == expected_keys,
            "missing/extra/duplicate plan provenance")
    public_configs = []
    for row in retained:
        require(set(row) == CONFIG_FIELDS, "unreviewed retained configuration fields")
        coverage = row["coverage"]
        require(isinstance(coverage, list) and len(coverage) == len(set(coverage))
                and set(coverage) <= COVERAGE_TOKENS and "core" in coverage, "invalid retained coverage tokens")
        public_configs.append(deepcopy(row))
    reproduction_configs = []
    for model in sorted(selection.NEW_MODELS):
        config, prepared = configurations[model], prepared_configs[model]
        require(config["model"] == model and config["display_name"] == REQUIRED_NEW_NAMES[model]
                and prepared["display_name"] == config["display_name"], "new ordinary display name differs")
        require(selection.stable_hash(config) == prepared["configuration_sha256"], "new configuration binding differs")
        same(config["provider"], prepared["route"], "prepared provider policy differs")
        require(config["provider"]["allow_fallbacks"] is False, "unfixed provider configuration")
        if model == selection.DEEPSEEK:
            require(config.get("configuration_id") == selection.DEEPINFRA_CONFIGURATION
                    and config["provider"]["only"] == ["deepinfra/fp8"]
                    and config["provider"]["quantizations"] == ["fp8"], "wrong selected DeepSeek configuration")
        public_configs.append({"model": model, "display_name": config["display_name"],
            "organization": config["organization"], "provider": "OpenRouter", "access": "API",
            "coverage": list(NEW_COVERAGE)})
        reproduction_configs.append({"model": model, "configuration_key": prepared["configuration_key"],
            "configuration_sha256": prepared["configuration_sha256"],
            "configuration_id": config.get("configuration_id"),
            "manifest": deepcopy(prepared["manifest"]), "pricing_snapshot": deepcopy(prepared["pricing"]),
            "access_route_label": config["access"], "provider_policy": deepcopy(config["provider"]),
            "selected_route": {k: deepcopy(config["selected_route"].get(k)) for k in
                ("model_id", "tag", "provider_name", "quantization")},
            "selected_route_sha256": selection.stable_hash(config["selected_route"]),
            "canonical_slug": config["canonical_slug"], "context_length": config["context_length"],
            "temperature": config.get("temperature"), "reasoning": deepcopy(config.get("reasoning")),
            "reasoning_override": deepcopy(config.get("reasoning_override"))})
    all_models = retained_models | selection.NEW_MODELS
    memberships = {token: {c["model"] for c in public_configs if token in c["coverage"]} for token in COVERAGE_TOKENS}
    for field, token in (("auxiliary_models", "auxiliary"), ("structural_registered_models", "structural_registered"),
                         ("structural_additional_models", "structural_additional")):
        exact_models(retained_panel[field], memberships[token] & retained_models, "retained eligibility differs: " + field)
    activation = retained_panel["activation_models"]
    require(len(activation) == 2 and set(activation) <= retained_models, "retained activation scope differs")
    exact_models(activation, set(activation), "duplicate retained activation model")
    groups, expected_rows = {}, {}
    for group, entries in retained_manifest["inputs"].items():
        group_models = [e["model"] for e in entries]
        require(all(m in retained_models for m in group_models), "foreign retained ledger model")
        groups[group] = set(group_models)
        expected_rows[group] = sum(e["rows"] for e in entries)
    same(expected_rows, retained_manifest["expected_rows"], "retained ledger row counts differ")
    same(sum(expected_rows.values()), 17224, "retained ledger total differs")
    for source in sources.values():
        group = source["ledger_group"]
        require(group in groups, "unregistered ledger group")
        groups[group].add(source["model"])
        expected_rows[group] += source["planned_conditions"]
    same(expected_rows, source_map["expected_group_rows"], "planned ledger row counts differ")
    def union(names):
        return set().union(*(groups[name] for name in names))
    require(union(("capacity", "candidate_capacity", "frontier_capacity")) == memberships["core"] == all_models
            and union(("discovery", "candidate_discovery", "frontier_discovery")) == all_models, "core coverage differs from ledger")
    for suffix in ("repair", "sentinel", "workflow_frontier"):
        require(union((suffix, "candidate_" + suffix, "frontier_" + suffix)) == memberships["auxiliary"],
                "auxiliary coverage differs from ledger")
    for token in ("external_rank", "menu_matched", "structural_registered", "structural_additional"):
        require(union(EPISODE_GROUPS[token]) == memberships[token], "block coverage differs from ledger: " + token)
    require(not memberships["structural_registered"] & memberships["structural_additional"], "structural strata overlap")
    concurrent = union(CONCURRENT_GROUPS)
    require(concurrent <= memberships["menu_matched"], "concurrent menu not in menu panel")
    coverage = {"core_descriptive": len(all_models), "auxiliary_descriptive": len(memberships["auxiliary"]),
        "structural_additional_descriptive": len(memberships["structural_additional"]),
        "concurrent_menu_descriptive": len(concurrent), "layer_activation_initial": len(activation),
        "structural_registered": len(memberships["structural_registered"])}
    same(coverage, selection.FINAL_COVERAGE, "final coverage denominator mismatch")
    episodes = {k: sum(expected_rows[g] for g in names) for k, names in EPISODE_GROUPS.items()}
    episodes["analysis_manifest"] = sum(expected_rows.values())
    same(episodes, selection.FINAL_EPISODES, "final episode denominator mismatch")
    require(sum(episodes[k] for k in EPISODE_GROUPS) == episodes["analysis_manifest"], "episode groups overlap or omit rows")
    public_policy = {k: deepcopy(retained_manifest["analysis_policy"][k]) for k in POLICY_SCALARS}
    public_policy["layer_activation_models"] = sorted(activation)
    same(public_policy["candidate_extension_confirmatory_weight"], 0, "candidate scientific weight differs")
    same(public_policy["frontier_extension_confirmatory_weight"], 0, "extension scientific weight differs")
    public_policy["panel_refresh_confirmatory_weight"] = 0
    public_policy["panel_refresh_admission"] = "all_eight_complete_independently_verified_inventories_required"
    panel = {"schema": "bottleneckbench-release-analysis-panel-v1", "status": "candidate_metadata_only",
        "release_admitted": False, "configurations": public_configs, "activation_models": sorted(activation),
        "auxiliary_models": sorted(memberships["auxiliary"]),
        "structural_registered_models": sorted(memberships["structural_registered"]),
        "structural_additional_models": sorted(memberships["structural_additional"]),
        "external_rank_models": sorted(memberships["external_rank"]), "concurrent_menu_models": sorted(concurrent),
        "coverage_counts": coverage, "episode_counts": episodes, "confirmatory_weight": 0,
        "reporting_policy": "unified model reporting; the 22-configuration core panel is the primary analysis panel and acquisition order appears only in provenance fields",
        "scientific_role": "candidate selected-panel declaration of planned denominators and evidence boundaries; not a result or publication-admission claim",
        "reporting_scope": {"selection": "user-specified model inventory", "source_rows_modified": False,
            "status": "candidate metadata; complete evidence admission remains a separate mandatory gate",
            "denominator_status": "expected_selected_panel_not_observed_completion"}}
    panel["artifact_hash"] = selection.stable_hash(panel)
    result = {"schema": "panel-refresh-22-selected-metadata-candidate-v1", "status": "candidate_metadata_only",
        "release_admitted": False, "new_api_calls": 0, "panel": panel, "analysis_policy": public_policy,
        "planned_ledger_group_models": {g: sorted(ms) for g, ms in sorted(groups.items())},
        "planned_new_block_models": {b: sorted(selection.NEW_MODELS) for b in selection.BLOCKS},
        "expected_group_rows": expected_rows, "source_selection_sha256": source_map["artifact_hash"],
        "reproducibility": {"new_configurations": reproduction_configs,
            "new_plan_metadata": sorted(deepcopy(plan_metadata), key=lambda p: (p["model"], p["block"])),
            "historical_coverage_policy": "retained_configuration_coverage_and_registered_strata_unchanged",
            "unknown_provenance_policy": "missing_plan_freeze_or_outcome_selection_fields_remain_null_not_inferred_from_manifest_preparation"},
        "inputs": deepcopy(inputs), "scope": "metadata projection only; no selected evidence materialization, scientific completion, analysis, or public export admission"}
    result["artifact_hash"] = selection.stable_hash(result)
    return result


def build_candidate_metadata(root: Path = ROOT) -> dict:
    """Return fresh candidate metadata; no caller-supplied success flag or writes."""
    root = Path(root).resolve()
    source_map = selection.prepare_source_map(root)
    retained_panel = selection.anchored(root, selection.RETAINED + "/release-panel.json")
    retained_manifest = selection.anchored(root, selection.RETAINED + "/release-analysis-inputs.json")
    inputs = {b["path"]: b["sha256"] for b in (source_map["retained_panel"], source_map["retained_manifest"])}
    configurations, metadata = {}, []
    for prepared in source_map["configurations"]:
        model = prepared["model"]
        for field in ("manifest", "pricing"):
            binding = prepared[field]
            require(binding["sha256"] == selection.ANCHORS[binding["path"]], "unanchored configuration source")
            inputs[binding["path"]] = binding["sha256"]
        manifest = selection.bound_json(root, prepared["manifest"])
        prices = selection.bound_json(root, prepared["pricing"])
        candidates = [prices["configuration"]] if model == selection.DEEPSEEK else prices["configurations"]
        selected = [c for c in candidates if c["model"] == model]
        require(len(selected) == 1, "ambiguous new configuration")
        configurations[model] = selected[0]
        for source in source_map["sources"]:
            if source["model"] != model:
                continue
            plan = selection.bound_json(root, source["plan"])
            inputs[source["plan"]["path"]] = source["plan"]["sha256"]
            metadata.append(_plan_provenance(plan, manifest, source, prepared))
    result = _derive(retained_panel, retained_manifest, source_map, configurations, metadata,
        [{"path": p, "sha256": h} for p, h in sorted(inputs.items())])
    selection.verify_bindings(root, result["inputs"])
    return result
