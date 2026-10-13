"""Freeze and execute a small, official-grader ToolSandbox criterion set."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import random
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
import traceback
from types import SimpleNamespace
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bottleneckbench.calibration import _client_for_configuration
from bottleneckbench.core.agnes import AgnesError, ModelRequest, ModelResponse, ToolCall
from bottleneckbench.core.experiments import ExperimentStore
from bottleneckbench.core.schema import JSON, canonical_json, stable_hash
from bottleneckbench.request_scope import completion_request_scope


UPSTREAM = ROOT / "vendor/tool-sandbox"
UPSTREAM_REPOSITORY = "https://github.com/apple/ToolSandbox"
UPSTREAM_COMMIT = "165848b9a78cead7ca7fe7c89c688b58e6501219"
SELECTION_SEED = 20260813
SCENARIO_STRATA: dict[str, tuple[str, ...]] = {
    "state_dependency": (
        "turn_on_wifi_low_battery_mode",
        "send_message_with_contact_content_cellular_off",
        "turn_on_location_low_battery_mode",
    ),
    "canonicalization": (
        "remove_contact_by_phone",
        "add_reminder_content_and_weekday_delta_and_time",
        "search_reminder_with_creation_recency_yesterday",
    ),
    "multi_tool": (
        "search_message_with_recency_latest",
        "search_message_with_recency_oldest",
        "update_contact_relationship_with_relationship",
    ),
    "insufficient_information": (
        "modify_contact_with_message_recency_insufficient_information",
        "remove_contact_by_phone_no_remove_contact_insufficient_information",
        "search_reminder_with_creation_recency_yesterday_insufficient_information",
    ),
}


def _object(path: Path) -> JSON:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree_hash(path: Path) -> str:
    files = [item for item in path.rglob("*") if item.is_file() and "__pycache__" not in item.parts]
    return stable_hash(
        [
            {"path": str(item.relative_to(path)), "sha256": _file_sha256(item)}
            for item in sorted(files)
        ]
    )


def _scenario_catalog() -> Mapping[str, Any]:
    from tool_sandbox.common.tool_discovery import ToolBackend
    from tool_sandbox.scenarios import named_scenarios

    random.seed(SELECTION_SEED)
    return named_scenarios(ToolBackend.DEFAULT)


def build_selection_manifest() -> JSON:
    from inspect import getmodule

    from tool_sandbox.common.execution_context import DatabaseNamespace
    from tool_sandbox.common.tool_discovery import ToolBackend, get_all_tools

    catalog = _scenario_catalog()
    all_tools = get_all_tools(ToolBackend.DEFAULT)
    selected: list[JSON] = []
    identifiers = [name for names in SCENARIO_STRATA.values() for name in names]
    if len(identifiers) != 12 or len(set(identifiers)) != 12:
        raise ValueError("criterion selection must contain 12 unique scenarios")
    for stratum, names in SCENARIO_STRATA.items():
        if len(names) != 3:
            raise ValueError("each external criterion stratum must contain three scenarios")
        for name in names:
            scenario = catalog[name]
            categories = [str(value) for value in scenario.categories]
            tools = list(scenario.starting_context.tool_allow_list or [])
            modules = sorted(
                {
                    str(getmodule(all_tools[tool]).__name__)
                    for tool in tools
                    if tool in all_tools and getmodule(all_tools[tool]) is not None
                }
            )
            rows = scenario.starting_context.get_database(
                DatabaseNamespace.SANDBOX,
                get_all_history_snapshots=True,
                drop_sandbox_message_index=False,
            ).to_dicts()
            prompts = [
                str(row["content"])
                for row in rows
                if str(row["sender"]) == "USER"
                and str(row["recipient"]) == "AGENT"
                and (
                    row.get("visible_to") is None
                    or any(str(role) == "AGENT" for role in row["visible_to"])
                )
            ]
            if len(prompts) != 1:
                raise ValueError(f"criterion scenario is not static single-request: {name}")
            if "NO_DISTRACTION_TOOLS" not in categories or "MULTIPLE_USER_TURN" in categories:
                raise ValueError(f"criterion scenario violates static selection policy: {name}")
            if any(module.endswith("rapid_api_search_tools") for module in modules):
                raise ValueError(f"criterion scenario requires an external data API: {name}")
            required_category = {
                "state_dependency": "STATE_DEPENDENCY",
                "canonicalization": "CANONICALIZATION",
                "multi_tool": "MULTIPLE_TOOL_CALL",
                "insufficient_information": "INSUFFICIENT_INFORMATION",
            }[stratum]
            if required_category not in categories:
                raise ValueError(f"criterion scenario lacks its registered stratum: {name}")
            selected.append(
                {
                    "scenario_id": name,
                    "stratum": stratum,
                    "categories": categories,
                    "tool_allow_list": tools,
                    "tool_modules": modules,
                    "initial_user_request": prompts[0],
                    "max_messages": int(scenario.max_messages),
                    "milestones": len(scenario.evaluation.milestone_matcher.milestones),
                    "minefields": len(scenario.evaluation.minefield_matcher.milestones),
                }
            )
    return {
        "schema": "toolsandbox-criterion-selection-v1",
        "selection_seed": SELECTION_SEED,
        "selection_basis": "static upstream identifiers and task metadata only; no model outcomes",
        "upstream_repository": UPSTREAM_REPOSITORY,
        "upstream_commit": UPSTREAM_COMMIT,
        "upstream_tree_hash": _tree_hash(UPSTREAM),
        "official_environment_and_evaluator_unchanged": True,
        "deterministic_terminal_user": "calls the official end_conversation tool after the first natural-language agent response and supplies no new task information",
        "public_task_contamination_risk": "present; external criterion only, not a held-out claim",
        "scenarios": selected,
        "checks": {
            "unique_scenarios": True,
            "three_per_stratum": True,
            "static_single_request": True,
            "no_external_data_api": True,
            "official_grader": True,
        },
    }


def _primary_configurations(panel: Mapping[str, object]) -> list[JSON]:
    configurations = panel.get("configurations")
    if not isinstance(configurations, list):
        raise ValueError("sentinel panel is missing configurations")
    selected = [dict(item) for item in configurations if isinstance(item, dict) and item.get("study_role") == "primary"]
    models = [str(item["model"]) for item in selected]
    if len(models) != 15 or len(set(models)) != 15:
        raise ValueError("external criterion requires one primary transport for each of 15 models")
    return sorted(selected, key=lambda item: str(item["model"]))


def build_plan(
    *,
    frozen_at: str,
    protocol_path: Path,
    panel_path: Path,
    selection_path: Path,
) -> JSON:
    protocol_path = protocol_path.resolve()
    panel_path = panel_path.resolve()
    selection_path = selection_path.resolve()
    panel = _object(panel_path)
    manifest = build_selection_manifest()
    configurations = _primary_configurations(panel)
    conditions: list[JSON] = []
    for configuration in configurations:
        model = str(configuration["model"])
        for scenario in manifest["scenarios"]:
            identity = {
                "model": model,
                "scenario_id": scenario["scenario_id"],
                "upstream_commit": UPSTREAM_COMMIT,
            }
            conditions.append(
                {
                    **identity,
                    "condition_id": stable_hash(identity),
                    "stratum": scenario["stratum"],
                    "inference_seed": int(stable_hash({**identity, "sampling": 1})[:8], 16),
                }
            )
    payload: JSON = {
        "schema": "toolsandbox-external-criterion-plan-v1",
        "frozen_at": frozen_at,
        "confirmatory_weight": 0,
        "scientific_role": "independently authored ecological criterion and model-ranking sensitivity",
        "adapter_path": str(Path(__file__).relative_to(ROOT)),
        "adapter_sha256": _file_sha256(Path(__file__)),
        "upstream": {
            "repository": UPSTREAM_REPOSITORY,
            "commit": UPSTREAM_COMMIT,
            "snapshot_path": str(UPSTREAM.relative_to(ROOT)),
            "tree_hash": manifest["upstream_tree_hash"],
        },
        "selection_manifest_path": str(selection_path.relative_to(ROOT)),
        "selection_manifest_hash": stable_hash(manifest),
        "protocol_path": str(protocol_path.relative_to(ROOT)),
        "protocol_sha256": _file_sha256(protocol_path),
        "model_panel_path": str(panel_path.relative_to(ROOT)),
        "model_panel_hash": stable_hash(panel),
        "execution_policy": dict(panel["execution_policy"]),
        "configurations": configurations,
        "conditions": conditions,
        "scoring": "official ToolSandbox milestone and minefield evaluator; no LLM judge",
        "sampling": "one request trajectory per model-scenario condition; temperature 0 and frozen inference seed when supported",
    }
    payload["plan_hash"] = stable_hash(payload)
    if len(conditions) != 180 or len({item["condition_id"] for item in conditions}) != 180:
        raise ValueError("external criterion plan must contain 180 unique conditions")
    return payload


def validate_plan(plan: Mapping[str, object]) -> None:
    unhashed = dict(plan)
    plan_hash = unhashed.pop("plan_hash", None)
    if plan_hash != stable_hash(unhashed):
        raise ValueError("external criterion plan hash mismatch")
    if plan.get("adapter_sha256") != _file_sha256(Path(__file__)):
        raise ValueError("external criterion adapter changed after freeze")
    upstream = plan.get("upstream")
    if not isinstance(upstream, dict) or upstream.get("tree_hash") != _tree_hash(UPSTREAM):
        raise ValueError("ToolSandbox source snapshot changed after freeze")
    selection_path = ROOT / str(plan["selection_manifest_path"])
    manifest = build_selection_manifest()
    if _object(selection_path) != manifest or plan.get("selection_manifest_hash") != stable_hash(manifest):
        raise ValueError("external criterion selection changed after freeze")
    protocol_path = ROOT / str(plan["protocol_path"])
    panel_path = ROOT / str(plan["model_panel_path"])
    if plan.get("protocol_sha256") != _file_sha256(protocol_path):
        raise ValueError("research protocol changed after external plan freeze")
    if plan.get("model_panel_hash") != stable_hash(_object(panel_path)):
        raise ValueError("model panel changed after external plan freeze")
    conditions = plan.get("conditions")
    if not isinstance(conditions, list) or len(conditions) != 180:
        raise ValueError("external criterion plan has an invalid condition set")


class DeterministicTerminalUser:
    """End a static scenario without adding information or another model call."""

    def respond(self, ending_index: int | None = None) -> None:
        from tool_sandbox.common.execution_context import RoleType
        from tool_sandbox.common.message_conversion import Message
        from tool_sandbox.roles.base_role import BaseRole

        messages = BaseRole.get_messages(ending_index=ending_index)
        if messages[-1].recipient != RoleType.USER:
            raise KeyError("terminal user received a message addressed to another role")
        BaseRole.add_messages(
            [
                Message(
                    sender=RoleType.USER,
                    recipient=RoleType.EXECUTION_ENVIRONMENT,
                    content="end_conversation()",
                )
            ]
        )

    def teardown(self) -> None:
        return None


class PanelAgent:
    """Thin bridge from the frozen provider client to ToolSandbox native tools."""

    def __init__(self, client: Any, model: str, inference_seed: int) -> None:
        from tool_sandbox.common.execution_context import RoleType
        from tool_sandbox.roles.openai_api_agent import OpenAIAPIAgent

        self._delegate_type = OpenAIAPIAgent
        self.role_type = RoleType.AGENT
        self.client = client
        self.model_name = model
        self.inference_seed = inference_seed
        self.responses: list[ModelResponse] = []

    def respond(self, ending_index: int | None = None) -> None:
        self._delegate_type.respond(self, ending_index=ending_index)

    def get_messages(self, ending_index: int | None = None):
        return self._delegate_type.get_messages(ending_index=ending_index)

    def messages_validation(self, messages):
        return self._delegate_type.messages_validation(messages)

    def filter_messages(self, messages):
        return self._delegate_type.filter_messages(messages)

    def get_available_tools(self):
        return self._delegate_type.get_available_tools()

    def add_messages(self, messages):
        return self._delegate_type.add_messages(messages)

    def model_inference(self, openai_messages, openai_tools):
        tools = tuple(dict(item) for item in openai_tools) if isinstance(openai_tools, Sequence) else ()
        response = self.client.complete(
            ModelRequest(
                model=self.model_name,
                messages=tuple(dict(item) for item in openai_messages),
                tools=tools,
                temperature=0.0,
                max_tokens=1024,
                seed=self.inference_seed,
            )
        )
        self.responses.append(response)
        if response.response_errors:
            raise AgnesError("model response contract: " + ",".join(response.response_errors))
        from openai.types.chat.chat_completion_message_tool_call import ChatCompletionMessageToolCall, Function

        calls = [
            ChatCompletionMessageToolCall(
                id=call.call_id,
                type="function",
                function=Function(
                    name=call.name,
                    arguments=json.dumps(call.arguments, ensure_ascii=False, sort_keys=True),
                ),
            )
            for call in response.tool_calls
        ]
        message = SimpleNamespace(content=response.content, tool_calls=calls or None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    def teardown(self) -> None:
        return None


def _run_scenario(
    *,
    scenario_id: str,
    condition: Mapping[str, object],
    configuration: Mapping[str, object],
    execution_policy: Mapping[str, object],
    plan_hash: str,
    output_root: Path,
    client: Any,
) -> JSON:
    from tool_sandbox.common.execution_context import RoleType
    from tool_sandbox.roles.execution_environment import ExecutionEnvironment

    scenario = _scenario_catalog()[scenario_id]
    agent = PanelAgent(client, str(condition["model"]), int(condition["inference_seed"]))
    roles = {
        RoleType.USER: DeterministicTerminalUser(),
        RoleType.EXECUTION_ENVIRONMENT: ExecutionEnvironment(),
        RoleType.AGENT: agent,
    }
    started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    started = perf_counter()
    retries_before = int(getattr(client, "retry_count", 0))
    try:
        with completion_request_scope(
            client,
            condition_id=str(condition["condition_id"]),
            repeat=int(condition["inference_seed"]),
            attempt=1,
        ):
            result = scenario.play_and_evaluate(
                roles=roles,
                output_directory=output_root,
                scenario_name=str(condition["condition_id"]),
            )
        evaluation = result.evaluation_result
        status = "complete"
        error = None
        scores = {
            "similarity": float(evaluation.similarity),
            "milestone_similarity": float(evaluation.milestone_similarity),
            "minefield_similarity": float(evaluation.minefield_similarity),
            "turn_count": int(evaluation.turn_count),
            "milestone_mapping": dict(evaluation.milestone_mapping),
            "minefield_mapping": dict(evaluation.minefield_mapping),
        }
    except Exception as exc:
        status = "failed_closed"
        error = f"{type(exc).__name__}: {str(exc)[:500]}"
        scores = {
            "similarity": 0.0,
            "milestone_similarity": 0.0,
            "minefield_similarity": 0.0,
            "turn_count": int(scenario.max_messages),
            "milestone_mapping": {},
            "minefield_mapping": {},
        }
    finally:
        for role in roles.values():
            role.teardown()
    prompt_tokens = sum(item.prompt_tokens for item in agent.responses)
    completion_tokens = sum(item.completion_tokens for item in agent.responses)
    return {
        "schema": "toolsandbox-external-criterion-row-v1",
        "condition_id": str(condition["condition_id"]),
        "plan_hash": plan_hash,
        "model": str(condition["model"]),
        "provider": str(configuration.get("provider", "unknown")),
        "scenario_id": scenario_id,
        "stratum": str(condition["stratum"]),
        "inference_seed": int(condition["inference_seed"]),
        "status": status,
        "error": error,
        "score": scores,
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "requests": len(agent.responses),
        },
        "response_ids": [item.raw_id for item in agent.responses if item.raw_id],
        "transport_retries": int(getattr(client, "retry_count", 0)) - retries_before,
        "started_at": started_at,
        "duration_seconds": perf_counter() - started,
        "official_evaluator": True,
        "terminal_user_policy": "deterministic_no_new_information",
    }


class _ScriptedClient:
    def __init__(self) -> None:
        self.index = 0
        self.retry_count = 0

    def complete(self, request: ModelRequest) -> ModelResponse:
        scripted: tuple[tuple[str, JSON] | str, ...] = (
            ("set_low_battery_mode_status", {"on": False}),
            ("set_wifi_status", {"on": True}),
            "Wifi has been turned on.",
        )
        value = scripted[self.index]
        self.index += 1
        calls = ()
        content = ""
        if isinstance(value, tuple):
            calls = (ToolCall(call_id=f"self_test_{self.index}", name=value[0], arguments=value[1]),)
        else:
            content = value
        return ModelResponse(
            model=request.model,
            content=content,
            reasoning="",
            tool_calls=calls,
            finish_reason="tool_calls" if calls else "stop",
            prompt_tokens=10,
            completion_tokens=2,
            raw_id=f"self-test-{self.index}",
        )


def self_test() -> JSON:
    condition = {
        "condition_id": "self-test",
        "model": "self-test-model",
        "scenario_id": "turn_on_wifi_low_battery_mode",
        "stratum": "state_dependency",
        "inference_seed": 1,
    }
    with TemporaryDirectory(prefix="toolsandbox-anchor-") as directory:
        row = _run_scenario(
            scenario_id=str(condition["scenario_id"]),
            condition=condition,
            configuration={"provider": "scripted"},
            execution_policy={},
            plan_hash="self-test-plan",
            output_root=Path(directory),
            client=_ScriptedClient(),
        )
    if row["status"] != "complete" or row["score"]["similarity"] != 1.0 or row["usage"]["requests"] != 3:
        raise AssertionError(f"ToolSandbox bridge self-test failed: {row}")
    return row


def freeze(args: argparse.Namespace) -> JSON:
    if args.selection_output.exists() or args.plan_output.exists():
        raise RuntimeError("refusing to overwrite a frozen external criterion artifact")
    manifest = build_selection_manifest()
    plan = build_plan(
        frozen_at=args.frozen_at,
        protocol_path=args.protocol,
        panel_path=args.panel,
        selection_path=args.selection_output,
    )
    args.selection_output.parent.mkdir(parents=True, exist_ok=True)
    args.plan_output.parent.mkdir(parents=True, exist_ok=True)
    args.selection_output.write_text(canonical_json(manifest) + "\n", encoding="utf-8")
    args.plan_output.write_text(canonical_json(plan) + "\n", encoding="utf-8")
    validate_plan(plan)
    return {"selection_hash": stable_hash(manifest), "plan_hash": plan["plan_hash"], "conditions": 180}


def run(args: argparse.Namespace) -> JSON:
    if not args.allow_paid:
        raise RuntimeError("paid external execution requires --allow-paid")
    plan = _object(args.plan)
    validate_plan(plan)
    configurations = {str(item["model"]): item for item in plan["configurations"]}
    if args.model not in configurations:
        raise ValueError(f"model is not in the external criterion plan: {args.model}")
    configuration = configurations[args.model]
    conditions = [item for item in plan["conditions"] if item["model"] == args.model]
    if args.max_conditions is not None:
        conditions = conditions[: args.max_conditions]
    output = args.output_root / f"{configuration['slug']}.jsonl"
    store = ExperimentStore(output)
    planned_ids = {str(item["condition_id"]) for item in conditions}
    for row in store.rows:
        if row.get("plan_hash") != plan["plan_hash"] or row.get("condition_id") not in planned_ids:
            raise ValueError("existing external criterion row is outside the frozen plan subset")
    client = _client_for_configuration(configuration, plan["execution_policy"])
    executed = 0
    for condition in conditions:
        if condition["condition_id"] in store.completed_condition_ids:
            continue
        row = _run_scenario(
            scenario_id=str(condition["scenario_id"]),
            condition=condition,
            configuration=configuration,
            execution_policy=plan["execution_policy"],
            plan_hash=str(plan["plan_hash"]),
            output_root=args.output_root / "trajectories" / str(configuration["slug"]),
            client=client,
        )
        store.append(row)
        executed += 1
    return {"model": args.model, "planned": len(conditions), "executed": executed, "output": str(output)}


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("self-test")
    freeze_parser = subparsers.add_parser("freeze")
    freeze_parser.add_argument("--frozen-at", required=True)
    freeze_parser.add_argument("--protocol", type=Path, required=True)
    freeze_parser.add_argument("--panel", type=Path, required=True)
    freeze_parser.add_argument("--selection-output", type=Path, required=True)
    freeze_parser.add_argument("--plan-output", type=Path, required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--plan", type=Path, required=True)
    run_parser.add_argument("--model", required=True)
    run_parser.add_argument("--output-root", type=Path, required=True)
    run_parser.add_argument("--max-conditions", type=int)
    run_parser.add_argument("--allow-paid", action="store_true")
    args = parser.parse_args()
    if args.command == "self-test":
        result = self_test()
    elif args.command == "freeze":
        result = freeze(args)
    else:
        result = run(args)
    print(canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
