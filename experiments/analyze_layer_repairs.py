#!/usr/bin/env python3
"""Frozen diagnosis-to-repair analysis for the four benchmark layers."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import defaultdict
from pathlib import Path
from statistics import fmean
from typing import Iterable, Mapping, Sequence

from bottleneckbench.analysis import (
    analyze_repair_rows,
    holm_adjust,
    is_infrastructure_failure,
)


JSON = dict[str, object]
METRICS = (
    "diagnosed_loss",
    "stress_repair_gain",
    "low_pressure_repair_gain",
    "repair_selectivity",
    "residual_loss",
)
RETAINED_ITT_INFRASTRUCTURE_IDS = frozenset(
    {"fbeab4aed2d7e60bc703cb7b51e1190d13abdbb08b044f2a050f17940adb9db9"}
)


def _load_jsonl(
    paths: Sequence[Path],
    *,
    expected_infrastructure_ids: frozenset[str] = frozenset(),
) -> list[JSON]:
    rows: list[JSON] = []
    seen: set[str] = set()
    observed_infrastructure: set[str] = set()
    for path in paths:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"non-object row at {path}:{number}")
            condition_id = value.get("condition_id")
            if not isinstance(condition_id, str) or not condition_id:
                raise ValueError(f"missing condition_id at {path}:{number}")
            if condition_id in seen:
                raise ValueError(f"duplicate condition_id: {condition_id}")
            if is_infrastructure_failure(value) and condition_id not in expected_infrastructure_ids:
                raise ValueError(f"infrastructure failure in active input: {condition_id}")
            if is_infrastructure_failure(value):
                observed_infrastructure.add(condition_id)
            seen.add(condition_id)
            rows.append(value)
    if observed_infrastructure != set(expected_infrastructure_ids):
        raise ValueError(
            "retained ITT infrastructure set differs from its frozen condition IDs"
        )
    return rows


def _condition(row: Mapping[str, object]) -> Mapping[str, object]:
    value = row.get("condition")
    if not isinstance(value, dict):
        raise ValueError("analysis row is missing condition")
    return value


def _capacity_success(row: Mapping[str, object]) -> float:
    verdict = row.get("verdict")
    return float(isinstance(verdict, dict) and verdict.get("verified_success") is True)


def _drop_infrastructure_clusters(
    rows: Sequence[Mapping[str, object]], unit_key: str
) -> list[Mapping[str, object]]:
    contaminated = {
        (str(_condition(row)["model"]), str(_condition(row)[unit_key]))
        for row in rows
        if is_infrastructure_failure(row)
    }
    return [
        row for row in rows
        if (str(_condition(row)["model"]), str(_condition(row)[unit_key])) not in contaminated
    ]


def _quad(
    base_low: float,
    base_stress: float,
    repair_low: float,
    repair_stress: float,
) -> dict[str, float]:
    return {
        "diagnosed_loss": base_low - base_stress,
        "stress_repair_gain": repair_stress - base_stress,
        "low_pressure_repair_gain": repair_low - base_low,
        "repair_selectivity": (
            repair_stress - base_stress - repair_low + base_low
        ),
        "residual_loss": base_low - repair_stress,
    }


def _bootstrap(values: Sequence[float], *, samples: int, seed: int) -> JSON:
    if not values or samples <= 0:
        raise ValueError("bootstrap requires values and positive samples")
    rng = random.Random(seed)
    draws = sorted(fmean(rng.choices(values, k=len(values))) for _ in range(samples))
    return {
        "estimate": fmean(values),
        "ci95": [
            draws[int(0.025 * (samples - 1))],
            draws[int(0.975 * (samples - 1))],
        ],
        "clusters": len(values),
        "bootstrap_samples": samples,
    }


def _sign_flip_p(values: Sequence[float], *, samples: int, seed: int) -> float:
    observed = fmean(values)
    rng = random.Random(seed)
    extreme = sum(
        fmean(value if rng.getrandbits(1) else -value for value in values)
        >= observed
        for _ in range(samples)
    )
    return (extreme + 1) / (samples + 1)


def _aggregate(
    contributions: Sequence[tuple[str, str, Mapping[str, float]]],
) -> tuple[dict[str, list[float]], dict[str, dict[str, float]]]:
    clustered: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    by_model: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for cluster, model, metrics in contributions:
        for metric in METRICS:
            clustered[cluster][metric].append(float(metrics[metric]))
            by_model[model][metric].append(float(metrics[metric]))
    cluster_values = {
        metric: [
            fmean(values[metric]) for _, values in sorted(clustered.items())
        ]
        for metric in METRICS
    }
    model_values = {
        model: {metric: fmean(values[metric]) for metric in METRICS}
        for model, values in sorted(by_model.items())
    }
    return cluster_values, model_values


def _recluster(
    contributions: Sequence[tuple[str, str, Mapping[str, float]]],
    labels: Mapping[str, str],
) -> list[tuple[str, str, Mapping[str, float]]]:
    return [(labels[cluster], model, metrics) for cluster, model, metrics in contributions]


def _retrieval_contributions(
    rows: Sequence[Mapping[str, object]],
) -> list[tuple[str, str, Mapping[str, float]]]:
    cells: dict[tuple[str, str, str], dict[tuple[str, int], float]] = defaultdict(dict)
    tools: dict[str, str] = {}
    for row in rows:
        condition = _condition(row)
        key = (
            str(condition["case_id"]),
            str(condition["model"]),
            str(condition["harness"]),
        )
        coordinate = (
            str(condition["retriever"]),
            int(condition["registry_size"]),
        )
        if coordinate in cells[key]:
            raise ValueError("duplicate retrieval repair cell")
        cells[key][coordinate] = float(row.get("success") is True)
        exposure = row.get("exposure")
        tool = exposure.get("required_tool") if isinstance(exposure, dict) else None
        if not isinstance(tool, str) or not tool:
            raise ValueError("retrieval row is missing required_tool")
        tools[key[0]] = tool
    required = {
        ("flat", 32),
        ("flat", 512),
        ("flat", 2048),
        ("hierarchical", 32),
        ("hierarchical", 512),
        ("hierarchical", 2048),
    }
    contributions = []
    for (case, model, _), values in sorted(cells.items()):
        if set(values) != required:
            raise ValueError(f"incomplete retrieval repair cell: {case}|{model}")
        contributions.append(
            (
                tools[case],
                model,
                _quad(
                    values[("flat", 32)],
                    values[("flat", 2048)],
                    values[("hierarchical", 32)],
                    values[("hierarchical", 2048)],
                ),
            )
        )
    return contributions


def _routing_contributions(
    rows: Sequence[Mapping[str, object]],
) -> list[tuple[str, str, Mapping[str, float]]]:
    strata = (("low", "raw"), ("high", "linked"))
    cells: dict[
        tuple[str, str, tuple[str, str]], dict[tuple[str, int], float]
    ] = defaultdict(dict)
    for row in rows:
        role = str(row.get("study_role", "primary"))
        if role not in {"primary", "robustness"}:
            continue
        condition = _condition(row)
        stratum = (str(condition["workflow"]), str(condition["representation"]))
        visible = int(condition["visible_tools"])
        if stratum not in strata or visible not in {8, 128}:
            continue
        key = (str(condition["family_id"]), str(condition["model"]), stratum)
        coordinate = (role, visible)
        if coordinate in cells[key]:
            raise ValueError("duplicate routing repair cell")
        cells[key][coordinate] = _capacity_success(row)
    required = {("robustness", 8), ("robustness", 128), ("primary", 8), ("primary", 128)}
    by_family_model: dict[tuple[str, str], list[Mapping[str, float]]] = defaultdict(list)
    for (family, model, _), values in sorted(cells.items()):
        if set(values) != required:
            raise ValueError(f"incomplete routing repair cell: {family}|{model}")
        by_family_model[(family, model)].append(
            _quad(
                values[("robustness", 8)],
                values[("robustness", 128)],
                values[("primary", 8)],
                values[("primary", 128)],
            )
        )
    contributions = []
    for (family, model), items in sorted(by_family_model.items()):
        if len(items) != len(strata):
            raise ValueError(f"routing strata are incomplete: {family}|{model}")
        contributions.append(
            (family, model, {metric: fmean(item[metric] for item in items) for metric in METRICS})
        )
    return contributions


def _policy_contributions(
    rows: Sequence[Mapping[str, object]],
    *,
    repair_role: str,
    low: tuple[int, str, str],
    stress: tuple[int, str, str],
) -> list[tuple[str, str, Mapping[str, float]]]:
    cells: dict[
        tuple[str, str], dict[tuple[str, tuple[int, str, str]], float]
    ] = defaultdict(dict)
    for row in rows:
        role = str(row.get("study_role", "primary"))
        if role not in {"primary", repair_role}:
            continue
        condition = _condition(row)
        coordinate = (
            int(condition["visible_tools"]),
            str(condition["workflow"]),
            str(condition["representation"]),
        )
        if coordinate not in {low, stress}:
            continue
        key = (str(condition["family_id"]), str(condition["model"]))
        cell = (role, coordinate)
        if cell in cells[key]:
            raise ValueError("duplicate policy repair cell")
        cells[key][cell] = _capacity_success(row)
    required = {
        ("primary", low),
        ("primary", stress),
        (repair_role, low),
        (repair_role, stress),
    }
    contributions = []
    for (family, model), values in sorted(cells.items()):
        if set(values) != required:
            raise ValueError(f"incomplete {repair_role} cell: {family}|{model}")
        contributions.append(
            (
                family,
                model,
                _quad(
                    values[("primary", low)],
                    values[("primary", stress)],
                    values[(repair_role, low)],
                    values[(repair_role, stress)],
                ),
            )
        )
    return contributions


def _summary(
    contributions: Sequence[tuple[str, str, Mapping[str, float]]],
    *,
    name: str,
    cluster_unit: str,
    organizations: Mapping[str, str],
    bootstrap_samples: int,
    randomization_samples: int,
    seed: int,
) -> JSON:
    cluster_values, model_values = _aggregate(contributions)
    if not cluster_values["repair_selectivity"] or not model_values:
        raise ValueError(f"{name} has no complete repair contributions")
    summaries = {
        metric: _bootstrap(
            values,
            samples=bootstrap_samples,
            seed=seed + int(hashlib.sha256(f"{name}:{metric}".encode()).hexdigest()[:8], 16),
        )
        for metric, values in cluster_values.items()
    }
    component_metrics = (
        "diagnosed_loss",
        "stress_repair_gain",
        "repair_selectivity",
    )
    component_p = {
        metric: _sign_flip_p(
            cluster_values[metric],
            samples=randomization_samples,
            seed=seed + int(hashlib.sha256(f"{name}:{metric}:sign".encode()).hexdigest()[:8], 16),
        )
        for metric in component_metrics
    }
    model_selectivity = [values["repair_selectivity"] for values in model_values.values()]
    organizations_present = sorted({organizations[model] for model in model_values})
    organization_selectivity = {
        organization: fmean(
            values["repair_selectivity"]
            for model, values in model_values.items()
            if organizations[model] == organization
        )
        for organization in organizations_present
    }
    return {
        "cluster_unit": cluster_unit,
        "clusters": len(cluster_values["repair_selectivity"]),
        "configurations": len(model_values),
        "metrics": summaries,
        "activation_test": {
            "test": "intersection-union of positive diagnosed loss, stress repair gain, and repair selectivity",
            "component_p_one_sided": component_p,
            "raw_p": max(component_p.values()),
            "all_point_directions_positive": all(
                float(summaries[metric]["estimate"]) > 0 for metric in component_metrics
            ),
        },
        "recoverable_loss_fraction": (
            float(summaries["stress_repair_gain"]["estimate"])
            / float(summaries["diagnosed_loss"]["estimate"])
            if float(summaries["diagnosed_loss"]["estimate"]) > 0
            else None
        ),
        "by_configuration": model_values,
        "configuration_bootstrap_sensitivity": _bootstrap(
            model_selectivity,
            samples=bootstrap_samples,
            seed=seed + int(hashlib.sha256(f"{name}:models".encode()).hexdigest()[:8], 16),
        ),
        "organization_balanced_selectivity": fmean(organization_selectivity.values()),
        "selectivity_by_organization": organization_selectivity,
        "leave_one_organization_out_selectivity": {
            organization: fmean(
                values["repair_selectivity"]
                for model, values in model_values.items()
                if organizations[model] != organization
            )
            for organization in organizations_present
            if any(organizations[model] != organization for model in model_values)
        },
    }


def analyze(
    *,
    prospective_capacity: Sequence[Mapping[str, object]],
    prospective_discovery: Sequence[Mapping[str, object]],
    all_capacity: Sequence[Mapping[str, object]],
    all_discovery: Sequence[Mapping[str, object]],
    repair_rows: Sequence[Mapping[str, object]],
    organizations: Mapping[str, str],
    eligible_models: set[str] | None = None,
    activation_models: set[str] | None = None,
    auxiliary_models: set[str] | None = None,
    bootstrap_samples: int = 10_000,
    randomization_samples: int = 10_000,
    seed: int = 20260813,
) -> JSON:
    prospective_models = {str(_condition(row)["model"]) for row in prospective_capacity}
    prospective_discovery_models = {
        str(_condition(row)["model"]) for row in prospective_discovery
    }
    all_models = {str(_condition(row)["model"]) for row in all_capacity}
    all_discovery_models = {str(_condition(row)["model"]) for row in all_discovery}
    if eligible_models is not None and all_models != eligible_models:
        raise ValueError("core input does not match the frozen eligible model set")
    if activation_models is not None and prospective_models != activation_models:
        raise ValueError("prospective input does not match the frozen activation model set")
    if not prospective_models or not prospective_models <= all_models:
        raise ValueError("activation configurations must be a non-empty subset of the synthesis panel")
    if prospective_discovery_models != prospective_models or all_discovery_models != all_models:
        raise ValueError("capacity and discovery model panels do not match")
    if not all_models <= set(organizations):
        raise ValueError("model panel is missing organization identities")

    observed_rows = {
        "prospective_capacity": len(prospective_capacity),
        "prospective_discovery": len(prospective_discovery),
        "all_capacity": len(all_capacity),
        "all_discovery": len(all_discovery),
        "repair": len(repair_rows),
    }
    expected_rows = {
        "prospective_capacity": len(prospective_models) * 576,
        "prospective_discovery": len(prospective_models) * 216,
        "all_capacity": len(all_models) * 576,
        "all_discovery": len(all_models) * 216,
        "repair": len(auxiliary_models or set()) * 144,
    }
    if observed_rows != expected_rows:
        raise ValueError(
            f"analysis inputs are not estimate-complete: expected {expected_rows}, "
            f"observed {observed_rows}"
        )

    family_to_intent: dict[str, str] = {}
    for row in all_capacity:
        condition = _condition(row)
        family, intent = str(condition["family_id"]), str(condition["intent"])
        previous = family_to_intent.setdefault(family, intent)
        if previous != intent:
            raise ValueError(f"family maps to multiple intents: {family}")
    intent_counts: dict[str, int] = defaultdict(int)
    for intent in family_to_intent.values():
        intent_counts[intent] += 1
    if len(family_to_intent) != 36 or set(intent_counts.values()) != {3} or len(intent_counts) != 12:
        raise ValueError("capacity families do not close at 12 intents by 3 variants")

    combined = [*all_capacity, *repair_rows]
    repair_models = {str(_condition(row)["model"]) for row in repair_rows}
    if auxiliary_models is not None and repair_models != auxiliary_models:
        raise ValueError("repair input does not match the frozen auxiliary model set")
    if not prospective_models <= repair_models:
        raise ValueError(
            f"expected repairs for all activation configurations; "
            f"observed {len(prospective_models & repair_models)} and {len(repair_models)}"
        )
    repair_complete_combined = [
        row for row in combined if str(_condition(row)["model"]) in repair_models
    ]
    missing_repair_arms = sorted(all_models - repair_models)
    primary = {
        "retrieval": _summary(
            _retrieval_contributions(all_discovery),
            name="retrieval",
            cluster_unit="required_tool",
            organizations=organizations,
            bootstrap_samples=bootstrap_samples,
            randomization_samples=randomization_samples,
            seed=seed,
        ),
        "visible_menu": _summary(
            _routing_contributions(all_capacity),
            name="visible_menu",
            cluster_unit="semantic_family",
            organizations=organizations,
            bootstrap_samples=bootstrap_samples,
            randomization_samples=randomization_samples,
            seed=seed,
        ),
        "workflow": _summary(
            _policy_contributions(
                repair_complete_combined,
                repair_role="verification_repair",
                low=(8, "low", "raw"),
                stress=(8, "high", "raw"),
            ),
            name="workflow",
            cluster_unit="semantic_family",
            organizations=organizations,
            bootstrap_samples=bootstrap_samples,
            randomization_samples=randomization_samples,
            seed=seed,
        ),
        "returned_state_interface": _summary(
            _policy_contributions(
                repair_complete_combined,
                repair_role="interface_guide",
                low=(8, "low", "raw"),
                stress=(8, "low", "linked"),
            ),
            name="returned_state_interface",
            cluster_unit="semantic_family",
            organizations=organizations,
            bootstrap_samples=bootstrap_samples,
            randomization_samples=randomization_samples,
            seed=seed,
        ),
    }
    adjusted = holm_adjust(
        {name: float(value["activation_test"]["raw_p"]) for name, value in primary.items()}
    )
    for name, holm_p in adjusted.items():
        primary[name]["activation_test"]["holm_p"] = holm_p
        primary[name]["activation_test"]["activated_at_0.05"] = (
            primary[name]["activation_test"]["all_point_directions_positive"] is True
            and holm_p < 0.05
        )

    intent_clustered_sensitivity = {
        "visible_menu": _summary(
            _recluster(_routing_contributions(all_capacity), family_to_intent),
            name="visible_menu_intent_sensitivity",
            cluster_unit="semantic_intent",
            organizations=organizations,
            bootstrap_samples=bootstrap_samples,
            randomization_samples=randomization_samples,
            seed=seed,
        ),
        "workflow": _summary(
            _recluster(
                _policy_contributions(
                    repair_complete_combined,
                    repair_role="verification_repair",
                    low=(8, "low", "raw"),
                    stress=(8, "high", "raw"),
                ),
                family_to_intent,
            ),
            name="workflow_intent_sensitivity",
            cluster_unit="semantic_intent",
            organizations=organizations,
            bootstrap_samples=bootstrap_samples,
            randomization_samples=randomization_samples,
            seed=seed,
        ),
        "returned_state_interface": _summary(
            _recluster(
                _policy_contributions(
                    repair_complete_combined,
                    repair_role="interface_guide",
                    low=(8, "low", "raw"),
                    stress=(8, "low", "linked"),
                ),
                family_to_intent,
            ),
            name="returned_state_interface_intent_sensitivity",
            cluster_unit="semantic_intent",
            organizations=organizations,
            bootstrap_samples=bootstrap_samples,
            randomization_samples=randomization_samples,
            seed=seed,
        ),
    }

    complete_case_sensitivity = None
    combined_cc: list[Mapping[str, object]] = []
    if any(is_infrastructure_failure(row) for row in (*all_capacity, *all_discovery)):
        capacity_cc = _drop_infrastructure_clusters(all_capacity, "family_id")
        discovery_cc = _drop_infrastructure_clusters(all_discovery, "case_id")
        combined_cc = _drop_infrastructure_clusters(repair_complete_combined, "family_id")
        complete_case_sensitivity = {
            "confirmatory_weight": 0,
            "policy": "drop the complete model-by-semantic-unit cluster containing any infrastructure row",
            "layers": {
                "retrieval": _summary(
                    _retrieval_contributions(discovery_cc), name="retrieval_complete_case",
                    cluster_unit="required_tool", organizations=organizations,
                    bootstrap_samples=bootstrap_samples, randomization_samples=randomization_samples, seed=seed,
                ),
                "visible_menu": _summary(
                    _routing_contributions(capacity_cc), name="visible_menu_complete_case",
                    cluster_unit="semantic_family", organizations=organizations,
                    bootstrap_samples=bootstrap_samples, randomization_samples=randomization_samples, seed=seed,
                ),
                "workflow": _summary(
                    _policy_contributions(combined_cc, repair_role="verification_repair",
                                          low=(8, "low", "raw"), stress=(8, "high", "raw")),
                    name="workflow_complete_case", cluster_unit="semantic_family", organizations=organizations,
                    bootstrap_samples=bootstrap_samples, randomization_samples=randomization_samples, seed=seed,
                ),
                "returned_state_interface": _summary(
                    _policy_contributions(combined_cc, repair_role="interface_guide",
                                          low=(8, "low", "raw"), stress=(8, "low", "linked")),
                    name="returned_state_interface_complete_case", cluster_unit="semantic_family", organizations=organizations,
                    bootstrap_samples=bootstrap_samples, randomization_samples=randomization_samples, seed=seed,
                ),
            },
        }
    # The shared implementation must agree with the independent extraction for the two
    # policy arms.  The shared analyzer rejects retained-ITT infrastructure outcomes, so
    # it is run on the same complete-case rows the sensitivity block uses when any exist.
    crosscheck_rows = (
        combined_cc if complete_case_sensitivity is not None else repair_complete_combined
    )
    crosscheck_reference = (
        complete_case_sensitivity["layers"]
        if complete_case_sensitivity is not None
        else primary
    )
    existing_shared = (
        None
        if any(is_infrastructure_failure(row) for row in crosscheck_rows)
        else analyze_repair_rows(
            crosscheck_rows,
            bootstrap_samples=bootstrap_samples,
            randomization_samples=randomization_samples,
            seed=seed,
        )
    )
    crosscheck: JSON = {}
    if existing_shared is None:
        crosscheck = {
            "status": "not_applicable",
            "reason": "the shared analyzer rejects retained ITT infrastructure outcomes",
        }
    else:
        crosscheck["compared_against"] = (
            "infrastructure_complete_case_sensitivity"
            if complete_case_sensitivity is not None
            else "primary_activation"
        )
        for layer, existing_name in (
            ("workflow", "verification"),
            ("returned_state_interface", "interface_guide"),
        ):
            observed = float(
                crosscheck_reference[layer]["metrics"]["repair_selectivity"]["estimate"]
            )
            expected = float(
                existing_shared["effects"][existing_name]["metrics"]["repair_selectivity"]["estimate"]
            )
            if abs(observed - expected) > 1e-12:
                raise AssertionError(f"{layer} extraction disagrees with shared analyzer")
            crosscheck[layer] = {"passed": True, "estimate": observed}
    return {
        "analysis": "layer-selective-diagnosis-to-repair-v4",
        "construct_scope": {
            "retrieval": "deterministic system-retriever calibration",
            "visible_menu": "conditional native-tool menu load",
            "workflow": "bundled certified workflow-and-evidence load",
            "returned_state_interface": "deployable raw-versus-linked interface bundle",
        },
        "estimate_complete_row_counts": observed_rows,
        "layer_configurations": {
            "retrieval": len(all_discovery_models),
            "visible_menu": len(all_models),
            "workflow": len(repair_models),
            "returned_state_interface": len(repair_models),
        },
        "configurations_without_repair_arms": missing_repair_arms,
        "primary_activation": primary,
        "intent_clustered_sensitivity": {
            "confirmatory_weight": 0,
            "note": "Three compiler variants share each of twelve intent templates; these summaries recluster by intent and cannot activate a layer claim.",
            "layers": intent_clustered_sensitivity,
        },
        "shared_analyzer_crosscheck": crosscheck,
        "infrastructure_complete_case_sensitivity": complete_case_sensitivity
        or {
            "status": "not_applicable",
            "value": None,
            "reason": (
                "no released capacity or discovery row of the core panel is an "
                "infrastructure failure, so the complete-case sensitivity has no "
                "cluster to drop and would reproduce the primary analysis exactly"
            ),
        },
        "multiplicity": (
            "Each layer uses an intersection-union test; its maximum component p-value "
            "enters a four-layer Holm family. Every layer test runs on all released "
            "configurations whose arms are complete: retrieval and visible menu on the "
            "full core panel, workflow and returned-state interface on the "
            "configurations that carry a repair block."
        ),
        "inference_note": (
            "Intervals and sign flips cluster by required tool for retrieval and "
            "semantic family otherwise. Configuration bootstrap and leave-one-organization-out "
            "values are finite-panel sensitivity checks, not random-model population inference."
        ),
    }


def _self_check() -> None:
    models = ("m1", "m2")
    discovery: list[JSON] = []
    capacity: list[JSON] = []
    repair: list[JSON] = []
    for model in models:
        for index in range(2):
            case = f"c{index}"
            for retriever, size, success in (
                ("flat", 32, True),
                ("flat", 512, False),
                ("flat", 2048, False),
                ("hierarchical", 32, True),
                ("hierarchical", 512, True),
                ("hierarchical", 2048, True),
            ):
                discovery.append(
                    {
                        "condition_id": f"d-{model}-{case}-{retriever}-{size}",
                        "condition": {
                            "case_id": case,
                            "model": model,
                            "harness": "h",
                            "retriever": retriever,
                            "registry_size": size,
                        },
                        "exposure": {"required_tool": f"tool{index}"},
                        "success": success,
                    }
                )
            family = f"f{index}"
            for workflow, representation in (("low", "raw"), ("high", "linked")):
                for role, visible, success in (
                    ("robustness", 8, True),
                    ("robustness", 128, False),
                    ("primary", 8, True),
                    ("primary", 128, True),
                ):
                    capacity.append(
                        {
                            "condition_id": f"r-{model}-{family}-{workflow}-{role}-{visible}",
                            "study_role": role,
                            "condition": {
                                "family_id": family,
                                "model": model,
                                "visible_tools": visible,
                                "workflow": workflow,
                                "representation": representation,
                            },
                            "verdict": {"verified_success": success},
                        }
                    )
            for workflow, representation, success in (
                ("high", "raw", False),
                ("low", "linked", False),
            ):
                capacity.append(
                    {
                        "condition_id": f"p-{model}-{family}-{workflow}-{representation}",
                        "study_role": "primary",
                        "condition": {
                            "family_id": family,
                            "model": model,
                            "visible_tools": 8,
                            "workflow": workflow,
                            "representation": representation,
                        },
                        "verdict": {"verified_success": success},
                    }
                )
            for role, workflow, representation in (
                ("verification_repair", "low", "raw"),
                ("verification_repair", "high", "raw"),
                ("interface_guide", "low", "raw"),
                ("interface_guide", "low", "linked"),
            ):
                repair.append(
                    {
                        "condition_id": f"x-{model}-{family}-{role}-{workflow}-{representation}",
                        "study_role": role,
                        "condition": {
                            "family_id": family,
                            "model": model,
                            "visible_tools": 8,
                            "workflow": workflow,
                            "representation": representation,
                        },
                        "verdict": {"verified_success": True},
                    }
                )
    assert all(
        item[2]["repair_selectivity"] == 1.0
        for item in _retrieval_contributions(discovery)
    )
    assert all(
        item[2]["repair_selectivity"] == 1.0
        for item in _routing_contributions(capacity)
    )
    combined = [*capacity, *repair]
    assert all(
        item[2]["repair_selectivity"] == 1.0
        for item in _policy_contributions(
            combined,
            repair_role="verification_repair",
            low=(8, "low", "raw"),
            stress=(8, "high", "raw"),
        )
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prospective-capacity", type=Path, action="append")
    parser.add_argument("--prospective-discovery", type=Path, action="append")
    parser.add_argument("--retained-capacity", type=Path, action="append")
    parser.add_argument("--retained-discovery", type=Path, action="append")
    parser.add_argument("--repair", type=Path, action="append")
    parser.add_argument("--panel", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    parser.add_argument("--randomization-samples", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=20260813)
    parser.add_argument("--expected-prospective-capacity-infrastructure-id", action="append")
    parser.add_argument("--expected-prospective-discovery-infrastructure-id", action="append")
    parser.add_argument("--expected-repair-infrastructure-id", action="append")
    parser.add_argument("--self-check", action="store_true")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.self_check:
        _self_check()
        return 0
    required = (
        args.prospective_capacity,
        args.prospective_discovery,
        args.retained_capacity,
        args.retained_discovery,
        args.repair,
    )
    if any(not value for value in required) or args.panel is None or args.output is None:
        raise SystemExit("all input groups, --panel, and --output are required")
    prospective_capacity = _load_jsonl(
        args.prospective_capacity,
        expected_infrastructure_ids=frozenset(args.expected_prospective_capacity_infrastructure_id or ()),
    )
    prospective_discovery = _load_jsonl(
        args.prospective_discovery,
        expected_infrastructure_ids=frozenset(args.expected_prospective_discovery_infrastructure_id or ()),
    )
    retained_capacity = _load_jsonl(
        args.retained_capacity,
        expected_infrastructure_ids=RETAINED_ITT_INFRASTRUCTURE_IDS,
    )
    retained_discovery = _load_jsonl(args.retained_discovery)
    repair_rows = _load_jsonl(
        args.repair,
        expected_infrastructure_ids=frozenset(args.expected_repair_infrastructure_id or ()),
    )
    panel = json.loads(args.panel.read_text(encoding="utf-8"))
    configurations = panel["configurations"]
    organizations = {
        str(item["model"]): str(item["organization"])
        for item in configurations
    }
    eligible_models = set(organizations)
    activation_models = {str(model) for model in panel["activation_models"]}
    auxiliary_models = {str(model) for model in panel["auxiliary_models"]}
    result = analyze(
        prospective_capacity=prospective_capacity,
        prospective_discovery=prospective_discovery,
        all_capacity=[*retained_capacity, *prospective_capacity],
        all_discovery=[*retained_discovery, *prospective_discovery],
        repair_rows=repair_rows,
        organizations=organizations,
        eligible_models=eligible_models,
        activation_models=activation_models,
        auxiliary_models=auxiliary_models,
        bootstrap_samples=args.bootstrap_samples,
        randomization_samples=args.randomization_samples,
        seed=args.seed,
    )
    result["retained_itt_infrastructure_condition_ids"] = sorted(
        RETAINED_ITT_INFRASTRUCTURE_IDS
    )
    result["prospective_infrastructure_condition_ids"] = {
        "capacity": sorted(args.expected_prospective_capacity_infrastructure_id or ()),
        "discovery": sorted(args.expected_prospective_discovery_infrastructure_id or ()),
        "repair": sorted(args.expected_repair_infrastructure_id or ()),
    }
    inputs = [
        *args.prospective_capacity,
        *args.prospective_discovery,
        *args.retained_capacity,
        *args.retained_discovery,
        *args.repair,
        args.panel,
    ]
    result["inputs"] = [
        {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in inputs
    ]
    result["analysis_source_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
