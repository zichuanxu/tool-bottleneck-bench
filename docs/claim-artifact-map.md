# ToolBottleneckBench Claim–Artifact Map (21-configuration panel, 2026-10-13)

This map binds every important quantitative and conceptual claim of the
manuscript to repository-relative raw rows, frozen plans and offline analyses.
Development, calibration, quarantined infrastructure rows and incomplete studies
carry zero scientific weight unless named explicitly. Historical analyses and
earlier panels remain in `results/` as local evidence; they are not current
manuscript artifacts.

## Common closure

- Selected panel: `results/reporting-panel-2026-10-13-final21/release-panel.json`
  (21 configurations, display names, block coverage, coverage counts).
- Row manifest: `results/reporting-panel-2026-10-13-final21/release-analysis-inputs.json`
  (26,576 distinct scored condition rows: 15,888 retained plus 10,688 new; every
  input hash, expected per-block denominators and the nine retained
  infrastructure conditions). Literal selected rows are under
  `results/reporting-panel-2026-10-13-final21/evidence/`.
- Generation record (private): `results/reporting-panel-2026-10-13-final21/analysis-generation.json`
  (16 analyses: 11 command-line analyzers and five direct analysis APIs, with the
  exact commands and bound inputs). Three further outputs are historical and
  byte-preserved, not recomputed on the 21-configuration panel:
  `toolsandbox-external-validation.json`, `structural-repair-holdout.json`,
  `historical-toolsandbox-menu.json`.
- Verification: `results/reporting-panel-2026-10-13-final21-verification-v1/report.json`
  (`passed=true`; separate same-algorithm deterministic recomputation of the 16
  analyses matching every typed JSON field with no ignored result fields; three
  byte-preservation checks; 157,817 input hashes stable before and after; zero
  API calls). This is not an independent statistical implementation, fresh
  generation or environment replay. The frozen analysis files retain their
  generation-time status strings (`release_admitted=false`); the later report
  establishes its scoped result separately.
- Per-model certificates for the eight additions are listed with report paths and
  SHA-256 bindings in
  `results/reporting-panel-2026-10-13-final21/internal/driver-provenance.json` (private)
  (`materialization.model_certificates`); the compact hash index is
  `results/local-only-panel-refresh-2026-10-07/execution/final-handoff-22-v1/SHA256SUMS` (private).
  Internal inventories are private; they must not be copied into a public or
  anonymous paper package.
- `paper/generated/manifest.json` (private) binds every generated figure, table and macro
  file to the analysis inputs it was computed from. `paper/reporting-panel.json` (private)
  names the analysis root the generator reads.

## Populations

| Population | Configurations | Rows | Success measure |
|---|---:|---:|---|
| Core capacity | 21 | 12,096 | verified task success (VTS) |
| Core discovery | 21 | 4,536 | intention-to-treat discovery outcome `Y = A·S` |
| Matched repair arms | 20 | 2,880 | VTS |
| Repeated sentinel | 20 | 1,440 | VTS |
| Workflow frontier | 20 | 320 | VTS |
| Pooled structural holdout | 17 (2 registered + 15 additional) | 3,672 | VTS |
| Concurrent ToolSandbox menu | 12 | 1,152 | ToolSandbox evaluator |
| Historical external rank | 5 | 160 | ToolSandbox evaluator |
| Historical menu (new rows) | 5 | 320 (+160 reused baselines) | ToolSandbox evaluator |

The exact-VTS population is 20,408 rows (capacity, repair, sentinel, frontier,
structural). The three success measures are never pooled.

## Claims

