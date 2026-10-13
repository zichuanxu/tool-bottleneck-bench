"""Hermetic selected-panel metadata projection and rejection regressions."""
from copy import deepcopy
import unittest

from experiments import panel_refresh_22_metadata as metadata

s = metadata.selection


def rehash(value):
    value.pop("artifact_hash", None)
    value["artifact_hash"] = s.stable_hash(value)


def fixture():
    old = [f"retained/model-{i:02d}" for i in range(14)]
    auxiliary = set(old) - {old[6]}
    registered = {old[1], old[2]}
    additional = {old[i] for i in (5, 7, 8, 9, 10, 11, 12, 13)}
    menu = set(old[:5]) | set(old[9:])
    panel = {"configurations": [{"model": m, "display_name": f"Retained {i}",
        "organization": "Historical", "provider": "Historical route", "access": "managed",
        "coverage": ["core"] + (["auxiliary"] if m in auxiliary else [])
            + (["external_rank"] if i < 5 else []) + (["menu_matched"] if m in menu else [])
            + (["structural_registered"] if m in registered else [])
            + (["structural_additional"] if m in additional else [])} for i, m in enumerate(old)],
        "activation_models": [old[0], old[4]], "auxiliary_models": sorted(auxiliary),
        "structural_registered_models": sorted(registered), "structural_additional_models": sorted(additional)}
    inputs = {}
    def group(name, models, count):
        inputs[name] = [{"model": m, "rows": count} for m in models]
    for prefix, core, aux in (("", old[:5], old[:5]),
                             ("candidate_", old[5:11], [old[i] for i in (5, 7, 8, 9, 10)]),
                             ("frontier_", old[11:], old[11:])):
        group(prefix + "capacity", core, 576)
        group(prefix + "discovery", core, 216)
        group(prefix + "repair", aux, 144)
        group(prefix + "sentinel", aux, 72)
        group(prefix + "workflow_frontier", aux, 16)
    group("external", old[:5], 32)
    group("historical_menu_intervention", old[:5], 64)
    group("candidate_menu_intervention", old[9:11], 96)
    group("frontier_menu_intervention", old[11:], 96)
    group("structural_holdout", sorted(registered), 216)
    group("candidate_structural_additional", [old[i] for i in (5, 7, 8, 9, 10)], 216)
    group("frontier_structural_additional", old[11:], 216)
    counts = {g: sum(e["rows"] for e in es) for g, es in inputs.items()}
    policy = {k: "historical policy" for k in metadata.POLICY_SCALARS}
    policy.update(candidate_extension_confirmatory_weight=0, frontier_extension_confirmatory_weight=0,
                  layer_activation_models=[*panel["activation_models"], "removed/stale-model"])
    manifest = {"models": old, "inputs": inputs, "expected_rows": counts, "analysis_policy": policy,
                "source_manifests": ["removed/stale-model"], "release_seed_artifacts": ["removed/stale-model"],
                "gate_archival_reference": "removed/stale-model"}
    configs, prepared, sources, plan_metadata = {}, [], [], []
    for model in sorted(s.NEW_MODELS):
        tag = "deepinfra/fp8" if model == s.DEEPSEEK else "fixture-provider"
        config = {"model": model, "display_name": metadata.REQUIRED_NEW_NAMES[model], "organization": "Fixture",
            "provider": {"only": [tag], "allow_fallbacks": False}, "access": "OpenRouter / " + tag,
            "canonical_slug": model, "context_length": 128000,
            "selected_route": {"model_id": model, "provider_name": "Fixture", "tag": tag}}
        if model == s.DEEPSEEK:
            config["configuration_id"] = s.DEEPINFRA_CONFIGURATION
            config["provider"]["quantizations"] = ["fp8"]
            config["selected_route"]["quantization"] = "fp8"
        configs[model] = config
        entry = {"model": model, "display_name": config["display_name"], "configuration_key": s.stable_hash({"model": model}),
            "configuration_sha256": s.stable_hash(config), "route": deepcopy(config["provider"]),
            "manifest": {"path": "fixture/manifest.json", "sha256": "1" * 64},
            "pricing": {"path": "fixture/pricing.json", "sha256": "2" * 64}}
        prepared.append(entry)
        for block, count in s.BLOCKS.items():
            source = {"model": model, "block": block, "planned_conditions": count,
                "configuration_key": entry["configuration_key"], "ledger_group": s.GROUPS[block],
                "plan": {"path": f"fixture/{model.replace('/', '--')}/{block}.json", "sha256": "3" * 64},
                "plan_hash": "4" * 64}
            sources.append(source)
            plan_metadata.append(metadata._plan_provenance({"confirmatory_weight": 0}, {}, source, entry))
    final_counts = dict(counts)
    for source in sources:
        final_counts[source["ledger_group"]] += source["planned_conditions"]
    source_map = {"schema": "panel-refresh-22-pre-admission-source-selection-v1", "status": "source_selection_validated_only",
        "release_admitted": False, "retained_models": sorted(old), "new_models": sorted(s.NEW_MODELS),
        "expected_coverage_counts": deepcopy(s.FINAL_COVERAGE), "expected_episode_counts": deepcopy(s.FINAL_EPISODES),
        "expected_total_rows": 27912, "planned_new_conditions": 10688, "configurations": prepared,
        "sources": sources, "expected_group_rows": final_counts}
    rehash(source_map)
    return panel, manifest, source_map, configs, plan_metadata, []


