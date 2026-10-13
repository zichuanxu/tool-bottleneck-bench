# Protocol: diagnosis x repair crossover (completion-budget arm)

Frozen 2026-09-04. This document is hashed into every plan of the campaign
`diagnosis-repair-crossover-1` (`protocol_artifact_hash`). Nothing below may be
changed after the first hosted row; a change requires a new protocol file, a new
hash and a new campaign directory.

Protocol version: `diagnosis-repair-crossover-2026-09`.

## 0. Authorization and spending ceiling

User-authorized on 2026-09-04 for this campaign only, superseding no earlier
authorization and inheriting none. The 2026-09-03 USD 210 Opus ceiling is **not**
a source of funds for this campaign.

- **Hard ceiling: USD 100.00** across every hosted row of this campaign.
- Estimated cost at the routes and prices of section 4: **USD 50.00**
  (7 configurations x 432 conditions x 2 arms x 2 repeats = 12,096 rows, priced
  from the measured per-configuration token profile of the released panel and
  inflated in the `cap4096` arm by the released truncation share).  The remaining
  headroom under the ceiling funds a third repeat for any stratum whose section 6
  outcome is inconclusive.
- Execution stops at the ceiling and reports, whatever the completion state.
- Cost is metered from provider-reported usage against the section 4 price table
  and written to
  `results/prospective-execution/diagnosis-repair-crossover-1/cost-ledger.json`.

## 1. The question

> Does the recorded failure signature of a configuration predict which repair
> recovers its visible-menu loss?

Concretely: the released panel shows that the V=8 -> V=128 loss coincides with
completion-cap truncation in some configurations and with non-truncating
selection or answer failures in others. If the recorded signature is diagnostic,
raising the per-request completion budget should recover the first group and not
the second. If it is not diagnostic, the budget should move both groups alike, or
neither.

This is the identifying experiment the released analysis lacks: the released
panel never varied `max_completion_tokens`, so its visible-menu contrast is
bounded rather than estimated.

## 2. Configuration strata (frozen before any row)

Strata are assigned from the **released** panel
(`results/analysis/failure-mechanism-decomposition.json` ->
`visible_load_decomposition.by_panel_configuration`), which is independent data
from a prior campaign. The assignment rule, applied in this order:

- **B (budget-limited):** `truncation_rate_visible_128` >= 0.20 **and**
  `dominant_corrected_stage` == `completion_no_call`.
- **S (loss without dominant truncation):** `truncation_rate_visible_128` <= 0.15
  **and** released `visible_effect` <= -0.08.
- **N (null):** `truncation_rate_visible_128` <= 0.05 **and**
  released `visible_effect` > -0.05.

The S threshold was widened from an initial 0.05 / -0.10 to 0.15 / -0.08 **before
any campaign row was scored**, because every configuration meeting the tighter
rule proved unreachable (section 2.1): the panel's two exemplars of a large
non-truncating menu loss are served on routes this environment cannot use. The
widening is a reachability repair, not an outcome-dependent choice, and it is
recorded here rather than applied silently. Its cost is stated plainly: the
campaign's stratum S carries losses of -0.10 and -0.11 rather than -0.26, so it
tests a weaker version of the selection-limited regime than the released panel
contains.

Within each stratum, configurations are taken cheapest-first at the section 4
prices, subject to being reachable with the credentials available in this
environment.

| Stratum | Configuration | released trunc@V128 | released V effect | released dominant stage |
|---|---|---:|---:|---|
| B | `inclusionai/ling-3.0-flash` | 0.861 | -0.424 | completion_no_call |
| B | `meta/muse-glimmer-30b` | 0.618 | -0.486 | completion_no_call |
| B | `glm-5.3-flash` | 0.278 | -0.285 | completion_no_call |
| B | `qwen/qwen3.8-flash` | 0.278 | -0.167 | completion_no_call |
| S | `z-ai/glm-5.2` | 0.104 | -0.097 | completion_no_call |
| S | `deepseek/deepseek-v4-flash-0731` | 0.042 | -0.111 | workflow |
| N | `openai/gpt-5.6-luna` | 0.000 | +0.028 | workflow |

Five configurationsThree configurations that the rule selects are **excluded for access reasons, not
for their results**. All three exclusions were recorded before any hosted row of
this campaign and are reported in the paper.

| Excluded | Stratum | Released V effect | Reason (verified 2026-09-04) |
|---|---|---:|---|
| `google/gemma-4-26b-a4b-it-maas` | S | -0.174 | Google Cloud / MaaS credential not present in this environment |
| `openai/gpt-5.6-sol` | N | -0.021 | Google Cloud / MaaS credential not present in this environment |
| `qwen/qwen3.7-flash` | B | -0.347 | sole endpoint (`alibaba`) returns HTTP 429 "temporarily rate-limited upstream" on 6 of 6 pre-flight attempts |
| `google/gemini-3.5-flash-lite` | S | -0.264 | 75% of multi-turn episodes fail `require_usage` on both OpenRouter Google routes (`google-ai-studio/flex` 18/24, `google-ai-studio` 9/12); its released rows were served on Vertex, which this environment cannot reach |
| `openai/gpt-5.6-terra` | N | -0.021 | reachable and working, but at USD 0.0145/row it is the costliest configuration in the set and the second of two nulls; dropped to keep spending headroom under the ceiling |

