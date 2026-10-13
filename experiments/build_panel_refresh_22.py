#!/usr/bin/env python3
"""Offline, fail-closed preparation for the October 22-configuration panel.

``check`` constructs and validates a *pre-admission* source selection in memory.
It does not read unfinished new journals or create an analysis directory.
``build`` deliberately refuses admission until independent certificate adapters,
route-aware prices, and explicit selected-row analysis adapters are implemented.
No passed/status flag, caller-provided certificate, or model ID can bypass this
gate. This module imports no acquisition runner and has no network capabilities.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "results/local-only-panel-refresh-2026-10-07"
HAIKU_BASE = "results/local-only-panel-refresh-haiku-2026-10-08"
DEEPINFRA_BASE = "results/local-only-panel-refresh-deepinfra-2026-10-08"
RETAINED = "results/reporting-panel-2026-10-07"
HAIKU = "anthropic/claude-haiku-5.5"
DEEPSEEK = "deepseek/deepseek-v4.1-flash"
QWEN = "qwen/qwen3.8-max-0902"
GEMINI = "google/gemini-3.8-flash"
NEW_MODELS = frozenset({HAIKU, DEEPSEEK, QWEN, GEMINI,
    "anthropic/claude-opus-5.5", "anthropic/claude-sonnet-5.5",
    "openai/gpt-6-luna", "openai/gpt-6.1-sol"})
ORIGINAL_SIX = NEW_MODELS - {HAIKU, DEEPSEEK}
DEEPINFRA_CONFIGURATION = "deepseek-v4.1-flash__openrouter-deepinfra-fp8-high__2026-10-08"
BLOCKS = {"primary_capacity": 432, "robustness_capacity": 144,
    "primary_discovery": 216, "verification_repair": 72, "interface_guide": 72,
    "sentinel": 72, "workflow_frontier": 16, "structural": 216, "toolsandbox": 96}
GROUPS = {"primary_capacity": "frontier_capacity", "robustness_capacity": "frontier_capacity",
    "primary_discovery": "frontier_discovery", "verification_repair": "frontier_repair",
    "interface_guide": "frontier_repair", "sentinel": "frontier_sentinel",
    "workflow_frontier": "frontier_workflow_frontier", "structural": "frontier_structural_additional",
    "toolsandbox": "frontier_menu_intervention"}
FINAL_FRONTIER_ROWS = {"frontier_capacity": 6336, "frontier_discovery": 2376,
    "frontier_repair": 1584, "frontier_sentinel": 792, "frontier_workflow_frontier": 176,
    "frontier_structural_additional": 2376, "frontier_menu_intervention": 1056}
FINAL_COVERAGE = {"core_descriptive": 22, "auxiliary_descriptive": 21,
    "structural_additional_descriptive": 16, "concurrent_menu_descriptive": 13,
    "layer_activation_initial": 2, "structural_registered": 2}
FINAL_EPISODES = {"analysis_manifest": 27912, "core": 17424,
    "auxiliary_repair_sentinel_frontier": 4872, "external_rank": 160,
    "menu_matched": 1568, "structural_additional": 3456, "structural_registered": 432}
MANIFEST_PATHS = {"original": BASE + "/plans/manifest.json",
    "haiku": HAIKU_BASE + "/plans/manifest.json", "deepinfra": DEEPINFRA_BASE + "/plans/manifest.json"}
PRICE_PATHS = {"original": "configs/panel-refresh-2026-10-07-pricing.json",
    "haiku": HAIKU_BASE + "/pricing.json", "deepinfra": DEEPINFRA_BASE + "/pricing.json"}
V5_REPORTS = {
    QWEN: BASE + "/execution/offline-v5-nine-block-replay-v2/qwen-2026-10-08-b/report.json",
    GEMINI: BASE + "/execution/offline-v5-nine-block-replay-v2/gemini-2026-10-08-a/report.json"}
# Literal source commitments, not hashes supplied by a candidate selection file.
ANCHORS = {
    RETAINED + "/release-panel.json": "1717b623e34e70b29cd1db37882e9a021c3ee9e7bd2790a39c60575208594f72",
    RETAINED + "/release-analysis-inputs.json": "6b1db73b9632e0cc40c4614f8d4acd5fa3712cd60d930ff67f3a42a0b30ab62d",
    MANIFEST_PATHS["original"]: "de04c77c8b26f7955da017f12ca4127dc3ecdb01e54626c22701fad10d4b9c27",
    MANIFEST_PATHS["haiku"]: "334aff62e7809bd208c03271133689c924f5707897cf457d9c3075b93d71b871",
    MANIFEST_PATHS["deepinfra"]: "7a3548dae1e396b724d8d046fcf7b37e66fd4c4c8634bd1703e3f1b4d940f41b",
    PRICE_PATHS["original"]: "14a30baddbe65acea95f8053a81c368627c1b42f0dbcb60a5dbcccb677274f79",
    PRICE_PATHS["haiku"]: "11f0b8caa062b003e7693b789c3a17193a0fd521f36607213684b57c39e5ae21",
    PRICE_PATHS["deepinfra"]: "1443e3ddcedc19c746e12112e542d2df5082186da3c040a6b00731f71c9968a1",
    V5_REPORTS[QWEN]: "71b5fb6fa8555eacefa211e251991a4b375aecbbb9a03ac134c0d7163da7ab1a",
    V5_REPORTS[GEMINI]: "08c003932aefa5489856cc19c9e1ae7bc5c3e69b3c6a965a3f60439f506afe8a"}
REMAINING_GATES = (
    "All eight complete independent model certificates and transitive evidence inventories must be revalidated by schema-specific adapters; no generic passed flag is sufficient.",
    "Haiku admission must resolve any generic OSError-derived supplement independently; DeepInfra must bind the isolated FP8 configuration and its three zero-weight compatibility/probe requests.",
    "Materialize only certified original/v5 selected literal ToolSandbox rows with provenance; preserve Gemini's adjudicated zero and Qwen's segmented-timing limitation.",
    "Implement explicit row-path and external freeze-provenance adapters without modifying frozen plans, plus panel-bound offline replay.",
    "Implement normalized scientific costs from the selected routes, including Haiku prompt-length tiers; do not flatten tiers, infer cache hits, or use original-provider DeepSeek prices.",
    "Derive fresh selected-panel policy metadata and public provenance closure; the retained manifest's historical activation metadata and full-panel archival references are not a final publication inventory.",
)


class AdmissionPending(ValueError):
    """Preparation succeeded, but scientific release admission is unsupported."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode()


