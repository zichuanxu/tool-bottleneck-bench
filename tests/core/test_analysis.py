from __future__ import annotations

import unittest

from bottleneckbench.core.analysis import (
    analyze_rows,
    capacity_threshold,
    holm_adjust,
    paired_risk_difference,
    scaling_auc,
    template_weighted_rate,
)
from bottleneckbench.core.schema import canonical_json


def _row(
    condition_id: str,
    task_id: str,
    *,
    track: str = "composition",
    harness: str = "oracle-react",
    representation: str = "raw",
    registry_size: int = 128,
    exposure_size: int = 16,
    seed: int = 1,
    strict: bool = False,
    available: float = 1.0,
    status: str = "agent_failure",
    tokens: int = 100,
) -> dict:
    return {
        "condition_id": condition_id,
        "condition": {
            "task_id": task_id,
            "track": track,
            "model": "agnes-2.5-flash",
            "harness": harness,
            "representation": representation,
            "registry_size": registry_size,
            "exposure_size": exposure_size,
            "seed": seed,
        },
        "status": status,
        "exposure": {"required_available": available},
        "verdict": {"strict_vts": strict},
        "usage": {"total_tokens": tokens},
    }


class AnalysisTest(unittest.TestCase):
    def test_template_weighting_does_not_treat_world_seeds_as_templates(self) -> None:
        rows = [
            _row("a1", "A", seed=1, strict=True),
            _row("a2", "A", seed=2, strict=True),
            _row("b1", "B", seed=1, strict=False),
        ]

        rate = template_weighted_rate(rows, "strict_vts")

        self.assertEqual(rate, 0.5)

    def test_paired_data_readiness_effect_uses_complete_task_cells_only(self) -> None:
        rows = [
            _row("a-raw", "A", representation="raw", strict=False),
            _row("a-typed", "A", representation="typed", strict=True),
            _row("b-raw", "B", representation="raw", strict=True),
            _row("b-typed", "B", representation="typed", strict=True),
            _row("c-typed", "C", representation="typed", strict=True),
        ]

        effect = paired_risk_difference(rows, treatment="typed", control="raw")

        self.assertEqual(effect["pairs"], 2)
        self.assertEqual(effect["risk_difference"], 0.5)
        self.assertEqual(effect["improved"], 1)
        self.assertEqual(effect["degraded"], 0)

    def test_analysis_includes_protocol_typed_minus_compact_contrast(self) -> None:
        rows = [
            _row("a-compact", "A", representation="generic_compact", strict=False),
            _row("a-typed", "A", representation="typed", strict=True),
            _row("b-compact", "B", representation="generic_compact", strict=True),
            _row("b-typed", "B", representation="typed", strict=True),
        ]

        analysis = analyze_rows(rows)

        contrast = analysis["paired_effects"]["typed_minus_generic_compact"]
        self.assertEqual(contrast["pairs"], 2)
        self.assertEqual(contrast["risk_difference"], 0.5)

    def test_capacity_threshold_reports_boundary_censoring(self) -> None:
        self.assertEqual(
            capacity_threshold([32, 128], [0.7, 0.4], threshold=0.8)["status"],
            "below_minimum",
        )
        self.assertEqual(
            capacity_threshold([32, 128], [0.9, 0.85], threshold=0.8)["status"],
            "right_censored",
        )
        observed = capacity_threshold([32, 128, 512], [0.95, 0.82, 0.6], threshold=0.8)
        self.assertEqual(observed, {"status": "observed", "maximum_level": 128})

    def test_scaling_auc_is_normalized_over_observed_log_range(self) -> None:
        auc = scaling_auc([32, 128, 512], [1.0, 0.5, 0.0])

        self.assertAlmostEqual(auc, 0.5)

    def test_holm_adjustment_is_monotone_in_original_rank(self) -> None:
        adjusted = holm_adjust({"h1": 0.01, "h2": 0.04, "h3": 0.03})

        self.assertEqual(adjusted, {"h1": 0.03, "h2": 0.06, "h3": 0.06})

    def test_analysis_is_deterministic_and_reports_itt_availability_cost(self) -> None:
        rows = [
            _row(
                "d1",
                "D1",
                track="discovery",
                registry_size=32,
                strict=True,
                available=1.0,
                status="verified_success",
                tokens=80,
            ),
            _row(
                "d2",
                "D2",
                track="discovery",
                registry_size=128,
                strict=False,
                available=0.0,
                status="capability_unavailable",
                tokens=0,
            ),
        ]

        first = analyze_rows(rows)
        second = analyze_rows(list(reversed(rows)))

        self.assertEqual(canonical_json(first), canonical_json(second))
        self.assertEqual(first["tracks"]["discovery"]["template_weighted_strict_vts"], 0.5)
        self.assertEqual(first["tracks"]["discovery"]["capability_availability"], 0.5)
        self.assertEqual(first["tracks"]["discovery"]["mean_total_tokens"], 40.0)
        self.assertEqual(first["status_counts"]["capability_unavailable"], 1)


if __name__ == "__main__":
    unittest.main()
