"""Reproducible command line for audits, plans, runs, and analyses."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Mapping, Sequence

from bottleneckbench.admission import development_admission
from bottleneckbench.calibration import (
    calibrate_concurrency,
    calibrate_transport,
    rescore_concurrency_calibration,
    rescore_transport_calibration,
)
from bottleneckbench.analysis import (
    analyze_capacity_rows,
    analyze_discovery_rows,
)
from bottleneckbench.design import Split, static_construct_audit
from bottleneckbench.discovery import discovery_construct_audit
from bottleneckbench.experiments import (
    EpisodePolicy,
    ExecutionProfile,
    _atomic_write,
    build_discovery_plan,
    build_plan,
    execute_discovery_plan,
    execute_plan,
    freeze_discovery_plan,
    freeze_plan,
    load_discovery_plan,
    load_plan,
    string_sequence,
)
from bottleneckbench.providers import ChatCompletionsClient
from bottleneckbench.orchestrator import (
    run_capacity_study_lanes,
    run_development_lanes,
    run_transfer_lanes,
)
from bottleneckbench.panel import (
    apply_concurrency_calibration,
    apply_transport_calibration,
)
from bottleneckbench.runtime import (
    efficiency_policy_audit,
    runtime_construct_audit,
)
from bottleneckbench.study_design import StudyRole, efficient_design_audit
from bottleneckbench.workload import prospective_workload
from bottleneckbench.core.schema import JSON, stable_hash


_PAID_COMMANDS = frozenset(
    {
        "calibrate-transport",
        "calibrate-concurrency",
        "run-development",
        "run-study",
        "run",
    }
)


def _read_json(path: Path) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _read_rows(paths: Sequence[Path]) -> list[JSON]:
    rows: list[JSON] = []
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ValueError(f"cannot read JSONL: {path}") from exc
        for line_number, line in enumerate(lines, start=1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"non-object JSONL row at {path}:{line_number}")
            rows.append(row)
    return rows


def _text_hash(path: Path) -> str:
    try:
        return stable_hash(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read frozen text artifact: {path}") from exc


def _panel_configuration(
    panel: Mapping[str, object], model: str, harness: str
) -> Mapping[str, object]:
    if panel.get("frozen_for_study") is not True:
        raise ValueError("model panel must be frozen for the active study")
    configurations = panel.get("configurations")
    if not isinstance(configurations, list):
        raise ValueError("model panel configurations must be a list")
    matches = [
        item
        for item in configurations
        if isinstance(item, dict)
        and item.get("model") == model
        and item.get("harness") == harness
    ]
    if len(matches) != 1:
        raise ValueError("model and harness must identify one frozen panel configuration")
    return matches[0]


def _panel_execution(
    panel: Mapping[str, object], model: str, harness: str
) -> ExecutionProfile:
    configuration = _panel_configuration(panel, model, harness)
    policy = panel.get("execution_policy")
    if not isinstance(policy, dict):
        raise ValueError("model panel execution_policy must be an object")
    episode_policy_payload = configuration.get(
        "episode_policy", policy.get("episode_policy")
    )
    return ExecutionProfile(
        endpoint=str(configuration["endpoint"]),
        api_key_env=str(configuration["api_key_env"]),
        timeout_seconds=float(policy["timeout_seconds"]),
        transport_retries=int(policy["transport_retries"]),
        workers=int(configuration.get("workers_per_job", policy["workers_per_job"])),
        provider_concurrency_cap=int(
            configuration.get(
                "provider_concurrency_cap", policy["provider_concurrency_cap"]
            )
        ),
        minimum_request_interval_seconds=float(
            configuration.get(
                "minimum_request_interval_seconds",
                policy.get("minimum_request_interval_seconds", 0.0),
            )
        ),
        api_key_header=str(configuration.get("api_key_header", "Authorization")),
        episode_policy=(
            EpisodePolicy.from_json(episode_policy_payload)
            if episode_policy_payload is not None
            else None
        ),
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


def _audit(args: argparse.Namespace) -> int:
    output = args.output_dir
    splits = tuple(Split) if args.split == "all" else (Split(args.split),)
    passed = True
    for split in splits:
        static = static_construct_audit(split)
        runtime = runtime_construct_audit(split)
        efficiency_policy = efficiency_policy_audit(split)
        _atomic_write(output / f"{split.value}-static.json", static)
        _atomic_write(output / f"{split.value}-runtime.json", runtime)
        _atomic_write(
            output / f"{split.value}-efficiency-policy.json",
            efficiency_policy,
        )
        passed &= (
            static["passed"] is True
            and runtime["passed"] is True
            and efficiency_policy["passed"] is True
        )
    efficient = efficient_design_audit()
    _atomic_write(output / "efficient-design.json", efficient)
    _atomic_write(output / "prospective-workload.json", prospective_workload())
    passed &= efficient["passed"] is True
    discovery_splits = (
        (Split.DEVELOPMENT, Split.MAIN)
        if args.split == "all"
        else tuple(split for split in splits if split is not Split.TRANSFER)
    )
    for split in discovery_splits:
        discovery = discovery_construct_audit(split)
        _atomic_write(output / f"{split.value}-discovery-construct.json", discovery)
        passed &= discovery["passed"] is True
    return 0 if passed else 2


def _calibrate(args: argparse.Namespace) -> int:
    panel = _read_json(args.model_panel)
    result = calibrate_transport(
        panel,
        requests_per_model=args.requests_per_model,
    )
    _atomic_write(args.output, result)
    print(
        json.dumps(
            {
                "passed": result["passed"],
                "selected_interval_seconds": result["selected_interval_seconds"],
            },
            sort_keys=True,
        )
    )
    return 0 if result["passed"] is True else 2


def _calibrate_concurrency(args: argparse.Namespace) -> int:
    panel = _read_json(args.model_panel)
    result = calibrate_concurrency(
        panel,
        requests_per_model=args.requests_per_model,
        candidates=tuple(args.candidates),
    )
    _atomic_write(args.output, result)
    print(
        json.dumps(
            {
                "passed": result["passed"],
                "selected_workers_by_model": result["selected_workers_by_model"],
            },
            sort_keys=True,
        )
    )
    return 0 if result["passed"] is True else 2


def _rescore_transport(args: argparse.Namespace) -> int:
    result = rescore_transport_calibration(_read_json(args.input))
    _atomic_write(args.output, result)
    print(
        json.dumps(
            {
                "passed": result["passed"],
                "selected_interval_seconds": result[
                    "selected_interval_seconds"
                ],
            },
            sort_keys=True,
        )
    )
    return 0 if result["passed"] is True else 2


def _rescore_concurrency(args: argparse.Namespace) -> int:
    result = rescore_concurrency_calibration(
        _read_json(args.input),
        _read_json(args.model_panel),
    )
    _atomic_write(args.output, result)
    print(
        json.dumps(
            {
                "passed": result["passed"],
                "selected_workers_by_model": result["selected_workers_by_model"],
            },
            sort_keys=True,
        )
    )
    return 0 if result["passed"] is True else 2


def _apply_transport_panel(args: argparse.Namespace) -> int:
    panel = apply_transport_calibration(
        _read_json(args.model_panel),
        _read_json(args.calibration),
        completed_at=args.completed_at,
        artifact=str(args.calibration),
    )
    _atomic_write(args.output, panel)
    return 0


def _apply_concurrency_panel(args: argparse.Namespace) -> int:
    panel = apply_concurrency_calibration(
        _read_json(args.model_panel),
        _read_json(args.calibration),
        completed_at=args.completed_at,
        artifact=str(args.calibration),
    )
    _atomic_write(args.output, panel)
    return 0


def _run_development(args: argparse.Namespace) -> int:
    panel = _read_json(args.model_panel)
    try:
        protocol_text = args.protocol.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read frozen protocol: {args.protocol}") from exc
    result = run_development_lanes(
        panel,
        protocol_text=protocol_text,
        plan_dir=args.plan_dir,
        output_dir=args.output_dir,
        status_dir=args.status_dir,
    )
    print(json.dumps({"passed": result["passed"], "errors": result["errors"]}, sort_keys=True))
    return 0 if result["passed"] is True else 2


def _run_study(args: argparse.Namespace) -> int:
    panel = _read_json(args.model_panel)
    try:
        protocol_text = args.protocol.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read frozen protocol: {args.protocol}") from exc
    split = Split(args.split)
    if split is Split.TRANSFER:
        result = run_transfer_lanes(
            panel,
            protocol_text=protocol_text,
            plan_dir=args.plan_dir,
            output_dir=args.output_dir,
            status_dir=args.status_dir,
        )
    else:
        result = run_capacity_study_lanes(
            panel,
            split=split,
            protocol_text=protocol_text,
            plan_dir=args.plan_dir,
            output_dir=args.output_dir,
            status_dir=args.status_dir,
        )
    print(json.dumps({"passed": result["passed"], "errors": result["errors"]}, sort_keys=True))
    return 0 if result["passed"] is True else 2


def _plan(args: argparse.Namespace) -> int:
    panel = _read_json(args.model_panel)
    panel_hash = stable_hash(dict(panel))
    protocol_hash = _text_hash(args.protocol)
    execution = _panel_execution(panel, args.model, args.harness)
    configuration = _panel_configuration(panel, args.model, args.harness)
    if args.kind == "discovery":
        if configuration.get("discovery_enabled") is not True:
            raise ValueError("configuration is not registered for Discovery")
        split = Split(args.split)
        admission = _read_json(args.admission) if args.admission else None
        plan = build_discovery_plan(
            split=split,
            model=args.model,
            harness=args.harness,
            frozen_at=args.frozen_at,
            protocol_artifact_hash=protocol_hash,
            model_panel_hash=panel_hash,
            execution=execution,
            construct_audit=_read_json(
                args.audit_dir / f"{split.value}-discovery-construct.json"
            ),
            admission_audit=admission,
        )
        freeze_discovery_plan(args.output, plan)
        return 0
    split = Split(args.split)
    admission = _read_json(args.admission) if args.admission else None
    plan = build_plan(
        split=split,
        model=args.model,
        harness=args.harness,
        study_role=StudyRole(str(configuration.get("study_role", "primary"))),
        frozen_at=args.frozen_at,
        protocol_artifact_hash=protocol_hash,
        model_panel_hash=panel_hash,
        execution=execution,
        static_audit=_read_json(args.audit_dir / f"{split.value}-static.json"),
        runtime_audit=_read_json(args.audit_dir / f"{split.value}-runtime.json"),
        admission_audit=admission,
    )
    freeze_plan(args.output, plan)
    return 0


def _freeze_plan_set(args: argparse.Namespace) -> int:
    panel = _read_json(args.model_panel)
    panel_hash = stable_hash(dict(panel))
    protocol_hash = _text_hash(args.protocol)
    if panel.get("frozen_for_study") is not True:
        raise ValueError("plan-set freeze requires a calibrated frozen panel")
    configurations = panel.get("configurations")
    if not isinstance(configurations, list):
        raise ValueError("model panel configurations must be a list")
    split = Split(args.split)
    admission = _read_json(args.admission) if args.admission else None
    if split is not Split.DEVELOPMENT and admission is None:
        raise ValueError("main and transfer plan sets require development admission")
    manifest: list[JSON] = []
    for raw_configuration in configurations:
        if not isinstance(raw_configuration, dict):
            raise ValueError("model panel configuration must be an object")
        role = StudyRole(str(raw_configuration.get("study_role", "primary")))
        if split is Split.TRANSFER and role is not StudyRole.PRIMARY:
            continue
        model = str(raw_configuration["model"])
        harness = str(raw_configuration["harness"])
        slug = str(raw_configuration["slug"])
        execution = _panel_execution(panel, model, harness)
        plan = build_plan(
            split=split,
            model=model,
            harness=harness,
            study_role=role,
            frozen_at=args.frozen_at,
            protocol_artifact_hash=protocol_hash,
            model_panel_hash=panel_hash,
            execution=execution,
            static_audit=_read_json(args.audit_dir / f"{split.value}-static.json"),
            runtime_audit=_read_json(args.audit_dir / f"{split.value}-runtime.json"),
            admission_audit=admission,
        )
        path = args.output_dir / f"{slug}-capacity.json"
        freeze_plan(path, plan)
        manifest.append(
            {
                "kind": "capacity",
                "slug": slug,
                "model": model,
                "harness": harness,
                "study_role": role.value,
                "conditions": len(plan.conditions),
                "plan_hash": plan.plan_hash,
                "source_hash": plan.source_hash,
                "path": str(path),
            }
        )
        if split is Split.TRANSFER or raw_configuration.get("discovery_enabled") is not True:
            continue
        discovery = build_discovery_plan(
            split=split,
            model=model,
            harness=harness,
            frozen_at=args.frozen_at,
            protocol_artifact_hash=protocol_hash,
            model_panel_hash=panel_hash,
            execution=execution,
            construct_audit=_read_json(
                args.audit_dir / f"{split.value}-discovery-construct.json"
            ),
            admission_audit=admission,
        )
        discovery_path = args.output_dir / f"{slug}-discovery.json"
        freeze_discovery_plan(discovery_path, discovery)
        manifest.append(
            {
                "kind": "discovery",
                "slug": slug,
                "model": model,
                "harness": harness,
                "conditions": len(discovery.conditions),
                "plan_hash": discovery.plan_hash,
                "source_hash": discovery.source_hash,
                "path": str(discovery_path),
            }
        )
    result: JSON = {
        "artifact": "capacity-surface-plan-set",
        "split": split.value,
        "frozen_at": args.frozen_at,
        "model_panel_hash": panel_hash,
        "protocol_artifact_hash": protocol_hash,
        "plans": manifest,
        "planned_conditions": sum(int(item["conditions"]) for item in manifest),
    }
    _atomic_write(args.output_dir / "manifest.json", result)
    print(
        json.dumps(
            {
                "split": split.value,
                "plans": len(manifest),
                "planned_conditions": result["planned_conditions"],
            },
            sort_keys=True,
        )
    )
    return 0


def _run(args: argparse.Namespace) -> int:
    plan = (
        load_discovery_plan(args.plan)
        if args.kind == "discovery"
        else load_plan(args.plan)
    )
    panel = _read_json(args.model_panel)
    if stable_hash(dict(panel)) != plan.model_panel_hash:
        raise ValueError("live model-panel hash does not match frozen plan")
    if _text_hash(args.protocol) != plan.protocol_artifact_hash:
        raise ValueError("live protocol-artifact hash does not match frozen plan")
    if _panel_execution(panel, plan.model, plan.harness) != plan.execution:
        raise ValueError("live execution profile does not match frozen plan")
    if (
        args.kind != "discovery"
        and StudyRole(
            str(
                _panel_configuration(panel, plan.model, plan.harness).get(
                    "study_role", "primary"
                )
            )
        )
        is not plan.study_role
    ):
        raise ValueError("live study role does not match frozen plan")
    client = ChatCompletionsClient(**plan.execution.client_kwargs())
    if args.kind == "discovery":
        result = execute_discovery_plan(
            plan,
            client,
            args.output,
            max_conditions=args.max_conditions,
            workers=plan.execution.workers,
        )
    else:
        result = execute_plan(
            plan,
            client,
            args.output,
            max_conditions=args.max_conditions,
            workers=plan.execution.workers,
        )
    print(json.dumps(result, sort_keys=True))
    return 0


def _analyze(args: argparse.Namespace) -> int:
    rows = _read_rows(args.input)
    result = (
        analyze_discovery_rows(
            rows,
            bootstrap_samples=args.bootstrap_samples,
            seed=args.seed,
        )
        if args.kind == "discovery"
        else analyze_capacity_rows(
            rows,
            bootstrap_samples=args.bootstrap_samples,
            seed=args.seed,
        )
    )
    _atomic_write(args.output, result)
    return 0


def _admit(args: argparse.Namespace) -> int:
    panel = _read_json(args.model_panel)
    configurations = panel.get("configurations")
    if not isinstance(configurations, list) or not all(
        isinstance(item, dict)
        and isinstance(item.get("model"), str)
        and isinstance(item.get("harness"), str)
        for item in configurations
    ):
        raise ValueError("model panel contains an invalid configuration")
    expected_capacity = [
        (
            str(item["model"]),
            str(item["harness"]),
            StudyRole(str(item.get("study_role", "primary"))).value,
        )
        for item in configurations
    ]
    expected_discovery = [
        (str(item["model"]), str(item["harness"]))
        for item in configurations
        if item.get("discovery_enabled") is True
    ]
    for model, harness, _ in expected_capacity:
        _panel_execution(panel, model, harness)
    audit = development_admission(
        _read_rows(args.capacity),
        _read_rows(args.discovery),
        [_read_json(path) for path in args.audit],
        minimum_models=args.minimum_models,
        expected_capacity_configurations=expected_capacity,
        expected_discovery_configurations=expected_discovery,
        model_panel_hash=stable_hash(dict(panel)),
        protocol_artifact_hash=_text_hash(args.protocol),
        workflow_boundary_admission=(
            _read_json(args.workflow_boundary_admission)
            if args.workflow_boundary_admission is not None
            else None
        ),
    )
    _atomic_write(args.output, audit)
    return 0 if audit["passed"] is True else 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bottleneckbench")
    commands = parser.add_subparsers(dest="command", required=True)

    audit = commands.add_parser("audit")
    audit.add_argument("--split", choices=("all", *(split.value for split in Split)), default="all")
    audit.add_argument("--output-dir", type=Path, required=True)
    audit.set_defaults(handler=_audit)

    calibrate = commands.add_parser("calibrate-transport")
    calibrate.add_argument("--model-panel", type=Path, required=True)
    calibrate.add_argument("--requests-per-model", type=int, default=20)
    calibrate.add_argument("--output", type=Path, required=True)
    calibrate.add_argument("--allow-paid", action="store_true")
    calibrate.set_defaults(handler=_calibrate)

    concurrency = commands.add_parser("calibrate-concurrency")
    concurrency.add_argument("--model-panel", type=Path, required=True)
    concurrency.add_argument("--requests-per-model", type=int, default=24)
    concurrency.add_argument(
        "--candidates", type=int, nargs="+", default=(1, 2, 4, 8)
    )
    concurrency.add_argument("--output", type=Path, required=True)
    concurrency.add_argument("--allow-paid", action="store_true")
    concurrency.set_defaults(handler=_calibrate_concurrency)

    rescore_transport = commands.add_parser("rescore-transport")
    rescore_transport.add_argument("--input", type=Path, required=True)
    rescore_transport.add_argument("--output", type=Path, required=True)
    rescore_transport.set_defaults(handler=_rescore_transport)

    rescore_concurrency = commands.add_parser("rescore-concurrency")
    rescore_concurrency.add_argument("--input", type=Path, required=True)
    rescore_concurrency.add_argument("--model-panel", type=Path, required=True)
    rescore_concurrency.add_argument("--output", type=Path, required=True)
    rescore_concurrency.set_defaults(handler=_rescore_concurrency)

    transport_panel = commands.add_parser("apply-transport-calibration")
    transport_panel.add_argument("--model-panel", type=Path, required=True)
    transport_panel.add_argument("--calibration", type=Path, required=True)
    transport_panel.add_argument("--completed-at", required=True)
    transport_panel.add_argument("--output", type=Path, required=True)
    transport_panel.set_defaults(handler=_apply_transport_panel)

    concurrency_panel = commands.add_parser("apply-concurrency-calibration")
    concurrency_panel.add_argument("--model-panel", type=Path, required=True)
    concurrency_panel.add_argument("--calibration", type=Path, required=True)
    concurrency_panel.add_argument("--completed-at", required=True)
    concurrency_panel.add_argument("--output", type=Path, required=True)
    concurrency_panel.set_defaults(handler=_apply_concurrency_panel)

    development = commands.add_parser("run-development")
    development.add_argument("--model-panel", type=Path, required=True)
    development.add_argument("--protocol", type=Path, required=True)
    development.add_argument("--plan-dir", type=Path, required=True)
    development.add_argument("--output-dir", type=Path, required=True)
    development.add_argument("--status-dir", type=Path, required=True)
    development.add_argument("--allow-paid", action="store_true")
    development.set_defaults(handler=_run_development)

    study = commands.add_parser("run-study")
    study.add_argument("--split", choices=tuple(split.value for split in Split), required=True)
    study.add_argument("--model-panel", type=Path, required=True)
    study.add_argument("--protocol", type=Path, required=True)
    study.add_argument("--plan-dir", type=Path, required=True)
    study.add_argument("--output-dir", type=Path, required=True)
    study.add_argument("--status-dir", type=Path, required=True)
    study.add_argument("--allow-paid", action="store_true")
    study.set_defaults(handler=_run_study)

    plan = commands.add_parser("plan")
    plan.add_argument("--kind", choices=("capacity", "discovery"), required=True)
    plan.add_argument("--split", choices=tuple(split.value for split in Split), default="development")
    plan.add_argument("--model", required=True)
    plan.add_argument("--harness", required=True)
    plan.add_argument("--frozen-at", required=True)
    plan.add_argument("--audit-dir", type=Path, required=True)
    plan.add_argument("--protocol", type=Path, required=True)
    plan.add_argument("--model-panel", type=Path, required=True)
    plan.add_argument("--admission", type=Path)
    plan.add_argument("--output", type=Path, required=True)
    plan.set_defaults(handler=_plan)

    plan_set = commands.add_parser("freeze-plan-set")
    plan_set.add_argument(
        "--split", choices=tuple(split.value for split in Split), required=True
    )
    plan_set.add_argument("--frozen-at", required=True)
    plan_set.add_argument("--audit-dir", type=Path, required=True)
    plan_set.add_argument("--protocol", type=Path, required=True)
    plan_set.add_argument("--model-panel", type=Path, required=True)
    plan_set.add_argument("--admission", type=Path)
    plan_set.add_argument("--output-dir", type=Path, required=True)
    plan_set.set_defaults(handler=_freeze_plan_set)

    run = commands.add_parser("run")
    run.add_argument("--kind", choices=("capacity", "discovery"), required=True)
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--protocol", type=Path, required=True)
    run.add_argument("--model-panel", type=Path, required=True)
    run.add_argument("--max-conditions", type=int)
    run.add_argument("--allow-paid", action="store_true")
    run.set_defaults(handler=_run)

    analyze = commands.add_parser("analyze")
    analyze.add_argument("--kind", choices=("capacity", "discovery"), required=True)
    analyze.add_argument("--input", type=Path, action="append", required=True)
    analyze.add_argument("--output", type=Path, required=True)
    analyze.add_argument("--bootstrap-samples", type=int, default=10000)
    analyze.add_argument("--seed", type=int, default=20260805)
    analyze.set_defaults(handler=_analyze)

    admit = commands.add_parser("admit")
    admit.add_argument("--capacity", type=Path, action="append", required=True)
    admit.add_argument("--discovery", type=Path, action="append", required=True)
    admit.add_argument("--audit", type=Path, action="append", required=True)
    admit.add_argument("--protocol", type=Path, required=True)
    admit.add_argument("--model-panel", type=Path, required=True)
    admit.add_argument("--minimum-models", type=int, default=3)
    admit.add_argument("--workflow-boundary-admission", type=Path)
    admit.add_argument("--output", type=Path, required=True)
    admit.set_defaults(handler=_admit)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command in _PAID_COMMANDS and not args.allow_paid:
        raise ValueError("hosted execution requires explicit --allow-paid")
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
