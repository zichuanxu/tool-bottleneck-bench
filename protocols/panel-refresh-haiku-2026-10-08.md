# Independent Haiku 5.5 acquisition — 2026-10-08

The author approved the additional budget with the exact reply: **“批准新增 $10 上限”**.
This campaign adds `anthropic/claude-haiku-5.5` to the unified planned panel, giving
22 configurations. It has its own nontransferable USD 10 cap, including probes,
successful calls, known failed-call costs, and unresolved-call reservations.
The preceding seven-model USD 560 allocation and all its per-model caps remain
unchanged. Combined authorization is USD 570, not a shared spending pool.

## Scientific contract

Replicate the unchanged `prepare_panel_refresh.make_model_plans` builder with the
new model identity, this protocol hash and an independent public pricing snapshot.
The nine condition counts are 432 primary capacity, 216 primary discovery,
144 robustness capacity, 72 verification repair, 72 interface guide, 72 sentinel,
16 workflow frontier, 216 structural, and 96 ToolSandbox: 1,336 total.
Task content, menus, condition construction, repetitions, harnesses, evaluator,
sampling, task-adaptive budgets and the 1,024-token completion cap are unchanged.
The original seven-model plans, protocol, source, raw rows, receipts and service
cap map are never edited or repurposed. New evidence stays under
`results/local-only-panel-refresh-haiku-2026-10-08/`.

Pin OpenRouter to the standard first-party `anthropic` route; require parameters,
disable fallback, deny provider data collection, omit unsupported temperature,
seed and parallel-tool-call controls, and preserve provider-default reasoning.
Do not enable server tools, cache directives, batch/flex tiers or model fallbacks.
Opaque provider reasoning/tool-continuation fields are preserved exactly using
the existing receipt client; they do not alter visible task or tool content.

## Pricing and monetary admission

The reviewed public route has a 1,000,000-token context and base input/output
prices USD 0.10/0.50 per million tokens. At a 100,000-token prompt the rates are
USD 0.50/2.50. Cache-read rates are 0.01/0.05, five-minute writes 0.125/0.625,
and one-hour writes 0.20/1.00 per million at the two bands respectively.
The full-context worst input/cache-write rate plus 1,024 output tokens yields
a conservative **USD 1.00256 reservation per attempted request**.
Unknown billing retains that whole reservation; it is never treated as free.
No new attempt is permitted unless known cost plus unresolved reservations plus
the new reservation fits USD 10. New metadata must not exceed frozen pricing at
any token band, add unreviewed price dimensions, reduce required capabilities,
change model identity, or make the selected route unavailable.

Current complete Sonnet 5.5 and Opus 5.5 token profiles imply approximately
USD 3.33 and USD 3.26 respectively before cache discounts. Their largest requests
have fewer than 25,000 input tokens. This is an estimate, not a completion
guarantee; taxes, payment/credit-purchase fees and exchange-rate effects are not
included. The USD 10 operational cap allows additional uncertainty and reservation
headroom, but may stop the run before all conditions finish.

## Probe and technical-retry contract

Two zero-weight logical probe requests use `tool_choice=auto`: request an
`echo_integer(value=7)` call, then supply the actual tool result and require a
final answer of `7`. Both preserve the 1,024-token cap. No scored acquisition
starts unless the same manifest's complete round-trip probe passes.

The author previously allowed technical reruns (“遇到问题我都允许重跑，不用再问了”).
This new campaign independently binds a maximum of **five technical supplementary
attempts per logical request** to its own manifest, authorization and phases.
It does not claim that the old seven-model v2 authorization contains Haiku.
Only missing receipts, transport failures without an observed answer, and
transient non-answer statuses 402/408/429/500/502/503/504 qualify. All attempts
retain separate pending/raw/header records; each unknown charge retains its
reservation. Backoff is bounded at 60 seconds and no call starts after the
48-hour launch deadline. A valid observed response is replayed, never regenerated
because of its score. Refusals, invalid returned model/provider, unknown-cost
answers, permanent errors and malformed/ambiguous non-transient responses stop.

Observed rows are append-only. A failed-closed ToolSandbox row stops acquisition
for separate evidence review, rather than being silently skipped or retried.
Unfinished ToolSandbox trajectories with any prior request likewise stop for
exact-context recovery review; a restart cannot regenerate the world state.
Offline deterministic internal harness replay may reuse exact receipts only if
the original logical request and literal wire payload match. No row overwrite,
score chasing, automatic adjudication or public release is authorized here.

## Activation and evidence

Preparation snapshots public metadata without credentials or paid calls, then
freezes complete source inputs and all nine plans. A separate activation artifact
must bind the reviewed manifest and exact USD 10 cap before a paid launch.
One detached worker owns an independent exclusive lock. Append-only launch intent
and actual-PID markers bind authorization, manifest and a single 48-hour deadline.
Status reports distinguish known receipt cost, unknown reservations and combined
accounted cost. Repeated launch commands cannot create another worker.
The API key is supplied by the launching environment, never read from shell files
by this code and never included in logs or artifacts. Public preparation and
offline checks require no key. No automatic publication or manuscript result
admission is part of this runner.
