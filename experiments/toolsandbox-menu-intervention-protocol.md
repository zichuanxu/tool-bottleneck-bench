# ToolSandbox Matched Menu-Pressure Protocol

## Status and scientific role

This protocol is frozen before any outcome from the new menu-pressure arms is
generated or inspected. It is a prospective auxiliary intervention study. It
does not alter the source-bound Capacity Surface hypotheses or the completed
ToolSandbox ranking analysis.

The study tests whether the visible-menu mechanism identified in the controlled
benchmark transfers to an independently authored, stateful environment. It
uses ToolSandbox's own semantic variants rather than a custom task rewrite.

## Population and matched intervention

The population is the already frozen set of 32 ToolSandbox scenarios and 12
eligible model-provider configurations. For every base scenario, ToolSandbox
constructs variants by deep-copying the task and changing only the tool allow
list. The initial request, databases, maximum message budget, milestones,
minefields, and official evaluator are unchanged.

Three matched menu levels are analyzed:

1. `scoped`: the completed no-distraction trajectory, with 2--6 visible tools;
2. `ten_distractors`: the official semantic twin with ten added tools, yielding
   12--16 visible tools; and
3. `all_tools`: the official semantic twin exposing all 34 local ToolSandbox
   tools.

The completed scoped rows are immutable inputs. The two new arms reuse the
same provider configuration and the same frozen inference seed as the scoped
condition. Provider fallback remains disabled. The deterministic terminal user
and official milestone/minefield evaluator are unchanged. No LLM judge is used.

## Construct gate

Before paid execution, a population-wide audit must establish for every
scenario-arm pair that:

- the base tool set is nested inside the expanded set;
- the registered visible-tool count matches the executable allow list;
- initial databases and non-menu execution-context fields are identical;
- maximum message budgets and evaluation objects are identical; and
- the expected ToolSandbox menu category is the only category change.

Failure of any check blocks paid execution. The upstream source remains pinned
at commit `165848b9a78cead7ca7fe7c89c688b58e6501219`.

## Registered analysis

The primary outcome is official ToolSandbox similarity. Full success, milestone
similarity, minefield avoidance, requests, tokens, and wall time are secondary.
For each model-scenario cell, compute `ten_distractors - scoped` and
`all_tools - scoped`. Report panel means, model-specific means, stratum means,
and counts of degraded, tied, and improved cells.

Use 10,000 scenario-cluster bootstrap samples with seed `20260815` for 95%
intervals. Use 100,000 scenario-cluster sign-flip samples for two-sided p-values
and Holm-adjust the two registered similarity contrasts. The descriptive
`all_tools - ten_distractors` contrast is not in the Holm family.

Visible-menu pressure transfers if the `all_tools - scoped` similarity estimate
is negative, its 95% interval excludes zero, and its Holm-adjusted p-value is
below .05. A graded response additionally requires the descriptive all-tools
estimate to be lower than the ten-distractor estimate. The scoped arm is an
upper-bound system intervention using authored task scope, not a claim that an
automatic router always recovers the correct scope.

## Sampling and claim boundary

There is one trajectory per new model-scenario-arm condition. Scenario-cluster
uncertainty is estimated here; run-to-run variance is characterized separately
by the benchmark's three-pass sentinel panel. This study does not establish a
deployment distribution, contamination-free generalization, or a deployable
ToolSandbox router. It tests a matched mechanism transfer at menus up to 34
tools.

## Budget

The conservative token-trace projection is USD 2.93 for 768 new trajectories.
Execution is fail-closed under an incremental USD 6.00 OpenRouter cap and USD
1.50 Google Cloud telemetry cap. Provider or transport failures are quarantined
and never silently scored as task failures.
