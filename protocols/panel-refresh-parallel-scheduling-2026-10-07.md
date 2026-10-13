# Two-model parallel scheduling amendment

## Material Passport

- Origin Skill: academic-research-suite / experiment-agent
- Origin Mode: run
- Origin Date: 2026-10-07
- Verification Status: PREPARED (live progress is recorded separately)
- Version Label: parallel_models_v1

The author explicitly approved: `可以并行运行，加快速度` after being told
that Luna and DeepSeek would each remain single-request workers, with caps of
$5 and $15 respectively and $20 combined. This supersedes only the cross-model
serialization in the preceding two protocols. It is not permission to raise
within-model concurrency, token budgets, retry counts, or the spending caps.

Luna continues in its existing process and retains its existing exclusive
`campaign.lock`, which prevents another legacy Luna runner. DeepSeek uses the
existing `deepseek-data-allow-queue.lock` as its exclusive model lock; both the
former sequential launcher and the parallel launcher acquire that same lock,
so they cannot overlap. The queue-only DeepSeek process (PID 45660) was checked
to have made no amended request, then stopped with SIGTERM (exit 143). The live
Luna process was not interrupted. No raw receipt or experiment row was removed.

Each model has its own accounting allocation. The unchanged Luna worker enforces
its $5 cap, including its probe. The DeepSeek worker reads only DeepSeek intents
and receipts, reconciles the one existing pre-provider zero-charge rejection,
and reserves the full next-request liability against its $15 cap. A pending
request belonging to the other model is expected concurrency, not an unresolved
request in this model. The disjoint allocations sum to $20; neither model can
borrow the other's unused allocation. Unknown cost or failure stops the affected
worker without automatic retries. Account credits are checked before launch,
with a $0.50 additional reserve. All probe costs remain included.

Use exactly the frozen Luna plans and DeepSeek data-policy-amended plans; retain
each model's request identity, receipt paths, and append-only result files.
DeepSeek keeps `data_collection: allow` on its pinned origin-provider route,
while Luna remains `deny`. The two-turn DeepSeek tool probe must pass before any
scored condition. No scientific condition, prompt, seed, scoring rule, token
budget, provider, or model parameter changes. Per-model concurrency stays one;
maximum combined concurrency is two. Latency observations during overlapping
execution are not a controlled serial-runtime comparison; retain the scheduling
amendment and actual timestamps in provenance.

The existing 180-second request timeout, zero transport retries, and 24-hour
per-worker execution window remain in force. The remaining five models stay
unauthorized. Current manuscripts must not include partial model inventories.
