# Model-versus-System Decision Protocol

## Status

This is a zero-activation-weight decision analysis frozen while the prospective
core matrix is still running. It does not modify any task, prompt, model panel,
primary estimand, multiplicity family, or claim threshold. At freeze, only row
counts, transport status, elapsed time, and provider spend were inspected; no
prospective success outcome, model ranking, layer contrast, or repair pair was
read.

## Decision question

For a named stressed layer and deployment configuration, is it more effective
to retain the acting model and change the diagnosed system layer, or to retain
the baseline harness and switch to another eligible model--provider
configuration?

This is a finite deployment comparison, not a causal estimate of model weights.
Provider, serving route, and unsupported decoding fields remain part of the
configuration.

## Eligible comparisons

For each layer, semantic family, and stress cell with complete paired rows:

- **system move:** same configuration, matched repair minus baseline harness;
- **configuration move:** candidate configuration under the baseline harness
  minus incumbent configuration under the same baseline harness;
- **combined move:** candidate configuration with the matched repair minus the
  incumbent baseline, when all four cells exist.

Never compare different semantic-family sets, stress coordinates, retry rules,
or exact-scoring contracts. Exclude outcome-known configurations from any
prospective activation statement, but retain all eligible configurations in
the descriptive decision map. Transport-quarantined or incomplete pairs carry
zero weight.

## Outcomes

Report paired changes in:

1. verified task success;
2. prompt, completion, and total tokens;
3. model requests and attempted tool calls;
4. transmitted tool-schema bytes;
5. wall time; and
6. estimated provider cost where response usage is observed.

No weighted composite score is permitted. A move is Pareto-dominated when
another eligible move has no lower paired success, no higher measured cost,
tokens, or latency, and is strictly better on at least one dimension. Otherwise
it remains on the empirical Pareto set. Missing usage prevents a cost-dominance
claim but does not erase success or latency evidence.

## Summaries and uncertainty

- Compute family-paired differences for every incumbent/candidate/layer move.
- Bootstrap decision-bearing success and observed-cost differences over semantic
  families (or required tools for retrieval) 10,000 times with seed `20260816`.
  Report the remaining resource changes as paired descriptive estimates; they
  enter point-estimate Pareto comparisons but not probabilistic dominance
  claims.
- Describe two success changes as tied when their 95% paired interval for the
  difference includes zero; do not use a model leaderboard tie-breaker.
- Report the fraction of incumbent configurations for which a matched system
  move is success-superior, cost-superior, or Pareto-nondominated relative to
  every configuration-only move.
- Stratify by layer and diagnosed-loss quartile, but do not select a model,
  layer, or threshold based on the observed direction.
- Repeat the summary after first averaging within model organization and after
  leaving each organization out.

## Permitted claims

The analysis may support statements such as: for a defined portion of the
finite panel and benchmark population, a layer repair recovers more verified
success per measured dollar or latency unit than switching configurations. It
may also show that a configuration change dominates the available repair, or
that both are needed. It cannot claim that one model architecture is intrinsically
better, that the cheapest API is universally optimal, or that a repair
generalizes beyond the tested pressure and semantic-family population.
