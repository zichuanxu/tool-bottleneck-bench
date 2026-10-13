"""Token-complete cost accounting for the pinned OpenRouter replication."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Mapping, Sequence

from bottleneckbench.core.schema import JSON


EXPECTED_REPLICATION_ROWS = {
    "development": 1200,
    "main": 3168,
    "transfer": 288,
}


def _schedules(snapshot: Mapping[str, object]) -> dict[str, JSON]:
    selected = snapshot.get("selected")
    if snapshot.get("prices_are_usd_per_token") is not True or not isinstance(
        selected, list
    ):
        raise ValueError("endpoint snapshot does not contain per-token prices")
    schedules: dict[str, JSON] = {}
    for raw in selected:
        if not isinstance(raw, dict):
            raise ValueError("endpoint price entry must be an object")
        model = raw.get("requested_model")
        if not isinstance(model, str) or not model or model in schedules:
            raise ValueError("endpoint snapshot has an invalid model identity")
        base_prompt = raw.get("prompt_price")
        base_completion = raw.get("completion_price")
        if not isinstance(base_prompt, (int, float)) or not isinstance(
            base_completion, (int, float)
        ):
            raise ValueError(f"endpoint snapshot lacks prices for {model}")
        tiers: list[JSON] = [
            {
                "minimum_prompt_tokens": 0,
                "prompt_price": float(base_prompt),
                "completion_price": float(base_completion),
            }
        ]
        overrides = raw.get("prompt_overrides", [])
        if not isinstance(overrides, list):
            raise ValueError("prompt_overrides must be a list")
        for override in overrides:
            if not isinstance(override, dict):
                raise ValueError("prompt price override must be an object")
            minimum = override.get("minimum_prompt_tokens")
            prompt_price = override.get("prompt_price")
            completion_price = override.get("completion_price")
            if (
                not isinstance(minimum, int)
                or isinstance(minimum, bool)
                or minimum <= 0
                or not isinstance(prompt_price, (int, float))
                or not isinstance(completion_price, (int, float))
            ):
                raise ValueError("prompt price override is malformed")
            tiers.append(
                {
                    "minimum_prompt_tokens": minimum,
                    "prompt_price": float(prompt_price),
                    "completion_price": float(completion_price),
                }
            )
        tiers.sort(key=lambda item: int(item["minimum_prompt_tokens"]))
        thresholds = [int(item["minimum_prompt_tokens"]) for item in tiers]
        if len(thresholds) != len(set(thresholds)) or any(
            float(item["prompt_price"]) < 0
            or float(item["completion_price"]) < 0
            for item in tiers
        ):
            raise ValueError("endpoint price tiers are invalid")
        schedules[model] = {
            "served_model": raw.get("served_model"),
            "provider_tag": raw.get("provider_tag"),
            "tiers": tiers,
        }
    return schedules


def price_for_usage(
    schedule: Mapping[str, object], *, prompt_tokens: int, completion_tokens: int
) -> JSON:
    if prompt_tokens < 0 or completion_tokens < 0:
        raise ValueError("token counts must be nonnegative")
    tiers = schedule.get("tiers")
    if not isinstance(tiers, list) or not tiers:
        raise ValueError("model price schedule has no tiers")
    applicable = [
        tier
        for tier in tiers
        if isinstance(tier, dict)
        and int(tier.get("minimum_prompt_tokens", -1)) <= prompt_tokens
    ]
    if not applicable:
        raise ValueError("no price tier applies to row")
    tier = applicable[-1]
    prompt_price = float(tier["prompt_price"])
    completion_price = float(tier["completion_price"])
    return {
        "minimum_prompt_tokens": int(tier["minimum_prompt_tokens"]),
        "prompt_price_per_token": prompt_price,
        "completion_price_per_token": completion_price,
        "estimated_cost_usd": (
            prompt_tokens * prompt_price + completion_tokens * completion_price
        ),
    }


def analyze_replication_cost(
    rows_by_split: Mapping[str, Sequence[Mapping[str, object]]],
    endpoint_snapshot: Mapping[str, object],
    *,
    expected_rows: Mapping[str, int] = EXPECTED_REPLICATION_ROWS,
) -> JSON:
    schedules = _schedules(endpoint_snapshot)
    selected = endpoint_snapshot.get("selected")
    optional_usage_models = {
        str(item.get("requested_model"))
        for item in selected or []
        if isinstance(item, dict)
        and isinstance(item.get("usage_sources"), list)
        and "stream_usage_missing" in item["usage_sources"]
    }
    if set(rows_by_split) != set(expected_rows):
        raise ValueError("cost analysis split set does not match the frozen protocol")
    all_ids: set[str] = set()
    split_reports: JSON = {}
    overall: dict[str, JSON] = defaultdict(
        lambda: {
            "rows": 0,
            "rows_with_token_telemetry": 0,
            "model_responses": 0,
            "responses_with_token_telemetry": 0,
            "responses_missing_usage": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "estimated_cost_usd": 0.0,
            "price_tier_rows": Counter(),
        }
    )
    expected_models = set(schedules)
    for split in expected_rows:
        rows = rows_by_split[split]
        if len(rows) != int(expected_rows[split]):
            raise ValueError(
                f"{split} row denominator changed: {len(rows)} != {expected_rows[split]}"
            )
        by_model: dict[str, JSON] = defaultdict(
            lambda: {
                "rows": 0,
                "rows_with_token_telemetry": 0,
                "model_responses": 0,
                "responses_with_token_telemetry": 0,
                "responses_missing_usage": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "estimated_cost_usd": 0.0,
                "price_tier_rows": Counter(),
            }
        )
        for row in rows:
            condition_id = row.get("condition_id")
            if not isinstance(condition_id, str) or not condition_id:
                raise ValueError("cost row is missing condition_id")
            if condition_id in all_ids:
                raise ValueError("cost input contains duplicate condition IDs")
            all_ids.add(condition_id)
            condition = row.get("condition")
            usage = row.get("usage")
            if not isinstance(condition, dict) or not isinstance(usage, dict):
                raise ValueError("cost row is missing condition or usage")
            model = condition.get("model")
            prompt = usage.get("prompt_tokens")
            completion = usage.get("completion_tokens")
            if model not in schedules:
                raise ValueError(f"cost row uses an unregistered model: {model}")
            if (
                not isinstance(prompt, int)
                or isinstance(prompt, bool)
                or not isinstance(completion, int)
                or isinstance(completion, bool)
            ):
                raise ValueError("cost row has incomplete integer token telemetry")
            response_ids = row.get("response_ids")
            if isinstance(response_ids, list):
                if not all(isinstance(value, str) and value for value in response_ids):
                    raise ValueError("cost row has malformed response IDs")
                response_count = len(response_ids)
                raw_sources = row.get("usage_sources")
                if raw_sources is None:
                    sources = ["legacy_response_body"] * response_count
                elif isinstance(raw_sources, list) and len(raw_sources) == response_count:
                    sources = [str(value) for value in raw_sources]
                else:
                    raise ValueError("cost row usage-source count does not match responses")
            else:
                response_id = row.get("response_id")
                response_count = int(isinstance(response_id, str) and bool(response_id))
                raw_source = row.get("usage_source")
                sources = (
                    [str(raw_source)]
                    if response_count and raw_source is not None
                    else ["legacy_response_body"] * response_count
                )
            missing_responses = sum(
                source == "stream_usage_missing" for source in sources
            )
            if missing_responses and model not in optional_usage_models:
                raise ValueError(
                    "missing usage is not registered for this model endpoint"
                )
            complete_responses = response_count - missing_responses
            row_telemetry_complete = missing_responses == 0
            priced = price_for_usage(
                schedules[str(model)],
                prompt_tokens=prompt,
                completion_tokens=completion,
            )
            tier = str(priced["minimum_prompt_tokens"])
            for profile in (by_model[str(model)], overall[str(model)]):
                profile["rows"] = int(profile["rows"]) + 1
                profile["rows_with_token_telemetry"] = int(
                    profile["rows_with_token_telemetry"]
                ) + int(row_telemetry_complete)
                profile["model_responses"] = int(profile["model_responses"]) + response_count
                profile["responses_with_token_telemetry"] = int(
                    profile["responses_with_token_telemetry"]
                ) + complete_responses
                profile["responses_missing_usage"] = int(
                    profile["responses_missing_usage"]
                ) + missing_responses
                profile["prompt_tokens"] = int(profile["prompt_tokens"]) + prompt
                profile["completion_tokens"] = int(
                    profile["completion_tokens"]
                ) + completion
                profile["estimated_cost_usd"] = float(
                    profile["estimated_cost_usd"]
                ) + float(priced["estimated_cost_usd"])
                profile["price_tier_rows"][tier] += 1
        if set(by_model) != expected_models:
            raise ValueError(f"{split} does not contain all four registered models")
        expected_per_model = int(expected_rows[split]) // len(expected_models)
        if expected_per_model * len(expected_models) != int(expected_rows[split]) or any(
            int(profile["rows"]) != expected_per_model
            for profile in by_model.values()
        ):
            raise ValueError(f"{split} per-model denominator is not balanced")
        split_reports[split] = {
            "rows": len(rows),
            "models": _public_profiles(by_model),
            "model_responses": sum(
                int(profile["model_responses"]) for profile in by_model.values()
            ),
            "responses_missing_usage": sum(
                int(profile["responses_missing_usage"])
                for profile in by_model.values()
            ),
            "estimated_cost_usd": sum(
                float(profile["estimated_cost_usd"])
                for profile in by_model.values()
            ),
        }
    return {
        "analysis": "capacity-surface-openrouter-replication-cost-v2",
        "role": (
            "descriptive scored-row accounting with endpoint-specific prompt tiers "
            "and explicit response-level usage coverage"
        ),
        "currency": "USD",
        "rows": sum(int(value) for value in expected_rows.values()),
        "splits": split_reports,
        "models": _public_profiles(overall),
        "estimated_cost_usd": sum(
            float(profile["estimated_cost_usd"]) for profile in overall.values()
        ),
        "token_telemetry_complete": all(
            int(profile["rows"]) == int(profile["rows_with_token_telemetry"])
            for profile in overall.values()
        ),
        "model_responses": sum(
            int(profile["model_responses"]) for profile in overall.values()
        ),
        "responses_with_token_telemetry": sum(
            int(profile["responses_with_token_telemetry"])
            for profile in overall.values()
        ),
        "responses_missing_usage": sum(
            int(profile["responses_missing_usage"])
            for profile in overall.values()
        ),
        "response_token_telemetry_coverage": (
            sum(
                int(profile["responses_with_token_telemetry"])
                for profile in overall.values()
            )
            / sum(int(profile["model_responses"]) for profile in overall.values())
            if sum(int(profile["model_responses"]) for profile in overall.values())
            else 1.0
        ),
        "estimated_cost_is_lower_bound": any(
            int(profile["responses_missing_usage"]) > 0
            for profile in overall.values()
        ),
        "note": (
            "Each row uses the highest prompt-token threshold not exceeding that row's "
            "observed aggregate prompt telemetry. Responses labeled stream_usage_missing "
            "remain in outcome denominators but contribute no imputed tokens or per-response "
            "cost, so the estimate is a lower bound whenever coverage is below one. "
            "Calibration, retries not reflected in provider token usage, taxes, credits, "
            "and non-token charges are outside this scored-matrix estimate."
        ),
    }


def _public_profiles(profiles: Mapping[str, Mapping[str, object]]) -> JSON:
    return {
        model: {
            **{
                key: value
                for key, value in profile.items()
                if key != "price_tier_rows"
            },
            "price_tier_rows": dict(
                sorted(
                    profile["price_tier_rows"].items(),
                    key=lambda item: int(item[0]),
                )
            ),
            "response_token_telemetry_coverage": (
                int(profile["responses_with_token_telemetry"])
                / int(profile["model_responses"])
                if int(profile["model_responses"])
                else 1.0
            ),
        }
        for model, profile in sorted(profiles.items())
    }
