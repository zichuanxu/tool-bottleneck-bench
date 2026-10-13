"""Controlled geometric workflow intervention for capacity-frontier estimation.

The core benchmark intentionally stops at three evidence records so that its
original three-model block remains immutable.  This module adds a separate,
prospective intervention. Within a semantic family it holds the user request,
three facts in one evidence record, exact answer, terminal-state contract,
source route, visible tool menu, and observation representation fixed. It
changes only the number of mandatory, fact-free locator records on the linear
access path to that evidence. Geometric anchors span 2--30 certified calls so
heterogeneous models can be estimated with explicit endpoint censoring.
"""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass, replace
import json
from typing import Mapping, Sequence

from bottleneckbench.design import (
    MinimalProgramCertificate,
    ProgramStep,
    RecordNode,
    SemanticFamily,
    Split,
    TwinTask,
    WorkflowLoad,
    build_families,
    build_world,
    compile_task,
    visible_menu,
)
from bottleneckbench.runtime import (
    Budget,
    CapacityVerdict,
    _trace_event,
    evaluate_episode,
    execute_capacity_tool,
)
from bottleneckbench.core.schema import JSON, canonical_json, stable_hash


WORKFLOW_BOUNDARY_STUDY = "workflow_capacity_frontier"
WORKFLOW_BOUNDARY_LOCATOR_LEVELS = (0, 2, 6, 14)
WORKFLOW_BOUNDARY_EVIDENCE_NODES = 1
WORKFLOW_BOUNDARY_CERTIFIED_CALL_LEVELS = tuple(
    2 * (level + WORKFLOW_BOUNDARY_EVIDENCE_NODES)
    for level in WORKFLOW_BOUNDARY_LOCATOR_LEVELS
)
WORKFLOW_BOUNDARY_VISIBLE_TOOLS = 8
WORKFLOW_BOUNDARY_INTENTS = (
    "escalation_assessment",
    "amount_reconciliation",
    "meeting_readiness",
    "response_sla_assessment",
    "document_consistency",
    "schedule_conflict_assessment",
)


@dataclass(frozen=True, slots=True)
class WorkflowBoundaryTask:
    """A runtime-compatible task with an explicit locator-depth coordinate."""

    family: SemanticFamily
    locator_nodes: int
    nodes: tuple[RecordNode, ...]
    program: tuple[ProgramStep, ...]
    action: object | None
    request: str
    base_tool_names: tuple[str, ...]
    workflow: WorkflowLoad = WorkflowLoad.HIGH

    def __post_init__(self) -> None:
        if self.locator_nodes not in WORKFLOW_BOUNDARY_LOCATOR_LEVELS:
            raise ValueError("unregistered workflow-boundary locator level")
        if len(self.nodes) != self.locator_nodes + WORKFLOW_BOUNDARY_EVIDENCE_NODES:
            raise ValueError("workflow-boundary node count is inconsistent")

    @property
    def task_id(self) -> str:
        return f"{self.family.family_id}-workflow-frontier-l{self.locator_nodes}"

    @property
    def task_hash(self) -> str:
        return stable_hash(self.hidden_payload())

    @property
    def semantic_signature(self) -> str:
        return self.family.semantic_signature

    @property
    def expected_fact_map(self) -> dict[str, object]:
        return self.family.fact_map

    @property
    def required_tool_names(self) -> tuple[str, ...]:
        return self.base_tool_names

    @property
    def total_nodes(self) -> int:
        return len(self.nodes)

    @property
    def certified_call_target(self) -> int:
        return len(self.program)

    def public_payload(self) -> JSON:
        return {
            "task_id": stable_hash(
                {"family": self.family.semantic_signature, "request": self.request}
            )[:20],
            "request": self.request,
        }

    def hidden_payload(self) -> JSON:
        return {
            "task_id": self.task_id,
            "study": WORKFLOW_BOUNDARY_STUDY,
            "semantic_signature": self.semantic_signature,
            "locator_nodes": self.locator_nodes,
            "evidence_nodes": WORKFLOW_BOUNDARY_EVIDENCE_NODES,
            "nodes": [
                {
                    "connector": node.connector,
                    "record_id": node.record_id,
                    "lookup_key": node.lookup_key,
                    "facts": list(node.facts),
                    "next_queries": list(node.next_queries),
                }
                for node in self.nodes
            ],
            "program": [
                {
                    "step_id": step.step_id,
                    "tool_name": step.tool_name,
                    "connector": step.connector,
                    "arguments": step.argument_map,
                    "depends_on": list(step.depends_on),
                    "produces_facts": list(step.produces_facts),
                }
                for step in self.program
            ],
            "action": self.action.to_json() if self.action else None,
            "base_tool_names": list(self.base_tool_names),
        }


