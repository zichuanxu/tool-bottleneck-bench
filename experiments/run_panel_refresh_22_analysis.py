#!/usr/bin/env python3
"""Generate a fresh, candidate-only 22-configuration analysis directory.

No acquisition, publication, paper mutation or verification certificate is part
of this driver. All eight independent certificates and scientific cost maps are
required *before* mkdir. Failure after mkdir leaves a clearly incomplete fresh
directory; it never overwrites, resumes, deletes, or promotes an older output.
Preserved historical sub-analyses keep their original bytes and panel bindings.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
from types import MappingProxyType

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT / "src", ROOT / "experiments"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from experiments import build_panel_refresh_22 as selection
from experiments import panel_refresh_22_materialize as materializer
from experiments import panel_refresh_22_metadata as metadata
from experiments import panel_refresh_22_costs as costing
from experiments import panel_refresh_22_external as external
from experiments import panel_refresh_22_rows as literal
from experiments import analyze_model_system_decisions as decisions
from experiments import analyze_final_panel_cost as total_cost
from experiments import analyze_structural_model_system_decisions as structural_cost

# The exact retained-fourteen five-file price policy, not October prices.
PRICE_BINDINGS = (
    ("experiments/openrouter-endpoint-snapshot.json", "0ea698e4477f2aa6847ebcbb26f50ecd460f500a7ddd68072a28b2c8c8c6fd8b"),
    ("experiments/google-cloud-price-snapshot.json", "70adaf06fa375c0de395f5700da5b660581ffbe8d1a1b27aaf5211cb55190ad6"),
    ("configs/release-candidate-pricing-2026-09-02.json", "60eadab158c077c52f5ae7d9c3b51b356582d85bb798be7adc53b80240fa3dc7"),
    ("configs/frontier-active-route-pricing-2026-09-02.json", "7b82b9002bf110e5256648d973df710ba3241f666b2e6b36dafa0e9a6607916f"),
    ("configs/opus-5-active-route-pricing-2026-09-03.json", "a6f90a79e29dd7e4728436d622523fa7885c7ee645fe683058b34254ba475b0a"),
)
PRESERVED = {
    "toolsandbox-external-validation.json": ("662c6f83ed7e956846213254606905c05fa058aa63c775533141a43f7b3d75b9", "external_rank", "external_rank_models"),
    "structural-repair-holdout.json": ("642c69808225ce3d49aa974bf68337816db816458ffb53c27600b584698ab88a", "structural_holdout", "structural_registered_models"),
    "historical-toolsandbox-menu.json": ("edaa7714eceed222fd0c703838c2393ceac686608f079283fb943f43f911c8cc", "historical_menu_intervention", None),
}
PROTOCOLS = (
    "experiments/model-versus-system-decision-protocol.md",
    "protocols/interaction-scale-sensitivity-2026-09-01.md",
    "protocols/failure-mechanism-decomposition-2026-09-02.md",
    "protocols/capacity-cluster-sensitivity-2026-09-02.md",
    "protocols/diagnosis-repair-crossover-2026-09-04.md",
)
STRUCTURAL_GROUPS = ("structural_holdout", "candidate_structural_additional", "frontier_structural_additional")
MENU_GROUPS = metadata.CONCURRENT_GROUPS
SUMMARY = "analysis-generation.json"


def require(value, message):
    selection.require(value, message)


def same(left, right, message):
    require(selection.canonical(left) == selection.canonical(right), message)


def encoded(value):
    # Existing ordinary CLIs validate this (ASCII-escaped) manifest hash format.
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def hashed(value, field="artifact_hash"):
    value = deepcopy(value)
    value.pop(field, None)
    value[field] = sha256(encoded(value)).hexdigest()
    return value


def _destination(root, output_root):
    root, target = Path(root).resolve(), Path(output_root)
    target = target if target.is_absolute() else root / target
    require(not target.exists() and not target.is_symlink(), "output directory must be fresh; no overwrite or resume")
    target = target.resolve()
    require(target.is_relative_to(root / "results") and target != root / "results",
            "output must be a fresh child of this repository's results directory")
    require(target.parent.is_dir(), "output parent must already exist")
    return target


def _binding(root, name):
    return selection.bind(root, name)


class Inputs:
    def __init__(self, root):
        self.root, self.bindings = root, {}

    def add(self, records):
        for record in records:
            require(set(record) == {"path", "sha256"}, "invalid input binding")
            name, digest = record["path"], record["sha256"]
            selection.resolve(self.root, name)
            require(name not in self.bindings or self.bindings[name] == digest, "inconsistent input snapshots")
            require(isinstance(digest, str) and len(digest) == 64
                    and all(c in "0123456789abcdef" for c in digest), "invalid input digest")
            self.bindings[name] = digest

    def read(self, record):
        self.add([record])
        data = selection.resolve(self.root, record["path"]).read_bytes()
        require(sha256(data).hexdigest() == record["sha256"], "changed bound input: " + record["path"])
        return data

    def records(self):
        return [{"path": p, "sha256": h} for p, h in sorted(self.bindings.items())]

    def verify(self):
        for record in self.records():
            self.read(record)


def _rows(files, manifest):
    result, seen = {}, set()
    for group, entries in manifest["inputs"].items():
        result[group] = []
        for entry in entries:
            raw = files[entry["path"]]
            require(sha256(raw).hexdigest() == entry["sha256"], "materialized journal digest differs")
            rows = [literal._json(line) for line in raw.splitlines()]
            same(len(rows), entry["rows"], "materialized row denominator differs")
            for row in rows:
                identity = materializer._identity(row, entry.get("identifier_field", "condition_id"), entry["model"], seen)
                require(bool(identity), "missing materialized identity")
            result[group].extend(rows)
    same(len(seen), 27912, "selected row identity denominator differs")
    return result


def _group_rows(rows, names):
    return [row for name in names for row in rows[name]]


def _check_join(material, meta, lookup, inputs):
    manifest, provenance = material.manifest, material.provenance
    selection.verify_self_hash(meta, "artifact_hash")
    selection.verify_self_hash(provenance, "artifact_hash")
    selection.verify_self_hash(manifest, "manifest_hash")
    require(meta.get("release_admitted") is False and manifest.get("release_admitted") is False
            and provenance.get("release_admitted") is False, "candidate helpers cannot grant publication admission")
    same(provenance["source_selection_sha256"], meta["source_selection_sha256"], "metadata/materialization source selection differs")
    same(manifest["expected_rows"], meta["expected_group_rows"], "metadata/materialization group counts differ")
    same(manifest["total_rows"], 27912, "not a complete 22-configuration inventory")
    require(manifest["path_base"] == "materialization_root", "unreviewed materializer path semantics")
    same(manifest["models"], sorted(c["model"] for c in meta["panel"]["configurations"]), "selected panel model inventory differs")
    same(meta["panel"]["coverage_counts"], selection.FINAL_COVERAGE, "selected coverage differs")
    same(meta["panel"]["episode_counts"], selection.FINAL_EPISODES, "selected episode counts differ")
    same(sorted(lookup.models), sorted(selection.NEW_MODELS), "all eight scientific cost maps required")
    inputs.add(meta["inputs"])
    inputs.add(provenance["inputs"])
    records = {c["model"]: c for c in provenance["model_certificates"]}
    configs = {c["model"]: c for c in provenance["configurations"]}
    artifacts = lookup.artifacts
    require(len(artifacts) == 8 and {a["model"] for a in artifacts} == selection.NEW_MODELS,
            "scientific cost inventory is incomplete or duplicated")
    for artifact in artifacts:
        selection.verify_self_hash(artifact, "artifact_hash")
        model = artifact["model"]
        same(artifact["conditions"], 1336, "incomplete scientific cost model")
        same(artifact["model_certificate_sha256"], records[model]["candidate_sha256"], "cost/model certificate differs")
        same(artifact["reports"], records[model]["reports"], "cost/report bindings differ")
        same(artifact["manifest"], records[model]["manifest"], "cost/acquisition manifest differs")
        same(artifact["configuration_key"], configs[model]["configuration_key"], "cost/configuration key differs")
        require(artifact["release_admitted"] is False and artifact["billed_spending_or_budget_hold_accounting"] is False,
                "scientific costs cannot be billing or publication claims")
        inputs.add(artifact["inputs"])
    rows = _rows(material.files, manifest)
    observed = Counter()
    for values in rows.values():
        for row in values:
            model = materializer._model(row)
            if model in selection.NEW_MODELS:
                lookup.cost_for_row(row)  # No missing-row/changed-byte fallback.
                observed[model] += 1
    same(dict(observed), {m: 1336 for m in selection.NEW_MODELS}, "new row/cost lookup coverage differs")
    for group, entries in manifest["inputs"].items():
        same(sorted({e["model"] for e in entries}), meta["planned_ledger_group_models"][group], "ledger model membership differs")
    return rows


def _historical(root, panel, retained, inputs):
    files, scopes = {}, []
    for name, (digest, group, field) in PRESERVED.items():
        binding = {"path": selection.RETAINED + "/" + name, "sha256": digest}
        raw = inputs.read(binding)
        payload = literal._json(raw)
        models = sorted({e["model"] for e in retained["inputs"]["external" if group == "external_rank" else group]})
        if field:
            same(models, panel[field], "preserved population differs from selected panel")
            same(payload["models"], models, "preserved artifact model scope differs")
        else:
            same(payload["models"], len(models), "historical menu model count differs")
        files[name] = raw
        scopes.append({"name": name, "source": binding, "sha256": digest, "models": models,
            "population": group, "operation": "byte_preserved_not_recomputed",
            "original_panel_binding_unchanged": True, "new_22_panel_binding": False})
    return files, scopes


def _external_provenance(root, material, retained, rows, panel, inputs):
    """Select old plan scopes without importing historical full-model metadata."""
    result = {"structural": [], "toolsandbox": [], "duration": []}
    old_inputs = retained["inputs"]
    for kind, scopes, groups in (
        ("structural", ("structural_holdout", "structural_additional"), STRUCTURAL_GROUPS),
        ("toolsandbox", ("menu_intervention",), MENU_GROUPS),
    ):
        expected = {materializer._model(r) for r in _group_rows(rows, groups)} - selection.NEW_MODELS
        seen = set()
        for scope in scopes:
            for source in retained["plan_sources"][scope]:
                models = set(source["models"])
                require(models <= expected and not models & seen, "old external plan scope differs")
                seen.update(models)
                candidates = [e for group in groups for e in old_inputs[group]
                    if e["model"] in models and e.get("plan_path") == source["plan"]]
                digests = {e["plan_sha256"] for e in candidates}
                if "plan_sha256" in source:
                    digests.add(source["plan_sha256"])
                require(len(digests) == 1, "old external plan has no unambiguous anchored binding")
                plan = literal._json(inputs.read({"path": source["plan"], "sha256": digests.pop()}))
                selected = [r for r in _group_rows(rows, groups) if materializer._model(r) in models]
                require(all(r["plan_hash"] == plan["plan_hash"] for r in selected), "old external row/plan mismatch")
                planned = [c for c in plan["conditions"] if c["model"] in models]
                ids = {selection.condition_id(c) for c in planned}
                require(len(ids) == len(planned) == len(selected)
                        and ids == {r["condition_id"] for r in selected}, "old external planned condition inventory differs")
                result[kind].append({"configurations": sorted(models), "plan_hash": plan["plan_hash"],
                    "frozen_at": plan.get("frozen_at"), "declared_row_weight": plan.get("confirmatory_weight"),
                    "structural_outcomes_seen_before_freeze": plan.get("structural_outcomes_access_before_freeze", plan.get("outcome_access_before_freeze")),
                    "configurations_selected_on_core_outcomes": plan.get("candidate_core_outcomes_inspected_before_freeze")})
        require(seen == expected, "old external provenance is incomplete")
    for projection in material.provenance["new_projections"]:
        p = projection["projection"]
        block = p["block"]
        if block in ("structural", "toolsandbox"):
            result[block].append(deepcopy(projection["external_plan_metadata"]))
        if block == "toolsandbox":
            result["duration"].extend(deepcopy(p["selected_rows"]))
    require(len(panel["structural_registered_models"]) == 2
            and len(panel["structural_additional_models"]) == 16
            and len(panel["concurrent_menu_models"]) == 13, "external population denominators differ")
    return result


def _code_paths(root):
    # Bind the offline implementation and tests, including imported statistical
    # helpers. Do not snapshot acquisition receipts or other live directories.
    return sorted({p.relative_to(root).as_posix() for base in ("experiments", "src/bottleneckbench", "tests/benchmark")
                   for p in (root / base).rglob("*.py")})


def _crossover_paths(root, models):
    from experiments import analyze_crossover_campaign as crossover
    slugs = {slug for slug, model in crossover.SLUGS.items() if model in models}
    require(len(slugs) == 4, "selected historical crossover population differs")
    base = root / "results/prospective-execution/diagnosis-repair-crossover-1"
    paths = []
    for path in base.glob("repeat-*/*.jsonl"):
        slug, arm = path.stem.rsplit("-", 1)
        if slug in slugs and arm in crossover.ARMS:
            paths.append(path.relative_to(root).as_posix())
    require(bool(paths), "selected historical crossover evidence missing")
    return sorted(paths)


PANEL_MODEL_LISTS = ("activation_models", "auxiliary_models", "structural_registered_models",
                     "structural_additional_models", "external_rank_models", "concurrent_menu_models")


def _subset(files, manifest, panel, infrastructure, exclude, reporting_date):
    """Reporting subset of the certified inventory: drop whole configurations, recompute denominators.

    Same presentation-scope projection as filter_reporting_panel.py; acquired rows and frozen
    plans are untouched. Populations of byte-preserved or confirmatory analyses cannot shrink.
    """
    require(isinstance(reporting_date, str) and len(reporting_date) == 10, "a reporting subset needs --reporting-date YYYY-MM-DD")
    models = {c["model"] for c in panel["configurations"]}
    require(exclude <= models, "excluded model is not in the selected panel")
    for field in ("activation_models", "external_rank_models", "structural_registered_models"):
        require(not exclude & set(panel[field]), "cannot exclude a member of a confirmatory or preserved population: " + field)
    retained_models = models - exclude
    for group, entries in manifest["inputs"].items():
        for entry in entries:
            if entry["model"] in exclude:
                files.pop(entry["path"])
        manifest["inputs"][group] = [e for e in entries if e["model"] in retained_models]
    require(not any(name.startswith("evidence/") for name in set(files) - {e["path"] for es in manifest["inputs"].values() for e in es}),
            "unbound evidence file after subset projection")
    seen = set()
    for entries in manifest["inputs"].values():
        for entry in entries:
            field = entry.get("identifier_field", "condition_id")
            seen.update(literal._json(line).get(field) for line in files[entry["path"]].splitlines())
    infrastructure = {k: [i for i in v if i in seen] for k, v in infrastructure.items()}
    manifest["expected_rows"] = {g: sum(e["rows"] for e in es) for g, es in manifest["inputs"].items()}
    manifest["total_rows"] = sum(manifest["expected_rows"].values())
    manifest["models"] = sorted(retained_models)
    manifest["model_count"] = len(retained_models)
    manifest["new_rows"] = sum(e["rows"] for es in manifest["inputs"].values() for e in es if e["model"] in selection.NEW_MODELS)
    manifest["retained_rows"] = manifest["total_rows"] - manifest["new_rows"]
    panel["configurations"] = [c for c in panel["configurations"] if c["model"] in retained_models]
    for field in PANEL_MODEL_LISTS:
        panel[field] = [m for m in panel[field] if m in retained_models]
    panel["coverage_counts"] = {"core_descriptive": len(retained_models), "auxiliary_descriptive": len(panel["auxiliary_models"]),
        "structural_additional_descriptive": len(panel["structural_additional_models"]),
        "concurrent_menu_descriptive": len(panel["concurrent_menu_models"]),
        "layer_activation_initial": len(panel["activation_models"]),
        "structural_registered": len(panel["structural_registered_models"])}
    episodes = {k: sum(manifest["expected_rows"][g] for g in names) for k, names in metadata.EPISODE_GROUPS.items()}
    episodes["analysis_manifest"] = manifest["total_rows"]
    require(sum(episodes[k] for k in metadata.EPISODE_GROUPS) == manifest["total_rows"], "subset episode groups omit rows")
    panel["episode_counts"] = episodes
    panel["reporting_policy"] = panel["reporting_policy"].replace(f"the {len(models)}-configuration", f"the {len(retained_models)}-configuration")
    panel["reporting_scope"].update(date=reporting_date, excluded_models=sorted(exclude),
        status=f"existing-data reporting subset of the certified {len(models)}-configuration inventory; "
               "complete inventories independently certified; whole-panel analyses not yet verified")
    return files, manifest, panel, infrastructure


def _subset_external(provenance, exclude, rows):
    """Narrow external plan scopes to the reporting subset; plans themselves are unchanged."""
    result = {"duration": [r for r in provenance["duration"] if r["condition_id"] in
                           {row.get("condition_id") for values in rows.values() for row in values}]}
    for kind in ("structural", "toolsandbox"):
        result[kind] = []
        for record in provenance[kind]:
            record = deepcopy(record)
            if "configurations" in record:
                record["configurations"] = [m for m in record["configurations"] if m not in exclude]
                if record["configurations"]:
                    result[kind].append(record)
            elif record.get("model") not in exclude:
                result[kind].append(record)
    return result


def _rebase(material, meta, retained, output_relative, exclude=frozenset(), reporting_date=None):
    files = dict(material.files)
    manifest = material.manifest
    files.pop(materializer.MANIFEST_PATH)
    panel = deepcopy(meta["panel"])
    panel.update(status="candidate_complete_evidence_pending_analysis_verification",
        scientific_role="selected complete-evidence panel; generated analyses and publication admission remain separate gates")
    panel["reporting_scope"].update(status="complete inventories independently certified; whole-panel analyses not yet verified",
        denominator_status="observed_selected_complete_inventory")
    infrastructure = deepcopy(retained["infrastructure_condition_ids"])
    if exclude:
        files, manifest, panel, infrastructure = _subset(files, manifest, panel, infrastructure, exclude, reporting_date)
    panel = hashed(panel)
    files["release-panel.json"] = encoded(panel) + b"\n"
    for entries in manifest["inputs"].values():
        for entry in entries:
            entry["path"] = str(PurePosixPath(output_relative) / entry["path"])
    manifest.update(path_base="repository_root", panel_path=output_relative + "/release-panel.json",
        panel_sha256=sha256(files["release-panel.json"]).hexdigest(),
        analysis_policy=deepcopy(meta["analysis_policy"]),
        infrastructure_condition_ids=infrastructure,
        status="complete_candidate_analysis_inputs_not_publication_admission",
        analysis_policy_status="fresh_selected_only_metadata", panel_binding_status="fresh_selected_panel_bytes")
    manifest = hashed(manifest, "manifest_hash")
    files[materializer.MANIFEST_PATH] = encoded(manifest) + b"\n"
    return files, manifest, panel


@dataclass(frozen=True)
class PreparedAnalysis:
    root: Path
    output_root: Path
    files: object
    manifest: dict
    panel: dict
    rows: dict
    row_costs: object
    prices: tuple
    external_provenance: dict
    preserved: list
    inputs: object
    source_selection_sha256: str
    private_provenance: dict
    code_paths: tuple
    crossover_paths: tuple


def prepare_analysis(root: Path, output_root: Path, exclude_models=(), reporting_date=None) -> PreparedAnalysis:
    """Write-free preflight; not a caller-supplied-certificate admission API.

    ``exclude_models`` (default none) projects the fully gated selected panel onto a reporting
    subset after every certificate, cost and denominator check has passed on the full inventory.
    """
    exclude = frozenset(exclude_models)
    root = Path(root).resolve()
    require(root == ROOT.resolve(), "driver must use the repository containing its analysis code")
    destination = _destination(root, output_root)
    inputs = Inputs(root)
    code_paths = _code_paths(root)
    inputs.add([_binding(root, p) for p in code_paths])
    material = materializer.prepare_materialization(root)  # All eight, fresh gates first.
    meta = metadata.build_candidate_metadata(root)
    lookup = costing.RowCostLookup(root, sorted(selection.NEW_MODELS))
    rows = _check_join(material, meta, lookup, inputs)
    retained = literal._json(inputs.read(material.provenance["retained_manifest"]))
    # This exact anchored inventory contains eight capacity and one discovery
    # infrastructure outcomes. None is dropped/reassigned on panel expansion.
    infrastructure = retained["infrastructure_condition_ids"]
    all_ids = set().union(*(set(v) for v in infrastructure.values()))
    require(len(all_ids) == 9 and len(infrastructure["capacity"]) == 8
            and len(infrastructure["discovery"]) == 1, "retained infrastructure inventory changed")
    row_ids = {r.get("condition_id") for values in rows.values() for r in values}
    require(all_ids <= row_ids, "retained infrastructure row missing")
    prices = tuple(literal._json(inputs.read({"path": p, "sha256": h})) for p, h in PRICE_BINDINGS)
    external_provenance = _external_provenance(root, material, retained, rows, meta["panel"], inputs)
    preserved_files, preserved = _historical(root, meta["panel"], retained, inputs)
    crossover_paths = _crossover_paths(root, set(material.manifest["models"]))
    inputs.add([_binding(root, p) for p in [*crossover_paths, *PROTOCOLS]])
    files, manifest, panel = _rebase(material, meta, retained, destination.relative_to(root).as_posix(), exclude, reporting_date)
    if exclude:
        rows = {group: [r for r in values if materializer._model(r) not in exclude] for group, values in rows.items()}
        external_provenance = _subset_external(external_provenance, exclude, rows)
    require(not set(files) & set(preserved_files), "preserved/generated file collision")
    files.update(preserved_files)
    private = {"schema": "panel-refresh-22-analysis-driver-private-provenance-v1", "release_admitted": False,
        "visibility": "internal_only_not_public_package_content", "materialization": material.provenance,
        "metadata": meta, "scientific_cost_artifacts": lookup.artifacts}
    inputs.verify()  # Includes transitive certificate inputs and every price snapshot.
    same(_code_paths(root), code_paths, "offline source inventory changed during preflight")
    _destination(root, destination)
    return PreparedAnalysis(root, destination, MappingProxyType(files), manifest, panel, rows, lookup,
        prices, external_provenance, preserved, inputs, meta["source_selection_sha256"], private,
        tuple(code_paths), tuple(crossover_paths))


def _repeat(flag, values):
    return [item for value in values for item in (flag, value)]


def ordinary_commands(prepared):
    """Same ordinary arguments/default seeds as run_release_analysis; no paid CLI."""
    root, out, manifest, panel = prepared.root, prepared.output_root, prepared.manifest, prepared.panel
    paths = {g: [e["path"] for e in es] for g, es in manifest["inputs"].items()}
    models = {e["path"]: e["model"] for es in manifest["inputs"].values() for e in es}
    def blocks(name):
        return [p for prefix in ("", "candidate_", "frontier_") for p in paths[prefix + name]]
    capacity, discovery, repair = blocks("capacity"), blocks("discovery"), blocks("repair")
    active = set(panel["activation_models"])
    prospective_capacity = [p for p in paths["capacity"] if models[p] in active]
    prospective_discovery = [p for p in paths["discovery"] if models[p] in active]
    infra = manifest["infrastructure_condition_ids"]
    panel_arg, manifest_arg = ["--panel", str(out / "release-panel.json")], ["--manifest", str(out / materializer.MANIFEST_PATH)]
    commands = []
    def add(script, name, *args):
        commands.append([sys.executable, str(root / "experiments" / script), *args, "--output", str(out / name)])
    add("analyze_unified_core_panel.py", "release-core-panel.json",
        *_repeat("--capacity-input", capacity), *_repeat("--discovery-input", discovery), *panel_arg,
        *_repeat("--expected-capacity-infrastructure-id", infra["capacity"]),
        *_repeat("--expected-discovery-infrastructure-id", infra["discovery"]))
    add("analyze_release_layer_repairs.py", "release-layer-repairs.json",
        *_repeat("--prospective-capacity", prospective_capacity), *_repeat("--prospective-discovery", prospective_discovery),
        *_repeat("--retained-capacity", [p for p in capacity if models[p] not in active]),
        *_repeat("--retained-discovery", [p for p in discovery if models[p] not in active]),
        *_repeat("--repair", repair), *panel_arg,
        *_repeat("--expected-prospective-capacity-infrastructure-id", infra["confirmatory_layer_capacity"]),
        *_repeat("--expected-prospective-discovery-infrastructure-id", infra["confirmatory_layer_discovery"]),
        *_repeat("--expected-retained-capacity-infrastructure-id", infra["retained_layer_capacity"]),
        *_repeat("--expected-retained-discovery-infrastructure-id", infra["retained_layer_discovery"]),
        *_repeat("--expected-repair-infrastructure-id", infra["repair"]))
    add("analyze_prospective_sentinel.py", "release-sentinel.json", *_repeat("--input", blocks("sentinel")), *panel_arg)
    add("analyze_workflow_frontier_panel.py", "release-workflow-frontier.json", *_repeat("--input", blocks("workflow_frontier")), *panel_arg)
    for script, name in (("analyze_failure_localization.py", "release-failure-localization.json"),
                         ("analyze_failure_localization_v2.py", "release-failure-localization-v2.json")):
        add(script, name, *manifest_arg)
    add("analyze_crossover_campaign.py", "crossover-campaign.json", *panel_arg)
    add("analyze_revision_audit.py", "revision-evidence-audit.json", *manifest_arg, *panel_arg)
    add("analyze_interaction_scale_sensitivity.py", "interaction-scale-sensitivity.json",
        *_repeat("--capacity-input", capacity), *panel_arg, "--protocol", str(root / PROTOCOLS[1]))
    add("analyze_failure_mechanisms.py", "failure-mechanism-decomposition.json",
        *manifest_arg, *panel_arg, "--protocol", str(root / PROTOCOLS[2]))
    add("analyze_capacity_sensitivity.py", "capacity-cluster-sensitivity.json", *manifest_arg, *panel_arg,
        "--external", str(out / "toolsandbox-external-validation.json"), "--protocol", str(root / PROTOCOLS[3]))
    require(not paths.get("targeted_vw_replication"), "unreviewed targeted-budget scope requires a separate adapter")
    return commands


def _legacy_prices(panel, prices, row_costs):
    candidates = {}
    for snapshot in prices[2:]:
        for model, price in snapshot["models"].items():
            value = (float(price["input"]) / 1_000_000, float(price["output"]) / 1_000_000)
            require(model not in candidates or candidates[model] == value, "conflicting retained frozen price snapshots")
            candidates[model] = value
    selected = {c["model"] for c in panel["configurations"]} - set(row_costs.models)
    historical = {**panel, "configurations": [c for c in panel["configurations"] if c["model"] in selected - set(candidates)]}
    result = decisions._prices(historical, prices[0], prices[1])
    result.update({m: p for m, p in candidates.items() if m in selected})
    require(set(result) == selected, "five frozen price snapshots do not cover the retained panel")
    return result


def _api_analyses(p):
    # Apply the same explicit ITT validation as the old CLI, not a new filter.
    def checked(name, count):
        paths = [p.root / e["path"] for group in (name, "candidate_" + name, "frontier_" + name)
                 for e in p.manifest["inputs"][group]]
        return decisions._rows(paths, expected_rows=count,
            allowed_infrastructure=frozenset(p.manifest["infrastructure_condition_ids"][name]))
    core, auxiliary = len(p.panel["configurations"]), len(p.panel["auxiliary_models"])  # 22 and 21 on the full panel
    capacity, discovery, repair = checked("capacity", core * 576), checked("discovery", core * 216), checked("repair", auxiliary * 144)
    common = {"row_costs": p.row_costs, "samples": 10_000, "seed": 20260816}
    decisions_result = decisions.analyze(capacity=capacity, discovery=discovery, repair=repair, panel=p.panel,
        openrouter_prices=p.prices[0], google_prices=p.prices[1], candidate_prices=list(p.prices[2:]), **common)
    prices = _legacy_prices(p.panel, p.prices, p.row_costs)
    cost_result = total_cost.analyze(p.manifest, prices, row_costs=p.row_costs)
    structural_rows = _group_rows(p.rows, STRUCTURAL_GROUPS)
    structural_models = set(p.panel["structural_registered_models"]) | set(p.panel["structural_additional_models"])
    structural_result = external.analyze_structural(structural_rows, structural_models,
        plan_provenance=p.external_provenance["structural"])
    structural_decisions = structural_cost.analyze(structural_result["plan_hashes"], structural_rows, prices,
        frozenset(structural_models), **common)
    menu_result = external.analyze_toolsandbox(_group_rows(p.rows, MENU_GROUPS), set(p.panel["concurrent_menu_models"]),
        plan_provenance=p.external_provenance["toolsandbox"], row_provenance=p.external_provenance["duration"])
    return {"release-model-versus-system.json": decisions_result, "release-panel-cost.json": cost_result,
        "structural-model-versus-system.json": structural_decisions, "structural-repair-pooled.json": structural_result,
        "candidate-toolsandbox-menu.json": menu_result}


def _write_new(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(raw)


def _verify_prepared(p):
    p.inputs.verify()
    same(_code_paths(p.root), list(p.code_paths), "offline source inventory changed")
    same(_crossover_paths(p.root, set(p.manifest["models"])), list(p.crossover_paths), "selected crossover inventory changed")
    for name, raw in p.files.items():
        if p.output_root.exists():
            require((p.output_root / name).read_bytes() == raw, "materialized or preserved file changed during analysis")


def run(root: Path, output_root: Path, exclude_models=(), reporting_date=None) -> dict:
    """Generate analyses, not a release or an independent replay certificate."""
    p = prepare_analysis(root, output_root, exclude_models, reporting_date)
    commands = ordinary_commands(p)
    _verify_prepared(p)
    p.output_root.mkdir(exist_ok=False)
    for name, raw in sorted(p.files.items()):
        _write_new(p.output_root / name, raw)
    # This full receipt/route provenance is explicitly private, not paper input.
    _write_new(p.output_root / "internal" / "driver-provenance.json", encoded(p.private_provenance) + b"\n")
    environment = {**os.environ, "PYTHONPATH": str(p.root / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    for command in commands:
        subprocess.run(command, cwd=p.root, env=environment, check=True)
    api_results = _api_analyses(p)
    generated = [Path(c[-1]).name for c in commands] + list(api_results)
    require(len(generated) == len(set(generated)) == 16, "generated analysis inventory differs")
    api_sources = {"release-model-versus-system.json": "analyze_model_system_decisions.py",
        "release-panel-cost.json": "analyze_final_panel_cost.py",
        "structural-model-versus-system.json": "analyze_structural_model_system_decisions.py",
        "structural-repair-pooled.json": "panel_refresh_22_external.py",
        "candidate-toolsandbox-menu.json": "panel_refresh_22_external.py"}
    for name, payload in api_results.items():
        payload = deepcopy(payload)
        payload.update(release_admitted=False, analysis_status="generated_pending_independent_whole_panel_verification",
            confirmatory_weight=0, panel_sha256=p.manifest["panel_sha256"], manifest_hash=p.manifest["manifest_hash"],
            inputs=[{"path": p.manifest["panel_path"], "sha256": p.manifest["panel_sha256"]},
                    _binding(p.root, (p.output_root / materializer.MANIFEST_PATH).relative_to(p.root).as_posix()),
                    *[{"path": path, "sha256": digest} for path, digest in PRICE_BINDINGS],
                    _binding(p.root, PROTOCOLS[0]),
                    _binding(p.root, (p.output_root / "internal" / "driver-provenance.json").relative_to(p.root).as_posix())],
            analysis_source_sha256=_binding(p.root, "experiments/" + api_sources[name])["sha256"],
            source_selection_sha256=p.source_selection_sha256)
        _write_new(p.output_root / name, encoded(hashed(payload)) + b"\n")
    # Annotate only newly computed outputs. Preserved analyses remain byte-for-
    # byte unchanged, including their historical panel binding and own hashes.
    for command in commands:
        path = Path(command[-1])
        payload = literal._json(path.read_bytes())
        payload.update(release_admitted=False,
            analysis_status="generated_pending_independent_whole_panel_verification",
            confirmatory_weight=0, selected_panel_sha256=p.manifest["panel_sha256"],
            analysis_manifest_hash=p.manifest["manifest_hash"],
            source_selection_sha256=p.source_selection_sha256)
        path.write_bytes(encoded(hashed(payload)) + b"\n")
    _verify_prepared(p)
    outputs = [_binding(p.root, (p.output_root / name).relative_to(p.root).as_posix()) for name in generated]
    report = hashed({"schema": "panel-refresh-22-analysis-generation-v1",
        "status": "analysis_generated_pending_independent_verification", "release_admitted": False,
        "visibility": "internal_generation_record_not_public_package_content",
        "new_api_calls": 0, "rows": p.manifest["total_rows"], "configurations": len(p.panel["configurations"]),
        "source_selection_sha256": p.source_selection_sha256, "manifest_hash": p.manifest["manifest_hash"],
        "panel_sha256": p.manifest["panel_sha256"], "generated_analyses": outputs,
        "preserved_artifacts": p.preserved, "inputs": p.inputs.records(), "commands": commands,
        "verification_scope": "single offline generation only; no second computation, fresh tool transitions, official regrading or publication admission",
        "required_next_gate": "independent typed whole-panel recomputation and artifact verification before paper integration"})
    _write_new(p.output_root / SUMMARY, encoded(report) + b"\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--exclude-model", action="append", default=[],
                        help="drop this configuration from the reporting panel (repeatable; default: none)")
    parser.add_argument("--reporting-date", help="YYYY-MM-DD recorded in reporting_scope; required with --exclude-model")
    args = parser.parse_args(argv)
    result = run(ROOT, args.output_root, args.exclude_model, args.reporting_date)
    print(json.dumps({"status": result["status"], "release_admitted": False,
        "analyses": len(result["generated_analyses"]), "new_api_calls": 0}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
