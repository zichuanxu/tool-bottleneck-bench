# BottleneckBench Benchmark Card

## Evaluation gap

A terminal tool-agent score does not identify which deployable pressure
produced a loss. Recent diagnostic benchmarks localize perturbations and test
repairs, but they do not combine a separate availability estimand with a
same-goal crossed visible-menu, certified-workflow, and lossless-observation
surface. BottleneckBench supplies that joint measurement and decision contract.

## Intended use

BottleneckBench evaluates named model–provider–agent-policy configurations. It
supports layer-specific paired effects, cross-layer interactions, exact outcome
and first-failure scoring, matched repair tests, repeated repair sentinels, and
separate token/request/schema/latency/cost reporting. It is not a general
intelligence score, safety certification, or procurement ranking.

## Tasks and exact evaluation

All records and identities are synthetic and execute in an isolated local
world. The main benchmark has 36 task families nested under 12 intents; a
separate structural holdout has 24 unseen intents. Within a family, matched
variants preserve the request, decision-relevant facts, decision label, evidence
rule, operational tools, and permitted terminal state. Workflow levels change
the required sources array, so the complete answer is not fixed across them.

Verified Task Success is the conjunction of required-tool availability,
operational selection, dependency-valid trace completion, exact answer,
citation-set exactness, safe terminal-state diff, governance, budget, and
completion integrity. Alternative valid dependency orders are accepted. No LLM
judge is used.

## Measurement layers

| Layer | Pressure | Matched repair |
|---|---|---|
| Retrieval | registered catalog `R = 32, 512, 2048` with a fixed 16-tool shortlist | connector-first retrieval |
| Visible menu | peak native schemas `V = 8, 32, 128`, added as executable near-neighbor distractors | routed exposure |
| Workflow | certified programs plus a fixed-answer 2/6/14/30-call frontier | public-handoff delegation |
| Returned state | raw, compact, typed, and linked lossless encodings | canonical delivery |

The two prompt-only repair arms are selected by a harness identifier that the
released rows carry in `condition.harness` as `<id>@<provider>`:
`deliberate-verify-v1` for workflow verification and `interface-guide-v1` for
interface guidance. These are the values a replicator sets.

## Closed evidence ledger

The verified panel has 21 configurations (model, provider route and harness)
from seven organizations. Thirteen are retained from the earlier acquisition
(rows acquired 11 August to 4 September 2026) and keep their immutable scored
rows unchanged; eight additions were acquired on 7 and 8 October 2026, each with
exactly 1,336 conditions over the same nine frozen blocks. Per-model
certificates reconstruct the frozen harness verdicts from saved responses for
the eight additions. The selected panel is
`results/reporting-panel-2026-10-13-final21/release-panel.json` and the row
manifest is `results/reporting-panel-2026-10-13-final21/release-analysis-inputs.json`.

| Population | Configurations | Rows | Success measure |
|---|---:|---:|---|
| Core capacity | 21 | 12,096 | VTS |
| Core Discovery | 21 | 4,536 | ITT `Y = A · S` |
| Matched repair arms | 20 | 2,880 | VTS |
| Repeated sentinel | 20 | 1,440 | VTS |
| Workflow frontier | 20 | 320 | VTS |
| Pooled structural holdout | 17 (2 registered + 15 additional) | 3,672 | VTS |
| Concurrent ToolSandbox menu | 12 | 1,152 | ToolSandbox evaluator |
| Historical ToolSandbox external rank | 5 | 160 | ToolSandbox evaluator |
| Historical ToolSandbox menu, new rows | 5 | 320 (+160 reused baselines) | ToolSandbox evaluator |
| **Total distinct scored condition rows** | **21** | **26,576** |  |

The 26,576 rows are 15,888 retained plus 10,688 new condition records, not
independent tasks. The 160 reused baselines of the historical menu stratum are
the external-rank rows and are not counted twice. Of the 26,576 rows, 20,408
(capacity, repair, sentinel, frontier and structural) carry an exact Verified
Task Success verdict. The 4,536 Discovery rows are scored on the ITT discovery
outcome `Y = A · S` and the ToolSandbox rows on ToolSandbox's own evaluator, so
neither block is part of the VTS population.

