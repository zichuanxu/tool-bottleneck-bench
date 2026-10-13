# October 2026 panel refresh: preparation only

## Material Passport

- Origin Skill: academic-research-suite / experiment-agent
- Origin Mode: plan
- Origin Date: 2026-10-07
- Verification Status: UNVERIFIED (hosted execution has not started)
- Version Label: panel_refresh_v1

## Scope and authorization

The author requested seven additional OpenRouter models and removal of seven
previous configurations from current reporting. The reporting selection is
recorded separately from immutable historical observations. The run stops before
any paid request. Earlier campaign spending approvals do not authorize this run.
No model outcome is expected or required for admission to the reporting panel.

New IDs: `anthropic/claude-opus-5.5`, `anthropic/claude-sonnet-5.5`,
`qwen/qwen3.8-max-0902`, `google/gemini-3.8-flash`, `openai/gpt-6-luna`,
`openai/gpt-6.1-sol`, and `deepseek/deepseek-v4.1-flash`.

## Fixed experiment contract

Each new configuration receives the existing complete 1,336-condition inventory:
432 primary capacity, 216 discovery, 144 fixed-menu robustness, 72 verification
repair, 72 interface-guide repair, 72 sentinel, 16 workflow frontier, 216 structural,
and 96 matched ToolSandbox conditions. Total: 9,352 scored conditions, not API
requests. Existing task families, paired conditions, seeds, scoring functions,
and harness limits are reused. Completion tokens remain capped at 1,024 per
request; the task-adaptive episode policy retains its 128,000-token total limit.
The ToolSandbox block keeps its existing scenario and message limits.

This is a descriptive extension of the existing design, not a new confirmatory
replication. Do not rerun retained models or introduce outcome-selected repairs.
Additional historical crossover or policy pilots are outside this campaign.
New configurations join the one reporting panel only after all nine blocks pass
exact-inventory completion, model identity, usage and infrastructure checks.

## Transport and monitoring

Use the seven pinned standard origin-provider routes in the timestamped public
OpenRouter snapshot; provider fallbacks are disabled. Use temperature zero when
supported, otherwise omit it; omit seed and parallel-tool-calls parameters. The
internal inference seeds remain recorded, but providers are not promised seeded
sampling. Leave reasoning at the provider default, as in the existing harness;
record its advertised setting in metadata. A new model's actual response and
reasoning behavior is unverified until a paid probe is explicitly authorized.
Preserve raw signed/encrypted reasoning and tool-call signature metadata across
tool continuations, bound to the tool-call IDs within the same condition.
Never silently enlarge completion limits or disable reasoning to make a probe
pass. Preserve refusals and failures; do not bypass provider safeguards.

Run one request at a time, with a 180-second request timeout and zero automatic
transport retries. This changes pacing, not the task or token contract. Save a
durable intent before every request and the raw response afterward. A missing
or malformed cost, unknown request outcome, refusal, model mismatch or provider
failure stops further spending; unresolved attempts retain their conservative
liability and cannot automatically be retried. Successful receipts can be
replayed after a local interruption. Resume only the exact same plan.

## Cost and launch boundary

Prices are public metadata; estimates replay historical observed input/output
tokens at new route prices without assuming cache discounts. Estimates are not
quotes or hard maxima. Mandatory reasoning can consume more tokens or leave no
visible output under the fixed cap. Provider transport probes are an explicit
separate first phase: seven two-turn probes (14 planned API requests), each
covering a tool call, its tool result and the final answer, with no scientific
weight. Probe charges are included in the eventual cumulative approved cap.

The preparation/check command never loads an API key or calls a generation API.
The run command requires an explicit paid flag, approved budget, and the exact
manifest hash. No authorization file is created by preparation. Budget approval
must be obtained from the author after the estimate is reviewed. The budget
counts all probe and scored requests in this campaign. Do not rely on auto-top-up.

## Reproduction and completion

Preparation: `.venv/bin/python experiments/prepare_panel_refresh.py prepare`.
Offline check: `.venv/bin/python experiments/prepare_panel_refresh.py check`.
Paid probe and execution entry point: `experiments/run_panel_refresh.py` (its
help lists the deliberately required authorization arguments).

Raw prior JSONL records and frozen protocols stay byte-identical locally. Current
paper statistics must be recalculated from the selected 14-model evidence;
new-model results are absent until measured. The final target is 21 models.
Excluded models must not remain in the current manuscript, figures, tables,
references, pooled statistics or new release exports. Historical snapshots are
local evidence, not current manuscript artifacts. Public export remains on hold
while the panel revision and new campaign are pending.
