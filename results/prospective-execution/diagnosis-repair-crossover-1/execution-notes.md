# Execution notes: diagnosis-repair-crossover-1

Protocol: `protocols/diagnosis-repair-crossover-2026-09-04.md` (frozen; its hash is
bound into every plan). This file records what happened during execution. It was
written after the protocol was frozen. It records implementation differences and
execution decisions; it does not rewrite the frozen policy.

## Incident 1 — concurrent writers produced duplicate rows (2026-09-04)

**What happened.** The campaign was started, appeared to have died (the sandbox
this session runs in hides processes from `ps`, so `ps aux | grep` returned
nothing while rows were in fact still being written), and was started a second
time. Both runners then wrote to the same output files.

`bottleneckbench run` is resumable by reading the set of completed
`condition_id`s **once, at start** (`src/bottleneckbench/experiments.py:891-907`).
Two runners over one output file therefore each observed the same pending set and
each executed it, appending a second row for conditions that were still pending
when the second runner started.

**Detection.** The first completed job reported 780 rows against a 432-condition
plan. An audit found 432 distinct `condition_id`s, a single `plan_hash`, and
`attempt == 1` on every row --- i.e. duplicates, not retries.

**Extent and repair.** Across all output files, 2,373 rows held 1,317 distinct
conditions; **1,056 duplicate rows** were removed, keeping the first row written
for each `condition_id`. Deduplication happened **before any analysis was run**,
so no reported quantity was ever computed on duplicated rows.

**Cost.** The duplicated rows were paid for. Metered spend at detection was
USD 6.83, of which roughly USD 3 bought rows that were then discarded, according
to the earlier execution note. The discarded usage rows are not retained, so the
exact duplicate-row cost is not reconstructible. A finalization audit found that
the runner's `recompute_spent()` sums only retained files and does not carry a
duplicate-cost offset. Its ledger therefore does **not** include these discarded
rows. The final report separates retained-row usage priced at frozen rates from
the historical approximate USD 3; neither is a provider invoice. Preflight and
unrecorded retry charges are not reconstructed. The ceiling was implemented at
job boundaries, not as a provider-side hard spending cap.

**Prevention.** `experiments/run_crossover_campaign.py` now takes a single-writer
PID lock (`.campaign.lock`) in the output directory, clears it when stale, and
refuses to start while a live runner holds it. Verified: a second invocation exits
with `another campaign runner (pid ...) holds ...`.

**Selection rule.** Deduplication is keyed on `condition_id`, which is
derived from the frozen condition fields, and the row retained is the first
written. The rule does not inspect outcomes. Duplicate traffic may nevertheless
have affected provider load and timing; its absence of scientific effect cannot
be verified from the retained rows.

## Deviations from the protocol

The frozen arms, conditions, seeds and admitted routes were retained. The final
code audit identified these differences from the prose protocol:

- Arm jobs are sequential within each configuration, in order cap1024/cap4096
  for repeat 1 and cap4096/cap1024 for repeat 2. Configuration lanes run in
  parallel. This counterbalances arm order but is not concurrent within each
  configuration and does not eliminate within-campaign time drift.
- Quarantine is checked after a job finishes, with a minimum of 50 rows, not
  when the failure share first crosses 20%. This explains why every retained
  quarantined job still has 432 rows. The analysis applies the same per-job
  rule, then omits both arms of an affected configuration from pooled contrasts.
- The executable exclusion drops provider-failure status even when earlier
  calls produced usage, and treats zero prompt usage as missing. This is broader
  than the protocol's provider error *with no response* or missing usage object.
  Exclusions are never scored as model failures.
- Budget checks occur at job boundaries and omit the discarded duplicate-row
  costs described above; they are not an exact all-charge hard cap.
- The optional third repeat was not exercised (rationale below). The registered
  intent- and configuration-cluster sensitivity intervals, variance-component
  decomposition, first-obligation migration and per-episode wall-time analysis
  were not implemented in the delivered analysis. Family-cluster intervals and
  repeat disagreement are reported; one is not a substitute for the other.

These differences qualify the follow-up; none changes a frozen plan or raw row.

## Incident 2 — host stopped the runner for memory (2026-09-05)