All scored condition IDs are unique and a normalized usage object exists on
every row. Nine retained infrastructure conditions (8 capacity, 1 Discovery)
stay in the manifest as configuration-level ITT failures. The panel is a
convenience sample selected for this reporting revision: the additions carry
confirmatory weight 0, every panel-level analysis is descriptive, and only the
registered two-configuration structural plan carries confirmatory weight 1.

## Main findings

All capacity contrasts below are paired within-family risk differences in VTS,
in percentage points, over 756 pairs per contrast on the 21 core
configurations, with 10,000-draw cluster bootstrap intervals over 36 families
and Holm correction over eight contrasts. They are assigned-pressure effects
under the frozen harness, from one hosted realization per cell, not intrinsic
model capacities.

- **Visible menu.** Enlarging the menu from 8 to 128 schemas changes VTS by
  **-12.3 points** (`[-14.6, -9.9]`, Holm `p < .001`) on the 21 core
  configurations; 34 of 36 families degrade, and the penalty holds with
  configurations as the resampling unit (`[-19.8, -5.7]`, Holm `p = .011`).
- **Workflow and encoding.** High workflow load changes VTS by **+0.8**
  (`[-1.2, +2.8]`, Holm `p = .836`): no detectable workflow effect on this
  panel. Compact delivery changes VTS by **-3.7** (`[-5.7, -1.9]`, Holm
  `p = .003`); typed and linked delivery do not survive correction. The
  visible-by-workflow interaction is **+3.0** (`[0.2, 5.7]`) but fails
  correction (Holm `p = .202`), and 68.9% of it comes from 31 floor units. A
  random-effects synthesis over configurations is undefined on this panel
  because several configurations have zero between-family variance, so no
  I² or prediction interval is reported.
- **Truncation association.** The visible-menu penalty co-occurs with
  completion-cap truncation. In the core capacity rows, truncation rises from
  1.6% of episodes at V=8 to 13.6% at V=128, and across the 21 configurations
  the menu effect tracks the V=128 truncation rate (Pearson r = -0.95). The
  eight configurations that never truncate at V=128 have effects between -2.1
  and +2.8 points, but they are also the strongest baselines, so cap
  exhaustion and capability are confounded. The menu contrast is not
  identified without an assumption about truncated episodes: holding every
  non-truncated trajectory fixed and letting each truncated failure resolve
  either way bounds it to `[-13.9, +1.3]` points, which includes zero.
  DeepSeek V4.1 Flash loses 18.1 points while truncating 0.7% of its V=128
  episodes. These diagnostics condition on a post-treatment variable and do
  not estimate a larger-cap intervention. The visible-load decomposition
  protocol was scoped to 12 configurations; it is reported here over all 21.
- **Failure census.** Of 3,725 failures among the 20,408 exactly graded
  episodes, 91.5% end at a harness limit (completion cap, token or call budget,
  or call-budget infeasibility) and 5.5% are wrong conclusions; 22.5% are
  truncated on the first response, before any call. Two caveats apply: the
  30-call frontier and structural baseline cells contribute 896 budget-bound
  failures by construction, and the token-or-call category merges the
  128,000-token episode budget with a call limit only two above the certified
  minimum.
- **Discovery.** On the 21 core configurations at `R = 2048`, required-tool
  availability is 19.4% under flat lexical retrieval and 77.8% under the
  connector-first router, identical for every configuration by construction.
  The flat ITT contrast from 32 to 2,048 registered tools is **-79.6 points**,
  which the router attenuates by **+58.1 points**.
- **Workflow frontier.** Over 20 configurations and four families, VTS is
  91.2%, 87.5%, 85.0% and 0.0% at 2, 6, 14 and 30 certified calls. At 30 calls,
  69 of 80 episodes exceed the 128,000-token episode budget. The zero is the
  operating boundary of that budget under this contract, not an intrinsic
  planning limit.
- **Repair activation.** Connector-first retrieval (21 configurations, Holm
  `p = .001`) and the routed menu (21 configurations, Holm `p = .003`) pass the
  intersection-union activation test; the router does so by construction of a
  model-independent retriever, and the routed menu is oracle-assisted. The
  workflow prompt and the interface guide (20 configurations each) do not
  activate (Holm `p = .949` for both). Activation shows differential benefit,
  not layer specificity.
