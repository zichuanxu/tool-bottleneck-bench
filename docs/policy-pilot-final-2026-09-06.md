# Public-information policy pilot: final evidence and revision record

## Material Passport

- Origin skill: academic-research-suite, local revision and offline validation.
- Evidence: complete `nonoracle-policy-pilot-1`, 1,536 episodes / 192 blocks.
- Acquisition completed: 2026-09-06 02:50:26 JST.
- Verification: raw/canonical mapping, frozen bindings, receipt-cost reconciliation
  and analysis counts verified locally. Public replay is a separate check.
- Scope: development on 12 previously inspected intents, not new-intent confirmation.

## Findings and interpretation

| Action | Success / 192 | Rate |
|---|---:|---:|
| Probe | 127 | 66.1% |
| Unchanged retry | 130 | 67.7% |
| Completion cap 4,096 | 141 | 73.4% |
| Public-description router | 151 | 78.6% |
| Public-handoff controller | 138 | 71.9% |
| Canonical delivery | 137 | 71.4% |
| Bundle plus cap 4,096 | 157 | 81.8% |
| Fixed switch to Luna | 190 | 99.0% |
| Trace selector | 188 | 97.9% |

The bundle improves on retry by 14.1 percentage points descriptively. It improves
for both incumbents: DeepSeek 52.1% to 67.7%, Qwen 83.3% to 95.8%. This is a
public-information intervention benefit, not proof of general semantic retrieval,
layer-specific causal diagnosis, or superiority over all configuration switches.

The metadata and training-fixed controls always switch; the trace selector
retains 15 probes and switches 177 times. Its success difference from those
controls is -1.04 points, conditional 95% interval [-2.60, 0.00], nominal
six-comparison familywise interval [-3.125, 0.00]. Conditional intent-bootstrap
intervals do not refit selectors and are developmental. No incremental selection
benefit is established. Luna is a peer configuration, not a presumed stronger tier.

Mean uncached cost per 1,000 conditions is USD 4.489 for the trace selector,
USD 4.615 for probe-then-switch, and USD 1.936 for direct switching without a
probe. The last control is necessary: a small saving against probe-then-switch
does not establish deployment savings. Frozen token prices are not an invoice.

## Integrity and costs

All 1,536 canonical episodes match the frozen inventory, with 192 eight-action
blocks and no duplicate accepted provider generation IDs. The original failed
transport row is unchanged; its recovered replacement is explicitly hash-linked.
The recovery is a valid agent failure, not an outcome-selected successful retry.
All earlier completed rows and bound code/protocols are unchanged.

All 5,795 admitted receipts reconcile. There are 23 empty-429 reservations and
27 eligible incomplete-stream in-band 502 events. Reported charges total
USD 1.407060902. Conservative liability is USD 11.749200650, including USD 6.90
of uncertain charges, below the USD 15 ceiling. Neither amount should be
misrepresented as the other. No additional hosted call is needed or authorized.

## Public replay boundary

`results/policy-pilot/` is a complete scientific projection, not a transport-log
dump. It contains canonical rows, the exact frozen analysis and decisions, a
closed acquisition-accounting totals and per-episode uncached costs derived from all receipts.
`provenance.json` binds these files, the frozen plan and estimator. Run:

```sh
PYTHONPATH=src .venv/bin/python experiments/replay_policy_pilot.py
```

Replay uses the unchanged estimator, substitutes only receipt parsing with the
exported cost totals, and verifies byte-identical analysis and decisions. It
does not reproduce hosted generations or independently validate provider bills.
Private receipts, prefix snapshots and transport amendments remain preserved in
the private mother repository; the source map records their canonical lineage.

## Statistical interpretation: 11/11 checks

1. Simpson: bundle-versus-retry gains have the same sign for both incumbents;
   heterogeneity remains visible in the model columns.
2. Ecological inference: neither the pooled rate nor the two incumbents imply
   a population-wide model capability tier.
3. Selection: preselected development intents are not a random task sample.
4. Collider conditioning: all enrolled conditions, including successful probes,
   enter selection; no conditioning on post-treatment truncation is used.
5. Base rate: report retry, retain, fixed and metadata controls alongside policy rates.
6. Regression to mean: restart controls and both repeats are retained; no
   post-failure-only enrollment or best-of-two scoring.
7. Survivorship: complete canonical matrix, no scientific outcome excluded;
   transport recovery and its wall-clock gap remain explicit.
8. Multiple comparisons: all six policy comparisons are exported; nominal
   Bonferroni coverage is not upgraded to refit-aware inference.
9. Forking paths: the frozen estimator, thresholds and inventory are unchanged;
   results are developmental despite the pre-acquisition freeze.
10. Causal overreach: assigned interventions test operational benefit, not a
    unique cognitive failure mechanism or universal intervention ranking.
11. Reverse causality: probe features precede selected actions; held-out choices
    are sealed before utility computation. Previously inspected intents and
    fixed-rule bootstrap uncertainty still limit generalization.

## Local revision decisions

| Item | Judgment and action |
|---|---|
| Non-oracle action utility | Add matched public-information results, preserving the literal-route limitation. |
| Diagnostic information implies useful policy | Reject that implication; directly report fixed/metadata and no-probe controls. |
| A Luna advantage invalidates the benchmark | Reject: this local configuration comparison does not negate paired measurement or repair gains. |
| A repair advantage must be universal | Reject: identify interventions and their tested contracts, not a universal preference. |
| Repeated narrowing without evidence | Avoid: preserve the unified 21-configuration core and measurement / information / intervention contribution. |
| Process narrative | Keep only material transport and inference qualifications in the appendix; keep operational history in private records. |

This is a targeted LaTeX revision using `apply_patch` and a scoped Git diff.
The skill's Markdown block-marker machinery is not inserted into LaTeX.
No independent external-model review, new literature search or full citation
reverification is claimed in this finalization pass. New claims use this study's
own artifacts; existing literature and authorship are preserved.