At 03:48 the host terminated the runner process ("system is running low on
memory"). It was found stopped at 06:58 with 9,178 rows written and restarted at
07:02. `bottleneckbench run` resumed from the completed-condition set; the PID
lock was stale (dead PID) and the runner cleared it. Audit after the restart:
**0 duplicate `condition_id`s** across all output files. No row was lost; about
three hours of wall-clock time were idle.

## Quarantine (pre-registered rule, section 7.3)

`meta/muse-glimmer-30b` at `cap4096`: 215 of 432 repeat-1 rows returned
`provider HTTP 520` (49.8%, above the 20% rule). The runner quarantined the arm
after repeat-1; its repeat-2 was not executed. The analysis removes the
configuration from every pooled contrast and reports its own row on the surviving
rows. Missingness may depend on condition; neither that dependence nor its bias
direction has been established. A sensitivity block
(`visible_menu_including_quarantined`) in the analysis artifact includes the
surviving rows.

`z-ai/glm-5.2` at `cap1024`: repeat 1 returned `provider HTTP 429` on 54 of 432
rows (12.5%, below the rule) and ran to completion; repeat 2 returned `provider
HTTP 400: Provider returned error` on 298 of 432 rows (69.0%), on nearly every
row after the first 124, and the runner quarantined the arm when that job
finished (2026-09-05 07:18 JST). Both repeats were written in full (864 rows,
512 kept) and no job was skipped, because no third repeat was scheduled. The
treatment is the same as above: the configuration leaves every pooled contrast,
so the stratum-S primary reading rests on `deepseek-v4-flash-0731` alone; its
own row is reported on the surviving rows and the sensitivity block includes
them.

## Protocol text artifact (not edited)

Section 2 of the frozen protocol reads "Five configurationsThree configurations
that the rule selects are excluded ... All three exclusions", an incomplete edit
made when the exclusion list grew from three to five before the freeze. The table
beneath that sentence lists five exclusions. Section 3 also retains the stale
eight-configuration/13,824-row total and describes two re-pins, although the
seven admitted configurations include only one executed re-pin (DeepSeek).
The admitted inventory is seven configurations and 12,096 planned rows, as in
section 2 and the executable plans. Muse's skipped repeat reduces the execution
target to 11,664 rows. The protocol is not edited because its hash is bound into
every execution plan.

## Manipulation check and the repeat-3 provision

All four stratum-B configurations failed the section 6 manipulation check:
`V=128` truncation in the `cap4096` arm exceeds the 5% bar. Exact final rates are
in `results/analysis/crossover-campaign.json`. The
pre-registered verdict is therefore **not readable as designed**, and the paper
reports the arm contrasts descriptively, as pre-declared secondary quantities.

Section 6 provides for a third repeat of a stratum whose interval straddles the
margin. That repeat was **not run**: no third repeat of stratum S
can make the crossover readable while stratum B has no configuration that passes
the check, so the additional rows could not change the verdict. Ceiling headroom
was available (about USD 34 of 100 spent at the time of the decision); the
decision rests on readability, not cost.

## Analysis implementation (v2)

`experiments/analyze_crossover_campaign.py` implements the section 6 gate, the
section 7.3 quarantine (configuration leaves pooled contrasts; own row reported)
and the section 7 row exclusion, and adds per-configuration arm contrasts,
per-arm run-to-run disagreement and a cost block. The cost accounting is
embedded in the analysis artifact because the ledger file itself falls under the
public builder's private `ledger` rule. Schema `…-crossover-v2`.

**v2.1 (2026-09-05).** The manipulation gate is applied to stratum B only, as
section 6 states ("for each stratum-B configuration"); the S and N primary
readings use every configuration that keeps both arms. The earlier v2 output
had gated all three strata, which removed `deepseek-v4-flash-0731` from the
stratum-S primary reading for its truncation rate. The verdict is unchanged
(not readable as designed), because stratum B has no passing configuration.

**v3 (2026-09-05, finalization audit).** Costs and per-job quarantine flags are
now recomputed directly from released raw rows using the runner's frozen price
table and transport predicate. The analyzer no longer depends on the private
runtime ledger, so a public checkout can reproduce both. Unestimable primary
quantities are JSON `null`, not non-standard `NaN`. The cost note explicitly
excludes discarded duplicate usage. Arm estimates use available V pairs; the
interaction requires both cap pairs on a cell, so with missing rows it need not
equal the difference of the two available-pair means. The unchanged estimand is
documented rather than forced to agree by deleting additional rows.

## Completion (2026-09-05)

The runner exited at 10:35 JST on 2026-09-05 after 27 jobs: 11,664 rows in 27
files (14 under `repeat-1`, 13 under `repeat-2`), every file holding 432 rows
with no duplicate `condition_id`. The 28th planned file (`muse-glimmer-30b`
cap4096, repeat 2) was never scheduled because of the quarantine above. Retained-row
usage at the frozen price table is USD 37.73 against the USD 100 ceiling, and the
ledger records `halted_at_ceiling: false`. The two quarantines above are the only
ones; the `deepseek-v4-flash-0731` repeat-2 arms completed with zero transport
failures. The final analysis read 11,664 rows and kept 11,008.
