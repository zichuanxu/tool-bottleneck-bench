"""Fail-closed development admission before confirmatory source freeze."""

from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Mapping, Sequence

from bottleneckbench.analysis import (
    is_infrastructure_failure,
    prospective_power,
    prospective_precision,
)
from bottleneckbench.design import Split
from bottleneckbench.study_design import (
    DISCOVERY_REGISTRY_SIZES,
    StudyRole,
    capacity_cells,
)
from bottleneckbench.core.schema import JSON, stable_hash


def _condition(row: Mapping[str, object]) -> Mapping[str, object]:
    value = row.get("condition")
    if not isinstance(value, dict):
        raise ValueError("run row is missing a condition object")
    return value


def _check(name: str, passed: bool, details: JSON) -> JSON:
    return {"name": name, "passed": passed, "details": details}


def development_admission(
    capacity_rows: Sequence[Mapping[str, object]],
    discovery_rows: Sequence[Mapping[str, object]],
    construct_audits: Sequence[Mapping[str, object]],
    *,
    minimum_models: int = 3,
    expected_capacity_configurations: Sequence[tuple[str, str, str]] | None = None,
    expected_discovery_configurations: Sequence[tuple[str, str]] | None = None,
    model_panel_hash: str | None = None,
    protocol_artifact_hash: str | None = None,
    workflow_boundary_admission: Mapping[str, object] | None = None,
) -> JSON:
    """Require complete, operational, non-degenerate development evidence."""

    capacity_ids = [row.get("condition_id") for row in capacity_rows]
    discovery_ids = [row.get("condition_id") for row in discovery_rows]
    unique = (
        all(isinstance(value, str) and value for value in capacity_ids + discovery_ids)
        and len(set(capacity_ids)) == len(capacity_ids)
        and len(set(discovery_ids)) == len(discovery_ids)
    )
    split_integrity = all(
        str(_condition(row).get("family_id", "")).startswith("development-")
        for row in capacity_rows
    ) and all(
        _condition(row).get("split") == "development" for row in discovery_rows
    )
    capacity_by_config: dict[
        tuple[str, str, str], list[Mapping[str, object]]
    ] = defaultdict(list)
    discovery_by_config: dict[tuple[str, str], list[Mapping[str, object]]] = defaultdict(list)
    for row in capacity_rows:
        condition = _condition(row)
        role = StudyRole(str(row.get("study_role", StudyRole.PRIMARY.value)))
        capacity_by_config[
            (str(condition.get("model")), str(condition.get("harness")), role.value)
        ].append(row)
    for row in discovery_rows:
        condition = _condition(row)
        discovery_by_config[(str(condition.get("model")), str(condition.get("harness")))].append(row)
    capacity_configs = set(capacity_by_config)
    discovery_configs = set(discovery_by_config)
    primary_configs = {
        (model, harness)
        for model, harness, role in capacity_configs
        if role == StudyRole.PRIMARY.value
    }
    robustness_configs = {
        (model, harness)
        for model, harness, role in capacity_configs
        if role == StudyRole.ROBUSTNESS.value
    }
    models = {model for model, _ in primary_configs}
    panel_closed = discovery_configs == primary_configs
    expected_capacity = set(expected_capacity_configurations or capacity_configs)
    expected_discovery = set(expected_discovery_configurations or discovery_configs)
    panel_matches_freeze = (
        capacity_configs == expected_capacity
        and discovery_configs == expected_discovery
    )

    required_audits = {
        "capacity-surface-static-construct-v4",
        "capacity-surface-runtime-construct-v4",
        "capacity-surface-discovery-construct-v3",
        "factorized-capacity-efficient-design",
        "factorized-capacity-efficiency-policy",
    }
    audits_by_name = {str(audit.get("audit")): audit for audit in construct_audits}
    construct_validity = set(audits_by_name) == required_audits and all(
        audit.get("passed") is True
        and (
            name == "factorized-capacity-efficient-design"
            or audit.get("split") == "development"
        )
        for name, audit in audits_by_name.items()
    )

    capacity_complete = True
    capacity_details: JSON = {}
    for config in sorted(capacity_configs):
        rows = capacity_by_config[config]
        by_family: dict[str, set[tuple[object, object, object]]] = defaultdict(set)
        metadata: dict[str, set[tuple[object, ...]]] = defaultdict(set)
        for row in rows:
            condition = _condition(row)
            family_id = str(condition.get("family_id"))
            by_family[family_id].add(
                (
                    condition.get("visible_tools"),
                    condition.get("workflow"),
                    condition.get("representation"),
                )
            )
            metadata[family_id].add(
                (
                    condition.get("intent"),
                    condition.get("outcome_variant"),
                    condition.get("prompt_variant"),
                    condition.get("branching"),
                    condition.get("action_required"),
                )
            )
        role = StudyRole(config[2])
        expected_cells = {cell.key for cell in capacity_cells(Split.DEVELOPMENT, role)}
        passed = (
            len(rows) == 12 * len(expected_cells)
            and len(by_family) == 12
            and all(cells == expected_cells for cells in by_family.values())
            and all(len(values) == 1 for values in metadata.values())
            and len({next(iter(values))[0] for values in metadata.values()}) == 12
        )
        capacity_complete &= passed
        capacity_details["|".join(config)] = {
            "rows": len(rows),
            "families": len(by_family),
            "passed": passed,
        }

    discovery_complete = True
    discovery_details: JSON = {}
    expected_discovery_cells = {
        (registry_size, retriever)
        for registry_size in DISCOVERY_REGISTRY_SIZES
        for retriever in ("flat", "hierarchical")
    }
    for config in sorted(discovery_configs):
        rows = discovery_by_config[config]
        by_case: dict[str, set[tuple[object, object]]] = defaultdict(set)
        for row in rows:
            condition = _condition(row)
            by_case[str(condition.get("case_id"))].add(
                (condition.get("registry_size"), condition.get("retriever"))
            )
        passed = (
            len(rows) == 18 * len(expected_discovery_cells)
            and len(by_case) == 18
            and all(cells == expected_discovery_cells for cells in by_case.values())
        )
        discovery_complete &= passed
        discovery_details["|".join(config)] = {
            "rows": len(rows),
            "cases": len(by_case),
            "passed": passed,
        }

    operational_details: JSON = {}
    operational = True
    for config in sorted(capacity_configs):
        capacity = capacity_by_config[config]
        discovery = discovery_by_config.get((config[0], config[1]), [])
        combined = capacity + discovery
        failure_rate = mean(is_infrastructure_failure(row) for row in combined)
        v128 = [row for row in capacity if _condition(row).get("visible_tools") == 128]
        v128_failure = (
            mean(is_infrastructure_failure(row) for row in v128)
            if v128
            else 1.0
        )
        passed = failure_rate <= 0.02 and v128_failure <= 0.02
        operational &= passed
        operational_details["|".join(config)] = {
            "provider_failure_rate": failure_rate,
            "v128_provider_failure_rate": v128_failure,
            "passed": passed,
        }

    def success(row: Mapping[str, object]) -> bool:
        verdict = row.get("verdict")
        return isinstance(verdict, dict) and verdict.get("verified_success") is True

    primary_capacity_rows = [
        row
        for row in capacity_rows
        if StudyRole(str(row.get("study_role", StudyRole.PRIMARY.value)))
        is StudyRole.PRIMARY
    ]
    easy = [
        row
        for row in primary_capacity_rows
        if _condition(row).get("visible_tools") == 8
        and _condition(row).get("workflow") == "low"
        and _condition(row).get("representation") == "raw"
    ]
    stress = [
        row
        for row in primary_capacity_rows
        if _condition(row).get("visible_tools") == 128
        and _condition(row).get("workflow") == "high"
        and _condition(row).get("representation") == "linked"
    ]
    easy_rate = mean(success(row) for row in easy) if easy else 0.0
    stress_rate = mean(success(row) for row in stress) if stress else 0.0
    mixed_configs = 0
    for config in sorted(capacity_configs):
        if config[2] != StudyRole.PRIMARY.value:
            continue
        outcomes = {success(row) for row in capacity_by_config[config]}
        mixed_configs += outcomes == {False, True}
    active_range = (
        easy_rate >= 0.20
        and stress_rate <= 0.80
        and easy_rate - stress_rate >= 0.10
        and mixed_configs >= max(2, minimum_models // 2)
    )

    def axis_rates(factor: str) -> dict[object, float]:
        grouped: dict[object, list[bool]] = defaultdict(list)
        for row in primary_capacity_rows:
            condition = _condition(row)
            at_anchor = (
                (factor == "visible_tools" and condition.get("workflow") == "low" and condition.get("representation") == "raw")
                or (factor == "workflow" and condition.get("visible_tools") == 8 and condition.get("representation") == "raw")
                or (factor == "representation" and condition.get("visible_tools") == 8 and condition.get("workflow") == "low")
            )
            if at_anchor:
                grouped[condition.get(factor)].append(success(row))
        return {
            level: mean(values) for level, values in grouped.items() if values
        }

    visible_rates = axis_rates("visible_tools")
    workflow_rates = axis_rates("workflow")
    representation_rates = axis_rates("representation")
    visible_gap = visible_rates.get(8, 0.0) - visible_rates.get(128, 0.0)
    workflow_gap = workflow_rates.get("low", 0.0) - workflow_rates.get("high", 0.0)
    representation_spread = (
        max(representation_rates.values()) - min(representation_rates.values())
        if len(representation_rates) == 4
        else 0.0
    )
    visible_positive_configs = 0
    workflow_positive_configs = 0
    representation_active_configs = 0
    representation_config_spreads: dict[str, float] = {}
    for config in sorted(capacity_configs):
        if config[2] != StudyRole.PRIMARY.value:
            continue
        rows = capacity_by_config[config]
        by_visible = {
            level: [
                success(row)
                for row in rows
                if _condition(row).get("visible_tools") == level
                and _condition(row).get("workflow") == "low"
                and _condition(row).get("representation") == "raw"
            ]
            for level in (8, 128)
        }
        by_workflow = {
            level: [
                success(row)
                for row in rows
                if _condition(row).get("workflow") == level
                and _condition(row).get("visible_tools") == 8
                and _condition(row).get("representation") == "raw"
            ]
            for level in ("low", "high")
        }
        by_representation = {
            level: [
                success(row)
                for row in rows
                if _condition(row).get("representation") == level
                and _condition(row).get("visible_tools") == 8
                and _condition(row).get("workflow") == "low"
            ]
            for level in ("raw", "generic_compact", "typed", "linked")
        }
        if all(by_visible.values()) and mean(by_visible[8]) > mean(by_visible[128]):
            visible_positive_configs += 1
        if all(by_workflow.values()) and mean(by_workflow["low"]) > mean(by_workflow["high"]):
            workflow_positive_configs += 1
        config_representation_rates = [
            mean(values) for values in by_representation.values() if values
        ]
        if len(config_representation_rates) == 4:
            config_spread = max(config_representation_rates) - min(
                config_representation_rates
            )
            representation_config_spreads["|".join(config)] = config_spread
            if config_spread >= 0.05:
                representation_active_configs += 1
    mean_representation_config_spread = (
        mean(representation_config_spreads.values())
        if len(representation_config_spreads) == len(primary_configs)
        else 0.0
    )
    visible_active = (
        set(visible_rates) == {8, 32, 128}
        and visible_gap >= 0.05
        and visible_positive_configs >= max(2, minimum_models // 2)
    )
    workflow_active = (
        set(workflow_rates) == {"low", "medium", "high"}
        and workflow_gap >= 0.10
        and workflow_positive_configs >= max(2, minimum_models // 2)
    )
    workflow_boundary_valid = (
        isinstance(workflow_boundary_admission, Mapping)
        and workflow_boundary_admission.get("audit")
        == "workflow-capacity-frontier-development-admission-v1"
        and workflow_boundary_admission.get("split") == "development"
        and workflow_boundary_admission.get("passed") is True
        and workflow_boundary_admission.get("model_panel_hash") == model_panel_hash
        and workflow_boundary_admission.get("protocol_artifact_hash")
        == protocol_artifact_hash
    )
    workflow_construct_active = workflow_active or workflow_boundary_valid
    representation_active = (
        set(representation_rates) == {"raw", "generic_compact", "typed", "linked"}
        and mean_representation_config_spread >= 0.05
        and representation_active_configs >= max(2, minimum_models // 2)
    )
    distractor_attempt_rates: dict[int, float] = {}
    for level in (8, 32, 128):
        rows = [
            row
            for row in primary_capacity_rows
            if _condition(row).get("visible_tools") == level
        ]
        distractor_attempt_rates[level] = mean(
            any(
                isinstance(event, dict) and event.get("distractor") is True
                for event in row.get("trace", [])
            )
            for row in rows
        ) if rows else 0.0

    discovery_rates: dict[tuple[str, int], list[bool]] = defaultdict(list)
    for row in discovery_rows:
        condition = _condition(row)
        discovery_rates[
            (str(condition.get("retriever")), int(condition.get("registry_size", 0)))
        ].append(row.get("success") is True)
    rate_values = [mean(values) for values in discovery_rates.values() if values]
    discovery_range = bool(rate_values) and max(rate_values) - min(rate_values) >= 0.10

    token_reporting = all(
        isinstance(row.get("usage"), dict)
        and isinstance(row["usage"].get("total_tokens"), int)
        and (
            row.get("status") in {"capability_unavailable", "provider_failure"}
            or row["usage"]["total_tokens"] > 0
        )
        for row in (*capacity_rows, *discovery_rows)
    )
    plan_closure = all(
        len({row.get("plan_hash") for row in rows}) == 1
        and isinstance(rows[0].get("plan_hash"), str)
        and bool(rows[0].get("plan_hash"))
        for rows in (*capacity_by_config.values(), *discovery_by_config.values())
        if rows
    )
    try:
        precision = prospective_precision(primary_capacity_rows)
    except ValueError as exc:
        precision = {
            "analysis": "factorized-capacity-prospective-precision",
            "passed": False,
            "error": str(exc),
        }
    try:
        legacy_power_diagnostic = prospective_power(primary_capacity_rows)
    except ValueError as exc:
        legacy_power_diagnostic = {
            "analysis": "factorized-capacity-prospective-power",
            "passed": False,
            "error": str(exc),
        }
    precision["legacy_eight_effect_power_diagnostic"] = legacy_power_diagnostic
    checks = [
        _check("unique_condition_ids", unique, {"capacity": len(capacity_ids), "discovery": len(discovery_ids)}),
        _check("development_split_integrity", split_integrity, {"capacity": len(capacity_rows), "discovery": len(discovery_rows)}),
        _check(
            "construct_validity",
            construct_validity,
            {"required": sorted(required_audits), "observed": sorted(audits_by_name)},
        ),
        _check(
            "model_panel",
            panel_closed
            and panel_matches_freeze
            and len(models) >= minimum_models
            and len(primary_configs) == len(robustness_configs) == len(models)
            and {model for model, _ in robustness_configs} == models,
            {
                "minimum_models": minimum_models,
                "panel_closed": panel_closed,
                "matches_frozen_panel": panel_matches_freeze,
                "models": sorted(models),
                "capacity_configurations": [list(item) for item in sorted(capacity_configs)],
                "discovery_configurations": [list(item) for item in sorted(discovery_configs)],
                "expected_capacity_configurations": [list(item) for item in sorted(expected_capacity)],
                "expected_discovery_configurations": [list(item) for item in sorted(expected_discovery)],
            },
        ),
        _check("plan_closure", plan_closure, {"capacity_configurations": len(capacity_configs), "discovery_configurations": len(discovery_configs)}),
        _check("capacity_matrix_complete", capacity_complete, capacity_details),
        _check("discovery_matrix_complete", discovery_complete, discovery_details),
        _check("provider_context_feasibility", operational, operational_details),
        _check(
            "joint_capacity_active_range",
            active_range,
            {
                "easy_rate": easy_rate,
                "stress_rate": stress_rate,
                "gap": easy_rate - stress_rate,
                "mixed_configurations": mixed_configs,
            },
        ),
        _check(
            "visible_load_active_range",
            visible_active,
            {
                "success_rates": visible_rates,
                "v8_minus_v128": visible_gap,
                "positive_configurations": visible_positive_configs,
                "minimum_gap": 0.05,
                "distractor_attempt_rates": distractor_attempt_rates,
            },
        ),
        _check(
            "workflow_load_active_range",
            workflow_construct_active,
            {
                "success_rates": workflow_rates,
                "low_minus_high": workflow_gap,
                "positive_configurations": workflow_positive_configs,
                "minimum_gap": 0.10,
                "core_three_level_active": workflow_active,
                "boundary_extension_active": workflow_boundary_valid,
                "measurement_rule": (
                    "the unchanged core three-level axis may satisfy this gate directly; "
                    "otherwise a separately frozen, passing long-workflow boundary admission "
                    "must demonstrate the same minimum active range without changing core cells"
                ),
            },
        ),
        _check(
            "representation_active_range",
            representation_active,
            {
                "success_rates": representation_rates,
                "pooled_maximum_minus_minimum": representation_spread,
                "mean_configuration_spread": mean_representation_config_spread,
                "configuration_spreads": representation_config_spreads,
                "active_configurations": representation_active_configs,
                "minimum_mean_configuration_spread": 0.05,
                "minimum_active_configurations": max(
                    2, minimum_models // 2
                ),
            },
        ),
        _check(
            "discovery_active_range",
            discovery_range,
            {"minimum_rate": min(rate_values, default=0.0), "maximum_rate": max(rate_values, default=0.0)},
        ),
        _check("token_reporting", token_reporting, {"rows": len(capacity_rows) + len(discovery_rows)}),
        _check("prospective_precision", precision["passed"] is True, precision),
    ]
    payload: JSON = {
        "audit": "factorized-capacity-development-admission",
        "split": "development",
        "passed": all(check["passed"] for check in checks),
        "checks": checks,
        "model_panel_hash": model_panel_hash,
        "protocol_artifact_hash": protocol_artifact_hash,
        "workflow_boundary_admission_hash": (
            stable_hash(dict(workflow_boundary_admission))
            if isinstance(workflow_boundary_admission, Mapping)
            else None
        ),
        "capacity_rows_hash": stable_hash(list(capacity_rows)),
        "discovery_rows_hash": stable_hash(list(discovery_rows)),
    }
    payload["audit_hash"] = stable_hash(payload)
    return payload
