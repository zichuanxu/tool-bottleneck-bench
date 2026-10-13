"""Concurrent, development-only transport calibration for hosted model lanes."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from math import ceil
from statistics import median
from threading import Lock
from time import perf_counter
from typing import Mapping, Sequence

from bottleneckbench.design import Split, tasks_for_split, visible_menu
from bottleneckbench.discovery import (
    DiscoveryCondition,
    Retriever,
    discovery_cases,
    run_discovery_episode,
)
from bottleneckbench.experiments import _conditions, string_sequence
from bottleneckbench.providers import ChatCompletionsClient
from bottleneckbench.runtime import _system_prompt
from bottleneckbench.study_design import StudyRole
from bottleneckbench.core.agnes import AgnesError, ModelRequest
from bottleneckbench.core.schema import JSON, RunStatus, canonical_json, stable_hash


def _percentile(values: Sequence[float], probability: float) -> float:
    if not values or not 0 < probability <= 1:
        raise ValueError("percentile requires values and a valid probability")
    ordered = sorted(values)
    return ordered[max(0, ceil(probability * len(ordered)) - 1)]


def _declared_model_lane_count(
    policy: Mapping[str, object], *, inferred: int
) -> int:
    value = int(policy.get("model_lane_count", inferred))
    if value <= 0:
        raise ValueError("model_lane_count must be positive")
    return value


def _client_for_configuration(
    configuration: Mapping[str, object],
    policy: Mapping[str, object],
    *,
    interval: float | None = None,
) -> ChatCompletionsClient:
    """Build the exact frozen provider transport used by calibration."""

    return ChatCompletionsClient(
        endpoint=str(configuration["endpoint"]),
        api_key_env=str(configuration["api_key_env"]),
        timeout_seconds=float(policy["timeout_seconds"]),
        max_retries=int(policy["transport_retries"]),
        minimum_request_interval_seconds=(
            float(
                configuration.get(
                    "minimum_request_interval_seconds",
                    policy.get("minimum_request_interval_seconds", 0.0),
                )
            )
            if interval is None
            else interval
        ),
        api_key_header=str(configuration.get("api_key_header", "Authorization")),
        provider_only=string_sequence(configuration, "provider_only"),
        provider_allow_fallbacks=bool(
            configuration.get("provider_allow_fallbacks", True)
        ),
        provider_data_collection=(
            str(configuration["provider_data_collection"])
            if configuration.get("provider_data_collection") is not None
            else None
        ),
        provider_require_parameters=bool(
            configuration.get("provider_require_parameters", False)
        ),
        provider_quantizations=string_sequence(
            configuration, "provider_quantizations"
        ),
        service_tier=(
            str(configuration["service_tier"])
            if configuration.get("service_tier") is not None
            else None
        ),
        accepted_response_models=string_sequence(
            configuration, "accepted_response_models"
        ),
        proxy_url=(
            str(configuration["proxy_url"])
            if configuration.get("proxy_url") is not None
            else None
        ),
        omit_temperature=bool(configuration.get("omit_temperature", False)),
        omit_seed=bool(configuration.get("omit_seed", False)),
        omit_parallel_tool_calls=bool(
            configuration.get("omit_parallel_tool_calls", False)
        ),
        require_usage=bool(configuration.get("require_usage", True)),
        stream=bool(configuration.get("stream", False)),
        disable_response_cache=bool(
            configuration.get("disable_response_cache", False)
        ),
    )


def _calibrate_model(
    configuration: Mapping[str, object],
    policy: Mapping[str, object],
    *,
    interval: float,
    requests: int,
) -> JSON:
    model = str(configuration["model"])
    harness = str(configuration["harness"])
    client = _client_for_configuration(configuration, policy, interval=interval)
    cases = discovery_cases(Split.DEVELOPMENT)
    latencies: list[float] = []
    retry_deltas: list[int] = []
    terminal_failures = 0
    native_contract_failures = 0
    exact_selection_successes = 0
    error_counts: dict[str, int] = {}
    response_ids: list[str] = []
    for index in range(requests):
        case = cases[index % len(cases)]
        condition = DiscoveryCondition(
            case_id=case.case_id,
            split=Split.DEVELOPMENT,
            model=model,
            harness=harness,
            retriever=Retriever.FLAT,
            registry_size=32,
            visible_tools=16,
            seed=case.seed,
        )
        retries_before = client.retry_count
        started = perf_counter()
        episode = run_discovery_episode(condition, case, client)
        latencies.append(perf_counter() - started)
        retry_deltas.append(client.retry_count - retries_before)
        terminal_failures += episode.status is RunStatus.PROVIDER_FAILURE
        native_contract_failures += any(
            error == "one_tool_call_required"
            or error.startswith("model_response_contract:")
            for error in episode.errors
        )
        exact_selection_successes += episode.success
        if episode.response_id:
            response_ids.append(episode.response_id)
        for error in episode.errors:
            error_counts[error] = error_counts.get(error, 0) + 1
    half = requests // 2
    first_half_retries = sum(retry_deltas[:half])
    second_half_retries = sum(retry_deltas[half:])
    latency_median = median(latencies)
    latency_p90 = _percentile(latencies, 0.90)
    native_successes = requests - terminal_failures - native_contract_failures
    passed = _transport_model_passed(
        {
            "requests": requests,
            "terminal_infrastructure_failures": terminal_failures,
            "first_half_retries": first_half_retries,
            "second_half_retries": second_half_retries,
            "latency_median_seconds": latency_median,
            "latency_p90_seconds": latency_p90,
        },
        interval=interval,
    )
    return {
        "model": model,
        "harness": harness,
        "interval_seconds": interval,
        "requests": requests,
        "native_contract_successes": native_successes,
        "exact_selection_successes": exact_selection_successes,
        "terminal_infrastructure_failures": terminal_failures,
        "native_contract_failures": native_contract_failures,
        "retry_count": sum(retry_deltas),
        "first_half_retries": first_half_retries,
        "second_half_retries": second_half_retries,
        "latency_median_seconds": latency_median,
        "latency_p90_seconds": latency_p90,
        "response_ids_hash": stable_hash(response_ids),
        "error_counts": dict(sorted(error_counts.items())),
        "passed": passed,
    }


def _transport_model_passed(
    result: Mapping[str, object], *, interval: float
) -> bool:
    """Evaluate infrastructure stability without selecting on model behavior.

    Whether a response obeys the native tool-call contract is an agent outcome,
    not a transport property.  It remains recorded for downstream diagnostics
    and concurrency-shift checks but cannot exclude a model from the benchmark.
    """

    requests = int(result["requests"])
    if requests <= 0:
        raise ValueError("transport result requires a positive request count")
    return (
        int(result["terminal_infrastructure_failures"]) / requests <= 0.02
        and int(result["second_half_retries"])
        <= int(result["first_half_retries"])
        and float(result["latency_p90_seconds"])
        <= 2 * float(result["latency_median_seconds"]) + interval
    )


def rescore_transport_calibration(
    calibration: Mapping[str, object],
) -> JSON:
    """Recompute the transport-only decision from immutable trial summaries."""

    if calibration.get("audit") != "factorized-capacity-transport-calibration":
        raise ValueError("input is not a transport calibration artifact")
    trials = calibration.get("trials")
    if not isinstance(trials, list) or not trials:
        raise ValueError("transport calibration is missing trials")
    rescored_trials: list[JSON] = []
    selected_by_model: dict[str, float] = {}
    for trial in trials:
        if not isinstance(trial, dict) or not isinstance(trial.get("models"), list):
            raise ValueError("transport calibration trial is malformed")
        interval = float(trial["interval_seconds"])
        models: list[JSON] = []
        for raw_result in trial["models"]:
            if not isinstance(raw_result, dict):
                raise ValueError("transport model result is malformed")
            result = dict(raw_result)
            result["passed"] = _transport_model_passed(
                result,
                interval=interval,
            )
            result["native_contract_diagnostic_only"] = True
            models.append(result)
        passed = all(result["passed"] is True for result in models)
        rescored_trials.append(
            {
                **trial,
                "passed": passed,
                "models": models,
            }
        )
        for result in models:
            model = str(result["model"])
            if result["passed"] is True and model not in selected_by_model:
                selected_by_model[model] = interval
    observed_models = {
        str(result["model"])
        for trial in rescored_trials
        for result in trial["models"]
    }
    passed = bool(observed_models) and set(selected_by_model) == observed_models
    shared_values = set(selected_by_model.values())
    shared_selected = next(iter(shared_values)) if len(shared_values) == 1 else None
    previous_decision = {
        "passed": calibration.get("passed"),
        "selected_interval_seconds": calibration.get(
            "selected_interval_seconds"
        ),
        "trial_passes": [
            trial.get("passed") if isinstance(trial, dict) else None
            for trial in trials
        ],
    }
    return {
        **dict(calibration),
        "passed": passed,
        "selected_interval_seconds": shared_selected,
        "selected_interval_seconds_by_model": dict(sorted(selected_by_model.items())),
        "trials": rescored_trials,
        "decision_version": "transport-only-per-model-v3",
        "superseded_decision_hash": stable_hash(previous_decision),
        "raw_trials_hash": stable_hash(trials),
        "decision_note": (
            "Native tool-call contract adherence remains an observed model "
            "diagnostic and is not an infrastructure admission criterion. "
            "Each independently served model binds its own smallest passing "
            "request interval; no provider is forced to share another provider's pacing."
        ),
    }


def calibrate_transport(
    panel: Mapping[str, object],
    *,
    requests_per_model: int = 20,
    intervals: Sequence[float] | None = None,
) -> JSON:
    """Choose the smallest passing interval while all model lanes contend normally."""

    if requests_per_model < 20:
        raise ValueError("transport calibration requires at least 20 requests per model")
    policy = panel.get("execution_policy")
    configurations = panel.get("configurations")
    calibration = panel.get("transport_calibration")
    if not isinstance(policy, dict) or not isinstance(configurations, list):
        raise ValueError("candidate panel is missing execution settings")
    primary = [
        item
        for item in configurations
        if isinstance(item, dict)
        and item.get("study_role") == "primary"
        and item.get("discovery_enabled") is True
    ]
    lane_count = _declared_model_lane_count(policy, inferred=len(primary))
    if len(primary) != lane_count or len(
        {str(item.get("model")) for item in primary}
    ) != lane_count:
        raise ValueError(
            "transport calibration requires the declared number of distinct primary models"
        )
    if intervals is None:
        raw = calibration.get("candidate_intervals_seconds") if isinstance(calibration, dict) else None
        if not isinstance(raw, list):
            raise ValueError("candidate panel is missing calibration intervals")
        intervals = tuple(float(value) for value in raw)
    ordered = tuple(sorted(set(float(value) for value in intervals)))
    if not ordered or ordered[0] < 0:
        raise ValueError("calibration intervals must be nonnegative")

    trials: list[JSON] = []
    selected_by_model: dict[str, float] = {}
    for interval in ordered:
        with ThreadPoolExecutor(max_workers=len(primary)) as executor:
            model_results = list(
                executor.map(
                    lambda item: _calibrate_model(
                        item,
                        policy,
                        interval=interval,
                        requests=requests_per_model,
                    ),
                    primary,
                )
            )
        passed = all(result["passed"] is True for result in model_results)
        trials.append(
            {
                "interval_seconds": interval,
                "passed": passed,
                "models": sorted(model_results, key=lambda item: str(item["model"])),
            }
        )
        for result in model_results:
            model = str(result["model"])
            if result["passed"] is True and model not in selected_by_model:
                selected_by_model[model] = interval
        if len(selected_by_model) == len(primary):
            break
    shared_values = set(selected_by_model.values())
    shared_selected = next(iter(shared_values)) if len(shared_values) == 1 else None
    return {
        "audit": "factorized-capacity-transport-calibration",
        "split": Split.DEVELOPMENT.value,
        "passed": len(selected_by_model) == len(primary),
        "panel_hash": stable_hash(dict(panel)),
        "requests_per_model_per_trial": requests_per_model,
        "selected_interval_seconds": shared_selected,
        "selected_interval_seconds_by_model": dict(sorted(selected_by_model.items())),
        "trials": trials,
        "confirmatory_weight": 0,
    }


def _calibrate_model_concurrency(
    configuration: Mapping[str, object],
    policy: Mapping[str, object],
    *,
    workers: int,
    requests: int,
) -> JSON:
    """Probe realistic first-turn payloads under bounded episode pipelining."""

    model = str(configuration["model"])
    harness = str(configuration["harness"])
    timeout_seconds = float(policy["timeout_seconds"])
    client = _client_for_configuration(configuration, policy)
    tasks = {task.task_id: task for task in tasks_for_split(Split.DEVELOPMENT)}
    diagnostic_conditions = _conditions(
        Split.DEVELOPMENT,
        model,
        harness,
        StudyRole.PRIMARY,
    )[:12]
    if len(diagnostic_conditions) != 12:
        raise AssertionError("concurrency calibration requires one balanced 12-cell block")
    episode_policy = policy.get("episode_policy")
    max_tokens = (
        int(episode_policy.get("max_completion_tokens", 1024))
        if isinstance(episode_policy, dict)
        else 1024
    )

    counter_lock = Lock()
    current_inflight = 0
    maximum_inflight = 0

    def probe(index: int) -> JSON:
        nonlocal current_inflight, maximum_inflight
        condition = diagnostic_conditions[index % len(diagnostic_conditions)]
        task = tasks[condition.task_id]
        tools = tuple(tool.to_openai() for tool in visible_menu(task, condition.visible_tools))
        request = ModelRequest(
            model=model,
            messages=(
                {"role": "system", "content": _system_prompt(harness)},
                {"role": "user", "content": task.request},
            ),
            tools=tools,
            temperature=0.0,
            max_tokens=max_tokens,
            seed=condition.seed,
        )
        eligible = tuple(step for step in task.program if not step.depends_on)
        with counter_lock:
            current_inflight += 1
            maximum_inflight = max(maximum_inflight, current_inflight)
        started = perf_counter()
        try:
            response = client.complete(request)
        except AgnesError as exc:
            return {
                "latency_seconds": perf_counter() - started,
                "terminal_failure": True,
                "native_contract": False,
                "exact_selection": False,
                "response_id": None,
                "error": f"provider_failure:{type(exc).__name__}",
            }
        finally:
            with counter_lock:
                current_inflight -= 1
        native_contract = (
            not response.response_errors and len(response.tool_calls) == 1
        )
        exact_selection = False
        if native_contract:
            call = response.tool_calls[0]
            exact_selection = any(
                call.name == step.tool_name and call.arguments == step.argument_map
                for step in eligible
            )
        return {
            "latency_seconds": perf_counter() - started,
            "terminal_failure": False,
            "native_contract": native_contract,
            "exact_selection": exact_selection,
            "response_id": response.raw_id,
            "error": (
                None
                if native_contract
                else "model_response_contract"
                if response.response_errors
                else "one_tool_call_required"
            ),
            "request_tool_count": len(request.tools),
            "request_schema_bytes": len(canonical_json(list(request.tools)).encode("utf-8")),
        }

    trial_started = perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as executor:
        rows = list(executor.map(probe, range(requests)))
    elapsed = perf_counter() - trial_started
    latencies = [float(row["latency_seconds"]) for row in rows]
    terminal_failures = sum(bool(row["terminal_failure"]) for row in rows)
    native_contract_successes = sum(bool(row["native_contract"]) for row in rows)
    exact_selection_successes = sum(bool(row["exact_selection"]) for row in rows)
    retry_times = client.retry_event_times
    midpoint = trial_started + elapsed / 2
    first_half_retries = sum(value < midpoint for value in retry_times)
    second_half_retries = len(retry_times) - first_half_retries
    error_counts: dict[str, int] = {}
    for row in rows:
        error = row.get("error")
        if error:
            error_counts[str(error)] = error_counts.get(str(error), 0) + 1
    transport_passed = (
        terminal_failures / requests <= 0.02
        and second_half_retries <= first_half_retries
        and _percentile(latencies, 0.90) < timeout_seconds
    )
    return {
        "model": model,
        "harness": harness,
        "workers": workers,
        "requests": requests,
        "maximum_observed_inflight": maximum_inflight,
        "native_contract_successes": native_contract_successes,
        "exact_selection_successes": exact_selection_successes,
        "terminal_infrastructure_failures": terminal_failures,
        "retry_count": len(retry_times),
        "first_half_retries": first_half_retries,
        "second_half_retries": second_half_retries,
        "elapsed_seconds": elapsed,
        "completed_requests_per_second": requests / elapsed,
        "latency_median_seconds": median(latencies),
        "latency_p90_seconds": _percentile(latencies, 0.90),
        "mean_request_tool_count": sum(
            int(row.get("request_tool_count", 0)) for row in rows
        ) / requests,
        "mean_request_schema_bytes": sum(
            int(row.get("request_schema_bytes", 0)) for row in rows
        ) / requests,
        "response_ids_hash": stable_hash(
            [row["response_id"] for row in rows if row.get("response_id")]
        ),
        "error_counts": dict(sorted(error_counts.items())),
        "transport_passed": transport_passed,
    }


def _concurrency_decision(
    trials: Sequence[Mapping[str, object]],
    policy: Mapping[str, object],
    *,
    minimum_throughput_gain: float,
) -> tuple[dict[str, int], dict[str, JSON]]:
    by_model: dict[str, list[Mapping[str, object]]] = {}
    for trial in trials:
        models = trial.get("models")
        if not isinstance(models, list):
            raise ValueError("concurrency trial is missing model results")
        for result in models:
            if not isinstance(result, dict) or not isinstance(result.get("model"), str):
                raise ValueError("concurrency model result is invalid")
            by_model.setdefault(str(result["model"]), []).append(result)

    timeout_seconds = float(policy["timeout_seconds"])
    raw_intervals = policy.get("minimum_request_interval_seconds_by_model", {})
    intervals_by_model = (
        {str(key): float(value) for key, value in raw_intervals.items()}
        if isinstance(raw_intervals, dict)
        else {}
    )

    def transport_passed(result: Mapping[str, object]) -> bool:
        required = {
            "requests",
            "terminal_infrastructure_failures",
            "first_half_retries",
            "second_half_retries",
            "latency_p90_seconds",
        }
        if not required <= set(result):
            explicit = result.get("transport_passed")
            return (
                explicit is True
                if explicit is not None
                else result.get("stability_passed") is True
            )
        requests = int(result["requests"])
        if requests <= 0:
            return False
        return (
            int(result["terminal_infrastructure_failures"]) / requests <= 0.02
            and int(result["second_half_retries"])
            <= int(result["first_half_retries"])
            and float(result["latency_p90_seconds"]) < timeout_seconds
        )

    selected: dict[str, int] = {}
    details: dict[str, JSON] = {}
    for model, unsorted_results in sorted(by_model.items()):
        interval = intervals_by_model.get(
            model, float(policy.get("minimum_request_interval_seconds", 0.0))
        )
        start_rate_ceiling = 1.0 / interval if interval > 0 else float("inf")
        results = sorted(unsorted_results, key=lambda item: int(item["workers"]))
        baseline = results[0]
        if int(baseline["workers"]) != 1 or not transport_passed(baseline):
            details[model] = {
                "selected_workers": None,
                "reason": "serial_transport_baseline_failed",
            }
            continue
        chosen = 1
        previous = baseline
        reason = "no_higher_candidate"
        for result in results[1:]:
            workers = int(result["workers"])
            if not transport_passed(result):
                reason = f"transport_failed_at_{workers}"
                break
            if int(result["native_contract_successes"]) < int(
                baseline["native_contract_successes"]
            ) - 1:
                reason = f"native_contract_shift_at_{workers}"
                break
            if int(result["exact_selection_successes"]) < int(
                baseline["exact_selection_successes"]
            ) - 1:
                reason = f"diagnostic_selection_shift_at_{workers}"
                break
            previous_throughput = float(previous["completed_requests_per_second"])
            if previous_throughput >= 0.90 * start_rate_ceiling:
                reason = f"request_start_ceiling_reached_at_{chosen}"
                break
            throughput = float(result["completed_requests_per_second"])
            if throughput < previous_throughput * (1.0 + minimum_throughput_gain):
                reason = f"throughput_gain_below_threshold_at_{workers}"
                break
            chosen = workers
            previous = result
            reason = "largest_candidate_passed"
        selected[model] = chosen
        details[model] = {"selected_workers": chosen, "reason": reason}
    return selected, details


def calibrate_concurrency(
    panel: Mapping[str, object],
    *,
    requests_per_model: int = 24,
    candidates: Sequence[int] = (1, 2, 4, 8),
    minimum_throughput_gain: float = 0.10,
) -> JSON:
    """Select the largest contiguous stable and materially faster worker limit."""

    if requests_per_model < 24:
        raise ValueError("concurrency calibration requires at least 24 requests per model")
    policy = panel.get("execution_policy")
    configurations = panel.get("configurations")
    if not isinstance(policy, dict) or not isinstance(configurations, list):
        raise ValueError("candidate panel is missing execution settings")
    primary = [
        item
        for item in configurations
        if isinstance(item, dict)
        and item.get("study_role") == "primary"
        and item.get("discovery_enabled") is True
    ]
    lane_count = _declared_model_lane_count(policy, inferred=len(primary))
    if len(primary) != lane_count or len(
        {str(item.get("model")) for item in primary}
    ) != lane_count:
        raise ValueError(
            "concurrency calibration requires the declared number of distinct primary models"
        )
    ordered = tuple(sorted(set(int(value) for value in candidates)))
    if not ordered or ordered[0] != 1 or any(value <= 0 for value in ordered):
        raise ValueError("concurrency candidates must be positive and start at one")
    if minimum_throughput_gain <= 0:
        raise ValueError("minimum throughput gain must be positive")

    trials: list[JSON] = []
    for workers in ordered:
        with ThreadPoolExecutor(max_workers=len(primary)) as executor:
            model_results = list(
                executor.map(
                    lambda item: _calibrate_model_concurrency(
                        item,
                        policy,
                        workers=workers,
                        requests=requests_per_model,
                    ),
                    primary,
                )
            )
        trials.append(
            {
                "workers": workers,
                "models": sorted(model_results, key=lambda item: str(item["model"])),
            }
        )

    selected, details = _concurrency_decision(
        trials,
        policy,
        minimum_throughput_gain=minimum_throughput_gain,
    )

    return {
        "audit": "factorized-capacity-concurrency-calibration",
        "split": Split.DEVELOPMENT.value,
        "passed": len(selected) == len(primary),
        "panel_hash": stable_hash(dict(panel)),
        "requests_per_model_per_trial": requests_per_model,
        "candidate_workers": list(ordered),
        "minimum_throughput_gain": minimum_throughput_gain,
        "selected_workers_by_model": selected,
        "selection_details": details,
        "trials": trials,
        "confirmatory_weight": 0,
    }


def rescore_concurrency_calibration(
    calibration: Mapping[str, object],
    panel: Mapping[str, object],
) -> JSON:
    """Recompute only the registered decision from immutable raw probe summaries."""

    if calibration.get("audit") != "factorized-capacity-concurrency-calibration":
        raise ValueError("input is not a concurrency calibration artifact")
    trials = calibration.get("trials")
    policy = panel.get("execution_policy")
    if not isinstance(trials, list) or not isinstance(policy, dict):
        raise ValueError("calibration or panel is missing decision inputs")
    minimum_gain = float(calibration.get("minimum_throughput_gain", 0.10))
    selected, details = _concurrency_decision(
        trials,
        policy,
        minimum_throughput_gain=minimum_gain,
    )
    primary_models = {
        str(item.get("model"))
        for item in panel.get("configurations", [])
        if isinstance(item, dict)
        and item.get("study_role") == "primary"
        and item.get("discovery_enabled") is True
    }
    previous_decision = {
        "passed": calibration.get("passed"),
        "selected_workers_by_model": calibration.get("selected_workers_by_model"),
        "selection_details": calibration.get("selection_details"),
    }
    lane_count = _declared_model_lane_count(policy, inferred=len(primary_models))
    return {
        **dict(calibration),
        "passed": (
            set(selected) == primary_models and len(primary_models) == lane_count
        ),
        "selected_workers_by_model": selected,
        "selection_details": details,
        "decision_version": "admission-aligned-relative-to-serial-v3",
        "superseded_decision_hash": stable_hash(previous_decision),
        "raw_trials_hash": stable_hash(trials),
    }
