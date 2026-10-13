"""Prospective minimum-sufficient design for the factorized capacity study.

The primary panel is an extreme 2 x 2 x 2 factorial over visible-menu load,
workflow load, and returned-data representation. Four axis checkpoints retain
the registered middle levels and the two additional representation arms. This
estimates every declared main effect and two-way interaction with 12 cells per
semantic family instead of the 36-cell Cartesian product.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping, Sequence

from bottleneckbench.design import Split, WorkflowLoad
from bottleneckbench.core.schema import JSON, Representation


class StudyRole(StrEnum):
    """Amount of the capacity design assigned to one model-harness lane."""

    PRIMARY = "primary"
    ROBUSTNESS = "robustness"
    VERIFICATION_REPAIR = "verification_repair"
    INTERFACE_GUIDE = "interface_guide"


@dataclass(frozen=True, slots=True)
class CapacityCell:
    """One registered V x W x D coordinate."""

    visible_tools: int
    workflow: WorkflowLoad
    representation: Representation

    @property
    def key(self) -> tuple[int, str, str]:
        return self.visible_tools, self.workflow.value, self.representation.value

    def to_json(self) -> JSON:
        return {
            "visible_tools": self.visible_tools,
            "workflow": self.workflow.value,
            "representation": self.representation.value,
        }


def _cell(visible: int, workflow: str, representation: str) -> CapacityCell:
    return CapacityCell(
        visible_tools=visible,
        workflow=WorkflowLoad(workflow),
        representation=Representation(representation),
    )


_BASELINE = _cell(8, "low", "raw")

# Eight extreme corners identify all main effects and interactions. The four
# additional cells identify the registered V/W midpoints and compact/typed
# representation effects without filling the redundant 36-cell cube.
PRIMARY_CAPACITY_CELLS = (
    _BASELINE,
    _cell(32, "low", "raw"),
    _cell(128, "low", "raw"),
    _cell(8, "medium", "raw"),
    _cell(8, "high", "raw"),
    _cell(8, "low", "generic_compact"),
    _cell(8, "low", "typed"),
    _cell(8, "low", "linked"),
    _cell(128, "high", "raw"),
    _cell(128, "low", "linked"),
    _cell(8, "high", "linked"),
    _cell(128, "high", "linked"),
)

# The second harness is a robustness diagnostic, not a second primary panel.
ROBUSTNESS_CAPACITY_CELLS = (
    _BASELINE,
    _cell(128, "low", "raw"),
    _cell(8, "high", "linked"),
    _cell(128, "high", "linked"),
)

VERIFICATION_REPAIR_CELLS = (
    _BASELINE,
    _cell(8, "high", "raw"),
)

INTERFACE_GUIDE_CELLS = (
    _BASELINE,
    _cell(8, "low", "linked"),
)

# Transfer checks the baseline, isolated high loads, a realistic midpoint, and
# the joint stress corner. It is intentionally descriptive and primary-harness
# only.
TRANSFER_CAPACITY_CELLS = (
    _BASELINE,
    _cell(128, "low", "raw"),
    _cell(8, "high", "raw"),
    _cell(8, "low", "linked"),
    _cell(32, "medium", "typed"),
    _cell(128, "high", "linked"),
)

# Three log-spaced catalog sizes retain a curve and the registered endpoints.
DISCOVERY_REGISTRY_SIZES = (32, 512, 2048)


def capacity_cells(
    split: Split | str,
    role: StudyRole | str = StudyRole.PRIMARY,
) -> tuple[CapacityCell, ...]:
    """Return the exact prospective cell set for a split and study role."""

    normalized_split = Split(split)
    normalized_role = StudyRole(role)
    if normalized_split is Split.TRANSFER:
        if normalized_role is not StudyRole.PRIMARY:
            raise ValueError("transfer is registered for the primary harness only")
        return TRANSFER_CAPACITY_CELLS
    if normalized_role is StudyRole.PRIMARY:
        return PRIMARY_CAPACITY_CELLS
    if normalized_role is StudyRole.ROBUSTNESS:
        return ROBUSTNESS_CAPACITY_CELLS
    if normalized_role is StudyRole.VERIFICATION_REPAIR:
        return VERIFICATION_REPAIR_CELLS
    return INTERFACE_GUIDE_CELLS


CellKey = tuple[int, str, str]
Contrast = tuple[tuple[CellKey, float], ...]


def _main_effect(axis: int) -> Contrast:
    terms: list[tuple[CellKey, float]] = []
    for visible in (8, 128):
        for workflow in ("low", "high"):
            for representation in ("raw", "linked"):
                coordinate = (visible, workflow, representation)
                levels = (visible, workflow, representation)
                high = (128, "high", "linked")[axis]
                terms.append((coordinate, 0.25 if levels[axis] == high else -0.25))
    return tuple(terms)


def _two_way(axis_a: int, axis_b: int) -> Contrast:
    terms: list[tuple[CellKey, float]] = []
    highs = (128, "high", "linked")
    for visible in (8, 128):
        for workflow in ("low", "high"):
            for representation in ("raw", "linked"):
                levels = (visible, workflow, representation)
                sign_a = 1.0 if levels[axis_a] == highs[axis_a] else -1.0
                sign_b = 1.0 if levels[axis_b] == highs[axis_b] else -1.0
                # Average the difference-in-differences over the third axis.
                terms.append(((visible, workflow, representation), 0.5 * sign_a * sign_b))
    return tuple(terms)


PRIMARY_CONTRASTS: Mapping[str, Contrast] = {
    "visible_128_minus_8": _main_effect(0),
    "workflow_high_minus_low": _main_effect(1),
    "compact_minus_raw": (
        ((8, "low", "generic_compact"), 1.0),
        (_BASELINE.key, -1.0),
    ),
    "typed_minus_raw": (
        ((8, "low", "typed"), 1.0),
        (_BASELINE.key, -1.0),
    ),
    "linked_minus_raw": _main_effect(2),
    "visible_by_workflow": _two_way(0, 1),
    "visible_by_linked": _two_way(0, 2),
    "workflow_by_linked": _two_way(1, 2),
}

EXPLORATORY_CONTRASTS: Mapping[str, Contrast] = {
    "visible_midpoint_curvature": (
        ((32, "low", "raw"), 1.0),
        ((8, "low", "raw"), -0.5),
        ((128, "low", "raw"), -0.5),
    ),
    "workflow_midpoint_curvature": (
        ((8, "medium", "raw"), 1.0),
        ((8, "low", "raw"), -0.5),
        ((8, "high", "raw"), -0.5),
    ),
    "visible_by_workflow_by_linked": tuple(
        (
            (visible, workflow, representation),
            (1.0 if visible == 128 else -1.0)
            * (1.0 if workflow == "high" else -1.0)
            * (1.0 if representation == "linked" else -1.0),
        )
        for visible in (8, 128)
        for workflow in ("low", "high")
        for representation in ("raw", "linked")
    ),
}


def required_cells(contrasts: Mapping[str, Contrast]) -> set[CellKey]:
    return {coordinate for terms in contrasts.values() for coordinate, _ in terms}


def efficient_design_audit() -> JSON:
    """Fail closed if the sparse design loses a declared estimand."""

    primary = [cell.key for cell in PRIMARY_CAPACITY_CELLS]
    robustness = [cell.key for cell in ROBUSTNESS_CAPACITY_CELLS]
    verification_repair = [cell.key for cell in VERIFICATION_REPAIR_CELLS]
    interface_guide = [cell.key for cell in INTERFACE_GUIDE_CELLS]
    transfer = [cell.key for cell in TRANSFER_CAPACITY_CELLS]
    extreme = {
        (visible, workflow, representation)
        for visible in (8, 128)
        for workflow in ("low", "high")
        for representation in ("raw", "linked")
    }
    all_contrasts = {**PRIMARY_CONTRASTS, **EXPLORATORY_CONTRASTS}
    checks = {
        "primary_has_12_unique_cells": len(primary) == len(set(primary)) == 12,
        "extreme_factorial_complete": extreme <= set(primary),
        "all_contrasts_identified": required_cells(all_contrasts) <= set(primary),
        "robustness_is_primary_subset": set(robustness) <= set(primary),
        "verification_repair_is_primary_subset": set(verification_repair) <= set(primary),
        "interface_guide_is_primary_subset": set(interface_guide) <= set(primary),
        "transfer_has_6_unique_cells": len(transfer) == len(set(transfer)) == 6,
        "discovery_has_endpoints_and_midpoint": DISCOVERY_REGISTRY_SIZES == (32, 512, 2048),
    }
    return {
        "audit": "factorized-capacity-efficient-design",
        "passed": all(checks.values()),
        "checks": [
            {"name": name, "passed": passed}
            for name, passed in checks.items()
        ],
        "primary_cells": [cell.to_json() for cell in PRIMARY_CAPACITY_CELLS],
        "robustness_cells": [cell.to_json() for cell in ROBUSTNESS_CAPACITY_CELLS],
        "verification_repair_cells": [cell.to_json() for cell in VERIFICATION_REPAIR_CELLS],
        "interface_guide_cells": [cell.to_json() for cell in INTERFACE_GUIDE_CELLS],
        "transfer_cells": [cell.to_json() for cell in TRANSFER_CAPACITY_CELLS],
        "discovery_registry_sizes": list(DISCOVERY_REGISTRY_SIZES),
        "primary_contrasts": {
            name: [
                {"cell": list(coordinate), "weight": weight}
                for coordinate, weight in terms
            ]
            for name, terms in PRIMARY_CONTRASTS.items()
        },
        "exploratory_contrasts": {
            name: [
                {"cell": list(coordinate), "weight": weight}
                for coordinate, weight in terms
            ]
            for name, terms in EXPLORATORY_CONTRASTS.items()
        },
    }


def contrast_value(values: Mapping[CellKey, float], terms: Sequence[tuple[CellKey, float]]) -> float:
    """Evaluate one registered linear contrast over a complete family panel."""

    missing = {coordinate for coordinate, _ in terms} - set(values)
    if missing:
        raise ValueError(f"contrast is missing registered cells: {sorted(missing)}")
    return sum(values[coordinate] * weight for coordinate, weight in terms)