- **Structural delegation.** On 24 held-out intents at the 30-call cell, the
  public-handoff controller raises VTS by **+96.6 points** over 17 pooled
  configurations and is success-superior to every configuration switch for
  17/17 incumbents. It succeeds by issuing almost every call itself, so this is
  a deterministic orchestration reference, not improved model planning.
- **Repairs versus switching.** The routed menu is success-superior to every
  configuration switch for 0/21 incumbents and Pareto-nondominated for 9/21.
  Success-superiority over every switch arises only by construction (the router)
  or through deterministic delegation (the controller).
- **Failure localization (exploratory).** On 127 single-failure packets,
  balanced accuracy is 53.4% for the first unmet obligation, 48.9% for
  obligation plus terminal flag, 51.3% with trace shape added and 54.8% for call
  counts alone, against 33.3% for a constant label. No paired gain between these
  decoders excludes zero at 99%.
- **Completion-budget crossover.** Four retained configurations re-ran the
  capacity factorial at 1,024 and 4,096 completion tokens. The manipulation
  check failed, so the crossover is descriptive: the truncation-heavy stratum
  B loses -23.6 points to the larger menu at 1,024 tokens and -21.3 at 4,096.
- **ToolSandbox.** In the 12-configuration concurrent menu study, exposing all
  tools rather than the authored scope changes full success by -2.9 points
  (`[-7.0, +0.5]`, sign-flip `p = .224`) under ToolSandbox's own evaluator. The
  duration contrast for that arm is not estimated. The five-configuration
  historical rank association with core VTS is uninformative.
- **Policy pilot.** On its own population of 192 matched development blocks
  with two incumbents, the combined public repairs reach 81.8%, the public
  router 78.6% and unchanged retry 67.7%, against 99.0% for a fixed switch to a
  strong peer configuration. See `policy-pilot-final-2026-09-06.md`; the pilot
  is not added to the 21-configuration estimands.
- **Cost.** Normalized scientific token cost at frozen route prices, without
  cache credit, is USD 768.38 for the 26,576 rows; this is a resource ledger,
  not a bill. The separate acquisition accounting for the eight additions
  records USD 270.96 in confirmed charges and a USD 28.64 unconfirmed reserve;
  a reserve is not a confirmed charge, and the two accountings are never
  combined.

The analysis root is `results/reporting-panel-2026-10-13-final21/`; its
verification report,
`results/reporting-panel-2026-10-13-final21-verification-v1/report.json`,
passed as a deterministic recomputation of 16 analyses with 3 byte-preserved
historical outputs, not an independent re-implementation.

## Incomplete prospective studies

Two earlier prospective studies, a repeated-core experiment
(`results/prospective-execution/core-v-replication/`) and a targeted
visible-menu-by-workflow repeat
(`results/prospective-plans/targeted-vw-full-panel-repeat-recovery-1/`), are
closed with no estimate; neither contributes a number. The episode-budget
replication of the 14- and 30-call frontier cells was never opened.

## Recovery episodes and private records

Some directory names under `results/frontier-model-extension/` record
condition-scoped acquisition tranches for retained configurations. Each set of
tranches is one acquisition, not separate studies; the release source map
selects complete tranches only, and superseded rows are excluded by name in
`.gitignore`.

**Archival references retained privately.** The release records SHA-256 digests
for a number of launch manifests, completion records, and selection records that
are **not** shipped. Their hashes are in the release and cannot be checked
against anything in it. They are listed here rather than left silent.

