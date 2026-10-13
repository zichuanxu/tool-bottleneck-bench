from __future__ import annotations

import unittest

from bottleneckbench.core.world import (
    PermissionDenied,
    core_tools,
    deep_diff,
    execute_tool,
    make_world,
)


class WorldTest(unittest.TestCase):
    def test_equal_seed_replays_identical_snapshot_and_hash(self) -> None:
        first = make_world(11)
        second = make_world(11)

        self.assertEqual(first.snapshot(), second.snapshot())
        self.assertEqual(first.snapshot_hash, second.snapshot_hash)
        self.assertNotEqual(first.snapshot_hash, make_world(12).snapshot_hash)

    def test_clone_mutation_does_not_change_initial_world(self) -> None:
        initial = make_world(3)
        run_world = initial.clone()

        execute_tool(
            run_world,
            "create_email_draft",
            {
                "recipient": "finance@example.test",
                "subject": "Invoice follow-up",
                "body": "Please review invoice INV-003.",
                "idempotency_key": "draft-3",
            },
        )

        self.assertEqual(len(initial.records["email"]), 4)
        self.assertEqual(len(run_world.records["email"]), 5)
        self.assertEqual(initial.snapshot_hash, make_world(3).snapshot_hash)

    def test_mutating_tool_requires_connector_permission(self) -> None:
        world = make_world(5)
        world.permissions.remove("email.write")

        with self.assertRaisesRegex(PermissionDenied, "email.write"):
            execute_tool(
                world,
                "create_email_draft",
                {
                    "recipient": "owner@example.test",
                    "subject": "Blocked",
                    "body": "No permission",
                    "idempotency_key": "blocked-5",
                },
            )

        self.assertEqual(len(world.records["email"]), 4)
        self.assertEqual(world.events, [])

    def test_idempotency_key_prevents_duplicate_side_effect(self) -> None:
        world = make_world(8)
        arguments = {
            "recipient": "finance@example.test",
            "subject": "Invoice follow-up",
            "body": "Please review invoice INV-008.",
            "idempotency_key": "draft-8",
        }

        first = execute_tool(world, "create_email_draft", arguments)
        second = execute_tool(world, "create_email_draft", arguments)

        self.assertEqual(first.output, second.output)
        self.assertEqual(first.receipt_id, second.receipt_id)
        self.assertFalse(second.mutated)
        self.assertEqual(len(world.records["email"]), 5)
        self.assertEqual(len(world.events), 1)

    def test_idempotency_keys_are_scoped_to_the_mutating_tool(self) -> None:
        world = make_world(15)
        shared_key = "review-inv015-20260920"

        calendar = execute_tool(
            world,
            "create_calendar_event",
            {
                "title": "Review INV-015",
                "start": "2026-09-20T10:00:00Z",
                "end": "2026-09-20T10:30:00Z",
                "idempotency_key": shared_key,
            },
        )
        accounting = execute_tool(
            world,
            "update_invoice_status",
            {
                "record_id": "invoice-target-15",
                "status": "reviewed",
                "idempotency_key": shared_key,
            },
        )

        self.assertTrue(calendar.mutated)
        self.assertTrue(accounting.mutated)
        self.assertNotEqual(calendar.receipt_id, accounting.receipt_id)
        self.assertEqual(world.records["accounting"]["invoice-target-15"]["status"], "reviewed")
        self.assertEqual(len(world.events), 2)

    def test_search_is_read_only_and_returns_source_versions(self) -> None:
        world = make_world(9)
        before = world.snapshot_hash

        result = execute_tool(world, "search_invoices", {"query": "overdue"})

        self.assertTrue(result.ok)
        self.assertFalse(result.mutated)
        self.assertEqual(before, world.snapshot_hash)
        self.assertGreaterEqual(len(result.output["records"]), 1)
        record = result.output["records"][0]
        self.assertIn("record_id", record)
        self.assertIn("version", record)
        self.assertEqual(result.source_ids, (record["record_id"],))

    def test_deep_diff_reports_exact_unexpected_created_field(self) -> None:
        before = {"email": {}}
        after = {
            "email": {
                "draft-1": {
                    "record_id": "draft-1",
                    "subject": "Expected",
                    "unexpected": "unsafe",
                }
            }
        }

        paths = {entry.path for entry in deep_diff(before, after)}

        self.assertIn("/email/draft-1", paths)
        self.assertIn("/email/draft-1/unexpected", paths)

    def test_core_tools_cover_four_connectors_with_unique_names(self) -> None:
        tools = core_tools()

        self.assertEqual({tool.connector for tool in tools}, {"email", "files", "calendar", "accounting"})
        self.assertEqual(len({tool.name for tool in tools}), len(tools))
        self.assertTrue(any(tool.side_effect for tool in tools))
        self.assertTrue(any(not tool.side_effect for tool in tools))


if __name__ == "__main__":
    unittest.main()
