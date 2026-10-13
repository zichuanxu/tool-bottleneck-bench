"""Offline, row-bound scientific costs for the eight new panel configurations.

The public builders first revalidate the model's complete certificate. They do
not accept a caller's ``passed`` flag or a candidate document instead. Prices
are applied to each unique accepted generation referenced by a selected row,
never to episode-aggregated tokens. Probes, failed attempts, legacy DeepSeek
charges and unknown-cost holds remain acquisition accounting, not this metric.
Nothing is written, no network is used, and no acquisition lock is acquired.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from decimal import Decimal, localcontext
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from types import MappingProxyType

from experiments import panel_refresh_22_certificates as certificates
from experiments import panel_refresh_22_pricing as pricing

ROOT = Path(__file__).resolve().parents[1]
BLOCKS = certificates.BLOCKS
ACCOUNTING = "normalized_scientific_text_tokens_no_cache_credit"
SCOPE = "text_only_no_provider_search"
TOKEN_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens")


class CostEvidenceError(ValueError):
    pass


def require(value, message):
    if not value:
        raise CostEvidenceError(message)


def same(left, right, message):
    require(pricing.stable_hash(left) == pricing.stable_hash(right), message)


def typed_int(value, name):
    require(type(value) is int and value >= 0, "untyped/negative integer: " + name)
    return value


def _json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(CostEvidenceError("nonfinite JSON")))


def row_identity(row):
    """Handle both frozen nested conditions and flat ToolSandbox records."""
    require(isinstance(row, dict), "row must be an object")
    condition = row.get("condition")
    nested = condition.get("model") if isinstance(condition, dict) else None
    model = row.get("model", nested)
    require(isinstance(model, str) and model in pricing.IDENTITIES, "foreign/missing row model")
    require(nested is None or nested == model, "conflicting row model identities")
    cid = row.get("condition_id")
    require(isinstance(cid, str) and re.fullmatch("[0-9a-f]{64}", cid), "invalid condition identity")
    return model, cid


def _usage(usage):
    require(isinstance(usage, dict), "missing token usage")
    values = {key: typed_int(usage.get(key), key) for key in TOKEN_KEYS}
    require(values["total_tokens"] == values["prompt_tokens"] + values["completion_tokens"], "token sum differs")
    return values


def _tool_calls(calls):
    require(isinstance(calls, list), "invalid local tool call list")
    for call in calls:
        require(isinstance(call, dict) and set(call) <= {"id", "type", "function", "index"}
                and call.get("type") == "function" and isinstance(call.get("id"), str),
                "unsupported provider-native tool or call metadata")
        if "index" in call:
            typed_int(call["index"], "tool_call.index")
        fn = call.get("function")
        # Invalid JSON arguments can be a scored model error. Do not execute or
        # repair them; their literal string still represents text-only usage.
        require(isinstance(fn, dict) and set(fn) == {"name", "arguments"}
                and isinstance(fn["name"], str) and isinstance(fn["arguments"], str), "nontext function call")


def _message(message, *, output=False):
    allowed = {"role", "content", "name", "tool_call_id", "tool_calls", "reasoning", "reasoning_content", "reasoning_details"}
    # The isolated DeepSeek tool probe preserves the provider's prior assistant
    # message verbatim, including refusal:null. That is text/null metadata,
    # not a new modality. It neither erases an observed refusal nor permits a
    # retry; certificate admission and the literal-wire binding remain separate.
    if output or isinstance(message, dict) and message.get("role") == "assistant":
        allowed.add("refusal")
    require(isinstance(message, dict) and set(message) <= allowed, "unsupported message dimension")
    require(message.get("role") in {"system", "developer", "user", "assistant", "tool"}, "unknown message role")
    require(message.get("content") is None or isinstance(message["content"], str), "nontext message content")
    for key in ("name", "tool_call_id", "reasoning", "reasoning_content"):
        require(message.get(key) is None or isinstance(message[key], str), "nontext message field")
    if "tool_calls" in message and message["tool_calls"] is not None:
        _tool_calls(message["tool_calls"])
    details = message.get("reasoning_details")
    if details is not None:
        require(isinstance(details, list), "unknown reasoning details")
        for detail in details:
            require(isinstance(detail, dict) and set(detail) <= {
                "type", "id", "index", "format", "text", "summary", "signature", "data"}
                and detail.get("type") in {"reasoning.text", "reasoning.summary", "reasoning.encrypted"},
                "unsupported reasoning modality")
    # A certificate-accepted refusal is an outcome, not an excuse to erase its
    # token cost or request another answer. Only its modality/type matters here.
    if "refusal" in message:
        require(message["refusal"] is None or isinstance(message["refusal"], str), "nontext refusal")


def derive_text_usage_scope(wire, body):
    """Prove the narrow supported usage scope; unknown dimensions fail closed.

    Harness-local function names containing 'search' are ordinary local tools,
    not provider web search. Native search/plugins, multimodal content and any
    nonzero image/audio/video usage are excluded. Cache and reasoning details
    are checked but do not grant cache credits or double-count output tokens.
    """
    allowed_wire = {"model", "messages", "tools", "tool_choice", "provider", "max_tokens", "temperature", "reasoning"}
    require(isinstance(wire, dict) and set(wire) <= allowed_wire, "unsupported wire dimension or provider search")
    require(isinstance(body, dict) and set(body) <= {"id", "object", "created", "model", "provider",
        "system_fingerprint", "service_tier", "choices", "usage", "error"}, "unsupported response dimension")
    require(isinstance(wire.get("messages"), list) and bool(wire["messages"]), "missing wire messages")
    for message in wire["messages"]:
        _message(message)
    tools = wire.get("tools", [])
    require(isinstance(tools, list), "invalid tool settings")
    for tool in tools:
        require(isinstance(tool, dict) and set(tool) == {"type", "function"} and tool["type"] == "function",
                "provider-native tool/search is unsupported")
        fn = tool["function"]
        require(isinstance(fn, dict) and set(fn) <= {"name", "description", "parameters", "strict"}
                and isinstance(fn.get("name"), str) and isinstance(fn.get("parameters"), dict), "unknown tool definition")
    choice = wire.get("tool_choice")
    require(choice is None or type(choice) is str and choice in {"auto", "none", "required"}, "unsupported tool choice")
    usage = body.get("usage")
    counts = _usage(usage)
    require(set(usage) <= set(TOKEN_KEYS) | {"prompt_tokens_details", "completion_tokens_details", "cost", "cost_details", "is_byok"},
            "unsupported usage dimension")
    detail_keys = {"prompt_tokens_details": {"cached_tokens", "cache_write_tokens", "audio_tokens", "video_tokens", "image_tokens"},
        "completion_tokens_details": {"reasoning_tokens", "audio_tokens", "image_tokens", "video_tokens"}}
    for field, allowed in detail_keys.items():
        details = usage.get(field, {})
        require(isinstance(details, dict) and set(details) <= allowed, "unknown token detail dimension")
        limit = counts["prompt_tokens" if field == "prompt_tokens_details" else "completion_tokens"]
        for name, value in details.items():
            require(typed_int(value, name) <= limit, "token detail exceeds total")
            if name in {"audio_tokens", "video_tokens", "image_tokens"}:
                require(value == 0, "nontext token usage")
    if "is_byok" in usage:
        require(type(usage["is_byok"]) is bool, "invalid billing metadata")
    billing = usage.get("cost_details", {})
    require(isinstance(billing, dict) and set(billing) <= {
        "upstream_inference_cost", "upstream_inference_prompt_cost", "upstream_inference_completions_cost"},
        "unknown billing dimension could include unsupported usage")
    for name, value in {**billing, **({"cost": usage["cost"]} if "cost" in usage else {})}.items():
        require(type(value) in {int, float} and math.isfinite(value) and value >= 0, "invalid separate billing metadata: " + name)
    require(isinstance(body.get("choices"), list) and len(body["choices"]) == 1, "ambiguous accepted response")
    for choice in body["choices"]:
        require(isinstance(choice, dict) and set(choice) <= {
            "index", "logprobs", "finish_reason", "native_finish_reason", "message"}, "unsupported response choice dimension")
        require(choice.get("finish_reason") in {"stop", "length", "tool_calls", "content_filter"}, "unsupported finish reason")
        _message(choice.get("message"), output=True)
    return SCOPE


class _BoundSources:
    def __init__(self, root, bindings):
        self.root, self.bindings = Path(root).resolve(), {}
        require(isinstance(bindings, list), "missing verified source inventory")
        for binding in bindings:
            require(isinstance(binding, dict) and set(binding) == {"path", "sha256"}, "invalid verified binding")
            name, digest = binding["path"], binding["sha256"]
            require(isinstance(name, str) and name not in self.bindings, "duplicate verified source")
            require(isinstance(digest, str) and re.fullmatch("[0-9a-f]{64}", digest), "invalid source digest")
            certificates.inventory.resolve(self.root, name)
            self.bindings[name] = digest

    def binding(self, name):
        require(name in self.bindings, "source is not certificate-bound: " + name)
        return {"path": name, "sha256": self.bindings[name]}

    def read(self, name):
        binding = self.binding(name)
        data = certificates.inventory.resolve(self.root, name).read_bytes()
        require(sha256(data).hexdigest() == binding["sha256"], "changed verified source: " + name)
        return data

    def load(self, name):
        return _json(self.read(name))

    def finish(self):
        for name in self.bindings:
            self.read(name)


def _phase_and_identity(intent, model):
    require(intent.get("model") == model, "pending model differs")
    require(type(intent.get("attempt")) is int and intent["attempt"] == 1, "scientific resample")
    typed_int(intent.get("turn_index"), "turn_index")
    require(intent.get("repeat") is None or type(intent["repeat"]) is int and intent["repeat"] >= 0, "untyped repeat")
    phase = intent.get("base_phase", intent.get("phase"))
    require(isinstance(phase, str), "missing pending phase")
    if model == pricing.HAIKU:
        normalized = {key: intent[key] for key in ("model", "condition_id", "repeat", "attempt", "turn_index", "request_hash")}
    else:
        normalized = {key: value for key, value in intent.items() if key != "unreconciled_liability_usd"}
        phase = re.sub(r"^deepinfra-technical:[1-5]:" if model == pricing.DEEPSEEK else
            r"^(?:authorized-recovery-v1:|technical-recovery-v2:[1-5]:)", "", phase)
    normalized["phase"] = phase
    allowed = {"deepinfra:scored", "deepinfra:compatibility-zero-weight", "deepinfra:tool-probe-zero-weight"} if model == pricing.DEEPSEEK else {"scored", "probe"}
    require(phase in allowed, "unknown scientific/technical phase")
    return phase, pricing.stable_hash(normalized)


def _accepted_capture(cap, event, intent, model, config, info):
    expected = {"generation_id", "logical_intent_hash", "raw", "raw_sha256", "request_hash", "wire_sha256"}
    if model == pricing.DEEPSEEK:
        expected.add("phase")
    require(isinstance(cap, dict) and set(cap) == expected, "unknown capture fields")
    phase, logical_hash = _phase_and_identity(intent, model)
    require(logical_hash == cap["logical_intent_hash"] and intent["request_hash"] == cap["request_hash"], "pending/capture request identity differs")
    require(type(event.get("attempt")) is int and event["attempt"] == 1 and type(event.get("http_status")) is int
            and event["http_status"] == 200 and not event.get("transport_error"), "unsuccessful accepted receipt")
    wire = event["request"]
    require(pricing.stable_hash(wire) == cap["wire_sha256"], "literal wire hash differs")
    body = _json(event["response_body"])
    require(isinstance(body, dict) and not body.get("error") and body.get("id") == cap["generation_id"]
            and event.get("generation_id") == body["id"] and isinstance(body["id"], str) and bool(body["id"]), "accepted generation provenance differs")
    require(body.get("model") in {model, info["canonical_slug"]} and body.get("provider") == info["provider_name"]
            and wire.get("model") == model, "served/requested model or provider differs")
    policy = {**config["provider"]}
    policy.setdefault("data_collection", "deny")
    actual = wire.get("provider")
    require(isinstance(actual, dict) and set(actual) == set(policy) | {"max_price"}, "selected route policy fields differ")
    same({k: v for k, v in actual.items() if k != "max_price"}, policy, "selected route policy differs")
    ceiling = actual["max_price"]
    require(isinstance(ceiling, dict) and set(ceiling) == {"prompt", "completion"}, "route price ceiling missing")
    for kind in ceiling:
        value = ceiling[kind]
        maximum = max(Decimal(band[kind + "_usd_per_million"]) for band in info["bands"])
        require(type(value) in {int, float} and math.isfinite(value)
                and abs(Decimal(str(value)) - maximum) <= Decimal("0.000000000001"), "route price ceiling differs")
    require(type(wire.get("max_tokens")) is int and wire["max_tokens"] == 1024, "completion cap differs")
    if config.get("temperature") is None:
        require("temperature" not in wire, "unsupported temperature override")
    else:
        # Frozen configuration uses integer 0; ModelRequest serializes 0.0.
        temperature = wire.get("temperature")
        require(type(temperature) in {int, float} and math.isfinite(temperature)
                and Decimal(str(temperature)) == Decimal(str(config["temperature"])), "temperature differs")
    if config.get("reasoning_override") is None:
        require("reasoning" not in wire, "unexpected reasoning override")
    else:
        same(wire.get("reasoning"), config["reasoning_override"], "reasoning configuration differs")
    scope = derive_text_usage_scope(wire, body)
    require(body["usage"]["completion_tokens"] <= wire["max_tokens"], "output usage exceeds frozen cap")
    return {"body": body, "intent": intent, "phase": phase, "scope": scope, "capture": cap}


def _response_ids(row, block):
    require(not ("response_ids" in row and "response_id" in row), "ambiguous row response fields")
    ids = row.get("response_ids", [row["response_id"]] if row.get("response_id") else [])
    require(isinstance(ids, list) and all(isinstance(rid, str) and rid for rid in ids)
            and len(ids) == len(set(ids)), "duplicate/invalid row response identities")
    require(ids or block == "primary_discovery" and row.get("status") == "capability_unavailable", "unexplained response-free row")
    return ids


def _sum_costs(values):
    values = list(values)
    with localcontext() as context:
        context.prec = max(28, max(map(len, values), default=1) + len(str(len(values))) + 8)
        return pricing.decimal_string(sum((Decimal(value) for value in values), Decimal(0)))


def _price_row(row, selection, receipts, catalog, info):
    model, cid = row_identity(row)
    require(model == info["model"] and cid == selection["condition_id"]
            and pricing.stable_hash(row) == selection["row_sha256"], "selected row identity/hash differs")
    ids = _response_ids(row, selection["block"])
    counts, quoted = Counter(), []
    # These are the exact frozen request-scope conventions, not interchangeable
    # notions of a seed/repeat: structural binds seed, ToolSandbox inference_seed.
    if selection["block"] == "structural":
        repeat = typed_int(row["condition"].get("seed"), "structural scope seed")
    elif selection["block"] == "toolsandbox":
        repeat = typed_int(row.get("inference_seed"), "ToolSandbox scope inference_seed")
    elif selection["block"] == "sentinel":
        repeat = typed_int(row["condition"].get("repeat"), "sentinel repeat")
    else:
        repeat = None
    for turn, rid in enumerate(ids):
        require(rid in receipts, "row references an unaccepted or missing generation")
        accepted = receipts[rid]
        body, intent = accepted["body"], accepted["intent"]
        require(intent["model"] == model and body["model"] in {model, info["canonical_slug"]}, "row/receipt model differs")
        require(accepted["phase"] in {"scored", "deepinfra:scored"} and intent["condition_id"] == cid, "row references a probe/foreign condition")
        same(intent["turn_index"], turn, "request turn order differs")
        same(intent.get("repeat"), repeat, "request repeat differs")
        counts.update(_usage(body["usage"]))
        quote = catalog.quote_request(model, configuration_sha256=info["configuration_sha256"], provider_tag=info["provider_tag"],
            prompt_tokens=body["usage"]["prompt_tokens"], completion_tokens=body["usage"]["completion_tokens"], usage_scope=accepted["scope"])
        cap = accepted["capture"]
        quoted.append({"request_id": rid, "turn_index": turn, "logical_intent_hash": cap["logical_intent_hash"],
            "request_hash": cap["request_hash"], "wire_sha256": cap["wire_sha256"],
            "raw": {"path": cap["raw"], "sha256": cap["raw_sha256"]}, "pending": accepted["pending"],
            "usage": _usage(body["usage"]), "usage_scope": accepted["scope"], "applied_band": quote["applied_band"],
            "normalized_cost_usd": quote["normalized_cost_usd"]})
    same(_usage(row.get("usage")), {key: counts[key] for key in TOKEN_KEYS}, "selected row/raw typed token sum differs")
    if "requests" in row["usage"]:
        same(row["usage"]["requests"], len(ids), "row usage request count differs")
    return {"model": model, **{key: selection[key] for key in (
        "block", "condition_id", "journal", "row_index", "literal_line_sha256", "row_sha256")},
        "normalized_cost_usd": _sum_costs(q["normalized_cost_usd"] for q in quoted), "request_ids": list(ids), "requests": quoted}


def _costs_from_verified_candidate(root, candidate, catalog):
    """Private evidence adapter; public entry always obtains candidate internally."""
    model = candidate["model"]
    require(candidate.get("schema") == "panel-refresh-22-verified-model-candidate-v1"
            and candidate.get("status") == "model_certificate_verified_for_candidate_selection"
            and candidate.get("release_admitted") is False, "not a verified candidate schema")
    same(candidate.get("conditions"), sum(BLOCKS.values()), "incomplete selected model")
    info = catalog.describe(model)
    require(candidate["configuration_sha256"] == info["configuration_sha256"]
            and candidate.get("configuration_id") == info["configuration_id"], "certificate/pricing configuration differs")
    sources = _BoundSources(root, candidate["verified_sources"])
    same(sources.binding(info["snapshot"]["path"]), info["snapshot"], "price snapshot not certificate-bound")
    snapshot = sources.load(info["snapshot"]["path"])
    configs = [snapshot["configuration"]] if info["campaign"] == "deepinfra" else snapshot["configurations"]
    configs = [config for config in configs if config["model"] == model]
    require(len(configs) == 1 and pricing.stable_hash(configs[0]) == info["configuration_sha256"], "pricing configuration changed")
    config = configs[0]
    same(sources.binding(candidate["manifest"]["path"]), candidate["manifest"], "manifest not bound")
    capture_paths = sorted({str(Path(report["path"]).parent / "reconstructed-requests.json")
        for report in candidate["reports"] if str(Path(report["path"]).parent / "reconstructed-requests.json") in sources.bindings})
    require(bool(capture_paths), "missing report-bound request reconstruction")
    receipts, logical_ids = {}, set()
    campaign_root = certificates.inventory.HAIKU_BASE if model == pricing.HAIKU else certificates.inventory.DEEPINFRA_BASE if model == pricing.DEEPSEEK else certificates.inventory.BASE
    receipt_dir = campaign_root + "/execution/receipts/" + model.replace("/", "--")
    for name in capture_paths:
        captures = sources.load(name)
        require(isinstance(captures, list) and bool(captures), "missing accepted capture inventory")
        for cap in captures:
            require(str(Path(cap["raw"]).parent) == receipt_dir and cap["raw"].endswith(".raw.json"), "foreign/legacy receipt source")
            same(sources.binding(cap["raw"]), {"path": cap["raw"], "sha256": cap["raw_sha256"]}, "raw is not capture-bound")
            pending = cap["raw"][:-len(".raw.json")] + ".pending.json"
            accepted = _accepted_capture(cap, sources.load(cap["raw"]), sources.load(pending), model, config, info)
            rid = cap["generation_id"]
            require(rid not in receipts and cap["logical_intent_hash"] not in logical_ids, "duplicate accepted generation/request")
            logical_ids.add(cap["logical_intent_hash"])
            accepted["pending"] = sources.binding(pending)
            receipts[rid] = accepted
    selected = candidate["selected_rows"]
    require(isinstance(selected, list) and Counter(r["block"] for r in selected) == Counter(BLOCKS)
            and len({r["condition_id"] for r in selected}) == len(selected), "incomplete/duplicate selected inventory")
    journals, rows, consumed = {}, [], set()
    for selection in selected:
        name = selection["journal"]
        if name not in journals:
            journals[name] = sources.read(name).splitlines(keepends=True)
        index = typed_int(selection["row_index"], "row_index")
        require(index < len(journals[name]), "selected line absent")
        line = journals[name][index]
        require(sha256(line).hexdigest() == selection["literal_line_sha256"], "literal selected row changed")
        priced = _price_row(_json(line), selection, receipts, catalog, info)
        require(not consumed.intersection(priced["request_ids"]), "generation used in multiple selected rows")
        consumed.update(priced["request_ids"])
        rows.append(priced)
    scored = {rid for rid, accepted in receipts.items() if accepted["phase"] in {"scored", "deepinfra:scored"}}
    require(consumed == scored, "selected row/accepted scored generation closure differs")
    probes = sorted(set(receipts) - scored)
    same(len(probes), candidate["probe_requests"], "excluded probe count differs")
    sources.finish()
    inputs = dict(sources.bindings)
    for path, digest in pricing.SNAPSHOTS.values():
        same(certificates.inventory.bind(Path(root), path), {"path": path, "sha256": digest}, "pricing catalog changed")
        require(path not in inputs or inputs[path] == digest, "conflicting catalog binding")
        inputs[path] = digest
    result = {"schema": "panel-refresh-22-scientific-model-costs-v1", "model": model, "release_admitted": False,
        "accounting_basis": ACCOUNTING, "billed_spending_or_budget_hold_accounting": False,
        "retained_fourteen_price_policy": "outside_this_adapter_unchanged_historical_price_sources",
        "price_schedule": info, "manifest": deepcopy(candidate["manifest"]),
        "configuration_key": pricing.stable_hash({"model": model, "manifest": candidate["manifest"], "configuration": config}),
        "model_certificate_sha256": pricing.stable_hash(candidate), "reports": deepcopy(candidate["reports"]),
        "conditions": len(rows), "request_count": len(consumed), "excluded_probe_request_ids": probes,
        "normalized_cost_usd": _sum_costs(row["normalized_cost_usd"] for row in rows),
        "rows": rows, "inputs": [{"path": path, "sha256": digest} for path, digest in sorted(inputs.items())],
        "new_api_calls": 0, "scope": "Selected-row normalized token cost only; no billed-spending, wall-time, fresh replay or publication claim."}
    result["artifact_hash"] = pricing.stable_hash(result)
    return result


def build_model_costs(root: Path, model: str) -> dict:
    """Revalidate one full model then return its cost artifact without writing."""
    require(model in pricing.IDENTITIES, "only the eight new configurations belong in this adapter")
    try:
        candidate = certificates.verify_model_certificate(Path(root), model)
    except ValueError as exc:
        if model in certificates.FRESH:
            raise certificates.inventory.AdmissionPending("Fresh Haiku/DeepInfra costs remain blocked until their full model certificate adapter is admitted") from exc
        raise
    return _costs_from_verified_candidate(root, candidate, pricing.load_catalog(Path(root)))


def _lookup_index(artifacts):
    index, models, request_ids = {}, set(), set()
    for artifact in artifacts:
        model = artifact["model"]
        require(model in pricing.IDENTITIES and model not in models, "duplicate/foreign lookup model")
        require(artifact.get("schema") == "panel-refresh-22-scientific-model-costs-v1"
                and artifact.get("accounting_basis") == ACCOUNTING and artifact.get("release_admitted") is False,
                "invalid lookup artifact scope")
        same(artifact["artifact_hash"], pricing.stable_hash({k: v for k, v in artifact.items() if k != "artifact_hash"}), "lookup artifact changed")
        models.add(model)
        for row in artifact["rows"]:
            key = (model, row["condition_id"])
            require(row["model"] == model and key not in index, "duplicate/foreign lookup row")
            require(not request_ids.intersection(row["request_ids"]), "duplicate generation in lookup")
            request_ids.update(row["request_ids"])
            value = Decimal(row["normalized_cost_usd"])
            require(value.is_finite() and value >= 0 and math.isfinite(float(value)), "invalid normalized cost")
            index[key] = (row["row_sha256"], str(value))
    require(bool(models), "empty model cost lookup")
    return frozenset(models), MappingProxyType(index)


class RowCostLookup:
    """Immutable consumer interface; construction always verifies certificates.

    ``models`` is a frozenset. Missing/foreign/tampered rows raise ValueError;
    consumers must never catch that error and silently use a flat-price fallback.
    The fourteen retained configurations remain outside ``models``.
    """
    def __init__(self, root: Path, models):
        requested = list(models)
        require(bool(requested) and len(requested) == len(set(requested)), "empty/duplicate requested models")
        artifacts = [build_model_costs(root, model) for model in requested]
        self._models, self._index = _lookup_index(artifacts)
        self._artifacts_json = json.dumps(artifacts, ensure_ascii=False, allow_nan=False, sort_keys=True)

    @property
    def models(self):
        return self._models

    @property
    def artifacts(self):
        return _json(self._artifacts_json)

    def exact_cost_for_row(self, row) -> str:
        key = row_identity(row)
        require(key in self._index, "missing/foreign selected row in scientific cost lookup")
        digest, value = self._index[key]
        require(pricing.stable_hash(row) == digest, "selected row changed since scientific costing")
        return value

    def cost_for_row(self, row) -> float:
        return float(self.exact_cost_for_row(row))


def build_cost_lookup(root: Path, models) -> RowCostLookup:
    return RowCostLookup(root, models)
