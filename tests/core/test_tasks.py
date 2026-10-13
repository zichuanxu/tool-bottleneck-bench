from __future__ import annotations

import unittest

from bottleneckbench.core.schema import Track
from bottleneckbench.core.tasks import (
    candidate_templates,
    compute_axes,
    design_diagnostics,
    development_templates,
    sample_panel,
)


class TaskGrammarTest(unittest.TestCase):
    def test_development_panel_has_eight_tasks_per_primary_track(self) -> None:
        tasks = development_templates()

        self.assertEqual(len(tasks), 16)
        self.assertEqual(sum(task.track is Track.DISCOVERY for task in tasks), 8)
        self.assertEqual(sum(task.track is Track.COMPOSITION for task in tasks), 8)
        self.assertEqual(len({task.template_id for task in tasks}), len(tasks))

    def test_discovery_tasks_fix_composition_axes(self) -> None:
        discovery = [task for task in development_templates() if task.track is Track.DISCOVERY]

        for task in discovery:
            self.assertEqual(task.axes.c_req, 1)
            self.assertEqual(task.axes.k_req, 1)
            self.assertEqual(task.axes.depth, 1)
            self.assertEqual(task.axes.j_identity, 0)
            self.assertEqual(task.axes.j_control, 0)
            self.assertEqual(len(task.required_tools), 1)

    def test_composition_axes_are_recomputed_from_hidden_graph(self) -> None:
        composition = [task for task in development_templates() if task.track is Track.COMPOSITION]

        for task in composition:
            self.assertEqual(compute_axes(task.steps, task.join_types), task.axes)
            step_ids = {step.step_id for step in task.steps}
            for step in task.steps:
                self.assertTrue(set(step.depends_on) <= step_ids)
                self.assertNotIn(step.step_id, step.depends_on)

    def test_public_payload_contains_natural_language_but_no_hidden_gold(self) -> None:
        task = development_templates()[9]

        payload = task.public_payload(world_seed=3)

        self.assertEqual(set(payload), {"task_id", "request"})
        self.assertIsInstance(payload["request"], str)
        self.assertNotIn(task.template_id, payload["task_id"])
        serialized = str(payload).lower()
        for forbidden in ("required_tools", "required_capabilities", "depends_on", "gold", "tdg"):
            self.assertNotIn(forbidden, serialized)

    def test_composition_requests_entail_every_obligation_without_tool_names(self) -> None:
        composition = [task for task in development_templates() if task.track is Track.COMPOSITION]

        for task in composition:
            request = task.render_request(9)
            for index in range(1, len(task.steps) + 1):
                self.assertIn(f"Obligation {index}:", request)
            for step in task.steps:
                self.assertNotIn(step.tool_name, request)
            for step in task.steps:
                for parent in step.depends_on:
                    parent_index = next(
                        index for index, candidate in enumerate(task.steps, start=1) if candidate.step_id == parent
                    )
                    child_index = next(
                        index for index, candidate in enumerate(task.steps, start=1) if candidate.step_id == step.step_id
                    )
                    self.assertIn(
                        f"Complete obligation {parent_index} before obligation {child_index}",
                        request,
                    )

    def test_mutation_postconditions_are_explicit_in_request_and_machine_readable(self) -> None:
        composition = [task for task in development_templates() if task.track is Track.COMPOSITION]

        for task in composition:
            request = task.render_request(13)
            expectations = task.expected_mutations(13)
            required_mutations = {
                step.tool_name
                for step in task.steps
                if step.tool_name.startswith(("create_", "update_"))
            }
            self.assertEqual(set(expectations), required_mutations)
            for fields in expectations.values():
                for value in fields.values():
                    self.assertIn(str(value), request)

    def test_development_composition_design_passes_orthogonality_gates(self) -> None:
        composition = [task for task in development_templates() if task.track is Track.COMPOSITION]

        diagnostics = design_diagnostics(composition)

        self.assertEqual(diagnostics.rank, 5)
        self.assertLessEqual(diagnostics.condition_number, 5.0)
        self.assertLessEqual(max(diagnostics.vifs.values()), 2.0)

    def test_candidate_panel_has_response_sealed_shape_and_full_rank(self) -> None:
        tasks = candidate_templates()
        composition = [task for task in tasks if task.track is Track.COMPOSITION]

        self.assertEqual(len(tasks), 48)
        self.assertEqual(len(composition), 32)
        self.assertEqual(sum(task.track is Track.DISCOVERY for task in tasks), 16)
        diagnostics = design_diagnostics(composition)
        self.assertEqual(diagnostics.rank, 5)
        self.assertLessEqual(diagnostics.condition_number, 5.0)
        self.assertLessEqual(max(diagnostics.vifs.values()), 2.0)

    def test_panel_sampling_is_seed_bound_and_manifest_hashed(self) -> None:
        first = sample_panel("T", seed=20260803)
        same = sample_panel("T", seed=20260803)
        different = sample_panel("T", seed=20260804)

        self.assertEqual(first.manifest_hash, same.manifest_hash)
        self.assertNotEqual(first.manifest_hash, different.manifest_hash)
        self.assertEqual(len(first.tasks), 48)
        self.assertEqual(first.seed, 20260803)


if __name__ == "__main__":
    unittest.main()
