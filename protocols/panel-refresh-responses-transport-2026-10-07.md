# Native Responses compatibility amendment

## Material Passport

- Origin Skill: academic-research-suite / experiment-agent
- Origin Mode: run
- Origin Date: 2026-10-07
- Verification Status: PREPARED; live compatibility not yet established
- Version Label: responses_transport_v4

The author's instruction `遇到问题我都允许重跑，不用再问了` permits bounded
technical recovery under the existing model/provider identities and caps. This
amendment does not change tasks, seeds, scoring, controller decisions, scientific
tool traces, reasoning settings, privacy policy, or the 1024-token completion
limit. DeepSeek remains pinned to the original DeepSeek provider with
data_collection=allow, no provider fallback, and a $15 non-transferable cap inside
the original $560 seven-model cap. All historical receipts and frozen code remain
unchanged. Both existing deterministic HTTP 400 liabilities remain reserved.

## Precisely bounded compatibility attempt

The original structural request and its controller-empty-reasoning v3 supplement
both returned the same HTTP 400. V4 binds both raw and pending receipts by SHA256
and replays all preceding successful provider turns locally. Only those two
specific deterministic errors may be skipped. The first Responses attempt must
be the same condition, repeat, logical request hash and turn as these failures.
Neither a different HTTP 400 nor a model answer is automatically discarded.

Only histories containing controller calls attributed to the unchanged frozen
TraceStateClient emitter use the alternate API serialization. Other histories
retain the existing Chat transport. Controller provenance is captured without
changing its returned response; prefixes alone are not accepted as provenance.
V4 does not fabricate empty reasoning for these controller calls.

The adapter targets the same OpenRouter host at /api/v1/responses. It preserves
visible text, tools, call IDs, argument strings, results and genuine provider
reasoning, and copies the original routing/privacy/price controls. It renames
max_tokens to max_output_tokens with the same 1024 value, retaining provider
reasoning defaults. Streaming and upstream-body debug are observation mechanisms.
No forced tools, structured-output constraint, added reasoning effort or hidden
state may enter through gateway transformation.

## Empirical admission gate

OpenRouter's Responses front door alone does not demonstrate DeepSeek's native
transport. Every accepted Responses response must include one upstream debug body
showing native input items, an unchanged scientific trace and allowed parameters,
one terminal result, observable frozen model/provider identity, and valid token
usage and cost. The initial successful, source-bound projection establishes only
observed compatibility for this route and serializer, not general API support.

Genuine normal max_output_tokens endings remain scored length-limit outcomes.
Malformed model tool-argument strings remain scored outputs. The adapter cannot
faithfully express native reasoning/tool/text interleavings in the existing Chat
projection: it therefore preserves such a literal response and stops admission,
without generating another answer. Observed output beyond 1024 tokens also stops
admission while retaining billing. Refusals and valid wrong answers are never
retried for performance.

## Append-only evidence and accounting

Each wire intent obtains a request-binding sidecar containing authorization,
pending-intent, logical identity, restored wire and adapter hashes. Exact full or
partial SSE bytes are saved independently in an exclusive-create transport
sidecar. Billing is extracted from literal complete terminal frames even when a
later gate rejects the stream; conflicting terminal usage remains unresolved.
The normalized Chat-shaped object is explicitly labeled a local projection, not
a provider Chat receipt, and references both transport and binding hashes.

Offline replay rechecks these source hashes, the current logical request, the
current restored wire, and the recomputed Responses input. A saved verdict is
never sufficient. An interrupted projection can be recovered from its original
SSE without repaying. A deterministic compatibility/parser failure writes an
explicit nonretryable local event; it must not resemble a missing response that
the generic recovery policy would blindly repeat. Such evidence can support a
later separately versioned offline adapter correction, never outcome selection.

Actual network failures without an observed terminal answer retain the existing
bounded technical policy. An explicit transient SSE error is eligible only when
literal, correctly framed evidence has no output/refusal/terminal result; unknown
or malformed frames stop for offline inspection. A partial HTTP read with only
non-output frames can retry, retaining all partial bytes and its cost reservation.
Existing matching pre-wire recovery and response-header sidecars are validated
and reused after an interruption, never overwritten. There are at most five v4
attempts per logical request, distinct
phases, and bounded backoff. Each attempt reserves its entire unresolved cost;
only observed valid costs settle reservations. Budget/deadline/ownership gates
remain unchanged. The worker uses the original DeepSeek exclusive execution lock
and separate v4 status/launch files. The caller must explicitly pass --allow-paid
to launch; offline preparation and tests make no provider requests.

Sources checked 2026-10-07:
- https://api-docs.deepseek.com/guides/tool_calls/#inserting-tool-calls-mid-conversation
- https://api-docs.deepseek.com/guides/responses_api
- https://openrouter.ai/docs/api_reference/errors-and-debugging#debugging
- https://openrouter.ai/docs/guides/best-practices/reasoning-tokens#preserving-reasoning
- https://openrouter.ai/openapi.json