The campaign therefore runs **7 configurations**: 4 in stratum B, 2 in stratum S,
1 in stratum N. Total planned rows: 7 x 432 x 2 arms x 2 repeats = **12,096**.

The `gemini-3.5-flash-lite` exclusion is the costliest of the five, because it was
the panel's clearest example of a large menu loss with zero truncation. Losing it
weakens stratum S, and the paper reports that the selection-limited regime is
represented here by two configurations with moderate losses rather than by the
panel's strongest case.

### 2.1 Declared route changes

Two configurations cannot be reached on their released route and are re-pinned.
They are therefore **new configurations**, not re-runs of the released ones, and
are reported as such. Because this campaign acquires its own contemporaneous
1,024-token arm (section 3), every contrast is internal to the campaign and does
not depend on the released rows.

| Configuration | Released route | Campaign route | Reason |
|---|---|---|---|
| `google/gemini-3.5-flash-lite` | Google Vertex AI | OpenRouter `google-ai-studio/flex` | no Google Cloud credential present |
| `deepseek/deepseek-v4-flash-0731` | OpenRouter DeepInfra FP4 | OpenRouter `sail-research/fp4` | DeepInfra no longer serves an FP4 endpoint for this model |

### 2.2 Parameter-support finding (pre-flight, 2026-09-04)

`openai/gpt-5.6-luna` (and `openai/gpt-5.6-terra`, since excluded) do not advertise `temperature` on
the `openai/flex` route, so a request carrying it is refused with HTTP 404 under
`require_parameters`. `gpt-5.6-luna` therefore sets `omit_temperature: true`;
decoding is the route's own default rather than an explicitly requested greedy
decode. This is a property of the pinned configuration, recorded here before the
run, and it is reported rather than corrected. All other configurations send
`temperature = 0`.

## 3. Arms, conditions and repetitions

**Arms.** Two, differing only in the per-request completion budget:

- `cap1024`: `episode_policy.max_completion_tokens = 1024` (the released value).
- `cap4096`: `episode_policy.max_completion_tokens = 4096`.

Every other policy field is identical in both arms and identical to the released
protocol: `max_total_tokens = 128000`, `tool_call_slack = 2`, `turn_slack = 2`,
`dynamic_shortlist = true`, `early_stop_infeasible = true`, and the same
decoding policy (temperature 0 except where section 2.2 records that the route
refuses the parameter).

**Both arms are acquired in this campaign.** The released 1,024-token rows are
*not* used as the baseline for the primary contrast. Provider routes and served
model builds drift, and two of the eight configurations are re-pinned (section
2.1), so an old-baseline-versus-new-treatment comparison would confound the
budget with elapsed time and route. The released rows are used only as an
out-of-campaign reference in a secondary comparison.

**Conditions.** The full registered primary capacity cell set for
`split = main`, `study_role = primary`: 12 cells x 36 semantic families = 432
conditions per configuration per arm per repeat. Running the whole registered
cell set, rather than only the eight V x W x D cells the budget question needs,
also delivers the contemporaneous repetition of the representation contrasts
(raw / generic_compact / typed / linked) that the single-realization limitation
requires. No condition field other than the episode policy differs from the
released plan.

**Interleaving.** The two arms are executed concurrently, alternating plans, so
that neither arm is systematically earlier in wall-clock time.

**Served-build binding.** Each configuration's `accepted_response_models` is
pinned to the served model id returned by the 2026-09-04 pre-flight probe, so a
silent build substitution fails the row rather than entering the panel.

**Repetitions.** 2 independent executions per (configuration, arm), written to
separate output files (`repeat-1`, `repeat-2`). Repeats are re-executions of the
same frozen plan; conditions keep their family-bound seeds, so a repeat measures
provider and decoding nondeterminism at fixed seed, which is the quantity the
single-realization limitation concerns. A third repeat is run only for
configurations whose section 6 outcome falls in the inconclusive band, and only
within the ceiling.

Total planned rows: 8 configurations x 432 conditions x 2 arms x 2 repeats =
**13,824** (see section 2.1 for the three access exclusions).

## 4. Routes and prices (fetched 2026-09-04)

Prices are USD per million tokens, taken from the OpenRouter model-endpoint API
on 2026-09-04 and frozen here. Cost is computed from provider-reported usage
against this table; it is an accounting, not an invoice.

| Configuration | Harness | Route | prompt | completion |
|---|---|---|---:|---:|
| `inclusionai/ling-3.0-flash` | react-routed@openrouter | `novita` | 0.021 | 0.063 |
| `qwen/qwen3.8-flash` | react-routed@openrouter | `alibaba` | 0.150 | 0.470 |
| `glm-5.3-flash` | react-routed@zai | Z.ai direct | 0.100 | 0.300 |
| `deepseek/deepseek-v4-flash-0731` | react-routed@openrouter | `sail-research/fp4` | 0.065 | 0.180 |
| `z-ai/glm-5.2` | react-routed@openrouter | `sail-research/fp8` | 0.500 | 3.150 |
| `openai/gpt-5.6-luna` | react-routed@openrouter | `openai/flex` | 0.100 | 0.600 |
| `meta/muse-glimmer-30b` | react-routed@openrouter | `phala` | 0.300 | 1.100 |

