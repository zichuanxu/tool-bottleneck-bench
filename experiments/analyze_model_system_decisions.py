#!/usr/bin/env python3
"""Compare layer-matched system moves with model-configuration moves."""

from __future__ import annotations

import argparse
from collections import defaultdict
from functools import lru_cache
from hashlib import sha256
import json
import math
from pathlib import Path
from statistics import mean
from typing import AbstractSet, Iterable, Mapping, Protocol, Sequence

from bottleneckbench.analysis import is_infrastructure_failure
from bottleneckbench.core.schema import canonical_json, stable_hash


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
PANEL = ROOT / "results/analysis/release-panel.json"
PROTOCOL = HERE / "model-versus-system-decision-protocol.md"
OPENROUTER_PRICES = HERE / "openrouter-endpoint-snapshot.json"
GOOGLE_PRICES = HERE / "google-cloud-price-snapshot.json"
OUTPUT = ROOT / "results/analysis/model-versus-system.json"
RETAINED_ITT_INFRASTRUCTURE_IDS = frozenset(
    {"fbeab4aed2d7e60bc703cb7b51e1190d13abdbb08b044f2a050f17940adb9db9"}
)
METRICS = (
    "success",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "model_requests",
    "tool_calls",
    "schema_bytes",
    "wall_time_seconds",
    "cost_usd",
)
INFERENTIAL_METRICS = frozenset({"success", "cost_usd"})
CellMetrics = dict[str, float | None]
ClusterCells = dict[str, dict[str, CellMetrics]]


class RowCosts(Protocol):
    """Source-bound normalized costs; implementations validate model/ID/row SHA."""

    models: AbstractSet[str]

    def cost_for_row(self, row: Mapping[str, object]) -> float: ...


def _row_cost_models(
    row_costs: RowCosts | None, expected_models: AbstractSet[str] | None = None,
) -> frozenset[str]:
    if row_costs is None:
        return frozenset()
    models = row_costs.models
    if not isinstance(models, (set, frozenset)) or any(not isinstance(m, str) or not m for m in models):
        raise ValueError("row cost lookup must declare a set of model IDs")
    if expected_models is not None and not models <= expected_models:
        raise ValueError("row cost lookup includes models outside the analysis panel")
    return frozenset(models)


def _bound_row_cost(
    row: Mapping[str, object], model: str, row_costs: RowCosts | None,
) -> float | None:
    """No fallback is permitted when an explicitly covered row fails lookup."""
    if model not in _row_cost_models(row_costs):
        return None
    value = row_costs.cost_for_row(row)  # type: ignore[union-attr]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("bound row cost must be finite and nonnegative")
    return float(value)


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _rows(
    paths: Sequence[Path],
    *,
    expected_rows: int,
    allowed_infrastructure: frozenset[str] = frozenset(),
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    infrastructure: set[str] = set()
    for path in paths:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"non-object row at {path}:{number}")
            condition_id = row.get("condition_id")
            if not isinstance(condition_id, str) or condition_id in seen:
                raise ValueError(f"missing or duplicate condition ID at {path}:{number}")
            seen.add(condition_id)
            if is_infrastructure_failure(row):
                infrastructure.add(condition_id)
                if condition_id not in allowed_infrastructure:
                    raise ValueError(f"unregistered infrastructure row: {condition_id}")
            rows.append(row)
    if len(rows) != expected_rows or infrastructure != set(allowed_infrastructure):
        raise ValueError(
            f"input closure mismatch: rows={len(rows)}/{expected_rows}, "
            f"infrastructure={sorted(infrastructure)}"
        )
    return rows


def _condition(row: Mapping[str, object]) -> Mapping[str, object]:
    value = row.get("condition")
    if not isinstance(value, dict):
        raise ValueError("row lacks a condition object")
    return value


def _number(container: object, key: str) -> float | None:
    if not isinstance(container, dict):
        return None
    value = container.get(key)
    return (
        float(value)
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else None
    )


