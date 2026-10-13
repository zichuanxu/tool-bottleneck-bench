from __future__ import annotations

import unittest

from bottleneckbench.costing import (
    _schedules,
    analyze_replication_cost,
    price_for_usage,
)


def _snapshot() -> dict:
    return {
        "prices_are_usd_per_token": True,
        "selected": [
            {
                "requested_model": "deepseek",
                "served_model": "deepseek-version",
                "provider_tag": "provider-a",
                "prompt_price": 0.1,
                "completion_price": 0.2,
            },
            {
                "requested_model": "qwen",
                "served_model": "qwen-version",
                "provider_tag": "provider-b",
                "prompt_price": 0.03,
                "completion_price": 0.13,
                "prompt_overrides": [
                    {
                        "minimum_prompt_tokens": 32000,
                        "prompt_price": 0.1,
                        "completion_price": 0.4,
                    },
                    {
                        "minimum_prompt_tokens": 256000,
                        "prompt_price": 0.2,
                        "completion_price": 0.8,
                    },
                ],
            },
            {
                "requested_model": "llama",
                "served_model": "llama-version",
                "provider_tag": "provider-c",
                "prompt_price": 0.25,
                "completion_price": 0.7,
                "usage_sources": [
                    "stream_terminal_event",
                    "stream_usage_missing",
                ],
            },
            {
                "requested_model": "luna",
                "served_model": "luna-version",
                "provider_tag": "provider-d",
                "prompt_price": 0.1,
                "completion_price": 0.6,
            },
        ],
    }


class ReplicationCostTest(unittest.TestCase):
    def test_prompt_thresholds_are_inclusive_and_row_specific(self) -> None:
        schedule = _schedules(_snapshot())["qwen"]

        self.assertEqual(
            price_for_usage(schedule, prompt_tokens=31999, completion_tokens=1)[
                "minimum_prompt_tokens"
            ],
            0,
        )
        self.assertEqual(
            price_for_usage(schedule, prompt_tokens=32000, completion_tokens=1)[
                "minimum_prompt_tokens"
            ],
            32000,
        )
        self.assertEqual(
            price_for_usage(schedule, prompt_tokens=256000, completion_tokens=1)[
                "minimum_prompt_tokens"
            ],
            256000,
        )

    def test_complete_balanced_matrix_is_accounted(self) -> None:
        rows = [
            {
                "condition_id": f"condition-{model}",
                "condition": {"model": model},
                "usage": {
                    "prompt_tokens": 32000 if model == "qwen" else 10,
                    "completion_tokens": 2,
                },
            }
            for model in ("deepseek", "qwen", "llama", "luna")
        ]
        result = analyze_replication_cost(
            {"main": rows},
            _snapshot(),
            expected_rows={"main": 4},
        )

        self.assertEqual(result["rows"], 4)
        self.assertTrue(result["token_telemetry_complete"])
        self.assertEqual(result["models"]["qwen"]["price_tier_rows"], {"32000": 1})
        self.assertAlmostEqual(
            result["models"]["qwen"]["estimated_cost_usd"],
            32000 * 0.1 + 2 * 0.4,
        )

    def test_unbalanced_model_denominator_fails_closed(self) -> None:
        rows = [
            {
                "condition_id": f"condition-{index}",
                "condition": {"model": "deepseek"},
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            }
            for index in range(4)
        ]
        with self.assertRaisesRegex(ValueError, "all four"):
            analyze_replication_cost(
                {"main": rows},
                _snapshot(),
                expected_rows={"main": 4},
            )

    def test_registered_missing_usage_is_coverage_not_imputed_cost(self) -> None:
        rows = [
            {
                "condition_id": f"condition-{model}",
                "condition": {"model": model},
                "usage": {
                    "prompt_tokens": 0 if model == "llama" else 10,
                    "completion_tokens": 0 if model == "llama" else 2,
                },
                "response_ids": [f"response-{model}"],
                "usage_sources": [
                    "stream_usage_missing" if model == "llama" else "response_body"
                ],
            }
            for model in ("deepseek", "qwen", "llama", "luna")
        ]
        result = analyze_replication_cost(
            {"main": rows},
            _snapshot(),
            expected_rows={"main": 4},
        )

        self.assertFalse(result["token_telemetry_complete"])
        self.assertTrue(result["estimated_cost_is_lower_bound"])
        self.assertEqual(result["responses_missing_usage"], 1)
        self.assertEqual(result["response_token_telemetry_coverage"], 0.75)
        self.assertEqual(result["models"]["llama"]["rows_with_token_telemetry"], 0)
        self.assertEqual(result["models"]["llama"]["estimated_cost_usd"], 0.0)


if __name__ == "__main__":
    unittest.main()
