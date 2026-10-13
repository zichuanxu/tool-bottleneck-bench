# Post-hoc capacity clustering and equivalence sensitivity

**Question.** Do the pre-specified capacity contrasts survive alternative resampling units and a null of independent failure mechanisms, and how large a prompt or interface repair do the data exclude?

**Eligibility.** Only released rows bound by `results/analysis/release-analysis-inputs.json`. The clustering, interaction, and equivalence sections use the frozen 12-configuration primary Capacity rows; the random-effects synthesis uses the 20-configuration descriptive panel; the equivalence bounds use the six activation configurations named in `results/analysis/release-panel.json` together with their released repair rows; the retrieval companion uses the Discovery rows of the 20-configuration panel; the rank-agreement section uses the 11 configuration profiles in `results/analysis/toolsandbox-external-validation.json`.

**Unit.** Configuration (12) for the clustered bootstrap; family within intent for the intraclass correlation and its design effect; crossed family x configuration for the variance components; configuration (20) for the DerSimonian-Laird synthesis; semantic family (36) for the interaction log-risk-ratio and log-odds-ratio intervals and for the equivalence bounds; configuration (11) for the ToolSandbox rank interval. All bootstraps use 10,000 draws and the declared seed, and the percentile and centred-p convention of `bottleneckbench.analysis._bootstrap`.

**Method.** (a) Configuration-clustered percentile bootstrap of all eight registered contrasts with Holm adjustment inside that family. (b) One-way moment intraclass correlation of family contrast values within intent, its design effect, and the effective number of families. (c) Balanced two-way random-effects variance components over family x configuration, giving the standard error when both factors are treated as random. (d) DerSimonian-Laird synthesis across configurations with tau, I-squared, and a t-based prediction interval; a contrast is reported as undefined where a configuration has zero between-family variance, because its inverse-variance weight does not exist. (e) Interaction: the independence benchmark `(p00-p10)(p00-p01)/p00` that a risk-difference interaction inherits from statistically independent failure mechanisms, the sixteen-pattern decomposition of the 864 units, family-clustered Jeffreys log-risk-ratio and log-odds-ratio intervals, and a descriptive re-estimate after dropping units whose interaction sign is arithmetically forced. (f) Two one-sided t tests on the family-clustered stress-repair gain of the workflow-prompt and returned-state-interface repairs, reporting the smallest margin the data reject. (g) The retrieval repair gain under intention to treat and conditional on required-tool availability in both arms. (h) Spearman and Kendall tau-b between ToolSandbox similarity and Capacity Surface success across configurations, with a configuration-resampled interval.

**Multiplicity.** The eight-contrast Holm family is reapplied inside each clustering and labelled a sensitivity; it does not replace the registered family-clustered adjustment. The two equivalence tests are a separately declared family and, like everything here, carry `confirmatory_weight` 0. The minimum p-value a 10,000-draw bootstrap can resolve, and its Holm-adjusted floor over eight contrasts, are recorded so that no reported p is read as more precise than the resampling allows.

**Claim boundary.** These are finite-panel sensitivities. Twelve or twenty configurations are not a random sample of deployable systems; the equivalence margins bound only the six activation configurations; the availability-conditional retrieval contrast conditions on a post-treatment variable and is descriptive. Disagreement with the primary analysis is to be reported, not resolved by choosing the favourable clustering unit.

---

## Analyses added in the second revision round

Every block below is **post-hoc, `confirmatory_weight` 0**, reads only rows already bound by
`results/analysis/release-analysis-inputs.json`, and amends no registered estimate.

**Seeds.** The declared seed of this analysis is **20260902** and the declared draw count is
**10,000**; both are recorded at `bootstrap_resolution` (`declared_seed`, `samples`). A block that
draws more than one interval offsets the declared seed by a fixed integer, and **every** bootstrap
payload in the artifact now records the seed it actually used under its own `seed` key.

### B1. Monotonicity census of the interaction quadruplets — `interaction.monotonicity`

*Question.* How many assigned units verify under joint stress while failing under a single stress,
and what does the interaction become if they are set aside?
*Eligibility.* The same 864 complete quadruplets as the sixteen-pattern decomposition: the frozen
12-configuration primary Capacity rows, cells $(V, W) \in \{8,128\} \times \{\text{low},
\text{high}\}$ within a `(model, family, representation)` unit.
*Unit.* The assigned unit (one complete quadruplet).
*Method.* A unit is **non-monotone** when its joint-stress cell verifies and at least one
single-stress cell fails, which no deterministic monotone-capacity model admits. The block reports
the count, the share, the risk-difference interaction recomputed over the remaining units, and
separately the count and share of units whose three stressed cells all fail (patterns `1000` and
`0000`) — a superset of the floor pattern, and the correct denominator for the sentence "every
stressed cell fails".
*Multiplicity.* None; no test, no adjustment.
*Claim boundary.* A run-level consistency tally over the single hosted realization of each cell. It
bounds how much of the interaction survives a monotone reading; it is **not** an estimate of
run-to-run reproducibility, and dropping the units is an outcome-derived selection, so the reduced
value is descriptive.

### B2. Log-risk-ratio interaction over units with a stressed-cell success — `interaction.non_zero_unit_family_cluster_log_risk_ratio`

*Question.* How much of the family-clustered log-risk-ratio interaction survives when the units
that verified no stressed cell at all — the units where the Jeffreys correction, not the data,
supplies the cell risk — are removed? (Raised as question 5 of the ICLR re-review.)
*Eligibility.* The same 864 complete quadruplets, restricted to units with at least one verified
stressed cell $(V{=}8, W{=}\text{high})$, $(V{=}128, W{=}\text{low})$ or $(V{=}128,
W{=}\text{high})$. The artifact records how many units were dropped and their sixteen-pattern
breakdown.
*Unit.* Semantic family (the clustering unit of the released interval), over the retained units.
*Method.* Identical to the released interval — Jeffreys-corrected family cell risks
`(successes + 0.5) / (trials + 1)`, the log-risk-ratio interaction per family, and a 10,000-draw
percentile bootstrap over families — with the restricted unit set. Seed: the declared seed + 4,
recorded in the payload.
*Multiplicity.* **Post-hoc, `confirmatory_weight` 0.** No adjustment; it does not join the
registered family and does not amend the released interval.
*Claim boundary.* Outcome-derived selection: the retained units are chosen by their outcomes, so
the restricted interval is descriptive and its coverage is not the nominal 95%. It says how much
of the published interval rests on cells the correction filled in; it is not an alternative
estimate of the interaction.