class CandidateMetadataTests(unittest.TestCase):
    def setUp(self):
        self.args = fixture()

    def derive(self):
        return metadata._derive(*self.args)

    def test_selected_22_planned_coverage_and_exact_episode_denominators(self):
        result = self.derive()
        panel = result["panel"]
        self.assertEqual(len(panel["configurations"]), 22)
        self.assertEqual(panel["coverage_counts"], s.FINAL_COVERAGE)
        self.assertEqual(panel["episode_counts"], s.FINAL_EPISODES)
        self.assertEqual(len(panel["structural_registered_models"]), 2)
        self.assertEqual(len(panel["structural_additional_models"]), 16)
        self.assertEqual(len(panel["concurrent_menu_models"]), 13)
        self.assertEqual(len(panel["external_rank_models"]), 5)
        self.assertFalse(result["release_admitted"])
        self.assertFalse(panel["release_admitted"])
        self.assertEqual(result["status"], "candidate_metadata_only")
        s.verify_self_hash(result, "artifact_hash")
        s.verify_self_hash(panel, "artifact_hash")

    def test_retained_coverage_and_classification_unchanged_without_mutating_inputs(self):
        before = deepcopy(self.args)
        result = self.derive()
        self.assertEqual(result["panel"]["configurations"][:14], self.args[0]["configurations"])
        self.assertEqual(self.args, before)

    def test_new_names_plain_and_route_classification_not_inferred(self):
        result = self.derive()
        rows = {r["model"]: r for r in result["panel"]["configurations"]}
        for model in s.NEW_MODELS:
            self.assertEqual(rows[model]["display_name"], metadata.REQUIRED_NEW_NAMES[model])
            self.assertEqual(rows[model]["provider"], "OpenRouter")
            self.assertEqual(rows[model]["access"], "API")
        self.assertEqual(rows[s.DEEPSEEK]["display_name"], "DeepSeek V4.1 Flash")
        route = next(c for c in result["reproducibility"]["new_configurations"] if c["model"] == s.DEEPSEEK)
        self.assertEqual(route["provider_policy"]["only"], ["deepinfra/fp8"])
        self.assertEqual(route["selected_route"]["quantization"], "fp8")

    def test_stale_activation_and_full_panel_archives_never_enter_projection(self):
        result = self.derive()
        self.assertEqual(result["analysis_policy"]["layer_activation_models"], sorted(self.args[0]["activation_models"]))
        encoded = s.canonical(result).decode()
        for word in ("removed/stale-model", "source_manifests", "release_seed_artifacts", "gate_archival_reference"):
            self.assertNotIn(word, encoded)

    def test_missing_extra_duplicate_prepared_configurations_rejected(self):
        for mutation in (lambda c: c.pop(), lambda c: c.append(deepcopy(c[0])),
                         lambda c: c[-1].update(model="foreign/model")):
            with self.subTest(mutation=mutation):
                self.args = fixture()
                mutation(self.args[2]["configurations"]); rehash(self.args[2])
                with self.assertRaises(ValueError): self.derive()

    def test_missing_extra_duplicate_block_coverage_rejected(self):
        for mutation in (lambda r: r.pop(), lambda r: r.append(deepcopy(r[0])),
                         lambda r: r.__setitem__(-1, deepcopy(r[0])),
                         lambda r: r[-1].update(block="unknown")):
            with self.subTest(mutation=mutation):
                self.args = fixture()
                mutation(self.args[2]["sources"]); rehash(self.args[2])
                with self.assertRaises(ValueError): self.derive()

    def test_planned_counts_and_ledger_membership_cannot_be_relabelled(self):
        for update in ({"planned_conditions": True}, {"planned_conditions": 433},
                       {"ledger_group": "external"}, {"configuration_key": "foreign"}):
            self.args = fixture()
            self.args[2]["sources"][0].update(update); rehash(self.args[2])
            with self.subTest(update=update), self.assertRaises(ValueError): self.derive()

    def test_declared_expected_counts_do_not_override_computed_counts(self):
        for key in ("expected_coverage_counts", "expected_episode_counts", "expected_group_rows"):
            self.args = fixture()
            first = next(iter(self.args[2][key])); self.args[2][key][first] += 1; rehash(self.args[2])
            with self.subTest(key=key), self.assertRaises(ValueError): self.derive()

    def test_missing_extra_duplicate_retained_coverage_rejected(self):
        for value in (["core"], ["core", "auxiliary", "unknown"], ["core", "core"]):
            self.args = fixture()
            self.args[0]["configurations"][0]["coverage"] = value
            with self.subTest(value=value), self.assertRaises(ValueError): self.derive()

    def test_duplicate_activation_and_structural_stratum_changes_rejected(self):
        self.args[0]["activation_models"] *= 2
        with self.assertRaises(ValueError): self.derive()
        self.args = fixture()
        self.args[0]["structural_registered_models"].append(self.args[0]["structural_additional_models"][0])
        with self.assertRaises(ValueError): self.derive()

    def test_new_model_cannot_inherit_removed_or_unknown_display_name(self):
        model = s.DEEPSEEK
        self.args[3][model]["display_name"] += " (DeepInfra FP8 retry)"
        with self.assertRaisesRegex(ValueError, "display name"): self.derive()

    def test_changed_configuration_or_duplicate_provenance_fails(self):
        self.args[3][s.HAIKU]["temperature"] = 99
        with self.assertRaisesRegex(ValueError, "configuration binding"): self.derive()
        self.args = fixture()
        self.args[4][-1] = deepcopy(self.args[4][0])
        with self.assertRaisesRegex(ValueError, "plan provenance"): self.derive()

    def test_manifest_preparation_never_becomes_missing_plan_freeze(self):
        source = next(p for p in self.args[2]["sources"] if p["block"] == "structural")
        config = next(c for c in self.args[2]["configurations"] if c["model"] == source["model"])
        result = metadata._plan_provenance({"confirmatory_weight": 0}, {"prepared_at_utc": "known-preparation"}, source, config)
        self.assertIsNone(result["plan_frozen_at"])
        self.assertIsNone(result["outcome_access_before_freeze"])
        self.assertIsNone(result["core_outcome_selection"])
        self.assertEqual(result["manifest_prepared_at_utc"], "known-preparation")
        self.assertNotIn("frozen_at", result["literal_plan_fields"])

    def test_explicit_null_and_false_provenance_remain_distinct(self):
        source = next(p for p in self.args[2]["sources"] if p["block"] == "structural")
        config = next(c for c in self.args[2]["configurations"] if c["model"] == source["model"])
        plan = {"confirmatory_weight": 0, "frozen_at": None, "structural_outcomes_access_before_freeze": False,
                "candidate_core_outcomes_inspected_before_freeze": None}
        result = metadata._plan_provenance(plan, {}, source, config)
        self.assertEqual(result["literal_plan_fields"], plan)
        self.assertIs(result["outcome_access_before_freeze"], False)
        self.assertIsNone(result["core_outcome_selection"])
        plan["confirmatory_weight"] = False
        with self.assertRaises(ValueError): metadata._plan_provenance(plan, {}, source, config)


if __name__ == "__main__":
    unittest.main()
