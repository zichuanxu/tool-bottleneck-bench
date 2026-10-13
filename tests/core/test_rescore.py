from __future__ import annotations

from copy import deepcopy
import unittest

from bottleneckbench.core.evaluation import trace_event_from_result
from bottleneckbench.core.rescore import rescore_row
from bottleneckbench.core.schema import stable_hash
from bottleneckbench.core.tasks import development_templates
from bottleneckbench.core.world import execute_tool, make_world


class CorrectiveRescoreTest(unittest.TestCase):
    def test_replay_rejects_original_false_positive_unrequested_mutation(self) -> None:
        task = development_templates()[0]
        world = make_world(3)
        trace = []
        for tool, arguments in (
            ("search_invoices", {"query": "INV-003"}),
            (
                "create_email_draft",
                {
                    "recipient": "finance@northstar.example",
                    "subject": "Unrequested",
                    "body": "Unrequested",
                    "idempotency_key": "rescore-3",
                },
            ),
        ):
            before_hash = world.snapshot_hash
            result = execute_tool(world, tool, arguments)
            trace.append(
                trace_event_from_result(
                    tool, arguments, before_hash, world.snapshot_hash, result
                )
            )
        condition = {
            "task_id": task.template_id,
            "track": "discovery",
            "model": "test",
            "harness": "flat-retrieval",
            "representation": "raw",
            "registry_size": 32,
            "exposure_size": 8,
            "seed": 3,
        }
        row = {
            "condition_id": stable_hash(condition),
            "condition": condition,
            "plan_hash": "test-plan",
            "status": "verified_success",
            "trace": [event.to_json() for event in trace],
            "answer": "INV-003 is overdue. Source invoice-target-3.",
            "citations": [trace[0].evidence[0].to_json()],
            "usage": {"total_tokens": 100},
            "errors": [],
            "verdict": {"budget": True, "strict_vts": True},
        }

        correction = rescore_row(row, task)

        self.assertTrue(correction["replay_valid"])
        self.assertEqual(correction["original_row_hash"], stable_hash(row))
        self.assertEqual(correction["plan_hash"], "test-plan")
        self.assertEqual(correction["task_semantic_hash"], task.semantic_hash)
        self.assertEqual(len(correction["corrective_source_hash"]), 64)
        self.assertTrue(correction["changed_strict_vts"])
        self.assertFalse(correction["corrective_verdict"]["strict_vts"])
        self.assertIn(
            "unrequested_mutation", correction["corrective_verdict"]["errors"]
        )

        oversized = deepcopy(row)
        oversized["usage"]["total_tokens"] = 999999
        oversized["verdict"]["budget"] = True
        oversized_correction = rescore_row(oversized, task)
        self.assertFalse(oversized_correction["corrective_verdict"]["budget"])
        self.assertIn(
            "budget_exceeded", oversized_correction["corrective_verdict"]["errors"]
        )


if __name__ == "__main__":
    unittest.main()
