"""Natural-language task grammar and orthogonal panel construction."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Sequence

from bottleneckbench.core.schema import JSON, Track, stable_hash


@dataclass(frozen=True, slots=True)
class ComplexityAxes:
    """Independently manipulated composition coordinates."""

    c_req: int
    k_req: int
    depth: int
    j_identity: int
    j_control: int

    def row(self) -> tuple[float, ...]:
        return (
            float(self.c_req),
            float(self.k_req),
            float(self.depth),
            float(self.j_identity),
            float(self.j_control),
        )

    def to_json(self) -> JSON:
        return {
            "c_req": self.c_req,
            "k_req": self.k_req,
            "depth": self.depth,
            "j_identity": self.j_identity,
            "j_control": self.j_control,
        }


@dataclass(frozen=True, slots=True)
class TaskStep:
    """One hidden capability node in a valid reference plan family."""

    step_id: str
    connector: str
    capability: str
    tool_name: str
    depends_on: tuple[str, ...] = ()

    def to_json(self) -> JSON:
        return {
            "step_id": self.step_id,
            "connector": self.connector,
            "capability": self.capability,
            "tool_name": self.tool_name,
            "depends_on": list(self.depends_on),
        }


@dataclass(frozen=True, slots=True)
class TaskTemplate:
    """Natural-language task plus hidden executable structure."""

    template_id: str
    track: Track
    family: str
    request_template: str
    steps: tuple[TaskStep, ...]
    join_types: tuple[str, ...]
    axes: ComplexityAxes
    forbidden_tools: tuple[str, ...] = ()
    answer_terms: tuple[str, ...] = ()

    @property
    def required_tools(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(step.tool_name for step in self.steps))

    @property
    def semantic_hash(self) -> str:
        return stable_hash(self.hidden_payload())

    def hidden_payload(self) -> JSON:
        return {
            "template_id": self.template_id,
            "track": self.track.value,
            "family": self.family,
            "request_template": self.request_template,
            "steps": [step.to_json() for step in self.steps],
            "join_types": list(self.join_types),
            "axes": self.axes.to_json(),
            "forbidden_tools": list(self.forbidden_tools),
            "answer_terms": list(self.answer_terms),
        }

    def expected_mutations(self, world_seed: int) -> dict[str, JSON]:
        """Return exact task-specific mutation postconditions keyed by tool."""

        invoice_id = f"INV-{world_seed:03d}"
        expectations: dict[str, JSON] = {}
        for step in self.steps:
            if step.tool_name == "create_email_draft":
                expectations[step.tool_name] = {
                    "recipient": "finance@northstar.example",
                    "subject": f"Follow-up for {invoice_id}",
                    "body": f"Draft evidence-backed follow-up for {invoice_id}.",
                    "status": "draft",
                }
            elif step.tool_name == "create_file":
                expectations[step.tool_name] = {
                    "title": f"Evidence brief {invoice_id}",
                    "text": f"Evidence brief for {invoice_id}.",
                    "kind": "created",
                }
            elif step.tool_name == "create_calendar_event":
                expectations[step.tool_name] = {
                    "title": f"Review {invoice_id}",
                    "start": "2026-09-20T10:00:00Z",
                    "end": "2026-09-20T10:30:00Z",
                    "kind": "created",
                }
            elif step.tool_name == "update_invoice_status":
                expectations[step.tool_name] = {"status": "reviewed"}
            elif step.tool_name == "update_crm_status":
                expectations[step.tool_name] = {"status": "verified"}
            elif step.tool_name == "create_task":
                expectations[step.tool_name] = {
                    "title": f"Follow up {invoice_id}",
                    "owner": "finance@northstar.example",
                    "due_date": "2026-09-25",
                    "status": "open",
                    "kind": "created",
                }
        return expectations

    def expected_record_id(self, step: TaskStep, world_seed: int) -> str | None:
        """Return the task-relevant source target for one hidden obligation."""

        suffix = str(world_seed)
        if step.tool_name in {"search_invoices", "get_invoice", "update_invoice_status"}:
            return f"invoice-target-{suffix}"
        if step.tool_name in {"search_email", "get_email"}:
            return f"email-receipt-{suffix}"
        if step.tool_name in {"search_files", "get_file"}:
            stem = "file-agreement" if self.track is Track.DISCOVERY else "file-receipt"
            return f"{stem}-{suffix}"
        if step.tool_name in {"search_calendar", "get_calendar"}:
            stem = "cal-kickoff" if self.track is Track.DISCOVERY else "cal-promise"
            return f"{stem}-{suffix}"
        if step.tool_name in {"search_crm", "get_crm", "update_crm_status"}:
            return f"crm-northstar-{suffix}"
        if step.tool_name in {"search_tasks", "get_task"}:
            return f"task-invoice-{suffix}"
        if step.tool_name in {
            "create_email_draft",
            "create_file",
            "create_calendar_event",
            "create_task",
        }:
            return None
        raise ValueError(f"no target-record rule for {step.tool_name}")

    def _obligation(self, step: TaskStep, world_seed: int) -> str:
        invoice_id = f"INV-{world_seed:03d}"
        obligations = {
            "search_email": f"Locate current email records associated with invoice {invoice_id}",
            "get_email": f"Open exact email record email-receipt-{world_seed} and preserve its source ID",
            "create_email_draft": (
                f"Create an email draft with recipient finance@northstar.example, subject Follow-up for {invoice_id}, "
                f"body Draft evidence-backed follow-up for {invoice_id}., and status draft; do not send it"
            ),
            "search_files": f"Locate current files associated with invoice {invoice_id}",
            "get_file": f"Open exact file record file-receipt-{world_seed} and preserve its source ID",
            "create_file": (
                f"Create a file with title Evidence brief {invoice_id}, text Evidence brief for {invoice_id}., "
                "and kind created"
            ),
            "search_calendar": f"Locate current calendar records associated with invoice {invoice_id}",
            "get_calendar": f"Open exact calendar record cal-promise-{world_seed} and preserve its source ID",
            "create_calendar_event": (
                f"Create a calendar event with title Review {invoice_id}, start 2026-09-20T10:00:00Z, "
                "end 2026-09-20T10:30:00Z, and kind created"
            ),
            "search_invoices": f"Locate the current accounting record for invoice {invoice_id}",
            "get_invoice": f"Open exact accounting record invoice-target-{world_seed} and preserve its source ID",
            "update_invoice_status": (
                f"Update accounting record invoice-target-{world_seed} to status reviewed; do not issue payment"
            ),
            "search_crm": f"Locate the current CRM contact associated with invoice {invoice_id}",
            "get_crm": f"Open exact CRM record crm-northstar-{world_seed} and preserve its source ID",
            "update_crm_status": (
                f"Update CRM record crm-northstar-{world_seed} to status verified"
            ),
            "search_tasks": f"Locate the current project task associated with invoice {invoice_id}",
            "get_task": f"Open exact task record task-invoice-{world_seed} and preserve its source ID",
            "create_task": (
                f"Create a task with title Follow up {invoice_id}, owner finance@northstar.example, "
                "due date 2026-09-25, status open, and kind created"
            ),
        }
        return obligations[step.tool_name]

    def render_request(self, world_seed: int) -> str:
        request = self.request_template.format(
            invoice_id=f"INV-{world_seed:03d}",
            seed=world_seed,
        )
        if self.track is not Track.COMPOSITION:
            return request
        obligations = "\n".join(
            f"Obligation {index}: {self._obligation(step, world_seed)}."
            for index, step in enumerate(self.steps, start=1)
        )
        positions = {step.step_id: index for index, step in enumerate(self.steps, start=1)}
        ordering = "\n".join(
            f"Complete obligation {positions[parent]} before obligation {positions[step.step_id]}."
            for step in self.steps
            for parent in step.depends_on
        )
        join_text = ""
        if "identity" in self.join_types:
            join_text += "\nJoin requirement: verify matching invoice, vendor, amount, or entity identifiers across cited sources."
        if "control" in self.join_types:
            join_text += "\nJoin requirement: verify timing, freshness, permission, and precondition consistency across cited sources."
        sections = [request, "Required obligations:", obligations]
        if ordering:
            sections.extend(["Required order:", ordering])
        return "\n".join(sections) + join_text

    def public_payload(self, world_seed: int) -> JSON:
        public_id = stable_hash(
            {"semantic_hash": self.semantic_hash, "world_seed": world_seed}
        )[:20]
        return {"task_id": public_id, "request": self.render_request(world_seed)}


@dataclass(frozen=True, slots=True)
class DesignDiagnostics:
    """Construct gates for a centered standardized design."""

    rank: int
    condition_number: float
    vifs: dict[str, float]

    @property
    def passed(self) -> bool:
        return (
            self.rank == 5
            and self.condition_number <= 5.0
            and max(self.vifs.values(), default=math.inf) <= 2.0
        )

    def to_json(self) -> JSON:
        return {
            "rank": self.rank,
            "condition_number": self.condition_number,
            "vifs": dict(self.vifs),
            "passed": self.passed,
        }


@dataclass(frozen=True, slots=True)
class PanelManifest:
    """Content-addressed task-panel selection."""

    panel: str
    seed: int
    tasks: tuple[TaskTemplate, ...]

    @property
    def manifest_hash(self) -> str:
        return stable_hash(
            {
                "panel": self.panel,
                "seed": self.seed,
                "task_hashes": [task.semantic_hash for task in self.tasks],
            }
        )

    def to_json(self) -> JSON:
        return {
            "panel": self.panel,
            "seed": self.seed,
            "manifest_hash": self.manifest_hash,
            "tasks": [
                {
                    "template_id": task.template_id,
                    "track": task.track.value,
                    "semantic_hash": task.semantic_hash,
                    "axes": task.axes.to_json(),
                }
                for task in self.tasks
            ],
        }


_CONNECTOR_CAPABILITIES: dict[str, tuple[tuple[str, str], ...]] = {
    "email": (
        ("search_email", "search_email"),
        ("get_email", "get_email"),
        ("create_email_draft", "create_email_draft"),
    ),
    "files": (
        ("search_files", "search_files"),
        ("get_files", "get_file"),
        ("create_file", "create_file"),
    ),
    "calendar": (
        ("search_calendar", "search_calendar"),
        ("get_calendar", "get_calendar"),
        ("create_calendar_event", "create_calendar_event"),
    ),
    "accounting": (
        ("search_accounting", "search_invoices"),
        ("get_accounting", "get_invoice"),
        ("update_invoice_status", "update_invoice_status"),
    ),
}
_CONNECTORS = tuple(_CONNECTOR_CAPABILITIES)
_FAMILIES = ("payable", "reconciliation", "meeting_preparation", "closeout")


_S_ROWS: tuple[tuple[int, int, int, int, int], ...] = (
    (1, 2, 2, 0, 0),
    (2, 4, 1, 1, 0),
    (2, 4, 2, 0, 1),
    (2, 4, 4, 0, 0),
    (2, 6, 1, 0, 0),
    (2, 6, 2, 0, 0),
    (2, 6, 4, 1, 0),
    (4, 4, 2, 0, 0),
)


_T_ROWS: tuple[tuple[int, int, int, int, int], ...] = (
    (1, 2, 1, 0, 0),
    (2, 2, 2, 0, 0),
    (2, 4, 1, 1, 0),
    (2, 4, 2, 0, 1),
    (2, 4, 2, 1, 0),
    (2, 4, 4, 0, 1),
    (2, 4, 4, 1, 0),
    (2, 6, 1, 0, 1),
    (2, 6, 2, 0, 0),
    (2, 6, 2, 0, 1),
    (2, 6, 2, 1, 0),
    (2, 6, 4, 0, 0),
    (2, 6, 4, 0, 1),
    (2, 6, 4, 1, 0),
    (4, 4, 1, 0, 0),
    (4, 4, 1, 0, 1),
    (4, 4, 1, 1, 1),
    (4, 4, 2, 0, 0),
    (4, 4, 2, 0, 1),
    (4, 4, 2, 1, 0),
    (4, 4, 2, 1, 1),
    (4, 4, 4, 0, 0),
    (4, 4, 4, 0, 1),
    (4, 4, 4, 1, 0),
    (4, 4, 4, 1, 1),
    (4, 6, 1, 0, 0),
    (4, 6, 1, 1, 0),
    (4, 6, 2, 0, 0),
    (4, 6, 2, 0, 1),
    (4, 6, 2, 1, 0),
    (4, 6, 2, 1, 1),
    (4, 6, 4, 0, 0),
)


def _critical_depth(steps: Sequence[TaskStep]) -> int:
    depths: dict[str, int] = {}
    remaining = {step.step_id: step for step in steps}
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
            raise ValueError("task dependency graph contains a cycle or unknown parent")
    return max(depths.values(), default=0)


def compute_axes(
    steps: Sequence[TaskStep], join_types: Sequence[str]
) -> ComplexityAxes:
    """Compute task labels from hidden capabilities and graph structure."""

    unknown = set(join_types) - {"identity", "control"}
    if unknown:
        raise ValueError(f"unknown join types: {sorted(unknown)}")
    return ComplexityAxes(
        c_req=len({step.connector for step in steps}),
        k_req=len({step.capability for step in steps}),
        depth=_critical_depth(steps),
        j_identity=sum(kind == "identity" for kind in join_types),
        j_control=sum(kind == "control" for kind in join_types),
    )


def _connector_order(index: int) -> tuple[str, ...]:
    offset = index % len(_CONNECTORS)
    return _CONNECTORS[offset:] + _CONNECTORS[:offset]


def _composition_steps(index: int, c_req: int, k_req: int, depth: int) -> tuple[TaskStep, ...]:
    connectors = _connector_order(index)[:c_req]
    available: list[tuple[str, str, str]] = []
    for operation_index in range(3):
        for connector in connectors:
            capability, tool_name = _CONNECTOR_CAPABILITIES[connector][operation_index]
            available.append((connector, capability, tool_name))
    if len(available) < k_req:
        raise ValueError("requested more distinct capabilities than selected connectors expose")
    selected = available[:k_req]
    steps: list[TaskStep] = []
    for step_index, (connector, capability, tool_name) in enumerate(selected, start=1):
        depends_on = ()
        if 1 < step_index <= depth:
            depends_on = (f"s{step_index - 1}",)
        steps.append(
            TaskStep(
                step_id=f"s{step_index}",
                connector=connector,
                capability=capability,
                tool_name=tool_name,
                depends_on=depends_on,
            )
        )
    return tuple(steps)


def _composition_prompt(family: str) -> str:
    prompts = {
        "payable": (
            "Investigate overdue invoice {invoice_id} across the available business records, "
            "preserve source evidence, and prepare only the requested safe draft or workflow update. "
            "Do not send email, issue payment, or invent missing facts."
        ),
        "reconciliation": (
            "Reconcile the receipt and business records associated with invoice {invoice_id}. "
            "Resolve any identity or timing conflict from source evidence and leave only reversible draft changes."
        ),
        "meeting_preparation": (
            "Prepare an evidence-backed meeting brief for the work associated with invoice {invoice_id}, "
            "checking the relevant records and schedule before creating any requested draft artifact."
        ),
        "closeout": (
            "Verify whether work associated with invoice {invoice_id} is ready for closeout across the available systems. "
            "Use source records, respect dependencies, and avoid irreversible actions."
        ),
    }
    return prompts[family]


def _composition_template(prefix: str, index: int, row: tuple[int, int, int, int, int]) -> TaskTemplate:
    c_req, k_req, depth, j_identity, j_control = row
    family = _FAMILIES[index % len(_FAMILIES)]
    steps = _composition_steps(index, c_req, k_req, depth)
    joins = ("identity",) * j_identity + ("control",) * j_control
    axes = compute_axes(steps, joins)
    declared = ComplexityAxes(c_req, k_req, depth, j_identity, j_control)
    if axes != declared:
        raise AssertionError(f"generated axes do not match design row: {axes} != {declared}")
    return TaskTemplate(
        template_id=f"{prefix}-C{index + 1:02d}",
        track=Track.COMPOSITION,
        family=family,
        request_template=_composition_prompt(family),
        steps=steps,
        join_types=joins,
        axes=axes,
        forbidden_tools=("send_email", "issue_payment", "delete_record"),
        answer_terms=("{invoice_id}",),
    )


def _discovery_specs(count: int) -> tuple[tuple[str, str, str, str], ...]:
    base = (
        ("accounting", "search_accounting", "search_invoices", "Find the overdue invoice {invoice_id} and report its source-backed status."),
        ("email", "search_email", "search_email", "Find the receipt email associated with invoice {invoice_id}."),
        ("files", "search_files", "search_files", "Find the signed agreement in the available documents."),
        ("calendar", "search_calendar", "search_calendar", "Find the client kickoff event in the calendar."),
        ("accounting", "get_accounting", "get_invoice", "Read accounting record invoice-target-{seed} and report its exact status."),
        ("email", "get_email", "get_email", "Read email-receipt-{seed} and identify the referenced invoice."),
        ("files", "get_files", "get_file", "Read file-agreement-{seed} and report whether it is signed."),
        ("calendar", "get_calendar", "get_calendar", "Read cal-kickoff-{seed} and report its scheduled time."),
    )
    return tuple(base[index % len(base)] for index in range(count))


def _discovery_templates(prefix: str, count: int) -> tuple[TaskTemplate, ...]:
    tasks: list[TaskTemplate] = []
    for index, (connector, capability, tool_name, prompt) in enumerate(_discovery_specs(count)):
        step = TaskStep("s1", connector, capability, tool_name)
        tasks.append(
            TaskTemplate(
                template_id=f"{prefix}-D{index + 1:02d}",
                track=Track.DISCOVERY,
                family=f"discovery_{connector}_{index // 8}",
                request_template=prompt,
                steps=(step,),
                join_types=(),
                axes=compute_axes((step,), ()),
                answer_terms=(
                    ("{invoice_id}", "overdue")
                    if tool_name == "search_invoices"
                    else ("overdue",)
                    if tool_name == "get_invoice"
                    else ("{invoice_id}",)
                    if connector == "email"
                    else ("signed",)
                    if tool_name == "get_file"
                    else ("2026-09-15",)
                    if tool_name == "get_calendar"
                    else ()
                ),
            )
        )
    return tuple(tasks)


def transfer_templates() -> tuple[TaskTemplate, ...]:
    """Return 12 exploratory CRM/Tasks transfer templates."""

    specifications: tuple[tuple[tuple[str, str, str], ...], ...] = (
        (("crm", "search_crm", "search_crm"),),
        (("crm", "get_crm", "get_crm"),),
        (("tasks", "search_tasks", "search_tasks"),),
        (("tasks", "get_tasks", "get_task"),),
        (("crm", "search_crm", "search_crm"), ("tasks", "search_tasks", "search_tasks")),
        (("crm", "get_crm", "get_crm"), ("tasks", "get_tasks", "get_task")),
        (("crm", "search_crm", "search_crm"), ("crm", "get_crm", "get_crm")),
        (("tasks", "search_tasks", "search_tasks"), ("tasks", "get_tasks", "get_task")),
        (
            ("crm", "search_crm", "search_crm"),
            ("crm", "get_crm", "get_crm"),
            ("crm", "update_crm_status", "update_crm_status"),
        ),
        (
            ("tasks", "search_tasks", "search_tasks"),
            ("tasks", "get_tasks", "get_task"),
            ("tasks", "create_task", "create_task"),
        ),
        (
            ("crm", "get_crm", "get_crm"),
            ("tasks", "get_tasks", "get_task"),
            ("tasks", "create_task", "create_task"),
        ),
        (
            ("crm", "search_crm", "search_crm"),
            ("tasks", "search_tasks", "search_tasks"),
            ("crm", "get_crm", "get_crm"),
            ("tasks", "get_tasks", "get_task"),
        ),
    )
    tasks: list[TaskTemplate] = []
    for index, specification in enumerate(specifications, start=1):
        steps = tuple(
            TaskStep(
                step_id=f"s{step_index}",
                connector=connector,
                capability=capability,
                tool_name=tool_name,
                depends_on=(f"s{step_index - 1}",) if step_index > 1 else (),
            )
            for step_index, (connector, capability, tool_name) in enumerate(
                specification, start=1
            )
        )
        joins = ("identity",) if len({step.connector for step in steps}) > 1 else ()
        tasks.append(
            TaskTemplate(
                template_id=f"U-{index:02d}",
                track=Track.COMPOSITION,
                family="crm_transfer" if index <= 6 else "tasks_transfer",
                request_template=(
                    "Complete the CRM and project-task workflow for invoice {invoice_id}. "
                    "Use current source evidence and make only the explicitly requested reversible changes."
                ),
                steps=steps,
                join_types=joins,
                axes=compute_axes(steps, joins),
                forbidden_tools=("send_email", "issue_payment", "delete_record"),
                answer_terms=("{invoice_id}",),
            )
        )
    return tuple(tasks)


def development_templates() -> tuple[TaskTemplate, ...]:
    """Return the 16-template source-visible S panel."""

    discovery = _discovery_templates("S", 8)
    composition = tuple(
        _composition_template("S", index, row) for index, row in enumerate(_S_ROWS)
    )
    return discovery + composition


def candidate_templates() -> tuple[TaskTemplate, ...]:
    """Return the 48-template response-sealed candidate panel."""

    discovery = _discovery_templates("T", 16)
    composition = tuple(
        _composition_template("T", index, row) for index, row in enumerate(_T_ROWS)
    )
    return discovery + composition


def sample_panel(panel: str, seed: int) -> PanelManifest:
    """Bind a frozen panel selection to an explicit selector seed."""

    normalized = panel.upper()
    if normalized == "S":
        tasks = development_templates()
    elif normalized == "T":
        tasks = candidate_templates()
    elif normalized == "U":
        tasks = transfer_templates()
    else:
        raise ValueError(f"unknown panel: {panel}")
    return PanelManifest(panel=normalized, seed=seed, tasks=tasks)


def _matrix_rank(matrix: list[list[float]], tolerance: float = 1e-10) -> int:
    work = [row[:] for row in matrix]
    rows = len(work)
    columns = len(work[0]) if work else 0
    rank = 0
    for column in range(columns):
        pivot = max(range(rank, rows), key=lambda row: abs(work[row][column]), default=rank)
        if rows == 0 or abs(work[pivot][column]) <= tolerance:
            continue
        work[rank], work[pivot] = work[pivot], work[rank]
        divisor = work[rank][column]
        work[rank] = [value / divisor for value in work[rank]]
        for row in range(rows):
            if row == rank:
                continue
            factor = work[row][column]
            work[row] = [
                value - factor * pivot_value
                for value, pivot_value in zip(work[row], work[rank], strict=True)
            ]
        rank += 1
        if rank == rows:
            break
    return rank


def _transpose(matrix: Sequence[Sequence[float]]) -> list[list[float]]:
    return [list(column) for column in zip(*matrix, strict=True)]


def _matmul(left: Sequence[Sequence[float]], right: Sequence[Sequence[float]]) -> list[list[float]]:
    right_columns = _transpose(right)
    return [
        [sum(a * b for a, b in zip(row, column, strict=True)) for column in right_columns]
        for row in left
    ]


def _inverse(matrix: Sequence[Sequence[float]]) -> list[list[float]]:
    size = len(matrix)
    work = [list(row) + [float(i == j) for j in range(size)] for i, row in enumerate(matrix)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(work[row][column]))
        if abs(work[pivot][column]) <= 1e-12:
            raise ValueError("singular matrix")
        work[column], work[pivot] = work[pivot], work[column]
        divisor = work[column][column]
        work[column] = [value / divisor for value in work[column]]
        for row in range(size):
            if row == column:
                continue
            factor = work[row][column]
            work[row] = [
                value - factor * pivot_value
                for value, pivot_value in zip(work[row], work[column], strict=True)
            ]
    return [row[size:] for row in work]


def _jacobi_eigenvalues(matrix: Sequence[Sequence[float]]) -> list[float]:
    values = [list(row) for row in matrix]
    size = len(values)
    for _ in range(100 * max(size, 1)):
        off_diagonal = [
            (abs(values[row][column]), row, column)
            for row in range(size)
            for column in range(row + 1, size)
        ]
        maximum, p, q = max(off_diagonal, default=(0.0, 0, 0))
        if maximum < 1e-12:
            break
        angle = 0.5 * math.atan2(2.0 * values[p][q], values[q][q] - values[p][p])
        cosine = math.cos(angle)
        sine = math.sin(angle)
        for row in range(size):
            if row in (p, q):
                continue
            old_p = values[row][p]
            old_q = values[row][q]
            values[row][p] = values[p][row] = cosine * old_p - sine * old_q
            values[row][q] = values[q][row] = sine * old_p + cosine * old_q
        old_pp = values[p][p]
        old_qq = values[q][q]
        old_pq = values[p][q]
        values[p][p] = cosine * cosine * old_pp - 2 * sine * cosine * old_pq + sine * sine * old_qq
        values[q][q] = sine * sine * old_pp + 2 * sine * cosine * old_pq + cosine * cosine * old_qq
        values[p][q] = values[q][p] = 0.0
    return [values[index][index] for index in range(size)]


def design_diagnostics(tasks: Sequence[TaskTemplate]) -> DesignDiagnostics:
    """Compute rank, standardized condition number, and per-axis VIF."""

    rows = [task.axes.row() for task in tasks]
    if not rows:
        raise ValueError("design requires at least one task")
    columns = _transpose(rows)
    means = [sum(column) / len(column) for column in columns]
    standard_deviations = [
        math.sqrt(sum((value - mean) ** 2 for value in column) / len(column))
        for column, mean in zip(columns, means, strict=True)
    ]
    if any(value <= 1e-12 for value in standard_deviations):
        raise ValueError("design contains a constant axis")
    standardized = [
        [
            (value - mean) / deviation
            for value, mean, deviation in zip(row, means, standard_deviations, strict=True)
        ]
        for row in rows
    ]
    rank = _matrix_rank(standardized)
    cross_product = _matmul(_transpose(standardized), standardized)
    eigenvalues = [value for value in _jacobi_eigenvalues(cross_product) if value > 1e-10]
    condition_number = math.inf
    if len(eigenvalues) == len(columns):
        condition_number = math.sqrt(max(eigenvalues) / min(eigenvalues))
    correlation = [
        [value / len(rows) for value in row]
        for row in cross_product
    ]
    inverse_correlation = _inverse(correlation)
    names = ("c_req", "k_req", "depth", "j_identity", "j_control")
    vifs = {name: inverse_correlation[index][index] for index, name in enumerate(names)}
    return DesignDiagnostics(rank=rank, condition_number=condition_number, vifs=vifs)
