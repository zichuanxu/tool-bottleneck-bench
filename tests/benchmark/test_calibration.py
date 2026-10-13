from __future__ import annotations

import unittest
from unittest.mock import patch

from bottleneckbench.calibration import (
    calibrate_concurrency,
    calibrate_transport,
    rescore_transport_calibration,
)
from bottleneckbench.core.schema import stable_hash


class TransportCalibrationTest(unittest.TestCase):
    @staticmethod
    def _panel(model_count: int = 3) -> dict:
        return {
            "execution_policy": {
                "timeout_seconds": 30,
                "transport_retries": 2,
                "model_lane_count": model_count,
            },
            "transport_calibration": {
                "candidate_intervals_seconds": [0, 0.25, 1],
            },
            "configurations": [
                {
                    "model": f"model-{index}",
                    "harness": "react-routed@test",
                    "study_role": "primary",
                    "discovery_enabled": True,
                }
                for index in range(model_count)
            ],
        }

    def test_selects_smallest_passing_interval_and_stops(self) -> None:
        def fake(configuration, _policy, *, interval, requests):
            return {
                "model": configuration["model"],
                "harness": configuration["harness"],
                "requests": requests,
                "passed": interval >= 0.25,
            }

        with patch(
            "bottleneckbench.calibration._calibrate_model",
            side_effect=fake,
        ) as calibrate:
            result = calibrate_transport(self._panel(), requests_per_model=20)

        self.assertTrue(result["passed"])
        self.assertEqual(result["selected_interval_seconds"], 0.25)
        self.assertEqual([trial["interval_seconds"] for trial in result["trials"]], [0, 0.25])
        self.assertEqual(calibrate.call_count, 6)

    def test_rejects_underpowered_transport_sample(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least 20"):
            calibrate_transport(self._panel(), requests_per_model=19)

    def test_transport_calibration_supports_four_model_extension(self) -> None:
        def fake(configuration, _policy, *, interval, requests):
            return {
                "model": configuration["model"],
                "harness": configuration["harness"],
                "requests": requests,
                "passed": True,
            }

        with patch(
            "bottleneckbench.calibration._calibrate_model",
            side_effect=fake,
        ) as calibrate:
            result = calibrate_transport(
                self._panel(model_count=4), requests_per_model=20, intervals=(0,)
            )

        self.assertTrue(result["passed"])
        self.assertEqual(calibrate.call_count, 4)

    def test_transport_rescore_does_not_select_on_native_tool_behavior(self) -> None:
        raw = {
            "audit": "factorized-capacity-transport-calibration",
            "passed": False,
            "selected_interval_seconds": None,
            "trials": [
                {
                    "interval_seconds": 0.25,
                    "passed": False,
                    "models": [
                        {
                            "model": "model-a",
                            "requests": 20,
                            "native_contract_successes": 14,
                            "terminal_infrastructure_failures": 0,
                            "first_half_retries": 0,
                            "second_half_retries": 0,
                            "latency_median_seconds": 2.0,
                            "latency_p90_seconds": 4.0,
                            "passed": False,
                        }
                    ],
                }
            ],
        }

        rescored = rescore_transport_calibration(raw)

        self.assertTrue(rescored["passed"])
        self.assertEqual(rescored["selected_interval_seconds"], 0.25)
        self.assertEqual(
            rescored["decision_version"], "transport-only-per-model-v3"
        )
        self.assertTrue(
            rescored["trials"][0]["models"][0][
                "native_contract_diagnostic_only"
            ]
        )
        self.assertEqual(rescored["raw_trials_hash"], stable_hash(raw["trials"]))

    def test_transport_rescore_binds_smallest_passing_interval_per_model(self) -> None:
        def result(model: str, *, retries: int, median: float, p90: float):
            return {
                "model": model,
                "requests": 20,
                "native_contract_successes": 20,
                "terminal_infrastructure_failures": 0,
                "first_half_retries": 0,
                "second_half_retries": retries,
                "latency_median_seconds": median,
                "latency_p90_seconds": p90,
                "passed": False,
            }

        raw = {
            "audit": "factorized-capacity-transport-calibration",
            "passed": False,
            "selected_interval_seconds": None,
            "trials": [
                {
                    "interval_seconds": 0.0,
                    "passed": False,
                    "models": [
                        result("model-a", retries=0, median=2.0, p90=3.0),
                        result("model-b", retries=1, median=1.0, p90=1.5),
                    ],
                },
                {
                    "interval_seconds": 0.25,
                    "passed": True,
                    "models": [
                        result("model-a", retries=0, median=2.0, p90=3.0),
                        result("model-b", retries=0, median=1.0, p90=1.5),
                    ],
                },
            ],
        }

        rescored = rescore_transport_calibration(raw)

        self.assertTrue(rescored["passed"])
        self.assertIsNone(rescored["selected_interval_seconds"])
        self.assertEqual(
            rescored["selected_interval_seconds_by_model"],
            {"model-a": 0.0, "model-b": 0.25},
        )

    def test_concurrency_selects_largest_contiguous_beneficial_limit(self) -> None:
        throughputs = {
            "model-0": {1: 0.20, 2: 0.37, 4: 0.65, 8: 0.66},
            "model-1": {1: 0.40, 2: 0.70, 4: 0.71, 8: 0.72},
            "model-2": {1: 0.92, 2: 0.96, 4: 0.97, 8: 0.98},
        }

        def fake(configuration, _policy, *, workers, requests):
            return {
                "model": configuration["model"],
                "harness": configuration["harness"],
                "workers": workers,
                "requests": requests,
                "native_contract_successes": requests,
                "exact_selection_successes": requests,
                "completed_requests_per_second": throughputs[configuration["model"]][workers],
                "transport_passed": True,
            }

        with patch(
            "bottleneckbench.calibration._calibrate_model_concurrency",
            side_effect=fake,
        ) as calibrate:
            result = calibrate_concurrency(
                self._panel(),
                requests_per_model=24,
                candidates=(1, 2, 4, 8),
            )

        self.assertTrue(result["passed"])
        self.assertEqual(
            result["selected_workers_by_model"],
            {"model-0": 4, "model-1": 2, "model-2": 1},
        )
        self.assertEqual(calibrate.call_count, 12)

    def test_concurrency_falls_back_before_unstable_candidate(self) -> None:
        def fake(configuration, _policy, *, workers, requests):
            return {
                "model": configuration["model"],
                "harness": configuration["harness"],
                "workers": workers,
                "requests": requests,
                "native_contract_successes": requests,
                "exact_selection_successes": requests,
                "completed_requests_per_second": 0.2 * workers,
                "transport_passed": workers < 4,
            }

        with patch(
            "bottleneckbench.calibration._calibrate_model_concurrency",
            side_effect=fake,
        ):
            result = calibrate_concurrency(
                self._panel(),
                requests_per_model=24,
                candidates=(1, 2, 4, 8),
            )

        self.assertTrue(result["passed"])
        self.assertEqual(set(result["selected_workers_by_model"].values()), {2})

    def test_concurrency_rejects_underpowered_probe(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least 24"):
            calibrate_concurrency(self._panel(), requests_per_model=23)

    def test_concurrency_compares_contract_to_nonperfect_serial_baseline(self) -> None:
        def fake(configuration, _policy, *, workers, requests):
            baseline = 20
            return {
                "model": configuration["model"],
                "harness": configuration["harness"],
                "workers": workers,
                "requests": requests,
                "native_contract_successes": baseline if workers < 8 else baseline - 3,
                "exact_selection_successes": 10,
                "completed_requests_per_second": {1: 0.2, 2: 0.35, 4: 0.60, 8: 0.90}[workers],
                "transport_passed": True,
            }

        with patch(
            "bottleneckbench.calibration._calibrate_model_concurrency",
            side_effect=fake,
        ):
            result = calibrate_concurrency(
                self._panel(), requests_per_model=24, candidates=(1, 2, 4, 8)
            )

        self.assertTrue(result["passed"])
        self.assertEqual(set(result["selected_workers_by_model"].values()), {4})
        self.assertTrue(
            all(
                item["reason"] == "native_contract_shift_at_8"
                for item in result["selection_details"].values()
            )
        )


if __name__ == "__main__":
    unittest.main()