def _boundary_record_id(
    family: SemanticFamily, *, kind: str, index: int, connector: str
) -> str:
    return "src-" + stable_hash(
        {
            "study": "workflow-capacity-frontier-v1",
            "family": family.family_id,
            "kind": kind,
            "index": index,
            "connector": connector,
        }
    )[:16]


def _boundary_query(family: SemanticFamily, *, kind: str, index: int) -> str:
    return "lookup-" + stable_hash(
        {
            "study": "workflow-capacity-frontier-v1",
            "family": family.family_id,
            "kind": kind,
            "index": index,
        }
    )[:18]


def _tool_names_by_connector(
    base: TwinTask,
) -> tuple[dict[str, str], dict[str, str]]:
    searches: dict[str, str] = {}
    gets: dict[str, str] = {}
    for step in base.program:
        if step.step_id.startswith("search-"):
            searches.setdefault(step.connector, step.tool_name)
        elif step.step_id.startswith("get-"):
            gets.setdefault(step.connector, step.tool_name)
    if set(base.family.connectors) - set(searches) or set(base.family.connectors) - set(gets):
        raise AssertionError("base task does not expose every family connector")
    return searches, gets


def compile_workflow_boundary_task(
    family: SemanticFamily, locator_nodes: int
) -> WorkflowBoundaryTask:
    """Compile a linear task while changing only mandatory locator depth."""

    if locator_nodes not in WORKFLOW_BOUNDARY_LOCATOR_LEVELS:
        raise ValueError("unregistered workflow-boundary locator level")
    # Branching is a backend-layout property, not user semantics.  The boundary
    # block uses one linear path at every level so depth is the sole intervention.
    linear_family = replace(family, branching=False)
    base = compile_task(linear_family, WorkflowLoad.HIGH)
    search_names, get_names = _tool_names_by_connector(base)

    # All nodes use the same routed connector and operation pair. This prevents
    # connector switching, tool diversity, and evidence fragmentation from
    # changing with horizon. Only the number of mandatory fact-free handoffs
    # changes. The final record contains the same complete fact bundle at every
    # level, including the two-call baseline.
    evidence_connector = linear_family.connectors[0]
    node_specs: list[tuple[str, int, str, tuple[str, ...]]] = []
    for index in range(locator_nodes):
        node_specs.append(("locator", index, evidence_connector, ()))
    node_specs.append(
        ("evidence", 0, evidence_connector, tuple(linear_family.fact_map))
    )

    lookup_keys: list[str] = [linear_family.case_id]
    for kind, index, _, _ in node_specs[1:]:
        lookup_keys.append(_boundary_query(linear_family, kind=kind, index=index))

    nodes: list[RecordNode] = []
    for position, (kind, index, connector, facts) in enumerate(node_specs):
        next_queries = (
            (lookup_keys[position + 1],)
            if position + 1 < len(node_specs)
            else ()
        )
        nodes.append(
            RecordNode(
                connector=connector,
                record_id=_boundary_record_id(
                    linear_family, kind=kind, index=index, connector=connector
                ),
                lookup_key=lookup_keys[position],
                facts=facts,
                next_queries=next_queries,
            )
        )

    steps: list[ProgramStep] = []
    for position, node in enumerate(nodes):
        number = position + 1
        search_id = f"search-{number}"
        get_id = f"get-{number}"
        steps.append(
            ProgramStep(
                step_id=search_id,
                tool_name=search_names[node.connector],
                connector=node.connector,
                arguments=(("query", node.lookup_key),),
                depends_on=() if position == 0 else (f"get-{position}",),
            )
        )
        steps.append(
            ProgramStep(
                step_id=get_id,
                tool_name=get_names[node.connector],
                connector=node.connector,
                arguments=(("record_id", node.record_id),),
                depends_on=(search_id,),
                produces_facts=node.facts,
            )
        )

    if base.action is not None:
        base_action_step = next(step for step in base.program if step.step_id == "action")
        steps.append(
            replace(
                base_action_step,
                depends_on=(f"get-{len(nodes)}",),
            )
        )
    return WorkflowBoundaryTask(
        family=linear_family,
        locator_nodes=locator_nodes,
        nodes=tuple(nodes),
        program=tuple(steps),
        action=base.action,
        request=base.request,
        base_tool_names=base.base_tool_names,
    )