| Retained artifact | Why it is not shipped |
|---|---|
| `configs/frontier-model-extension.json` (private) | acquisition configuration, names provider accounts |
| `configs/frontier-sol-budget-overlay-2026-09-02.json` (private) | spend overlay |
| `results/frontier-model-extension/plans/manifest.json` (private) | launch manifest |
| `results/frontier-model-extension/claude-full-condition-scoped-recovery/plans/manifest.json` (private) | launch manifest |
| `results/frontier-model-extension/claude-full-condition-scoped-recovery/execution/completion.json` (private) | execution completion record |
| `results/frontier-model-extension/claude-full-condition-scoped-recovery/execution/active-source-selection.json` (private) | execution selection record |
| `results/frontier-model-extension/claude-full-condition-scoped-recovery-continuation-2/plan.json` (private) | continuation plan |
| `results/frontier-model-extension/sol-fast-route-recovery/plans/manifest.json` (private) | launch manifest |
| `results/frontier-model-extension/sol-fast-route-recovery/execution/completion.json` (private) | execution completion record |
| `results/frontier-model-extension/sol-fast-route-recovery/execution/active-source-selection.json` (private) | execution selection record |
| `results/execution-governance/targeted-vw-full-panel-repeat-recovery-1-v3/sol-post-probe-budget-handoff.json` (private) | the targeted V×W closure gate named by the release manifest |
| `results/prospective-execution/core-v-replication/cost-telemetry.json` (private) | provider account balance figures |
| `results/candidate-expansion/frozen-panel.json` (private), sha256 `c217a5ad30663ba050b8e8fe992e9cc30be462e6b0c6c0fefcd5158fec1e6c16` | archival-provenance record named by the two evaluation plans under `results/candidate-expansion/plans/`, not itself shipped |
| `results/frontier-model-extension/frozen-panel.json` (private), sha256 `fe897b7298b26d144e72b81c3d23fdbdc6e07cffde8c059fa620d34af0214784` | archival-provenance record named by the structural and ToolSandbox plans under `results/frontier-model-extension/`, not itself shipped |
| `results/external-menu-intervention/plan.json` (private) | names a configuration outside the public panel |
| two transport-exclusion amendments under `results/external-menu-intervention/` (private) | only usable with the withheld menu-intervention plan |
| `results/prospective-plans/core-v-replication/plan.json` and its five amendments (private) | name a configuration outside the public panel in their budget block |
| `results/prospective-plans/structural-repair-holdout/plan.json` (private) | superseded by the released redacted copy described below |

Every entry above is a **private forensic or acquisition record whose hash is
recorded in the release but is not checkable from it**.

**One released analysis is therefore not regenerable from the public tree.**
`results/external-menu-intervention/analysis.json` is shipped as a released
artifact and records the SHA-256 of every input it consumed, but the plan
those inputs include is withheld because it names a configuration outside
the public panel (the release builder enforces this by name, so the name itself
is not reproduced here):
`results/external-menu-intervention/plan.json` (private) with its two
transport-exclusion amendments, all listed in the table above.
The rows that analysis scores (`results/external-menu-intervention/execution/`)
are released in full, so the numbers can be checked against the rows; only the
frozen plan bundle is private. The 21-configuration
analysis root carries byte-preserved historical outputs
(`toolsandbox-external-validation.json`, `historical-toolsandbox-menu.json` and
`structural-repair-holdout.json`); they are not recomputed on the
21-configuration panel.

**One plan is released with a documented redaction.** The structural repair
holdout plan is published as
`results/prospective-plans/structural-repair-holdout/plan.redacted.json`. Exactly
one class of change was made to it: the hosted Google Cloud project number inside
the two `aiplatform.googleapis.com` endpoint URLs
(`$.configurations[0].execution.endpoint` and
`$.configurations[1].execution.endpoint`) is replaced by the literal string
`REDACTED-PROJECT-NUMBER`. Every other key, including `conditions`, `cells`,
`construct_audit`, `frozen_at`, and `plan_hash`, is byte-identical to the
original, whose SHA-256 is recorded in the file's own
`public_release_redaction` block together with the exact JSON paths changed, the
replacement string, the reason, and the redaction date. Because `plan_hash` is
computed over the pre-redaction body it no longer self-verifies, so the plan's
content is checked a different way: the library regenerates its 864 conditions
in order, all 48 task ids, and the whole `construct_audit` block byte-for-byte
(`tests/benchmark/test_plan_binding.py` (private),
`tests/benchmark/test_structural_holdout.py`).

## Panel and blocks

