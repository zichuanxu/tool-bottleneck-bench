from __future__ import annotations

import unittest

from bottleneckbench.core.data import compile_data
from bottleneckbench.core.experiments import build_transfer_plan
from bottleneckbench.core.evaluation import AuditCheck, ConstructAudit
from bottleneckbench.core.registry import build_transfer_registry, oracle_exposure
from bottleneckbench.core.schema import Representation
from bottleneckbench.core.tasks import transfer_templates
from bottleneckbench.core.world import (
    core_tools,
    execute_tool,
    make_transfer_world,
    transfer_tools,
)


class TransferSliceTest(unittest.TestCase):
    def test_transfer_world_adds_crm_and_tasks_without_changing_core_tools(self) -> None:
        world = make_transfer_world(21)

        self.assertEqual(set(world.records), {"email", "files", "calendar", "accounting", "crm", "tasks"})
        self.assertEqual(len(core_tools()), 12)
        self.assertEqual(len(transfer_tools()), 6)
        crm = execute_tool(world, "search_crm", {"query": "Northstar"})
        tasks = execute_tool(world, "search_tasks", {"query": "INV-021"})
        self.assertTrue(crm.source_ids)
        self.assertTrue(tasks.source_ids)

    def test_transfer_data_representations_preserve_all_six_connectors(self) -> None:
        world = make_transfer_world(22)

        typed = compile_data(world, Representation.TYPED)
        linked = compile_data(world, Representation.LINKED)

        connectors = {item["source"]["connector"] for item in typed.objects}
        self.assertEqual(connectors, {"email", "files", "calendar", "accounting", "crm", "tasks"})
        self.assertEqual(typed.world_hash, linked.world_hash)
        self.assertTrue(any(edge["relation"] == "same_invoice" for edge in linked.links))

    def test_transfer_panel_has_twelve_natural_language_templates(self) -> None:
        tasks = transfer_templates()

        self.assertEqual(len(tasks), 12)
        self.assertEqual(len({task.template_id for task in tasks}), 12)
        for task in tasks:
            request = task.render_request(31)
            self.assertNotIn("search_crm", request)
            self.assertNotIn("search_tasks", request)
            self.assertTrue(set(task.required_tools) <= {tool.name for tool in core_tools() + transfer_tools()})

    def test_transfer_oracle_exposes_transfer_tools_in_a_fixed_menu(self) -> None:
        tasks = transfer_templates()
        registry = build_transfer_registry(128, seed=31)

        first = oracle_exposure(tasks[0], registry, limit=20)
        last = oracle_exposure(tasks[-1], registry, limit=20)

        self.assertEqual(first.tool_names, last.tool_names)
        self.assertEqual(first.required_available, 1.0)
        self.assertEqual(last.required_available, 1.0)
        self.assertTrue({tool.name for tool in transfer_tools()} <= set(first.tool_names))

    def test_transfer_plan_has_48_exploratory_cells(self) -> None:
        audit = ConstructAudit(
            checks=tuple(AuditCheck(f"gate-{index}", True, {}) for index in range(10)),
            panel="U",
        )
        plan = build_transfer_plan(
            audit,
            model="agnes-2.5-flash",
            selector_seed=20260808,
            frozen_at="2026-08-05T00:00:00Z",
        )

        self.assertEqual(plan.panel, "U")
        self.assertEqual(len(plan.conditions), 48)
        self.assertEqual(len({condition.condition_id for condition in plan.conditions}), 48)


if __name__ == "__main__":
    unittest.main()