@dataclass(frozen=True, slots=True)
class _PlannerState:
    queries: frozenset[str]
    located_records: frozenset[str]
    fetched_records: frozenset[str]
    facts: frozenset[str]
    action_done: bool


def _critical_depth(program: Sequence[ProgramStep]) -> int:
    depths: dict[str, int] = {}
    remaining = {step.step_id: step for step in program}
    while remaining:
        progressed = False
        for step_id, step in tuple(remaining.items()):
            if all(parent in depths for parent in step.depends_on):
                depths[step_id] = 1 + max(
                    (depths[parent] for parent in step.depends_on), default=0
                )
                del remaining[step_id]
                progressed = True
        if not progressed:
            raise ValueError("workflow-boundary program has an invalid dependency graph")
    return max(depths.values(), default=0)


def certify_workflow_boundary_program(
    task: WorkflowBoundaryTask,
) -> MinimalProgramCertificate:
    """Independently find the shortest plan, including fact-free locator gets."""

    initial = _PlannerState(
        frozenset({task.family.case_id}), frozenset(), frozenset(), frozenset(), False
    )
    queue = deque([(initial, 0)])
    distances = {initial: 0}
    ways = {initial: 1}
    minimum: int | None = None
    minimal_ways = 0
    required_facts = frozenset(task.expected_fact_map)

    def goal(state: _PlannerState) -> bool:
        return required_facts <= state.facts and (task.action is None or state.action_done)

    while queue:
        state, distance = queue.popleft()
        if minimum is not None and distance > minimum:
            break
        if goal(state):
            minimum = distance
            minimal_ways += ways[state]
            continue
        successors: list[_PlannerState] = []
        for node in task.nodes:
            if node.lookup_key in state.queries and node.record_id not in state.located_records:
                successors.append(
                    replace(
                        state,
                        located_records=state.located_records | {node.record_id},
                    )
                )
            if node.record_id in state.located_records and node.record_id not in state.fetched_records:
                successors.append(
                    replace(
                        state,
                        queries=state.queries | frozenset(node.next_queries),
                        fetched_records=state.fetched_records | {node.record_id},
                        facts=state.facts | frozenset(node.facts),
                    )
                )
        if task.action is not None and required_facts <= state.facts and not state.action_done:
            successors.append(replace(state, action_done=True))
        for successor in successors:
            next_distance = distance + 1
            known = distances.get(successor)
            if known is None:
                distances[successor] = next_distance
                ways[successor] = ways[state]
                queue.append((successor, next_distance))
            elif known == next_distance:
                ways[successor] += ways[state]
    return MinimalProgramCertificate(
        minimum_calls=-1 if minimum is None else minimum,
        declared_calls=len(task.program),
        minimal_plan_count=minimal_ways,
        critical_depth=_critical_depth(task.program),
    )


