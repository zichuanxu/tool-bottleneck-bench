#!/usr/bin/env python3
"""Separate deterministic rerun of the same selected-panel analysis algorithms.

This is NOT an independent statistical implementation, a fresh model/tool run,
or publication admission. The driver is used only for write-free, current
eight-model certificate/material/cost revalidation. This module constructs and
executes its own eleven CLI and five Python API calls; it never calls the
driver's run, ordinary_commands, or _api_analyses functions.

Recomputation reads the verified candidate inputs at their original paths.
Only command --output destinations change. Consequently comparison has NO
excluded fields, timestamp tolerances, recursive path rewriting, float rounding,
or ignored hashes. Three historical analyses are byte-preserved separately.
"""
from __future__ import annotations

import argparse
import ast
from copy import deepcopy
from dataclasses import dataclass, replace
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
from types import MappingProxyType

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT, ROOT / "src", ROOT / "experiments"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from experiments import run_panel_refresh_22_analysis as driver
from experiments import analyze_model_system_decisions as decisions
from experiments import analyze_final_panel_cost as total_cost
from experiments import analyze_structural_model_system_decisions as structural_cost
from experiments import panel_refresh_22_external as external

APPROVED_DRIVER_SHA256 = "8b7c770d29fbc1b9c5b74b7e5c43201b60dd0d51f448af197399e0eab0659cc7"
DRIVER_PATH = "experiments/run_panel_refresh_22_analysis.py"
VERIFIER_PATH = "experiments/verify_panel_refresh_22_analysis.py"
TEST_PATH = "tests/benchmark/test_verify_panel_refresh_22_analysis.py"
MANIFEST = "release-analysis-inputs.json"
PANEL = "release-panel.json"
GENERATION = "analysis-generation.json"
PRIVATE = "internal/driver-provenance.json"
PLAN = "verification-plan.json"
REPORT = "report.json"
FAILURE = "failure.json"
PRESERVED_NAMES = ("toolsandbox-external-validation.json", "structural-repair-holdout.json", "historical-toolsandbox-menu.json")
CLI_OUTPUTS = (
    ("analyze_unified_core_panel.py", "release-core-panel.json"),
    ("analyze_release_layer_repairs.py", "release-layer-repairs.json"),
    ("analyze_prospective_sentinel.py", "release-sentinel.json"),
    ("analyze_workflow_frontier_panel.py", "release-workflow-frontier.json"),
    ("analyze_failure_localization.py", "release-failure-localization.json"),
    ("analyze_failure_localization_v2.py", "release-failure-localization-v2.json"),
    ("analyze_crossover_campaign.py", "crossover-campaign.json"),
    ("analyze_revision_audit.py", "revision-evidence-audit.json"),
    ("analyze_interaction_scale_sensitivity.py", "interaction-scale-sensitivity.json"),
    ("analyze_failure_mechanisms.py", "failure-mechanism-decomposition.json"),
    ("analyze_capacity_sensitivity.py", "capacity-cluster-sensitivity.json"),
)
API_SOURCES = {
    "release-model-versus-system.json": "analyze_model_system_decisions.py",
    "release-panel-cost.json": "analyze_final_panel_cost.py",
    "structural-model-versus-system.json": "analyze_structural_model_system_decisions.py",
    "structural-repair-pooled.json": "panel_refresh_22_external.py",
    "candidate-toolsandbox-menu.json": "panel_refresh_22_external.py",
}
OUTPUT_NAMES = tuple(name for _, name in CLI_OUTPUTS) + tuple(API_SOURCES)
CLI_PARAMETERS = {
    "analyze_unified_core_panel.py": {"--bootstrap-samples": 10_000, "--seed": 20260805},
    "analyze_release_layer_repairs.py": {"--bootstrap-samples": 10_000, "--randomization-samples": 10_000, "--seed": 20260813},
    "analyze_prospective_sentinel.py": {"--bootstrap-samples": 10_000, "--seed": 20260813},
    "analyze_workflow_frontier_panel.py": {"--bootstrap-draws": 10_000},
    "analyze_failure_localization.py": {"--bootstrap-samples": 10_000, "--seed": 20260901},
    "analyze_failure_localization_v2.py": {"--bootstrap-samples": 10_000, "--seed": 20260904},
    "analyze_crossover_campaign.py": {"--bootstrap-samples": 10_000, "--seed": 20260904},
    "analyze_revision_audit.py": {"--samples": 10_000, "--seed": 20260905},
    "analyze_interaction_scale_sensitivity.py": {},
    "analyze_failure_mechanisms.py": {"--bootstrap-samples": 10_000, "--seed": 20260902},
    "analyze_capacity_sensitivity.py": {"--bootstrap-samples": 10_000, "--seed": 20260902},
}
API_PARAMETERS = {
    "decision_cost_bootstrap": {"samples": 10_000, "seed": 20260816},
    "structural_bootstrap": {"bootstrap_samples": 20_000, "randomization_samples": 100_000, "seed": 20260814},
    "concurrent_menu": {"bootstrap_samples": 10_000, "sign_flip_samples": 100_000, "analysis_seed": 20260815},
}
GENERATION_FIELDS = frozenset(("schema", "status", "release_admitted", "visibility", "new_api_calls", "rows",
    "configurations", "source_selection_sha256", "manifest_hash", "panel_sha256", "generated_analyses",
    "preserved_artifacts", "inputs", "commands", "verification_scope", "required_next_gate", "artifact_hash"))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value):
    """Typed JSON: int/float/bool/null and nested keys are never conflated."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def same(left, right, message):
    require(canonical(left) == canonical(right), message)


def _json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value, "duplicate JSON key")
            value[key] = item
        return value
    def nonfinite(value):
        raise ValueError("non-finite JSON value")
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite)
    require(isinstance(value, dict), "JSON object required")
    canonical(value)  # Also reject overflowing numeric literals parsed as inf.
    return value


def _hash(value, field="artifact_hash"):
    body = deepcopy(value)
    body.pop(field, None)
    body[field] = sha256(canonical(body)).hexdigest()
    return body


def _verify_hash(value, field="artifact_hash"):
    require(isinstance(value.get(field), str) and value[field] == _hash(value, field)[field],
            "invalid self hash: " + field)


def _no_symlinks(root, path):
    require(path.is_relative_to(root), "path leaves repository")
    for part in [path, *path.parents]:
        if part == root:
            break
        require(not part.is_symlink(), "symlink is not an accepted verification path")


def _directory(root, value, *, fresh):
    value = Path(value)
    require(".." not in value.parts, "parent traversal is forbidden")
    path = value if value.is_absolute() else root / value
    _no_symlinks(root, path)
    path = path.resolve()
    require(path.is_relative_to(root / "results") and path != root / "results", "directory must be below repository results")
    if fresh:
        require(not path.exists() and path.parent.is_dir(), "verification output must be fresh with an existing parent")
    else:
        require(path.is_dir(), "candidate directory does not exist")
    return path


def _snapshot(root, directory):
    """Exact regular-file inventory; extra files, symlinks and FIFOs fail closed."""
    result = {}
    for path in sorted(directory.rglob("*")):
        _no_symlinks(root, path)
        mode = path.stat().st_mode
        if stat.S_ISDIR(mode):
            continue
        require(stat.S_ISREG(mode), "non-regular candidate/output entry")
        result[path.relative_to(directory).as_posix()] = sha256(path.read_bytes()).hexdigest()
    return result


def _bound(root, name):
    require(isinstance(name, str) and str(PurePosixPath(name)) == name
            and not PurePosixPath(name).is_absolute() and ".." not in PurePosixPath(name).parts,
            "noncanonical repository input path")
    path = root / name
    _no_symlinks(root, path)
    require(path.is_file(), "missing bound verification input")
    return {"path": name, "sha256": sha256(path.read_bytes()).hexdigest()}


def _write(path, raw):
    # Every caller uses fixed output basenames, never a candidate-supplied path.
    with path.open("xb") as stream:
        stream.write(raw)


def _assert_parameters(root):
    """The original driver omits these CLI defaults; verify then pass explicitly."""
    for script, expected in CLI_PARAMETERS.items():
        tree = ast.parse((root / "experiments" / script).read_text())
        found = {}
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "add_argument" and node.args
                    and isinstance(node.args[0], ast.Constant) and node.args[0].value in expected):
                continue
            flag = node.args[0].value
            require(flag not in found, "ambiguous CLI default")
            defaults = [kw.value for kw in node.keywords if kw.arg == "default"]
            require(len(defaults) == 1, "missing explicit statistical default")
            found[flag] = ast.literal_eval(defaults[0])
        same(found, expected, "statistical CLI defaults changed: " + script)
    menu = external.concurrent.menu
    same({"bootstrap_samples": menu.BOOTSTRAP_SAMPLES, "sign_flip_samples": menu.SIGN_FLIP_SAMPLES,
          "analysis_seed": menu.ANALYSIS_SEED}, API_PARAMETERS["concurrent_menu"], "menu resampling parameters changed")


def _repeat(flag, values):
    return [item for value in values for item in (flag, str(value))]


def _commands(root, candidate, manifest, panel, destination, *, explicit_parameters):
    """Independently specified consumer calls. Only --output points elsewhere."""
    groups = {g: [e["path"] for e in entries] for g, entries in manifest["inputs"].items()}
    model = {e["path"]: e["model"] for es in manifest["inputs"].values() for e in es}
    def pooled(name):
        return [p for prefix in ("", "candidate_", "frontier_") for p in groups[prefix + name]]
    cap, disc, repair = pooled("capacity"), pooled("discovery"), pooled("repair")
    activation = set(panel["activation_models"])
    infra = manifest["infrastructure_condition_ids"]
    pa, ma = ["--panel", str(candidate / PANEL)], ["--manifest", str(candidate / MANIFEST)]
    arguments = [
        [*_repeat("--capacity-input", cap), *_repeat("--discovery-input", disc), *pa,
         *_repeat("--expected-capacity-infrastructure-id", infra["capacity"]), *_repeat("--expected-discovery-infrastructure-id", infra["discovery"])],
        [*_repeat("--prospective-capacity", [p for p in groups["capacity"] if model[p] in activation]),
         *_repeat("--prospective-discovery", [p for p in groups["discovery"] if model[p] in activation]),
         *_repeat("--retained-capacity", [p for p in cap if model[p] not in activation]),
         *_repeat("--retained-discovery", [p for p in disc if model[p] not in activation]),
         *_repeat("--repair", repair), *pa,
         *_repeat("--expected-prospective-capacity-infrastructure-id", infra["confirmatory_layer_capacity"]),
         *_repeat("--expected-prospective-discovery-infrastructure-id", infra["confirmatory_layer_discovery"]),
         *_repeat("--expected-retained-capacity-infrastructure-id", infra["retained_layer_capacity"]),
         *_repeat("--expected-retained-discovery-infrastructure-id", infra["retained_layer_discovery"]),
         *_repeat("--expected-repair-infrastructure-id", infra["repair"])],
        [*_repeat("--input", pooled("sentinel")), *pa],
        [*_repeat("--input", pooled("workflow_frontier")), *pa],
        ma, ma, pa, [*ma, *pa],
        [*_repeat("--capacity-input", cap), *pa, "--protocol", str(root / "protocols/interaction-scale-sensitivity-2026-09-01.md")],
        [*ma, *pa, "--protocol", str(root / "protocols/failure-mechanism-decomposition-2026-09-02.md")],
        [*ma, *pa, "--external", str(candidate / "toolsandbox-external-validation.json"),
         "--protocol", str(root / "protocols/capacity-cluster-sensitivity-2026-09-02.md")],
    ]
    require(len(arguments) == len(CLI_OUTPUTS) == 11, "ordinary consumer coverage changed")
    result = []
    for (script, filename), args in zip(CLI_OUTPUTS, arguments):
        parameters = [part for flag, value in CLI_PARAMETERS[script].items() for part in (flag, str(value))] if explicit_parameters else []
        result.append([sys.executable, str(root / "experiments" / script), *args, *parameters, "--output", str(destination / filename)])
    return result


def _candidate_material(prepared, candidate):
    """Finite constructor mapping, not normalization of candidate artifacts.

