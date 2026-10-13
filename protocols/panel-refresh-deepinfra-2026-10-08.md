# DeepSeek V4.1 Flash: isolated OpenRouter / DeepInfra FP8 replacement

## Authority and configuration

On 2026-10-08 the author approved: “允许验证并完整重跑”, in response to
the question recorded verbatim in the preparation manifest. This authorizes a
zero-weight compatibility probe followed by a complete, separate 1,336-condition
configuration through OpenRouter, pinned to `deepinfra/fp8`, quantization FP8,
with fallbacks disabled and data collection allowed. It does not authorize a
direct DeepSeek endpoint, a different model version, or an increased budget.

The catalog canonical identity must remain
`deepseek/deepseek-v4.1-flash-20260910`. All nine scientific blocks, condition
IDs, tasks, observations, controller policy, scoring, seeds, harness budgets,
temperature zero, maximum completion tokens 1,024, and serial within-model
execution are unchanged. The new provider configuration explicitly requests
`reasoning: {enabled: true, effort: high}` on every request. High was the
observed native default in the previous upstream debug evidence; here it is an
explicitly declared request setting. We do not infer or claim measurement of an
undocumented provider-internal numerical reasoning budget.

Public model and endpoint metadata, retrieval times, literal bytes, prices,
and runtime source dependencies are frozen by hash before paid work. Metadata
must advertise the required reasoning and tool controls. Live public preflight
checks the same canonical model, pinned route and price ceilings. At the
reviewed promotional rates, maximum input/output/cache-read prices are
$0.14/$0.42/$0.0042 per million tokens; rate increases stop the stage. No cache
saving is assumed in the conservative per-request reserve.

## Compatibility experiment: zero scoring weight

The probe is the exact first original structural trace-state continuation that
failed with HTTP 400. A local, network-free replay of the original first
successful provider response passes through the unchanged structural harness
and deterministic controller. The resulting second logical request must have
the exact original request hash. It includes authentic previous provider
reasoning and the deterministic controller's tool call and tool result. The
controller's absent reasoning remains absent: no reasoning text or empty
provider reasoning fields are fabricated.

Only this reconstructed continuation is sent to the new route, with the new
declared provider and high-reasoning configuration. The response must identify
DeepInfra and the frozen model, carry actual usage and cost, remain within the
1,024-token cap, and contain observable genuine reasoning text or reasoning
details. Its visible answer is not tested for benchmark correctness: a valid
wrong answer, valid length-truncated answer, or valid tool call is sufficient
for transport compatibility. A refusal or malformed/invariant-failing observed
answer stops the gate without an outcome-selective retry. The full raw receipt,
request binding, prior source hashes and zero-weight declaration are retained.
The compatibility proof is revalidated before launch and worker admission.

An additional fresh two-turn, zero-weight tool roundtrip verifies continuation
with reasoning produced by DeepInfra itself. Its first request offers one
`probe_echo` tool under automatic tool choice; the second preserves the exact
returned assistant reasoning, call IDs and arguments, followed by a
deterministic echo observation. Tool-call argument correctness and final-answer
correctness are not scored. A valid but incorrect argument does not fail the
gate. A response that never exercises the offered tool leaves this compatibility
requirement unverified and is preserved without resampling. This adds two
baseline probe requests, for three baseline requests across both probes; all
charges and any technical supplements remain inside the same $15 allocation.

The scored campaign starts from fresh request scopes, receipts and outputs.
No original-provider scored row, response or cache is reused in any of the
new configuration's 1,336 conditions. The probe's historical entry response
is used only for constructing the compatibility check and never enters a
scored run. The original partial configuration remains local historical
evidence and must not be mixed into this replacement's results.

## Accounting, recovery, and execution

The cap remains the original DeepSeek allocation of $15. The frozen original
known charges and unresolved full-cost holds count against it alongside all
new probe/scored charges and unresolved holds. At preparation, the anticipated
original liability is $2.378908086; the manifest records the actual recomputed
value and binds the complete old receipt inventory. The new conservative
reserve is full advertised context times the maximum input rate plus 1,024
output tokens; every unresolved request retains that entire reservation.
Only observed finite nonnegative provider `usage.cost` settles a reservation.
No unknown charge is recorded as zero, and no local estimate is labeled a
provider charge. Auto-top-up never relaxes the cap.

There is one 48-hour stage deadline, starting with execution authorization
preparation, shared by the probe and scored campaign. Both take the original
DeepSeek model lock, so old and replacement workers cannot overlap. The new
configuration has its own plan, raw receipt, binding, status, and trajectory
directory. Every receipt has a configuration/source-bound request mapping;
logical request serialization is never mutated when authentic reasoning is
restored to the wire payload.

At most five supplementary attempts per logical request are allowed only for
explicit technical non-answers (transient provider status or actual transport
failure) or interrupted wire attempts after durable wire-start evidence.
All previous receipts and holds remain. Local serialization, credential,
invariant, or persistence failures are not classified as network retries.
Nonempty unparseable partial application bytes are also nonretryable because
they may contain an already observed answer. Durable logical identities bind
the base phase, condition, repeat, attempt and turn to one request/wire hash;
supplement indices must be contiguous and cannot follow any observed answer.
Every resumed reservation must exactly equal the frozen conservative liability.
Any observed answer, refusal, incompatible provider, missing billing,
token-cap violation, or parser/proof rejection without an explicit technical
non-answer stops for offline inspection; no scored answer is discarded for
resampling. Retries use bounded backoff and the same condition and payload.

Preparation/check commands cannot generate. Compatibility, launch and worker
commands require an explicit `--allow-paid` flag; launch additionally requires
a successfully bound compatibility proof. This protocol does not claim
compatibility, experiment completion, or full-paper readiness before those
gates and the complete nine-block coverage have actually passed.

Workers require the launcher's exact token and actual child PID. An unfinished
ToolSandbox condition with any prior scored intent cannot be resumed by
recreating its mutable-clock world; it requires a separately reviewed,
evidence-preserving recovery. Completion of collection is reported only as
`acquisition_complete_pending_audit`, with `release_admitted: false`, until
the independent source, row, cost and provenance closure audit is complete.
