"""Resume-safe model lanes for development, main, and transfer execution."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from math import floor
from pathlib import Path
from typing import Callable, Mapping, Sequence

from bottleneckbench.analysis import is_infrastructure_failure
from bottleneckbench.design import Split
from bottleneckbench.experiments import (
    DiscoveryRunPlan,
    RunPlan,
    _atomic_write,
    execute_discovery_plan,
    execute_plan,
    load_discovery_plan,
    load_plan,
)
from bottleneckbench.providers import ChatCompletionsClient
from bottleneckbench.runtime import CompletionClient
from bottleneckbench.study_design import StudyRole
from bottleneckbench.core.schema import JSON, stable_hash


def _rows(path: Path) -> list[JSON]:
    if not path.exists():
        return []
    rows: list[JSON] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL row at {path}:{line_number}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"JSONL row must be an object at {path}:{line_number}")
        rows.append(row)
    return rows


def infrastructure_ceiling(
    capacity_rows: Sequence[Mapping[str, object]],
    discovery_rows: Sequence[Mapping[str, object]],
    *,
    planned_capacity: int,
    planned_discovery: int,
    planned_v128: int,
    maximum_rate: float = 0.02,
) -> JSON:
    """Detect when no completion of the planned matrix can recover admission."""

    if not 0 <= maximum_rate < 1:
        raise ValueError("maximum_rate must lie in [0, 1)")
    capacity_failures = sum(is_infrastructure_failure(row) for row in capacity_rows)
    discovery_failures = sum(is_infrastructure_failure(row) for row in discovery_rows)
    v128_rows = [
        row
        for row in capacity_rows
        if isinstance(row.get("condition"), dict)
        and row["condition"].get("visible_tools") == 128
    ]
    v128_failures = sum(is_infrastructure_failure(row) for row in v128_rows)
    total_allowed = floor(maximum_rate * (planned_capacity + planned_discovery))
    v128_allowed = floor(maximum_rate * planned_v128)
    return {
        "passed": (
            capacity_failures + discovery_failures <= total_allowed
            and v128_failures <= v128_allowed
        ),
        "observed_rows": len(capacity_rows) + len(discovery_rows),
        "infrastructure_failures": capacity_failures + discovery_failures,
        "allowed_infrastructure_failures": total_allowed,
        "observed_v128_rows": len(v128_rows),
        "v128_infrastructure_failures": v128_failures,
        "allowed_v128_infrastructure_failures": v128_allowed,
    }


def _validate_plan_binding(
    plans: Sequence[RunPlan | DiscoveryRunPlan],
    *,
    panel_hash: str,
    protocol_hash: str,
) -> None:
    if any(plan.model_panel_hash != panel_hash for plan in plans):
        raise ValueError("lane plan does not match the live frozen model panel")
    if any(plan.protocol_artifact_hash != protocol_hash for plan in plans):
        raise ValueError("lane plan does not match the live frozen protocol")
    if len({plan.model for plan in plans}) != 1:
        raise ValueError("lane plans must bind one model")


def _model_lane_count(panel: Mapping[str, object], *, inferred: int) -> int:
    """Return the frozen lane count, retaining compatibility with older panels."""

    policy = panel.get("execution_policy")
    if not isinstance(policy, dict):
        raise ValueError("model panel execution_policy must be an object")
    value = int(policy.get("model_lane_count", inferred))
    if value <= 0:
        raise ValueError("model_lane_count must be positive")
    return value


def _discovery_target(split: Split, index: int, *, blocks: int, total: int) -> int:
    """Return a six-cell-aligned cumulative Discovery target for one block."""

    if split is Split.DEVELOPMENT:
        target = ((3 * (index + 1)) // 2) * 6
    elif split is Split.MAIN:
        target = (index + 1) * 6
    else:
        raise ValueError("transfer has no registered Discovery lane")
    if not 0 <= index < blocks or target > total:
        raise ValueError("Discovery interleave is incompatible with plan geometry")
    return target


def _run_capacity_discovery_lane(
    primary_configuration: Mapping[str, object],
    fixed_configuration: Mapping[str, object],
    *,
    split: Split,
    plan_dir: Path,
    output_dir: Path,
    status_dir: Path,
    panel_hash: str,
    protocol_hash: str,
    client_factory: Callable[..., CompletionClient] = ChatCompletionsClient,
) -> JSON:
    primary_slug = str(primary_configuration["slug"])
    fixed_slug = str(fixed_configuration["slug"])
    primary_plan = load_plan(plan_dir / f"{primary_slug}-capacity.json")
    discovery_plan = load_discovery_plan(plan_dir / f"{primary_slug}-discovery.json")
    fixed_plan = load_plan(plan_dir / f"{fixed_slug}-capacity.json")
    plans: tuple[RunPlan | DiscoveryRunPlan, ...] = (
        primary_plan,
        discovery_plan,
        fixed_plan,
    )
    _validate_plan_binding(plans, panel_hash=panel_hash, protocol_hash=protocol_hash)
    if any(plan.split is not split for plan in plans):
        raise ValueError("lane plans do not match the requested split")
    if primary_plan.study_role is not StudyRole.PRIMARY:
        raise ValueError("primary lane plan has the wrong study role")
    if fixed_plan.study_role is not StudyRole.ROBUSTNESS:
        raise ValueError("fixed lane plan has the wrong study role")
    primary_transport = primary_plan.execution
    if any(
        plan.execution.transport_identity != primary_transport.transport_identity
        for plan in plans
    ):
        raise ValueError("lane plans do not share one transport profile")

    client = client_factory(**primary_transport.client_kwargs())
    primary_output = output_dir / f"{primary_slug}-capacity.jsonl"
    discovery_output = output_dir / f"{primary_slug}-discovery.jsonl"
    fixed_output = output_dir / f"{fixed_slug}-capacity.jsonl"
    status_path = status_dir / f"{primary_slug}.json"
    primary_block = 12
    fixed_block = 4
    discovery_block = 6
    blocks = len(primary_plan.conditions) // primary_block
    discovery_per_block = 9 if split is Split.DEVELOPMENT else 6
    if split not in {Split.DEVELOPMENT, Split.MAIN} or (
        len(primary_plan.conditions) % primary_block
        or len(fixed_plan.conditions) != blocks * fixed_block
        or len(discovery_plan.conditions) != blocks * discovery_per_block
    ):
        raise ValueError(f"{split.value} lane plans do not have the registered block geometry")

    def write_status(state: str, block: int, error: str | None = None) -> None:
        _atomic_write(
            status_path,
            {
                "model": primary_plan.model,
                "split": split.value,
                "primary_slug": primary_slug,
                "fixed_slug": fixed_slug,
                "state": state,
                "completed_blocks": block,
                "total_blocks": blocks,
                "rows": {
                    "primary_capacity": len(_rows(primary_output)),
                    "fixed_capacity": len(_rows(fixed_output)),
                    "discovery": len(_rows(discovery_output)),
                },
                "transport_retries": client.retry_count,
                "error": error,
            },
        )

    write_status("running", 0)
    try:
        for index in range(blocks):
            primary_target = (index + 1) * primary_block
            fixed_target = (index + 1) * fixed_block
            capacity_jobs = (
                (
                    lambda: execute_plan(
                        primary_plan,
                        client,
                        primary_output,
                        max_conditions=primary_target,
                    )
                ),
                (
                    lambda: execute_plan(
                        fixed_plan,
                        client,
                        fixed_output,
                        max_conditions=fixed_target,
                    )
                ),
            )
            for job in (
                capacity_jobs if index % 2 == 0 else reversed(capacity_jobs)
            ):
                job()
            discovery_target = _discovery_target(
                split,
                index,
                blocks=blocks,
                total=len(discovery_plan.conditions),
            )
            execute_discovery_plan(
                discovery_plan,
                client,
                discovery_output,
                max_conditions=discovery_target,
            )
            primary_gate = infrastructure_ceiling(
                _rows(primary_output),
                _rows(discovery_output),
                planned_capacity=len(primary_plan.conditions),
                planned_discovery=len(discovery_plan.conditions),
                planned_v128=sum(
                    condition.visible_tools == 128
                    for condition in primary_plan.conditions
                ),
            )
            fixed_gate = infrastructure_ceiling(
                _rows(fixed_output),
                (),
                planned_capacity=len(fixed_plan.conditions),
                planned_discovery=0,
                planned_v128=sum(
                    condition.visible_tools == 128
                    for condition in fixed_plan.conditions
                ),
            )
            if not primary_gate["passed"] or not fixed_gate["passed"]:
                raise RuntimeError(
                    f"{split.value} infrastructure admission is unrecoverable"
                )
            write_status("running", index + 1)
    except Exception as exc:
        write_status("failed", index if "index" in locals() else 0, f"{type(exc).__name__}:{str(exc)[:300]}")
        raise
    write_status("complete", blocks)
    return json.loads(status_path.read_text(encoding="utf-8"))


def run_capacity_study_lanes(
    panel: Mapping[str, object],
    *,
    split: Split,
    protocol_text: str,
    plan_dir: Path,
    output_dir: Path,
    status_dir: Path,
    client_factory: Callable[..., CompletionClient] = ChatCompletionsClient,
) -> JSON:
    """Run paired Capacity/Discovery lanes for development or main."""

    if panel.get("frozen_for_study") is not True:
        raise ValueError("study execution requires a frozen model panel")
    if split not in {Split.DEVELOPMENT, Split.MAIN}:
        raise ValueError("paired Capacity/Discovery lanes support development or main")
    configurations = panel.get("configurations")
    if not isinstance(configurations, list):
        raise ValueError("model panel configurations must be a list")
    primary_configurations = [
        item
        for item in configurations
        if isinstance(item, dict) and item.get("study_role") == StudyRole.PRIMARY.value
    ]
    fixed_configurations = [
        item
        for item in configurations
        if isinstance(item, dict)
        and item.get("study_role") == StudyRole.ROBUSTNESS.value
    ]
    primary = {
        str(item.get("model")): item
        for item in primary_configurations
    }
    fixed = {
        str(item.get("model")): item
        for item in fixed_configurations
    }
    lane_count = _model_lane_count(panel, inferred=len(primary))
    if (
        len(primary_configurations) != lane_count
        or len(fixed_configurations) != lane_count
        or len(primary) != lane_count
        or set(primary) != set(fixed)
    ):
        raise ValueError(
            "study execution requires the declared number of distinct paired model lanes"
        )
    panel_hash = stable_hash(dict(panel))
    protocol_hash = stable_hash(protocol_text)
    output_dir.mkdir(parents=True, exist_ok=True)
    status_dir.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=lane_count) as executor:
        futures = {
            model: executor.submit(
                _run_capacity_discovery_lane,
                primary[model],
                fixed[model],
                split=split,
                plan_dir=plan_dir,
                output_dir=output_dir,
                status_dir=status_dir,
                panel_hash=panel_hash,
                protocol_hash=protocol_hash,
                client_factory=client_factory,
            )
            for model in sorted(primary)
        }
        lanes: list[JSON] = []
        errors: list[JSON] = []
        for model, future in futures.items():
            try:
                lanes.append(future.result())
            except Exception as exc:
                errors.append(
                    {
                        "model": model,
                        "error": f"{type(exc).__name__}:{str(exc)[:300]}",
                    }
                )
    result = {
        "run": f"factorized-capacity-{split.value}-lanes",
        "split": split.value,
        "passed": not errors and len(lanes) == lane_count,
        "panel_hash": panel_hash,
        "protocol_hash": protocol_hash,
        "lanes": sorted(lanes, key=lambda item: str(item["model"])),
        "errors": errors,
    }
    _atomic_write(status_dir / "summary.json", result)
    return result


def run_development_lanes(
    panel: Mapping[str, object],
    *,
    protocol_text: str,
    plan_dir: Path,
    output_dir: Path,
    status_dir: Path,
) -> JSON:
    """Backward-compatible development entry point."""

    return run_capacity_study_lanes(
        panel,
        split=Split.DEVELOPMENT,
        protocol_text=protocol_text,
        plan_dir=plan_dir,
        output_dir=output_dir,
        status_dir=status_dir,
    )


def _run_transfer_lane(
    configuration: Mapping[str, object],
    *,
    plan_dir: Path,
    output_dir: Path,
    status_dir: Path,
    panel_hash: str,
    protocol_hash: str,
) -> JSON:
    slug = str(configuration["slug"])
    plan = load_plan(plan_dir / f"{slug}-capacity.json")
    _validate_plan_binding((plan,), panel_hash=panel_hash, protocol_hash=protocol_hash)
    if plan.split is not Split.TRANSFER or plan.study_role is not StudyRole.PRIMARY:
        raise ValueError("transfer lane plan has the wrong split or study role")
    transport = plan.execution
    client = ChatCompletionsClient(**transport.client_kwargs())
    output = output_dir / f"{slug}-capacity.jsonl"
    status_path = status_dir / f"{slug}.json"

    def write_status(state: str, error: str | None = None) -> None:
        _atomic_write(
            status_path,
            {
                "model": plan.model,
                "split": Split.TRANSFER.value,
                "slug": slug,
                "state": state,
                "rows": len(_rows(output)),
                "planned_rows": len(plan.conditions),
                "transport_retries": client.retry_count,
                "error": error,
            },
        )

    write_status("running")
    try:
        execute_plan(plan, client, output)
        gate = infrastructure_ceiling(
            _rows(output),
            (),
            planned_capacity=len(plan.conditions),
            planned_discovery=0,
            planned_v128=sum(
                condition.visible_tools == 128 for condition in plan.conditions
            ),
        )
        if not gate["passed"]:
            raise RuntimeError("transfer infrastructure admission is unrecoverable")
    except Exception as exc:
        write_status("failed", f"{type(exc).__name__}:{str(exc)[:300]}")
        raise
    write_status("complete")
    return json.loads(status_path.read_text(encoding="utf-8"))


def run_transfer_lanes(
    panel: Mapping[str, object],
    *,
    protocol_text: str,
    plan_dir: Path,
    output_dir: Path,
    status_dir: Path,
) -> JSON:
    """Run all declared routed transfer plans concurrently by model."""

    if panel.get("frozen_for_study") is not True:
        raise ValueError("transfer execution requires a frozen model panel")
    configurations = panel.get("configurations")
    if not isinstance(configurations, list):
        raise ValueError("model panel configurations must be a list")
    primary = [
        item
        for item in configurations
        if isinstance(item, dict) and item.get("study_role") == StudyRole.PRIMARY.value
    ]
    lane_count = _model_lane_count(panel, inferred=len(primary))
    if len(primary) != lane_count or len(
        {str(item.get("model")) for item in primary}
    ) != lane_count:
        raise ValueError(
            "transfer execution requires the declared number of distinct primary model lanes"
        )
    panel_hash = stable_hash(dict(panel))
    protocol_hash = stable_hash(protocol_text)
    output_dir.mkdir(parents=True, exist_ok=True)
    status_dir.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=lane_count) as executor:
        futures = {
            str(item["model"]): executor.submit(
                _run_transfer_lane,
                item,
                plan_dir=plan_dir,
                output_dir=output_dir,
                status_dir=status_dir,
                panel_hash=panel_hash,
                protocol_hash=protocol_hash,
            )
            for item in primary
        }
        lanes: list[JSON] = []
        errors: list[JSON] = []
        for model, future in futures.items():
            try:
                lanes.append(future.result())
            except Exception as exc:
                errors.append(
                    {"model": model, "error": f"{type(exc).__name__}:{str(exc)[:300]}"}
                )
    result = {
        "run": "factorized-capacity-transfer-lanes",
        "split": Split.TRANSFER.value,
        "passed": not errors and len(lanes) == lane_count,
        "panel_hash": panel_hash,
        "protocol_hash": protocol_hash,
        "lanes": sorted(lanes, key=lambda item: str(item["model"])),
        "errors": errors,
    }
    _atomic_write(status_dir / "summary.json", result)
    return result
