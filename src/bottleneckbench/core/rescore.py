"""Fail-closed post-hoc rescoring of preserved ConnectorScale trajectories."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Mapping, Sequence

from bottleneckbench.core.analysis import analyze_rows
from bottleneckbench.core.evaluation import (
    Budget,
    EvidenceRef,
    TraceEvent,
    evaluate,
    trace_event_from_result,
)
from bottleneckbench.core.schema import JSON, RunStatus, canonical_json, stable_hash
from bottleneckbench.core.tasks import TaskTemplate
from bottleneckbench.core.world import (
    execute_tool,
    make_transfer_world,
    make_world,
    transfer_tools,
)


CORRECTIVE_EVALUATOR_VERSION = "connectorscale-v3.1-corrective-2"


def corrective_source_hash() -> str:
    package = Path(__file__).parent
    sources = {
        path.name: stable_hash(path.read_text(encoding="utf-8"))
        for path in sorted(package.glob("*.py"))
    }
    return stable_hash(sources)


def _evidence_ref(payload: Mapping[str, object]) -> EvidenceRef:
    return EvidenceRef(
        connector=str(payload["connector"]),
        record_id=str(payload["record_id"]),
        version=int(payload["version"]),
    )


def _recorded_event(payload: Mapping[str, object]) -> TraceEvent:
    evidence = payload.get("evidence", [])
    records = payload.get("result_records", [])
    if not isinstance(evidence, list) or not isinstance(records, list):
        raise ValueError("trace event evidence and result_records must be lists")
    return TraceEvent(
        tool=str(payload["tool"]),
        arguments=deepcopy(dict(payload.get("arguments", {}))),
        before_hash=str(payload["before_hash"]),
        after_hash=str(payload["after_hash"]),
        evidence=tuple(_evidence_ref(item) for item in evidence),
        result_records=tuple(deepcopy(item) for item in records),
        mutated=bool(payload["mutated"]),
        receipt_id=(str(payload["receipt_id"]) if payload.get("receipt_id") else None),
    )


def _replay_trace(
    task: TaskTemplate, seed: int, raw_trace: Sequence[Mapping[str, object]]
):
    transfer_names = {tool.name for tool in transfer_tools()}
    initial = (
        make_transfer_world(seed)
        if set(task.required_tools) & transfer_names
        else make_world(seed)
    )
    final = initial.clone()
    replayed: list[TraceEvent] = []
    for raw in raw_trace:
        recorded = _recorded_event(raw)
        before_hash = final.snapshot_hash
        result = execute_tool(final, recorded.tool, recorded.arguments)
        event = trace_event_from_result(
            recorded.tool,
            recorded.arguments,
            before_hash,
            final.snapshot_hash,
            result,
        )
        if canonical_json(event.to_json()) != canonical_json(recorded.to_json()):
            raise ValueError(
                f"trajectory replay mismatch for {recorded.tool}: "
                f"{recorded.before_hash[:8]}->{recorded.after_hash[:8]}"
            )
        replayed.append(event)
    return initial, final, tuple(replayed)


def rescore_row(row: Mapping[str, object], task: TaskTemplate) -> JSON:
    """Replay and rescore one preserved row without changing the original artifact."""

    condition = row.get("condition")
    if not isinstance(condition, dict):
        raise ValueError("run row lacks a condition")
    if condition.get("task_id") != task.template_id:
        raise ValueError("task does not match run-row condition")
    condition_id = row.get("condition_id")
    if not isinstance(condition_id, str) or stable_hash(condition) != condition_id:
        raise ValueError("run-row condition identity is invalid")
    plan_hash = row.get("plan_hash")
    if not isinstance(plan_hash, str) or not plan_hash:
        raise ValueError("run row lacks a plan hash")
    raw_trace = row.get("trace", [])
    raw_citations = row.get("citations", [])
    if not isinstance(raw_trace, list) or not isinstance(raw_citations, list):
        raise ValueError("run row trace and citations must be lists")
    seed = int(condition["seed"])
    initial, final, trace = _replay_trace(task, seed, raw_trace)
    citations = tuple(_evidence_ref(item) for item in raw_citations)
    usage = row.get("usage", {})
    total_tokens = int(usage.get("total_tokens", 0)) if isinstance(usage, dict) else 0
    old_verdict = row.get("verdict", {})
    run_errors = {str(error).split(":", 1)[0] for error in row.get("errors", [])}
    used_tool_calls = 9 if "budget_exceeded" in run_errors else len(trace)
    budget = Budget(8, used_tool_calls, 20000, total_tokens)
    status = str(row.get("status", ""))
    completion_valid = not (
        {"model_finish_length", "turn_limit_exceeded", "provider_failure"} & run_errors
        or status
        in {
            RunStatus.PROVIDER_FAILURE.value,
            RunStatus.CAPABILITY_UNAVAILABLE.value,
            RunStatus.HARNESS_FAILURE.value,
        }
    )
    verdict = evaluate(
        task,
        initial,
        final,
        trace,
        budget,
        answer=str(row.get("answer", "")),
        citations=citations,
        completion_valid=completion_valid,
    )
    return {
        "condition_id": condition_id,
        "condition": deepcopy(condition),
        "plan_hash": plan_hash,
        "original_row_hash": stable_hash(dict(row)),
        "task_semantic_hash": task.semantic_hash,
        "corrective_evaluator_version": CORRECTIVE_EVALUATOR_VERSION,
        "corrective_source_hash": corrective_source_hash(),
        "status": status,
        "original_verdict": deepcopy(old_verdict),
        "corrective_verdict": verdict.to_json(),
        "changed_strict_vts": bool(old_verdict.get("strict_vts")) != verdict.strict_vts
        if isinstance(old_verdict, dict)
        else verdict.strict_vts,
        "replay_valid": True,
    }


def rescore_rows(
    rows: Sequence[Mapping[str, object]], tasks: Sequence[TaskTemplate]
) -> tuple[list[JSON], JSON]:
    """Return row-level corrective verdicts and deterministic revised summaries."""

    task_by_id = {task.template_id: task for task in tasks}
    corrective = [
        rescore_row(row, task_by_id[str(row["condition"]["task_id"])])
        for row in rows
    ]
    revised_rows: list[JSON] = []
    for original, correction in zip(rows, corrective, strict=True):
        revised = deepcopy(dict(original))
        revised["verdict"] = correction["corrective_verdict"]
        revised_rows.append(revised)
    source_hash = corrective_source_hash()
    task_set_hash = stable_hash(
        sorted(task.semantic_hash for task in tasks)
    )
    input_rows_hash = stable_hash(
        [stable_hash(dict(row)) for row in rows]
    )
    summary = {
        "status": "post_hoc_automated_corrective_rescore_not_independent_human_validation",
        "corrective_evaluator_version": CORRECTIVE_EVALUATOR_VERSION,
        "corrective_source_hash": source_hash,
        "corrective_task_set_hash": task_set_hash,
        "input_rows_hash": input_rows_hash,
        "plan_hashes": sorted({str(row.get("plan_hash")) for row in rows}),
        "corrective_rows_hash": stable_hash(corrective),
        "rows": len(corrective),
        "original_strict_vts": sum(
            bool(item["original_verdict"].get("strict_vts")) for item in corrective
        ),
        "corrective_strict_vts": sum(
            bool(item["corrective_verdict"].get("strict_vts")) for item in corrective
        ),
        "changed_strict_vts": sum(bool(item["changed_strict_vts"]) for item in corrective),
        "analysis": analyze_rows(revised_rows),
    }
    return corrective, summary
