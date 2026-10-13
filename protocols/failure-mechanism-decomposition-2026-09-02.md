# Post-hoc failure-mechanism decomposition

## Interpretation clarification, 2026-09-04

This append-only clarification supersedes the causal interpretation of A2 below,
not its saved rows or numerical calculation. The compatibility key `manski_bounds`
is a **truncation-resolution bound**: all currently nontruncated trajectories and
outcomes are held fixed; only truncated failures are allowed to resolve either
way in each arm. This untested stability assumption is additional to arm-wise
resolution. It is not the outcome of a larger-cap intervention. The descriptive
analysis uses the authoritative unified 20-configuration panel as recorded in
the artifact's protocol deviation; no acquisition cohort defines a new estimand.

The v2 derived artifact adds a mutually exclusive terminal-flag census, in this
precedence: completion cap, token-or-call budget, turn limit, call-infeasibility
early stop, wrong conclusion, other contract failure. `budget_exceeded` is not
assumed to mean token exhaustion alone. There is no flag overlap in the current
failure population. These buckets are not causal sources or repair assignments.
The no-call diagnostic is versioned `analysis-failure-stage-v1`; raw historical
obligation labels are immutable. Zero-token accounting now separates status,
block, configuration and presence of response IDs. These additions are post-hoc,
zero-confirmatory-weight diagnostics and add no hosted experiment.

**Question.** How much of each pre-specified capacity contrast, and of the workflow-frontier floor, is carried by exhaustion of a frozen harness budget rather than by a scored decision, and what do corrected first-failure labels show?

**Eligibility.** Only released rows bound by `results/analysis/release-analysis-inputs.json`. Tallies use every scored episode carrying an exact verdict (Capacity, repair, sentinel, workflow-frontier, structural-holdout blocks; Discovery and ToolSandbox rows use different outcome schemas and are excluded). The visible-load decomposition uses the frozen 12-configuration primary routed extreme cells (`visible_tools` 8 or 128, `workflow` low or high, `representation` raw or linked); per-configuration tables are additionally reported over the 20-configuration descriptive panel. The frontier section uses all 288 workflow-frontier rows, the structural section all 2,376 structural rows. Compiled-task probes (representation byte sizes, mutation census, route-string recoverability, distractor payloads) read the `main` split of the shipped task compiler; no hosted call is made.

**Unit.** Episode for tallies and cell rates; configuration for per-configuration tables; family cluster for the frontier paired contrasts, reusing `bottleneckbench.workflow_boundary_analysis._family_cluster_interval` with the frozen 10,000-draw convention and its seed derivation. The corrected localization keeps the leave-one-intent-out folds and the 10,000-draw intent-cluster bootstrap of `experiments/analyze_failure_localization.py`.

**Method.** A harness-limit failure carries any of `budget_exceeded`, `model_finish_length`, `success_infeasible_under_call_budget`, `turn_limit_exceeded`. A wrong-conclusion failure carries `decision_invalid` or `answer_facts_invalid` and no harness-limit error. Truncation is `model_finish_length`, the frozen 1,024-token per-request completion cap; truncation before the first call additionally requires zero executed tool calls. The corrected first-failure stage is the analysis-layer `bottleneckbench.analysis._failure_stage`, overridden to `completion_no_call` when the episode truncated before executing any tool call; the localization audit is recomputed with that labeler and nothing else changed. Truncation-attributable points are the change in truncated failures between the two visible-load arms divided by the rows per arm. Contrasts restricted to non-truncated episodes condition on a post-treatment variable and are reported as diagnostics.

**Multiplicity.** None. Every quantity is descriptive; no hypothesis is tested and no adjustment is applied. `confirmatory_weight` is 0.

**Claim boundary.** This analysis identifies which episodes met which budget under the frozen protocol. It does not estimate what would happen under a different completion cap, call cap, or episode token budget; that requires the prospective replication runbook. The conditional contrasts are not causal estimands.

---

## Analyses added in the second revision round

Every block below is **post-hoc, `confirmatory_weight` 0**. Each reads only rows already bound by
`results/analysis/release-analysis-inputs.json`, adds no hypothesis test, and amends no registered
estimate. The eligibility, unit and claim boundary of the sections above apply unless restated.

**Seeds.** The declared seed of this analysis is **20260902** and the declared draw count is
**10,000**; both are recorded at `bootstrap_policy`. Every bootstrap payload in the artifact now
records the seed it actually used. The frontier paired contrasts are the one exception to a single
declared value: their seed is *derived* per call level as
`stable_hash({"study": "workflow-boundary", "level": <locator_nodes>, "draws": <draws>})[:16]` read
as hexadecimal, and each contrast records that derived integer under `bootstrap.seed`.

### A1. Matched visible-load twin decomposition — `visible_load_decomposition.matched_pair_decomposition`

*Question.* Of the 8-to-128 visible-menu contrast, how much is carried by units whose $V{=}128$ twin
truncated, and how much by units that failed with the completion budget intact?
*Eligibility.* The frozen 12-configuration primary routed extreme cells, paired on
`model`, `family_id`, `workflow`, `representation`, `outcome_variant`, `prompt_variant`; the matrix
must close into complete twins or the analysis raises.
*Unit.* The assigned unit (one $V{=}8$ / $V{=}128$ twin).
*Method.* A pair is *lost* when the $V{=}8$ twin verifies and the $V{=}128$ twin does not, *gained*
in the reverse case; lost pairs split on whether the $V{=}128$ twin carries `model_finish_length`.
Points are counts over the number of pairs. The net equals the marginal contrast exactly.
*Multiplicity.* None; descriptive.
*Claim boundary.* An exact attribution of realized discordance under the frozen protocol. It says
which limit fired in each lost pair, not what that pair would have done under a larger budget.

