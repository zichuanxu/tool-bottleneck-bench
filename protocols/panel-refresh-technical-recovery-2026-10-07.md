# Technical recovery amendment

## Material Passport

- Origin Skill: academic-research-suite / experiment-agent
- Origin Mode: run
- Origin Date: 2026-10-07
- Verification Status: PREPARED
- Version Label: technical_recovery_v2

## Author authority and unchanged scope

The author explicitly instructed: `遇到问题我都允许重跑，不用再问了`.
This supersedes the earlier per-incident retry approval requirement for technical
failures. It does not authorize selecting better model answers, changing tasks,
scoring, seeds, reasoning settings, model/provider identities, privacy policies,
completion/episode limits, or the seven non-transferable model caps totalling
$560. Preserve the original 63 plans and all original receipts byte-for-byte.

Only missing local responses, transport errors, and provider errors 402, 408,
429, 500, 502, 503, 504 may receive automatic additional attempts. No automatic
retry of a valid model answer, content-filter/refusal, successful response with
missing billing, or an unclassified error. Diagnose deterministic 400 errors
before introducing any separately validated transport-only adapter. The known
DeepSeek structural-history incompatibility remains gated in this version.

## Bounded append-only recovery

Replay every earlier successful turn without another API call. Keep the exact
logical request, condition, repeat and turn identity. Each supplementary wire
attempt has a distinct `technical-recovery-v2:N:` phase and an append-only
sidecar linking it to the original intent and this amendment's authorization.
Use at most five new supplementary attempts per logical request in this version,
with 15, 30, then at most 60 seconds backoff; stop after exhaustion. Previously
approved Luna/DeepSeek supplementary receipts remain preferred over new attempts.
Their original missing intents are never discarded or marked free.

All known receipt charges and the full conservative pending-intent liability for
every unresolved attempt count against that model's existing cap. A new pending
intent immediately reserves its liability; replace the reservation only after
observing a valid usage.cost. The separately hash-verified original DeepSeek
pre-provider rejection retains its prior documented zero-cost reconciliation.
No other error, including HTTP 404 from a metadata lookup, is assumed free.

The new worker acquires the exact original per-model execution lock. It may wait
for an existing worker but never interrupts or overlaps it. Completion is checked
against all nine frozen plans after the lock is acquired. All existing rows are
reused. The original launcher and its status files remain unchanged; this layer
keeps separate status, logs and authorization. Detached workers preserve the
existing 48-hour window from the original seven-model supervisor start time.

## Evidence boundary

This is an operational recovery amendment, not a change to benchmark scoring.
No partial seven-model panel is promoted to a completed paper. Every successful
output still requires exact plan closure and final provider/receipt validation.
Budget exhaustion or a transport incompatibility is reported honestly, without
silently changing the scientific design to achieve nominal completion.
