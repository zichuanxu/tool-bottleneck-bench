# Post-hoc interaction-scale sensitivity

This zero-weight analysis uses only the frozen 12-configuration confirmatory capacity rows. It does not replace or amend the registered risk-difference estimand.

For the visible-load by workflow contrast, outcomes are pooled over the two registered extreme representations (`raw` and `linked`) within each of the 12 fixed intents. We report the four cell risks and the interaction on three scales: risk difference, log risk ratio, and log odds ratio. Log-scale intent contributions use the prespecified Jeffreys correction `(successes + 0.5) / (trials + 1)` to remain finite at boundary cells.

For all eight registered sparse-design contrasts, we additionally enumerate all `2^12` sign assignments over the 12 intent means, compute exact two-sided sign-flip p-values, and apply Holm correction across the same eight-effect family. These results are sensitivity evidence with `confirmatory_weight = 0`; disagreement with the family-cluster primary analysis must be reported rather than resolved by selecting the favorable clustering unit or effect scale.
