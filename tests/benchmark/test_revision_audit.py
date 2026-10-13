"""Small independent checks of the new offline estimands."""
import itertools
import unittest

from experiments import analyze_revision_audit as audit


class RevisionAuditTest(unittest.TestCase):
    def test_binary_bounds_match_exhaustive_completions(self):
        terms = [(0.25, True), (-0.25, None), (0.25, None), (-0.25, False)]
        completions = [0.25 - 0.25 * a + 0.25 * b
                       for a, b in itertools.product((0, 1), repeat=2)]
        self.assertEqual(audit.outcome_bounds(terms), [min(completions), max(completions)])

    def test_generic_rescue_passes_activation_without_specificity(self):
        p0, p1, rescue = .8, .4, .5
        q0, q1 = (p + rescue * (1 - p) for p in (p0, p1))
        loss, gain = p0 - p1, q1 - p1
        selectivity = gain - (q0 - p0)
        self.assertTrue(all(x > 0 for x in (loss, gain, selectivity)))
        self.assertAlmostEqual(selectivity, rescue * loss)

    def test_absent_contract_bit_is_not_imputed_false(self):
        f = audit.extra_features({"trace": [], "verdict": {"selection": False}})
        self.assertEqual(f["verdict_vector"], "?0???????")
        self.assertEqual(f["calls_only"], "calls=0")

    def test_identical_predictions_have_zero_paired_interval(self):
        packets = [{"intent": intent, "label": label} for intent in ("a", "b", "c")
                   for label in audit.loc.LABELS]
        predictions = {m: [p["label"] for p in packets] for m in audit.METHODS}
        _, paired = audit.paired_metrics(packets, predictions, 100, 1)
        for result in paired["contrasts"].values():
            self.assertEqual(result["paired_ci95_pp"], [0, 0])
            self.assertEqual(result["bonferroni_ci99_pp"], [0, 0])


if __name__ == "__main__":
    unittest.main()
