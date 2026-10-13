"""Small, separately budgeted public-observation policy-development campaign.

No task object reaches the router, adapters, provider, or feature extractor.
This is a development pilot, never a new-intent confirmatory evaluation.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import random
import re
import sys
from tempfile import NamedTemporaryFile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bottleneckbench.core.agnes import ModelRequest
from bottleneckbench.core.schema import Representation, stable_hash
from bottleneckbench.design import CapacityCondition, WorkflowLoad, build_families, compile_task, certify_minimal_program
from bottleneckbench.providers import ChatCompletionsClient
from bottleneckbench.runtime import run_episode, reference_execution
from bottleneckbench.structural_holdout import CanonicalDeliveryClient, ProviderCallLog, TraceStateClient

CAMPAIGN = ROOT / "results/prospective-execution/nonoracle-policy-pilot-1"
PLAN = ROOT / "results/prospective-plans/nonoracle-policy-pilot-1/plan.json"
PROTOCOL = ROOT / "protocols/nonoracle-policy-pilot-2026-09-05.md"
MODELS = (
    ("qwen/qwen3.8-flash", "alibaba"),
    ("deepseek/deepseek-v4-flash-0731", "sail-research/fp4"),
    ("openai/gpt-5.6-luna", "openai/flex"),
)
ACTIONS = ("retry", "budget", "router", "controller", "delivery", "bundle", "switch")
CELLS = ((8, "low", "raw"), (128, "low", "raw"), (8, "high", "raw"), (8, "low", "generic_compact"))
CEILING = Decimal("15")
RESERVE = Decimal("0.30")
LIMITS = dict(max_turns=34, max_tool_calls=32, max_total_tokens=128000,
              dynamic_shortlist=False, early_stop_infeasible=False)
ROUTE = re.compile(r"workspace=([A-Za-z0-9_-]+), region=([A-Za-z0-9_-]+), collection=([A-Za-z0-9_-]+)")


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, sort_keys=True, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    temporary.replace(path)


def read(path):
    return json.loads(path.read_text())


def code_bindings():
    paths = sorted((ROOT / "src/bottleneckbench").rglob("*.py")) + [Path(__file__), PROTOCOL]
    return {str(path.relative_to(ROOT)): sha256(path.read_bytes()).hexdigest() for path in paths}


class PublicRouter:
    def __init__(self, base):
        self.base = base

    def complete(self, request):
        # Only the original user message specifies a source route, not tool output.
        matches = set()
        for message in request.messages:
            if message.get("role") == "user":
                matches.update(ROUTE.findall(str(message.get("content", ""))))
        if len(matches) != 1:
            return self.base.complete(request)
        route = next(iter(matches))
        retained, matched = [], 0
        for tool in request.tools:
            description = str(tool.get("function", {}).get("description", ""))
            coordinates = ROUTE.findall(description)
            if "using source route" in description and len(coordinates) != 1:
                return self.base.complete(request)  # malformed: preserve full menu
            if not coordinates or coordinates[0] == route:
                retained.append(tool)
                matched += bool(coordinates)
        if not matched:
            return self.base.complete(request)
        return self.base.complete(replace(request, tools=tuple(retained)))


class PublicTrace(ProviderCallLog):
    """Numeric features from delivered requests and provider responses, not rows."""
    def __init__(self, base):
        super().__init__(base)
        self.features = dict(menu_count=0, completion_cap=0, call_limit=32,
                             total_token_limit=128000, requests=0, tool_calls=0,
                             tool_observations=0, public_tool_errors=0,
                             length_finishes=0, empty_finishes=0,
                             prompt_tokens=0, completion_tokens=0)
        self.transcript_digest = sha256()

    def complete(self, request):
        if not self.features["requests"]:
            self.features["menu_count"] = len(request.tools)
            self.features["completion_cap"] = request.max_tokens
        self.features["requests"] += 1
        tool_messages = [m for m in request.messages if m.get("role") == "tool"]
        self.features["tool_observations"] = len(tool_messages)
        public_errors = 0
        for message in tool_messages:
            try:
                payload = json.loads(message.get("content", ""))
            except (TypeError, json.JSONDecodeError):
                continue
            public_errors += isinstance(payload, dict) and payload.get("ok") is False
        self.features["public_tool_errors"] = public_errors
        self.transcript_digest.update(json.dumps(request.to_json(), sort_keys=True).encode())
        response = super().complete(request)
        self.features["tool_calls"] += len(response.tool_calls)
        self.features["length_finishes"] += response.finish_reason == "length"
        self.features["empty_finishes"] += not response.content and not response.tool_calls
        self.features["prompt_tokens"] += response.prompt_tokens
        self.features["completion_tokens"] += response.completion_tokens
        self.transcript_digest.update(repr(response).encode())
        return response


def wrap(base, action):
    # Router -> controller -> delivery -> meter -> provider, fresh for every arm.
    client = CanonicalDeliveryClient(base) if action in {"delivery", "bundle"} else base
    client = TraceStateClient(client) if action in {"controller", "bundle"} else client
    return PublicRouter(client) if action in {"router", "bundle"} else client


def inventory():
    blocks = []
    for family in build_families("development"):
        for menu, workflow, representation in CELLS:
            task = compile_task(family, workflow)
            for model, _ in MODELS[:2]:
                for repeat in range(2):
                    block = dict(family_id=family.family_id, intent=family.intent,
                                 workflow=workflow, representation=representation,
                                 visible_tools=menu, incumbent=model, repeat=repeat,
                                 task_hash=task.task_hash)
                    block["block_id"] = stable_hash(block)
                    blocks.append(block)
    random.Random(20260905).shuffle(blocks)
    for index, block in enumerate(blocks):
        offset = index % len(ACTIONS)
        actions = ACTIONS[offset:] + ACTIONS[:offset]
        block["actions"] = ["probe", *actions]
        block["probe_seed"] = 910000 + block["repeat"]
        block["restart_seed"] = 920000 + block["repeat"]
    return blocks


def condition(block, action, family):
    task = compile_task(family, block["workflow"])
    result = CapacityCondition(
        task_id=task.task_id, family_id=family.family_id, intent=family.intent,
        outcome_variant=family.outcome_variant, prompt_variant=family.prompt_variant,
        branching=family.branching, action_required=task.action is not None,
        model=MODELS[2][0] if action == "switch" else block["incumbent"],
        harness="nonoracle-policy-pilot@openrouter", workflow=WorkflowLoad(block["workflow"]),
        representation=Representation(block["representation"]),
        visible_tools=block["visible_tools"], seed=family.seed)
    return result, task


def prices():
    result = {}
    for model, pin in MODELS:
        url = f"https://openrouter.ai/api/v1/models/{model}/endpoints"
        with urlopen(url, timeout=30) as response:
            endpoints = json.load(response)["data"]["endpoints"]
        selected = [e for e in endpoints if e.get("tag") == pin]
        if len(selected) != 1:
            raise RuntimeError(f"No unique endpoint: {model}/{pin}")
        entry = selected[0]
        required = {"tools", "tool_choice", "max_tokens", "seed"}
        if not required.issubset(entry["supported_parameters"]):
            raise RuntimeError(f"Required parameters unsupported: {model}")
        tiers = [entry["pricing"], *entry["pricing"].get("overrides", [])]
        maximum_prompt = max(Decimal(str(t.get(k, 0))) for t in tiers
                             for k in ("prompt", "input_cache_write"))
        maximum_completion = max(Decimal(str(t["completion"])) for t in tiers)
        upper = int(entry.get("max_prompt_tokens") or entry["context_length"]) * maximum_prompt + 4096 * maximum_completion
        if upper >= RESERVE:
            raise RuntimeError(f"Request envelope exceeds reserved liability: {model}")
        # No web-search tools are enabled; no per-request price component allowed.
        if Decimal(str(entry["pricing"].get("request", 0))) != 0:
            raise RuntimeError("Unexpected request-based fee")
        result[model] = dict(source=url, observed_at=now(), endpoint=entry,
                             max_input_price=str(maximum_prompt),
                             max_output_price=str(maximum_completion))
    return result


def prepare():
    if PLAN.exists():
        raise RuntimeError("Frozen plan exists; use check, never overwrite")
    certifications = []
    for family in build_families("development"):
        for workflow in ("low", "high"):
            task = compile_task(family, workflow)
            certificate = certify_minimal_program(task)
            verdict = reference_execution(task)[3]
            if not certificate.passed or certificate.declared_calls > 32 or not verdict.verified_success:
                raise RuntimeError(f"Reference solvability failed: {task.task_id}")
            certifications.append(dict(task_hash=task.task_hash, **certificate.to_json()))
    atomic(PLAN, dict(study="nonoracle-policy-development-pilot-1", frozen_at=now(),
                      evidence_weight="development_only", approved_ceiling_usd=str(CEILING),
                      request_reservation_usd=str(RESERVE), blocks=inventory(),
                      episodes=1536, limits=LIMITS, base_completion_cap=1024,
                      code_bindings=code_bindings(), prices=prices(), certifications=certifications))


def check():
    plan = read(PLAN)
    if plan["code_bindings"] != code_bindings() or plan["blocks"] != inventory():
        raise RuntimeError("Frozen code or inventory changed; do not dispatch")
    if plan["approved_ceiling_usd"] != str(CEILING):
        raise RuntimeError("Budget binding differs")
    return plan


class Guard:
    """One durable outstanding liability, no automatic retries or ambiguous resume."""
    def __init__(self, directory, plan):
        self.directory, self.plan = directory, plan
        self.path = directory / "ledger.json"
        self.state = read(self.path) if self.path.exists() else dict(
            admitted=0, accounted_usd="0", reported_usd="0", pending=None,
            active_episode=None, plan_sha256=sha256(PLAN.read_bytes()).hexdigest())
        if self.state["plan_sha256"] != sha256(PLAN.read_bytes()).hexdigest():
            raise RuntimeError("Ledger belongs to a different plan")
        self.model = None

    def save(self):
        atomic(self.path, self.state)

    def before(self):
        if self.state["pending"] is not None:
            raise RuntimeError("Unresolved request liability; no automatic retry")
        if Decimal(self.state["accounted_usd"]) + RESERVE > CEILING:
            raise RuntimeError("USD 15 ceiling: next request not admitted")
        self.state["admitted"] += 1
        self.state["pending"] = dict(number=self.state["admitted"], reserve_usd=str(RESERVE),
                                     model=self.model, episode=self.state["active_episode"], at=now())
        self.save()  # reserve before the socket is opened

    def observe(self, receipt):
        pending = self.state["pending"]
        if pending is None:
            raise RuntimeError("Response without prior reservation")
        atomic(self.directory / "receipts" / f"{pending['number']:07d}.json", dict(
            admission=pending, observed_at=now(), **receipt))
        try:
            payload = json.loads(receipt["response_body"])
            usage = payload["usage"]
            prompt, completion = int(usage["prompt_tokens"]), int(usage["completion_tokens"])
            cost = Decimal(str(usage["cost"]))
            if prompt < 0 or completion < 0 or prompt + completion <= 0 or not cost.is_finite() or cost < 0:
                raise ValueError("Invalid usage/cost")
        except (ValueError, TypeError, KeyError):
            raise RuntimeError("Missing or invalid billing; reservation retained, campaign halted") from None
        prices_ = self.plan["prices"][pending["model"]]
        conservative = prompt * Decimal(prices_["max_input_price"]) + completion * Decimal(prices_["max_output_price"])
        liability = max(cost, conservative)
        self.state["accounted_usd"] = str(Decimal(self.state["accounted_usd"]) + liability)
        self.state["reported_usd"] = str(Decimal(self.state["reported_usd"]) + cost)
        self.state["pending"] = None
        self.save()
        if liability > RESERVE:
            raise RuntimeError("Pricing envelope violation; stop immediately")


def provider(model, guard):
    guard.model = model
    pin = dict(MODELS)[model]
    return ChatCompletionsClient(
        endpoint="https://openrouter.ai/api/v1/chat/completions", api_key_env="OPENROUTER_API_KEY",
        timeout_seconds=90, max_retries=0, provider_only=(pin,), provider_allow_fallbacks=False,
        provider_require_parameters=True, accepted_response_models=(model,),
        omit_temperature=model.startswith("openai/"), omit_parallel_tool_calls=True,
        require_usage=True, disable_response_cache=True,
        before_attempt=guard.before, response_observer=guard.observe)


def preflight(plan, guard):
    path = CAMPAIGN / "transport-preflight.json"
    if path.exists():
        if not read(path).get("passed"):
            raise RuntimeError("Previous transport preflight did not pass")
        return
    if guard.state["active_episode"]:
        raise RuntimeError("Incomplete prior preflight; no duplicate calls")
    guard.state["active_episode"] = "transport-preflight"
    guard.save()
    checks = []
    for model, pin in MODELS:
        request = ModelRequest(model=model, messages=({"role": "user", "content": "Call echo with text hello. Do not answer in prose."},),
            tools=({"type": "function", "function": {"name": "echo", "description": "Echo text.",
                "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}},),
            max_tokens=1024, seed=20260905)
        response = provider(model, guard).complete(request)
        passed = bool(response.tool_calls) and response.tool_calls[0].name == "echo" and response.tool_calls[0].arguments == {"text": "hello"}
        checks.append(dict(model=model, pin=pin, served_model=response.model, passed=passed,
                           generation_id=response.raw_id, total_tokens=response.total_tokens))
        atomic(path, dict(passed=False, checks=checks))
        if not passed:
            raise RuntimeError("Tool-call transport check failed; no benchmark dispatched")
    atomic(path, dict(passed=True, checks=checks, completed_at=now()))
    guard.state["active_episode"] = None
    guard.save()


def execute(plan, guard):
    path = CAMPAIGN / "rows.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    done = {r["episode_id"] for r in rows}
    if len(done) != len(rows):
        raise RuntimeError("Duplicate episode rows")
    active = guard.state["active_episode"]
    if active and (active not in done or guard.state["pending"] is not None):
        raise RuntimeError("Interrupted episode; inspect receipts, do not automatically repeat")
    families = {f.family_id: f for f in build_families("development")}
    for block in plan["blocks"]:
        for action in block["actions"]:
            episode_id = stable_hash(dict(block_id=block["block_id"], action=action))
            if episode_id in done:
                continue
            guard.state["active_episode"] = episode_id
            guard.save()
            started, cost_before = now(), Decimal(guard.state["reported_usd"])
            cond, task = condition(block, action, families[block["family_id"]])
            if task.task_hash != block["task_hash"]:
                raise RuntimeError("Task hash differs from frozen inventory")
            meter = PublicTrace(provider(cond.model, guard))
            cap = 4096 if action in {"budget", "bundle"} else 1024
            result = run_episode(cond, task, wrap(meter, action),
                inference_seed=block["probe_seed"] if action == "probe" else block["restart_seed"],
                max_completion_tokens=cap, **LIMITS)
            result = meter.normalize(result)
            row = dict(episode_id=episode_id, block_id=block["block_id"], action=action,
                       repeat=block["repeat"], incumbent=block["incumbent"], started_at=started,
                       completed_at=now(), completion_cap=cap, public_features=meter.features,
                       public_transcript_sha256=meter.transcript_digest.hexdigest(),
                       reported_cost_usd=str(Decimal(guard.state["reported_usd"]) - cost_before),
                       episode=result.to_json())
            with path.open("a") as handle:
                handle.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            done.add(episode_id)
            guard.state["active_episode"] = None
            guard.save()
            print(json.dumps(dict(at=now(), rows=len(done), planned=1536, action=action,
                                  model=cond.model, reported_usd=guard.state["reported_usd"],
                                  accounted_usd=guard.state["accounted_usd"])), flush=True)
            if result.status.value == "provider_failure":
                raise RuntimeError("Transport failure recorded as missing outcome; stopped")
    atomic(CAMPAIGN / "status.json", dict(status="complete", rows=len(done), completed_at=now()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "check", "preflight", "run"))
    parser.add_argument("--allow-paid", action="store_true")
    args = parser.parse_args()
    if args.mode == "prepare":
        prepare()
        print("Frozen development pilot: 192 blocks / 1536 episodes / USD 15 ceiling")
        return
    plan = check()
    if args.mode == "check":
        print("Code, task inventory and budget bindings match")
        return
    if not args.allow_paid or not os.environ.get("OPENROUTER_API_KEY"):
        raise RuntimeError("Explicit --allow-paid and environment key required")
    # ponytail: single-flight writer; add concurrency only with separately audited reservations.
    CAMPAIGN.mkdir(parents=True, exist_ok=True)
    with (CAMPAIGN / "writer.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        guard = Guard(CAMPAIGN, plan)
        try:
            preflight(plan, guard)
            if args.mode == "run":
                atomic(CAMPAIGN / "status.json", dict(status="running", started_at=now()))
                execute(plan, guard)
        except Exception as exc:
            atomic(CAMPAIGN / "status.json", dict(status="incomplete", stopped_at=now(),
                   reason=str(exc), reported_usd=guard.state["reported_usd"], pending=guard.state["pending"]))
            raise


if __name__ == "__main__":
    main()
