from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from bottleneckbench.core.agnes import ModelRequest, ModelResponse, ToolCall
from bottleneckbench.core.evaluation import AuditCheck, ConstructAudit, run_construct_audit
from bottleneckbench.core.experiments import (
    ExperimentStore,
    RunPlan,
    build_confirmatory_plan,
    build_pilot_plan,
    execute_plan,
    freeze_plan,
    load_plan,
)
from bottleneckbench.core.schema import Representation, Track


class _PilotFakeClient:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self.calls % 2:
            return ModelResponse(
                model=request.model,
                content="",
                reasoning="find invoice",
                tool_calls=(ToolCall("call-1", "search_invoices", {"query": "overdue"}),),
                finish_reason="tool_calls",
                prompt_tokens=10,
                completion_tokens=5,
                raw_id=f"r-{self.calls}",
            )
        return ModelResponse(
            model=request.model,
            content="INV-001 is overdue. Source: invoice-target-1.",
            reasoning="answer",
            tool_calls=(),
            finish_reason="stop",
            prompt_tokens=12,
            completion_tokens=6,
            raw_id=f"r-{self.calls}",
        )


def _passing_audit(panel: str) -> ConstructAudit:
    return ConstructAudit(
        checks=tuple(
            AuditCheck(f"gate-{index}", True, {}) for index in range(10)
        ),
        panel=panel,
    )


