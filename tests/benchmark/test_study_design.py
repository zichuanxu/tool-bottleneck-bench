from __future__ import annotations

import unittest

from bottleneckbench.study_design import (
    DISCOVERY_REGISTRY_SIZES,
    EXPLORATORY_CONTRASTS,
    INTERFACE_GUIDE_CELLS,
    PRIMARY_CAPACITY_CELLS,
    PRIMARY_CONTRASTS,
    ROBUSTNESS_CAPACITY_CELLS,
    StudyRole,
    VERIFICATION_REPAIR_CELLS,
    capacity_cells,
    contrast_value,
    efficient_design_audit,
)
from bottleneckbench.design import Split


class EfficientStudyDesignTest(unittest.TestCase):
    def test_design_audit_preserves_every_registered_estimand(self) -> None:
        audit = efficient_design_audit()

        self.assertTrue(audit["passed"], audit)
        self.assertEqual(len(PRIMARY_CAPACITY_CELLS), 12)
        self.assertEqual(len(ROBUSTNESS_CAPACITY_CELLS), 4)
        self.assertEqual(len(VERIFICATION_REPAIR_CELLS), 2)
        self.assertEqual(len(INTERFACE_GUIDE_CELLS), 2)
        self.assertEqual(DISCOVERY_REGISTRY_SIZES, (32, 512, 2048))

    def test_primary_design_recovers_main_and_interaction_contrasts(self) -> None:
        values = {
            cell.key: (
                0.70
                - 0.10 * (cell.visible_tools == 128)
                - 0.20 * (cell.workflow.value == "high")
                + 0.15 * (cell.representation.value == "linked")
                - 0.05
                * (cell.visible_tools == 128)
                * (cell.workflow.value == "high")
            )
            for cell in PRIMARY_CAPACITY_CELLS
        }

        self.assertAlmostEqual(
            contrast_value(values, PRIMARY_CONTRASTS["visible_128_minus_8"]),
            -0.125,
        )
        self.assertAlmostEqual(
            contrast_value(values, PRIMARY_CONTRASTS["workflow_high_minus_low"]),
            -0.225,
        )
        self.assertAlmostEqual(
            contrast_value(values, PRIMARY_CONTRASTS["linked_minus_raw"]),
            0.15,
        )
        self.assertAlmostEqual(
            contrast_value(values, PRIMARY_CONTRASTS["visible_by_workflow"]),
            -0.05,
        )
        self.assertAlmostEqual(
            contrast_value(
                values,
                EXPLORATORY_CONTRASTS["visible_by_workflow_by_linked"],
            ),
            0.0,
        )

    def test_roles_and_transfer_have_registered_sizes(self) -> None:
        self.assertEqual(len(capacity_cells(Split.MAIN, StudyRole.PRIMARY)), 12)
        self.assertEqual(len(capacity_cells(Split.MAIN, StudyRole.ROBUSTNESS)), 4)
        self.assertEqual(len(capacity_cells(Split.MAIN, StudyRole.VERIFICATION_REPAIR)), 2)
        self.assertEqual(len(capacity_cells(Split.MAIN, StudyRole.INTERFACE_GUIDE)), 2)
        self.assertEqual(len(capacity_cells(Split.TRANSFER, StudyRole.PRIMARY)), 6)
        with self.assertRaisesRegex(ValueError, "primary harness"):
            capacity_cells(Split.TRANSFER, StudyRole.ROBUSTNESS)


if __name__ == "__main__":
    unittest.main()
