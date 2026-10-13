"""Pinned, offline scientific price adapter for eight October configurations.

This prices text-only accepted model requests at their selected route's prompt
and completion rates, without cache credits or cache-write surcharges. It is a
normalized scientific metric, NOT provider billing or budget accounting. Old
charges, rejected/technical attempts, probes and unknown-cost holds belong in a
separate acquisition ledger. The caller must select independently certified
scored requests and establish text-only/no-provider-search usage before calling.

Prompt-length bands apply to the entire request, including its completion, at
the inclusive ``min_prompt_tokens`` boundary. Never sum episode tokens before
selecting each request's band. Public pricing ``discount`` is metadata: published
effective rates are used directly, not discounted a second time.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from hashlib import sha256
import json
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
MILLION = Decimal(1_000_000)
HAIKU = "anthropic/claude-haiku-5.5"
DEEPSEEK = "deepseek/deepseek-v4.1-flash"
# Same literal commitments as build_panel_refresh_22.PRICE_PATHS / ANCHORS.
# No caller-provided checksum or alternate route can replace these sources.
SNAPSHOTS = {
    "original": ("configs/panel-refresh-2026-10-07-pricing.json", "14a30baddbe65acea95f8053a81c368627c1b42f0dbcb60a5dbcccb677274f79"),
    "haiku": ("results/local-only-panel-refresh-haiku-2026-10-08/pricing.json", "11f0b8caa062b003e7693b789c3a17193a0fd521f36607213684b57c39e5ae21"),
    "deepinfra": ("results/local-only-panel-refresh-deepinfra-2026-10-08/pricing.json", "1443e3ddcedc19c746e12112e542d2df5082186da3c040a6b00731f71c9968a1"),
}
IDENTITIES = {
    "anthropic/claude-opus-5.5": ("original", "anthropic", "Anthropic"),
    "anthropic/claude-sonnet-5.5": ("original", "anthropic", "Anthropic"),
    "qwen/qwen3.8-max-0902": ("original", "alibaba", "Alibaba"),
    "google/gemini-3.8-flash": ("original", "google-ai-studio", "Google AI Studio"),
    "openai/gpt-6-luna": ("original", "openai", "OpenAI"),
    "openai/gpt-6.1-sol": ("original", "openai", "OpenAI"),
    HAIKU: ("haiku", "anthropic", "Anthropic"),
    DEEPSEEK: ("deepinfra", "deepinfra/fp8", "DeepInfra"),
}
TOKEN_FIELDS = frozenset({"prompt", "completion", "input_cache_read", "input_cache_write", "input_cache_write_1h"})
# Retain these native fields without inventing their unit or applying them to
# text-only requests. A non-text/search request is outside this adapter's scope.
INACTIVE_FIELDS = frozenset({"audio", "image", "input_audio_cache", "web_search"})
RECOGNIZED_FIELDS = TOKEN_FIELDS | INACTIVE_FIELDS | {"discount", "internal_reasoning", "request", "overrides"}


class PricingError(ValueError):
    pass


def require(value, message):
    if not value:
        raise PricingError(message)


def stable_hash(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def decimal_string(value: Decimal) -> str:
    result = format(value, "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def _rate(value, name):
    require(type(value) in (str, int, float), "invalid price type: " + name)
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise PricingError("invalid price: " + name) from exc
    require(number.is_finite() and number >= 0, "negative/nonfinite price: " + name)
    return number


def _tokens(value, name):
    require(type(value) is int and value >= 0, "token usage must be a nonnegative integer: " + name)
    return value


@dataclass(frozen=True)
class PriceTier:
    min_prompt_tokens: int
    rates: tuple[tuple[str, Decimal], ...]

    def describe(self):
        rates = dict(self.rates)
        return {"min_prompt_tokens": self.min_prompt_tokens, "boundary": "inclusive",
            "prompt_usd_per_million": decimal_string(rates["prompt"] * MILLION),
            "completion_usd_per_million": decimal_string(rates["completion"] * MILLION),
            "cache_price_fields_native": {k: decimal_string(v) for k, v in self.rates if k.startswith("input_cache_")},
            "cache_policy": "preserved_native_metadata_not_applied_to_normalized_cost"}


@dataclass(frozen=True)
class PriceSchedule:
    model: str
    canonical_slug: str
    campaign: str
    provider_tag: str
    provider_name: str
    configuration_sha256: str
    selected_route_sha256: str
    configuration_id: str | None
    snapshot_path: str
    snapshot_sha256: str
    context_length: int
    tiers: tuple[PriceTier, ...]
    raw_pricing_json: str
    inactive_json: str

    def bindings(self):
        return {"snapshot": {"path": self.snapshot_path, "sha256": self.snapshot_sha256},
            "configuration_sha256": self.configuration_sha256, "selected_route_sha256": self.selected_route_sha256,
            "configuration_id": self.configuration_id, "provider_tag": self.provider_tag,
            "provider_name": self.provider_name, "canonical_slug": self.canonical_slug}

    def describe(self):
        return {"model": self.model, "campaign": self.campaign, **self.bindings(), "context_length": self.context_length,
            "bands": [tier.describe() for tier in self.tiers], "raw_route_pricing": json.loads(self.raw_pricing_json),
            "inactive_native_dimensions": json.loads(self.inactive_json),
            "accounting_basis": "normalized_scientific_text_tokens_no_cache_credit",
            "discount_policy": "published_effective_rates_no_second_discount",
            "reasoning_policy": "completion_tokens_already_include_reported_reasoning_tokens_no_double_count",
            "billed_spending_or_budget_hold_accounting": False}


def _normalize(config: dict, campaign: str) -> PriceSchedule:
    model = config["model"]
    require(model in IDENTITIES and IDENTITIES[model][0] == campaign, "model/configuration is outside selected campaign")
    _, tag, provider = IDENTITIES[model]
    route = config["selected_route"]
    require(route["model_id"] == model and route["tag"] == tag and route["provider_name"] == provider,
            "selected route identity differs")
    policy = config["provider"]
    require(policy.get("only") == [tag] and policy.get("order") == [tag] and policy.get("allow_fallbacks") is False,
            "provider route is not pinned")
    context = config["context_length"]
    require(type(context) is int and context > 0 and route["context_length"] == context, "context bound differs")
    if model == DEEPSEEK:
        require(config.get("configuration_id") == "deepseek-v4.1-flash__openrouter-deepinfra-fp8-high__2026-10-08"
                and route.get("quantization") == "fp8" and policy.get("quantizations") == ["fp8"]
                and policy.get("data_collection") == "allow" and config.get("reasoning_override") == {"enabled": True, "effort": "high"},
                "original or different DeepSeek configuration cannot enter scientific repricing")
    pricing = route["pricing"]
    require(isinstance(pricing, dict) and set(pricing) <= RECOGNIZED_FIELDS, "unsupported price dimension")
    require({"prompt", "completion"} <= set(pricing), "prompt/output price missing")
    base = {k: _rate(v, k) for k, v in pricing.items() if k in TOKEN_FIELDS}
    require(_rate(pricing.get("request", 0), "request") == 0, "nonzero per-request fee is unsupported")
    discount = _rate(pricing.get("discount", 0), "discount")
    require(discount <= 1, "invalid effective-price discount metadata")
    for key in INACTIVE_FIELDS & set(pricing):
        _rate(pricing[key], key)
    if "internal_reasoning" in pricing:
        require(_rate(pricing["internal_reasoning"], "internal_reasoning") == base["completion"],
                "different internal-reasoning tariff needs a separate validated accounting adapter")
    overrides = pricing.get("overrides", [])
    require(isinstance(overrides, list), "overrides must be a list")
    tiers = [PriceTier(0, tuple(sorted(base.items())))]
    for override in overrides:
        require(isinstance(override, dict) and set(override) <= TOKEN_FIELDS | {"min_prompt_tokens"}
                and "min_prompt_tokens" in override, "unsupported temporal, modality or price-band condition")
        threshold = _tokens(override["min_prompt_tokens"], "min_prompt_tokens")
        require(tiers[-1].min_prompt_tokens < threshold <= context, "nonincreasing/duplicate/out-of-context threshold")
        # Endpoint overrides are partial overrides of the base rate object, not
        # cumulative changes to another band. Cache dimensions stay explicit.
        rates = {**base, **{k: _rate(v, k) for k, v in override.items() if k in TOKEN_FIELDS}}
        tiers.append(PriceTier(threshold, tuple(sorted(rates.items()))))
    path, digest = SNAPSHOTS[campaign]
    return PriceSchedule(model, config["canonical_slug"], campaign, tag, provider, stable_hash(config), stable_hash(route),
        config.get("configuration_id"), path, digest, context, tuple(tiers), json.dumps(pricing, sort_keys=True),
        json.dumps({k: pricing[k] for k in sorted(INACTIVE_FIELDS & set(pricing))}, sort_keys=True))


class PricingCatalog:
    """Immutable schedules loaded only through ``load_catalog`` in production."""
    def __init__(self, schedules: Mapping[str, PriceSchedule]):
        require(set(schedules) == set(IDENTITIES), "not the eight selected October configurations")
        self._schedules = MappingProxyType(dict(schedules))

    def describe(self, model: str) -> dict:
        require(model in self._schedules, "unsupported selected model")
        return self._schedules[model].describe()

    def quote_request(self, model: str, *, configuration_sha256: str, provider_tag: str,
                      prompt_tokens: int, completion_tokens: int, usage_scope: str) -> dict:
        """Price one already-certified text-only request; no billing inputs accepted.

        ``usage_scope`` must explicitly be ``text_only_no_provider_search``.
        Passing a scope is an adapter contract, not independent receipt proof.
        Caller must verify it against the certified wire and full usage evidence.
        """
        require(model in self._schedules, "unsupported selected model")
        schedule = self._schedules[model]
        require(configuration_sha256 == schedule.configuration_sha256 and provider_tag == schedule.provider_tag,
                "request pricing configuration/route binding differs")
        require(usage_scope == "text_only_no_provider_search", "nontext/search/unknown usage scope is unsupported")
        prompt = _tokens(prompt_tokens, "prompt_tokens")
        completion = _tokens(completion_tokens, "completion_tokens")
        require(prompt <= schedule.context_length, "prompt usage exceeds bound route context")
        tier = max((band for band in schedule.tiers if prompt >= band.min_prompt_tokens), key=lambda band: band.min_prompt_tokens)
        rates = dict(tier.rates)
        # Do not depend on a caller's Decimal context (the default precision is
        # only 28). Preserve exact arithmetic even for synthetic large counts.
        with localcontext() as context:
            context.prec = max(28, len(str(prompt)) + len(str(completion)) + 8 + sum(
                len(rates[k].as_tuple().digits) + abs(rates[k].as_tuple().exponent) for k in ("prompt", "completion")))
            input_cost, output_cost = Decimal(prompt) * rates["prompt"], Decimal(completion) * rates["completion"]
            total = input_cost + output_cost
        return {"schema": "panel-refresh-scientific-request-price-v1", "model": model, **schedule.bindings(),
            "usage": {"prompt_tokens": prompt, "completion_tokens": completion}, "usage_scope": usage_scope,
            "accounting_basis": "normalized_scientific_text_tokens_no_cache_credit", "applied_band": tier.describe(),
            "normalized_prompt_cost_usd": decimal_string(input_cost), "normalized_completion_cost_usd": decimal_string(output_cost),
            "normalized_cost_usd": decimal_string(total), "cache_credit_applied": False,
            "cache_write_surcharge_applied": False, "billed_spending_or_budget_hold_accounting": False,
            "evidence_scope": "price_arithmetic_only_not_receipt_validation_or_release_admission"}

    def quote_requests(self, model: str, *, configuration_sha256: str, provider_tag: str,
                       requests: Iterable[Mapping], usage_scope: str) -> dict:
        """Sum per-request quotes, never select bands using aggregate tokens."""
        quotes, seen = [], set()
        for request in requests:
            require(isinstance(request, Mapping) and set(request) == {"request_id", "prompt_tokens", "completion_tokens"},
                    "request inventory requires only identity and prompt/completion counts; billing/cache fields are not inputs")
            rid = request["request_id"]
            require(isinstance(rid, str) and bool(rid) and rid not in seen, "missing or duplicate request identity")
            seen.add(rid)
            quote = self.quote_request(model, configuration_sha256=configuration_sha256, provider_tag=provider_tag,
                prompt_tokens=request["prompt_tokens"], completion_tokens=request["completion_tokens"], usage_scope=usage_scope)
            quotes.append({"request_id": rid, **quote})
        # Even an empty capability-unavailable episode must bind a real schedule.
        self.quote_request(model, configuration_sha256=configuration_sha256, provider_tag=provider_tag,
                           prompt_tokens=0, completion_tokens=0, usage_scope=usage_scope)
        with localcontext() as context:
            context.prec = max(28, max((len(q["normalized_cost_usd"]) for q in quotes), default=1) + len(str(len(quotes))) + 8)
            total = sum((Decimal(q["normalized_cost_usd"]) for q in quotes), Decimal(0))
        return {"schema": "panel-refresh-scientific-request-sum-v1", "model": model,
            "accounting_basis": "normalized_scientific_text_tokens_no_cache_credit", "request_count": len(quotes),
            "normalized_cost_usd": decimal_string(total),
            "requests": quotes, "billed_spending_or_budget_hold_accounting": False}


def load_catalog(root: Path = ROOT) -> PricingCatalog:
    """Read only the three pinned local snapshots; never call a provider API."""
    root = Path(root).resolve()
    snapshots = {}
    for campaign, (relative, expected) in SNAPSHOTS.items():
        path = (root / relative).resolve()
        require(path.is_relative_to(root), "snapshot symlink escapes repository")
        raw = path.read_bytes()
        require(sha256(raw).hexdigest() == expected, "pinned pricing snapshot changed: " + campaign)
        value = json.loads(raw)
        body = dict(value)
        require(body.pop("artifact_hash") == stable_hash(body), "pricing self-hash differs")
        snapshots[campaign] = value
    schedules = {}
    for model, (campaign, _, _) in IDENTITIES.items():
        snapshot = snapshots[campaign]
        configs = [snapshot["configuration"]] if campaign == "deepinfra" else snapshot["configurations"]
        matches = [c for c in configs if c.get("model") == model]
        require(len(matches) == 1, "ambiguous/missing selected price configuration")
        schedules[model] = _normalize(matches[0], campaign)
    return PricingCatalog(schedules)