| Claim | Raw authority | Analyzer and aggregate | Boundary |
|---|---|---|---|
| **Panel and design.** 21 configurations from seven organizations; 13 retained plus eight additions with 1,336 conditions each over nine frozen blocks; 36 task families in 12 intents; 108 route-keyed tasks. | `release-panel.json`; `release-analysis-inputs.json`; the frozen plan manifests named in `driver-provenance.json` (private) | `experiments/build_panel_refresh_22.py`; `panel_refresh_22_materialize.py` | convenience sample selected for this reporting revision; additions declared confirmatory weight 0; not a preregistered or exhaustive model sample |
| **Harness contract.** Completion cap 1,024 tokens per request; episode budget 128,000 reported tokens; call limit certified minimum + 2; routed-menu primary lane with a paired fixed-menu robustness lane. | frozen plans and `condition.harness` on every released row | protocol tables in the manuscript appendix | every effect is conditional on these budgets; provider counting semantics differ |
| **Visible-menu penalty.** Raising the menu from 8 to 128 schemas lowers VTS by about 12 points; supported under family, intent and configuration clustering after Holm correction over the eight-contrast family. | release manifest capacity inputs | `experiments/analyze_unified_core_panel.py` → `release-core-panel.json` (`capacity.primary_effects`); `analyze_capacity_sensitivity.py` → `capacity-cluster-sensitivity.json` (`configuration_cluster_bootstrap`); `analyze_interaction_scale_sensitivity.py` → `interaction-scale-sensitivity.json` (exact 12-intent sign flips) | one hosted realization per core cell; assigned-pressure effect under the fixed harness, not intrinsic model capacity |
| **Null and small pressure effects.** Workflow load has no detectable effect; compact encoding lowers VTS by a few points; typed and linked do not survive correction; the positive visible-by-workflow coefficient fails Holm correction and its sensitivity analyses. | same inputs | `release-core-panel.json`; `capacity-cluster-sensitivity.json` (`interaction`: independence benchmark, floor pattern, non-floor re-estimate, non-monotone units; `random_effects_panel` is undefined on this panel) | compact is the larger encoding, so structure and size are not separated; null is not equivalence |
| **Budget mediation of the menu penalty.** Completion-cap truncation rises from about 1.6% to 14% of episodes; most lost matched pairs have a truncated large-menu variant; the truncation-resolution bound includes zero; the per-configuration menu effect tracks the truncation rate (Pearson about −0.95); eight never-truncating configurations have effects within three points of zero. | release manifest capacity inputs | `experiments/analyze_failure_mechanisms.py` → `failure-mechanism-decomposition.json` (`visible_load_decomposition`: `truncation_rate_visible_*`, `matched_pair_decomposition`, `manski_bounds`, `paired_non_truncated`, `visible_effect_versus_truncation_rate`, `zero_truncation_panel_configurations`, `by_panel_configuration`) | descriptive decomposition conditioning on a post-treatment variable; it does not estimate a larger-cap intervention; one configuration (DeepSeek V4.1 Flash) loses without truncating, so truncation is not the only path |
| **Failure census.** Of 3,725 failures among 20,408 exactly graded episodes, 91.5% end at a harness limit (completion cap, token or call budget, call-budget infeasibility) and 5.5% are wrong conclusions; 22.5% are truncated before any call and carry a `selection` label. | immutable release rows; `protocols/failure-mechanism-decomposition-2026-09-02.md` | `failure-mechanism-decomposition.json` (`release_failure_census`, `corrected_failure_stages`) | mutually exclusive descriptive terminal flags, neither causal sources nor repair assignments; the protocol scoped a 12-configuration subset and the release computes over 21 (`protocol_deviation`) |
| **Registered-catalog availability.** Under flat lexical retrieval, required-tool availability at 2,048 registered tools is about 19% against about 78% connector-first, identical for every configuration by construction; the ITT loss and its attenuation are estimated over 18 required-tool clusters. | release manifest discovery inputs | `release-core-panel.json` (`discovery.primary_itt_effects`, `availability_effects`); `capacity-cluster-sensitivity.json` (`retrieval_availability`) | intervention on the frozen retriever, not arbitrary production retrieval; availability-conditional contrasts are descriptive |
| **Workflow frontier boundary.** Pooled VTS at 2, 6, 14 and 30 certified calls over 20 configurations and four families; zero at 30 calls where most episodes exceed the episode token budget and no episode executed more than 23 calls. | release frontier inputs | `experiments/analyze_workflow_frontier_panel.py` → `release-workflow-frontier.json`; `failure-mechanism-decomposition.json` (`frontier_budget_boundary`) | four families; an operating boundary of the budget under this contract, not a planning-capacity law |
| **Failure record does not recover the pressure.** On 127 single-failure packets, obligation, termination, trace-shape and call-count decoders reach balanced accuracies between about 48% and 55% against a 33.3% constant baseline and no paired gain excludes zero at 99%. | capacity rows bound by the release manifest | `experiments/analyze_failure_localization.py` and `experiments/analyze_failure_localization_v2.py` → `release-failure-localization.json`, `release-failure-localization-v2.json`; `analyze_revision_audit.py` → `revision-evidence-audit.json` (`diagnostics`) | exploratory; selected population (baseline success, exactly one failing stress arm); leave-one-intent-out lookup decoder; the menu-size proxy is treatment-revealing and is not a method |
| **Repair activation.** Connector-first retrieval and routed menus pass the intersection–union activation test (positive assigned loss, stress-arm gain and low-pressure selectivity, Holm over four layers); the workflow prompt and interface guide do not. | release capacity, discovery and repair inputs | `experiments/analyze_release_layer_repairs.py` → `release-layer-repairs.json` (`primary_activation`, `layer_configurations`); `capacity-cluster-sensitivity.json` (`repair_equivalence` TOST margins); `analyze_prospective_sentinel.py` → `release-sentinel.json` | the router activates by construction of a model-independent retriever; the routed menu is oracle-assisted; activation is differential benefit, not layer specificity (a generic rescue satisfies it); null is not equivalence; 20 configurations carry workflow and interface arms |
| **Structural delegation.** On 24 held-out intents and 17 pooled configurations, the public-handoff controller restores about 97 points at the 30-call cell with positive matched specificity; the registered two-configuration plan alone also activates; the canonical-delivery wrong-layer control faced no supported loss. | extension structural plans and rows; `results/prospective-plans/structural-repair-holdout/plan.redacted.json` | `experiments/analyze_candidate_structural_release.py` → `structural-repair-pooled.json`; byte-preserved `structural-repair-holdout.json`; `failure-mechanism-decomposition.json` (`structural_call_attribution`) | intents are held out, configurations are not (four additional configurations were chosen after their core rows were scored, `plan_provenance`); a deterministic orchestration reference that issues almost every call, not improved model planning |
| **Configuration-versus-system decision.** Against every complete configuration switch on the same stressed cells, the routed menu and both prompt repairs are success-superior for no incumbent; the controller is success-superior for all 17 structural incumbents; the router is success-superior for all 21 by construction. | complete release capacity, discovery, repair and structural blocks | `experiments/analyze_model_system_decisions.py` → `release-model-versus-system.json` (`layers.*.finite_panel_fractions`, `incumbents`); `analyze_structural_model_system_decisions.py` → `structural-model-versus-system.json` | finite-panel dominance without a composite score; a switch is a peer candidate, not a presumed upgrade; no universal repair-versus-switch rule |
| **Completion-budget crossover.** Four retained configurations re-ran the capacity factorial at 1,024 and 4,096 completion tokens twice; the pre-declared manipulation check failed for both truncation-heavy configurations, and the arm contrasts are reported descriptively. | `results/prospective-execution/diagnosis-repair-crossover-1/`; `protocols/diagnosis-repair-crossover-2026-09-04.md` | `experiments/analyze_crossover_campaign.py` → `crossover-campaign.json`; `revision-evidence-audit.json` (`crossover` missing-outcome bounds) | not readable as designed; descriptive available-pair contrasts; the selection stratum rests on one re-pinned route; repeat disagreement is not a core-panel standard error |
| **Completion-cap sweep.** Eight configurations (six truncation-heavy, one mild, one null) re-ran the 432-condition routed-menu capacity factorial at 1,024 and 16,384 completion tokens, two repeats per cap; the pre-declared manipulation check (V=128 truncation under 5% at the high cap) is read per configuration, and the registered decision table is read from the pooled stratum-B interaction and high-cap contrast over the configurations that pass. | `results/prospective-execution/completion-cap-sweep-1/` and `completion-cap-sweep-2/` (rows, pilot-gate records); `results/prospective-plans/completion-cap-sweep-{1,2}/` | `experiments/analyze_cap_sweep_campaign.py` (reads both directories as one sweep) → `results/prospective-execution/completion-cap-sweep-1/analysis/cap-sweep-campaign.json` | routed-menu lane only, one high cap, episode budget and call limit unchanged; the pooled reading over the four originally registered stratum-B configurations is retained in the artifact as `registered_subset`; two execution hosts with identical frozen code; family-bootstrap intervals with repeats pooled, not core-panel standard errors |
| **External environment.** In the 12-configuration concurrent ToolSandbox menu study the all-tools arm changes full success by -2.9 points with a 95% interval that includes zero (sign-flip p = .224) while similarity does not move; the duration contrast is not estimated; the five-configuration historical rank association with core VTS is negative and uninformative. | extension ToolSandbox plans and rows; historical external-rank rows | `experiments/analyze_candidate_toolsandbox_release.py` → `candidate-toolsandbox-menu.json`; byte-preserved `toolsandbox-external-validation.json`, `historical-toolsandbox-menu.json`; `capacity-cluster-sensitivity.json` (`toolsandbox_rank_agreement`) | ToolSandbox's own evaluator, never pooled with VTS; the duration contrast is unestimated because one continuation has segment-only duration |
| **Policy pilot.** On 192 matched development blocks with two incumbents, public routing and the combined repair improve success over unchanged retry, and a fixed switch to a strong configuration exceeds every repair. | `results/policy-pilot/`; `results/prospective-plans/nonoracle-policy-pilot-1/plan.json` | `experiments/analyze_policy_pilot.py` → `results/policy-pilot/analysis.json` | development study on previously examined intents; its own population and binding; not a general repair-versus-switch preference |
| **Cost.** Normalized scientific token cost of about USD 768 for the 26,576 selected rows at frozen route prices without cache credit; the eight additions' acquisition accounting is separate. | normalized usage on every row; `configs/*pricing*.json` | `experiments/analyze_final_panel_cost.py` → `release-panel-cost.json`; `results/local-only-panel-refresh-2026-10-07/execution/final-handoff-22-v1/cost-ledger.json` (private) | a frozen-rate resource ledger, not an invoice; the acquisition ledger's unknown reserve is not a confirmed charge |

