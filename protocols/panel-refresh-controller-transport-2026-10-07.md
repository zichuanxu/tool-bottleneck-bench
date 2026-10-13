# Controller-origin metadata transport amendment

## Material Passport

- Origin Skill: academic-research-suite / experiment-agent
- Origin Mode: run
- Origin Date: 2026-10-07
- Verification Status: PREPARED
- Version Label: controller_transport_v3

The author's instruction `遇到问题我都允许重跑，不用再问了` permits this
bounded technical recovery under the existing seven-model and per-model caps.
This amendment does not change the frozen scientific request, controller, tool
history, scoring, model/provider, endpoint, reasoning mode or budgets.

DeepSeek's structural call returned HTTP 400 after a deterministic controller
tool call. Actual provider reasoning was already retained. The frozen
TraceStateClient._synthetic method explicitly returns reasoning="", zero tokens,
and usage_source="deterministic_structural_controller". Its empty reasoning was
not serialized by the generic chat-message construction.

The new transport layer records the actual return of that unchanged frozen
method, verifies the exact tool call and empty reasoning/token provenance, and
adds reasoning_content="" only to those recorded controller-origin assistant
turns on the DeepSeek wire. This is controller metadata, not a fabricated
provider reasoning response or an empty replacement for genuine reasoning.
Provider messages retain their original reasoning fields unchanged. Prefixes
alone are not provenance. The observer wrapper returns the original response
object and does not change controller decisions or counts. This also handles
canonical-delivery arms, where wire observations differ from the controller's
pre-adaptation observations; provenance is captured at the original emitter.

The first compatibility attempt supplements exactly the known 400 intent, keeps
its full original $0.3158016 liability, and replays preceding successful turns.
Only that exact failed raw receipt is classified as compatibility-retryable.
A new deterministic 400 does not cause repeated identical retries. Transient
errors follow the existing recovery-v2 bounded policy. Normal wrong answers and
normal length-limit outcomes remain scored, never retried for performance.

Every adapted request writes an append-only sidecar with the controller's exact
call/provenance, logical-request hash, original and adapted wire hashes, and the
authorization hash. Original receipts, plans and source files are immutable.
The provider and served-model identity and usage.cost must be recorded before
admitting the result. A successful request establishes only observed acceptance
of this exact serialization on this fixed route, not general native Chat API
support or a proof of unchanged provider-internal semantics.

DeepSeek's current documentation instead recommends native Responses/Anthropic
formats for inserted client tool calls. A separately tested Responses adapter
exists as an unused alternative; this narrower amendment intentionally retains
the original API surface and must be empirically checked before declaring the
problem resolved. No reasoning settings are changed.

Sources checked 2026-10-07:
- https://api-docs.deepseek.com/guides/tool_calls/#inserting-tool-calls-mid-conversation
- https://openrouter.ai/docs/guides/best-practices/reasoning-tokens#preserving-reasoning
