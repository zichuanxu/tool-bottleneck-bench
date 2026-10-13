from __future__ import annotations

from copy import deepcopy
import inspect
import unittest

from bottleneckbench.core.data import (
    compile_data,
    provenance_coverage,
    render_observation,
    source_facts,
    validate_compiled_data,
)
from bottleneckbench.core.schema import Representation, canonical_json
from bottleneckbench.core.world import execute_tool, make_world


class DataReadinessTest(unittest.TestCase):
    def test_compiler_interface_cannot_receive_a_query_or_task(self) -> None:
        parameters = tuple(inspect.signature(compile_data).parameters)

        self.assertEqual(parameters, ("world", "representation"))

    def test_all_representations_preserve_the_same_source_facts(self) -> None:
        world = make_world(7)
        compiled = {
            representation: compile_data(world, representation)
            for representation in Representation
        }
        expected = source_facts(compiled[Representation.RAW])

        for representation, data in compiled.items():
            with self.subTest(representation=representation):
                self.assertEqual(source_facts(data), expected)
                self.assertEqual(data.world_hash, world.snapshot_hash)
                self.assertEqual(provenance_coverage(data), 1.0)

    def test_generic_compaction_is_query_independent_and_shorter_than_raw(self) -> None:
        world = make_world(13)

        raw = compile_data(world, Representation.RAW)
        compact = compile_data(world, Representation.GENERIC_COMPACT)
        repeated = compile_data(world.clone(), Representation.GENERIC_COMPACT)

        self.assertEqual(compact.compiled_hash, repeated.compiled_hash)
        self.assertLess(len(canonical_json(compact.objects)), len(canonical_json(raw.objects)))

    def test_typed_objects_have_stable_types_and_field_provenance(self) -> None:
        typed = compile_data(make_world(2), Representation.TYPED)

        object_types = {item["type"] for item in typed.objects}
        self.assertIn("Invoice", object_types)
        self.assertIn("EmailMessage", object_types)
        self.assertIn("Document", object_types)
        self.assertIn("CalendarEvent", object_types)
        self.assertTrue(all("source" in item and "fields" in item for item in typed.objects))
        self.assertTrue(typed.provenance)

    def test_linked_objects_add_only_provenance_backed_relations(self) -> None:
        world = make_world(4)
        typed = compile_data(world, Representation.TYPED)
        linked = compile_data(world, Representation.LINKED)

        self.assertEqual(source_facts(linked), source_facts(typed))
        self.assertTrue(linked.links)
        invoice_edges = [edge for edge in linked.links if edge["relation"] == "same_invoice"]
        self.assertGreaterEqual(len(invoice_edges), 3)
        for edge in invoice_edges:
            self.assertEqual(len(edge["source_record_ids"]), 2)
            self.assertNotEqual(edge["source_record_ids"][0], edge["source_record_ids"][1])

    def test_linked_observation_exposes_cross_source_relation_without_copying_remote_fact(self) -> None:
        world = make_world(4)
        result = execute_tool(world, "search_invoices", {"query": "INV-004"})
        linked = compile_data(world, Representation.LINKED)

        rendered = render_observation(result, linked)

        self.assertTrue(rendered["links"])
        remote_ids = {
            source_id
            for edge in rendered["links"]
            for source_id in edge["source_record_ids"]
            if source_id not in result.source_ids
        }
        self.assertIn("email-receipt-4", remote_ids)
        self.assertNotIn("email-receipt-4", rendered["source_ids"])
        self.assertEqual(len(rendered["objects"]), 1)

    def test_field_value_swap_fails_closed(self) -> None:
        world = make_world(5)
        typed = compile_data(world, Representation.TYPED)
        tampered = deepcopy(typed)
        target = next(item for item in tampered.objects if item["type"] == "Invoice")
        target["fields"]["status"], target["fields"]["currency"] = (
            target["fields"]["currency"],
            target["fields"]["status"],
        )

        with self.assertRaisesRegex(ValueError, "field-value mapping"):
            validate_compiled_data(world, tampered)

    def test_model_visible_observations_include_dictionary_and_scoped_provenance(self) -> None:
        world = make_world(6)
        result = execute_tool(world, "search_invoices", {"query": "INV-006"})

        compact = render_observation(
            result, compile_data(world, Representation.GENERIC_COMPACT)
        )
        typed = render_observation(result, compile_data(world, Representation.TYPED))

        self.assertEqual(compact["field_dictionary"]["st"], "status")
        self.assertEqual(compact["field_dictionary"]["n"], "invoice_id")
        self.assertTrue(compact["provenance"])
        self.assertTrue(typed["provenance"])
        for item in compact["provenance"] + typed["provenance"]:
            self.assertEqual(item["record_id"], "invoice-target-6")
            self.assertIn("field", item)

    def test_render_observation_preserves_tool_scope_across_representations(self) -> None:
        world = make_world(6)
        result = execute_tool(world, "search_invoices", {"query": "overdue"})
        rendered_ids = []

        for representation in Representation:
            compiled = compile_data(world, representation)
            rendered = render_observation(result, compiled)
            self.assertEqual(rendered["tool"], "search_invoices")
            self.assertEqual(rendered["source_ids"], list(result.source_ids))
            self.assertEqual(len(rendered["objects"]), 1)
            rendered_ids.append(tuple(rendered["source_ids"]))

        self.assertEqual(len(set(rendered_ids)), 1)


if __name__ == "__main__":
    unittest.main()
