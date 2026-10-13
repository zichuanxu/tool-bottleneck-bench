# Transport recovery, amendment 2

The author approved the proposed recovery with “重试” on 2026-09-05, after the
stopped state was inspected. The original USD 15 ceiling, inventory, order,
providers, seeds, scientific budgets, and development-only inference boundary
remain unchanged. No completed scientific outcome is rerun or selected by result.

Receipt 0001465 is HTTP 200 with an in-band 502 `provider_unavailable`, from the
pinned DeepSeek V4 Flash 0731 / Sail Research endpoint. Its explicit error says
the streamed tool call was incomplete. The unchanged provider rejected it
before parsing or executing any of its partial calls. The episode had two
accepted responses (0001463–0001464); these are replayed verbatim, with every
serialized request and the interrupted next request checked for exact equality.
The saved 401st row is transport missingness, not a model failure. Preserve it.

The interrupted request is allowed at most six **new** attempts, additional to
the failed recorded attempt. Subsequent requests allow at most six total
attempts. Eligibility extends amendment 1's empty 429 rule only to the exact
observed HTTP-200/in-band-502 incomplete-stream error, with the same served
model, provider, generation ID and valid usage. Other errors, ambiguous billing,
or different identities still halt. Retry the unchanged request, not the episode.
The existing bounded backoff (at least 60/120/180/240/300 seconds) is reused;
its legacy `rate_limit_backoff` event is preceded by `retryable_inband_502` for
this error. Six exhausted attempts require another explicit recovery decision.

Every attempt is durably recorded and conservatively charged before retry.
The old 502 reported usage.cost = 0, but its upstream inference cost is nonzero;
do not describe it as free. Its token-priced conservative liability already
enters the original ledger. Keep the original uncertain-429 reservation too.
Prefix responses are neither admitted nor charged again during reconstruction.
Discarded transport calls do not count as accepted agent responses or executed
tools, but their reported and token-priced costs remain acquisition costs.

Store a successful recovery in a separate hash-linked `transport-recovered.json`.
The raw failed row and receipts remain immutable. Only after the full original
1,536-episode inventory is complete, construct `analysis-input/rows.jsonl` by
substituting this one transport-missing row with its recovered outcome, retaining
a source map and using all original receipts for costs. No other missing outcome
may be silently substituted. Reuse the unchanged analysis over this explicit
view. Episode token totals are accepted-response tokens; failed-generation usage
is not an invented agent observation and is not silently added to that metric.

This is a disclosed transport deviation with a wall-clock gap, not an independent
repeat or a new scientific result. No inference is made from an incomplete pilot.
