# Transport-only resumption, amendment 1

On 2026-09-05 the author instructed: resume the experiment and finish the paper.
The initial run stopped on upstream Alibaba HTTP 429, receipt 0001208, after
326 scored episodes. Four successful responses (0001204–0001207) already exist
in the interrupted episode. The original protocol, plan, receipts and scored
rows remain immutable. This amendment is frozen before any resumed request.

Reconstruct the interrupted world and conversation by feeding the four original
responses through the unchanged provider parser and runtime. Check every
serialized request, including the failed next request, against its saved receipt.
Do not resample the prefix or repeat a completed episode. New episodes retain
the original inventory, order, providers, seeds, caps and task semantics.

Only a bare HTTP 429 error with no choices, generation identifier or usage is
eligible for a same-request retry. Retain each error receipt and count its full
USD 0.30 reservation permanently toward the original USD 15 ceiling unless
subsequently reconciled by independent billing evidence; do not assert zero
actual charge. Retry at most six attempts per request, waiting at least 60,
120, 180, 240 and 300 seconds, and honoring a larger Retry-After value. No
provider fallback, model switch or outcome-dependent retry. Other transport
or billing ambiguity still stops execution. The paid runner remains single-flight.

Resumption spans a wall-clock gap and is a transport protocol deviation, not a
new scientific experiment or independent confirmation. All completed scientific
outcomes remain eligible irrespective of direction. The interrupted response
prefix is charged exactly once; retry reservations are reported separately
from token-priced successful generations. The analysis must skip only audited
429 receipts in the unknown-charge ledger, never model failures. The original
development-only inference boundary is unchanged.

Official transport reference consulted before resumption:
https://openrouter.ai/docs/api/reference/errors-and-debugging