def _prices(
    panel: Mapping[str, object],
    openrouter: Mapping[str, object],
    google: Mapping[str, object],
) -> dict[str, tuple[float, float]]:
    models = {str(item["model"]) for item in panel["configurations"]}  # type: ignore[index]
    prices: dict[str, tuple[float, float]] = {}
    for item in openrouter["selected"]:  # type: ignore[index]
        model = str(item["requested_model"])
        if model in models:
            prices[model] = (float(item["prompt_price"]), float(item["completion_price"]))
    google_models = google.get("models")
    if not isinstance(google_models, dict):
        raise ValueError("Google price snapshot lacks models")
    for model, item in google_models.items():
        if model in models and isinstance(item, dict):
            prices[str(model)] = (
                float(item["input"]) / 1_000_000,
                float(item["output"]) / 1_000_000,
            )
    if set(prices) != models:
        raise ValueError(f"price snapshot does not cover final panel: {sorted(models-set(prices))}")
    return prices


def _row_metrics(
    row: Mapping[str, object], prices: Mapping[str, tuple[float, float]],
    *, row_costs: RowCosts | None = None,
) -> CellMetrics:
    condition = _condition(row)
    model = str(condition["model"])
    verdict = row.get("verdict")
    success = float(
        row.get("success") is True
        if "success" in row
        else isinstance(verdict, dict) and verdict.get("verified_success") is True
    )
    usage = row.get("usage")
    prompt = _number(usage, "prompt_tokens")
    completion = _number(usage, "completion_tokens")
    total = _number(usage, "total_tokens")
    efficiency = row.get("efficiency")
    missing_usage = _number(efficiency, "usage_missing_responses") or 0.0
    if missing_usage > 0:
        prompt = completion = total = None
    response_id = row.get("response_id")
    requests = _number(efficiency, "model_requests_attempted")
    if requests is None and "success" in row:
        requests = float(isinstance(response_id, str) and bool(response_id))
    attempts = row.get("call_attempts")
    calls = float(len(attempts)) if isinstance(attempts, list) else 0.0 if "success" in row else None
    schema_bytes = _number(efficiency, "tool_schema_bytes")
    wall = _number(efficiency, "wall_time_seconds")
    bound_cost = _bound_row_cost(row, model, row_costs)
    if bound_cost is not None and (prompt is None or completion is None):
        raise ValueError("bound row cost requires complete row usage")
    cost = bound_cost if bound_cost is not None else (
        prompt * prices[model][0] + completion * prices[model][1]
        if prompt is not None and completion is not None
        else None
    )
    return {
        "success": success,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "model_requests": requests,
        "tool_calls": calls,
        "schema_bytes": schema_bytes,
        "wall_time_seconds": wall,
        "cost_usd": cost,
    }


def _mean_metrics(items: Sequence[CellMetrics]) -> CellMetrics:
    if not items:
        raise ValueError("cannot average an empty metric set")
    result: CellMetrics = {}
    for metric in METRICS:
        values = [item[metric] for item in items]
        result[metric] = (
            mean(float(value) for value in values if value is not None)
            if all(value is not None for value in values)
            else None
        )
    return result


def _difference(high: CellMetrics, low: CellMetrics) -> CellMetrics:
    return {
        metric: (
            float(high[metric]) - float(low[metric])
            if high[metric] is not None and low[metric] is not None
            else None
        )
        for metric in METRICS
    }