class ExperimentPlanTest(unittest.TestCase):
    def test_pilot_plan_is_deterministic_unique_and_track_balanced(self) -> None:
        audit = _passing_audit("S")

        first = build_pilot_plan(audit, model="agnes-2.5-flash", selector_seed=20260803)
        same = build_pilot_plan(audit, model="agnes-2.5-flash", selector_seed=20260803)

        self.assertEqual(first.plan_hash, same.plan_hash)
        self.assertEqual(len(first.conditions), 96)
        self.assertEqual(len({condition.condition_id for condition in first.conditions}), 96)
        self.assertEqual(sum(c.track is Track.DISCOVERY for c in first.conditions), 32)
        self.assertEqual(sum(c.track is Track.COMPOSITION for c in first.conditions), 64)
        self.assertEqual(
            {c.representation for c in first.conditions if c.track is Track.COMPOSITION},
            {
                Representation.RAW,
                Representation.GENERIC_COMPACT,
                Representation.TYPED,
                Representation.LINKED,
            },
        )

    def test_confirmatory_plan_has_response_sealed_three_track_shape(self) -> None:
        audit = _passing_audit("T")

        plan = build_confirmatory_plan(
            audit,
            model="agnes-2.5-flash",
            selector_seed=20260807,
            frozen_at="2026-08-04T00:00:00Z",
        )

        self.assertEqual(plan.panel, "T")
        self.assertEqual(len(plan.conditions), 384)
        self.assertEqual(len({condition.condition_id for condition in plan.conditions}), 384)
        self.assertEqual(sum(c.track is Track.DISCOVERY for c in plan.conditions), 128)
        self.assertEqual(sum(c.track is Track.COMPOSITION for c in plan.conditions), 256)
        self.assertEqual(
            {c.registry_size for c in plan.conditions if c.track is Track.DISCOVERY},
            {32, 128, 512, 2048},
        )
        self.assertEqual(len(plan.source_hash), 64)
        self.assertEqual(len(plan.task_manifest_hash), 64)

    def test_freeze_rejects_failed_construct_audit(self) -> None:
        failed = ConstructAudit(
            checks=(AuditCheck("axis_orthogonality", False, {"reason": "rank"}),),
            panel="S",
        )
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "construct audit"):
                freeze_plan(
                    Path(directory) / "plan.json",
                    panel="S",
                    audit=failed,
                    model="agnes-2.5-flash",
                    selector_seed=1,
                    frozen_at="2026-08-03T00:00:00Z",
                )

    def test_t_freeze_roundtrip_preserves_source_and_task_hashes(self) -> None:
        audit = _passing_audit("T")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "t-plan.json"

            plan = freeze_plan(
                path,
                panel="T",
                audit=audit,
                model="agnes-2.5-flash",
                selector_seed=20260807,
                frozen_at="2026-08-04T00:00:00Z",
            )
            loaded = load_plan(path)

        self.assertEqual(loaded, plan)
        self.assertEqual(loaded.panel, "T")
        self.assertEqual(len(loaded.conditions), 384)
        self.assertEqual(len(loaded.source_hash), 64)

    def test_frozen_plan_roundtrip_preserves_hash_and_conditions(self) -> None:
        audit = _passing_audit("S")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"

            plan = freeze_plan(
                path,
                panel="S",
                audit=audit,
                model="agnes-2.5-flash",
                selector_seed=20260803,
                frozen_at="2026-08-03T00:00:00Z",
            )
            loaded = load_plan(path)

        self.assertEqual(loaded, plan)
        self.assertEqual(loaded.plan_hash, plan.plan_hash)
        self.assertEqual(loaded.frozen_at, "2026-08-03T00:00:00Z")

    def test_store_deduplicates_identical_rows_and_rejects_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runs.jsonl"
            store = ExperimentStore(path)
            row = {
                "condition_id": "condition-1",
                "status": "agent_failure",
                "attempt": 1,
                "payload": {"value": 1},
            }

            self.assertTrue(store.append(row))
            self.assertFalse(store.append(dict(row)))
            with self.assertRaisesRegex(ValueError, "conflicting"):
                store.append({**row, "payload": {"value": 2}})

            lines = path.read_text().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0]), row)

    def test_execute_plan_is_checkpointed_and_resume_safe(self) -> None:
        audit = _passing_audit("S")
        plan = build_pilot_plan(
            audit, model="agnes-2.5-flash", selector_seed=20260803
        )
        fake = _PilotFakeClient()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runs.jsonl"

            first = execute_plan(plan, fake, path, max_conditions=1)
            second = execute_plan(plan, fake, path, max_conditions=1)
            rows = [json.loads(line) for line in path.read_text().splitlines()]

        self.assertEqual(first, {"planned": 1, "executed": 1, "skipped": 0})
        self.assertEqual(second, {"planned": 1, "executed": 0, "skipped": 1})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["plan_hash"], plan.plan_hash)
        self.assertEqual(rows[0]["status"], "verified_success")

    def test_execute_plan_rejects_live_source_mismatch_before_provider_call(self) -> None:
        full = build_pilot_plan(
            _passing_audit("S"), model="agnes-2.5-flash", selector_seed=20260803
        )
        plan = replace(full, source_hash="0" * 64)
        fake = _PilotFakeClient()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "source hash"):
                execute_plan(plan, fake, Path(directory) / "runs.jsonl")
        self.assertEqual(fake.calls, 0)

    def test_resume_rejects_rows_from_another_plan_before_skipping(self) -> None:
        full = build_pilot_plan(
            _passing_audit("S"), model="agnes-2.5-flash", selector_seed=20260803
        )
        plan = full
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runs.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "condition_id": plan.conditions[0].condition_id,
                        "condition": plan.conditions[0].to_json(),
                        "plan_hash": "different-plan",
                    }
                )
                + "\n"
            )
            with self.assertRaisesRegex(ValueError, "plan hash"):
                execute_plan(plan, _PilotFakeClient(), path, max_conditions=1)

    def test_resume_rejects_incomplete_rows_instead_of_skipping(self) -> None:
        plan = build_pilot_plan(
            _passing_audit("S"), model="agnes-2.5-flash", selector_seed=20260803
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runs.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "condition_id": plan.conditions[0].condition_id,
                        "condition": plan.conditions[0].to_json(),
                        "plan_hash": plan.plan_hash,
                    }
                )
                + "\n"
            )
            with self.assertRaisesRegex(ValueError, "incomplete"):
                execute_plan(plan, _PilotFakeClient(), path, max_conditions=1)

    def test_resume_rejects_field_complete_but_schema_invalid_rows(self) -> None:
        plan = build_pilot_plan(
            _passing_audit("S"), model="agnes-2.5-flash", selector_seed=20260803
        )
        condition = plan.conditions[0]
        invalid = {
            "answer": None,
            "attempt": 1,
            "citations": "not-a-list",
            "condition": condition.to_json(),
            "condition_id": condition.condition_id,
            "errors": None,
            "exposure": None,
            "plan_hash": plan.plan_hash,
            "response_ids": None,
            "status": "verified_success",
            "trace": None,
            "usage": None,
            "verdict": None,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runs.jsonl"
            path.write_text(json.dumps(invalid) + "\n")
            with self.assertRaisesRegex(ValueError, "schema"):
                execute_plan(plan, _PilotFakeClient(), path, max_conditions=1)

    def test_execute_rejects_conditions_outside_generated_panel(self) -> None:
        full = build_pilot_plan(
            _passing_audit("S"), model="agnes-2.5-flash", selector_seed=20260803
        )
        altered = replace(full.conditions[0], model="different-model")
        plan = replace(full, conditions=(altered, *full.conditions[1:]))
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "condition set"):
                execute_plan(plan, _PilotFakeClient(), Path(directory) / "runs.jsonl")

    def test_plan_build_rejects_construct_audit_for_another_panel(self) -> None:
        with self.assertRaisesRegex(ValueError, "panel"):
            build_confirmatory_plan(
                _passing_audit("S"),
                model="agnes-2.5-flash",
                selector_seed=20260807,
                frozen_at="internally-labeled",
            )

    def test_store_rejects_missing_condition_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ExperimentStore(Path(directory) / "runs.jsonl")
            with self.assertRaisesRegex(ValueError, "condition_id"):
                store.append({"status": "agent_failure"})


if __name__ == "__main__":
    unittest.main()
