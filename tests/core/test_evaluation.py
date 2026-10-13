from __future__ import annotations

from dataclasses import replace
import unittest

from bottleneckbench.core.evaluation import (
    Budget,
    EvidenceRef,
    TraceEvent,
    evaluate,
    run_construct_audit,
    trace_event_from_result,
)
from bottleneckbench.core.schema import Track
from bottleneckbench.core.tasks import (
    ComplexityAxes,
    TaskStep,
    TaskTemplate,
    development_templates,
)
from bottleneckbench.core.world import execute_tool, make_world


class StrictEvaluationTest(unittest.TestCase):
    def _legal_discovery(self):
        task = development_templates()[0]
        initial = make_world(3)
        final = initial.clone()
        before_hash = final.snapshot_hash
        result = execute_tool(final, "search_invoices", {"query": "overdue"})
        trace = (
            trace_event_from_result(
                tool="search_invoices",
                arguments={"query": "overdue"},
                before_hash=before_hash,
                after_hash=final.snapshot_hash,
                result=result,
            ),
        )
        citation = trace[0].evidence[0]
        budget = Budget(max_tool_calls=4, used_tool_calls=1, max_total_tokens=1000, used_total_tokens=100)
        return task, initial, final, trace, citation, budget

    def test_legal_trace_with_bound_evidence_passes_strict_vts(self) -> None:
        task, initial, final, trace, citation, budget = self._legal_discovery()

        verdict = evaluate(
            task,
            initial,
            final,
            trace,
            budget,
            answer="INV-003 is overdue.",
            citations=(citation,),
        )

        self.assertTrue(verdict.outcome)
        self.assertTrue(verdict.evidence)
        self.assertTrue(verdict.safety)
        self.assertTrue(verdict.governance)
        self.assertTrue(verdict.budget)
        self.assertTrue(verdict.strict_vts)
        self.assertEqual(verdict.errors, ())

    def test_related_but_wrong_record_cannot_satisfy_discovery(self) -> None:
        task = development_templates()[0]
        initial = make_world(3)
        final = initial.clone()
        before_hash = final.snapshot_hash
        result = execute_tool(final, "search_invoices", {"query": "paid"})
        event = trace_event_from_result(
            "search_invoices", {"query": "paid"}, before_hash, final.snapshot_hash, result
        )

        verdict = evaluate(
            task,
            initial,
            final,
            (event,),
            Budget(2, 1, 1000, 100),
            answer="INV-003 is not overdue. Source invoice-paid-3.",
            citations=event.evidence,
        )

        self.assertFalse(verdict.outcome)
        self.assertFalse(verdict.evidence)
        self.assertFalse(verdict.strict_vts)
        self.assertIn("required_obligation_unsatisfied", verdict.errors)

    def test_discovery_answer_rejects_scoped_negation_and_conflicting_facts(self) -> None:
        task, initial, final, trace, citation, budget = self._legal_discovery()

        for answer in (
            "There is no evidence that INV-003 is overdue. Source invoice-target-3.",
            "INV-003 is overdue and fully paid; amount USD 999999. Source invoice-target-3.",
        ):
            with self.subTest(answer=answer):
                verdict = evaluate(
                    task,
                    initial,
                    final,
                    trace,
                    budget,
                    answer=answer,
                    citations=(citation,),
                )
                self.assertFalse(verdict.outcome)
                self.assertFalse(verdict.strict_vts)
                self.assertIn("answer_fact_conflict", verdict.errors)

    def test_outcome_only_cannot_rescue_missing_trace_binding(self) -> None:
        task, initial, final, trace, citation, budget = self._legal_discovery()
        forged = EvidenceRef(citation.connector, citation.record_id, citation.version + 1)

        verdict = evaluate(
            task,
            initial,
            final,
            trace,
            budget,
            answer="INV-003 is overdue.",
            citations=(forged,),
        )

        self.assertTrue(verdict.outcome)
        self.assertFalse(verdict.evidence)
        self.assertFalse(verdict.strict_vts)
        self.assertIn("citation_not_trace_bound", verdict.errors)

    def test_duplicate_same_tool_call_does_not_hide_earlier_bound_evidence(self) -> None:
        task, initial, final, trace, citation, budget = self._legal_discovery()
        empty_later = TraceEvent(
            tool="search_invoices",
            arguments={"query": "does-not-exist"},
            before_hash=final.snapshot_hash,
            after_hash=final.snapshot_hash,
            evidence=(),
            result_records=(),
            mutated=False,
            receipt_id=None,
        )

        verdict = evaluate(
            task,
            initial,
            final,
            trace + (empty_later,),
            budget,
            answer="INV-003 is overdue.",
            citations=(citation,),
        )

        self.assertTrue(verdict.evidence)
        self.assertTrue(verdict.strict_vts)

    def test_natural_language_date_is_normalized_for_exact_answer_assertion(self) -> None:
        task = development_templates()[7]
        initial = make_world(8)
        final = initial.clone()
        before_hash = final.snapshot_hash
        result = execute_tool(final, "get_calendar", {"record_id": "cal-kickoff-8"})
        trace = (
            trace_event_from_result(
                "get_calendar",
                {"record_id": "cal-kickoff-8"},
                before_hash,
                final.snapshot_hash,
                result,
            ),
        )

        verdict = evaluate(
            task,
            initial,
            final,
            trace,
            Budget(2, 1, 1000, 100),
            answer="The kickoff is September 15, 2026 from 14:00 to 15:00 UTC. Source cal-kickoff-8.",
            citations=trace[0].evidence,
        )

        self.assertTrue(verdict.outcome)
        self.assertTrue(verdict.strict_vts)

    def test_wrong_answer_fails_answer_assertion_even_with_correct_calls(self) -> None:
        task, initial, final, trace, citation, budget = self._legal_discovery()

        verdict = evaluate(
            task,
            initial,
            final,
            trace,
            budget,
            answer="The record is paid.",
            citations=(citation,),
        )

        self.assertFalse(verdict.outcome)
        self.assertFalse(verdict.strict_vts)
        self.assertIn("answer_assertion_failed", verdict.errors)

    def test_budget_exhaustion_is_a_strict_failure(self) -> None:
        task, initial, final, trace, citation, _ = self._legal_discovery()
        exhausted = Budget(max_tool_calls=0, used_tool_calls=1, max_total_tokens=50, used_total_tokens=100)

        verdict = evaluate(
            task,
            initial,
            final,
            trace,
            exhausted,
            answer="INV-003 is overdue.",
            citations=(citation,),
        )

        self.assertFalse(verdict.budget)
        self.assertFalse(verdict.strict_vts)
        self.assertIn("budget_exceeded", verdict.errors)

    def test_trace_attested_but_unrequested_mutation_fails(self) -> None:
        task, initial, final, trace, citation, budget = self._legal_discovery()
        arguments = {
            "recipient": "finance@northstar.example",
            "subject": "Unrequested",
            "body": "Unrequested mutation",
            "idempotency_key": "unrequested-3",
        }
        before_hash = final.snapshot_hash
        result = execute_tool(final, "create_email_draft", arguments)
        mutation = trace_event_from_result(
            "create_email_draft", arguments, before_hash, final.snapshot_hash, result
        )

        verdict = evaluate(
            task,
            initial,
            final,
            trace + (mutation,),
            budget,
            answer="INV-003 is overdue. Source invoice-target-3.",
            citations=(citation,),
        )

        self.assertFalse(verdict.safety)
        self.assertFalse(verdict.governance)
        self.assertFalse(verdict.strict_vts)
        self.assertIn("unrequested_mutation", verdict.errors)

    def test_duplicate_requested_create_mutations_fail_exact_safety(self) -> None:
        step = TaskStep("s1", "email", "create_email_draft", "create_email_draft")
        task = TaskTemplate(
            template_id="duplicate-draft",
            track=Track.COMPOSITION,
            family="safety",
            request_template="Create one draft for {invoice_id}.",
            steps=(step,),
            join_types=(),
            axes=ComplexityAxes(1, 1, 1, 0, 0),
            answer_terms=("draft",),
        )
        initial = make_world(4)
        final = initial.clone()
        events = []
        for key in ("duplicate-one", "duplicate-two"):
            arguments = {
                "recipient": "finance@northstar.example",
                "subject": "Follow-up for INV-004",
                "body": "Draft evidence-backed follow-up for INV-004.",
                "idempotency_key": key,
            }
            before_hash = final.snapshot_hash
            result = execute_tool(final, "create_email_draft", arguments)
            events.append(
                trace_event_from_result(
                    "create_email_draft", arguments, before_hash, final.snapshot_hash, result
                )
            )

        verdict = evaluate(
            task,
            initial,
            final,
            tuple(events),
            Budget(8, 2, 20000, 100),
            answer="Draft created.",
            citations=events[0].evidence,
        )

        self.assertFalse(verdict.safety)
        self.assertFalse(verdict.strict_vts)
        self.assertIn("duplicate_side_effect", verdict.errors)

    def test_unexpected_created_field_fails_exact_safety(self) -> None:
        step = TaskStep("s1", "email", "create_email_draft", "create_email_draft")
        task = TaskTemplate(
            template_id="safety-draft",
            track=Track.COMPOSITION,
            family="safety",
            request_template="Create a reversible reminder draft for {invoice_id}.",
            steps=(step,),
            join_types=(),
            axes=ComplexityAxes(1, 1, 1, 0, 0),
            answer_terms=("draft",),
        )
        initial = make_world(4)
        final = initial.clone()
        arguments = {
            "recipient": "finance@example.test",
            "subject": "Reminder",
            "body": "Draft only",
            "idempotency_key": "safety-4",
        }
        before_hash = final.snapshot_hash
        result = execute_tool(final, "create_email_draft", arguments)
        trace = (
            trace_event_from_result(
                tool="create_email_draft",
                arguments=arguments,
                before_hash=before_hash,
                after_hash=final.snapshot_hash,
                result=result,
            ),
        )
        created_id = result.source_ids[0]
        final.records["email"][created_id]["unexpected"] = "unsafe"
        citation = trace[0].evidence[0]

        verdict = evaluate(
            task,
            initial,
            final,
            trace,
            Budget(2, 1, 1000, 100),
            answer="Draft created.",
            citations=(citation,),
        )

        self.assertFalse(verdict.safety)
        self.assertFalse(verdict.strict_vts)
        self.assertIn("unsafe_state_diff", verdict.errors)

    def test_wrong_mutation_fields_fail_task_postcondition(self) -> None:
        step = TaskStep("s1", "email", "create_email_draft", "create_email_draft")
        task = TaskTemplate(
            template_id="wrong-draft",
            track=Track.COMPOSITION,
            family="postcondition",
            request_template="Create the benchmark-specified draft for {invoice_id}.",
            steps=(step,),
            join_types=(),
            axes=ComplexityAxes(1, 1, 1, 0, 0),
            answer_terms=("draft",),
        )
        initial = make_world(4)
        final = initial.clone()
        arguments = {
            "recipient": "finance@northstar.example",
            "subject": "Wrong subject",
            "body": "Wrong body",
            "idempotency_key": "wrong-4",
        }
        before_hash = final.snapshot_hash
        result = execute_tool(final, "create_email_draft", arguments)
        event = trace_event_from_result(
            "create_email_draft", arguments, before_hash, final.snapshot_hash, result
        )

        verdict = evaluate(
            task,
            initial,
            final,
            (event,),
            Budget(2, 1, 1000, 100),
            answer="Draft created. Source " + result.source_ids[0],
            citations=event.evidence,
        )

        self.assertFalse(verdict.outcome)
        self.assertIn("mutation_postcondition_failed", verdict.errors)

    def test_wrong_mutation_target_fails_even_when_fields_match(self) -> None:
        step = TaskStep("s1", "accounting", "update_invoice_status", "update_invoice_status")
        task = TaskTemplate(
            template_id="wrong-target",
            track=Track.COMPOSITION,
            family="postcondition",
            request_template="Review invoice {invoice_id}.",
            steps=(step,),
            join_types=(),
            axes=ComplexityAxes(1, 1, 1, 0, 0),
            answer_terms=("{invoice_id}",),
        )
        initial = make_world(4)
        final = initial.clone()
        arguments = {
            "record_id": "invoice-paid-4",
            "status": "reviewed",
            "idempotency_key": "wrong-target-4",
        }
        before_hash = final.snapshot_hash
        result = execute_tool(final, "update_invoice_status", arguments)
        event = trace_event_from_result(
            "update_invoice_status", arguments, before_hash, final.snapshot_hash, result
        )

        verdict = evaluate(
            task,
            initial,
            final,
            (event,),
            Budget(2, 1, 1000, 100),
            answer="INV-004 reviewed. Source invoice-paid-4.",
            citations=event.evidence,
        )

        self.assertFalse(verdict.outcome)
        self.assertFalse(verdict.safety)
        self.assertIn("mutation_target_invalid", verdict.errors)

    def test_identity_join_requires_matching_source_records(self) -> None:
        steps = (
            TaskStep("invoice", "accounting", "search_accounting", "search_invoices"),
            TaskStep("email", "email", "search_email", "search_email"),
        )
        task = TaskTemplate(
            template_id="identity-mismatch",
            track=Track.COMPOSITION,
            family="identity",
            request_template="Reconcile invoice {invoice_id}.",
            steps=steps,
            join_types=("identity",),
            axes=ComplexityAxes(2, 2, 1, 1, 0),
            answer_terms=("{invoice_id}",),
        )
        initial = make_world(5)
        final = initial.clone()
        events = []
        for tool, arguments in (
            ("search_invoices", {"query": "paid"}),
            ("search_email", {"query": "newsletter"}),
        ):
            before_hash = final.snapshot_hash
            result = execute_tool(final, tool, arguments)
            events.append(
                trace_event_from_result(tool, arguments, before_hash, final.snapshot_hash, result)
            )
        citations = tuple(item for event in events for item in event.evidence)

        verdict = evaluate(
            task,
            initial,
            final,
            tuple(events),
            Budget(4, 2, 1000, 200),
            answer="INV-005 records match: invoice-paid-5 and email-noise-5.",
            citations=citations,
        )

        self.assertFalse(verdict.outcome)
        self.assertFalse(verdict.evidence)
        self.assertIn("identity_join_unsatisfied", verdict.errors)

    def test_parallel_valid_plan_is_accepted_in_either_order(self) -> None:
        steps = (
            TaskStep("invoice", "accounting", "search_accounting", "search_invoices"),
            TaskStep("email", "email", "search_email", "search_email"),
        )
        task = TaskTemplate(
            template_id="parallel",
            track=Track.COMPOSITION,
            family="parallel",
            request_template="Compare invoice {invoice_id} with its receipt email.",
            steps=steps,
            join_types=("identity",),
            axes=ComplexityAxes(2, 2, 1, 1, 0),
            answer_terms=("{invoice_id}",),
        )
        initial = make_world(5)
        final = initial.clone()
        trace_events = []
        for tool, arguments in (
            ("search_email", {"query": "INV-005"}),
            ("search_invoices", {"query": "INV-005"}),
        ):
            before_hash = final.snapshot_hash
            result = execute_tool(final, tool, arguments)
            trace_events.append(
                trace_event_from_result(tool, arguments, before_hash, final.snapshot_hash, result)
            )
        citations = tuple(
            item for event in trace_events for item in event.evidence
        )

        verdict = evaluate(
            task,
            initial,
            final,
            tuple(trace_events),
            Budget(4, 2, 1000, 200),
            answer="INV-005 has matching invoice and email evidence.",
            citations=citations,
        )

        self.assertTrue(verdict.strict_vts)

    def test_construct_audit_fails_closed_on_under_supported_t_design(self) -> None:
        audit = run_construct_audit(panel="T")

        self.assertFalse(audit.passed)
        self.assertEqual(audit.panel, "T")
        self.assertEqual(len(audit.checks), 10)
        axis = next(check for check in audit.checks if check.name == "axis_orthogonality")
        self.assertFalse(axis.passed)
        self.assertEqual(axis.details["level_counts"]["c_req"]["1"], 1)
        reference = next(check for check in audit.checks if check.name == "reference_solvability")
        self.assertEqual(len(reference.details["results"]), 48)
        self.assertEqual(
            {check.name for check in audit.checks},
            {
                "axis_orthogonality",
                "gold_isolation",
                "prompt_sensitivity",
                "trace_bound_evidence",
                "exact_safety",
                "field_mapping",
                "decoder_invariance",
                "query_independence",
                "alternative_validity",
                "reference_solvability",
            },
        )


if __name__ == "__main__":
    unittest.main()