## Figure and table traceability

All figures and tables are generated by `paper/generate_results.py` (private) from the
analysis root in `paper/reporting-panel.json` (private); `paper/generated/manifest.json` (private)
records the SHA-256 of every input and output. Generated table bodies are
`paper/generated/tab_*.tex` (private); macros are `paper/generated/results_macros.tex` (private).

| Figure artifact | Data authority | Visual claim and boundary |
|---|---|---|
| `fig_concept.*` | compiler and experimental contract | schematic of task families, matched variants, pressures, harness budgets and grading; no empirical magnitude |
| `fig_capacity.*` | `release-core-panel.json`, `capacity-cluster-sensitivity.json`, `failure-mechanism-decomposition.json` | eight contrasts under three cluster units; per-configuration menu effect against truncation rate |
| `fig_failures.*` | `failure-mechanism-decomposition.json`, `release-workflow-frontier.json`, `release-core-panel.json` | terminal flags by cell; frontier levels and budget exceedance; discovery availability and ITT success |
| `fig_repairs.*` | `release-layer-repairs.json`, `structural-repair-pooled.json`, `release-model-versus-system.json`, `structural-model-versus-system.json` | activation components with intervals; switch-comparison fractions |
| `fig_config_effects.*` | `capacity-cluster-sensitivity.json`, `failure-mechanism-decomposition.json` | per-configuration effects; no intrinsic ranking |
| `fig_structural.*` | `failure-mechanism-decomposition.json` (`structural_call_attribution`) | arm means; deterministic reference, not model capability |
| `fig_frontier_tokens.*` | `release-workflow-frontier.json`, `failure-mechanism-decomposition.json` | (a) 30-call episodes against the episode budget; (b) per-level verified-success and over-budget shares read from `frontier_budget_boundary.levels` |
| `fig_diagnostic_gains.*` | `revision-evidence-audit.json` | paired decoder gains with 95% and 99% intervals |
| `fig_toolsandbox.*` | `candidate-toolsandbox-menu.json`, `historical-toolsandbox-menu.json` | two strata never pooled |
| `fig_crossover.*` | `crossover-campaign.json` | descriptive per-configuration arms |
| `tab_truncated_first.tex` (private) | `results/reporting-panel-2026-10-13-final21/internal/truncated-first-response-audit.json` (private), written by `experiments/audit_truncated_first_responses.py` (private) from the retained receipts | descriptive; nine configurations with reasoning-token telemetry; first requests carrying 128 schemas, all blocks |
| `tab_panel.tex` (private) fixed-menu column | `release-layer-repairs.json` (`primary_activation.visible_menu.by_configuration.*.diagnosed_loss`) | per-configuration point estimates, no interval |
| `tab_policy_pilot.tex` (private) after-failed-probe columns | `results/policy-pilot/rows.jsonl` | 65 failed probes of 192; development pilot, no confirmatory weight |
| `tab_cap_sweep.tex` (private) and the `Sweep*` macros | `cap-sweep-campaign.json` (both sweep directories; released V-effect column from `capacity-cluster-sensitivity.json`) | per-configuration arms and pooled strata with 95% family-bootstrap intervals; gate counts from the two `pilot-gate.json` records |
| `fig_variant_example.*` | compiler invariants | illustrative; not an extra scored task |

Any manuscript statement not mapped here is descriptive discussion, an explicit
limitation, or outside the evidence boundary.