def stable_hash(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def resolve(root: Path, name: str) -> Path:
    require(isinstance(name, str) and name != "", "artifact path is missing")
    relative = Path(name)
    require(not relative.is_absolute() and ".." not in relative.parts, "artifact path escapes repository")
    path = (root / relative).resolve()
    require(path.is_relative_to(root.resolve()), "artifact symlink escapes repository")
    return path


def bind(root: Path, name: str) -> dict:
    path = resolve(root, name)
    require(path.is_file(), f"missing bound artifact: {name}")
    return {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def bound_json(root: Path, binding: dict) -> dict:
    require(bind(root, binding["path"]) == {k: binding[k] for k in ("path", "sha256")},
            f"artifact hash changed: {binding['path']}")
    value = json.loads(resolve(root, binding["path"]).read_text())
    require(isinstance(value, dict), "artifact root must be an object")
    return value


def anchored(root: Path, name: str) -> dict:
    return bound_json(root, {"path": name, "sha256": ANCHORS[name]})


def verify_self_hash(value: dict, field: str) -> None:
    body = dict(value)
    require(body.pop(field, None) == stable_hash(body), f"invalid {field}")


def condition_id(condition: dict) -> str:
    value = condition.get("condition_id")
    return value if isinstance(value, str) else stable_hash(condition)


def verify_bindings(root: Path, entries: list[dict]) -> None:
    for entry in entries:
        require(bind(root, entry["path"])["sha256"] == entry["sha256"],
                f"bound input changed: {entry['path']}")


def retained_sources(root: Path) -> tuple[dict, dict, set[str]]:
    panel = anchored(root, RETAINED + "/release-panel.json")
    manifest = anchored(root, RETAINED + "/release-analysis-inputs.json")
    verify_self_hash(panel, "artifact_hash")
    verify_self_hash(manifest, "manifest_hash")
    models = {item["model"] for item in panel["configurations"]}
    require(len(models) == len(panel["configurations"]) == 14 and not models & NEW_MODELS,
            "retained configuration inventory changed")
    require(set(manifest["models"]) == models and manifest["total_rows"] == 17224,
            "retained manifest inventory changed")
    require(manifest["panel_sha256"] == ANCHORS[RETAINED + "/release-panel.json"],
            "retained panel binding changed")
    require(panel["coverage_counts"] == {"core_descriptive": 14, "auxiliary_descriptive": 13,
        "structural_additional_descriptive": 8, "concurrent_menu_descriptive": 5,
        "layer_activation_initial": 2, "structural_registered": 2}, "retained coverage changed")
    observed, seen, condition_ids = {}, set(), set()
    for group, entries in manifest["inputs"].items():
        count = 0
        for entry in entries:
            require(entry["model"] in models, "foreign retained model")
            verify_bindings(root, [entry])
            raw = resolve(root, entry["path"]).read_bytes().splitlines()
            require(len(raw) == entry["rows"] and all(raw), "retained row count changed")
            field = entry.get("identifier_field", "condition_id")
            for line in raw:
                row = json.loads(line)
                require(row.get("model", row.get("condition", {}).get("model")) == entry["model"],
                        "retained row model changed")
                identity = row.get(field)
                require(isinstance(identity, str) and (field, identity) not in seen,
                        "retained identifier missing or duplicated")
                seen.add((field, identity))
                if field == "condition_id":
                    condition_ids.add(identity)
            count += len(raw)
        observed[group] = count
    require(observed == manifest["expected_rows"] and sum(observed.values()) == 17224,
            "retained group denominators changed")
    return panel, manifest, condition_ids


def selected_v5_tool_rows(root: Path, model: str, plan: dict, original: str) -> dict:
    """Select literal certified rows; do not interpret a model-error zero as missing."""
    report = anchored(root, V5_REPORTS[model])
    require(report.get("schema") == "panel-refresh-v5-nine-block-offline-replay-v2"
            and report.get("verification_driver_version") == "v6" and report.get("model") == model
            and report.get("passed") is True and report.get("release_admitted") is False
            and report.get("conditions") == 1336 and report.get("internal_conditions") == 1240
            and report.get("toolsandbox_conditions") == 96 and report.get("new_api_calls") == 0
            and report.get("network_disabled") is True
            and report.get("toolsandbox_official_regrades") == (96 if model == QWEN else 95)
            and report.get("toolsandbox_frozen_zero_fallbacks") == (0 if model == QWEN else 1),
            "v5/v6 selection certificate differs")
    audit_records = [b for b in report["outputs"] if b["path"] == str(Path(V5_REPORTS[model]).parent / "input-audit.json")]
    require(len(audit_records) == 1, "missing exact v5 input audit")
    audit = bound_json(root, audit_records[0])
    require(audit.get("schema") == "panel-refresh-v5-nine-block-audit-v1"
            and audit.get("model") == model and audit.get("passed") is True
            and audit.get("release_admitted") is False and audit.get("completed_conditions") == 1336
            and audit.get("conditions_by_block") == BLOCKS, "v5 selection inventory differs")
    require(Counter(item["block"] for item in audit["selected"]) == BLOCKS
            and len({item["condition_id"] for item in audit["selected"]}) == 1336,
            "v5 selected condition inventory differs")
    selected = [item for item in audit["selected"] if item["block"] == "toolsandbox"]
    overlay = BASE + "/execution/toolsandbox-recovery-v5/" + model.replace("/", "--") + ".jsonl"
    expected_counts = {original: 68, overlay: 28} if model == QWEN else {original: 25, overlay: 71}
    require(Counter(item["journal"] for item in selected) == expected_counts,
            "ToolSandbox original/overlay source selection changed")
    input_hashes = {item["path"]: item["sha256"] for item in audit["inputs"]}
    lines = {}
    for name in expected_counts:
        require(name in input_hashes, "selected journal lacks audit binding")
        verify_bindings(root, [{"path": name, "sha256": input_hashes[name]}])
        lines[name] = resolve(root, name).read_bytes().splitlines(keepends=True)
    require(len(lines[original]) == (69 if model == QWEN else 25)
            and len(lines[overlay]) == expected_counts[overlay]
            and json.loads(lines[original][-1]).get("status") == "failed_closed",
            "frozen original/overlay journal boundary changed")
    planned = {condition_id(c): c for c in plan["conditions"]}
    seen = set()
    for item in selected:
        index, name = item["row_index"], item["journal"]
        require(type(index) is int and 0 <= index < len(lines[name]), "invalid literal row index")
        line = lines[name][index]
        row = json.loads(line)
        identifier = item["condition_id"]
        require(hashlib.sha256(line).hexdigest() == item["literal_line_sha256"]
                and stable_hash(row) == item["row_sha256"], "selected literal row changed")
        require(identifier in planned and identifier not in seen and row.get("condition_id") == identifier
                and row.get("model") == model and row.get("plan_hash") == plan["plan_hash"],
                "selected row condition/model/plan differs")
        observed = {k: row[k] for k in planned[identifier] if k in row}
        expected = {k: v for k, v in planned[identifier].items() if k in row}
        require(stable_hash(observed) == stable_hash(expected),
                "selected row condition payload differs")
        seen.add(identifier)
    require(seen == set(planned) and len(seen) == 96, "selected ToolSandbox inventory is incomplete")
    return {"mode": "literal_v5_selection", "report": bind(root, V5_REPORTS[model]),
            "input_audit": audit_records[0], "journals": [bind(root, p) for p in sorted(lines)],
            "selected_rows": deepcopy(selected), "admission_status": "pending_independent_certificate_closure",
            "timing_policy": "segmented_not_uninterrupted_latency" if model == QWEN else "unchanged",
            "model_error_policy": "preserve_independently_adjudicated_unknown_tool_zero" if model == GEMINI else "unchanged"}


def prepare_source_map(root: Path = ROOT) -> dict:
    """Validate immutable metadata and retained evidence; never admit new results."""
    root = root.resolve()
    panel, retained, retained_ids = retained_sources(root)
    manifests = {key: anchored(root, path) for key, path in MANIFEST_PATHS.items()}
    prices = {key: anchored(root, path) for key, path in PRICE_PATHS.items()}
    for manifest in manifests.values():
        verify_self_hash(manifest, "manifest_hash")
        verify_bindings(root, manifest["inputs"])
    for price in prices.values():
        verify_self_hash(price, "artifact_hash")
    require(len(manifests["original"]["plans"]) == 63 and manifests["original"]["planned_conditions"] == 9352,
            "original frozen inventory changed")
    require(manifests["haiku"]["models"] == [HAIKU] and manifests["haiku"]["planned_conditions"] == 1336,
            "Haiku is not an independent complete planned inventory")
    require(manifests["deepinfra"]["model"] == DEEPSEEK
            and manifests["deepinfra"]["configuration_id"] == DEEPINFRA_CONFIGURATION
            and manifests["deepinfra"]["planned_conditions"] == 1336,
            "DeepSeek is not the isolated DeepInfra configuration")
    configuration_lists = {"original": prices["original"]["configurations"],
        "haiku": prices["haiku"]["configurations"], "deepinfra": [prices["deepinfra"]["configuration"]]}
    sources, configurations, all_ids = [], [], set()
    for model in sorted(NEW_MODELS):
        campaign = "haiku" if model == HAIKU else "deepinfra" if model == DEEPSEEK else "original"
        manifest = manifests[campaign]
        candidates = [c for c in configuration_lists[campaign] if c["model"] == model]
        require(len(candidates) == 1, "selected configuration/price is ambiguous")
        configuration = candidates[0]
        entries = [e for e in manifest["plans"] if e["model"] == model]
        require(Counter(e["block"] for e in entries) == Counter({block: 1 for block in BLOCKS}),
                "selected model must have exactly nine plans")
        configuration_key = stable_hash({"model": model, "manifest": bind(root, MANIFEST_PATHS[campaign]),
                                         "configuration": configuration})
        configuration_record = {"model": model, "configuration_key": configuration_key,
            "display_name": configuration["display_name"],
            "configuration_sha256": stable_hash(configuration), "manifest": bind(root, MANIFEST_PATHS[campaign]),
            "pricing": bind(root, PRICE_PATHS[campaign]), "route": deepcopy(configuration["provider"]),
            "raw_route_pricing": deepcopy(configuration["selected_route"]["pricing"]),
            "price_adapter_status": "pending_route_and_prompt_band_aware_normalization"}
        if model == DEEPSEEK:
            require(configuration["configuration_id"] == DEEPINFRA_CONFIGURATION
                    and configuration["display_name"] == "DeepSeek V4.1 Flash"
                    and configuration["provider"]["only"] == ["deepinfra/fp8"]
                    and configuration["provider"]["quantizations"] == ["fp8"], "foreign DeepSeek route")
            configuration_record["configuration_id"] = DEEPINFRA_CONFIGURATION
        configurations.append(configuration_record)
        model_ids = set()
        for entry in entries:
            block = entry["block"]
            plan = bound_json(root, entry)
            plan_body = dict(plan)
            supplied = plan_body.pop("plan_hash", None)
            expected_hash = stable_hash(plan_body)
            require((supplied is None or supplied == expected_hash) and entry["plan_hash"] == expected_hash,
                    "plan self-hash differs")
            require(type(entry["conditions"]) is int and entry["conditions"] == len(plan["conditions"]) == BLOCKS[block],
                    "planned block denominator changed")
            ids = [condition_id(c) for c in plan["conditions"]]
            require(len(set(ids)) == len(ids) and not (set(ids) & (all_ids | retained_ids))
                    and all(c["model"] == model for c in plan["conditions"]), "condition IDs overlap or name a foreign model")
            execution = plan.get("execution", plan.get("configuration", {}).get("execution"))
            require(isinstance(execution, dict) and execution["provider_only"] == configuration["provider"]["only"]
                    and execution["provider_allow_fallbacks"] is False, "plan route does not match selected configuration")
            if model == DEEPSEEK:
                require(entry.get("configuration_id") == DEEPINFRA_CONFIGURATION
                        and entry["path"].startswith(DEEPINFRA_BASE + "/plans/")
                        and entry["output"] == DEEPINFRA_BASE + "/execution/scored/" + block + ".jsonl"
                        and execution.get("provider_quantizations") == ["fp8"], "old DeepSeek route cannot enter final source map")
            resolve(root, entry["output"])
            selection = {"mode": "whole_frozen_output", "path": entry["output"],
                         "admission_status": "pending_complete_independent_model_certificate"}
            if model in V5_REPORTS and block == "toolsandbox":
                selection = selected_v5_tool_rows(root, model, plan, entry["output"])
            sources.append({"model": model, "configuration_key": configuration_key,
                "block": block, "ledger_group": GROUPS[block], "planned_conditions": BLOCKS[block],
                "plan": {"path": entry["path"], "sha256": entry["sha256"]}, "plan_hash": entry["plan_hash"],
                "condition_ids_sha256": stable_hash(sorted(ids)), "execution_sha256": stable_hash(execution),
                "row_selection": selection})
            all_ids.update(ids)
            model_ids.update(ids)
        require(len(model_ids) == 1336, "per-model condition inventory changed")
        if campaign != "original":
            require(stable_hash(sorted(model_ids)) == manifest["condition_ids_sha256"], "independent campaign condition commitment changed")
    require(len(sources) == 72 and len(all_ids) == 10688, "new planned inventory does not close")
    increments = Counter()
    for source in sources:
        increments[source["ledger_group"]] += source["planned_conditions"]
    expected_rows = dict(retained["expected_rows"])
    for name, count in increments.items():
        expected_rows[name] += count
    require(sum(expected_rows.values()) == 27912
            and {name: expected_rows[name] for name in FINAL_FRONTIER_ROWS} == FINAL_FRONTIER_ROWS,
            "final expected group denominators changed")
    result = {"schema": "panel-refresh-22-pre-admission-source-selection-v1",
        "status": "source_selection_validated_only", "release_admitted": False,
        "independent_certificate_admission": "not_implemented", "new_api_calls": 0,
        "paper_display_policy": "Use model names in prose/tables/figures; route and precision belong only to reproducibility configuration metadata, not acquisition chronology.",
        "retained_panel": bind(root, RETAINED + "/release-panel.json"),
        "retained_manifest": bind(root, RETAINED + "/release-analysis-inputs.json"),
        "retained_models": sorted(c["model"] for c in panel["configurations"]),
        "new_models": sorted(NEW_MODELS), "configurations": configurations, "sources": sources,
        "price_snapshots": [bind(root, p) for p in PRICE_PATHS.values()],
        "planned_new_conditions": 10688, "expected_total_rows": 27912,
        "expected_group_rows": expected_rows, "expected_coverage_counts": deepcopy(FINAL_COVERAGE),
        "expected_episode_counts": deepcopy(FINAL_EPISODES), "remaining_gates": list(REMAINING_GATES)}
    result["artifact_hash"] = stable_hash(result)
    return result


def build(output_root: Path, *, root: Path = ROOT) -> None:
    """Reserved final entry point: refuse before creating *any* output directory."""
    require(not output_root.exists(), "refusing existing output root")
    prepare_source_map(root)
    raise AdmissionPending("No final panel was written: " + " ".join(REMAINING_GATES))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "build"))
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args(argv)
    if args.command == "build" and args.output_root is None:
        parser.error("build requires --output-root (currently fail-closed; no files will be written)")
    if args.command == "check" and args.output_root is not None:
        parser.error("check never writes an output root")
    try:
        if args.command == "build":
            build(args.output_root)
        else:
            result = prepare_source_map()
            print(json.dumps({key: result[key] for key in ("status", "release_admitted", "new_api_calls",
                "planned_new_conditions", "expected_total_rows", "expected_coverage_counts", "remaining_gates")}, indent=2))
        return 0
    except (ValueError, KeyError, OSError, TypeError) as exc:
        print(json.dumps({"status": "blocked", "release_admitted": False, "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
