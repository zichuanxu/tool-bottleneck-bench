# Public-observation repair selection: approved development pilot

The author approved the USD 15 ceiling in the current conversation on
2026-09-05, following `docs/policy-validation-preflight-2026-09-05.md`.
That earlier proposal remains a historical pre-approval record. This protocol
is bound by the new plan's timestamp and hashes before any campaign request.
No older frozen protocol, model panel, or outcome is changed.

## Scope and acquisition

The complete inventory is 12 existing development families × four cells
(8/low/raw, 128/low/raw, 8/high/raw, 8/low/generic_compact) × two incumbent
model-provider configurations × two repeats = 192 blocks. Every block has
one probe and seven independent restart actions, or 1,536 episodes. The
incumbents are Qwen3.8 Flash/Alibaba and DeepSeek V4 Flash 0731/Sail Research
FP4. The fixed switch is GPT-5.6 Luna/OpenAI Flex. No provider fallback.

Actions: unchanged retry, 4,096 completion cap, public-description router,
public-handoff controller, canonical delivery, the bundle of those three
repairs plus the 4,096 cap, and fixed switch. Other episodes use a 1,024 cap.
Retaining the probe answer is an additional policy choice with no restart.
All actions use 32 attempted calls, 34 turns, and a 128,000-token stopping
envelope. The token envelope is checked after responses, not a billing cap.
Dynamic oracle shortlisting and hidden-program early stopping are disabled.
Reference program solvability is checked before freezing.

Blocks are shuffled with seed 20260905; restart order rotates over the seven
actions, so positions differ by at most one across 192 blocks. Probe requested
seeds are 910000 + repeat; restart seeds are 920000 + repeat, shared across
restart actions but different from the probe. Requested seeds do not imply
provider determinism. Every episode resets the world, messages and wrappers.

The router reads only user text and tool descriptions. It retains descriptions
with the unique requested route and tools without a route; absent, ambiguous
or malformed routes fail closed to the unchanged menu. It acts before the
first request, unlike the earlier oracle operational shortlisting. The bundle
order is router → controller → canonical delivery → provider meter → provider.
The existing controller and adapter support only raw/compact observations.

## Policy and analysis boundary

Numeric features come from delivered requests and responses, before accessing
the evaluator's outcome: menu count, configured cap/call/token limits, request
and emitted-call counts, public observation/error counts, length/empty finish
counts, and prompt/completion usage. No task/family/intent identifiers, hidden
hashes, grading obligations, operational tool labels or program metadata enter
selection. Receipt hashes are provenance only, never prediction features.

Acquire every action on every enrolled condition. Do not select only failures
or choose the better of two outputs using the grader. Learned policies may use
training-fold outcomes, but held-out action selection must be sealed before
reading that fold's outcomes. Include unchanged retry, retain answer, a
training-selected fixed repair, budget, switch, bundle, and an equally flexible
metadata-only policy. Operational resources are probe plus the chosen action,
not the sum of all counterfactual acquisition arms. Also show no-probe fixed
controls where applicable, so an unnecessary diagnostic probe is not free.

This is development material on previously examined semantic intents, not a
new-intent confirmation. Any cross-validated utility analysis is exploratory;
policy details and sample size for a genuinely independent semantic test require
a later frozen design and separate authorization. Preserve per-model/intent
results and paired uncertainty; do not interpret missing routes as model
negatives or a 4,096 cap as proven removal of truncation. A policy can be useful
without recovering a unique cognitive cause. A negative utility comparison
does not support a positive practical-policy claim.

## Money, missingness and stopping

One writer, one in-flight request, no automatic retries. All three small paid
tool-call preflights, successful/failed/invalid responses and uncertain charges
belong to this new ceiling. Reserve USD 0.30 durably before each request;
admit only if conservative accounted liability plus the reservation is at most
USD 15. The reservation includes the pinned endpoint's maximum prompt envelope,
4,096 completion tokens, and advertised cache-write/long-context tiers. No
web-search tool is enabled. Recheck unauthenticated endpoint prices at freeze.

Persist raw receipts without authorization headers. Require valid positive
token usage and nonnegative reported cost. Settle at the larger of reported
cost and conservative token pricing; if billing is unknown, retain the entire
reservation and halt. Stop on served-model mismatch or transport failure. Never
automatically repeat an interrupted episode. Unfinished or transport-invalid
blocks remain missing, with denominators/bounds recorded before any pooled
policy claim. The budget gate is not a stopping rule based on success outcomes.

Raw records and material counterevidence remain intact. Irrelevant developmental
details need not occupy the paper, but retained scientific claims must remain
consistent with all relevant valid evidence. No publication or remote push is
part of this approval.
