from __future__ import annotations

import unittest

from bottleneckbench.workload import prospective_workload


class ProspectiveWorkloadTest(unittest.TestCase):
    def test_three_model_workload_matches_registered_design(self) -> None:
        audit = prospective_workload(3)

        self.assertEqual(audit["per_model"]["development"]["total_episodes"], 300)
        self.assertEqual(audit["per_model"]["main"]["total_episodes"], 792)
        self.assertEqual(audit["per_model"]["transfer"]["total_episodes"], 72)
        self.assertEqual(audit["prospective"]["episodes"], 3492)
        self.assertEqual(audit["matched_dense_counterfactual"]["episodes"], 12960)
        self.assertGreater(audit["reductions"]["episode_fraction"], 0.73)
        self.assertGreater(audit["reductions"]["episode_factor"], 3.7)

    def test_routed_primary_policy_reduces_sparse_schema_exposure(self) -> None:
        audit = prospective_workload(3)

        self.assertGreater(
            audit["prospective"]["capacity_static_tool_schema_exposures"],
            audit["prospective"]["capacity_actual_tool_schema_exposures"],
        )
        self.assertGreater(
            audit["reductions"]["capacity_schema_exposure_fraction_vs_static_sparse"],
            0.50,
        )


if __name__ == "__main__":
    unittest.main()