Provider fallbacks are disabled and parameters are required, so a route that
stops serving fails the row rather than silently substituting another provider.

## 5. Primary and secondary quantities

**Primary estimand.** The paired within-family risk difference in Verified Task
Success, V=128 minus V=8, pooled over the crossed W x D cells, estimated
separately in each arm; and the **difference of those differences between arms**
(the budget x menu interaction), estimated separately in stratum B and stratum S.

**Uncertainty.** 95% bootstrap intervals over the 36 semantic-family clusters,
10,000 draws, seed 20260904. Intent-cluster (12) and configuration-cluster
intervals are reported as pre-declared sensitivities. The family-cluster interval
is primary. Repeats enter as a within-condition variance component; the
run-to-run disagreement rate is reported separately from the family variance.

**Pre-declared secondary quantities**, all reported whatever the primary outcome:

1. Truncation rate at V=128 in each arm, per configuration (the manipulation check).
2. Share of failures emitting no tool call, per arm and configuration.
3. First-unmet-obligation distribution per arm, and the migration between arms.
4. The representation contrasts (compact/typed/linked minus raw) in the
   `cap1024` arm, with their run-to-run variability.
5. Tokens, wall time and cost per episode per arm.

## 6. Decision rule (fixed before the first row)

**Manipulation check.** In the `cap4096` arm the V=128 truncation share must fall
below 5% for each stratum-B configuration. A configuration where it does not is
reported as a failed manipulation and is excluded from the primary crossover
reading, but its rows are still released and reported.

**Primary reading**, on the budget x menu interaction, stratum by stratum:

| Outcome | Rule | Reading |
|---|---|---|
| **Crossover supported** | stratum B interaction interval lies entirely above +5.0 points **and** stratum S interval lies entirely below +5.0 | the recorded signature is diagnostic: the budget repair recovers the budget-limited group and not the selection-limited group |
| **Uniform budget effect** | both strata's intervals lie entirely above +5.0 | raising the budget helps regardless of signature; the signature is not diagnostic and the menu loss is a budget artifact panel-wide |
| **No budget effect** | both intervals lie entirely below +5.0 | the menu loss survives a four-fold larger budget in both strata; it is not a truncation artifact |
| **Inconclusive** | any stratum interval straddles +5.0 | report the interval and achieved half-width; do not round the conclusion in either direction; run repeat 3 for the straddling stratum if the ceiling allows |

The +5.0-point threshold is a design margin fixed in advance. It sits above the
released non-truncated residual contrast (-4.1 points marginal, -5.3 points
paired) so that "the budget explains the loss" is not declared for a shift smaller
than the residual already observed at the released cap. It may not be adjusted
afterwards.

**A null is publishable here.** "No budget effect" and "uniform budget effect" are
both reportable outcomes that change the paper's claims (section 8) and neither is
a reason to extend the campaign.

## 7. Exclusions and stopping

**Pre-declared exclusions.** A row is excluded from analysis only if it carries a
transport or provider error with no model response, or reports no usage object.
Excluded rows are counted and reported per configuration. No exclusion may
reference the row's verdict.

**Stopping rules.** Execution stops when any of the following holds:

1. The USD 100.00 ceiling is reached.
2. Every planned condition has a scored row.
3. A configuration returns transport or provider failures on more than 20% of its
   attempted rows within an arm; that configuration's arm is quarantined, its
   partial rows are retained and reported as incomplete, and the campaign
   continues with the rest. This is the failure mode that stopped the
   2026-08 replication at 97 of 768 rows, and it is handled here rather than
   allowed to end the campaign.

Partial completion is reported as partial. No result is dropped because it is
unfavourable, and no configuration is added after outcomes are seen.

## 8. What each outcome changes in the paper

- **Crossover supported.** The visible-menu result is stated as two mechanisms
  rather than one bounded effect: a budget-responsive loss that a deployable
  harness change removes, and a selection-responsive loss that it does not. The
  truncation-resolution bound is replaced by an estimate in each stratum. The
  repair-versus-switch section gains its first non-oracle repair whose success
  was not guaranteed by construction, together with a case where the same repair
  correctly fails.
- **Uniform budget effect.** The visible-menu effect is reported as an artifact of
  the released completion policy across the panel, the headline contrast is
  retired as a capacity claim, and the failure-signature diagnostic is reported as
  non-diagnostic for repair selection. This weakens the paper and is reported.
- **No budget effect.** The truncation-resolution bound is superseded: the menu
  loss is not a budget artifact and the released bound was uninformatively wide.
  The headline contrast strengthens; the failure signature is reported as not
  predictive of budget responsiveness.
- **Inconclusive.** The bound stands, the achieved precision is reported, and the
  paper states the experiment was run and did not resolve the question at this
  precision.
