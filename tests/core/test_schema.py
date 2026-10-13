from __future__ import annotations

import dataclasses
import json
import unittest

from bottleneckbench.core.schema import (
    Condition,
    Representation,
    RunStatus,
    Track,
    canonical_json,
    normalize_floats,
    stable_hash,
)


class SchemaTest(unittest.TestCase):
    def test_normalize_floats_is_recursive_and_removes_negative_zero(self) -> None:
        self.assertEqual(
            normalize_floats({"x": [0.30000000000000004, -1e-16]}),
            {"x": [0.3, 0.0]},
        )

    def test_canonical_json_sorts_keys_and_uses_compact_utf8(self) -> None:
        value = {"z": 1, "a": "发票", "nested": {"b": 2, "a": 1}}

        encoded = canonical_json(value)

        self.assertEqual(encoded, '{"a":"发票","nested":{"a":1,"b":2},"z":1}')
        self.assertEqual(json.loads(encoded), value)

    def test_stable_hash_is_order_independent_and_value_sensitive(self) -> None:
        left = {"registry": 128, "task": "D01"}
        reordered = {"task": "D01", "registry": 128}
        changed = {"task": "D01", "registry": 512}

        self.assertEqual(stable_hash(left), stable_hash(reordered))
        self.assertNotEqual(stable_hash(left), stable_hash(changed))
        self.assertEqual(len(stable_hash(left)), 64)

    def test_condition_id_binds_every_experimental_field(self) -> None:
        base = Condition(
            task_id="D01-s1",
            track=Track.DISCOVERY,
            model="agnes-2.5-flash",
            harness="flat-retrieval",
            representation=Representation.RAW,
            registry_size=128,
            exposure_size=16,
            seed=7,
        )
        same = dataclasses.replace(base)
        changed = dataclasses.replace(base, exposure_size=32)

        self.assertEqual(base.condition_id, same.condition_id)
        self.assertNotEqual(base.condition_id, changed.condition_id)
        self.assertEqual(base.to_json()["track"], "discovery")
        self.assertEqual(base.to_json()["representation"], "raw")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            base.seed = 9  # type: ignore[misc]

    def test_status_and_representation_values_are_manifest_safe(self) -> None:
        self.assertEqual(RunStatus.CAPABILITY_UNAVAILABLE.value, "capability_unavailable")
        self.assertEqual(Representation.GENERIC_COMPACT.value, "generic_compact")
        self.assertEqual(Representation.TYPED.value, "typed")
        self.assertEqual(Representation.LINKED.value, "linked")


if __name__ == "__main__":
    unittest.main()
