"""Fail-closed transitions from a candidate panel to a calibrated frozen panel."""

from __future__ import annotations

import json
from typing import Mapping

from bottleneckbench.core.schema import JSON, canonical_json, stable_hash


def _copy_object(payload: Mapping[str, object]) -> JSON:
    copied = json.loads(canonical_json(dict(payload)))
    if not isinstance(copied, dict):
        raise AssertionError("canonical panel copy must remain an object")
    return copied


def _primary_models(panel: Mapping[str, object]) -> set[str]:
    configurations = panel.get("configurations")
    if not isinstance(configurations, list):
        raise ValueError("model panel configurations must be a list")
    primary = [
        item
        for item in configurations
        if isinstance(item, dict)
        and item.get("study_role") == "primary"
        and item.get("discovery_enabled") is True
    ]
    models = {str(item.get("model")) for item in primary}
    policy = panel.get("execution_policy")
    if not isinstance(policy, dict):
        raise ValueError("model panel execution_policy must be an object")
    expected = int(policy.get("model_lane_count", len(primary)))
    if expected <= 0 or len(primary) != expected or len(models) != expected:
        raise ValueError("candidate panel does not contain its declared model lanes")
    return models


def apply_transport_calibration(
    panel: Mapping[str, object],
    calibration: Mapping[str, object],
    *,
    completed_at: str,
    artifact: str,
) -> JSON:
    """Bind each model's smallest passing request interval into the panel."""

    if panel.get("frozen_for_study") is not False:
        raise ValueError("transport calibration requires an unfrozen candidate panel")
    _primary_models(panel)
    if (
        calibration.get("audit") != "factorized-capacity-transport-calibration"
        or calibration.get("passed") is not True
        or calibration.get("panel_hash") != stable_hash(dict(panel))
    ):
        raise ValueError("passing transport calibration does not bind this panel")
    primary_models = _primary_models(panel)
    raw_by_model = calibration.get("selected_interval_seconds_by_model")
    if isinstance(raw_by_model, dict):
        if set(str(key) for key in raw_by_model) != primary_models:
            raise ValueError("transport calibration does not cover every primary model")
        selected_by_model = {
            str(model): float(value) for model, value in raw_by_model.items()
        }
    else:
        selected = calibration.get("selected_interval_seconds")
        if not isinstance(selected, (int, float)):
            raise ValueError("transport calibration did not select a valid interval")
        selected_by_model = {model: float(selected) for model in primary_models}
    if any(value < 0 for value in selected_by_model.values()):
        raise ValueError("transport calibration intervals must be nonnegative")
    result = _copy_object(panel)
    policy = result.get("execution_policy")
    if not isinstance(policy, dict):
        raise AssertionError("validated execution policy was lost")
    policy["minimum_request_interval_seconds"] = min(selected_by_model.values())
    policy["minimum_request_interval_seconds_by_model"] = dict(
        sorted(selected_by_model.items())
    )
    configurations = result.get("configurations")
    if not isinstance(configurations, list):
        raise AssertionError("validated configurations were lost")
    for configuration in configurations:
        if not isinstance(configuration, dict):
            raise AssertionError("validated configuration became invalid")
        model = str(configuration.get("model"))
        if model not in selected_by_model:
            raise ValueError("every paired configuration must use a calibrated model")
        configuration["minimum_request_interval_seconds"] = selected_by_model[model]
    registered = result.get("transport_calibration")
    if not isinstance(registered, dict):
        raise ValueError("candidate panel is missing transport calibration policy")
    registered.update(
        {
            "status": "passed",
            "completed_at": completed_at,
            "artifact": artifact,
            "artifact_content_hash": stable_hash(dict(calibration)),
            "selected_interval_seconds": calibration.get(
                "selected_interval_seconds"
            ),
            "selected_interval_seconds_by_model": dict(
                sorted(selected_by_model.items())
            ),
        }
    )
    return result


def apply_concurrency_calibration(
    panel: Mapping[str, object],
    calibration: Mapping[str, object],
    *,
    completed_at: str,
    artifact: str,
) -> JSON:
    """Bind per-model workers and close the final panel for study execution."""

    if panel.get("frozen_for_study") is not False:
        raise ValueError("concurrency calibration requires an unfrozen candidate panel")
    primary_models = _primary_models(panel)
    selected = calibration.get("selected_workers_by_model")
    if (
        calibration.get("audit") != "factorized-capacity-concurrency-calibration"
        or calibration.get("passed") is not True
        or calibration.get("panel_hash") != stable_hash(dict(panel))
        or not isinstance(selected, dict)
        or set(str(key) for key in selected) != primary_models
    ):
        raise ValueError("passing concurrency calibration does not bind this panel")
    workers: dict[str, int] = {}
    for model, value in selected.items():
        worker_count = int(value)
        if worker_count <= 0:
            raise ValueError("selected worker counts must be positive")
        workers[str(model)] = worker_count

    result = _copy_object(panel)
    configurations = result.get("configurations")
    if not isinstance(configurations, list):
        raise AssertionError("validated configurations were lost")
    for configuration in configurations:
        if not isinstance(configuration, dict):
            raise AssertionError("validated configuration became invalid")
        model = str(configuration.get("model"))
        if model not in workers:
            raise ValueError("every paired configuration must use a calibrated model")
        cap = int(configuration.get("provider_concurrency_cap", 0))
        if workers[model] > cap:
            raise ValueError("selected workers exceed the frozen provider cap")
        configuration["workers_per_job"] = workers[model]

    registered = result.get("concurrency_calibration")
    if not isinstance(registered, dict):
        raise ValueError("candidate panel is missing concurrency calibration policy")
    registered.update(
        {
            "status": "passed",
            "completed_at": completed_at,
            "artifact": artifact,
            "artifact_content_hash": stable_hash(dict(calibration)),
            "selected_workers_by_model": dict(sorted(workers.items())),
        }
    )
    result["frozen_for_study"] = True
    result["frozen_at"] = completed_at
    return result
