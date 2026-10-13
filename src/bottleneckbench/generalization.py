"""Prospective model-generalization analyses for the Capacity Surface benchmark.

The first three-model study and the four-model OpenRouter replication are kept
as distinct evidence blocks.  This module reports each block, every fixed model
configuration, and a clearly labelled seven-model finite-panel synthesis.  It
never treats the selected models as a random sample from a model population.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import random
from statistics import mean, stdev
from typing import Callable, Mapping, Sequence

from bottleneckbench.core.analysis import holm_adjust
from bottleneckbench.core.schema import JSON, stable_hash

from bottleneckbench.analysis import (
    _registered_contrast_values,
    analyze_capacity_rows,
    analyze_discovery_rows,
)
from bottleneckbench.study_design import PRIMARY_CONTRASTS
from bottleneckbench.workflow_boundary import (
    WORKFLOW_BOUNDARY_LOCATOR_LEVELS,
    WORKFLOW_BOUNDARY_STUDY,
)


ROUTING_NONINFERIORITY_MARGIN = 0.05
NAMED_REPLICATION_CLAIMS = (
    "visible_load_loss",
    "routed_exposure_efficiency",
    "hierarchical_discovery_attenuation",
    "workflow_boundary_loss",
)


def _condition(row: Mapping[str, object]) -> Mapping[str, object]:
    condition = row.get("condition")
    if not isinstance(condition, dict):
        raise ValueError("analysis row is missing a condition object")
    return condition


def _model(row: Mapping[str, object]) -> str:
    model = _condition(row).get("model")
    if not isinstance(model, str) or not model:
        raise ValueError("analysis row is missing a model identity")
    return model


def _models(rows: Sequence[Mapping[str, object]]) -> tuple[str, ...]:
    return tuple(sorted({_model(row) for row in rows}))


def _success(row: Mapping[str, object]) -> float:
    verdict = row.get("verdict")
    return float(isinstance(verdict, dict) and verdict.get("verified_success") is True)


def _tool_schema_exposures(row: Mapping[str, object]) -> float:
    efficiency = row.get("efficiency")
    value = efficiency.get("tool_schema_exposures") if isinstance(efficiency, dict) else None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    response_ids = row.get("response_ids")
    requests = len(response_ids) if isinstance(response_ids, list) else 0
    visible_tools = _condition(row).get("visible_tools")
    if isinstance(visible_tools, (int, float)) and not isinstance(visible_tools, bool):
        return float(requests) * float(visible_tools)
    raise ValueError("routing row does not report tool-schema exposure")


def _validate_unique(rows: Sequence[Mapping[str, object]], *, label: str) -> None:
    identifiers = [row.get("condition_id") for row in rows]
    if any(not isinstance(value, str) or not value for value in identifiers):
        raise ValueError(f"{label} contains a missing condition ID")
    if len(set(identifiers)) != len(identifiers):
        raise ValueError(f"{label} contains duplicate condition IDs")


def _validate_block(
    capacity_rows: Sequence[Mapping[str, object]],
    discovery_rows: Sequence[Mapping[str, object]],
    *,
    label: str,
    expected_models: int,
    expected_capacity_rows_per_model: int | None,
    expected_discovery_rows_per_model: int | None,
) -> tuple[str, ...]:
    if not capacity_rows or not discovery_rows:
        raise ValueError(f"{label} requires both Capacity and Discovery rows")
    _validate_unique(capacity_rows, label=f"{label} Capacity")
    _validate_unique(discovery_rows, label=f"{label} Discovery")
    models = _models(capacity_rows)
    if len(models) != expected_models or set(models) != set(_models(discovery_rows)):
        raise ValueError(
            f"{label} model closure failed: expected {expected_models} shared models"
        )
    if expected_capacity_rows_per_model is not None:
        counts = Counter(_model(row) for row in capacity_rows)
        if set(counts.values()) != {expected_capacity_rows_per_model}:
            raise ValueError(
                f"{label} Capacity denominator differs by model: {dict(counts)}"
            )
    if expected_discovery_rows_per_model is not None:
        counts = Counter(_model(row) for row in discovery_rows)
        if set(counts.values()) != {expected_discovery_rows_per_model}:
            raise ValueError(
                f"{label} Discovery denominator differs by model: {dict(counts)}"
            )
    return models


def _filter_models(
    rows: Sequence[Mapping[str, object]], models: set[str]
) -> list[Mapping[str, object]]:
    return [row for row in rows if _model(row) in models]


def _bootstrap_directional(
    values: Sequence[float],
    *,
    direction: str,
    null_value: float,
    samples: int,
    seed: int,
) -> JSON:
    """Return a deterministic cluster bootstrap and a boundary-null p-value."""

    if not values or samples <= 0:
        raise ValueError("directional bootstrap requires values and positive samples")
    if direction not in {"less", "greater"}:
        raise ValueError("direction must be less or greater")
    observed = mean(values)
    rng = random.Random(seed)
    size = len(values)
    raw_draws = sorted(mean(rng.choices(values, k=size)) for _ in range(samples))
    boundary_values = [value - observed + null_value for value in values]
    boundary_draws = [
        mean(rng.choices(boundary_values, k=size)) for _ in range(samples)
    ]
    extreme = sum(
        draw <= observed if direction == "less" else draw >= observed
        for draw in boundary_draws
    )
    return {
        "estimate": observed,
        "ci95": [
            raw_draws[int(0.025 * (samples - 1))],
            raw_draws[int(0.975 * (samples - 1))],
        ],
        "one_sided_95_bound": (
            raw_draws[int(0.95 * (samples - 1))]
            if direction == "less"
            else raw_draws[int(0.05 * (samples - 1))]
        ),
        "direction": direction,
        "null_boundary": null_value,
        "bootstrap_p_one_sided": (extreme + 1) / (samples + 1),
        "clusters": size,
        "bootstrap_samples": samples,
    }


def _capacity_values(
    rows: Sequence[Mapping[str, object]], name: str
) -> list[float]:
    values, _, incomplete, _ = _registered_contrast_values(
        rows, PRIMARY_CONTRASTS[name], cluster_unit="family"
    )
    if incomplete:
        raise ValueError(f"Capacity claim {name} has {incomplete} incomplete cells")
    return values


def _fixed_menu_visible_values(
    rows: Sequence[Mapping[str, object]],
) -> list[float]:
    """Return the registered permanent-menu V128-minus-V8 family contrasts.

    The active visible-load claim is not the routed 2x2x2 factorial main
    effect.  It pools the two prospectively registered fixed-menu robustness
    strata, pairing V=8 and V=128 within model and semantic family before
    clustering at the family level.
    """

    registered_strata = (("low", "raw"), ("high", "linked"))
    required = {
        (visible, workflow, representation)
        for workflow, representation in registered_strata
        for visible in (8, 128)
    }
    cells: dict[tuple[str, str], dict[tuple[int, str, str], float]] = defaultdict(dict)
    observed_models: set[str] = set()
    observed_families: set[str] = set()
    for row in rows:
        if str(row.get("study_role", "primary")) != "robustness":
            continue
        condition = _condition(row)
        coordinate = (
            int(condition.get("visible_tools", 0)),
            str(condition.get("workflow")),
            str(condition.get("representation")),
        )
        if coordinate not in required:
            raise ValueError(
                "fixed-menu visible-load row has an unregistered robustness cell"
            )
        family = condition.get("family_id")
        model = condition.get("model")
        harness = condition.get("harness")
        if not all(
            isinstance(value, str) and value
            for value in (family, model, harness)
        ):
            raise ValueError(
                "fixed-menu visible-load row is missing identity metadata"
            )
        key = (family, model)
        if coordinate in cells[key]:
            raise ValueError("duplicate fixed-menu visible-load cell")
        cells[key][coordinate] = _success(row)
        observed_models.add(model)
        observed_families.add(family)

    if not cells:
        raise ValueError("fixed-menu visible-load claim has no robustness rows")
    family_models: dict[str, set[str]] = defaultdict(set)
    family_contributions: dict[str, list[float]] = defaultdict(list)
    for (family, model), values in cells.items():
        missing = required - set(values)
        if missing:
            raise ValueError(
                "fixed-menu visible-load claim has incomplete registered strata: "
                f"family={family}, model={model}, missing={sorted(missing)}"
            )
        stratum_differences = [
            values[(128, workflow, representation)]
            - values[(8, workflow, representation)]
            for workflow, representation in registered_strata
        ]
        family_contributions[family].append(mean(stratum_differences))
        family_models[family].add(model)

    if set(family_contributions) != observed_families or any(
        models != observed_models for models in family_models.values()
    ):
        raise ValueError(
            "fixed-menu visible-load families do not contain the same model panel"
        )
    return [
        mean(family_contributions[family])
        for family in sorted(family_contributions)
    ]


def _routing_values(
    rows: Sequence[Mapping[str, object]],
) -> tuple[list[float], list[float], int]:
    """Return family-clustered fixed-minus-routed success and exposure values."""

    def key(row: Mapping[str, object]) -> tuple[str, str, int, str, str]:
        condition = _condition(row)
        return (
            _model(row),
            str(condition.get("family_id")),
            int(condition.get("visible_tools", 0)),
            str(condition.get("workflow")),
            str(condition.get("representation")),
        )

    primary: dict[tuple[str, str, int, str, str], Mapping[str, object]] = {}
    fixed: dict[tuple[str, str, int, str, str], Mapping[str, object]] = {}
    for row in rows:
        role = str(row.get("study_role", "primary"))
        target = primary if role == "primary" else fixed if role == "robustness" else None
        if target is None:
            continue
        row_key = key(row)
        if row_key in target:
            raise ValueError("duplicate routing comparison cell")
        target[row_key] = row
    if not fixed or not set(fixed) <= set(primary):
        raise ValueError("routing comparison does not have complete routed counterparts")
    observed_models = {_model(row) for row in fixed.values()}
    success_by_family: dict[str, list[float]] = defaultdict(list)
    exposure_by_family: dict[str, list[float]] = defaultdict(list)
    models_by_family: dict[str, set[str]] = defaultdict(set)
    for row_key in sorted(fixed):
        routed_row = primary[row_key]
        fixed_row = fixed[row_key]
        model, family, _, _, _ = row_key
        success_by_family[family].append(
            _success(fixed_row) - _success(routed_row)
        )
        exposure_by_family[family].append(
            _tool_schema_exposures(fixed_row) - _tool_schema_exposures(routed_row)
        )
        models_by_family[family].add(model)
    families = sorted(success_by_family)
    expected_contributions = 4 * len(observed_models)
    if families != sorted(exposure_by_family):
        raise AssertionError("routing cluster identities diverged")
    if any(models != observed_models for models in models_by_family.values()) or any(
        len(success_by_family[family]) != expected_contributions
        or len(exposure_by_family[family]) != expected_contributions
        for family in families
    ):
        raise ValueError("routing families do not contain the complete paired panel")
    return (
        [mean(success_by_family[family]) for family in families],
        [mean(exposure_by_family[family]) for family in families],
        len(fixed),
    )


def _discovery_attenuation_values(
    rows: Sequence[Mapping[str, object]],
    measure: Callable[[Mapping[str, object]], float],
) -> list[float]:
    """Match the registered Discovery estimator and cluster by required tool."""

    cells: dict[
        tuple[str, str, str], dict[tuple[str, int], float]
    ] = defaultdict(dict)
    case_tools: dict[str, str] = {}
    for row in rows:
        condition = _condition(row)
        key = (
            str(condition.get("case_id")),
            _model(row),
            str(condition.get("harness")),
        )
        coordinate = (
            str(condition.get("retriever")),
            int(condition.get("registry_size", 0)),
        )
        if coordinate in cells[key]:
            raise ValueError("duplicate Discovery interaction cell")
        cells[key][coordinate] = measure(row)
        exposure = row.get("exposure")
        tool = exposure.get("required_tool") if isinstance(exposure, dict) else None
        if not isinstance(tool, str) or not tool:
            raise ValueError("Discovery row is missing required-tool metadata")
        case_tools[key[0]] = tool
    required = {
        ("flat", 32),
        ("flat", 2048),
        ("hierarchical", 32),
        ("hierarchical", 2048),
    }
    by_case: dict[str, list[float]] = defaultdict(list)
    incomplete = 0
    for (case_id, _, _), values in cells.items():
        if required <= set(values):
            by_case[case_id].append(
                values[("hierarchical", 2048)]
                - values[("hierarchical", 32)]
                - values[("flat", 2048)]
                + values[("flat", 32)]
            )
        else:
            incomplete += 1
    if incomplete:
        raise ValueError(f"Discovery attenuation has {incomplete} incomplete cells")
    by_tool: dict[str, list[float]] = defaultdict(list)
    for case_id, values in by_case.items():
        by_tool[case_tools[case_id]].append(mean(values))
    return [mean(values) for _, values in sorted(by_tool.items())]


def _discovery_success(row: Mapping[str, object]) -> float:
    return float(row.get("success") is True)


def _claim_points(
    capacity_rows: Sequence[Mapping[str, object]],
    discovery_rows: Sequence[Mapping[str, object]],
) -> JSON:
    routing_success, routing_exposure, pairs = _routing_values(capacity_rows)
    return {
        "visible_load_loss": mean(_fixed_menu_visible_values(capacity_rows)),
        "workflow_load_loss": mean(
            _capacity_values(capacity_rows, "workflow_high_minus_low")
        ),
        "visible_workflow_floor_interaction": mean(
            _capacity_values(capacity_rows, "visible_by_workflow")
        ),
        "routed_exposure_efficiency": {
            "fixed_minus_routed_verified_success": mean(routing_success),
            "fixed_minus_routed_tool_schema_exposures": mean(routing_exposure),
            "pairs": pairs,
        },
        "hierarchical_discovery_attenuation": mean(
            _discovery_attenuation_values(discovery_rows, _discovery_success)
        ),
    }


def _boundary_values(
    rows: Sequence[Mapping[str, object]],
) -> list[float]:
    """Return family-clustered 30-call-minus-2-call VTS contrasts."""

    if not rows:
        raise ValueError("workflow-boundary claim requires rows")
    _validate_unique(rows, label="workflow boundary")
    models = set(_models(rows))
    cells: dict[tuple[str, str, int], list[float]] = defaultdict(list)
    family_models: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        condition = _condition(row)
        if condition.get("study") != WORKFLOW_BOUNDARY_STUDY:
            raise ValueError("workflow-boundary row has the wrong study identity")
        family = str(condition.get("family_id"))
        model = _model(row)
        locator = condition.get("locator_nodes")
        if not isinstance(locator, int) or locator not in WORKFLOW_BOUNDARY_LOCATOR_LEVELS:
            raise ValueError("workflow-boundary row has an unregistered locator level")
        cells[(family, model, locator)].append(_success(row))
        family_models[family].add(model)
    if len(models) < 1 or any(observed != models for observed in family_models.values()):
        raise ValueError("workflow-boundary families do not contain the same model panel")
    values: list[float] = []
    for family in sorted(family_models):
        model_differences: list[float] = []
        for model in sorted(models):
            observed = {
                locator: cells.get((family, model, locator), [])
                for locator in WORKFLOW_BOUNDARY_LOCATOR_LEVELS
            }
            if any(len(items) != 1 for items in observed.values()):
                raise ValueError(
                    f"workflow-boundary family {family} model {model} is incomplete"
                )
            model_differences.append(
                observed[WORKFLOW_BOUNDARY_LOCATOR_LEVELS[-1]][0]
                - observed[WORKFLOW_BOUNDARY_LOCATOR_LEVELS[0]][0]
            )
        values.append(mean(model_differences))
    return values


def _replication_claim_points(
    capacity_rows: Sequence[Mapping[str, object]],
    discovery_rows: Sequence[Mapping[str, object]],
    boundary_rows: Sequence[Mapping[str, object]],
) -> JSON:
    core = _claim_points(capacity_rows, discovery_rows)
    return {
        "visible_load_loss": core["visible_load_loss"],
        "routed_exposure_efficiency": core["routed_exposure_efficiency"],
        "hierarchical_discovery_attenuation": core[
            "hierarchical_discovery_attenuation"
        ],
        "workflow_boundary_loss": mean(_boundary_values(boundary_rows)),
    }


def _direction_supported(name: str, point: object) -> bool:
    if name in {"visible_load_loss", "workflow_boundary_loss"}:
        return isinstance(point, (int, float)) and float(point) < 0
    if name in {
        "visible_workflow_floor_interaction",
        "hierarchical_discovery_attenuation",
    }:
        return isinstance(point, (int, float)) and float(point) > 0
    if name == "routed_exposure_efficiency" and isinstance(point, dict):
        success = point.get("fixed_minus_routed_verified_success")
        exposure = point.get("fixed_minus_routed_tool_schema_exposures")
        return (
            isinstance(success, (int, float))
            and float(success) < ROUTING_NONINFERIORITY_MARGIN
            and isinstance(exposure, (int, float))
            and float(exposure) > 0
        )
    raise ValueError(f"unknown replication claim: {name}")


def _named_claims(
    capacity_rows: Sequence[Mapping[str, object]],
    discovery_rows: Sequence[Mapping[str, object]],
    boundary_rows: Sequence[Mapping[str, object]],
    *,
    bootstrap_samples: int,
    seed: int,
) -> JSON:
    claims: JSON = {}
    capacity_specs = {
        "visible_load_loss": (
            "permanent_fixed_menu_visible_128_minus_8_pooled_robustness_strata",
            "less",
            _fixed_menu_visible_values(capacity_rows),
        )
    }
    for name, (contrast, direction, values) in capacity_specs.items():
        test = _bootstrap_directional(
            values,
            direction=direction,
            null_value=0.0,
            samples=bootstrap_samples,
            seed=seed + int(stable_hash(name)[:8], 16),
        )
        claims[name] = {
            "estimand": contrast,
            "cluster_unit": "semantic_family",
            "test": test,
            "raw_p": test["bootstrap_p_one_sided"],
            "direction_supported": _direction_supported(name, test["estimate"]),
        }

    routing_success, routing_exposure, pairs = _routing_values(capacity_rows)
    success_test = _bootstrap_directional(
        routing_success,
        direction="less",
        null_value=ROUTING_NONINFERIORITY_MARGIN,
        samples=bootstrap_samples,
        seed=seed + int(stable_hash("routing-success")[:8], 16),
    )
    exposure_test = _bootstrap_directional(
        routing_exposure,
        direction="greater",
        null_value=0.0,
        samples=bootstrap_samples,
        seed=seed + int(stable_hash("routing-exposure")[:8], 16),
    )
    routing_point: JSON = {
        "fixed_minus_routed_verified_success": success_test["estimate"],
        "fixed_minus_routed_tool_schema_exposures": exposure_test["estimate"],
    }
    claims["routed_exposure_efficiency"] = {
        "estimand": "intersection-union routing noninferiority and exposure reduction",
        "cluster_unit": "semantic_family",
        "noninferiority_margin": ROUTING_NONINFERIORITY_MARGIN,
        "matched_pairs": pairs,
        "success_component": success_test,
        "exposure_component": exposure_test,
        "raw_p": max(
            float(success_test["bootstrap_p_one_sided"]),
            float(exposure_test["bootstrap_p_one_sided"]),
        ),
        "direction_supported": _direction_supported(
            "routed_exposure_efficiency", routing_point
        ),
    }

    discovery_test = _bootstrap_directional(
        _discovery_attenuation_values(discovery_rows, _discovery_success),
        direction="greater",
        null_value=0.0,
        samples=bootstrap_samples,
        seed=seed + int(stable_hash("discovery-attenuation")[:8], 16),
    )
    claims["hierarchical_discovery_attenuation"] = {
        "estimand": "itt_hierarchical_attenuation",
        "cluster_unit": "required_tool",
        "test": discovery_test,
        "raw_p": discovery_test["bootstrap_p_one_sided"],
        "direction_supported": _direction_supported(
            "hierarchical_discovery_attenuation", discovery_test["estimate"]
        ),
    }
    boundary_test = _bootstrap_directional(
        _boundary_values(boundary_rows),
        direction="less",
        null_value=0.0,
        samples=bootstrap_samples,
        seed=seed + int(stable_hash("workflow-boundary-loss")[:8], 16),
    )
    claims["workflow_boundary_loss"] = {
        "estimand": "calls30_minus_calls2_verified_success",
        "cluster_unit": "semantic_family",
        "test": boundary_test,
        "raw_p": boundary_test["bootstrap_p_one_sided"],
        "direction_supported": _direction_supported(
            "workflow_boundary_loss", boundary_test["estimate"]
        ),
    }
    if tuple(claims) != NAMED_REPLICATION_CLAIMS:
        raise AssertionError("named replication claim order changed")
    adjusted = holm_adjust(
        {name: float(payload["raw_p"]) for name, payload in claims.items()}
    )
    for name, holm_p in adjusted.items():
        claims[name]["holm_p"] = holm_p
        claims[name]["supported_at_0.05"] = (
            claims[name]["direction_supported"] is True and holm_p < 0.05
        )
    return claims


def _model_sign_consistency(
    capacity_rows: Sequence[Mapping[str, object]],
    discovery_rows: Sequence[Mapping[str, object]],
    boundary_rows: Sequence[Mapping[str, object]],
) -> JSON:
    details: dict[str, JSON] = {}
    capacity_models = set(_models(capacity_rows))
    for model in sorted(capacity_models):
        points = _replication_claim_points(
            _filter_models(capacity_rows, {model}),
            _filter_models(discovery_rows, {model}),
            _filter_models(boundary_rows, {model}),
        )
        details[model] = {
            name: {
                "point": point,
                "direction_supported": _direction_supported(name, point),
            }
            for name, point in points.items()
        }
    summary: JSON = {}
    for name in NAMED_REPLICATION_CLAIMS:
        supporting = [
            model
            for model, model_details in details.items()
            if model_details[name]["direction_supported"] is True
        ]
        summary[name] = {
            "supporting_models": supporting,
            "supporting_model_count": len(supporting),
            "model_count": len(details),
            "unanimous": len(supporting) == len(details),
        }
    return {"summary": summary, "by_model": details}


def _leave_one_model_out(
    capacity_rows: Sequence[Mapping[str, object]],
    discovery_rows: Sequence[Mapping[str, object]],
    boundary_rows: Sequence[Mapping[str, object]],
) -> JSON:
    models = set(_models(capacity_rows))
    estimates: dict[str, JSON] = {}
    for excluded in sorted(models):
        retained = models - {excluded}
        estimates[excluded] = _replication_claim_points(
            _filter_models(capacity_rows, retained),
            _filter_models(discovery_rows, retained),
            _filter_models(boundary_rows, retained),
        )
    ranges: JSON = {}
    for name in NAMED_REPLICATION_CLAIMS:
        values = [payload[name] for payload in estimates.values()]
        if name == "routed_exposure_efficiency":
            ranges[name] = {
                component: [
                    min(float(value[component]) for value in values if isinstance(value, dict)),
                    max(float(value[component]) for value in values if isinstance(value, dict)),
                ]
                for component in (
                    "fixed_minus_routed_verified_success",
                    "fixed_minus_routed_tool_schema_exposures",
                )
            }
        else:
            numeric = [float(value) for value in values]
            ranges[name] = [min(numeric), max(numeric)]
    return {"excluded_model_estimates": estimates, "ranges": ranges}


def _block_analysis(
    capacity_rows: Sequence[Mapping[str, object]],
    discovery_rows: Sequence[Mapping[str, object]],
    *,
    bootstrap_samples: int,
    seed: int,
) -> JSON:
    return {
        "models": list(_models(capacity_rows)),
        "capacity": analyze_capacity_rows(
            capacity_rows,
            bootstrap_samples=bootstrap_samples,
            seed=seed,
        ),
        "discovery": analyze_discovery_rows(
            discovery_rows,
            bootstrap_samples=bootstrap_samples,
            seed=seed,
        ),
        "claim_points": _claim_points(capacity_rows, discovery_rows),
    }


def _per_model_analyses(
    capacity_rows: Sequence[Mapping[str, object]],
    discovery_rows: Sequence[Mapping[str, object]],
    *,
    bootstrap_samples: int,
    seed: int,
) -> JSON:
    result: JSON = {}
    for model in _models(capacity_rows):
        result[model] = _block_analysis(
            _filter_models(capacity_rows, {model}),
            _filter_models(discovery_rows, {model}),
            bootstrap_samples=bootstrap_samples,
            seed=seed + int(stable_hash(model)[:8], 16),
        )
    return result


def _numeric_heterogeneity(values: Mapping[str, float]) -> JSON:
    ordered = [float(values[model]) for model in sorted(values)]
    return {
        "model_estimates": dict(sorted(values.items())),
        "finite_panel_mean": mean(ordered),
        "minimum": min(ordered),
        "maximum": max(ordered),
        "range": max(ordered) - min(ordered),
        "sample_sd": stdev(ordered) if len(ordered) > 1 else 0.0,
        "negative_models": sum(value < 0 for value in ordered),
        "positive_models": sum(value > 0 for value in ordered),
        "zero_models": sum(value == 0 for value in ordered),
    }


def _heterogeneity(per_model: Mapping[str, object]) -> JSON:
    capacity: JSON = {}
    for contrast in PRIMARY_CONTRASTS:
        values = {
            model: float(payload["capacity"]["primary_effects"][contrast]["estimate"])
            for model, payload in per_model.items()
            if isinstance(payload, dict)
        }
        capacity[contrast] = _numeric_heterogeneity(values)
    discovery_values = {
        model: float(
            payload["discovery"]["primary_itt_effects"][
                "itt_hierarchical_attenuation"
            ]["estimate"]
        )
        for model, payload in per_model.items()
        if isinstance(payload, dict)
    }
    routing_success = {
        model: float(
            payload["claim_points"]["routed_exposure_efficiency"][
                "fixed_minus_routed_verified_success"
            ]
        )
        for model, payload in per_model.items()
        if isinstance(payload, dict)
    }
    routing_exposure = {
        model: float(
            payload["claim_points"]["routed_exposure_efficiency"][
                "fixed_minus_routed_tool_schema_exposures"
            ]
        )
        for model, payload in per_model.items()
        if isinstance(payload, dict)
    }
    return {
        "capacity_contrasts": capacity,
        "discovery_hierarchical_attenuation": _numeric_heterogeneity(
            discovery_values
        ),
        "routing_fixed_minus_routed_success": _numeric_heterogeneity(
            routing_success
        ),
        "routing_fixed_minus_routed_schema_exposure": _numeric_heterogeneity(
            routing_exposure
        ),
        "estimand_note": (
            "These summaries describe the seven fixed model configurations; the sample SD "
            "is heterogeneity within this finite panel, not a model-population standard deviation."
        ),
    }


def analyze_main_model_generalization(
    original_capacity_rows: Sequence[Mapping[str, object]],
    original_discovery_rows: Sequence[Mapping[str, object]],
    replication_capacity_rows: Sequence[Mapping[str, object]],
    replication_discovery_rows: Sequence[Mapping[str, object]],
    replication_boundary_rows: Sequence[Mapping[str, object]],
    *,
    bootstrap_samples: int = 10000,
    seed: int = 20260811,
    expected_capacity_rows_per_model: int | None = 576,
    expected_discovery_rows_per_model: int | None = 216,
    expected_boundary_rows_per_model: int | None = 48,
) -> JSON:
    """Analyze the original, replication, and seven-model main-study blocks."""

    original_models = _validate_block(
        original_capacity_rows,
        original_discovery_rows,
        label="original block",
        expected_models=3,
        expected_capacity_rows_per_model=expected_capacity_rows_per_model,
        expected_discovery_rows_per_model=expected_discovery_rows_per_model,
    )
    replication_models = _validate_block(
        replication_capacity_rows,
        replication_discovery_rows,
        label="replication block",
        expected_models=4,
        expected_capacity_rows_per_model=expected_capacity_rows_per_model,
        expected_discovery_rows_per_model=expected_discovery_rows_per_model,
    )
    if set(original_models) & set(replication_models):
        raise ValueError("original and replication model panels must be disjoint")
    _validate_unique(replication_boundary_rows, label="replication workflow boundary")
    boundary_models = _models(replication_boundary_rows)
    if set(boundary_models) != set(replication_models):
        raise ValueError("workflow-boundary model panel differs from the replication")
    if expected_boundary_rows_per_model is not None:
        boundary_counts = Counter(_model(row) for row in replication_boundary_rows)
        if set(boundary_counts.values()) != {expected_boundary_rows_per_model}:
            raise ValueError(
                "workflow-boundary denominator differs by model: "
                f"{dict(boundary_counts)}"
            )
    _boundary_values(replication_boundary_rows)
    original_ids = {str(row["condition_id"]) for row in [*original_capacity_rows, *original_discovery_rows]}
    replication_ids = {
        str(row["condition_id"])
        for row in [*replication_capacity_rows, *replication_discovery_rows]
    }
    boundary_ids = {str(row["condition_id"]) for row in replication_boundary_rows}
    if len(original_ids) != len(original_capacity_rows) + len(original_discovery_rows):
        raise ValueError("original Capacity and Discovery blocks share condition IDs")
    if len(replication_ids) != len(replication_capacity_rows) + len(
        replication_discovery_rows
    ):
        raise ValueError("replication Capacity and Discovery blocks share condition IDs")
    if original_ids & replication_ids:
        raise ValueError("original and replication blocks share condition IDs")
    if boundary_ids & (original_ids | replication_ids):
        raise ValueError("workflow-boundary and core blocks share condition IDs")

    combined_capacity = [*original_capacity_rows, *replication_capacity_rows]
    combined_discovery = [*original_discovery_rows, *replication_discovery_rows]
    original = _block_analysis(
        original_capacity_rows,
        original_discovery_rows,
        bootstrap_samples=bootstrap_samples,
        seed=seed,
    )
    replication = _block_analysis(
        replication_capacity_rows,
        replication_discovery_rows,
        bootstrap_samples=bootstrap_samples,
        seed=seed + 1,
    )
    combined = _block_analysis(
        combined_capacity,
        combined_discovery,
        bootstrap_samples=bootstrap_samples,
        seed=seed + 2,
    )
    per_model = _per_model_analyses(
        combined_capacity,
        combined_discovery,
        bootstrap_samples=bootstrap_samples,
        seed=seed + 3,
    )
    return {
        "analysis": "capacity-surface-model-generalization-main-v3",
        "corrective_estimand_repair": {
            "status": "corrected_against_frozen_stage_resolved_amendment",
            "affected_claim": "visible_load_loss",
            "registered_estimand": (
                "permanent fixed-menu V128 minus V8, paired within model and "
                "semantic family and averaged over both registered robustness strata"
            ),
            "previous_implementation_error": (
                "the named claim reused a helper that filters to routed primary rows"
            ),
            "raw_rows_changed": False,
            "hypothesis_family_changed": False,
        },
        "corrective_cluster_repair": {
            "status": "corrected_against_frozen_family_cluster_unit",
            "affected_claim": "routed_exposure_efficiency",
            "registered_cluster_unit": "semantic_family",
            "previous_implementation_error": (
                "the named routing claim aggregated paired rows by intent rather than "
                "semantic family"
            ),
            "raw_rows_changed": False,
            "hypothesis_family_changed": False,
        },
        "analysis_role": (
            "prospective four-model replication with a stratified seven-model finite-panel synthesis"
        ),
        "original_models": list(original_models),
        "replication_models": list(replication_models),
        "models": [*original_models, *replication_models],
        "rows": {
            "original_capacity": len(original_capacity_rows),
            "original_discovery": len(original_discovery_rows),
            "replication_capacity": len(replication_capacity_rows),
            "replication_discovery": len(replication_discovery_rows),
            "replication_workflow_boundary": len(replication_boundary_rows),
            "combined_core": len(combined_capacity) + len(combined_discovery),
            "combined": (
                len(combined_capacity)
                + len(combined_discovery)
                + len(replication_boundary_rows)
            ),
        },
        "study_blocks": {
            "original": original,
            "replication": replication,
            "seven_model_synthesis": combined,
        },
        "named_replication_claims": _named_claims(
            replication_capacity_rows,
            replication_discovery_rows,
            replication_boundary_rows,
            bootstrap_samples=bootstrap_samples,
            seed=seed + 4,
        ),
        "replication_model_sign_consistency": _model_sign_consistency(
            replication_capacity_rows,
            replication_discovery_rows,
            replication_boundary_rows,
        ),
        "replication_leave_one_model_out": _leave_one_model_out(
            replication_capacity_rows,
            replication_discovery_rows,
            replication_boundary_rows,
        ),
        "workflow_boundary": {
            "models": list(boundary_models),
            "rows": len(replication_boundary_rows),
            "locator_levels": list(WORKFLOW_BOUNDARY_LOCATOR_LEVELS),
            "family_count": len(
                {str(_condition(row).get("family_id")) for row in replication_boundary_rows}
            ),
            "status_counts": dict(
                sorted(Counter(str(row.get("status")) for row in replication_boundary_rows).items())
            ),
            "calls30_minus_calls2": mean(
                _boundary_values(replication_boundary_rows)
            ),
        },
        "per_model": per_model,
        "seven_model_heterogeneity": _heterogeneity(per_model),
        "inference_note": (
            "The original and replication blocks remain separate. Family-clustered bootstrap "
            "inference targets the finite benchmark, and the four directional replication tests "
            "use Holm correction. The routing claim is an intersection-union test: routed success "
            "must be noninferior within five percentage points and fixed menus must expose more "
            "tool schemas. The workflow-boundary claim is estimated only in the prospectively "
            "separated four-model block. Seven-model summaries do not support population-level "
            "model inference."
        ),
    }
