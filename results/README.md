# Result provenance

This directory separates scored scientific rows from private provider payloads.
Released analyses read only source-bound scored JSONL, frozen plans, and closed
accounting metadata. Raw response receipts, reasoning payloads, and transport
incident snapshots are private forensic evidence and are excluded by
`.gitignore`.

## Evidence strata

- `reporting-panel-2026-10-13-final21/`: the verified 21-configuration analysis
  root the manuscript reads. Its `release-analysis-inputs.json` names the
  scored rows under `evidence/` (182 files, all 21 configurations and no
  other model); `reporting-panel-2026-10-13-final21-verification-v1/report.json`
  is the passed deterministic recomputation. The generation record
  `analysis-generation.json` (private) and `internal/driver-provenance.json`
  (private) carry route and pricing metadata of the local-only acquisition
  tree and are not part of the public projection.

- `policy-pilot/`: the complete 1,536-episode public-information development
  projection, separate from the main ledger. Canonical scored rows, frozen
  analysis/choices, and hash-bound per-episode uncached costs reproduce via
  `experiments/replay_policy_pilot.py`. Raw
  receipts and transport snapshots stay private; offline replay is not
  independent billing verification or a hosted-generation rerun.

- `main/` and `model-generalization/`: immutable completed historical rows.
- `candidate-expansion/`: later outcome-frozen configuration blocks. Only
  matrix-complete blocks named by the release manifest have scientific weight.
- `frontier-model-extension/`: the final extension. Its source map selects only
  complete tranches; incomplete transport attempts remain zero-weight history.
- `prospective-plans/`: frozen acquisition plans. `core-main/` holds the 18
  self-hashed core plans that bind 4,752 of the released core rows (the six
  configurations acquired 2026-08-13);
  `structural-repair-holdout/plan.redacted.json` is the structural plan released
  with one documented redaction (a hosted project number in two endpoint URLs,
  described in its own `public_release_redaction` block);
  `targeted-vw-full-panel-repeat-recovery-1/` holds the budget and
  transport-recovery amendments of the closed-incomplete targeted V×W study.
- `prospective-execution/core-v-replication/`: the repeated-core study that
  terminated at 97/768, reported only as an incomplete study and never as a
  model result or repeated effect. `analysis.json` records `rows_observed: 97`
  against `rows_planned: 768`, computed as 768 minus the 671
  `replication_condition_id`s the plan never bound to a scored row. The 108
  released rows carry 108 distinct `replication_condition_id`s spread across
  four `replication_plan_hash` generations — 99 rows under the base generation
  (zero of which are `provider_failure`) and 9 across three later
  transport-recovery amendments (8 of which are `provider_failure`, including
  the sole generation `analysis.json` itself records as its own `plan_hash`).
  None of the 108 released IDs appear among the 671 missing ones, and
  `row_binding_failures` flags exactly one released row. Reconciling which of
  the 108 released rows the plan's own accounting binds against which of the
  768 planned slots requires the plan itself and its five amendments, which are
  retained privately (`prospective-plans/core-v-replication/plan.json`, table
  below); the plan-eligibility rule that turns the released rows into the
  counted 97 is therefore not independently reproducible from this public
  tree, and the honest restatement is: 108 rows released, 97 counted against
  the 768-row plan. The 108 saved rows carry their own token usage
  (1,857,571 prompt + 104,648 completion). The closure accounting also recorded
  provider account balances and is retained privately, so no monetary figure is
  published here.
- `prospective-execution/diagnosis-repair-crossover-1/`: separate two-cap,
  repeated-execution follow-up on seven configurations. Frozen plans are in
  the correspondingly named `prospective-plans/` directory. The analysis is
  `reporting-panel-2026-10-13-final21/crossover-campaign.json`; its registered
  manipulation check failed, so the arm estimates are descriptive and do not alter the original panel.
  Released raw rows suffice to recompute quarantine flags and retained-row
  usage costs without the private runtime ledger. Execution notes disclose
  counterbalanced sequential arms, job-boundary quarantine, broader executable
  provider exclusions and unretained duplicate costs.
- `prospective-execution/completion-cap-sweep-1/` and `completion-cap-sweep-2/`:
  one completion-cap sweep in two campaign directories; the second completed
  the truncation-heavy stratum. Eight configurations re-ran the
  432-condition routed-menu capacity factorial at 1,024 and 16,384 completion
  tokens, two repeats per arm. Released: the scored rows under `repeat-*/`, the
  pilot-gate verdicts, the execution notes and the frozen plans under the
  correspondingly named `prospective-plans/` directories. The joint analysis is
  `completion-cap-sweep-1/analysis/cap-sweep-campaign.json`, written by
  `experiments/analyze_cap_sweep_campaign.py`, which reads both directories as
  one sweep and recomputes usage cost from the rows and the frozen price
  table; the private cost ledgers, runner logs and locks are not needed to
  reproduce it.
- `external-menu-intervention/`: auxiliary external-mechanism evidence with a
  nonconcurrent baseline and zero confirmatory weight. `execution/` holds every
  scored row; `analysis.json` is released and records the SHA-256 of each input
  it consumed, but its frozen plan (`plan.json`) and the two transport-exclusion
  amendments are withheld because they name a configuration removed from the
  authoritative public panel. The analysis is therefore released with recorded
  input digests but is reproducible only from the private plan inputs.

`execution.proxy_url` (`http://127.0.0.1:7890`) appears in 19 released plan
files. It records the local outbound proxy used during acquisition and carries
no account-identifying information.

## Release rule

Do not edit a frozen plan or scored row after outcomes exist. Correct provenance
with a new amendment and a new output root. A public release may include scored
rows, plans, aggregate analyses, closed ledgers, and receipt-hash manifests; it
must not include raw provider receipts or credentials.
