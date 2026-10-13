"""Offline safety checks for the independently budgeted development pilot."""
from collections import Counter
from dataclasses import replace
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

from bottleneckbench.core.agnes import ModelRequest, ModelResponse
from bottleneckbench.design import build_families, visible_menu
from bottleneckbench.structural_holdout import _SequentialAuditClient, static_audit
from bottleneckbench.runtime import run_episode

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("policy_pilot", ROOT / "experiments/policy_pilot.py")
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)
sys.modules["policy_pilot"] = pilot
analysis_spec = importlib.util.spec_from_file_location("analyze_policy_pilot", ROOT / "experiments/analyze_policy_pilot.py")
analysis = importlib.util.module_from_spec(analysis_spec)
analysis_spec.loader.exec_module(analysis)


class Echo:
    def __init__(self):
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        return ModelResponse(request.model, "{}", "", (), "stop", 3, 2, "test")


class PolicyPilotTest(unittest.TestCase):
    def test_complete_balanced_inventory_and_fixed_limits(self):
        blocks = pilot.inventory()
        self.assertEqual(blocks, pilot.inventory())
        self.assertEqual(len(blocks), 192)
        self.assertEqual(len({b["block_id"] for b in blocks}), 192)
        self.assertEqual(len({b["intent"] for b in blocks}), 12)
        self.assertEqual(sum(len(b["actions"]) for b in blocks), 1536)
        counts = Counter(pilot.MODELS[2][0] if a == "switch" else b["incumbent"] for b in blocks for a in b["actions"])
        self.assertEqual(sorted(counts.values()), [192, 672, 672])
        for action in pilot.ACTIONS:
            positions = Counter(b["actions"].index(action) for b in blocks)
            self.assertLessEqual(max(positions.values()) - min(positions.values()), 1)
        self.assertFalse(pilot.LIMITS["dynamic_shortlist"])
        self.assertFalse(pilot.LIMITS["early_stop_infeasible"])
        self.assertEqual(pilot.LIMITS["max_tool_calls"], 32)

    def test_router_and_public_features_cannot_consume_hidden_labels(self):
        block = pilot.inventory()[0]
        family = next(f for f in build_families("development") if f.family_id == block["family_id"])
        cond, task = pilot.condition(block, "probe", family)
        tools = tuple(t.to_openai() for t in visible_menu(task, 128))
        request = ModelRequest(model=cond.model, messages=({"role": "user", "content": task.request},), tools=tools)
        first, second = Echo(), Echo()
        a, b = pilot.PublicTrace(first), pilot.PublicTrace(second)
        pilot.PublicRouter(a).complete(request)
        # These mutations are evaluator-side only; neither object has a task argument.
        altered = replace(task, family=replace(family, decision="deliberately_changed"))
        self.assertNotEqual(task.semantic_signature, altered.semantic_signature)
        pilot.PublicRouter(b).complete(request)
        self.assertEqual(a.features, b.features)
        self.assertEqual(first.requests, second.requests)
        self.assertLess(len(first.requests[0].tools), 128)
        self.assertGreater(len(first.requests[0].tools), 0)
        self.assertNotIn("intent", a.features)
        ambiguous = replace(request, messages=({"role": "user", "content": task.request + " workspace=other, region=other, collection=other"},))
        pilot.PublicRouter(a).complete(ambiguous)
        self.assertEqual(first.requests[-1].tools, tools)
        malformed_tools = list(tools)
        malformed_tools[0] = {"type": "function", "function": {"name": "bad", "description": "using source route broken"}}
        pilot.PublicRouter(a).complete(replace(request, tools=tuple(malformed_tools)))
        self.assertEqual(first.requests[-1].tools, tuple(malformed_tools))

    def test_reference_restarts_and_adapter_composition(self):
        families = {f.family_id: f for f in build_families("development")}
        blocks = pilot.inventory()
        for block in blocks:
            if block["repeat"] or block["incumbent"] != pilot.MODELS[0][0]:
                continue
            for action in ("probe", "router", "delivery"):
                cond, task = pilot.condition(block, action, families[block["family_id"]])
                client = pilot.PublicTrace(_SequentialAuditClient(task))
                result = run_episode(cond, task, pilot.wrap(client, action), max_completion_tokens=1024, **pilot.LIMITS)
                self.assertTrue(result.verdict.verified_success, (block["intent"], action, result.verdict.errors))
                self.assertEqual(result.effective_max_tool_calls, 32)
                self.assertEqual(result.effective_max_turns, 34)
        # Existing exhaustive audits include hidden-program invariance, ambiguity,
        # raw/compact decoding and controller/delivery bundle compatibility.
        self.assertTrue(static_audit()["passed"])
        a, b = pilot.wrap(Echo(), "bundle"), pilot.wrap(Echo(), "bundle")
        self.assertIsNot(a.base, b.base)

    def test_cash_reservation_unknown_billing_and_cap(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            plan_file = directory / "plan.json"
            plan_file.write_text("{}")
            plan = {"prices": {"test": {"max_input_price": "0.000001", "max_output_price": "0.000002"}}}
            with patch.object(pilot, "PLAN", plan_file):
                guard = pilot.Guard(directory, plan)
                guard.model = "test"
                guard.before()
                self.assertIsNotNone(pilot.read(guard.path)["pending"])
                with self.assertRaisesRegex(RuntimeError, "Unresolved"):
                    guard.before()
                with self.assertRaisesRegex(RuntimeError, "billing"):
                    guard.observe({"response_body": "{}"})
                self.assertIsNotNone(guard.state["pending"])
                guard.observe({"response_body": json.dumps({"usage": {"prompt_tokens": 100, "completion_tokens": 50, "cost": 0.0001}})})
                self.assertEqual(Decimal(guard.state["accounted_usd"]), Decimal("0.0002"))
                self.assertEqual(guard.state["pending"], None)
                guard.state["accounted_usd"] = "14.71"
                with self.assertRaisesRegex(RuntimeError, "ceiling"):
                    guard.before()
                self.assertIsNone(guard.state["pending"])

    def test_action_choice_uses_training_outcomes_not_test_truth(self):
        examples = []
        for i in range(32):
            features = {k: 0 for k in analysis.TRACE}
            features["menu_count"] = 8
            features["length_finishes"] = i % 2
            good = "budget" if i % 2 else "router"
            examples.append(dict(features=features,
                success={a: int(a == good) for a in analysis.CHOICES},
                cost={a: 1 for a in analysis.CHOICES}))
        public = [{"id": "test", "features": examples[1]["features"]}]
        decisions, rules = analysis.held_out_choices(examples, public)
        self.assertEqual(decisions["test"]["trace_policy"], "budget")
        self.assertIsNone(rules["metadata_policy"]["feature"])
        public[0]["hidden_test_outcomes"] = {a: 0 for a in analysis.CHOICES}
        self.assertEqual(decisions, analysis.held_out_choices(examples, public)[0])
        public[0]["hidden_test_outcomes"] = {a: 1 for a in analysis.CHOICES}
        self.assertEqual(decisions, analysis.held_out_choices(examples, public)[0])


if __name__ == "__main__":
    unittest.main()
