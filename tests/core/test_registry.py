from __future__ import annotations

import unittest

from bottleneckbench.core.registry import (
    build_registry,
    oracle_exposure,
    retrieve_flat,
    retrieve_hierarchical,
)
from bottleneckbench.core.tasks import development_templates
from bottleneckbench.core.world import core_tools


class RegistryTest(unittest.TestCase):
    def test_registry_levels_are_nested_master_prefixes(self) -> None:
        registries = {size: build_registry(size, seed=17) for size in (32, 128, 512, 2048)}

        for smaller, larger in ((32, 128), (128, 512), (512, 2048)):
            self.assertEqual(
                [tool.name for tool in registries[smaller]],
                [tool.name for tool in registries[larger][:smaller]],
            )
        self.assertEqual(len({tool.name for tool in registries[2048]}), 2048)

    def test_core_tools_are_unchanged_and_distractor_types_are_present(self) -> None:
        registry = build_registry(128, seed=2)
        expected = {tool.name: tool for tool in core_tools()}
        actual = {tool.name: tool for tool in registry[: len(expected)]}

        self.assertEqual(actual, expected)
        self.assertEqual(
            {tool.distractor_kind for tool in registry[len(expected) :]},
            {"random", "same_domain", "adversarial"},
        )
        self.assertTrue(all(not tool.executable for tool in registry[len(expected) :]))

    def test_flat_retrieval_exposes_matching_required_tool_at_exact_limit(self) -> None:
        registry = build_registry(32, seed=9)

        exposure = retrieve_flat(
            "Find the overdue invoice and report its status",
            registry,
            limit=16,
        )

        self.assertEqual(len(exposure.tools), 16)
        self.assertIn("search_invoices", exposure.tool_names)
        self.assertEqual(len(set(exposure.tool_names)), 16)

    def test_calibrated_hard_negatives_create_registry_pressure_at_fixed_exposure(self) -> None:
        query = "Find the overdue invoice and report its status"

        small_flat = retrieve_flat(query, build_registry(32, seed=9), limit=8)
        large_flat = retrieve_flat(query, build_registry(128, seed=9), limit=8)
        small_hierarchical = retrieve_hierarchical(query, build_registry(32, seed=9), limit=8)
        large_hierarchical = retrieve_hierarchical(query, build_registry(128, seed=9), limit=8)

        self.assertIn("search_invoices", small_flat.tool_names)
        self.assertIn("search_invoices", small_hierarchical.tool_names)
        self.assertNotIn("search_invoices", large_flat.tool_names)
        self.assertNotIn("search_invoices", large_hierarchical.tool_names)

    def test_hierarchical_retrieval_is_deterministic_and_exact(self) -> None:
        registry = build_registry(32, seed=4)
        query = "Find the receipt email associated with an invoice"

        first = retrieve_hierarchical(query, registry, limit=8)
        second = retrieve_hierarchical(query, registry, limit=8)

        self.assertEqual(first, second)
        self.assertEqual(len(first.tools), 8)
        self.assertIn("search_email", first.tool_names)
        self.assertEqual(first.selected_connectors[0], "email")

    def test_nonsense_request_exposes_no_controlled_core_tool(self) -> None:
        registry = build_registry(32, seed=3)
        core_names = {tool.name for tool in core_tools()}

        flat = retrieve_flat("quartz nebula nonsense", registry, limit=8)
        hierarchical = retrieve_hierarchical("quartz nebula nonsense", registry, limit=8)

        self.assertFalse(set(flat.tool_names) & core_names)
        self.assertFalse(set(hierarchical.tool_names) & core_names)

    def test_composition_oracle_menu_is_task_invariant_at_fixed_registry(self) -> None:
        tasks = [item for item in development_templates() if item.track.value == "composition"]
        low_k = min(tasks, key=lambda item: item.axes.k_req)
        high_k = max(tasks, key=lambda item: item.axes.k_req)
        registry = build_registry(128, seed=6)

        low_exposure = oracle_exposure(low_k, registry, limit=16)
        high_exposure = oracle_exposure(high_k, registry, limit=16)

        self.assertEqual(low_exposure.tool_names, high_exposure.tool_names)
        self.assertEqual(low_exposure.required_available, 1.0)
        self.assertEqual(high_exposure.required_available, 1.0)
        self.assertEqual(sum(tool.distractor_kind == "core" for tool in low_exposure.tools), 12)
        self.assertEqual(sum(tool.distractor_kind == "random" for tool in low_exposure.tools), 4)

    def test_oracle_exposure_contains_every_required_tool_and_matched_fillers(self) -> None:
        task = [
            item
            for item in development_templates()
            if item.track.value == "composition" and len(item.required_tools) >= 4
        ][0]
        registry = build_registry(128, seed=6)

        exposure = oracle_exposure(task, registry, limit=16)

        self.assertEqual(len(exposure.tools), 16)
        self.assertTrue(set(task.required_tools) <= set(exposure.tool_names))
        self.assertTrue(exposure.oracle)
        self.assertEqual(exposure.required_available, 1.0)

    def test_invalid_registry_and_exposure_sizes_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least"):
            build_registry(8, seed=1)
        with self.assertRaisesRegex(ValueError, "positive"):
            retrieve_flat("invoice", build_registry(32, seed=1), limit=0)
        with self.assertRaisesRegex(ValueError, "exceeds"):
            retrieve_flat("invoice", build_registry(32, seed=1), limit=33)


if __name__ == "__main__":
    unittest.main()
