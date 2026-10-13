from __future__ import annotations

import unittest

from bottleneckbench.panel import (
    apply_concurrency_calibration,
    apply_transport_calibration,
)
from bottleneckbench.core.schema import stable_hash


class PanelFreezeTest(unittest.TestCase):
    @staticmethod
    def _panel() -> dict:
        configurations = []
        for model in ("model-a", "model-b"):
            configurations.extend(
                [
                    {
                        "model": model,
                        "harness": "routed",
                        "study_role": "primary",
                        "discovery_enabled": True,
                        "provider_concurrency_cap": 4,
                        "workers_per_job": 1,
                    },
                    {
                        "model": model,
                        "harness": "fixed",
                        "study_role": "robustness",
                        "discovery_enabled": False,
                        "provider_concurrency_cap": 4,
                        "workers_per_job": 1,
                    },
                ]
            )
        return {
            "frozen_for_study": False,
            "execution_policy": {
                "model_lane_count": 2,
                "minimum_request_interval_seconds": 0.0,
            },
            "transport_calibration": {"status": "pending"},
            "concurrency_calibration": {"status": "pending"},
            "configurations": configurations,
        }

    def test_two_stage_calibration_freezes_transport_and_workers(self) -> None:
        panel = self._panel()
        transport = {
            "audit": "factorized-capacity-transport-calibration",
            "passed": True,
            "panel_hash": stable_hash(panel),
            "selected_interval_seconds": 0.25,
        }
        transport_bound = apply_transport_calibration(
            panel,
            transport,
            completed_at="2026-08-11T00:00:00Z",
            artifact="transport.json",
        )
        self.assertFalse(transport_bound["frozen_for_study"])
        self.assertEqual(
            transport_bound["execution_policy"][
                "minimum_request_interval_seconds"
            ],
            0.25,
        )

        concurrency = {
            "audit": "factorized-capacity-concurrency-calibration",
            "passed": True,
            "panel_hash": stable_hash(transport_bound),
            "selected_workers_by_model": {"model-a": 4, "model-b": 2},
        }
        frozen = apply_concurrency_calibration(
            transport_bound,
            concurrency,
            completed_at="2026-08-11T00:01:00Z",
            artifact="concurrency.json",
        )

        self.assertTrue(frozen["frozen_for_study"])
        workers = {
            item["model"]: item["workers_per_job"]
            for item in frozen["configurations"]
        }
        self.assertEqual(workers, {"model-a": 4, "model-b": 2})

    def test_calibration_for_another_panel_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not bind"):
            apply_transport_calibration(
                self._panel(),
                {
                    "audit": "factorized-capacity-transport-calibration",
                    "passed": True,
                    "panel_hash": "0" * 64,
                    "selected_interval_seconds": 0.0,
                },
                completed_at="2026-08-11T00:00:00Z",
                artifact="transport.json",
            )


if __name__ == "__main__":
    unittest.main()