Only manifest.inputs[*].path and manifest.panel_path are constructed for the
candidate directory, with a newly computed EXPECTED manifest hash. No bytes,
hash or nested field in the actual candidate object is ever modified.
"""
    origin = prepared.output_root.relative_to(prepared.root).as_posix()
    target = candidate.relative_to(prepared.root).as_posix()
    manifest = deepcopy(prepared.manifest)
    for entries in manifest["inputs"].values():
        for entry in entries:
            require(isinstance(entry["path"], str), "manifest input path must be text")
            path = PurePosixPath(entry["path"])
            require(path.is_relative_to(PurePosixPath(origin) / "evidence"), "unexpected prepared evidence path")
            suffix = path.relative_to(origin)
            entry["path"] = (PurePosixPath(target) / suffix).as_posix()
    same(manifest["panel_path"], origin + "/" + PANEL, "unexpected prepared panel locator")
    manifest["panel_path"] = target + "/" + PANEL
    manifest = _hash(manifest, "manifest_hash")
    files = dict(prepared.files)
    files[MANIFEST] = canonical(manifest) + b"\n"
    return replace(prepared, output_root=candidate, manifest=manifest, files=MappingProxyType(files))


def _generation_header(report, rows=27912, configurations=22):
    require(set(report) == GENERATION_FIELDS, "unknown or missing generation report fields")
    _verify_hash(report)
    same({k: report[k] for k in ("schema", "status", "release_admitted", "visibility", "new_api_calls", "rows", "configurations")},
         {"schema": "panel-refresh-22-analysis-generation-v1", "status": "analysis_generated_pending_independent_verification",
          "release_admitted": False, "visibility": "internal_generation_record_not_public_package_content",
          "new_api_calls": 0, "rows": rows, "configurations": configurations}, "candidate generation scope/status differs")
    same(report["verification_scope"], "single offline generation only; no second computation, fresh tool transitions, official regrading or publication admission",
         "candidate generation verification claim differs")
    same(report["required_next_gate"], "independent typed whole-panel recomputation and artifact verification before paper integration",
         "candidate generation next gate differs")


def _validate_candidate(prepared, candidate, snapshot, generation):
    p = _candidate_material(prepared, candidate)
    expected = set(p.files) | {PRIVATE, GENERATION, *OUTPUT_NAMES}
    require(set(snapshot) == expected, "candidate has extra or missing files")
    for name, expected_bytes in p.files.items():
        require((candidate / name).read_bytes() == expected_bytes, "candidate does not match current verified material: " + name)
    same(_json((candidate / PRIVATE).read_bytes()), p.private_provenance, "candidate private source/certificate/cost provenance differs")
    expected_private = canonical(p.private_provenance) + b"\n"
    require((candidate / PRIVATE).read_bytes() == expected_private, "candidate private provenance serialization differs")
    for field, expected_value in (("source_selection_sha256", p.source_selection_sha256),
                                  ("manifest_hash", p.manifest["manifest_hash"]), ("panel_sha256", p.manifest["panel_sha256"]),
                                  ("preserved_artifacts", p.preserved), ("inputs", p.inputs.records())):
        same(generation[field], expected_value, "generation binding differs: " + field)
    inputs = generation["inputs"]
    require(len({record["path"] for record in inputs}) == len(inputs), "duplicate generation input")
    required_code = {DRIVER_PATH, VERIFIER_PATH, TEST_PATH,
        *("experiments/" + script for script, _ in CLI_OUTPUTS),
        *("experiments/" + script for script in API_SOURCES.values())}
    require(required_code <= {record["path"] for record in inputs}, "generator must bind verifier/tests and all consumers before candidate creation")
    candidate_relative = candidate.relative_to(p.root).as_posix()
    require(all(not PurePosixPath(r["path"]).is_relative_to(candidate_relative) for r in inputs), "generation input cannot self-bind its candidate outputs")
    expected_commands = _commands(p.root, candidate, p.manifest, p.panel, candidate, explicit_parameters=False)
    same(generation["commands"], expected_commands, "candidate analysis commands/parameters differ")
    expected_outputs = [_bound(p.root, candidate_relative + "/" + name) for name in OUTPUT_NAMES]
    same(generation["generated_analyses"], expected_outputs, "candidate analysis output coverage/hash differs")
    for name in OUTPUT_NAMES:
        payload = _json((candidate / name).read_bytes())
        _verify_hash(payload)
        same({k: payload[k] for k in ("release_admitted", "confirmatory_weight", "analysis_status", "source_selection_sha256")},
             {"release_admitted": False, "confirmatory_weight": 0,
              "analysis_status": "generated_pending_independent_whole_panel_verification", "source_selection_sha256": p.source_selection_sha256},
             "candidate analysis status differs: " + name)
        fields = ("panel_sha256", "manifest_hash") if name in API_SOURCES else ("selected_panel_sha256", "analysis_manifest_hash")
        same([payload[f] for f in fields], [p.manifest["panel_sha256"], p.manifest["manifest_hash"]], "candidate analysis panel binding differs")
    return p


@dataclass(frozen=True)
class VerificationPlan:
    prepared: object
    candidate: Path
    destination: Path
    snapshot: dict
    generation: dict
    commands: tuple


def prepare_verification(root: Path, candidate_root: Path, output_root: Path,
                         exclude_models=(), reporting_date=None) -> VerificationPlan:
    """No writes, mkdir, caller-provided certificates, success flags or row costs.

    ``exclude_models``/``reporting_date`` must repeat the driver's reporting-subset arguments
    (default none: the full 22-configuration panel).
    """
    root = Path(root).resolve()
    require(root == ROOT.resolve(), "use the repository containing this verifier")
    candidate = _directory(root, candidate_root, fresh=False)
    destination = _directory(root, output_root, fresh=True)
    require(not destination.is_relative_to(candidate) and not candidate.is_relative_to(destination), "candidate and verification directories must be disjoint")
    before = _snapshot(root, candidate)
    require(GENERATION in before, "candidate has no completed generation record")
    generation = _json((candidate / GENERATION).read_bytes())
    if not exclude_models:
        _generation_header(generation)
    require(_bound(root, DRIVER_PATH)["sha256"] == APPROVED_DRIVER_SHA256, "unreviewed analysis driver version")
    # Shared admission/materialization infrastructure, not shared orchestration:
    # this invokes complete live-local certificate and RowCostLookup validation.
    prepared = driver.prepare_analysis(root, destination, exclude_models, reporting_date)
    if exclude_models:  # subset denominators are known only after the shared projection
        _generation_header(generation, prepared.manifest["total_rows"], len(prepared.panel["configurations"]))
    _assert_parameters(root)
    p = _validate_candidate(prepared, candidate, before, generation)
    _verify_stability(p, candidate, before)
    _directory(root, destination, fresh=True)
    commands = _commands(root, candidate, p.manifest, p.panel, destination, explicit_parameters=True)
    return VerificationPlan(p, candidate, destination, before, generation, tuple(commands))


def _verify_stability(p, candidate, initial):
    p.inputs.verify()
    same(driver._code_paths(p.root), list(p.code_paths), "source-code inventory changed")
    same(driver._crossover_paths(p.root, set(p.manifest["models"])), list(p.crossover_paths), "historical crossover source inventory changed")
    same(_snapshot(p.root, candidate), initial, "candidate input/output bytes or inventory changed")


def _prices(panel, snapshots, cost_models):
    """Independently reconstruct the historical five-snapshot precedence."""
    frozen = {}
    for snapshot in snapshots[2:]:
        for model, value in snapshot["models"].items():
            quote = (float(value["input"]) / 1_000_000, float(value["output"]) / 1_000_000)
            require(model not in frozen or frozen[model] == quote, "conflicting historical scientific prices")
            frozen[model] = quote
    models = {c["model"] for c in panel["configurations"]} - set(cost_models)
    historical = {**panel, "configurations": [c for c in panel["configurations"] if c["model"] in models - set(frozen)]}
    prices = decisions._prices(historical, snapshots[0], snapshots[1])
    prices.update({model: quote for model, quote in frozen.items() if model in models})
    require(set(prices) == models, "retained scientific prices do not cover exactly fourteen configurations")
    return prices


def _api_recompute(p):
    """Direct API calls with fixed parameters, not driver's analysis wrapper."""
    def rows(name, count):
        paths = [p.root / item["path"] for group in (name, "candidate_" + name, "frontier_" + name)
                 for item in p.manifest["inputs"][group]]
        return decisions._rows(paths, expected_rows=count,
            allowed_infrastructure=frozenset(p.manifest["infrastructure_condition_ids"][name]))
    n_core, n_auxiliary = len(p.panel["configurations"]), len(p.panel["auxiliary_models"])  # 22 and 21 on the full panel
    core = decisions.analyze(capacity=rows("capacity", n_core * 576), discovery=rows("discovery", n_core * 216),
        repair=rows("repair", n_auxiliary * 144),
        panel=p.panel, openrouter_prices=p.prices[0], google_prices=p.prices[1], candidate_prices=list(p.prices[2:]),
        row_costs=p.row_costs, samples=10_000, seed=20260816)
    prices = _prices(p.panel, p.prices, p.row_costs.models)
    cost = total_cost.analyze(p.manifest, prices, row_costs=p.row_costs)
    structural_rows = [row for group in ("structural_holdout", "candidate_structural_additional", "frontier_structural_additional") for row in p.rows[group]]
    models = set(p.panel["structural_registered_models"]) | set(p.panel["structural_additional_models"])
    counts = p.panel["coverage_counts"]
    require(len(models) == counts["structural_registered"] + counts["structural_additional_descriptive"], "pooled structural scope differs")
    structural = external.analyze_structural(structural_rows, models, plan_provenance=p.external_provenance["structural"],
        bootstrap_samples=20_000, randomization_samples=100_000, seed=20260814)
    structural_decisions = structural_cost.analyze(structural["plan_hashes"], structural_rows, prices, frozenset(models),
        row_costs=p.row_costs, samples=10_000, seed=20260816)
    menu_models = set(p.panel["concurrent_menu_models"])
    require(len(menu_models) == counts["concurrent_menu_descriptive"], "concurrent menu scope differs")
    menu = external.analyze_toolsandbox([row for g in ("candidate_menu_intervention", "frontier_menu_intervention") for row in p.rows[g]],
        menu_models, plan_provenance=p.external_provenance["toolsandbox"], row_provenance=p.external_provenance["duration"])
    return {"release-model-versus-system.json": core, "release-panel-cost.json": cost,
        "structural-model-versus-system.json": structural_decisions, "structural-repair-pooled.json": structural,
        "candidate-toolsandbox-menu.json": menu}


