from __future__ import annotations

import unittest

from bottleneckbench.design import Split
from bottleneckbench.orchestrator import (
    _discovery_target,
    _model_lane_count,
    infrastructure_ceiling,
)


def _row(*, visible: int = 8, provider_failure: bool = False) -> dict:
    return {
        "condition": {"visible_tools": visible},
        "status": "provider_failure" if provider_failure else "agent_failure",
        "errors": ["provider_failure:test"] if provider_failure else [],
    }


class DevelopmentOrchestratorTest(unittest.TestCase):
    def test_declared_four_model_lane_count_is_supported(self) -> None:
        panel = {"execution_policy": {"model_lane_count": 4}}

        self.assertEqual(_model_lane_count(panel, inferred=3), 4)

    def test_discovery_interleave_closes_registered_geometry(self) -> None:
        development = [
            _discovery_target(Split.DEVELOPMENT, index, blocks=12, total=108)
            for index in range(12)
        ]
        main = [
            _discovery_target(Split.MAIN, index, blocks=36, total=216)
            for index in range(36)
        ]

        self.assertEqual(development[:4], [6, 18, 24, 36])
        self.assertEqual(development[-1], 108)
        self.assertEqual(main[:4], [6, 12, 18, 24])
        self.assertEqual(main[-1], 216)

    def test_discovery_interleave_rejects_transfer(self) -> None:
        with self.assertRaisesRegex(ValueError, "Discovery"):
            _discovery_target(Split.TRANSFER, 0, blocks=12, total=0)

    def test_v128_failure_stops_when_final_rate_cannot_pass(self) -> None:
        gate = infrastructure_ceiling(
            [_row(visible=128, provider_failure=True)],
            [],
            planned_capacity=144,
            planned_discovery=108,
            planned_v128=48,
        )

        self.assertFalse(gate["passed"])
        self.assertEqual(gate["allowed_infrastructure_failures"], 5)
        self.assertEqual(gate["allowed_v128_infrastructure_failures"], 0)

    def test_non_v128_failures_use_final_planned_denominator(self) -> None:
        passing = infrastructure_ceiling(
            [_row(provider_failure=True) for _ in range(5)],
            [],
            planned_capacity=144,
            planned_discovery=108,
            planned_v128=48,
        )
        failing = infrastructure_ceiling(
            [_row(provider_failure=True) for _ in range(6)],
            [],
            planned_capacity=144,
            planned_discovery=108,
            planned_v128=48,
        )

        self.assertTrue(passing["passed"])
        self.assertFalse(failing["passed"])



if __name__ == "__main__":
    unittest.main()
