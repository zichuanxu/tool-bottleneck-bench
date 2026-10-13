# Hosted Evaluation System Card

## Released panel

The scientific unit is a model–provider–harness configuration: a pinned served
model, provider route, prompt, tool policy, and budget. **Twenty-one** configurations enter the common Capacity and
Discovery core; **twenty** enter the repair, sentinel, and workflow-frontier
panels. The historical ToolSandbox external-rank criterion is a separate
5-configuration block. These counts are the `coverage_counts` of
`results/reporting-panel-2026-10-13-final21/release-panel.json` (`core_descriptive` 21,
`auxiliary_descriptive` 20) and its `external_rank_models`.

| Model | Pinned route | Access | Core | Auxiliary |
|---|---|---|---|---|
| Claude Haiku 5.5 | OpenRouter § | API | yes | yes |
| Claude Opus 5 | OpenRouter / Anthropic | managed | yes | yes |
| Claude Opus 5.5 | OpenRouter § | API | yes | yes |
| Claude Sonnet 5 | OpenRouter / Anthropic | managed | yes | yes |
| Claude Sonnet 5.5 | OpenRouter § | API | yes | yes |
| DeepSeek V4 Flash 0731 | OpenRouter / DeepInfra FP4 | open weight | yes | yes |
| DeepSeek V4 Pro 0813 | OpenRouter / CoreWeave FP8 ‡ | managed | yes | yes |
| DeepSeek V4.1 Flash | OpenRouter § | API | yes | yes |
| Gemini 3.6 Flash | OpenRouter / Google AI Studio flex | managed | yes | yes |
| Gemini 3.8 Flash | OpenRouter § | API | yes | yes |
| GLM-5.3 | Z.AI direct | managed | yes | yes |
| GLM-5.3 Flash | Z.AI direct | managed | yes | yes |
| GPT-5.6 Luna | OpenRouter / OpenAI | managed | yes | yes |
| GPT-5.6 Sol | OpenRouter / OpenAI fast | managed | yes | yes |
| GPT-5.6 Terra | OpenRouter / OpenAI flex | managed | yes | yes |
| GPT-6 Luna | OpenRouter § | API | yes | yes |
| GPT-6.1 Sol | OpenRouter § | API | yes | yes |
| HY4 Preview | OpenRouter † | managed | yes | yes |
| Qwen3.7 Flash | OpenRouter / Alibaba | managed | yes | yes |
| Qwen3.8 Flash | OpenRouter † | managed | yes | no |
| Qwen3.8 Max (0902) | OpenRouter § | API | yes | yes |

† The served sub-provider for these two configurations is acquisition
metadata that no released artifact carries; `release-panel.json` records
`openrouter`, and that is what the table now states.
‡ The DeepSeek V4 Pro sub-provider route is corroborated only by the `note`
field of the released pricing config, not by the scored rows.
§ For the eight configurations added in October 2026 the table states the
route and access that `release-panel.json` records.

All twenty-one configurations are reported together; the table is unified because
acquisition time is provenance, not a scientific factor. GPT-5.6 Sol is served
from a route-amended selector after a
transport failure, and Claude Sonnet 5 was acquired across three continuation
directories; both are described in
[the benchmark card](benchmark-card.md#recovery-episodes-and-private-records).

## Common loop and budgets

Configurations use native Chat Completions tools, automatic tool choice,
disabled parallel calls, identical English prompts, and common budgets. The
budgets are part of the measured unit: several reported effects are partly
effects of these numbers, not of the model alone.

| Harness budget | Frozen value |
|---|---|
| Sampling temperature | 0 |
| Seed | condition-bound integer, requested only where the provider supports it; unsupported providers run at their default nondeterminism |
| Completion tokens per request | 1,024 (`max_completion_tokens`) |
| Reported tokens per episode | 128,000 (`max_total_tokens`) |
| Tool-call limit | certified minimum + 2 (`tool_call_slack`) |
| Turn limit | tool-call limit + 2 (`turn_slack`) |
| Infeasibility stop | `early_stop_infeasible` enabled |
| Menu narrowing | `dynamic_shortlist` enabled; routed exposure narrows to the ground-truth operational set |
| Transport | `transport_retries` 5, `provider_allow_fallbacks` false, 300 s request timeout |

The 1,024-token completion cap and the 128,000-token episode budget are the two
budgets that bind most often: a request that exceeds the completion cap ends the
episode with `model_finish_length`, and a 30-call program exhausts the episode
budget at a median of 21 executed calls. Both are reported as harness
boundaries wherever a result depends on them.

Model-generated invalid argument JSON is an agent failure and is never repaired
by the harness. Provider/transport failures fail closed and remain distinct from
agent stages. Plans store environment-variable names, never credentials.

## What is in the public library and what is not

The public-handoff controller (`TraceStateClient`), the lossless canonical
delivery adapter (`CanonicalDeliveryClient`), the structural task compiler, and
the three controller audits the paper names — hidden-program invariance,
public-handoff sensitivity, and fail-closed ambiguity — are shipped in
`src/bottleneckbench/structural_holdout.py` and are exercised over all 24
unseen-intent 30-call tasks by `tests/benchmark/test_structural_holdout.py`.
The controller's synthetic tool calls carry call ids beginning with
`structural-controller-`, which is how a released row is split into
model-issued and controller-issued calls.

The **hosted execution runner is outside the public projection**. The code that
spends provider credit, meters budgets, quarantines transport failures, and
writes result rows is private; everything shipped here runs offline against the
in-process sandbox. A reader can therefore recompile every task, rerun every
audit, and replay every analysis, but cannot reissue the hosted calls.

## Reporting boundary

Results describe the pinned configurations above, not model weights in
isolation. Provider route, served implementation, tokenizer, protocol, retries,
and harness policy are part of the reported unit.