The panel is organized by block, not by acquisition date. The 21-configuration
core carries the Capacity (12,096 rows) and Discovery (4,536 rows) analyses.
The repair test for the retrieval and visible-menu layers uses all 21
configurations, evaluated from the core Discovery and Capacity rows. The
workflow and returned-state-interface layers use the 20 configurations with
repair arms: the matched-repair block (2,880 rows), the repeated-sentinel block
(1,440 rows) and the workflow-frontier block (320 rows); Qwen3.8 Flash has no
repair arms. The structural holdout pools 17 configurations over 24 held-out
intents (3,672 rows). The intents are held out, but the configurations are not:
the registered plan names 2 of them and the other 15 come from additional
frozen plans with no confirmatory weight, some chosen after their core rows were
scored (`plan_provenance` in `structural-repair-pooled.json`). The registered
plan alone is reported in the byte-preserved `structural-repair-holdout.json`
as a sensitivity result. A 12-configuration block runs the ToolSandbox menu
intervention concurrently across all three arms (1,152 rows); the historical
ToolSandbox external-rank stratum (160 rows, 5 configurations) and the
historical nonconcurrent menu stratum (320 new rows plus 160 reused baselines,
5 configurations) are further auxiliary blocks.

The `coverage_counts` keys in `release-panel.json` are provenance names carried
from the acquisition ledger and hashed into the release manifest; they are not
inference classes. The analyses are exactly those described above.

## Limitations

The exhaustive census of 3,725 failures among the 20,408 exact-VTS episodes has
1,068 completion-cap stops, 1,863 token-or-call-budget violations, 477
call-infeasibility early stops, 204 wrong conclusions and 113 other contract
failures. The categories are mutually exclusive descriptive flags, not causal
sources or repair choices. The 30-call frontier and structural baseline cells
contribute 896 budget-bound failures by construction, and the
token-or-call-budget category merges the episode budget with a call limit only
two above the certified minimum. The first-unmet-obligation label is unchanged: all
849 `selection` labels belong to episodes without a call, because the grader
checks selection before completion integrity. A separate termination-aware
diagnostic relabels pre-call truncation; this is not a reordered obligation
checker.

The 26,576-row panel has 1,118 zero-token rows: 1,113 pre-request availability
stops and 5 provider or execution failures. None has a recorded provider
response. Usage-object presence is not complete telemetry; the normalized rows
do not retain reasoning or cache breakdowns. The exploratory localization
sample contains 127 single-failure packets, only 12 of them returned-state; the
obligation label alone recovers 0 of the 12 and obligation plus terminal flag
recovers 1. Termination-aware labels reach 54.1% balanced accuracy. These point
estimates do not establish a reliable paired improvement or a general causal
diagnostic, and a predictive decoder is not a causal attribution.

The completion-budget crossover runs 4 retained configurations at two caps with
two repeats each (6,912 rows read, 6,904 kept). Its protocol was frozen on
4 September 2026, and its analysis is
`results/reporting-panel-2026-10-13-final21/crossover-campaign.json`. The
manipulation check failed for both truncation-heavy configurations, so every
arm contrast is descriptive. The selection stratum runs on a different provider
route from its core-panel counterpart. Run-to-run disagreement (24.2% at 1,024
tokens, 21.2% at 4,096) is a repeatability measure, not a standard error for
the core panel's effects. The core panel's immutable rows are unchanged by the
crossover.

All 108 route-keyed tasks expose the operational tool set through literal
route matching; distractors return empty records. Workflow pressure changes
call count, accumulated context, call allowance and the required sources array.
Returned-state encodings also differ in size; compact is larger than raw.
Retrieval uses a frozen lexical scorer, and its routed repair is oracle-like.
No general non-oracle repair-over-switch superiority is established.

The controlled synthetic environment prioritizes identification over ecological
breadth. Primary core intervals condition on one hosted realization per
condition. Workflow load bundles several execution demands except in the
fixed-answer frontier. The ToolSandbox menu baseline is nonconcurrent and the
block is outside the main analysis. No independent human naturalness or exact-
scorer agreement audit is claimed.

## Versioning and refresh contract

The release includes the deterministic generator, scorer, difficulty axes, and
the manifests used in this paper. A future benchmark version must freeze
disjoint generator templates, program structures, intents, and seeds and
publish their content hash before observing
model outcomes. New packs may extend factor coordinates but may not change the
matched-variant or exact-scoring contract under the same version label. The
current paper reports no private-server score. Layer slopes, interactions,
repair selectivity, and nonsaturated intermediate coordinates are the maintained
diagnostics; the saturated 30-call cell remains an operating-boundary probe,
not the benchmark's primary leaderboard endpoint.