def workflow_boundary_families(split: Split | str) -> tuple[SemanticFamily, ...]:
    """Return the registered read-only, outcome-balanced boundary panel."""

    normalized = Split(split)
    families = tuple(
        family
        for family in build_families(normalized)
        if family.intent in WORKFLOW_BOUNDARY_INTENTS
        and (
            normalized is not Split.MAIN
            or family.outcome_variant in {0, 1}
        )
    )
    expected = {Split.DEVELOPMENT: 6, Split.MAIN: 12, Split.TRANSFER: 6}[normalized]
    if len(families) != expected:
        raise AssertionError("workflow-boundary semantic panel is incomplete")
    if any(family.action_name is not None for family in families):
        raise AssertionError("workflow-boundary panel must remain read-only")
    if normalized is Split.MAIN:
        for intent in WORKFLOW_BOUNDARY_INTENTS:
            selected = [family for family in families if family.intent == intent]
            if {family.positive_outcome for family in selected} != {False, True}:
                raise AssertionError("workflow-boundary main outcomes are not balanced")
    return families


def workflow_boundary_tasks(
    split: Split | str,
) -> tuple[WorkflowBoundaryTask, ...]:
    return tuple(
        compile_workflow_boundary_task(family, level)
        for family in workflow_boundary_families(split)
        for level in WORKFLOW_BOUNDARY_LOCATOR_LEVELS
    )


def _reference_answer(task: WorkflowBoundaryTask) -> str:
    return canonical_json(
        {
            "decision": task.family.decision,
            "facts": task.expected_fact_map,
            "sources": [node.record_id for node in task.nodes if node.facts],
        }
    )


def reference_workflow_boundary_execution(
    task: WorkflowBoundaryTask,
) -> CapacityVerdict:
    """Execute the declared program without a model and apply the exact scorer."""

    initial = build_world(task)  # type: ignore[arg-type]
    world = initial.clone()
    menu = {tool.name: tool for tool in visible_menu(task, 8)}  # type: ignore[arg-type]
    trace = []
    for step in task.program:
        tool = menu[step.tool_name]
        before_hash = world.snapshot_hash
        result = execute_capacity_tool(
            world, task, tool, step.argument_map  # type: ignore[arg-type]
        )
        trace.append(
            _trace_event(tool, step.argument_map, before_hash, world.snapshot_hash, result)
        )
    verdict, _ = evaluate_episode(
        task,  # type: ignore[arg-type]
        initial,
        world,
        trace,
        Budget(len(task.program), len(trace), 1_000_000, 1),
        answer=_reference_answer(task),
    )
    return verdict


def _check(name: str, passed: bool, **details: object) -> JSON:
    return {"name": name, "passed": bool(passed), **deepcopy(details)}


