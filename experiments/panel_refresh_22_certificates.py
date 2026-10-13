"""Read-only, schema-specific model certificate adapters for the 22-panel candidate.

These adapters recheck certified artifacts; they do not launch a harness, take a
worker lock, publish a panel, or issue a new acquisition/replay certificate. Six
historical reports and the completed Haiku and DeepInfra reports are literal-byte
pinned, with pinned acquisition/verifier identities for both isolated campaigns.
Successful records remain release_admitted=False until whole-panel validation.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from experiments import build_panel_refresh_22 as inventory

BLOCKS = inventory.BLOCKS
INTERNAL = {k: v for k, v in BLOCKS.items() if k != "toolsandbox"}
IGNORED = ["executor.started_at", "executor.completed_at", "efficiency.wall_time_seconds"]
BASE = inventory.BASE + "/execution"
APPROVED_CAPS = {
    "anthropic/claude-opus-5.5": 200, "anthropic/claude-sonnet-5.5": 100,
    "openai/gpt-6-luna": 5, "openai/gpt-6.1-sol": 100,
    inventory.QWEN: 100, inventory.GEMINI: 40,
    inventory.HAIKU: 10, inventory.DEEPSEEK: 15,
}
LEGACY = {
    "openai/gpt-6-luna": ("luna", "dcadead88e0a795a479110a98171a98604c3c6f5670d8a7df7af5b58a7136a5b", "dcba820ada64ba41ae997a611433e6ea64614de5fb45a794fe2e05b32c8e4ecd"),
    "anthropic/claude-sonnet-5.5": ("sonnet", "ae67da464e66b81e576e92fce967cf561f11249e2ea202194109f4efd0d3e4f8", "2a0932fa3dfd6fe7b5e50d7a8514dccb1043b119e660d3c32340517749f646fa"),
    "anthropic/claude-opus-5.5": ("opus", "fae255dbb5f9cc77d9c92d7223e92d6e4833242936f1d498e5c1cb9ea0edfdb8", "0733c987598896453bdc0e7487faa98cfd86cfb5e715ed9057c5b1e4e10cc6d1"),
    "openai/gpt-6.1-sol": ("sol", "363c881ef61fde22b028daf4a5f53a422d637a6880c44f2292cfc13a0c476fb1", "99df4eecc6ef42405b796aa889953d0098e390e8ac147e8b7fa85f14b4890992"),
}
HAIKU_REPORT = {
    "path": inventory.HAIKU_BASE + "/execution/offline-nine-block-replay-v1/haiku-2026-10-08-a/report.json",
    "sha256": "9dc127b1f43937a0b3b69057bcefbbd74058a2741fef7c0cdeae5d2560061722",
}
DEEPINFRA_REPORT = {
    "path": inventory.DEEPINFRA_BASE + "/offline-replay-v1/final-v1/report.json",
    "sha256": "16a869980f6edb5d44227505c004f6bc171fec58d5559c9677e31a152b02d163",
}
FRESH = {
    inventory.HAIKU: {
        "campaign": "haiku", "root": inventory.HAIKU_BASE + "/execution/offline-nine-block-replay-v1",
        "schema": "panel-refresh-haiku-nine-block-offline-replay-v1", "probes": 2,
        "pins": {
            inventory.HAIKU_BASE + "/execution/authorization.json": "1b03d9c1c50c87be766ba6609c9d7b22479050534504ca5babc700a3423b91a0",
            "experiments/audit_panel_refresh_haiku_v1.py": "a9474507c07776dd0208751a5428480eff60b994c55a915c69a47f7b8f3c439b",
            "experiments/replay_panel_refresh_haiku_v1.py": "e97cec478e08017f3f775469b9ab1df0c98fb7d429b8cc5836978746cda3dd4e",
            "protocols/panel-refresh-haiku-2026-10-08.md": "f97ea4e2cedc336cabdb0ef75972d61f62b438e892134c9b17ed14a7de979c62"}},
    inventory.DEEPSEEK: {
        "campaign": "deepinfra", "root": inventory.DEEPINFRA_BASE + "/offline-replay-v1",
        "schema": "deepinfra-nine-block-offline-replay-v1", "probes": 3,
        "pins": {
            inventory.DEEPINFRA_BASE + "/execution/authorization.json": "9250fa66e6dd59cec16a47a565ec27b53e96e286dfe31e5c584c03225eb1406b",
            "experiments/audit_panel_refresh_deepinfra_v1.py": "c5491297d08b594b4a6c9bf48c27ca263f1c24d5e7b38adee164a992caa7e21e",
            "experiments/replay_panel_refresh_deepinfra_v1.py": "6fecbc9a86b9d03fa108f866091bf7d35aa0331371df5315ba0f6346cd331ef0",
            "protocols/panel-refresh-deepinfra-2026-10-08.md": "1fb16d3274f865eab75011581a2dca30dcb82c5daaabfae0873f07ddf184afd7"}},
}
FIELDS = {
    "panel-refresh-typed-harness-verification-v2": "blocks canonical_typed_rows_equal canonical_typed_wires_equal checked_at_utc conditions excluded_blocks inputs model new_api_calls passed probe_requests release_admitted requests schema",
    "panel-refresh-internal-harness-replay-v1": "api_cost_usd blocks completed_at_utc conditions excluded_blocks ignored_row_paths input_audit_sha256 inputs model network_disabled new_api_calls passed probe_requests release_admitted request_count requests_sha256 schema scope verification_status",
    "panel-refresh-toolsandbox-saved-state-verification-v2": "checked checked_at_utc conditions inputs limitations model new_api_calls normalization passed release_admitted request_captures_sha256 requests schema scope",
    "panel-refresh-recovered-completion-audit-v1": "audited_at_utc blocks budget_accounted_usd completed_conditions harness_request_replay inputs known_receipt_cost_usd model model_cap_usd passed pending_intent_count planned_conditions pre_transport_request_hash_reconstruction probe_response_count raw_receipt_count recovery_chains release_admitted required_before_final_admission schema scope scored_response_count unresolved_cost_reserved_usd validated_plans verification_status",
    "panel-refresh-v5-nine-block-offline-replay-v2": "api_cost_usd base_eight_request_score_replay blocks budget_boundary_pending_terminal_cases completed_at_utc conditions ignored_internal_row_paths inputs internal_conditions known_receipt_cost_usd limitations model network_disabled new_api_calls outputs passed probe probe_requests release_admitted request_count schema toolsandbox_conditions toolsandbox_frozen_zero_fallbacks toolsandbox_official_regrades unknown_cost_reserved_usd verification_driver_version verification_status",
    "panel-refresh-v5-nine-block-audit-v1": "schema model passed checked_at_utc release_admitted completed_conditions verification_status pre_transport_request_score_replay conditions_by_block dispositions known_receipt_cost_usd unknown_cost_reserved_usd budget_accounted_usd model_cap_usd scored_responses probe_responses recovery_chains selected new_api_calls scope inputs",
    "panel-refresh-haiku-nine-block-offline-replay-v1": "schema model completed_at_utc passed release_admitted verification_status conditions internal_conditions toolsandbox_conditions probe_requests request_count base_eight_request_score_replay blocks toolsandbox_official_regrades toolsandbox_frozen_zero_fallbacks budget_boundary_pending_terminal_cases canonical_typed_rows_equal canonical_typed_wires_equal new_api_calls api_cost_usd network_disabled known_receipt_cost_usd unknown_cost_reserved_usd budget_accounted_usd model_cap_usd ignored_internal_row_paths limitations outputs probe inputs",
    "panel-refresh-haiku-independent-audit-v1": "schema model passed checked_at_utc release_admitted verification_status completed_conditions conditions_by_block scored_responses probe_responses recovery_chains known_receipt_cost_usd unknown_cost_reserved_usd budget_accounted_usd model_cap_usd request_score_replay new_api_calls selected limitations inputs",
    "deepinfra-nine-block-offline-replay-v1": "schema passed release_admitted model configuration_id configuration_sha256 manifest authorization protocol route completed_at_utc verification_status conditions internal_conditions toolsandbox_conditions probe_requests accepted_request_count blocks source_blocks toolsandbox_official_regrades environment_transitions_reexecuted known_receipt_cost_usd unknown_cost_reserved_usd budget_accounted_usd model_cap_usd closure network_disabled new_api_calls api_cost_usd probes ignored_internal_row_paths limitations outputs inputs",
    "deepinfra-full-inventory-audit-v1": "schema passed release_admitted model configuration_id configuration_sha256 manifest authorization protocol route completed_at_utc conditions planned_conditions completed_conditions internal_conditions toolsandbox_conditions probe_requests blocks accepted_request_count scored_response_count pending_attempt_count new_known_receipt_cost_usd new_unknown_cost_reserved_usd legacy_known_receipt_cost_usd legacy_unknown_cost_reserved_usd known_receipt_cost_usd unknown_cost_reserved_usd budget_accounted_usd model_cap_usd closure new_api_calls network_disabled scope inputs",
}
require, equal_hash = inventory.require, inventory.stable_hash


def equal(left, right, message):
    require(equal_hash(left) == equal_hash(right), message)


def schema(value, expected, model):
    require(isinstance(value, dict) and set(value) == set(FIELDS[expected].split()),
            f"unknown/incomplete certificate fields: {expected}")
    require(value["schema"] == expected and value["model"] == model
            and value["passed"] is True and value["release_admitted"] is False,
            "certificate identity/status differs")
    if expected != "panel-refresh-recovered-completion-audit-v1":
        equal(value["new_api_calls"], 0, "certificate is not offline")


class Evidence:
    """Exact byte bindings plus a final read-only inventory/hash stability check."""
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.bindings = {}
        self.inventories = {}

    def add(self, binding):
        require(isinstance(binding, dict) and set(binding) == {"path", "sha256"}, "invalid artifact binding fields")
        name, digest = binding["path"], binding["sha256"]
        require(isinstance(digest, str) and re.fullmatch("[0-9a-f]{64}", digest), "invalid artifact digest")
        require(name not in self.bindings or self.bindings[name] == digest, "conflicting artifact binding")
        if name not in self.bindings:
            equal(inventory.bind(self.root, name), binding, f"changed certificate evidence: {name}")
            self.bindings[name] = digest
        return inventory.resolve(self.root, name)

    def many(self, bindings):
        require(isinstance(bindings, list), "artifact inventory is not a list")
        names = [b["path"] for b in bindings]
        require(len(names) == len(set(names)), "duplicate artifact inventory binding")
        for binding in bindings:
            self.add(binding)

    def load(self, binding):
        return json.loads(self.add(binding).read_text(), parse_constant=lambda s: (_ for _ in ()).throw(ValueError("nonfinite JSON")))

    def bound(self, name):
        require(name in self.bindings, f"required file absent from certificate inventory: {name}")
        return {"path": name, "sha256": self.bindings[name]}

    def document(self, binding, expected, model):
        value = self.load(binding)
        schema(value, expected, model)
        self.many(value["inputs"])
        if "outputs" in value:
            self.many(value["outputs"])
        return value

    def watch(self, directory):
        path = inventory.resolve(self.root, directory)
        observed = {str(p.relative_to(self.root)) for p in path.rglob("*.json")}
        bound = {name for name in self.bindings if Path(name).is_relative_to(directory) and name.endswith(".json")}
        require(observed == bound, f"missing/orphan certificate inventory: {directory}")
        self.inventories[directory] = observed

    def finish(self):
        for name, digest in self.bindings.items():
            require(inventory.bind(self.root, name)["sha256"] == digest, f"evidence changed during verification: {name}")
        for name, expected in self.inventories.items():
            actual = {str(p.relative_to(self.root)) for p in inventory.resolve(self.root, name).rglob("*.json")}
            require(actual == expected, "certificate inventory changed during verification")


def binding(path, digest):
    return {"path": path, "sha256": digest}


def block_index(blocks, expected):
    require(isinstance(blocks, list) and len(blocks) == len(expected), "incomplete block inventory")
    result = {b["block"]: b for b in blocks}
    require(len(result) == len(blocks) and set(result) == set(expected), "duplicate/foreign block")
    for name, count in expected.items():
        equal(result[name]["conditions"], count, "block count differs")
    return result


def semantic(row):
    result = deepcopy(row)
    for dotted in IGNORED:
        owner = result
        keys = dotted.split(".")
        for key in keys[:-1]:
            owner = owner.get(key, {}) if isinstance(owner, dict) else {}
        if isinstance(owner, dict):
            owner.pop(keys[-1], None)
    return result


def typed_rows(source, replay):
    def index(rows):
        values = {r["condition_id"]: semantic(r) for r in rows}
        require(len(values) == len(rows), "duplicate typed replay row")
        return values
    old, new = index(source), index(replay)
    equal(old, new, "typed replay rows differ")
    return equal_hash([old[k] for k in sorted(old)])


def row_records(evidence, name, block):
    path = evidence.add(evidence.bound(name))
    lines = path.read_bytes().splitlines(keepends=True)
    require(lines and all(line.strip() for line in lines), "empty/blank source journal")
    result = []
    for index, line in enumerate(lines):
        row = json.loads(line)
        result.append({"block": block, "condition_id": row["condition_id"], "journal": name,
            "row_index": index, "literal_line_sha256": hashlib.sha256(line).hexdigest(),
            "row_sha256": equal_hash(row), "row": row})
    return result


def pinned_plans(evidence, model):
    campaign = FRESH[model]["campaign"] if model in FRESH else "original"
    manifest_path, price_path = inventory.MANIFEST_PATHS[campaign], inventory.PRICE_PATHS[campaign]
    manifest = evidence.load(binding(manifest_path, inventory.ANCHORS[manifest_path]))
    price = evidence.load(binding(price_path, inventory.ANCHORS[price_path]))
    inventory.verify_self_hash(manifest, "manifest_hash")
    inventory.verify_self_hash(price, "artifact_hash")
    evidence.many(manifest["inputs"])
    configs = [price["configuration"]] if campaign == "deepinfra" else price["configurations"]
    configs = [c for c in configs if c["model"] == model]
    require(len(configs) == 1, "model configuration is ambiguous")
    entries = [e for e in manifest["plans"] if e["model"] == model]
    require(Counter(e["block"] for e in entries) == Counter({b: 1 for b in BLOCKS}), "not nine exact frozen plans")
    plans, ids = {}, set()
    for entry in entries:
        require(type(entry["conditions"]) is int and entry["conditions"] == BLOCKS[entry["block"]],
                "untyped/different frozen plan entry count")
        plan = evidence.load({k: entry[k] for k in ("path", "sha256")})
        body = {k: v for k, v in plan.items() if k != "plan_hash"}
        require(entry["plan_hash"] == equal_hash(body) == plan.get("plan_hash", equal_hash(body)), "frozen plan hash differs")
        conditions = {inventory.condition_id(c): c for c in plan["conditions"]}
        require(len(conditions) == len(plan["conditions"]) == BLOCKS[entry["block"]]
                and not ids & set(conditions) and all(c["model"] == model for c in conditions.values()), "plan condition inventory differs")
        if model == inventory.DEEPSEEK:
            require(entry.get("configuration_id") == inventory.DEEPINFRA_CONFIGURATION
                    and entry["output"] == inventory.DEEPINFRA_BASE + "/execution/scored/" + entry["block"] + ".jsonl",
                    "old provider cannot enter isolated DeepSeek selection")
        plans[entry["block"]] = (entry, conditions)
        ids.update(conditions)
    require(len(ids) == 1336, "not exactly 1336 planned conditions")
    return plans, configs[0], manifest


def source_selection(evidence, model, plans, selected=None):
    records = []
    for block, (entry, conditions) in plans.items():
        if selected is None:
            current = row_records(evidence, entry["output"], block)
        else:
            declared = [r for r in selected if r["block"] == block]
            allowed = {entry["output"]}
            if model in inventory.V5_REPORTS and block == "toolsandbox":
                allowed.add(BASE + "/toolsandbox-recovery-v5/" + model.replace("/", "--") + ".jsonl")
            require({r["journal"] for r in declared} <= allowed, "foreign selected source journal")
            available = {name: row_records(evidence, name, block) for name in {r["journal"] for r in declared}}
            current = []
            for item in declared:
                index = item["row_index"]
                require(type(index) is int and 0 <= index < len(available[item["journal"]]), "invalid literal row index")
                record = available[item["journal"]][index]
                equal({k: item[k] for k in record if k != "row"}, {k: v for k, v in record.items() if k != "row"}, "literal selected provenance differs")
                for key in ("context_path", "conversation_path"):
                    if item.get(key) is not None:
                        evidence.add(evidence.bound(item[key]))
                current.append({**item, "row": record["row"]})
            if model in inventory.V5_REPORTS and block == "toolsandbox":
                overlay = next(iter(allowed - {entry["output"]}))
                equal(dict(Counter(r["journal"] for r in current)),
                    {entry["output"]: 68, overlay: 28} if model == inventory.QWEN else {entry["output"]: 25, overlay: 71}, "v5 source selection differs")
        require(len(current) == len({r["condition_id"] for r in current}) == BLOCKS[block]
                and {r["condition_id"] for r in current} == set(conditions), "selected block is incomplete/duplicated")
        for record in current:
            row, cid = record["row"], record["condition_id"]
            require(row["plan_hash"] == entry["plan_hash"], "selected row plan changed")
            condition = row.get("condition")
            if isinstance(condition, dict):
                equal(condition, conditions[cid], "typed frozen condition differs")
                require(row.get("model", model) == model, "conflicting row model")
            else:
                require(row.get("model") == model, "foreign row model")
                fields = set(conditions[cid]) - {"design", "upstream_commit"}
                require(fields <= set(row), "flat frozen condition projection is incomplete")
                equal({k: row[k] for k in fields},
                      {k: conditions[cid][k] for k in fields}, "flat frozen condition differs")
            equal(row.get("attempt", 1), 1, "scientific resample")
            equal(row.get("confirmatory_weight", 0), 0, "nonzero new evidence weight")
            require(row.get("status") != "provider_failure" or
                    "provider_failure:tool call arguments are invalid JSON" in row.get("errors", []), "infrastructure row")
            if row.get("status") == "failed_closed":
                require(model == inventory.GEMINI and block == "toolsandbox"
                        and cid == "4cee4a20d5026460fb8d4a5c079ff052904d329af30c425f41c8c3d7f55f6bda",
                        "unadjudicated failed-closed row")
        records.extend(current)
    require(len(records) == len({r["condition_id"] for r in records}) == 1336, "full selected inventory differs")
    return records


def verify_internal(evidence, directory, blocks, records, style):
    for block, item in block_index(blocks, INTERNAL).items():
        if style == "legacy":
            target = binding(directory + "/" + block + ".jsonl", item["replay_rows_sha256"])
        elif style == "deepinfra":
            target = item["rows"]
        else:
            target = binding(item["output"], item["sha256"])
        require(target["path"] == directory + "/" + block + ".jsonl", "replay output escaped certificate directory")
        evidence.add(target)
        replay = [json.loads(line) for line in inventory.resolve(evidence.root, target["path"]).read_text().splitlines()]
        source = [r["row"] for r in records if r["block"] == block]
        require(typed_rows(source, replay) == item["semantic_sha256"], "semantic replay digest differs")


def verify_tools(tools, records, model, style):
    rows = {r["condition_id"]: r["row"] for r in records if r["block"] == "toolsandbox"}
    require(len(tools) == len({t["condition_id"] for t in tools}) == 96
            and {t["condition_id"] for t in tools} == set(rows), "ToolSandbox grade coverage differs")
    official = 0
    for item in tools:
        equal(item["score"], rows[item["condition_id"]]["score"], "typed saved-state score differs")
        if style != "legacy":
            require(type(item["official_grader_reevaluation"]) is bool, "ambiguous ToolSandbox grading flag")
            official += item["official_grader_reevaluation"]
            if not item["official_grader_reevaluation"]:
                require(model == inventory.GEMINI and item["condition_id"] == "4cee4a20d5026460fb8d4a5c079ff052904d329af30c425f41c8c3d7f55f6bda"
                        and item["verification"] == "frozen_unknown_tool_zero_fallback_reproduced", "unapproved nonofficial grade")
    require(style == "legacy" or official == (95 if model == inventory.GEMINI else 96), "official grade count differs")


def verify_captures(evidence, captures, records, model, configuration, probe_count):
    require(len(captures) == len({c["logical_intent_hash"] for c in captures})
            == len({c["generation_id"] for c in captures}), "duplicate request/generation capture")
    receipts, probes = {}, []
    root = inventory.HAIKU_BASE if model == inventory.HAIKU else inventory.DEEPINFRA_BASE if model == inventory.DEEPSEEK else inventory.BASE
    receipt_dir = root + "/execution/receipts/" + model.replace("/", "--")
    for cap in captures:
        allowed = {"generation_id", "logical_intent_hash", "raw", "raw_sha256", "request_hash", "wire_sha256"}
        if model == inventory.DEEPSEEK:
            allowed.add("phase")
        require(set(cap) == allowed and str(Path(cap["raw"]).parent) == receipt_dir, "capture has foreign route/source or unknown fields")
        event = evidence.load(binding(cap["raw"], cap["raw_sha256"]))
        pending_name = cap["raw"].replace(".raw.json", ".pending.json")
        intent = evidence.load(evidence.bound(pending_name))
        require(intent["model"] == model, "request model differs")
        equal(intent["attempt"], 1, "request is a scientific resample")
        equal(intent["request_hash"], cap["request_hash"], "typed request hash differs")
        equal(equal_hash(event["request"]), cap["wire_sha256"], "typed wire hash differs")
        body = json.loads(event["response_body"])
        require(type(event["http_status"]) is int and event["http_status"] == 200 and not event.get("transport_error") and not body.get("error")
                and body.get("choices") and body["id"] == cap["generation_id"], "unsuccessful accepted capture")
        require(body["model"] in {model, configuration["canonical_slug"]}
                and body["provider"] == configuration["selected_route"]["provider_name"]
                and event["request"]["model"] == model
                and event["request"]["provider"]["only"] == configuration["provider"]["only"], "served/selected provider differs")
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            require(type(body["usage"][key]) is int and body["usage"][key] >= 0, "untyped raw usage")
        equal(body["usage"]["total_tokens"], body["usage"]["prompt_tokens"] + body["usage"]["completion_tokens"], "raw token sum differs")
        phase = intent.get("base_phase", intent["phase"])
        if model == inventory.HAIKU:
            normalized = {k: intent[k] for k in ("model", "condition_id", "repeat", "attempt", "turn_index", "request_hash")}
            normalized["phase"] = phase
        else:
            normalized = {k: v for k, v in intent.items() if k != "unreconciled_liability_usd"}
            if model == inventory.DEEPSEEK:
                phase = re.sub(r"^deepinfra-technical:[1-5]:", "", phase)
            else:
                phase = re.sub(r"^(?:authorized-recovery-v1:|technical-recovery-v2:[1-5]:)", "", phase)
            normalized["phase"] = phase
        equal(equal_hash(normalized), cap["logical_intent_hash"], "logical intent normalization differs")
        require(phase in {"scored", "probe"} if model != inventory.DEEPSEEK else phase in {
            "deepinfra:scored", "deepinfra:compatibility-zero-weight", "deepinfra:tool-probe-zero-weight"}, "unknown normalized request phase")
        if "scored" not in phase:
            probes.append(phase)
        receipts[body["id"]] = (body, intent, phase)
    require(len(probes) == probe_count, "probe scope differs")
    if model == inventory.DEEPSEEK:
        equal(dict(Counter(probes)), {"deepinfra:compatibility-zero-weight": 1, "deepinfra:tool-probe-zero-weight": 2}, "DeepInfra compatibility/fresh-probe scopes differ")
    referenced = []
    for record in records:
        row = record["row"]
        ids = row.get("response_ids", [row["response_id"]] if row.get("response_id") else [])
        require(isinstance(ids, list) and len(ids) == len(set(ids)), "invalid row response inventory")
        require(ids or record["block"] == "primary_discovery" and row["status"] == "capability_unavailable", "unexplained response-free row")
        for turn, rid in enumerate(ids):
            require(rid in receipts and receipts[rid][1]["condition_id"] == row["condition_id"]
                    and "scored" in receipts[rid][2], "response is not this scored condition")
            equal(receipts[rid][1]["turn_index"], turn, "row turn order differs")
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            equal(row["usage"][key], sum(receipts[rid][0]["usage"][key] for rid in ids), "typed row/raw token sum differs")
        referenced.extend(ids)
    require(len(referenced) == len(set(referenced)) and set(referenced) == {rid for rid, v in receipts.items() if "scored" in v[2]}, "scored response closure differs")
    evidence.watch(receipt_dir)
    return len(referenced)


def output_document(evidence, directory, report, name):
    path = directory + "/" + name
    matches = [b for b in report["outputs"] if b["path"] == path]
    require(len(matches) == 1, f"missing exact nested certificate/output: {name}")
    return evidence.load(matches[0])


def audit_billing(audit, report=None, *, model):
    unknown_key = "unknown_cost_reserved_usd" if "unknown_cost_reserved_usd" in audit else "unresolved_cost_reserved_usd"
    keys = ("known_receipt_cost_usd", unknown_key, "budget_accounted_usd", "model_cap_usd")
    values = []
    for key in keys:
        value = audit[key]
        require(type(value) in {int, float} and Decimal(str(value)).is_finite() and value >= 0, "invalid billing scalar")
        values.append(Decimal(str(value)))
    require(abs(values[0] + values[1] - values[2]) < Decimal("0.00000001") and values[2] <= values[3], "billing/cap closure differs")
    require(model in APPROVED_CAPS and values[3] == Decimal(APPROVED_CAPS[model]), "cap differs from approved model cap")
    if report is not None:
        for key in ("known_receipt_cost_usd", "unknown_cost_reserved_usd"):
            equal(report[key], audit[key], "nested billing claims differ")


def verify_haiku_receipt_joins(evidence, captures, configuration, manifest, audit, report):
    """Recheck the admitted Haiku's exact no-retry receipt/header/probe closure.

    This does not import or run an acquisition/audit worker. The fixed report
    covers no technical attempts; a future recovery inventory needs a separately
    reviewed certificate, not a relaxed interpretation of this report.
    """
    model, base = inventory.HAIKU, inventory.HAIKU_BASE + "/execution"
    directory = base + "/receipts/" + model.replace("/", "--")
    auth_sha = FRESH[model]["pins"][base + "/authorization.json"]
    fields = {"schema", "model", "base_phase", "phase", "retry_index", "condition_id", "repeat",
        "attempt", "turn_index", "request_hash", "wire_sha256", "manifest_hash", "authorization_sha256", "unreconciled_liability_usd"}
    prices = configuration["selected_route"]["pricing"]
    bands = [{**prices, **override} for override in [{}, *prices.get("overrides", [])]]
    output_rate = max(Decimal(str(band["completion"])) for band in bands)
    input_rate = max(Decimal(str(band[key])) for band in bands for key in (
        "prompt", "input_cache_read", "input_cache_write", "input_cache_write_1h") if key in band)
    liability = configuration["context_length"] * input_rate + 1024 * output_rate
    provider = {**configuration["provider"], "max_price": {
        key: max(float(band[key]) for band in bands) * 1e6 for key in ("prompt", "completion")}}
    seen, probes, known = set(), [], Decimal(0)
    for capture in captures:
        raw_path = capture["raw"]
        event = evidence.load(binding(raw_path, capture["raw_sha256"]))
        pending_path, header_path = raw_path.replace(".raw.json", ".pending.json"), raw_path.replace(".raw.json", ".headers.json")
        intent, header = evidence.load(evidence.bound(pending_path)), evidence.load(evidence.bound(header_path))
        require(set(intent) == fields and intent["schema"] == "haiku-wire-intent-v1" and intent["model"] == model,
                "Haiku intent schema/model differs")
        equal(intent["retry_index"], 0, "admitted Haiku report has no technical retries")
        equal(intent["attempt"], 1, "Haiku scientific attempt differs")
        require(type(intent["turn_index"]) is int and intent["turn_index"] >= 0
                and (intent["repeat"] is None or type(intent["repeat"]) is int), "Haiku untyped request identity")
        phase = intent["base_phase"]
        require(phase in {"probe", "scored"} and intent["phase"] == "haiku-v1:" + phase,
                "Haiku independent phase differs")
        require(intent["authorization_sha256"] == auth_sha and intent["manifest_hash"] == manifest["manifest_hash"],
                "Haiku request authority differs")
        require(type(intent["unreconciled_liability_usd"]) in {int, float}
                and Decimal(str(intent["unreconciled_liability_usd"])) == liability, "Haiku durable liability differs")
        stem = equal_hash({k: v for k, v in intent.items() if k != "unreconciled_liability_usd"})
        require(raw_path == directory + "/" + stem + ".raw.json", "Haiku durable intent path/hash differs")
        wire = event["request"]
        require(intent["wire_sha256"] == capture["wire_sha256"] == equal_hash(wire), "Haiku durable wire join differs")
        equal(event.get("attempt"), 1, "Haiku hidden transport attempts")
        equal(wire.get("max_tokens"), 1024, "Haiku completion cap differs")
        equal(wire.get("provider"), provider, "Haiku pinned routing differs")
        expected_wire = {"model", "messages", "max_tokens", "provider"}
        if "tools" in wire:
            require(isinstance(wire["tools"], list) and wire["tools"] and wire.get("tool_choice") == "auto", "Haiku tool settings differ")
            expected_wire |= {"tools", "tool_choice"}
        require(set(wire) == expected_wire and isinstance(wire["messages"], list), "Haiku frozen wire fields differ")
        body = json.loads(event["response_body"])
        require(set(header) == {"received_at_utc", "http_status", "generation_id", "request_sha256"}
                and header["request_sha256"] == intent["wire_sha256"], "Haiku header schema/wire binding differs")
        require(isinstance(header["received_at_utc"], str), "Haiku header timestamp is missing")
        datetime.fromisoformat(header["received_at_utc"].replace("Z", "+00:00"))
        equal(header["http_status"], 200, "Haiku accepted header status differs")
        require(isinstance(body["id"], str) and body["id"]
                and header["generation_id"] == event.get("generation_id") == body["id"] == capture["generation_id"], "Haiku header generation join differs")
        use = body["usage"]
        require(type(use.get("cost")) in {int, float} and Decimal(str(use["cost"])).is_finite()
                and use["cost"] >= 0, "Haiku accepted billing is unknown/invalid")
        require(use["total_tokens"] > 0 and use["prompt_tokens"] <= configuration["context_length"]
                and use["completion_tokens"] <= 1024, "Haiku usage exceeds frozen bounds")
        known += Decimal(str(use["cost"]))
        triplet = {raw_path, pending_path, header_path}
        require(not seen & triplet, "Haiku duplicate receipt triplet")
        seen.update(triplet)
        if phase == "probe":
            require(intent["condition_id"] == "transport-probe-haiku-2026-10-08" and intent["repeat"] is None,
                    "Haiku probe request scope differs")
            probes.append((intent["turn_index"], body["id"], triplet))
    equal(sorted(seen), sorted(name for name in evidence.bindings if str(Path(name).parent) == directory), "Haiku unaccounted receipt attempts")
    equal(audit["recovery_chains"], [], "Haiku pinned report unexpectedly contains recovery")
    for summary in (audit, report):
        for field, expected in (("known_receipt_cost_usd", known), ("budget_accounted_usd", known), ("unknown_cost_reserved_usd", Decimal(0))):
            value = summary[field]
            require(type(value) in {int, float} and Decimal(str(value)).is_finite()
                    and abs(Decimal(str(value)) - expected) < Decimal("0.000000001"), "Haiku full receipt billing closure differs")
        require(type(summary["model_cap_usd"]) in {int, float} and Decimal(str(summary["model_cap_usd"])) == 10 and known <= 10,
                "Haiku independent USD10 cap differs")
    equal(audit["probe_responses"], 2, "Haiku audit probe count differs")
    ordered = sorted(probes)
    equal([turn for turn, _, _ in ordered], [0, 1], "Haiku two-turn probe inventory differs")
    source = evidence.load(evidence.bound(base + "/probes/" + model.replace("/", "--") + ".json"))
    replayed = evidence.load(report["probe"])
    equal(source, replayed, "Haiku original/replayed probe differs")
    require(source["model"] == model and source["passed"] is True and source["manifest_hash"] == manifest["manifest_hash"]
            and source["authorization_sha256"] == auth_sha and source["probe_contract"] == "tool-roundtrip-v1", "Haiku probe authority differs")
    equal(source["requests"], 2, "Haiku probe cardinality differs")
    equal(source["scientific_weight"], 0, "Haiku probe has scientific weight")
    equal(source["generation_ids"], [rid for _, rid, _ in ordered], "Haiku probe generation order differs")
    equal(source["inputs"], [evidence.bound(name) for name in sorted(set().union(*(paths for _, _, paths in ordered)))],
          "Haiku probe receipt input closure differs")


def verify_deepinfra_receipt_joins(evidence, captures, configuration, manifest, audit, report):
    """Revalidate this isolated, no-supplement inventory without importing workers.

    Original-provider costs/holds remain pinned liabilities, never scored data.
    A future technical supplement requires a new certificate review; this gate
    deliberately cannot infer its safety from an HTTP error or missing receipt.
    """
    model, base = inventory.DEEPSEEK, inventory.DEEPINFRA_BASE + "/execution"
    directory = base + "/receipts/" + model.replace("/", "--")
    auth_binding = binding(base + "/authorization.json", FRESH[model]["pins"][base + "/authorization.json"])
    auth = evidence.load(auth_binding)
    equal(auth["manifest_sha256"], evidence.bound(inventory.MANIFEST_PATHS["deepinfra"])["sha256"],
          "DeepInfra authorization manifest differs")
    require(auth["configuration_id"] == configuration["configuration_id"] == inventory.DEEPINFRA_CONFIGURATION,
            "DeepInfra authorization configuration differs")
    equal(auth["maximum_supplements"], 5, "DeepInfra authorized supplement limit differs")
    equal(auth["scored_conditions"], 1336, "DeepInfra authorized condition count differs")
    equal(auth["compatibility_probe_scoring_weight"], 0, "DeepInfra authorized probe weight differs")
    equal(auth["legacy_accounting"], manifest["legacy_accounting"], "DeepInfra original liabilities changed")
    for key in ("manifest", "authorization", "protocol"):
        expected = evidence.bound(inventory.MANIFEST_PATHS["deepinfra"]) if key == "manifest" else (
            auth_binding if key == "authorization" else evidence.bound("protocols/panel-refresh-deepinfra-2026-10-08.md"))
        equal(audit[key], expected, "DeepInfra audit authority binding differs")
        equal(report[key], expected, "DeepInfra replay authority binding differs")
    for key in ("conditions", "planned_conditions", "completed_conditions"):
        equal(audit[key], 1336, "DeepInfra full audit condition count differs")
    equal(audit["internal_conditions"], 1240, "DeepInfra audit internal scope differs")
    equal(audit["toolsandbox_conditions"], 96, "DeepInfra audit saved-state scope differs")
    equal(audit["probe_requests"], 3, "DeepInfra audit probe count differs")
    equal(audit["accepted_request_count"], len(captures), "DeepInfra accepted request count differs")
    equal(audit["pending_attempt_count"], len(captures), "DeepInfra no-supplement attempt count differs")
    prices = configuration["selected_route"]["pricing"]
    liability = Decimal(str(configuration["context_length"] * max(float(prices[k]) for k in ("prompt", "input_cache_read"))
                            + 1024 * float(prices["completion"])))
    provider = {**configuration["provider"], "max_price": {k: float(prices[k]) * 1e6 for k in ("prompt", "completion")}}
    equal(configuration["provider"], {"only": ["deepinfra/fp8"], "order": ["deepinfra/fp8"],
        "quantizations": ["fp8"], "allow_fallbacks": False, "data_collection": "allow", "require_parameters": True},
        "DeepInfra pinned configuration route differs")
    equal(configuration["reasoning_override"], {"enabled": True, "effort": "high"}, "DeepInfra reasoning setting differs")
    fields = {"model", "phase", "condition_id", "repeat", "attempt", "turn_index", "request_hash", "unreconciled_liability_usd"}
    seen, probes, known = set(), {}, Decimal(0)
    def timestamp(value):
        require(isinstance(value, str), "DeepInfra receipt timestamp missing")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(parsed.tzinfo is not None, "DeepInfra receipt timestamp lacks timezone")
        return parsed.timestamp()
    authorized_start = timestamp(auth["started_at_utc"])
    require(type(auth["deadline_epoch"]) in {int, float} and Decimal(str(auth["deadline_epoch"])).is_finite(),
            "DeepInfra authorization deadline differs")
    for capture in captures:
        raw_path = capture["raw"]
        event = evidence.load(binding(raw_path, capture["raw_sha256"]))
        paths = {s: raw_path.removesuffix(".raw.json") + "." + s + ".json"
                 for s in ("pending", "binding", "wire-started", "headers", "recovery")}
        intent, bound, started, header, recovery = (evidence.load(evidence.bound(paths[s]))
            for s in ("pending", "binding", "wire-started", "headers", "recovery"))
        require(set(intent) == fields and intent["model"] == model, "DeepInfra intent schema/model differs")
        phase = intent["phase"]
        require(phase in {"deepinfra:scored", "deepinfra:compatibility-zero-weight", "deepinfra:tool-probe-zero-weight"},
                "DeepInfra admitted inventory has no technical supplements")
        equal(capture["phase"], phase, "DeepInfra capture phase differs")
        equal(intent["attempt"], 1, "DeepInfra scientific attempt differs")
        require(type(intent["turn_index"]) is int and intent["turn_index"] >= 0
                and (intent["repeat"] is None or type(intent["repeat"]) is int)
                and isinstance(intent["condition_id"], str) and intent["condition_id"]
                and isinstance(intent["request_hash"], str) and re.fullmatch("[0-9a-f]{64}", intent["request_hash"]),
                "DeepInfra untyped request identity")
        pure = {k: v for k, v in intent.items() if k != "unreconciled_liability_usd"}
        require(raw_path == directory + "/" + equal_hash(pure) + ".raw.json", "DeepInfra intent path/hash differs")
        require(type(intent["unreconciled_liability_usd"]) in {int, float}
                and Decimal(str(intent["unreconciled_liability_usd"])) == liability, "DeepInfra durable reserve differs")
        wire = event["request"]
        equal(bound, {"intent": pure, "wire": wire, "authorization_sha256": auth_binding["sha256"],
                      "configuration_id": inventory.DEEPINFRA_CONFIGURATION}, "DeepInfra wire/source binding differs")
        wire_fields = {"model", "messages", "max_tokens", "temperature", "provider", "reasoning"}
        if "tools" in wire:
            require(isinstance(wire["tools"], list) and wire["tools"] and wire.get("tool_choice") == "auto", "DeepInfra tool settings differ")
            wire_fields |= {"tools", "tool_choice"}
        require(set(wire) == wire_fields and isinstance(wire["messages"], list), "DeepInfra frozen wire fields differ")
        equal({k: wire[k] for k in ("model", "max_tokens", "temperature", "provider", "reasoning")},
              # The frozen two-turn probe builds ModelRequest(temperature=0);
              # frozen plan restoration and the structural probe use float 0.0.
              {"model": model, "max_tokens": 1024,
               "temperature": 0 if phase == "deepinfra:tool-probe-zero-weight" else float(configuration["temperature"]),
               "provider": provider, "reasoning": configuration["reasoning_override"]}, "DeepInfra scientific wire differs")
        wire_hash = equal_hash(wire)
        equal(capture["wire_sha256"], wire_hash, "DeepInfra captured wire differs")
        equal(recovery, {"superseded_attempts": [], "maximum_supplements": 5,
                         "logical_request_hash": intent["request_hash"]}, "DeepInfra no-supplement recovery provenance differs")
        require(set(started) == {"started_at_utc", "request_sha256", "authorization_sha256"}
                and started["request_sha256"] == wire_hash and started["authorization_sha256"] == auth_binding["sha256"],
                "DeepInfra wire start binding differs")
        start = timestamp(started["started_at_utc"])
        require(authorized_start <= start < auth["deadline_epoch"], "DeepInfra wire started outside authorization")
        equal(event.get("attempt"), 1, "DeepInfra hidden wire attempts")
        require(event.get("wire_attempted") is True and not event.get("transport_error"), "DeepInfra wire evidence absent/failed")
        body = json.loads(event["response_body"])
        require(isinstance(body.get("choices"), list) and len(body["choices"]) == 1
                and isinstance(body["choices"][0].get("message"), dict), "DeepInfra response shape differs")
        choice = body["choices"][0]
        blocked = {"refusal", "content_filter", "safety", "prohibited_content", "blocklist"}
        require(not choice["message"].get("refusal") and all(str(choice.get(k, "")).lower() not in blocked
                for k in ("finish_reason", "native_finish_reason")), "DeepInfra observed refusal is not admitted")
        require(set(header) == {"http_status", "generation_id", "received_at_utc", "request_sha256"}
                and header["request_sha256"] == wire_hash, "DeepInfra header source differs")
        equal(header["http_status"], 200, "DeepInfra accepted header status differs")
        require(timestamp(header["received_at_utc"]) >= start and isinstance(body["id"], str) and body["id"]
                and header["generation_id"] == event.get("generation_id") == body["id"] == capture["generation_id"],
                "DeepInfra header generation/time join differs")
        use = body["usage"]
        require(type(use.get("cost")) in {int, float} and Decimal(str(use["cost"])).is_finite() and use["cost"] >= 0,
                "DeepInfra accepted billing is unknown/invalid")
        require(use["total_tokens"] > 0 and use["prompt_tokens"] <= configuration["context_length"]
                and use["completion_tokens"] <= 1024, "DeepInfra usage exceeds frozen bounds")
        known += Decimal(str(use["cost"]))
        group = {raw_path, *paths.values()}
        require(not seen & group, "DeepInfra duplicate receipt group")
        seen.update(group)
        if phase != "deepinfra:scored":
            probes.setdefault(phase, []).append((intent, evidence.bound(raw_path), group))
    equal(sorted(seen), sorted(name for name in evidence.bindings if str(Path(name).parent) == directory),
          "DeepInfra unaccounted receipt attempts/sidecars")
    old = auth["legacy_accounting"]
    for key in ("known_receipt_cost_usd", "unresolved_cost_reserved_usd", "budget_accounted_usd", "model_cap_usd"):
        require(type(old[key]) in {int, float} and Decimal(str(old[key])).is_finite() and old[key] >= 0,
                "DeepInfra invalid legacy liability")
    legacy_known, legacy_hold = Decimal(str(old["known_receipt_cost_usd"])), Decimal(str(old["unresolved_cost_reserved_usd"]))
    require(abs(legacy_known + legacy_hold - Decimal(str(old["budget_accounted_usd"]))) < Decimal("0.000000001"),
            "DeepInfra legacy liability sum differs")
    for item in manifest["legacy_receipts"]:
        require(str(Path(item["path"]).parent) == inventory.BASE + "/execution/receipts/" + model.replace("/", "--"),
                "DeepInfra legacy receipt directory differs")
        equal(item, evidence.bound(item["path"]), "DeepInfra legacy evidence missing/changed")
    for summary, expected in ((audit, {"new_known_receipt_cost_usd": known, "new_unknown_cost_reserved_usd": Decimal(0),
        "legacy_known_receipt_cost_usd": legacy_known, "legacy_unknown_cost_reserved_usd": legacy_hold}),
        (report, {})):
        expected = {**expected, "known_receipt_cost_usd": legacy_known + known, "unknown_cost_reserved_usd": legacy_hold,
                    "budget_accounted_usd": legacy_known + legacy_hold + known, "model_cap_usd": Decimal(15)}
        for key, value in expected.items():
            require(type(summary[key]) in {int, float} and Decimal(str(summary[key])).is_finite()
                    and abs(Decimal(str(summary[key])) - value) < Decimal("0.000000001"), "DeepInfra full receipt/hold billing closure differs")
    require(legacy_known + legacy_hold + known <= 15 and old["model_cap_usd"] == auth["cap_usd_including_legacy"] == 15,
            "DeepInfra original nontransferable cap differs")
    combined_path, structural_path, tool_path = (base + "/" + n for n in
        ("compatibility.json", "structural-compatibility.json", "tool-roundtrip-compatibility.json"))
    equal(report["probes"]["source"], evidence.bound(combined_path), "DeepInfra combined probe source differs")
    combined = evidence.load(evidence.bound(combined_path))
    equal(combined, {"schema": "deepinfra-combined-compatibility-v1", "authorization_sha256": auth_binding["sha256"],
        "transport_compatible": True, "scoring_weight": 0, "release_admitted": False,
        "sources": [evidence.bound(structural_path), evidence.bound(tool_path)]}, "DeepInfra combined probe authority differs")
    structural, tool = (evidence.load(evidence.bound(p)) for p in (structural_path, tool_path))
    for proof, expected_schema in ((structural, "deepinfra-controller-probe-v1"), (tool, "deepinfra-fresh-tool-roundtrip-v1")):
        require(proof["schema"] == expected_schema and proof["authorization_sha256"] == auth_binding["sha256"]
                and proof["transport_compatible"] is True and proof["score_correctness_tested"] is False,
                "DeepInfra probe scope differs")
        equal(proof["scoring_weight"], 0, "DeepInfra probe has scoring weight")
        equal(proof["reasoning_requested"], configuration["reasoning_override"], "DeepInfra probe reasoning differs")
        for source in proof["sources"]:
            equal(source, evidence.bound(source["path"]), "DeepInfra probe source binding differs")
    require(structural["reasoning_observed"] is True and tool["fresh_provider_reasoning_preserved"] is True,
            "DeepInfra genuine probe reasoning absent")
    structural_calls = probes.get("deepinfra:compatibility-zero-weight", [])
    tool_calls = sorted(probes.get("deepinfra:tool-probe-zero-weight", []), key=lambda v: v[0]["turn_index"])
    require(len(structural_calls) == 1 and len(tool_calls) == 2, "DeepInfra three-probe receipt inventory differs")
    intent, raw, _ = structural_calls[0]
    provenance = structural["provenance"]
    equal({k: intent[k] for k in ("condition_id", "repeat", "turn_index", "request_hash")},
        {"condition_id": "compatibility:" + provenance["original_condition_id"], "repeat": 606, "turn_index": 0,
         "request_hash": provenance["logical_request_hash"]}, "DeepInfra structural probe request differs")
    equal(structural["raw_receipt"], raw, "DeepInfra structural probe receipt differs")
    equal(tool["raw_receipts"], [raw for _, raw, _ in tool_calls], "DeepInfra tool probe receipts differ")
    equal({k: provenance[k] for k in ("configuration_id", "probe_provider", "source_provider", "scoring_weight")},
          {"configuration_id": inventory.DEEPINFRA_CONFIGURATION, "probe_provider": "DeepInfra",
           "source_provider": "DeepSeek", "scoring_weight": 0}, "DeepInfra historical probe provenance differs")
    for proof, calls in ((structural, structural_calls), (tool, tool_calls)):
        required = {name for _, _, group in calls for name in group
                    if any(name.endswith(s) for s in (".raw.json", ".pending.json", ".binding.json", ".wire-started.json"))}
        if proof is structural:
            for field in ("historical_source_used_only_for_probe", "original_failed_request"):
                source = provenance[field]
                equal(source, evidence.bound(source["path"]), "DeepInfra historical probe source differs")
                required.add(source["path"])
        names = [source["path"] for source in proof["sources"]]
        require(len(names) == len(set(names)) and required <= set(names), "DeepInfra probe receipt/source closure differs")
    for number, (intent, _, _) in enumerate(tool_calls):
        equal({k: intent[k] for k in ("condition_id", "repeat", "turn_index")},
            {"condition_id": "tool-roundtrip:deepinfra-fp8-high-v1", "repeat": 606, "turn_index": number},
            "DeepInfra tool roundtrip identity differs")


def verify_model_certificate(root: Path, model: str, *, report_path: str | None = None,
                             expected_report_sha256: str | None = None) -> dict:
    """Return a verified candidate record; no writes, locks, network, or admission.

    Approved reports are fixed internally. No caller-selected certificate or
    digest can substitute for candidate selection.
    """
    require(model in inventory.NEW_MODELS, "model outside final eight additions")
    ev = Evidence(root)
    reports, captures, audit, typed = [], [], None, None
    if model in LEGACY:
        require(report_path is None and expected_report_sha256 is None, "cannot override approved legacy certificate")
        name, internal_sha, tools_sha = LEGACY[model]
        directory = BASE + "/offline-harness-replay-v1/" + name + "-2026-10-07-a"
        typed_binding = binding(directory + "/typed-verification-v2.json", internal_sha)
        typed = ev.document(typed_binding, "panel-refresh-typed-harness-verification-v2", model)
        require(typed["canonical_typed_rows_equal"] is True and typed["canonical_typed_wires_equal"] is True, "typed verification missing")
        equal(typed["conditions"], 1240, "typed condition count differs")
        equal(typed["probe_requests"], 2, "typed probe scope differs")
        equal(typed["excluded_blocks"], {"toolsandbox": "96 conditions need a separate clock/UUID/environment provenance replay; not verified here."}, "typed excluded blocks differ")
        report_binding = ev.bound(directory + "/report.json")
        report = ev.document(report_binding, "panel-refresh-internal-harness-replay-v1", model)
        audit_binding = binding(directory + "/input-audit.json", report["input_audit_sha256"])
        audit = ev.document(audit_binding, "panel-refresh-recovered-completion-audit-v1", model)
        ev.many(audit["inputs"])
        require({(b["path"], b["sha256"]) for b in audit["inputs"]} <= {(b["path"], b["sha256"]) for b in report["inputs"]}, "nested audit inventory is not fully bound by replay")
        capture_binding = binding(directory + "/reconstructed-requests.json", report["requests_sha256"])
        captures.extend(ev.load(capture_binding))
        tool_directory = BASE + "/offline-toolsandbox-replay-v2/" + name + "-2026-10-07-a"
        tool_binding = binding(tool_directory + "/report.json", tools_sha)
        tool_report = ev.document(tool_binding, "panel-refresh-toolsandbox-saved-state-verification-v2", model)
        tool_captures = ev.load(binding(tool_directory + "/reconstructed-requests.json", tool_report["request_captures_sha256"]))
        equal(tool_report["requests"], len(tool_captures), "ToolSandbox capture count differs")
        equal(typed["requests"], len(captures), "typed capture count differs")
        captures.extend(tool_captures)
        tools, style, selected = tool_report["checked"], "legacy", None
        reports = [typed_binding, report_binding, tool_binding, audit_binding]
    else:
        if model in inventory.V5_REPORTS:
            require(report_path is None and expected_report_sha256 is None, "cannot override approved v6 certificate")
            path = inventory.V5_REPORTS[model]
            report_binding = binding(path, inventory.ANCHORS[path])
            expected_schema = "panel-refresh-v5-nine-block-offline-replay-v2"
            expected_audit = "panel-refresh-v5-nine-block-audit-v1"
            style = "v6"
        elif model == inventory.HAIKU:
            require(report_path is None and expected_report_sha256 is None, "cannot override approved Haiku certificate")
            report_binding = deepcopy(HAIKU_REPORT)
            expected_schema, style = FRESH[model]["schema"], "haiku"
            expected_audit = "panel-refresh-haiku-independent-audit-v1"
        else:
            policy = FRESH[model]
            require(report_path is None and expected_report_sha256 is None, "cannot override approved DeepInfra certificate")
            report_binding = deepcopy(DEEPINFRA_REPORT)
            expected_schema, style = policy["schema"], policy["campaign"]
            expected_audit = "deepinfra-full-inventory-audit-v1"
        directory = str(Path(report_binding["path"]).parent)
        report = ev.document(report_binding, expected_schema, model)
        audit = output_document(ev, directory, report, "input-audit.json")
        schema(audit, expected_audit, model)
        ev.many(audit["inputs"])
        require({(b["path"], b["sha256"]) for b in audit["inputs"]} <= {(b["path"], b["sha256"]) for b in report["inputs"]}, "nested audit inventory is not fully bound by replay")
        captures = output_document(ev, directory, report, "reconstructed-requests.json")
        tools = output_document(ev, directory, report, "toolsandbox-verification.json")
        selected = audit.get("selected")
        reports = [report_binding, ev.bound(directory + "/input-audit.json")]
        equal(report["conditions"], 1336, "full replay conditions differ")
        equal(report["internal_conditions"], 1240, "internal scope differs")
        equal(report["toolsandbox_conditions"], 96, "ToolSandbox scope differs")
        if style != "deepinfra":
            require(report["base_eight_request_score_replay"] == "verified", "internal request/score replay missing")
            equal(report["toolsandbox_official_regrades"], 95 if model == inventory.GEMINI else 96, "official regrade scope differs")
            equal(report["toolsandbox_frozen_zero_fallbacks"], 1 if model == inventory.GEMINI else 0, "fallback scope differs")
            ev.add(report["probe"])
        if style == "v6":
            require(report["verification_driver_version"] == "v6", "wrong selected-row replay version")
            output_document(ev, directory, report, "model-error-adjudications.json")
        if style == "haiku":
            require(report["canonical_typed_rows_equal"] is True and report["canonical_typed_wires_equal"] is True, "Haiku typed replay absent")
        if style == "deepinfra":
            equal(report["closure"], dict.fromkeys(("conditions", "requests", "scores", "billing", "probes"), True), "DeepInfra replay closure is incomplete")
            equal(audit["closure"], {"conditions": True, "requests": True, "billing": True, "probes": True, "scores": "pending_offline_replay"}, "DeepInfra audit role differs")
            require(report["environment_transitions_reexecuted"] is False, "unsupported transition replay claim")
            equal(report["toolsandbox_official_regrades"], 96, "DeepInfra grade scope differs")
            for field in ("manifest", "authorization", "protocol", "configuration_id", "configuration_sha256", "route", "source_blocks"):
                equal(report[field], audit["blocks" if field == "source_blocks" else field], "DeepInfra source/authority claims differ")
            equal(report["route"], {"provider": "DeepInfra", "tag": "deepinfra/fp8", "quantization": "fp8", "reasoning": "high", "data_collection": "allow"}, "DeepInfra route differs")
            require(report["configuration_id"] == inventory.DEEPINFRA_CONFIGURATION, "wrong DeepSeek configuration")
            ev.add(report["probes"]["source"])
            equal({k: report["probes"][k] for k in ("requests", "scoring_weight", "structural_controller_reconstructed", "fresh_two_turn_tool_roundtrip_reconstructed")},
                {"requests": 3, "scoring_weight": 0, "structural_controller_reconstructed": True, "fresh_two_turn_tool_roundtrip_reconstructed": True}, "DeepInfra probe reconstruction differs")
    require(report["network_disabled"] is True, "replay network boundary missing")
    equal(report["api_cost_usd"], 0, "replay API cost differs")
    require(report["verification_status"] == ("REPRODUCED_OFFLINE_FROM_FIXED_RESPONSES" if style == "legacy" else "REPRODUCED_OFFLINE_FROM_FIXED_RESPONSES_AND_SAVED_STATES"), "wrong verification scope")
    equal(report["ignored_row_paths" if style == "legacy" else "ignored_internal_row_paths"], IGNORED, "unreviewed replay normalization")
    equal(audit["completed_conditions"], 1336, "incomplete acquisition audit")
    if "conditions_by_block" in audit:
        equal(audit["conditions_by_block"], BLOCKS, "audit nine-block inventory differs")
    else:
        block_index(audit["blocks"], BLOCKS)
    plans, config, manifest = pinned_plans(ev, model)
    if model in FRESH:
        for name, digest in FRESH[model]["pins"].items():
            equal(ev.bound(name), binding(name, digest), "unpinned independent verifier/authority")
        if model == inventory.DEEPSEEK:
            equal(report["configuration_sha256"], equal_hash(config), "DeepInfra configuration digest differs")
    records = source_selection(ev, model, plans, selected)
    if style == "deepinfra":
        for name, item in block_index(report["source_blocks"], BLOCKS).items():
            entry = plans[name][0]
            equal(item["plan"], {k: entry[k] for k in ("path", "sha256")}, "DeepInfra source plan differs")
            equal(item["rows"], ev.bound(entry["output"]), "DeepInfra source rows differ")
            equal(item["statuses"], dict(Counter(r["row"]["status"] for r in records if r["block"] == name)),
                  "DeepInfra source status inventory differs")
        for name, item in block_index(report["blocks"], INTERNAL).items():
            equal(item["source_rows"], ev.bound(plans[name][0]["output"]), "DeepInfra internal replay source differs")
        expected_outputs = {directory + "/" + name for name in
            ("input-audit.json", "reconstructed-requests.json", "toolsandbox-verification.json", *[b + ".jsonl" for b in INTERNAL])}
        equal(sorted(b["path"] for b in report["outputs"]), sorted(expected_outputs), "DeepInfra replay output inventory differs")
    verify_internal(ev, directory, report["blocks"], records, style)
    if typed:
        for name, item in block_index(typed["blocks"], INTERNAL).items():
            equal(item["semantic_sha256"], block_index(report["blocks"], INTERNAL)[name]["semantic_sha256"], "typed report digest differs")
    verify_tools(tools, records, model, style)
    expected_probes = FRESH[model]["probes"] if model in FRESH else 2
    equal(report["probe_requests"], expected_probes, "replay probe count differs")
    count_key = "accepted_request_count" if style == "deepinfra" else "request_count"
    equal(report[count_key] + (tool_report["requests"] if style == "legacy" else 0), len(captures), "total reconstructed request count differs")
    scored_count = verify_captures(ev, captures, records, model, config, expected_probes)
    equal(audit["scored_response_count" if style in {"legacy", "deepinfra"} else "scored_responses"], scored_count, "audit scored response count differs")
    audit_billing(audit, report if style != "legacy" else None, model=model)
    if model == inventory.HAIKU:
        verify_haiku_receipt_joins(ev, captures, config, manifest, audit, report)
        receipt_prefix = inventory.HAIKU_BASE + "/execution/receipts/"
        for name in ev.bindings:
            if name.startswith(receipt_prefix) and name.endswith(".raw.json"):
                event = json.loads(inventory.resolve(ev.root, name).read_text())
                require(event.get("transport_error") != "OSError", "Haiku generic OSError provenance unresolved")
    if model == inventory.DEEPSEEK:
        verify_deepinfra_receipt_joins(ev, captures, config, manifest, audit, report)
        rows = {r["condition_id"]: r["row"] for r in records if r["block"] == "toolsandbox"}
        for item in tools:
            require(set(item) == {"condition_id", "official_grader_reevaluation", "environment_transitions_reexecuted",
                "score", "terminal_policy", "context", "conversation", "grader_timestamp"}
                and item["environment_transitions_reexecuted"] is False, "DeepInfra saved-state verification scope differs")
            cid = item["condition_id"]
            equal(item["grader_timestamp"], rows[cid]["started_at"], "DeepInfra authoritative grader timestamp differs")
            context_root = inventory.DEEPINFRA_BASE + "/execution/trajectories/" + model.replace("/", "--") + "/trajectories/" + cid
            for field, name in (("context", "execution_context.json"), ("conversation", "conversation.json")):
                equal(item[field], ev.bound(context_root + "/" + name), "DeepInfra saved-state source differs")
    ev.finish()
    sources = []
    for block, (entry, _) in plans.items():
        sources.append({"block": block, "conditions": BLOCKS[block],
            "plan": {k: entry[k] for k in ("path", "sha256")}, "plan_hash": entry["plan_hash"],
            "row_selection": [ev.bound(name) for name in sorted({r["journal"] for r in records if r["block"] == block})]})
    return {"schema": "panel-refresh-22-verified-model-candidate-v1", "model": model,
        "status": "model_certificate_verified_for_candidate_selection", "release_admitted": False,
        "conditions": 1336, "probe_requests": expected_probes, "reports": reports,
        "configuration_sha256": equal_hash(config), "configuration_id": config.get("configuration_id"),
        "manifest": ev.bound(inventory.MANIFEST_PATHS[FRESH[model]["campaign"] if model in FRESH else "original"]),
        "verified_sources": [binding(p, h) for p, h in sorted(ev.bindings.items())], "source_blocks": sources,
        "selected_rows": [{k: v for k, v in r.items() if k != "row"} for r in records],
        "scope": "Revalidation of fixed-response/saved-state certificates and typed artifact closure, not a fresh harness/tool-environment execution or whole-panel publication admission.",
        "new_api_calls": 0}
