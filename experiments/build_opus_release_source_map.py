#!/usr/bin/env python3
"""Admit only the complete frozen Opus inventory; replay without private receipts."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from tempfile import NamedTemporaryFile

from bottleneckbench.analysis import is_infrastructure_failure
from bottleneckbench.core.schema import canonical_json, stable_hash

ROOT = Path(__file__).resolve().parents[1]
MODEL = "anthropic/claude-opus-5"
SCHEMA = "bottleneckbench-opus-release-source-map-v1"
FROZEN_MANIFEST_HASH = "b9c9b4a1579394bb6601dc9b6549c682021311e49cc3e4e29dcfbe42adf2c15a"
FROZEN_AUTHORIZATION_HASH = "9f6723915ba327b874b5eed3818a168a42014ec89788322aa1887124005dfb70"
ACQUISITION = Path("results/opus-5-acquisition")
DEFAULT_OUTPUT = ROOT / ACQUISITION / "release-source-map.json"
PRICING = Path("configs/opus-5-active-route-pricing-2026-09-03.json")
BLOCKS = {
    "primary_capacity": (432, "core", "frontier_capacity"),
    "primary_discovery": (216, "core", "frontier_discovery"),
    "robustness_capacity": (144, "core", "frontier_capacity"),
    "verification_repair": (72, "repair", "frontier_repair"),
    "interface_guide": (72, "repair", "frontier_repair"),
    "sentinel": (72, "sentinel", "frontier_sentinel"),
    "workflow_frontier": (16, "frontier", "frontier_workflow_frontier"),
    "structural": (216, "structural", "frontier_structural_additional"),
    "toolsandbox": (96, "toolsandbox", "frontier_menu_intervention"),
}


def _read(path):
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("expected an artifact object")
    return value


def _sha(path):
    return sha256(path.read_bytes()).hexdigest()


def _resolve(root, path):
    relative = Path(path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("nonportable or escaping source path")
    resolved = (root / relative).resolve()
    resolved.relative_to(root.resolve())
    return resolved


def _item(root, path):
    return {"path": str(path.resolve().relative_to(root.resolve())), "sha256": _sha(path)}


def _hashed(record, field):
    body = dict(record)
    if body.pop(field, None) != stable_hash(body):
        raise ValueError("artifact identity hash mismatch")


def _bound(root, item):
    path = _resolve(root, item["path"])
    if _sha(path) != item["sha256"]:
        raise ValueError(f"bound artifact changed: {item['path']}")
    return path


def _selected_output(root, entry):
    if entry["block"] == "primary_capacity":
        return root / ACQUISITION / "execution/credit-recovery/core/claude-opus-5-primary-capacity.jsonl"
    if entry["block"] == "primary_discovery":
        return root / ACQUISITION / "execution/provider-recovery-12/core/claude-opus-5-primary-discovery.jsonl"
    return _resolve(root, entry["output"])


def _admission(root, manifest, certificate):
    _hashed(manifest, "manifest_hash")
    _hashed(certificate, "artifact_hash")
    if (manifest.get("manifest_hash") != FROZEN_MANIFEST_HASH
            or manifest.get("model") != MODEL or manifest.get("planned_conditions") != 1336
            or certificate.get("status") != "complete" or certificate.get("rows") != 1336
            or certificate.get("eligible_for_unified_first_class_panel") is not True
            or certificate.get("condition_ids_exact_once") is not True
            or certificate.get("provider_failures") != 0 or certificate.get("transport_failures") != 0
            or certificate.get("manifest_hash") != manifest["manifest_hash"]
            or not certificate.get("completion_continuation_amendment")):
        raise ValueError("Opus complete-inventory admission gate is closed")
    amendment_path = root / ACQUISITION / "authorization.json"
    amendment = _read(amendment_path)
    _hashed(amendment, "artifact_hash")
    if (amendment.get("schema") != "bottleneckbench-opus-public-authorization-v1"
            or amendment["original_authorization_artifact_hash"] != FROZEN_AUTHORIZATION_HASH
            or amendment["original_authorization_artifact_hash"] != certificate["completion_continuation_amendment"]
            or amendment["base_manifest_hash"] != manifest["manifest_hash"]
            or certificate.get("hard_cumulative_cap_usd") != amendment["hard_cumulative_cap_usd"]):
        raise ValueError("completion certificate lacks its authorized cap binding")
    _bound(root, amendment["protocol_artifact"])
    return amendment_path


def prepare_admission(root=ROOT):
    """Publish only completion gates and selected-row bindings, not private ledgers."""
    root = root.resolve()
    private_path = root / ACQUISITION / "execution/completion.json"
    certificate = _read(private_path)
    manifest = _read(root / ACQUISITION / "plans/manifest.json")
    original_path = root / "results/prospective-plans/opus-5-completion-continuation-12/amendment.json"
    original = _read(original_path)
    _hashed(original, "artifact_hash")
    if original["artifact_hash"] != FROZEN_AUTHORIZATION_HASH:
        raise ValueError("original authorized completion amendment changed")
    protocol = original_path.with_name("protocol.md")
    authorization = {"schema": "bottleneckbench-opus-public-authorization-v1",
        "original_authorization_artifact_hash": original["artifact_hash"],
        "original_authorization_sha256_digest": _sha(original_path),
        "base_manifest_hash": manifest["manifest_hash"],
        "hard_cumulative_cap_usd": original["hard_cumulative_cap_usd"],
        "protocol_artifact": _item(root, protocol),
        "publication_boundary": "Original frozen authorization and account-funding snapshot retained privately; this public projection publishes only the cap, protocol and hash bindings."}
    authorization["artifact_hash"] = stable_hash(authorization)
    authorization_path = root / ACQUISITION / "authorization.json"
    if authorization_path.exists() and _read(authorization_path) != authorization:
        raise ValueError("existing public authorization differs from frozen original")
    if not authorization_path.exists():
        authorization_path.write_text(canonical_json(authorization) + "\n", encoding="utf-8")
    _admission(root, manifest, certificate)
    fields = ("status", "rows", "eligible_for_unified_first_class_panel", "condition_ids_exact_once",
        "provider_failures", "transport_failures", "manifest_hash", "outputs",
        "completion_continuation_amendment", "hard_cumulative_cap_usd")
    record = {"schema": "bottleneckbench-opus-public-admission-v1",
        **{key: certificate[key] for key in fields},
        "private_completion_sha256_digest": _sha(private_path),
        "raw_receipt_closure": "verified in the private complete-inventory check; not rerun by public offline replay"}
    record["artifact_hash"] = stable_hash(record)
    path = root / ACQUISITION / "admission.json"
    if path.exists():
        if _read(path) != record:
            raise ValueError("existing public admission differs from private completion")
    else:
        path.write_text(canonical_json(record) + "\n", encoding="utf-8")
    return path


def validate_source_map(path=DEFAULT_OUTPUT, root=ROOT):
    """Validate public plans, selected rows and admission certificate, without APIs."""
    from build_release_analysis_manifest import validate_plan_sources

    root = root.resolve()
    record = _read(path)
    _hashed(record, "artifact_hash")
    if (record.get("schema") != SCHEMA or record.get("models") != [MODEL]
            or record.get("rows") != 1336 or record.get("scientific_panel_role") != "first_class"
            or len(record.get("sources", [])) != 9):
        raise ValueError("Opus source-map inventory identity changed")
    generator = _bound(root, record["generator"])
    if generator != _resolve(root, "experiments/build_opus_release_source_map.py"):
        raise ValueError("unexpected Opus source-map generator")
    manifest_path = _bound(root, record["launch_artifact"])
    if manifest_path != root / ACQUISITION / "plans/manifest.json":
        raise ValueError("unexpected Opus launch artifact")
    manifest = _read(manifest_path)
    certificate_path = _bound(root, record["admission_artifact"])
    if certificate_path != root / ACQUISITION / "admission.json":
        raise ValueError("unexpected Opus admission artifact")
    certificate = _read(certificate_path)
    if certificate.get("schema") != "bottleneckbench-opus-public-admission-v1":
        raise ValueError("unexpected Opus public admission schema")
    amendment_path = _admission(root, manifest, certificate)
    if _bound(root, record["authorization_artifact"]) != amendment_path:
        raise ValueError("unexpected Opus authorization artifact")
    pricing_path = _bound(root, record["active_route_pricing"])
    if pricing_path != root / PRICING:
        raise ValueError("unexpected Opus pricing artifact")
    prices = _read(pricing_path)
    price = prices.get("models", {}).get(MODEL, {})
    if (price.get("input") != 5 or price.get("output") != 25
            or price.get("provider_only") != ["anthropic"]
            or price.get("provider") != "OpenRouter / Anthropic"):
        raise ValueError("Opus frozen normalized token pricing changed")
    endpoint_path = _bound(root, record["endpoint_snapshot_artifact"])
    if endpoint_path != root / ACQUISITION / "endpoint-snapshot.json":
        raise ValueError("unexpected Opus endpoint artifact")
    route = _read(endpoint_path)["selected_route"]
    if (route["provider_only"] != ["anthropic"] or route["allow_fallbacks"] is not False
            or float(route["pricing"]["prompt"]) != 5e-6
            or float(route["pricing"]["completion"]) != 25e-6):
        raise ValueError("Opus pricing does not match its frozen standard endpoint")
    plans = {entry["block"]: entry for entry in manifest["plans"]}
    if set(plans) != set(BLOCKS) or len(manifest["plans"]) != 9:
        raise ValueError("Opus launch block inventory changed")
    certified = {item["name"]: item for item in certificate["outputs"]}
    sources, selected_paths, seen_blocks = [], set(), set()
    for source in record["sources"]:
        block = source["acquisition_block"]
        if block in seen_blocks or block not in BLOCKS:
            raise ValueError("duplicate or unregistered Opus source block")
        seen_blocks.add(block)
        count, group, ledger_group = BLOCKS[block]
        entry = plans[block]
        plan_path = _bound(root, source["plan_artifact"])
        plan = _read(plan_path)
        if (source["block"] != group or source["ledger_group"] != ledger_group
                or source["plan"] != source["plan_artifact"]["path"]
                or plan_path != manifest_path.parent / entry["name"]
                or source["plan_artifact"]["sha256"] != entry["sha256"]
                or source["models"] != [MODEL] or entry["conditions"] != count
                or len(source["row_artifacts"]) != 1 or len(source["rows"]) != 1):
            raise ValueError("Opus selected plan differs from the frozen launch")
        config = plan.get("configuration", {})
        execution = plan.get("execution") or config.get("execution") or config
        if (execution.get("provider_only") != ["anthropic"]
                or execution.get("provider_allow_fallbacks") is not False
                or execution.get("omit_temperature") is not True
                or execution.get("omit_seed") is not True
                or execution.get("omit_parallel_tool_calls") is not True):
            raise ValueError("Opus source changed the matched standard route or controls")
        output = _bound(root, source["row_artifacts"][0])
        name = str(output.relative_to(root))
        if (source["rows"] != [name] or output != _selected_output(root, entry)
                or name in selected_paths or certified.get(name, {}).get("sha256") != _sha(output)
                or certified[name]["rows"] != count):
            raise ValueError("Opus selected output differs from the completed inventory")
        selected_paths.add(name)
        rows = [json.loads(line) for line in output.read_text().splitlines()]
        if len(rows) != count or any(is_infrastructure_failure(r) or r.get("status") == "failed_closed" for r in rows):
            raise ValueError("Opus output is incomplete or contains infrastructure failures")
        sources.append({"plan": plan_path, "rows": [output], "models": {MODEL}})
    if selected_paths != set(certified):
        raise ValueError("Opus source map does not cover every certified output")
    entries = validate_plan_sources(sources, expected_condition_ids=set(manifest["condition_ids"]), verify_plan_inputs=False)
    if sum(item["rows"] for item in entries) != 1336:
        raise ValueError("Opus exact condition closure failed")
    return record


def source_groups(path=DEFAULT_OUTPUT, root=ROOT):
    record = validate_source_map(path, root)
    groups = {}
    for item in record["sources"]:
        groups.setdefault(item["block"], []).append({
            "plan": _resolve(root, item["plan"]),
            "rows": [_resolve(root, p) for p in item["rows"]], "models": {MODEL},
            **({"input_root": _resolve(root, item["input_root"])} if "input_root" in item else {})})
    return groups


def build(root=ROOT):
    root = root.resolve()
    manifest_path = root / ACQUISITION / "plans/manifest.json"
    certificate_path = root / ACQUISITION / "admission.json"
    manifest, certificate = _read(manifest_path), _read(certificate_path)
    amendment = _admission(root, manifest, certificate)
    sources = []
    for entry in manifest["plans"]:
        block = entry["block"]
        count, group, ledger_group = BLOCKS[block]
        plan_path = manifest_path.parent / entry["name"]
        output = _selected_output(root, entry)
        sources.append({"acquisition_block": block, "block": group,
            "ledger_group": ledger_group, "models": [MODEL], "expected_rows": count,
            "plan": str(plan_path.relative_to(root)), "plan_artifact": _item(root, plan_path),
            "rows": [str(output.relative_to(root))], "row_artifacts": [_item(root, output)],
            **({"input_root": str(output.parent.relative_to(root))} if block in {"structural", "toolsandbox"} else {})})
    record = {"schema": SCHEMA, "models": [MODEL], "rows": 1336,
        "scientific_panel_role": "first_class", "sources": sources,
        "reporting_policy": "one unified configuration panel and the same estimators; ledger-group names retain implementation compatibility, not scientific acquisition cohorts",
        "generator": _item(root, root / "experiments/build_opus_release_source_map.py"),
        "launch_artifact": _item(root, manifest_path),
        "admission_artifact": _item(root, certificate_path),
        "authorization_artifact": _item(root, amendment),
        "active_route_pricing": _item(root, root / PRICING),
        "endpoint_snapshot_artifact": _item(root, root / ACQUISITION / "endpoint-snapshot.json"),
        "raw_receipt_closure": "verified by the complete-inventory certificate at acquisition; private raw bodies are not required or freshly verified by public offline replay"}
    record["artifact_hash"] = stable_hash(record)
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        record = validate_source_map(args.output)
    else:
        if args.output.exists():
            raise RuntimeError("refusing to overwrite an admitted source map")
        prepare_admission()
        record = build()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", encoding="utf-8", dir=args.output.parent, delete=False) as handle:
            handle.write(canonical_json(record) + "\n")
            temporary = Path(handle.name)
        try:
            validate_source_map(temporary)
            temporary.replace(args.output)
        finally:
            if temporary.exists():
                temporary.unlink()
    print(canonical_json({"passed": True, "rows": record["rows"], "artifact_hash": record["artifact_hash"]}))


if __name__ == "__main__":
    main()
