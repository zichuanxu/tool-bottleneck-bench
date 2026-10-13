"""Hermetic adversarial checks for the write-free certificate selection boundary."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments import panel_refresh_22_certificates as c


class CertificateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def put(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(c.inventory.canonical(value) + b"\n")
        return c.inventory.bind(self.root, name)

    def envelope(self, schema, model="test/model"):
        result = dict.fromkeys(c.FIELDS[schema].split())
        result.update(schema=schema, model=model, passed=True, release_admitted=False)
        if "new_api_calls" in result:
            result["new_api_calls"] = 0
        return result

    def test_all_declared_schemas_reject_unknown_missing_and_boolean_aliases(self):
        for name in c.FIELDS:
            for mutation in (lambda r: r.update(unknown=True), lambda r: r.pop("model"),
                             lambda r: r.update(new_api_calls=False), lambda r: r.update(passed=1),
                             lambda r: r.update(release_admitted=0)):
                with self.subTest(schema=name, mutation=mutation):
                    value = self.envelope(name)
                    mutation(value)
                    with self.assertRaises(ValueError):
                        c.schema(value, name, "test/model")

    def test_all_declared_schema_envelopes_have_valid_positive_baselines(self):
        for name in c.FIELDS:
            with self.subTest(schema=name):
                c.schema(self.envelope(name), name, "test/model")

    def test_original_certificate_paths_and_hashes_cannot_be_replaced(self):
        for model in [*c.LEGACY, *c.inventory.V5_REPORTS]:
            with self.subTest(model=model), self.assertRaisesRegex(ValueError, "cannot override"):
                c.verify_model_certificate(self.root, model, expected_report_sha256="a" * 64)

    def test_passed_flag_alone_is_not_certificate(self):
        for name in c.FIELDS:
            with self.subTest(schema=name), self.assertRaisesRegex(ValueError, "unknown/incomplete"):
                c.schema({"passed": True}, name, "test/model")

    def test_schema_identity_cannot_be_relabelled(self):
        name = "panel-refresh-haiku-nine-block-offline-replay-v1"
        report = self.envelope(name, c.inventory.HAIKU)
        with self.assertRaisesRegex(ValueError, "identity"):
            c.schema(report, name, c.inventory.DEEPSEEK)

    def test_binding_conflicts_and_unknown_fields_fail(self):
        ref = self.put("a.json", {})
        ev = c.Evidence(self.root)
        ev.add(ref)
        with self.assertRaisesRegex(ValueError, "conflicting"):
            ev.add({**ref, "sha256": "f" * 64})
        with self.assertRaisesRegex(ValueError, "binding fields"):
            ev.add({**ref, "passed": True})

    def test_changed_nested_output_fails(self):
        ref = self.put("r/nested.json", {"complete": True})
        report = {"outputs": [ref]}
        (self.root / "r/nested.json").write_text('{}\n')
        with self.assertRaisesRegex(ValueError, "changed certificate"):
            c.output_document(c.Evidence(self.root), "r", report, "nested.json")

    def test_missing_nested_output_fails(self):
        with self.assertRaisesRegex(ValueError, "missing exact nested"):
            c.output_document(c.Evidence(self.root), "r", {"outputs": []}, "input-audit.json")

    def test_duplicate_inventory_fails(self):
        ref = self.put("same.json", {})
        with self.assertRaisesRegex(ValueError, "duplicate artifact"):
            c.Evidence(self.root).many([ref, ref])

    def test_finish_detects_post_validation_mutation(self):
        ref = self.put("a.json", {"a": 1})
        ev = c.Evidence(self.root)
        ev.add(ref)
        self.put("a.json", {"a": 2})
        with self.assertRaisesRegex(ValueError, "changed during verification"):
            ev.finish()

    def test_orphan_and_late_inventory_members_fail(self):
        ev = c.Evidence(self.root)
        ev.add(self.put("receipts/first.json", {}))
        ev.watch("receipts")
        self.put("receipts/orphan.json", {})
        with self.assertRaisesRegex(ValueError, "inventory changed"):
            ev.finish()
        with self.assertRaisesRegex(ValueError, "orphan"):
            ev.watch("receipts")

    def test_path_escape_and_invalid_hash_fail(self):
        ev = c.Evidence(self.root)
        for ref in ({"path": "../a", "sha256": "a" * 64}, {"path": "a", "sha256": "not-a-hash"}):
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                ev.add(ref)

    def test_duplicate_or_missing_blocks_fail(self):
        blocks = [{"block": b, "conditions": n} for b, n in c.BLOCKS.items()]
        c.block_index(blocks, c.BLOCKS)
        for bad in (blocks[:-1], [*blocks[:-1], blocks[0]]):
            with self.assertRaises(ValueError):
                c.block_index(bad, c.BLOCKS)
        with self.assertRaises(ValueError):
            c.block_index([{"block": "one", "conditions": True}], {"one": 1})

    def test_typed_replay_keeps_scores_and_trace_but_ignores_only_declared_timing(self):
        old = [{"condition_id": "c", "score": 0, "executor": {"started_at": "old"},
                "efficiency": {"wall_time_seconds": 1}, "trace": [1]}]
        new = deepcopy(old)
        new[0]["executor"]["started_at"] = "new"
        new[0]["efficiency"]["wall_time_seconds"] = 2
        c.typed_rows(old, new)
        new[0]["score"] = False
        with self.assertRaisesRegex(ValueError, "typed replay"):
            c.typed_rows(old, new)
        new = deepcopy(old)
        new[0]["trace"] = [False]
        with self.assertRaises(ValueError):
            c.typed_rows(old, new)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            c.typed_rows(old, old * 2)

    def source_fixture(self):
        ev, plans = c.Evidence(self.root), {}
        model = "synthetic/model"
        for block, count in c.BLOCKS.items():
            conditions = {c.equal_hash({"model": model, "block": block, "index": i}):
                          {"model": model, "block": block, "index": i} for i in range(count)}
            output = f"rows/{block}.jsonl"
            rows = [{"condition_id": cid, "condition": cond, "plan_hash": f"plan-{block}",
                     "status": "verified_success", "attempt": 1, "confirmatory_weight": 0}
                    for cid, cond in conditions.items()]
            path = self.root / output
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"".join(c.inventory.canonical(row) + b"\n" for row in rows))
            ev.add(c.inventory.bind(self.root, output))
            plans[block] = ({"output": output, "plan_hash": f"plan-{block}"}, conditions)
        return ev, model, plans

    def test_source_inventory_closes_exact_1336_with_literal_provenance(self):
        ev, model, plans = self.source_fixture()
        records = c.source_selection(ev, model, plans)
        self.assertEqual(len(records), 1336)
        self.assertEqual(len({r["condition_id"] for r in records}), 1336)
        self.assertTrue(all(len(r["literal_line_sha256"]) == 64 for r in records))

    def test_source_resample_or_infrastructure_fails_even_when_rebound(self):
        for field, value in (("attempt", True), ("attempt", 2), ("confirmatory_weight", 1),
                             ("status", "provider_failure"), ("status", "failed_closed")):
            with self.subTest(field=field, value=value):
                ev, model, plans = self.source_fixture()
                name = plans["primary_capacity"][0]["output"]
                path = self.root / name
                rows = [json.loads(x) for x in path.read_text().splitlines()]
                rows[0][field] = value
                path.write_bytes(b"".join(c.inventory.canonical(row) + b"\n" for row in rows))
                ev.bindings.pop(name)
                ev.add(c.inventory.bind(self.root, name))
                with self.assertRaises(ValueError):
                    c.source_selection(ev, model, plans)

    def test_flat_source_requires_complete_typed_condition_projection(self):
        ev, model, plans = self.source_fixture()
        name = plans["toolsandbox"][0]["output"]
        path = self.root / name
        rows = [json.loads(x) for x in path.read_text().splitlines()]
        for row in rows:
            row.update(row.pop("condition"))
        path.write_bytes(b"".join(c.inventory.canonical(row) + b"\n" for row in rows))
        ev.bindings.pop(name)
        ev.add(c.inventory.bind(self.root, name))
        c.source_selection(ev, model, plans)
        rows[0].pop("index")
        path.write_bytes(b"".join(c.inventory.canonical(row) + b"\n" for row in rows))
        ev.bindings.pop(name)
        ev.add(c.inventory.bind(self.root, name))
        with self.assertRaisesRegex(ValueError, "projection is incomplete"):
            c.source_selection(ev, model, plans)

    def test_toolsandbox_grade_scope_and_typed_score(self):
        records = [{"block": "toolsandbox", "condition_id": str(i), "row": {"score": {"similarity": 0}}} for i in range(96)]
        tools = [{"condition_id": str(i), "score": {"similarity": 0}, "official_grader_reevaluation": True} for i in range(96)]
        c.verify_tools(tools, records, c.inventory.HAIKU, "haiku")
        tools[0]["score"]["similarity"] = False
        with self.assertRaisesRegex(ValueError, "typed saved-state score"):
            c.verify_tools(tools, records, c.inventory.HAIKU, "haiku")
        tools[0]["score"]["similarity"] = 0
        tools[0]["official_grader_reevaluation"] = False
        with self.assertRaisesRegex(ValueError, "unapproved nonofficial"):
            c.verify_tools(tools, records, c.inventory.HAIKU, "haiku")

    def captures_fixture(self):
        ev = c.Evidence(self.root)
        model = "anthropic/claude-sonnet-5.5"
        config = {"canonical_slug": model, "provider": {"only": ["anthropic"]},
                  "selected_route": {"provider_name": "Anthropic"}}
        directory = c.inventory.BASE + "/execution/receipts/anthropic--claude-sonnet-5.5"
        captures = []
        for i, phase in enumerate(("probe", "probe", "scored")):
            intent = {"model": model, "phase": phase, "condition_id": "probe" if i < 2 else "condition",
                      "attempt": 1, "turn_index": i if i < 2 else 0, "repeat": None, "request_hash": "a" * 64}
            stem = c.equal_hash(intent)
            wire = {"model": model, "provider": config["provider"]}
            body = {"id": f"generation-{i}", "model": model, "provider": "Anthropic", "choices": [{}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost": 0.001}}
            event = {"http_status": 200, "request": wire, "response_body": json.dumps(body)}
            raw = self.put(directory + f"/{stem}.raw.json", event)
            pending = self.put(directory + f"/{stem}.pending.json", {**intent, "unreconciled_liability_usd": 1})
            ev.add(raw); ev.add(pending)
            captures.append({"generation_id": body["id"], "logical_intent_hash": stem,
                "raw": raw["path"], "raw_sha256": raw["sha256"], "request_hash": intent["request_hash"],
                "wire_sha256": c.equal_hash(wire)})
        records = [{"block": "primary_capacity", "row": {"condition_id": "condition", "response_ids": ["generation-2"],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}}]
        return ev, captures, records, model, config

    def test_capture_closure_positive_and_no_network_or_writes(self):
        ev, captures, records, model, config = self.captures_fixture()
        before = {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        with patch("socket.socket", side_effect=AssertionError("network")):
            self.assertEqual(c.verify_captures(ev, captures, records, model, config, 2), 1)
            ev.finish()
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_capture_duplicate_wire_tamper_probe_scope_and_row_token_alias_fail(self):
        ev, caps, rows, model, config = self.captures_fixture()
        for captures, records, probes in ((caps + [caps[0]], rows, 2), (caps, rows, 3)):
            with self.assertRaises(ValueError):
                c.verify_captures(ev, captures, records, model, config, probes)
        changed = deepcopy(caps)
        changed[0]["wire_sha256"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "typed wire"):
            c.verify_captures(ev, changed, rows, model, config, 2)
        rows[0]["row"]["usage"]["prompt_tokens"] = True
        with self.assertRaisesRegex(ValueError, "typed row/raw"):
            c.verify_captures(ev, caps, rows, model, config, 2)

    def test_foreign_deepseek_receipt_is_rejected(self):
        ev, caps, rows, _, config = self.captures_fixture()
        for cap in caps:
            cap["phase"] = "deepinfra:scored"
        with self.assertRaisesRegex(ValueError, "foreign route/source"):
            c.verify_captures(ev, caps, rows, c.inventory.DEEPSEEK, config, 3)

    def test_raw_http_status_must_be_integer_even_when_rebound(self):
        ev, caps, rows, model, config = self.captures_fixture()
        name = caps[0]["raw"]
        event = json.loads((self.root / name).read_text())
        event["http_status"] = 200.0
        new_binding = self.put(name, event)
        caps[0]["raw_sha256"] = new_binding["sha256"]
        ev.bindings.pop(name)
        ev.add(new_binding)
        with self.assertRaisesRegex(ValueError, "unsuccessful accepted"):
            c.verify_captures(ev, caps, rows, model, config, 2)

    def test_billing_aliases_missing_holds_and_cap_violation_fail(self):
        ok = {"known_receipt_cost_usd": 1.0, "unknown_cost_reserved_usd": 2.0,
              "budget_accounted_usd": 3.0, "model_cap_usd": 5}
        c.audit_billing(ok, model="openai/gpt-6-luna")
        for mutation in ({"known_receipt_cost_usd": True}, {"budget_accounted_usd": 1.0}, {"model_cap_usd": 2}, {"model_cap_usd": 100}):
            with self.assertRaises(ValueError):
                c.audit_billing({**ok, **mutation}, model="openai/gpt-6-luna")

    def test_new_reports_cannot_be_inferred_or_replaced_by_passed_flag(self):
        self.assertEqual(c.DEEPINFRA_REPORT, {
            "path": c.inventory.DEEPINFRA_BASE + "/offline-replay-v1/final-v1/report.json",
            "sha256": "16a869980f6edb5d44227505c004f6bc171fec58d5559c9677e31a152b02d163"})
        for kwargs in ({"report_path": c.DEEPINFRA_REPORT["path"]},
                       {"expected_report_sha256": c.DEEPINFRA_REPORT["sha256"]},
                       {"report_path": "fake/report.json", "expected_report_sha256": "a" * 64}):
            with self.assertRaisesRegex(ValueError, "cannot override approved DeepInfra"):
                c.verify_model_certificate(self.root, c.inventory.DEEPSEEK, **kwargs)
        self.put(c.DEEPINFRA_REPORT["path"], {"passed": True})
        with self.assertRaisesRegex(ValueError, "changed certificate evidence"):
            c.verify_model_certificate(self.root, c.inventory.DEEPSEEK)
        model = next(iter(c.LEGACY))
        with self.assertRaisesRegex(ValueError, "cannot override"):
            c.verify_model_certificate(self.root, model, report_path="fake/report.json")

    def test_haiku_has_one_literal_report_pin_and_rejects_all_caller_overrides(self):
        self.assertEqual(c.HAIKU_REPORT, {
            "path": c.inventory.HAIKU_BASE + "/execution/offline-nine-block-replay-v1/haiku-2026-10-08-a/report.json",
            "sha256": "9dc127b1f43937a0b3b69057bcefbbd74058a2741fef7c0cdeae5d2560061722"})
        for kwargs in ({"report_path": c.HAIKU_REPORT["path"]},
                       {"expected_report_sha256": c.HAIKU_REPORT["sha256"]},
                       {"report_path": "fake/report.json", "expected_report_sha256": "a" * 64}):
            with self.assertRaisesRegex(ValueError, "cannot override approved Haiku"):
                c.verify_model_certificate(self.root, c.inventory.HAIKU, **kwargs)
        self.put(c.HAIKU_REPORT["path"], {"passed": True})
        with self.assertRaisesRegex(ValueError, "changed certificate evidence"):
            c.verify_model_certificate(self.root, c.inventory.HAIKU)

    def haiku_fixture(self):
        model, base = c.inventory.HAIKU, c.inventory.HAIKU_BASE + "/execution"
        directory = base + "/receipts/" + model.replace("/", "--")
        ev = c.Evidence(self.root)
        config = {"context_length": 1000, "provider": {"only": ["anthropic"], "order": ["anthropic"],
            "allow_fallbacks": False, "data_collection": "deny", "require_parameters": True},
            "selected_route": {"pricing": {"prompt": "0.000001", "completion": "0.000002"}}}
        manifest = {"manifest_hash": "f" * 64}
        auth = c.FRESH[model]["pins"][base + "/authorization.json"]
        captures, probe_inputs, ids = [], [], []
        for i, phase in enumerate(("probe", "probe", "scored")):
            wire = {"model": model, "messages": [{"role": "user", "content": "hello"}], "max_tokens": 1024,
                "provider": {**config["provider"], "max_price": {"prompt": 1.0, "completion": 2.0}}}
            intent = {"schema": "haiku-wire-intent-v1", "model": model, "base_phase": phase, "phase": "haiku-v1:" + phase,
                "retry_index": 0, "condition_id": "transport-probe-haiku-2026-10-08" if phase == "probe" else "condition",
                "repeat": None, "attempt": 1, "turn_index": i if phase == "probe" else 0,
                "request_hash": c.equal_hash({"request": i}), "wire_sha256": c.equal_hash(wire),
                "manifest_hash": manifest["manifest_hash"], "authorization_sha256": auth, "unreconciled_liability_usd": .003048}
            stem = c.equal_hash({k: v for k, v in intent.items() if k != "unreconciled_liability_usd"})
            body = {"id": "gen-" + str(i), "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost": .001}}
            event = {"attempt": 1, "http_status": 200, "generation_id": body["id"], "request": wire, "response_body": json.dumps(body)}
            header = {"http_status": 200, "generation_id": body["id"], "received_at_utc": "2026-10-08T00:00:00+00:00", "request_sha256": c.equal_hash(wire)}
            bindings = [self.put(directory + "/" + stem + suffix, value) for suffix, value in
                ((".pending.json", intent), (".raw.json", event), (".headers.json", header))]
            ev.many(bindings)
            if phase == "probe": probe_inputs.extend(bindings); ids.append(body["id"])
            raw = bindings[1]
            captures.append({"generation_id": body["id"], "raw": raw["path"], "raw_sha256": raw["sha256"],
                "wire_sha256": c.equal_hash(wire)})
        probe = {"model": model, "passed": True, "requests": 2, "scientific_weight": 0, "probe_contract": "tool-roundtrip-v1",
            "manifest_hash": manifest["manifest_hash"], "authorization_sha256": auth, "generation_ids": ids,
            "inputs": sorted(probe_inputs, key=lambda b: b["path"])}
        original = self.put(base + "/probes/" + model.replace("/", "--") + ".json", probe)
        replayed = self.put("fixture/replayed-probe.json", probe)
        ev.many([original, replayed])
        audit = {"recovery_chains": [], "probe_responses": 2, "known_receipt_cost_usd": .003,
            "unknown_cost_reserved_usd": 0.0, "budget_accounted_usd": .003, "model_cap_usd": 10.0}
        report = {**audit, "probe": replayed}
        return ev, captures, config, manifest, audit, report

    def test_haiku_exact_no_retry_header_probe_and_billing_closure(self):
        args = self.haiku_fixture()
        with patch("socket.socket", side_effect=AssertionError("network forbidden")):
            c.verify_haiku_receipt_joins(*args)
            args[0].finish()

    def test_haiku_join_mutations_fail_even_when_local_files_are_rebound(self):
        changes = [("header", lambda v: v.update(generation_id="foreign")),
            ("header", lambda v: v.update(http_status=200.0)),
            ("header", lambda v: v.update(received_at_utc=None)),
            ("pending", lambda v: v.update(retry_index=True)),
            ("pending", lambda v: v.update(retry_index=1)),
            ("pending", lambda v: v.update(authorization_sha256="0" * 64)),
            ("pending", lambda v: v.update(wire_sha256="0" * 64)),
            ("pending", lambda v: v.update(repeat=False)),
            ("pending", lambda v: v.update(unreconciled_liability_usd=0)),
            ("raw", lambda v: v.update(attempt=True)),
            ("raw", lambda v: v["request"]["provider"].update(allow_fallbacks=True))]
        for kind, mutate in changes:
            args = self.haiku_fixture(); ev, caps = args[:2]
            suffix = ".headers.json" if kind == "header" else ".pending.json" if kind == "pending" else ".raw.json"
            name = caps[0]["raw"].replace(".raw.json", suffix)
            value = json.loads((self.root / name).read_text()); mutate(value)
            updated = self.put(name, value); ev.bindings.pop(name); ev.add(updated)
            if kind == "raw": caps[0]["raw_sha256"] = updated["sha256"]
            with self.subTest(kind=kind), self.assertRaises(ValueError): c.verify_haiku_receipt_joins(*args)

    def test_haiku_unknown_holds_unaccounted_attempts_and_forged_probe_rejected(self):
        for mutate in (lambda args: args[4].update(known_receipt_cost_usd=.002),
                       lambda args: args[5].update(unknown_cost_reserved_usd=.001),
                       lambda args: args[5].update(model_cap_usd=11),
                       lambda args: args[4].update(recovery_chains=[{}]),
                       lambda args: args[4].update(probe_responses=True)):
            args = self.haiku_fixture(); mutate(args)
            with self.assertRaises(ValueError): c.verify_haiku_receipt_joins(*args)
        args = self.haiku_fixture()
        args[0].add(self.put(str(Path(args[1][0]["raw"]).parent / "orphan.raw.json"), {}))
        with self.assertRaisesRegex(ValueError, "unaccounted"): c.verify_haiku_receipt_joins(*args)
        args = self.haiku_fixture(); ev, report = args[0], args[-1]
        name = report["probe"]["path"]; probe = json.loads((self.root / name).read_text()); probe["inputs"] = []
        updated = self.put(name, probe); ev.bindings.pop(name); ev.add(updated); report["probe"] = updated
        with self.assertRaisesRegex(ValueError, "original/replayed probe"): c.verify_haiku_receipt_joins(*args)

    def deepinfra_fixture(self):
        model, base = c.inventory.DEEPSEEK, c.inventory.DEEPINFRA_BASE + "/execution"
        directory = base + "/receipts/" + model.replace("/", "--")
        ev = c.Evidence(self.root)
        config = {"model": model, "canonical_slug": model + "-20260910", "context_length": 1000,
            "configuration_id": c.inventory.DEEPINFRA_CONFIGURATION, "temperature": 0,
            "reasoning_override": {"enabled": True, "effort": "high"},
            "provider": {"only": ["deepinfra/fp8"], "order": ["deepinfra/fp8"], "quantizations": ["fp8"],
                "allow_fallbacks": False, "data_collection": "allow", "require_parameters": True},
            "selected_route": {"provider_name": "DeepInfra", "pricing": {
                "prompt": "0.00000014", "completion": "0.00000042", "input_cache_read": "0.0000000042"}}}
        old_dir = c.inventory.BASE + "/execution/receipts/" + model.replace("/", "--")
        old_sources = [self.put(old_dir + "/" + name + ".raw.json", {"synthetic": name}) for name in ("prior", "failed")]
        legacy = {"known_receipt_cost_usd": 1.2, "unresolved_cost_reserved_usd": .3,
                  "budget_accounted_usd": 1.5, "model_cap_usd": 15.0}
        manifest = {"manifest_hash": "f" * 64, "legacy_accounting": legacy, "legacy_receipts": old_sources}
        mb = self.put(c.inventory.MANIFEST_PATHS["deepinfra"], manifest)
        ab = self.put(base + "/authorization.json", {"configuration_id": c.inventory.DEEPINFRA_CONFIGURATION,
            "manifest_sha256": mb["sha256"], "maximum_supplements": 5, "scored_conditions": 1336,
            "compatibility_probe_scoring_weight": 0, "legacy_accounting": legacy, "cap_usd_including_legacy": 15.0,
            "started_at_utc": "2026-10-08T00:00:00+00:00", "deadline_epoch": 1791507600})
        pb = self.put("protocols/panel-refresh-deepinfra-2026-10-08.md", {"synthetic": True})
        patcher = patch.dict(c.FRESH[model]["pins"], {ab["path"]: ab["sha256"]})
        patcher.start(); self.addCleanup(patcher.stop)
        ev.many([mb, ab, pb, *old_sources])
        captures, all_groups = [], []
        phases = ["deepinfra:compatibility-zero-weight", "deepinfra:tool-probe-zero-weight",
                  "deepinfra:tool-probe-zero-weight", "deepinfra:scored"]
        for i, phase in enumerate(phases):
            wire = {"model": model, "messages": [{"role": "user", "content": "hello"}], "max_tokens": 1024,
                "temperature": 0 if phase == "deepinfra:tool-probe-zero-weight" else 0.0,
                "reasoning": deepcopy(config["reasoning_override"]),
                "provider": {**config["provider"], "max_price": {"prompt": .14, "completion": .42}}}
            intent = {"model": model, "phase": phase, "attempt": 1, "turn_index": 1 if i == 2 else 0,
                "repeat": None if i == 3 else 606,
                "condition_id": "condition" if i == 3 else "compatibility:prior-condition" if i == 0 else "tool-roundtrip:deepinfra-fp8-high-v1",
                "request_hash": c.equal_hash({"request": i}),
                "unreconciled_liability_usd": 1000 * .00000014 + 1024 * .00000042}
            pure = {k: v for k, v in intent.items() if k != "unreconciled_liability_usd"}
            stem = c.equal_hash(pure)
            body = {"id": "gen-" + str(i), "model": model, "provider": "DeepInfra",
                "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "ready"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost": .001}}
            event = {"attempt": 1, "http_status": 200, "wire_attempted": True, "generation_id": body["id"],
                     "request": wire, "response_body": json.dumps(body)}
            bound = {"intent": pure, "wire": wire, "authorization_sha256": ab["sha256"], "configuration_id": config["configuration_id"]}
            header = {"http_status": 200, "generation_id": body["id"], "request_sha256": c.equal_hash(wire),
                      "received_at_utc": "2026-10-08T00:00:02+00:00"}
            started = {"request_sha256": c.equal_hash(wire), "authorization_sha256": ab["sha256"],
                       "started_at_utc": "2026-10-08T00:00:01+00:00"}
            recovery = {"maximum_supplements": 5, "superseded_attempts": [], "logical_request_hash": intent["request_hash"]}
            group = {name: self.put(directory + "/" + stem + "." + name + ".json", value) for name, value in
                (("pending", intent), ("raw", event), ("binding", bound), ("headers", header), ("wire-started", started), ("recovery", recovery))}
            ev.many(list(group.values())); all_groups.append(group)
            captures.append({"logical_intent_hash": c.equal_hash(pure), "request_hash": intent["request_hash"],
                "wire_sha256": c.equal_hash(wire), "raw": group["raw"]["path"], "raw_sha256": group["raw"]["sha256"],
                "generation_id": body["id"], "phase": phase})
        common = {"authorization_sha256": ab["sha256"], "transport_compatible": True, "scoring_weight": 0,
                  "score_correctness_tested": False, "reasoning_requested": config["reasoning_override"]}
        structural = {**common, "schema": "deepinfra-controller-probe-v1", "reasoning_observed": True,
            "raw_receipt": all_groups[0]["raw"], "sources": [all_groups[0][k] for k in ("pending", "binding", "raw", "wire-started")] + old_sources,
            "provenance": {"original_condition_id": "prior-condition", "logical_request_hash": captures[0]["request_hash"],
                "configuration_id": config["configuration_id"], "probe_provider": "DeepInfra", "source_provider": "DeepSeek",
                "scoring_weight": 0, "historical_source_used_only_for_probe": old_sources[0], "original_failed_request": old_sources[1]}}
        tool = {**common, "schema": "deepinfra-fresh-tool-roundtrip-v1", "fresh_provider_reasoning_preserved": True,
            "raw_receipts": [all_groups[i]["raw"] for i in (1, 2)],
            "sources": [all_groups[i][k] for i in (1, 2) for k in ("pending", "binding", "raw", "wire-started")]}
        proof_refs = [self.put(base + "/" + name, value) for name, value in
            (("structural-compatibility.json", structural), ("tool-roundtrip-compatibility.json", tool))]
        combined = self.put(base + "/compatibility.json", {"schema": "deepinfra-combined-compatibility-v1",
            "authorization_sha256": ab["sha256"], "transport_compatible": True, "scoring_weight": 0,
            "release_admitted": False, "sources": proof_refs})
        ev.many([*proof_refs, combined])
        audit = {"manifest": mb, "authorization": ab, "protocol": pb, "conditions": 1336, "planned_conditions": 1336,
            "completed_conditions": 1336, "internal_conditions": 1240, "toolsandbox_conditions": 96, "probe_requests": 3,
            "accepted_request_count": 4, "pending_attempt_count": 4, "new_known_receipt_cost_usd": .004,
            "new_unknown_cost_reserved_usd": 0.0, "legacy_known_receipt_cost_usd": 1.2,
            "legacy_unknown_cost_reserved_usd": .3, "known_receipt_cost_usd": 1.204,
            "unknown_cost_reserved_usd": .3, "budget_accounted_usd": 1.504, "model_cap_usd": 15}
        report = {**audit, "probes": {"source": combined}}
        return ev, captures, config, manifest, audit, report

    def test_deepinfra_no_supplement_six_artifact_probe_and_legacy_hold_closure(self):
        args = self.deepinfra_fixture()
        row = {"condition_id": "condition", "response_ids": ["gen-3"],
               "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
        with patch("socket.socket", side_effect=AssertionError("network forbidden")), \
             patch("fcntl.flock", side_effect=AssertionError("worker lock forbidden")):
            self.assertEqual(c.verify_captures(args[0], args[1], [{"block": "primary_capacity", "row": row}],
                                              c.inventory.DEEPSEEK, args[2], 3), 1)
            c.verify_deepinfra_receipt_joins(*args)
            args[0].finish()

    def test_deepinfra_rebound_sidecar_config_raw_and_identity_mutations_fail(self):
        mutations = [
            ("pending", lambda v: v.update(phase="deepinfra-technical:1:deepinfra:scored")),
            ("pending", lambda v: v.update(attempt=True)),
            ("pending", lambda v: v.update(unreconciled_liability_usd=0)),
            ("pending", lambda v: v.update(request_hash="a")),
            ("binding", lambda v: v.update(authorization_sha256="0" * 64)),
            ("binding", lambda v: v.update(configuration_id="original-provider")),
            ("binding", lambda v: v["wire"].update(temperature=1)),
            ("wire-started", lambda v: v.update(request_sha256="0" * 64)),
            ("wire-started", lambda v: v.update(started_at_utc="2020-01-01T00:00:00+00:00")),
            ("headers", lambda v: v.update(http_status=True)),
            ("headers", lambda v: v.update(generation_id="foreign")),
            ("recovery", lambda v: v.update(superseded_attempts=[{}])),
            ("raw", lambda v: v.update(wire_attempted=1)),
            ("raw", lambda v: v.update(transport_error="OSError")),
            ("raw", lambda v: v["request"]["reasoning"].update(effort="low")),
        ]
        for kind, mutate in mutations:
            args = self.deepinfra_fixture(); ev, captures = args[:2]
            name = captures[0]["raw"].removesuffix(".raw.json") + "." + kind + ".json"
            value = json.loads((self.root / name).read_text()); mutate(value)
            updated = self.put(name, value); ev.bindings.pop(name); ev.add(updated)
            if kind == "raw": captures[0]["raw_sha256"] = updated["sha256"]
            with self.subTest(kind=kind), self.assertRaises(ValueError): c.verify_deepinfra_receipt_joins(*args)

    def test_deepinfra_counts_billing_config_probes_and_unaccounted_attempts_fail(self):
        for target, key, value in [(4, "conditions", 1335), (4, "accepted_request_count", 3),
            (4, "pending_attempt_count", 5), (4, "probe_requests", True), (4, "new_unknown_cost_reserved_usd", .01),
            (4, "new_known_receipt_cost_usd", .003), (4, "legacy_unknown_cost_reserved_usd", 0),
            (5, "unknown_cost_reserved_usd", 0), (5, "model_cap_usd", 16), (2, "configuration_id", "other")]:
            args = self.deepinfra_fixture(); args[target][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): c.verify_deepinfra_receipt_joins(*args)
        args = self.deepinfra_fixture(); args[0].add(self.put(str(Path(args[1][0]["raw"]).parent / "orphan.pending.json"), {}))
        with self.assertRaisesRegex(ValueError, "unaccounted"): c.verify_deepinfra_receipt_joins(*args)
        args = self.deepinfra_fixture(); args[0].bindings.pop(args[3]["legacy_receipts"][0]["path"])
        with self.assertRaisesRegex(ValueError, "absent"): c.verify_deepinfra_receipt_joins(*args)
        args = self.deepinfra_fixture(); args[5]["probes"]["source"] = args[4]["manifest"]
        with self.assertRaisesRegex(ValueError, "probe source"): c.verify_deepinfra_receipt_joins(*args)

    def test_deepinfra_changed_raw_billing_refusal_model_usage_and_wire_fail(self):
        changes = [lambda v: v["usage"].update(cost=None), lambda v: v["usage"].update(cost=True),
            lambda v: v["usage"].update(cost=.002), lambda v: v["usage"].update(cost=-1),
            lambda v: v["usage"].update(completion_tokens=True), lambda v: v.update(provider="DeepSeek"),
            lambda v: v["choices"][0].update(finish_reason="refusal"),
            lambda v: v["choices"][0]["message"].update(refusal="refused")]
        for mutation in changes:
            args = self.deepinfra_fixture(); ev, caps = args[:2]
            name = caps[0]["raw"]; event = json.loads((self.root / name).read_text())
            body = json.loads(event["response_body"]); mutation(body); event["response_body"] = json.dumps(body)
            updated = self.put(name, event); ev.bindings.pop(name); ev.add(updated); caps[0]["raw_sha256"] = updated["sha256"]
            row = {"condition_id": "condition", "response_ids": ["gen-3"],
                   "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                c.verify_captures(ev, caps, [{"block": "primary_capacity", "row": row}], c.inventory.DEEPSEEK, args[2], 3)
                c.verify_deepinfra_receipt_joins(*args)

    def test_deepinfra_empty_or_changed_probe_sources_are_not_accepted(self):
        for name, mutation in (("tool-roundtrip-compatibility.json", lambda v: v.update(sources=[])),
            ("tool-roundtrip-compatibility.json", lambda v: v.update(raw_receipts=list(reversed(v["raw_receipts"])))),
            ("structural-compatibility.json", lambda v: v["provenance"].update(logical_request_hash="0" * 64))):
            args = self.deepinfra_fixture(); ev = args[0]
            path = c.inventory.DEEPINFRA_BASE + "/execution/" + name
            value = json.loads((self.root / path).read_text()); mutation(value)
            updated = self.put(path, value); ev.bindings.pop(path); ev.add(updated)
            combined_ref = args[5]["probes"]["source"]
            combined = json.loads((self.root / combined_ref["path"]).read_text())
            combined["sources"] = [updated if b["path"] == path else b for b in combined["sources"]]
            fresh = self.put(combined_ref["path"], combined); ev.bindings.pop(fresh["path"]); ev.add(fresh)
            args[5]["probes"]["source"] = fresh
            with self.subTest(name=name), self.assertRaises(ValueError): c.verify_deepinfra_receipt_joins(*args)


if __name__ == "__main__":
    unittest.main()