def _annotate(value, name, p):
    """Reconstruct the generator's envelope without changing scientific fields."""
    result = deepcopy(value)
    result.update(release_admitted=False, analysis_status="generated_pending_independent_whole_panel_verification",
        confirmatory_weight=0, source_selection_sha256=p.source_selection_sha256)
    if name in API_SOURCES:
        relative = p.output_root.relative_to(p.root).as_posix()
        result.update(panel_sha256=p.manifest["panel_sha256"], manifest_hash=p.manifest["manifest_hash"],
            inputs=[{"path": p.manifest["panel_path"], "sha256": p.manifest["panel_sha256"]},
                    _bound(p.root, relative + "/" + MANIFEST),
                    *[{"path": path, "sha256": digest} for path, digest in driver.PRICE_BINDINGS],
                    _bound(p.root, "experiments/model-versus-system-decision-protocol.md"), _bound(p.root, relative + "/" + PRIVATE)],
            analysis_source_sha256=_bound(p.root, "experiments/" + API_SOURCES[name])["sha256"])
    else:
        result.update(selected_panel_sha256=p.manifest["panel_sha256"], analysis_manifest_hash=p.manifest["manifest_hash"])
    return _hash(result)


def compare_artifact(candidate, recomputed):
    """All fields participate, including nested timestamps/paths/self hashes."""
    _verify_hash(candidate)
    _verify_hash(recomputed)
    same(candidate, recomputed, "typed deterministic analysis mismatch")