def workflow_boundary_static_audit(split: Split | str) -> JSON:
    """Audit semantic isolation, exact scoring, and absence of graph shortcuts."""

    normalized = Split(split)
    tasks = workflow_boundary_tasks(normalized)
    by_family: dict[str, list[WorkflowBoundaryTask]] = {}
    for task in tasks:
        by_family.setdefault(task.family.family_id, []).append(task)

    certificates = {
        task.task_id: certify_workflow_boundary_program(task) for task in tasks
    }
    reference_verdicts = {
        task.task_id: reference_workflow_boundary_execution(task) for task in tasks
    }
    semantic_isolation = all(
        len({task.semantic_signature for task in family_tasks}) == 1
        and len({task.request for task in family_tasks}) == 1
        and len({task.base_tool_names for task in family_tasks}) == 1
        and len(
            {
                canonical_json(
                    task.action.to_json(logical_target=True) if task.action else None
                )
                for task in family_tasks
            }
        )
        == 1
        and len(
            {
                canonical_json(
                    [
                        {
                            "connector": node.connector,
                            "record_id": node.record_id,
                            "facts": list(node.facts),
                        }
                        for node in task.nodes
                        if node.facts
                    ]
                )
                for task in family_tasks
            }
        )
        == 1
        for family_tasks in by_family.values()
    )
    menu_isolation = all(
        len(
            {
                canonical_json([tool.to_openai() for tool in visible_menu(task, 8)])
                for task in family_tasks
            }
        )
        == 1
        for family_tasks in by_family.values()
    )
    linear_graph = all(
        all(
            node.next_queries
            == ((task.nodes[index + 1].lookup_key,) if index + 1 < len(task.nodes) else ())
            for index, node in enumerate(task.nodes)
        )
        and all(
            len(node.facts)
            == (0 if index < task.locator_nodes else len(task.expected_fact_map))
            for index, node in enumerate(task.nodes)
        )
        and sum(bool(node.facts) for node in task.nodes)
        == WORKFLOW_BOUNDARY_EVIDENCE_NODES
        and len({node.connector for node in task.nodes}) == 1
        for task in tasks
    )
    gold_isolated = all(
        all(node.record_id not in task.request for node in task.nodes)
        and all(
            query not in task.request
            for node in task.nodes
            for query in node.next_queries
        )
        and all(step.tool_name not in task.request for step in task.program)
        for task in tasks
    )
    checks = [
        _check(
            "registered_factor_support",
            len(tasks)
            == len(workflow_boundary_families(normalized))
            * len(WORKFLOW_BOUNDARY_LOCATOR_LEVELS)
            and all(
                {task.locator_nodes for task in family_tasks}
                == set(WORKFLOW_BOUNDARY_LOCATOR_LEVELS)
                for family_tasks in by_family.values()
            ),
            family_count=len(by_family),
            task_count=len(tasks),
            locator_levels=list(WORKFLOW_BOUNDARY_LOCATOR_LEVELS),
            intents=list(WORKFLOW_BOUNDARY_INTENTS),
        ),
        _check("semantic_and_evidence_isolation", semantic_isolation),
        _check("visible_menu_isolation", menu_isolation),
        _check("linear_single_route_locator_graph", linear_graph),
        _check(
            "minimal_program_certification",
            all(certificate.passed for certificate in certificates.values()),
            call_targets=sorted(
                {certificate.minimum_calls for certificate in certificates.values()}
            ),
        ),
        _check(
            "exact_reference_execution",
            all(verdict.verified_success for verdict in reference_verdicts.values()),
        ),
        _check("gold_isolation", gold_isolated),
        _check(
            "deterministic_recompilation",
            [task.task_hash for task in tasks]
            == [task.task_hash for task in workflow_boundary_tasks(normalized)],
        ),
    ]
    return {
        "audit": "workflow-capacity-frontier-static-construct-v1",
        "split": normalized.value,
        "design": {
            "locator_levels": list(WORKFLOW_BOUNDARY_LOCATOR_LEVELS),
            "certified_call_levels": list(WORKFLOW_BOUNDARY_CERTIFIED_CALL_LEVELS),
            "evidence_nodes": WORKFLOW_BOUNDARY_EVIDENCE_NODES,
            "evidence_fields": 3,
            "visible_tools": WORKFLOW_BOUNDARY_VISIBLE_TOOLS,
            "representation": "raw",
            "graph": "linear",
            "intent_panel": list(WORKFLOW_BOUNDARY_INTENTS),
            "mutation_policy": "read_only",
        },
        "checks": checks,
        "passed": all(check["passed"] for check in checks),
        "task_manifest_hash": stable_hash(
            [task.hidden_payload() for task in tasks]
        ),
    }


def workflow_boundary_manifest(split: Split | str) -> str:
    return json.dumps(
        [task.hidden_payload() for task in workflow_boundary_tasks(split)],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