### A2. Manski bounds on the visible-load contrast — `visible_load_decomposition.manski_bounds`

*Question.* What does the released evidence permit the $V$ contrast to be under a completion budget
that never binds?
*Eligibility and unit.* As A1.
*Method.* The lower bound gives every truncated failure the failure it was scored; the upper bound
gives every truncated failure a success. No other assumption enters.
*Multiplicity.* None.
*Claim boundary.* The bounds are the honest statement of the estimand: the contrast is bounded, not
identified, and any point inside the interval is consistent with the release.

### A3. Intact-pair visible-load contrast — `visible_load_decomposition.paired_non_truncated`

*Question.* What is the contrast over pairs in which neither twin truncated?
*Eligibility and unit.* As A1.
*Method.* The mean twin difference over pairs with no `model_finish_length` on either side.
*Multiplicity.* None.
*Claim boundary.* Still conditions on a post-treatment variable, but unlike the marginal
non-truncated contrast it keeps the matching, so it does not silently drop one arm at a higher rate
than the other. A diagnostic, not a causal estimand.

### A4. Zero-call truncation per visible-load arm — `visible_load_decomposition.cells.*.truncated_before_first_call`

Already computed by the sections above; registered here because the manuscript now prints it.
Truncation before the first call is `model_finish_length` with zero executed tool calls. It is a
strict subset of truncated failures, and the two must not be reported under one another's label.

### A5. Ex-ante reachability of the top call level — `frontier_budget_boundary.reachability`

*Question.* Was the highest certified call level attainable at all under the frozen
128,000-token episode budget, before any model was run?
*Eligibility.* All released workflow-frontier rows for the cheapest-cost table; the rows of the top
level for the fits.
*Unit.* Episode. Calls are counted as **attempts** (`call_attempts`), because an attempt is what the
episode pays for; the executed-call (`trace`) fit is reported beside it so the choice is visible and
the executed-call maximum is reported separately.
*Method.* Three independent routes. (i) The observed maximum executed calls. (ii) A flat-rate lower
bound: the cheapest observed tokens per call among episodes at or above 18 calls, multiplied by the
target call count. (iii) An ordinary least-squares fit of $t = an^2 + bn$ over the level's rows,
evaluated at the target. Reported alongside: the cheapest observed total tokens at 18 calls, the
episode budget left after it, and the cheapest observed marginal cost per call above it.
*Multiplicity.* None; no test.
*Claim boundary.* A feasibility statement about the protocol, not about any configuration. It
establishes that the level's success rate is a property of the frozen budget; it does not exclude a
planning cliff at the same level, which this design cannot separate from budget exhaustion.

### A6. Structural resource means restricted to the confirmatory holdout — `structural_call_attribution.confirmatory_only`

*Question.* What are the controller and baseline resource means on the pre-registered
four-configuration structural holdout alone?
*Eligibility.* Structural rows whose `condition.model` is in `structural_confirmatory_models` of
`results/analysis/release-panel.json`.
*Unit.* Episode, within arm.
*Method.* The same arm means as the pooled block, over the restricted row set.
*Multiplicity.* None; descriptive.
*Claim boundary.* The pooled arms mix the confirmatory holdout with the zero-weight auxiliary
extension. A sentence inside a paragraph labelled confirmatory must quote this restriction. No
contrast is re-tested and the class of the block is unchanged.

### A7. Release-wide corrected-labeller tally — `corrected_failure_stages.release_wide`

*Question.* How far does the analysis-layer corrected labeller differ from the evaluator's own
labels across the whole scored release?
*Eligibility.* Every scored row (the population defined above).
*Unit.* Episode.
*Method.* `bottleneckbench.analysis._failure_stage` with `completion_no_call` substituted for a
truncation before the first executed call, tallied against the evaluator's emitted stages. The
artifact also names the corrected labels the evaluator never emits and the registered stages the
evaluator emits zero times.
*Multiplicity.* None.
*Claim boundary.* The corrected column is a **different taxonomy**, not a relabelling: its `budget`
and `arguments` categories are diagnostic and are not the registered `budget` obligation, which the
evaluator never emits. Neither column is a ground truth.

### A8. Retained-ITT infrastructure recode — `release-core-confirmatory.json` / `release-core-panel.json`, key `retained_itt_infrastructure_sensitivity`

Computed by `experiments/analyze_unified_core_panel.py`; registered here because it is a post-hoc
zero-weight sensitivity of this revision round and the amendment it tests
(`protocols/candidate-infrastructure-analysis-amendment-2026-09-01.md`) is append-only and closed.

*Question.* How much can the intention-to-test rule that retains frozen infrastructure failures move
a printed contrast?
*Eligibility.* The capacity rows of the analyzed panel; the recoded rows are exactly the frozen
`retained_itt_infrastructure_condition_ids`.
*Unit.* Semantic family, as in the registered family-mean estimator.
*Method.* Score every retained ITT infrastructure row as a verified success, recompute all eight
registered contrasts, and report each as-scored value, recoded value and difference. Nothing else
changes; no interval is recomputed.
*Multiplicity.* None; no test.
*Claim boundary.* A bound on the arithmetic reach of a scoring rule. It is not an alternative
estimate and does not replace the registered analysis.

### A9. Balanced accuracy of the emitted labeller — `results/analysis/release-failure-localization.json`, key `methods.first_failure_stage.balanced_accuracy`

Already released; registered here because the manuscript must print it beside the corrected value.
The correction raises plain accuracy and lowers balanced accuracy; both must be shown or neither.