def verify(root: Path, candidate_root: Path, output_root: Path, exclude_models=(), reporting_date=None) -> dict:
    plan = prepare_verification(root, candidate_root, output_root, exclude_models, reporting_date)
    p, out = plan.prepared, plan.destination
    _verify_stability(p, plan.candidate, plan.snapshot)
    out.mkdir(exist_ok=False)
    stage = "write verification plan"
    try:
        description = _hash({"schema": "panel-refresh-22-deterministic-verification-plan-v1", "release_admitted": False,
            "candidate_generation": _bound(p.root, (plan.candidate / GENERATION).relative_to(p.root).as_posix()),
            "commands": list(plan.commands), "api_parameters": API_PARAMETERS,
            "comparison": "full typed canonical JSON including self hashes; no ignored or relocated result fields",
            "output_relocation": [{"argument": "--output", "candidate": str(plan.candidate / name),
                "recomputed": str(out / name)} for _, name in CLI_OUTPUTS],
            "scope": "separate deterministic rerun of the same algorithms, not an independent statistical implementation"})
        _write(out / PLAN, canonical(description) + b"\n")
        for name in PRESERVED_NAMES:
            _write(out / name, (plan.candidate / name).read_bytes())
        env = {**os.environ, "PYTHONPATH": str(p.root / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
        for command in plan.commands:
            stage = "CLI " + Path(command[1]).name
            subprocess.run(command, cwd=p.root, env=env, check=True)
            path = Path(command[-1])
            value = _annotate(_json(path.read_bytes()), path.name, p)
            # The CLI created this verifier-owned file. No candidate is edited.
            path.write_bytes(canonical(value) + b"\n")
        stage = "five direct analysis APIs"
        results = _api_recompute(p)
        require(set(results) == set(API_SOURCES), "API recomputation output inventory differs")
        for name, value in results.items():
            _write(out / name, canonical(_annotate(value, name, p)) + b"\n")
        stage = "strict comparisons and final hash closure"
        require(set(_snapshot(p.root, out)) == {PLAN, *PRESERVED_NAMES, *OUTPUT_NAMES}, "verification output inventory differs")
        checks = []
        for name in OUTPUT_NAMES:
            candidate_raw, recomputed_raw = (plan.candidate / name).read_bytes(), (out / name).read_bytes()
            compare_artifact(_json(candidate_raw), _json(recomputed_raw))
            checks.append({"name": name, "operation": "separate_same_algorithm_deterministic_recomputation", "passed": True,
                "candidate": {"path": (plan.candidate / name).relative_to(p.root).as_posix(), "sha256": sha256(candidate_raw).hexdigest()},
                "recomputed": {"path": (out / name).relative_to(p.root).as_posix(), "sha256": sha256(recomputed_raw).hexdigest()},
                "comparison": "all_fields_typed_canonical_json", "ignored_fields": []})
        preserved = []
        for name in PRESERVED_NAMES:
            source_raw, copy_raw = (plan.candidate / name).read_bytes(), (out / name).read_bytes()
            require(copy_raw == source_raw, "preserved artifact bytes differ")
            preserved.append({"name": name, "passed": True, "operation": "byte_preserved_not_recomputed",
                "original_panel_binding_unchanged": True, "new_22_panel_binding": False,
                "source": {"path": (plan.candidate / name).relative_to(p.root).as_posix(), "sha256": sha256(source_raw).hexdigest()},
                "copy": {"path": (out / name).relative_to(p.root).as_posix(), "sha256": sha256(copy_raw).hexdigest()}})
        _verify_stability(p, plan.candidate, plan.snapshot)
        before_report = _snapshot(p.root, out)
        compared_inventory = {c["name"]: c["recomputed"]["sha256"] for c in checks}
        compared_inventory.update({c["name"]: c["copy"]["sha256"] for c in preserved})
        compared_inventory[PLAN] = sha256(canonical(description) + b"\n").hexdigest()
        same(before_report, compared_inventory, "recomputed output changed since comparison")
        certificates = p.private_provenance["materialization"]["model_certificates"]
        costs = p.row_costs.artifacts
        require(len(checks) == len({c["name"] for c in checks}) == 16
                and len(certificates) == len(costs) == 8, "verification denominator differs")
        report = _hash({"schema": "panel-refresh-22-deterministic-analysis-verification-v1",
            "status": "deterministic_analysis_recomputation_matched", "passed": True, "release_admitted": False,
            "visibility": "internal_verification_record_not_public_package_content", "new_api_calls": 0,
            "rows": p.manifest["total_rows"], "configurations": len(p.panel["configurations"]),
            "checks": checks, "preserved_artifact_checks": preserved,
            "prior_model_certificate_revalidation": deepcopy(certificates),
            "scientific_row_cost_revalidation": [{"model": a["model"], "artifact_hash": a["artifact_hash"],
                "model_certificate_sha256": a["model_certificate_sha256"]} for a in costs],
            "source_selection_sha256": p.source_selection_sha256,
            "candidate_generation": _bound(p.root, (plan.candidate / GENERATION).relative_to(p.root).as_posix()),
            "panel_sha256": p.manifest["panel_sha256"], "manifest_hash": p.manifest["manifest_hash"],
            "inputs": p.inputs.records(), "candidate_inventory": plan.snapshot,
            "recomputed_inventory_before_report": before_report, "input_hashes_stable_before_after": True,
            "ignored_result_fields": [], "verification_plan": _bound(p.root, (out / PLAN).relative_to(p.root).as_posix()),
            "verification_scope": "separate deterministic rerun of the same algorithms, not an independent statistical implementation, publication permission, new experiment, live API replay or tool-environment transition execution",
            "model_replay_scope": "existing fixed-response/saved-state certificates revalidated, not rerun; the Gemini adjudicated zero retains its narrower certificate scope",
            "remaining_gate": "scientific claim review and explicit paper integration; publication admission is separate"})
        if exclude_models:
            report = _hash({**report, "reporting_subset": {"excluded_models": sorted(set(exclude_models)),
                "reporting_date": reporting_date, "reporting_scope": p.panel["reporting_scope"]}})
        _write(out / REPORT, canonical(report) + b"\n")
        return report
    except Exception as error:
        # Keep partial recomputation as diagnostic evidence, never clean up or
        # write a success-shaped report after any failed precondition/comparison.
        _write(out / FAILURE, canonical(_hash({"schema": "panel-refresh-22-deterministic-verification-failure-v1",
            "status": "failed", "passed": False, "release_admitted": False, "stage": stage,
            "error_type": type(error).__name__, "message": str(error), "new_api_calls": 0})) + b"\n")
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--exclude-model", action="append", default=[],
                        help="repeat the driver's --exclude-model arguments (default: none)")
    parser.add_argument("--reporting-date", help="repeat the driver's --reporting-date")
    args = parser.parse_args(argv)
    report = verify(ROOT, args.candidate_root, args.output_root, args.exclude_model, args.reporting_date)
    print(json.dumps({"passed": report["passed"], "checks": len(report["checks"]),
        "release_admitted": False, "scope": "same-algorithm deterministic analysis only"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
