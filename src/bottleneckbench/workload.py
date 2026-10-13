"""Auditable workload and schema-exposure accounting for the prospective study."""

from __future__ import annotations

from bottleneckbench.design import Split, build_families, compile_task
from bottleneckbench.discovery import discovery_cases
from bottleneckbench.study_design import (
    DISCOVERY_REGISTRY_SIZES,
    StudyRole,
    capacity_cells,
)
from bottleneckbench.core.schema import JSON


_DENSE_CAPACITY_CELLS = 3 * 3 * 4
_DENSE_DISCOVERY_CELLS = 4 * 2


def _capacity_accounting(split: Split, role: StudyRole) -> JSON:
    families = build_families(split)
    cells = capacity_cells(split, role)
    episodes = 0
    reference_requests = 0
    maximum_requests = 0
    static_schema_exposures = 0
    routed_schema_exposures = 0
    for family in families:
        for cell in cells:
            task = compile_task(family, cell.workflow)
            program_calls = len(task.program)
            requests = program_calls + 1
            episodes += 1
            reference_requests += requests
            maximum_requests += program_calls + 4
            static_schema_exposures += cell.visible_tools * requests
            routed_schema_exposures += (
                cell.visible_tools + len(task.base_tool_names) * program_calls
            )
    return {
        "families": len(families),
        "cells_per_family": len(cells),
        "episodes": episodes,
        "reference_path_model_requests": reference_requests,
        "maximum_outer_model_requests": maximum_requests,
        "static_tool_schema_exposures": static_schema_exposures,
        "routed_tool_schema_exposures": routed_schema_exposures,
    }


def prospective_workload(model_count: int = 3) -> JSON:
    """Return exact counts for the frozen sparse design and a matched dense design."""

    if model_count <= 0:
        raise ValueError("model_count must be positive")
    per_model: JSON = {}
    total_episodes = 0
    total_reference_requests = 0
    total_maximum_requests = 0
    total_static_exposures = 0
    total_actual_exposures = 0
    for split in Split:
        primary = _capacity_accounting(split, StudyRole.PRIMARY)
        roles: JSON = {StudyRole.PRIMARY.value: primary}
        capacity_episodes = int(primary["episodes"])
        reference_requests = int(primary["reference_path_model_requests"])
        maximum_requests = int(primary["maximum_outer_model_requests"])
        static_exposures = int(primary["static_tool_schema_exposures"])
        actual_exposures = int(primary["routed_tool_schema_exposures"])
        if split is not Split.TRANSFER:
            robustness = _capacity_accounting(split, StudyRole.ROBUSTNESS)
            roles[StudyRole.ROBUSTNESS.value] = robustness
            capacity_episodes += int(robustness["episodes"])
            reference_requests += int(robustness["reference_path_model_requests"])
            maximum_requests += int(robustness["maximum_outer_model_requests"])
            static_exposures += int(robustness["static_tool_schema_exposures"])
            actual_exposures += int(robustness["static_tool_schema_exposures"])
        discovery_episodes = 0
        if split is not Split.TRANSFER:
            discovery_episodes = (
                len(discovery_cases(split))
                * 2
                * len(DISCOVERY_REGISTRY_SIZES)
            )
        split_episodes = capacity_episodes + discovery_episodes
        split_reference_requests = reference_requests + discovery_episodes
        split_maximum_requests = maximum_requests + discovery_episodes
        per_model[split.value] = {
            "capacity_roles": roles,
            "capacity_episodes": capacity_episodes,
            "discovery_episodes": discovery_episodes,
            "total_episodes": split_episodes,
            "reference_path_model_requests": split_reference_requests,
            "maximum_outer_model_requests": split_maximum_requests,
            "capacity_static_tool_schema_exposures": static_exposures,
            "capacity_actual_tool_schema_exposures": actual_exposures,
        }
        total_episodes += split_episodes
        total_reference_requests += split_reference_requests
        total_maximum_requests += split_maximum_requests
        total_static_exposures += static_exposures
        total_actual_exposures += actual_exposures

    dense_per_model = (
        len(build_families(Split.DEVELOPMENT)) * _DENSE_CAPACITY_CELLS * 2
        + len(build_families(Split.MAIN)) * _DENSE_CAPACITY_CELLS * 2
        + len(build_families(Split.TRANSFER)) * _DENSE_CAPACITY_CELLS
        + len(discovery_cases(Split.DEVELOPMENT)) * _DENSE_DISCOVERY_CELLS
        + len(discovery_cases(Split.MAIN)) * _DENSE_DISCOVERY_CELLS
    )
    full_total = total_episodes * model_count
    dense_total = dense_per_model * model_count
    reference_requests_total = total_reference_requests * model_count
    maximum_requests_total = total_maximum_requests * model_count
    return {
        "audit": "factorized-capacity-prospective-workload",
        "passed": (
            full_total > 0
            and dense_total > full_total
            and total_actual_exposures < total_static_exposures
        ),
        "model_count": model_count,
        "per_model": per_model,
        "prospective": {
            "episodes": full_total,
            "reference_path_model_requests": reference_requests_total,
            "maximum_outer_model_requests": maximum_requests_total,
            "capacity_static_tool_schema_exposures": total_static_exposures * model_count,
            "capacity_actual_tool_schema_exposures": total_actual_exposures * model_count,
        },
        "matched_dense_counterfactual": {
            "definition": (
                "The same models, splits, and primary/robustness roles, with all 36 Capacity cells; "
                "Discovery uses four registry sizes and two retrievers on the primary role."
            ),
            "episodes": dense_total,
        },
        "reductions": {
            "episode_fraction": 1.0 - full_total / dense_total,
            "episode_factor": dense_total / full_total,
            "capacity_schema_exposure_fraction_vs_static_sparse": (
                1.0 - total_actual_exposures / total_static_exposures
            ),
        },
        "throttle_only_wall_floor_hours": {
            str(interval): total_reference_requests * interval / 3600
            for interval in (0, 1, 10)
        },
        "notes": [
            "Counts are exact generator counts, not runtime estimates.",
            "Reference-path requests assume one tool call per certified program step plus one final answer.",
            "Maximum outer requests use the frozen task-adaptive call budget plus two turn slots.",
            "Throttle floors assume three model lanes execute concurrently, one lane per model.",
        ],
    }
