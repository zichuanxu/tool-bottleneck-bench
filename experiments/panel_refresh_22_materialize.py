"""Prepare complete-22 literal evidence in memory, never publish or write it.

The only public preparation entry obtains the anchored source map and all eight
complete, independently revalidated model candidates itself. Caller-authored
``passed`` flags or partial candidates are not an input. Fresh-certificate gates
therefore currently prevent any actual full-panel materialization.

Returned paths are relative to an as-yet-unselected *materialization root*, NOT
the repository root used by historical analyzers. A final driver must choose a
new destination and explicitly rebase paths, attach fresh selected-only panel
metadata/policy, and pass its remaining release gates before writing anything.
Only selected literal JSONL files and a sanitized manifest are in ``files``.
Full original source references remain in the separate internal provenance.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
from types import MappingProxyType
from typing import Mapping

from experiments import build_panel_refresh_22 as selection
from experiments import panel_refresh_22_certificates as certificates
from experiments import panel_refresh_22_rows as rows_adapter

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = "release-analysis-inputs.json"
RETAINED_ROWS = 17224
NEW_ROWS = 10688
TOTAL_ROWS = 27912
ENTRY_FIELDS = frozenset({"model", "path", "sha256", "rows", "identifier_field", "plan_hash", "plan_path", "plan_sha256"})


def require(value, message):
    selection.require(value, message)


def equal(left, right, message):
    require(selection.canonical(left) == selection.canonical(right), message)


def _json(raw):
    return rows_adapter._json(raw)


def _integer(value, label):
    require(type(value) is int and value >= 0, "invalid integer: " + label)
    return value


def _model(row):
    nested = row.get("condition", {})
    nested_model = nested.get("model") if isinstance(nested, dict) else None
    model = row.get("model", nested_model)
    require(isinstance(model, str) and model and (nested_model is None or nested_model == model), "conflicting/missing row model")
    return model


class _Sources:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.bindings = {}

    def add(self, binding):
        require(isinstance(binding, dict) and set(binding) == {"path", "sha256"}, "invalid source binding")
        path, digest = binding["path"], binding["sha256"]
        require(isinstance(path, str) and isinstance(digest, str) and re.fullmatch("[0-9a-f]{64}", digest), "malformed source binding")
        selection.resolve(self.root, path)
        require(path not in self.bindings or self.bindings[path] == digest, "conflicting source hashes")
        self.bindings[path] = digest

    def many(self, bindings):
        require(isinstance(bindings, list) and len({item["path"] for item in bindings}) == len(bindings), "duplicate source inventory")
        for binding in bindings:
            self.add(binding)

    def read(self, binding):
        self.add(binding)
        raw = selection.resolve(self.root, binding["path"]).read_bytes()
        require(sha256(raw).hexdigest() == binding["sha256"], "changed bound input: " + binding["path"])
        return raw

    def finish(self):
        for path, digest in self.bindings.items():
            self.read({"path": path, "sha256": digest})

    def inventory(self):
        return [{"path": path, "sha256": digest} for path, digest in sorted(self.bindings.items())]


@dataclass(frozen=True)
class Materialization:
    """No output-root or write method; metadata properties return fresh copies."""
    files: Mapping[str, bytes]
    _manifest_json: str
    _provenance_json: str

    @property
    def manifest(self):
        return json.loads(self._manifest_json)

    @property
    def provenance(self):
        """Internal only; do not copy this object into a public panel package."""
        return json.loads(self._provenance_json)


def _put(files, path, raw):
    relative = PurePosixPath(path)
    require(isinstance(raw, bytes) and not relative.is_absolute() and ".." not in relative.parts
            and str(relative) == path and path not in files, "unsafe/duplicate materialization destination")
    require(path.startswith("evidence/") or path == MANIFEST_PATH, "destination is outside selected evidence")
    files[path] = raw
    return {"path": path, "sha256": sha256(raw).hexdigest()}


def _safe_component(value):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]+(?:[.][A-Za-z0-9_-]+)*", value), "unsafe destination component")
    return value


def _identity(row, field, model, seen):
    require(field in {"condition_id", "sample_id", "episode_id"}, "unsupported retained identifier field")
    require(_model(row) == model, "row model is outside selected configuration")
    identity = row.get(field)
    require(isinstance(identity, str) and bool(identity) and (field, identity) not in seen, "missing/duplicate selected row identity")
    seen.add((field, identity))
    return identity


def _join_disposition(source, record, provenance):
    mode = source["row_selection"]["mode"]
    if mode == "literal_v5_selection":
        equal(record.get("disposition"), provenance.get("disposition"), "selected row disposition differs")
    else:
        require(mode == "whole_frozen_output", "unknown source selection mode")
        # Whole-journal projections need no overlay disposition. The independent
        # Haiku/v6 auditors explicitly label those same unchanged rows as
        # original_complete; legacy certificates omit the optional field.
        # Neither convention can authorize a continuation or adjudicated row.
        equal(provenance.get("disposition"), None, "whole-source projection disposition differs")
        require(record.get("disposition") in (None, "original_complete"),
                "whole-source certificate disposition differs")


def _retained(root, source_map, sources, files, entries, seen):
    panel_binding, manifest_binding = source_map["retained_panel"], source_map["retained_manifest"]
    for binding, name in ((panel_binding, selection.RETAINED + "/release-panel.json"),
                          (manifest_binding, selection.RETAINED + "/release-analysis-inputs.json")):
        equal(binding, {"path": name, "sha256": selection.ANCHORS[name]}, "retained anchor differs")
    panel = _json(sources.read(panel_binding))
    manifest = _json(sources.read(manifest_binding))
    selection.verify_self_hash(panel, "artifact_hash")
    selection.verify_self_hash(manifest, "manifest_hash")
    models = {config["model"] for config in panel["configurations"]}
    require(len(models) == len(panel["configurations"]) == 14 and not models & selection.NEW_MODELS, "retained configuration coverage differs")
    equal(sorted(models), source_map["retained_models"], "retained source-map model join differs")
    equal(sorted(models), sorted(manifest["models"]), "retained manifest model join differs")
    equal(manifest["panel_sha256"], panel_binding["sha256"], "retained panel join differs")
    equal(manifest["total_rows"], RETAINED_ROWS, "retained total differs")
    projected, counts, observed_models = [], Counter(), set()
    for group, originals in manifest["inputs"].items():
        _safe_component(group)
        require(group in entries and isinstance(originals, list), "foreign retained group")
        for entry in originals:
            require(isinstance(entry, dict) and set(entry) <= ENTRY_FIELDS, "unknown retained entry metadata")
            model = entry["model"]
            require(model in models, "foreign retained source model")
            observed_models.add(model)
            raw = sources.read({key: entry[key] for key in ("path", "sha256")})
            literals = raw.splitlines(keepends=True)
            require(len(literals) == _integer(entry["rows"], "retained entry rows") and all(line.strip() for line in literals), "retained journal row count differs")
            field = entry.get("identifier_field", "condition_id")
            row_provenance = []
            for index, line in enumerate(literals):
                row = _json(line)
                identity = _identity(row, field, model, seen)
                if "plan_hash" in entry:
                    equal(row.get("plan_hash"), entry["plan_hash"], "retained row/entry plan hash differs")
                row_provenance.append({"identifier_field": field, "identifier": identity, "source_row_index": index,
                    "literal_line_sha256": sha256(line).hexdigest(), "row_sha256": selection.stable_hash(row)})
            require(("plan_path" in entry) == ("plan_sha256" in entry), "incomplete retained plan binding")
            plan = None
            if "plan_path" in entry:
                plan = {"path": entry["plan_path"], "sha256": entry["plan_sha256"]}
                sources.read(plan)
            slug = _safe_component(model.replace("/", "--"))
            path = f"evidence/retained/{group}/{slug}/{entry['sha256']}.jsonl"
            destination = _put(files, path, raw)
            metadata = {key: deepcopy(entry[key]) for key in ("model", "rows", "identifier_field", "plan_hash") if key in entry}
            entries[group].append({**metadata, **destination})
            counts[group] += len(literals)
            projected.append({"kind": "retained_literal_journal", "model": model, "ledger_group": group,
                "destination": destination, "source_entry": deepcopy(entry), "plan": plan,
                "rows": len(literals), "identifier_field_effective": field, "row_order": "unchanged_source_order",
                "source_row_bytes_modified": False, "selected_rows": row_provenance})
    equal(dict(counts), manifest["expected_rows"], "retained group counts differ")
    require(sum(counts.values()) == RETAINED_ROWS and observed_models == models, "retained rows/models do not close")
    return projected, counts, models


def _candidate_join(source_map, candidates, sources):
    require(isinstance(candidates, dict) and set(candidates) == selection.NEW_MODELS, "all eight verified model candidates are required")
    configs = {config["model"]: config for config in source_map["configurations"]}
    require(len(configs) == len(source_map["configurations"]) == 8 and set(configs) == selection.NEW_MODELS
            and len({c["configuration_key"] for c in configs.values()}) == 8, "configuration joins are incomplete/duplicated")
    indexed = {}
    for model, candidate in candidates.items():
        require(candidate.get("schema") == "panel-refresh-22-verified-model-candidate-v1"
                and candidate.get("model") == model and candidate.get("status") == "model_certificate_verified_for_candidate_selection"
                and candidate.get("release_admitted") is False, "not an independently verified candidate schema")
        equal(candidate.get("conditions"), 1336, "incomplete per-model candidate")
        equal(candidate.get("new_api_calls"), 0, "candidate gate is not offline")
        config = configs[model]
        campaign = "haiku" if model == selection.HAIKU else "deepinfra" if model == selection.DEEPSEEK else "original"
        for key, paths in (("manifest", selection.MANIFEST_PATHS), ("pricing", selection.PRICE_PATHS)):
            name = paths[campaign]
            equal(config[key], {"path": name, "sha256": selection.ANCHORS[name]}, "configuration campaign anchor differs")
        equal(candidate["configuration_sha256"], config["configuration_sha256"], "certificate/configuration digest differs")
        equal(candidate.get("configuration_id"), config.get("configuration_id"), "configuration identity differs")
        equal(candidate["manifest"], config["manifest"], "certificate/source-map manifest differs")
        sources.many(candidate["verified_sources"])
        local_bindings = {b["path"]: b["sha256"] for b in candidate["verified_sources"]}
        for binding in [config["manifest"], config["pricing"], *candidate["reports"]]:
            require(local_bindings.get(binding["path"]) == binding["sha256"], "configuration/certificate is not transitively bound")
        price = _json(sources.read(config["pricing"]))
        prices = [price["configuration"]] if model == selection.DEEPSEEK else price["configurations"]
        selected_config = [item for item in prices if item["model"] == model]
        require(len(selected_config) == 1 and selection.stable_hash(selected_config[0]) == config["configuration_sha256"], "source configuration changed")
        equal(config["route"], selected_config[0]["provider"], "configuration route metadata differs")
        equal(config["raw_route_pricing"], selected_config[0]["selected_route"]["pricing"], "configuration price metadata differs")
        equal(config["display_name"], selected_config[0]["display_name"], "configuration display metadata differs")
        equal(config["configuration_key"], selection.stable_hash({"model": model, "manifest": config["manifest"],
            "configuration": selected_config[0]}), "configuration-key join differs")
        records = candidate["selected_rows"]
        require(isinstance(records, list) and Counter(item["block"] for item in records) == Counter(selection.BLOCKS)
                and len({item["condition_id"] for item in records}) == 1336, "candidate selected row inventory differs")
        blocks = candidate["source_blocks"]
        require(isinstance(blocks, list) and len(blocks) == 9 and {item["block"] for item in blocks} == set(selection.BLOCKS), "candidate block inventory differs")
        indexed[model] = {"config": config, "records": {item["condition_id"]: item for item in records},
            "blocks": {item["block"]: item for item in blocks}}
    return indexed


def _new(root, source_map, candidates, sources, files, entries, seen):
    joined = _candidate_join(source_map, candidates, sources)
    expected = {(model, block) for model in selection.NEW_MODELS for block in selection.BLOCKS}
    actual = [(source["model"], source["block"]) for source in source_map["sources"]]
    require(len(actual) == len(set(actual)) == 72 and set(actual) == expected, "new model/block source map is incomplete or duplicated")
    projected, counts = [], Counter()
    for source in sorted(source_map["sources"], key=lambda item: (item["model"], item["block"])):
        model, block = source["model"], source["block"]
        joined_model, candidate = joined[model], candidates[model]
        config, candidate_block = joined_model["config"], joined_model["blocks"][block]
        equal(source["configuration_key"], config["configuration_key"], "source configuration join differs")
        equal(source["ledger_group"], selection.GROUPS[block], "source ledger group differs")
        equal(source["planned_conditions"], selection.BLOCKS[block], "source block count differs")
        for key, expected_value in (("plan", source["plan"]), ("plan_hash", source["plan_hash"]), ("conditions", source["planned_conditions"])):
            equal(candidate_block[key], expected_value, "candidate/source plan join differs")
        result = rows_adapter.read_selected_block(root, source, verified_sources=candidate["verified_sources"])
        require(len(result.rows) == len(result.literal_lines) == len(result.provenance["selected_rows"])
                == source["planned_conditions"], "literal block projection count differs")
        equal(candidate_block["row_selection"], result.provenance["source_journals"], "candidate/projected source journal join differs")
        plan = _json(sources.read(source["plan"]))
        execution = plan.get("execution", plan.get("configuration", {}).get("execution"))
        equal(selection.stable_hash(execution), source["execution_sha256"], "source execution hash differs")
        require(isinstance(execution, dict) and execution.get("provider_only") == config["route"]["only"]
                and execution.get("provider_allow_fallbacks") is False, "source execution route differs")
        for row, provenance in zip(result.rows, result.provenance["selected_rows"]):
            cid = _identity(row, "condition_id", model, seen)
            require(cid in joined_model["records"], "projected row absent from verified model selection")
            record = joined_model["records"][cid]
            equal(record["block"], block, "certificate selected row block differs")
            for key in ("condition_id", "journal", "row_index", "literal_line_sha256", "row_sha256"):
                equal(record[key], provenance[key], "certificate/projected literal row join differs")
            _join_disposition(source, record, provenance)
        external = None
        if block in {"structural", "toolsandbox"}:
            external = rows_adapter.external_plan_metadata(root, source, manifest_binding=config["manifest"],
                verified_sources=candidate["verified_sources"])
        key = config["configuration_key"]
        require(isinstance(key, str) and re.fullmatch("[0-9a-f]{64}", key), "invalid configuration destination key")
        destination = _put(files, f"evidence/new/{key}/{block}.jsonl", result.jsonl_bytes)
        require(destination["sha256"] == result.provenance["projected_jsonl_sha256"], "projected output hash differs")
        entry = {**destination, "model": model, "block": block, "rows": len(result.rows), "identifier_field": "condition_id",
            "plan_hash": source["plan_hash"], "configuration_key": key}
        entries[source["ledger_group"]].append(entry)
        counts[source["ledger_group"]] += len(result.rows)
        projected.append({"kind": "new_literal_block", "destination": destination,
            "certificate_sha256": selection.stable_hash(candidate), "projection": result.provenance,
            "external_plan_metadata": external})
    require(sum(counts.values()) == NEW_ROWS, "new selected row count differs")
    return projected, counts


def _assemble(root, source_map, candidates):
    """Private projection stage; production callers must use the public gate."""
    selection.verify_self_hash(source_map, "artifact_hash")
    require(source_map.get("schema") == "panel-refresh-22-pre-admission-source-selection-v1"
            and source_map.get("status") == "source_selection_validated_only"
            and source_map.get("release_admitted") is False, "not the anchored pre-admission source map")
    equal(source_map["new_models"], sorted(selection.NEW_MODELS), "new model scope changed")
    equal(source_map["planned_new_conditions"], NEW_ROWS, "new row denominator differs")
    equal(source_map["expected_total_rows"], TOTAL_ROWS, "complete panel denominator differs")
    equal(source_map["expected_coverage_counts"], selection.FINAL_COVERAGE, "coverage expectation differs")
    equal(source_map["expected_episode_counts"], selection.FINAL_EPISODES, "episode expectation differs")
    sources, files, seen = _Sources(root), {}, set()
    # The map's literal root anchors are independently rechecked. No archival
    # manifest/policy content is copied into the public file inventory.
    for name, digest in selection.ANCHORS.items():
        sources.read({"path": name, "sha256": digest})
    entries = {group: [] for group in sorted(source_map["expected_group_rows"])}
    old_provenance, old_counts, retained_models = _retained(root, source_map, sources, files, entries, seen)
    new_provenance, new_counts = _new(root, source_map, candidates, sources, files, entries, seen)
    counts = old_counts + new_counts
    equal(dict(counts), source_map["expected_group_rows"], "complete group denominators differ")
    require(sum(counts.values()) == len(seen) == TOTAL_ROWS, "complete row identity/count closure differs")
    require(not set(files).intersection(sources.bindings), "destination aliases a frozen input path")
    models = retained_models | selection.NEW_MODELS
    require(len(models) == 22, "complete configuration coverage differs")
    manifest = {"schema": "panel-refresh-22-materialization-inputs-v1", "status": "complete_candidate_materialization_not_release",
        "release_admitted": False, "path_base": "materialization_root", "new_api_calls": 0,
        "analysis_policy_status": "requires_fresh_selected_only_metadata_adapter",
        "panel_binding_status": "requires_final_driver_selected_panel_bytes_and_destination",
        "models": sorted(models), "model_count": 22, "total_rows": TOTAL_ROWS,
        "retained_rows": RETAINED_ROWS, "new_rows": NEW_ROWS, "expected_rows": dict(sorted(counts.items())),
        "inputs": entries, "source_row_bytes_modified": False}
    manifest["manifest_hash"] = selection.stable_hash(manifest)
    _put(files, MANIFEST_PATH, selection.canonical(manifest) + b"\n")
    sources.finish()
    provenance = {"schema": "panel-refresh-22-internal-materialization-provenance-v1", "release_admitted": False,
        "visibility": "internal_only_not_public_package_content", "path_base": "materialization_root",
        "source_selection_sha256": source_map["artifact_hash"], "retained_panel": source_map["retained_panel"],
        "retained_manifest": source_map["retained_manifest"], "configurations": deepcopy(source_map["configurations"]),
        "model_certificates": [{"model": model, "candidate_sha256": selection.stable_hash(candidates[model]),
            "reports": deepcopy(candidates[model]["reports"]), "manifest": deepcopy(candidates[model]["manifest"])} for model in sorted(candidates)],
        "retained_projections": old_provenance, "new_projections": new_provenance, "inputs": sources.inventory(),
        "outputs": [{"path": name, "sha256": sha256(raw).hexdigest()} for name, raw in sorted(files.items())],
        "manifest_hash": manifest["manifest_hash"], "new_api_calls": 0,
        "scope": "Complete literal-row projection only; no experiment re-execution, official regrading, policy finalization or publication admission."}
    provenance["artifact_hash"] = selection.stable_hash(provenance)
    return Materialization(MappingProxyType(dict(sorted(files.items()))), json.dumps(manifest, sort_keys=True), json.dumps(provenance, sort_keys=True))


def prepare_materialization(root: Path = ROOT) -> Materialization:
    """Require complete internally verified eight-model evidence before projection.

    No alternate source map, passed flag, certificate path, output root or write
    option is accepted. Any unfinished isolated model remains blocked by the
    certificate adapter; completion of another model cannot bypass that gate.
    """
    root = Path(root).resolve()
    source_map = selection.prepare_source_map(root)
    candidates = {}
    # Fresh gates first avoid needlessly rereading all six completed receipt
    # inventories while a full 22-configuration projection remains impossible.
    order = [selection.HAIKU, selection.DEEPSEEK, *sorted(selection.ORIGINAL_SIX)]
    for model in order:
        try:
            candidates[model] = certificates.verify_model_certificate(root, model)
        except ValueError as exc:
            if model in {selection.HAIKU, selection.DEEPSEEK}:
                raise selection.AdmissionPending("No materialization produced: a complete independently verified " + model + " certificate is required") from exc
            raise
    return _assemble(root, source_map, candidates)
