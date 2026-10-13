from __future__ import annotations

import unittest

from bottleneckbench.design import (
    CapacityCondition,
    Split,
    WorkflowLoad,
    build_families,
    build_world,
    certify_minimal_program,
    compile_task,
    source_route,
    static_construct_audit,
    visible_menu,
)
from bottleneckbench.core.schema import Representation


class SemanticTwinDesignTest(unittest.TestCase):
    def test_splits_are_disjoint_and_have_registered_family_counts(self) -> None:
        development = build_families(Split.DEVELOPMENT)
        main = build_families(Split.MAIN)
        transfer = build_families(Split.TRANSFER)

        self.assertEqual((len(development), len(main), len(transfer)), (12, 36, 12))
        ids = [family.family_id for family in (*development, *main, *transfer)]
        cases = [family.case_id for family in (*development, *main, *transfer)]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(cases), len(set(cases)))

    def test_seed_offset_is_opt_in_and_shifts_seeds(self) -> None:
        default = build_families(Split.DEVELOPMENT)
        same = build_families(Split.DEVELOPMENT, seed_offset=0)
        shifted = build_families(Split.DEVELOPMENT, seed_offset=1000)

        self.assertEqual([f.seed for f in default], [f.seed for f in same])
        self.assertEqual(
            [f.seed + 1000 for f in default], [f.seed for f in shifted]
        )
        self.assertNotEqual([f.seed for f in default], [f.seed for f in shifted])

    def test_workflow_twins_preserve_public_semantics_and_operational_menu(self) -> None:
        family = build_families(Split.DEVELOPMENT)[0]
        tasks = tuple(compile_task(family, level) for level in WorkflowLoad)

        self.assertEqual(len({task.semantic_signature for task in tasks}), 1)
        self.assertEqual(len({task.request for task in tasks}), 1)
        self.assertEqual(len({task.base_tool_names for task in tasks}), 1)
        self.assertEqual(
            [certify_minimal_program(task).minimum_calls for task in tasks],
            [2, 4, 6],
        )

    def test_action_twins_add_one_call_without_changing_terminal_contract(self) -> None:
        family = next(
            item
            for item in build_families(Split.DEVELOPMENT)
            if item.action_required
        )
        tasks = tuple(compile_task(family, level) for level in WorkflowLoad)

        self.assertEqual(
            [certify_minimal_program(task).minimum_calls for task in tasks],
            [3, 5, 7],
        )
        self.assertEqual(
            len(
                {
                    str(task.action.to_json(logical_target=True))
                    for task in tasks
                    if task.action
                }
            ),
            1,
        )

    def test_main_balances_positive_negative_and_no_action_cases(self) -> None:
        families = build_families(Split.MAIN)

        self.assertEqual(sum(family.positive_outcome for family in families), 18)
        action_families = [family for family in families if family.action_name]
        self.assertEqual(sum(family.action_required for family in action_families), 9)
        for intent in {family.intent for family in families}:
            outcomes = {
                family.positive_outcome for family in families if family.intent == intent
            }
            self.assertEqual(outcomes, {False, True})

    def test_branching_high_workflow_accepts_multiple_minimal_orders(self) -> None:
        family = next(item for item in build_families(Split.DEVELOPMENT) if item.branching)
        task = compile_task(family, WorkflowLoad.HIGH)
        certificate = certify_minimal_program(task)

        self.assertTrue(certificate.passed)
        self.assertGreater(certificate.minimal_plan_count, 1)
        self.assertLess(certificate.critical_depth, certificate.minimum_calls)

    def test_public_prompt_contains_no_hidden_ids_tokens_or_tool_names(self) -> None:
        task = compile_task(build_families(Split.DEVELOPMENT)[5], WorkflowLoad.HIGH)
        lowered = task.request.casefold()

        for node in task.nodes:
            self.assertNotIn(node.record_id.casefold(), lowered)
            for query in node.next_queries:
                self.assertNotIn(query.casefold(), lowered)
        for step in task.program:
            self.assertNotIn(step.tool_name.casefold(), lowered)
        self.assertNotIn("obligation 1", lowered)
        self.assertNotIn("required order", lowered)

    def test_public_prompt_states_the_exact_source_contract(self) -> None:
        task = compile_task(build_families(Split.DEVELOPMENT)[0], WorkflowLoad.LOW)

        self.assertIn("Required source route:", task.request)
        self.assertIn("matches all three route coordinates", task.request)
        self.assertTrue(
            all(value in task.request for _, value in source_route(task.family))
        )
        self.assertIn("exclude newly created action outputs or receipts", task.request)

    def test_world_uses_opaque_ids_and_preserves_canonical_fact_values(self) -> None:
        family = build_families(Split.DEVELOPMENT)[2]
        for level in WorkflowLoad:
            task = compile_task(family, level)
            world = build_world(task)
            observed = {}
            for node in task.nodes:
                record = world.records[node.connector][node.record_id]
                self.assertTrue(node.record_id.startswith("src-"))
                for name in node.facts:
                    observed[name] = record[name]
            self.assertEqual(observed, family.fact_map)

    def test_workflow_handoffs_publish_connector_and_exact_query(self) -> None:
        task = compile_task(build_families(Split.DEVELOPMENT)[0], WorkflowLoad.HIGH)
        world = build_world(task)
        first = world.records[task.nodes[0].connector][task.nodes[0].record_id]

        self.assertEqual(
            first["next_handoff"],
            {
                "connector": task.nodes[1].connector,
                "query": task.nodes[1].lookup_key,
            },
        )
        search_tools = [
            tool
            for tool in visible_menu(task, 8)
            if tool.name in task.base_tool_names and tool.name.startswith("search_")
        ]
        self.assertTrue(all("next_handoff" in tool.description for tool in search_tools))

    def test_visible_menu_is_nested_in_set_and_invariant_across_workflow_twins(self) -> None:
        family = build_families(Split.DEVELOPMENT)[3]
        tasks = tuple(compile_task(family, level) for level in WorkflowLoad)
        menus = {
            level: {tool.name for tool in visible_menu(task, 32)}
            for level, task in zip(WorkflowLoad, tasks, strict=True)
        }
        self.assertEqual(len({frozenset(menu) for menu in menus.values()}), 1)

        low = {tool.name for tool in visible_menu(tasks[0], 8)}
        medium = {tool.name for tool in visible_menu(tasks[0], 32)}
        high = {tool.name for tool in visible_menu(tasks[0], 128)}
        self.assertTrue(low < medium < high)
        self.assertTrue(set(tasks[0].required_tool_names) <= low)

    def test_static_construct_audit_passes_all_three_splits(self) -> None:
        for split in Split:
            with self.subTest(split=split):
                audit = static_construct_audit(split)
                self.assertTrue(audit["passed"], audit)
                self.assertEqual(
                    {check["name"] for check in audit["checks"]},
                    {
                        "semantic_twin_equivalence",
                        "minimal_program_certification",
                        "factor_support",
                        "decision_and_action_contract",
                        "response_source_contract",
                        "menu_intervention_isolation",
                        "source_route_near_neighbors",
                        "connector_fact_layout",
                        "gold_isolation",
                        "deterministic_reproducibility",
                    },
                )

    def test_condition_hash_binds_joint_cube_coordinates(self) -> None:
        task = compile_task(build_families(Split.DEVELOPMENT)[0], WorkflowLoad.LOW)
        condition = CapacityCondition(
            task_id=task.task_id,
            family_id=task.family.family_id,
            intent=task.family.intent,
            outcome_variant=task.family.outcome_variant,
            prompt_variant=task.family.prompt_variant,
            branching=task.family.branching,
            action_required=task.family.action_required,
            model="test-model",
            harness="react",
            workflow=task.workflow,
            representation=Representation.RAW,
            visible_tools=8,
            seed=task.family.seed,
        )
        changed = CapacityCondition(
            task_id=compile_task(task.family, WorkflowLoad.HIGH).task_id,
            family_id=task.family.family_id,
            intent=task.family.intent,
            outcome_variant=task.family.outcome_variant,
            prompt_variant=task.family.prompt_variant,
            branching=task.family.branching,
            action_required=task.family.action_required,
            model="test-model",
            harness="react",
            workflow=WorkflowLoad.HIGH,
            representation=Representation.RAW,
            visible_tools=8,
            seed=task.family.seed,
        )

        self.assertNotEqual(condition.condition_id, changed.condition_id)
        self.assertEqual(len(condition.condition_id), 64)


if __name__ == "__main__":
    unittest.main()