def _aggregate_units(
    units: Mapping[str, tuple[str, Mapping[str, CellMetrics]]]
) -> ClusterCells:
    grouped: dict[str, dict[str, list[CellMetrics]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for _, (cluster, cells) in units.items():
        for name, metrics in cells.items():
            grouped[cluster][name].append(metrics)
    return {
        cluster: {
            name: _mean_metrics(items) for name, items in sorted(cells.items())
        }
        for cluster, cells in sorted(grouped.items())
    }


def _layer_cells(
    capacity: Sequence[Mapping[str, object]],
    discovery: Sequence[Mapping[str, object]],
    repair: Sequence[Mapping[str, object]],
    prices: Mapping[str, tuple[float, float]],
    *, row_costs: RowCosts | None = None,
) -> dict[str, dict[str, ClusterCells]]:
    capacity_index: dict[tuple[object, ...], CellMetrics] = {}
    for row in [*capacity, *repair]:
        condition = _condition(row)
        key = (
            str(condition["family_id"]),
            str(condition["model"]),
            str(row.get("study_role", "primary")),
            int(condition["visible_tools"]),
            str(condition["workflow"]),
            str(condition["representation"]),
        )
        if key in capacity_index:
            raise ValueError(f"duplicate capacity decision cell: {key}")
        capacity_index[key] = _row_metrics(row, prices, row_costs=row_costs)

    discovery_index: dict[tuple[object, ...], tuple[str, CellMetrics]] = {}
    for row in discovery:
        condition = _condition(row)
        exposure = row.get("exposure")
        tool = exposure.get("required_tool") if isinstance(exposure, dict) else None
        if not isinstance(tool, str) or not tool:
            raise ValueError("discovery row lacks required tool")
        key = (
            str(condition["case_id"]),
            str(condition["model"]),
            str(condition["retriever"]),
            int(condition["registry_size"]),
        )
        if key in discovery_index:
            raise ValueError(f"duplicate discovery decision cell: {key}")
        discovery_index[key] = (tool, _row_metrics(row, prices, row_costs=row_costs))

    models = sorted({str(_condition(row)["model"]) for row in capacity})
    families = sorted({str(_condition(row)["family_id"]) for row in capacity})
    cases = sorted({str(_condition(row)["case_id"]) for row in discovery})
    repair_models = {str(_condition(row)["model"]) for row in repair}
    layers: dict[str, dict[str, ClusterCells]] = defaultdict(dict)

    for model in models:
        retrieval_units: dict[str, tuple[str, Mapping[str, CellMetrics]]] = {}
        for case in cases:
            coordinates = {}
            tool: str | None = None
            for name, retriever, size in (
                ("base_low", "flat", 32),
                ("base_stress", "flat", 2048),
                ("repair_low", "hierarchical", 32),
                ("repair_stress", "hierarchical", 2048),
            ):
                observed_tool, metrics = discovery_index[(case, model, retriever, size)]
                tool = tool or observed_tool
                if tool != observed_tool:
                    raise ValueError(f"required tool changed within case: {case}")
                coordinates[name] = metrics
            retrieval_units[case] = (str(tool), coordinates)
        layers["retrieval"][model] = _aggregate_units(retrieval_units)

        visible_units: dict[str, tuple[str, Mapping[str, CellMetrics]]] = {}
        workflow_units: dict[str, tuple[str, Mapping[str, CellMetrics]]] = {}
        interface_units: dict[str, tuple[str, Mapping[str, CellMetrics]]] = {}
        for family in families:
            for stratum, workflow, representation in (
                ("low_raw", "low", "raw"),
                ("high_linked", "high", "linked"),
            ):
                visible_units[f"{family}:{stratum}"] = (
                    family,
                    {
                        "base_low": capacity_index[(family, model, "robustness", 8, workflow, representation)],
                        "base_stress": capacity_index[(family, model, "robustness", 128, workflow, representation)],
                        "repair_low": capacity_index[(family, model, "primary", 8, workflow, representation)],
                        "repair_stress": capacity_index[(family, model, "primary", 128, workflow, representation)],
                    },
                )
            if model in repair_models:
                workflow_units[family] = (
                    family,
                    {
                        "base_low": capacity_index[(family, model, "primary", 8, "low", "raw")],
                        "base_stress": capacity_index[(family, model, "primary", 8, "high", "raw")],
                        "repair_low": capacity_index[(family, model, "verification_repair", 8, "low", "raw")],
                        "repair_stress": capacity_index[(family, model, "verification_repair", 8, "high", "raw")],
                    },
                )
                interface_units[family] = (
                    family,
                    {
                        "base_low": capacity_index[(family, model, "primary", 8, "low", "raw")],
                        "base_stress": capacity_index[(family, model, "primary", 8, "low", "linked")],
                        "repair_low": capacity_index[(family, model, "interface_guide", 8, "low", "raw")],
                        "repair_stress": capacity_index[(family, model, "interface_guide", 8, "low", "linked")],
                    },
                )
        layers["visible_menu"][model] = _aggregate_units(visible_units)
        if model in repair_models:
            layers["workflow"][model] = _aggregate_units(workflow_units)
            layers["returned_state_interface"][model] = _aggregate_units(interface_units)
    return dict(layers)


@lru_cache(maxsize=None)
def _bootstrap_weights(size: int, samples: int, seed: int):
    import numpy as np

    if size <= 0 or samples <= 0:
        raise ValueError("bootstrap size and samples must be positive")
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, size, size=(samples, size))
    weights = np.zeros((samples, size), dtype=np.float64)
    rows = np.repeat(np.arange(samples), size)
    np.add.at(weights, (rows, indices.ravel()), 1.0 / size)
    return weights


def _bootstrap(values: Sequence[float], *, samples: int, seed: int) -> dict[str, object]:
    import numpy as np

    data = np.asarray(values, dtype=np.float64)
    draws = np.sort(np.einsum("ij,j->i", _bootstrap_weights(len(data), samples, seed), data))
    return {
        "estimate": mean(values),
        "ci95": [
            float(draws[int(0.025 * (samples - 1))]),
            float(draws[int(0.975 * (samples - 1))]),
        ],
    }


def _summary(
    contributions: Mapping[str, CellMetrics], *, samples: int, seed: int
) -> dict[str, object]:
    metrics: dict[str, object] = {}
    for metric in METRICS:
        values = [float(item[metric]) for item in contributions.values() if item[metric] is not None]
        uncertainty = (
            _bootstrap(
                values,
                samples=samples,
                seed=seed + int(sha256(metric.encode()).hexdigest()[:8], 16),
            )
            if values and metric in INFERENTIAL_METRICS
            else {"estimate": mean(values) if values else None, "ci95": None}
        )
        metrics[metric] = {
            **uncertainty,
            "clusters_observed": len(values),
            "clusters_total": len(contributions),
            "uncertainty_role": (
                "family_bootstrap" if metric in INFERENTIAL_METRICS else "descriptive"
            ),
        }
    return {"clusters": len(contributions), "metrics": metrics}


def _move(cells: ClusterCells) -> dict[str, CellMetrics]:
    return {
        cluster: _difference(cells[cluster]["repair_stress"], cells[cluster]["base_stress"])
        for cluster in cells
    }


def _configuration_move(
    incumbent: ClusterCells, candidate: ClusterCells, *, combined: bool
) -> dict[str, CellMetrics]:
    if set(incumbent) != set(candidate):
        raise ValueError("configuration comparison changes semantic clusters")
    arm = "repair_stress" if combined else "base_stress"
    return {
        cluster: _difference(candidate[cluster][arm], incumbent[cluster]["base_stress"])
        for cluster in incumbent
    }


def _paired_difference(
    left: Mapping[str, CellMetrics], right: Mapping[str, CellMetrics], metric: str
) -> list[float]:
    values = []
    for cluster in left:
        a, b = left[cluster][metric], right[cluster][metric]
        if a is not None and b is not None:
            values.append(float(a) - float(b))
    return values


def _dominates(candidate: Mapping[str, object], system: Mapping[str, object]) -> bool:
    c = candidate["metrics"]  # type: ignore[index]
    s = system["metrics"]  # type: ignore[index]
    names = ("success", "total_tokens", "wall_time_seconds", "cost_usd")
    estimates = {
        name: (c[name]["estimate"], s[name]["estimate"])  # type: ignore[index]
        for name in names
    }
    if any(a is None or b is None for a, b in estimates.values()):
        return False
    no_worse = (
        estimates["success"][0] >= estimates["success"][1]
        and all(estimates[name][0] <= estimates[name][1] for name in names[1:])
    )
    strict = (
        estimates["success"][0] > estimates["success"][1]
        or any(estimates[name][0] < estimates[name][1] for name in names[1:])
    )
    return bool(no_worse and strict)


def _quartiles(cells: ClusterCells) -> dict[str, list[str]]:
    ordered = sorted(
        cells,
        key=lambda cluster: (
            float(_difference(cells[cluster]["base_low"], cells[cluster]["base_stress"])["success"]),
            cluster,
        ),
    )
    groups = {f"Q{index}": [] for index in range(1, 5)}
    for index, cluster in enumerate(ordered):
        groups[f"Q{min(4, index * 4 // len(ordered) + 1)}"].append(cluster)
    return groups


def analyze(
    *,
    capacity: Sequence[Mapping[str, object]],
    discovery: Sequence[Mapping[str, object]],
    repair: Sequence[Mapping[str, object]],
    panel: Mapping[str, object],
    openrouter_prices: Mapping[str, object],
    google_prices: Mapping[str, object],
    candidate_prices: Mapping[str, object] | Sequence[Mapping[str, object]] | None = None,
    row_costs: RowCosts | None = None,
    samples: int,
    seed: int,
) -> dict[str, object]:
    configurations = [str(item["model"]) for item in panel["configurations"]]  # type: ignore[index]
    cost_models = _row_cost_models(row_costs, frozenset(configurations))
    organizations = {
        str(item["model"]): str(item["organization"])
        for item in panel["configurations"]  # type: ignore[index]
    }
    auxiliary = set(panel["auxiliary_models"])  # type: ignore[arg-type]
    price_sets = (
        [candidate_prices]
        if isinstance(candidate_prices, dict)
        else list(candidate_prices or [])
    )
    candidate_rows: dict[str, object] = {}
    for price_set in price_sets:
        for model, price in price_set.get("models", {}).items():  # type: ignore[union-attr]
            prior = candidate_rows.get(str(model))
            if prior is not None and (
                float(prior["input"]), float(prior["output"])  # type: ignore[index]
            ) != (float(price["input"]), float(price["output"])):  # type: ignore[index]
                raise ValueError(f"conflicting frozen prices for {model}")
            candidate_rows[str(model)] = price
    priced_panel = {
        **panel,
        "configurations": [
            item for item in panel["configurations"]  # type: ignore[index]
            if item["model"] not in candidate_rows and item["model"] not in cost_models
        ],
    }
    prices = _prices(priced_panel, openrouter_prices, google_prices)
    expected_models = {str(item["model"]) for item in panel["configurations"]}  # type: ignore[index]
    for model, price in candidate_rows.items():  # type: ignore[union-attr]
        if model in expected_models and model not in cost_models:
            prices[str(model)] = (
                float(price["input"]) / 1_000_000,
                float(price["output"]) / 1_000_000,
            )
    if set(prices) | cost_models != expected_models:
        raise ValueError("combined price snapshots do not cover the decision panel")
    layers = _layer_cells(capacity, discovery, repair, prices, row_costs=row_costs)
    expected = {
        "retrieval": set(configurations),
        "visible_menu": set(configurations),
        "workflow": auxiliary,
        "returned_state_interface": auxiliary,
    }
    if {name: set(models) for name, models in layers.items()} != expected:
        raise ValueError("layer decision panels do not match the frozen eligible sets")

    layer_reports: dict[str, object] = {}
    for layer, model_cells in sorted(layers.items()):
        incumbent_reports: dict[str, object] = {}
        flags: dict[str, dict[str, bool]] = {}
        comparisons_by_incumbent: dict[str, dict[str, object]] = {}
        for incumbent, cells in sorted(model_cells.items()):
            system_values = _move(cells)
            system_summary = _summary(system_values, samples=samples, seed=seed)
            configuration_moves = {}
            combined_moves = {}
            comparisons = {}
            for candidate, candidate_cells in sorted(model_cells.items()):
                if candidate == incumbent:
                    continue
                configuration_values = _configuration_move(cells, candidate_cells, combined=False)
                combined_values = _configuration_move(cells, candidate_cells, combined=True)
                configuration_summary = _summary(configuration_values, samples=samples, seed=seed)
                configuration_moves[candidate] = configuration_summary
                combined_moves[candidate] = _summary(combined_values, samples=samples, seed=seed)
                success_difference = _paired_difference(system_values, configuration_values, "success")
                cost_difference = _paired_difference(system_values, configuration_values, "cost_usd")
                success_report = (
                    _bootstrap(success_difference, samples=samples, seed=seed)
                    if success_difference
                    else None
                )
                cost_report = (
                    _bootstrap(cost_difference, samples=samples, seed=seed)
                    if len(cost_difference) == len(cells)
                    else None
                )
                comparisons[candidate] = {
                    "system_minus_configuration_success": success_report,
                    "system_minus_configuration_cost_usd": cost_report,
                    "success_tied": bool(
                        success_report is not None
                        and success_report["ci95"][0] <= 0 <= success_report["ci95"][1]
                    ),
                    "configuration_dominates_system": _dominates(configuration_summary, system_summary),
                }
            success_superior = all(
                item["system_minus_configuration_success"] is not None
                and item["system_minus_configuration_success"]["ci95"][0] > 0
                for item in comparisons.values()
            )
            cost_superior = all(
                item["system_minus_configuration_cost_usd"] is not None
                and item["system_minus_configuration_cost_usd"]["ci95"][1] < 0
                for item in comparisons.values()
            )
            nondominated = not any(item["configuration_dominates_system"] for item in comparisons.values())
            flags[incumbent] = {
                "success_superior_to_every_configuration_move": success_superior,
                "cost_superior_to_every_configuration_move": cost_superior,
                "pareto_nondominated_against_configuration_moves": nondominated,
            }
            comparisons_by_incumbent[incumbent] = comparisons
            quartile_profiles = {}
            for quartile, clusters in _quartiles(cells).items():
                system_subset = {cluster: system_values[cluster] for cluster in clusters}
                quartile_profiles[quartile] = {
                    "clusters": len(clusters),
                    "system_move": _summary(system_subset, samples=samples, seed=seed),
                    "configuration_moves": {
                        candidate: _summary(
                            {cluster: _configuration_move(cells, candidate_cells, combined=False)[cluster] for cluster in clusters},
                            samples=samples,
                            seed=seed,
                        )
                        for candidate, candidate_cells in sorted(model_cells.items())
                        if candidate != incumbent
                    },
                }
            incumbent_reports[incumbent] = {
                "organization": organizations[incumbent],
                "system_move": system_summary,
                "configuration_moves": configuration_moves,
                "combined_moves": combined_moves,
                "paired_comparisons": comparisons,
                "decision_flags": flags[incumbent],
                "diagnosed_loss_quartiles": quartile_profiles,
            }

        def decision_flags(incumbent: str, candidates: Sequence[str]) -> dict[str, bool]:
            comparisons = comparisons_by_incumbent[incumbent]
            selected = [comparisons[candidate] for candidate in candidates if candidate != incumbent]
            return {
                "success_superior_to_every_configuration_move": bool(selected) and all(
                    item["system_minus_configuration_success"] is not None
                    and item["system_minus_configuration_success"]["ci95"][0] > 0
                    for item in selected
                ),
                "cost_superior_to_every_configuration_move": bool(selected) and all(
                    item["system_minus_configuration_cost_usd"] is not None
                    and item["system_minus_configuration_cost_usd"]["ci95"][1] < 0
                    for item in selected
                ),
                "pareto_nondominated_against_configuration_moves": not any(
                    item["configuration_dominates_system"] for item in selected
                ),
            }

        def fractions(
            incumbents: Sequence[str], candidates: Sequence[str] | None = None
        ) -> dict[str, float]:
            candidate_panel = list(model_cells) if candidates is None else list(candidates)
            return {
                name: mean(
                    float(decision_flags(model, candidate_panel)[name])
                    for model in incumbents
                )
                for name in next(iter(flags.values()))
            }

        organizations_present = sorted({organizations[model] for model in model_cells})
        by_organization = {
            organization: fractions(
                [model for model in model_cells if organizations[model] == organization]
            )
            for organization in organizations_present
        }
        layer_reports[layer] = {
            "configurations": len(model_cells),
            "clusters_per_configuration": len(next(iter(model_cells.values()))),
            "incumbents": incumbent_reports,
            "finite_panel_fractions": fractions(list(model_cells)),
            "organization_balanced_fractions": {
                name: mean(values[name] for values in by_organization.values())
                for name in next(iter(flags.values()))
            },
            "fractions_by_incumbent_organization": by_organization,
            "leave_one_organization_out_fractions": {
                organization: fractions(retained, retained)
                for organization in organizations_present
                if (
                    retained := [
                        model
                        for model in model_cells
                        if organizations[model] != organization
                    ]
                )
            },
        }
    return {
        "analysis": "model-versus-system-decision-map-v1",
        "confirmatory_weight": 0,
        "models": configurations,
        "layers": layer_reports,
        "bootstrap": {"samples": samples, "seed": seed, "cluster": "required tool or semantic family"},
        "dominance_rule": "higher success and no higher total tokens, wall time, or observed cost; strict on at least one dimension",
        "claim_boundary": "finite model-provider configurations and benchmark semantic populations only",
    }


def self_check() -> None:
    bootstrap = _bootstrap([0.0, 1.0], samples=1_000, seed=7)
    assert bootstrap["estimate"] == 0.5
    assert bootstrap["ci95"][0] <= 0.5 <= bootstrap["ci95"][1]  # type: ignore[index]
    base = {metric: 0.0 for metric in METRICS}
    system = {**base, "success": 0.2, "total_tokens": -2.0, "wall_time_seconds": -1.0, "cost_usd": -0.1}
    worse = {**base, "success": 0.1, "total_tokens": -1.0, "wall_time_seconds": 0.0, "cost_usd": 0.0}
    assert _dominates({"metrics": {k: {"estimate": system[k]} for k in ("success", "total_tokens", "wall_time_seconds", "cost_usd")}}, {"metrics": {k: {"estimate": worse[k]} for k in ("success", "total_tokens", "wall_time_seconds", "cost_usd")}})
    cells = {
        f"f{index}": {
            "base_low": {**base, "success": 1.0},
            "base_stress": {**base, "success": 0.0},
            "repair_low": {**base, "success": 1.0},
            "repair_stress": {**base, "success": 1.0},
        }
        for index in range(4)
    }
    assert all(item["success"] == 1.0 for item in _move(cells).values())
    assert set(_quartiles(cells)) == {"Q1", "Q2", "Q3", "Q4"}


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capacity-input", type=Path, action="append")
    parser.add_argument("--discovery-input", type=Path, action="append")
    parser.add_argument("--repair-input", type=Path, action="append")
    parser.add_argument("--panel", type=Path, default=PANEL)
    parser.add_argument("--openrouter-prices", type=Path, default=OPENROUTER_PRICES)
    parser.add_argument("--google-prices", type=Path, default=GOOGLE_PRICES)
    parser.add_argument("--candidate-prices", type=Path, action="append")
    parser.add_argument("--protocol", type=Path, default=PROTOCOL)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=20260816)
    parser.add_argument("--expected-capacity-infrastructure-id", action="append")
    parser.add_argument("--expected-discovery-infrastructure-id", action="append")
    parser.add_argument("--expected-repair-infrastructure-id", action="append")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)
    if args.self_check:
        self_check()
        print(canonical_json({"passed": True}))
        return 0
    if not args.capacity_input or not args.discovery_input or not args.repair_input:
        parser.error("analysis requires capacity, discovery, and repair inputs")
    panel = _object(args.panel)
    core_models = len(panel["configurations"])  # type: ignore[arg-type]
    auxiliary_models = len(panel["auxiliary_models"])  # type: ignore[arg-type]
    payload = analyze(
        capacity=_rows(
            args.capacity_input,
            expected_rows=core_models * 576,
            allowed_infrastructure=frozenset(
                args.expected_capacity_infrastructure_id
                if args.expected_capacity_infrastructure_id is not None
                else RETAINED_ITT_INFRASTRUCTURE_IDS
            ),
        ),
        discovery=_rows(
            args.discovery_input,
            expected_rows=core_models * 216,
            allowed_infrastructure=frozenset(args.expected_discovery_infrastructure_id or ()),
        ),
        repair=_rows(
            args.repair_input,
            expected_rows=auxiliary_models * 144,
            allowed_infrastructure=frozenset(args.expected_repair_infrastructure_id or ()),
        ),
        panel=panel,
        openrouter_prices=_object(args.openrouter_prices),
        google_prices=_object(args.google_prices),
        candidate_prices=[_object(path) for path in (args.candidate_prices or [])],
        samples=args.bootstrap_samples,
        seed=args.seed,
    )
    payload["analysis_status"] = "descriptive release-panel reanalysis under the pre-existing protocol"
    payload["inputs"] = [
        {"path": str(path.resolve().relative_to(ROOT)), "sha256": _sha(path)}
        for path in [
            *args.capacity_input,
            *args.discovery_input,
            *args.repair_input,
            args.panel,
            args.openrouter_prices,
            args.google_prices,
            *(args.candidate_prices or []),
            args.protocol,
        ]
    ]
    payload["analysis_source_sha256"] = _sha(Path(__file__))
    payload["artifact_hash"] = stable_hash(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(canonical_json(payload) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
