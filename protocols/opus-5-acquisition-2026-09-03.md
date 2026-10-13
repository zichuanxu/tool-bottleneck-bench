# Claude Opus 5 first-class configuration acquisition

Status: frozen before any Claude Opus 5 benchmark outcome was observed. The
only permitted pre-score activity is the zero-weight transport probe defined
below.

Transport amendment 1 (2026-09-03T14:48:19Z): the first logical probe request
exhausted its five fixed retries because the route returned six HTTP-200
envelopes containing the same in-band `503 Overloaded` provider error. It
produced no choices, token usage, cost, or scientific outcome. The raw attempts
and their hashes are retained in
`results/opus-5-acquisition/transport-probe-attempt-1.json`. Before any scored
outcome, the receipt accounting was clarified to classify HTTP errors,
transport exceptions, and in-band provider errors together. One identical
outcome-blind probe retry is permitted after this failed request is quarantined;
no route, request, inventory, prompt, budget, or scientific rule is changed.

Bookkeeping amendment 2 (2026-09-04): the permitted identical probe retry also
exhausted all six attempts with in-band 503 errors and no model output. Both
probe records and the attempt-2 freeze are preserved. Before any scored call,
local audit corrected output paths to live under this acquisition's execution
directory and made every ToolSandbox execution exception completion-blocking.
Tests cover both guards. This changes no scientific condition or model-visible
input, and grants no additional probe attempt. Scored acquisition remains
blocked by the absent passing probe and insufficient funds.

Budget amendment 3 (2026-09-04, before any scored call): apply the account and
liability guard before every wire attempt, including fixed transport retries,
not only before a logical request. Unknown response cost retains the full
per-attempt worst-case liability in the raw receipt until separately reconciled;
it is never presumed free. The total guard includes probe and scored receipts.
The maximum two-attempt liability is conservatively raised to USD 20.0512,
using the highest advertised input rate (one-hour cache write), even though
the request adds no cache directives. This reserve may stop acquisition before
the nominal USD 180 cap; it is not a prediction of actual per-request cost.
This may stop retries early for lack of budget, never for a scientific outcome.
The shared transport source is now explicitly hash-bound. Prior freezes and
raw attempts remain immutable; no additional probe is authorized by this change.

Durability amendment 4 (2026-09-04, before any scored call): every wire attempt
now persists a request intent and its liability before dispatch. A process crash
without a response therefore leaves an explicit unresolved intent, blocks silent
reissue on resume, and retains its worst-case cost liability. Pending intents are
not counted as responses. The retry/crash guard has an offline test. No scientific
condition, route, prompt, completion limit, or scored outcome changes.

## Scientific role

Funding and resumption amendment 5 (2026-09-04, before any scored call): the
user explicitly instructs use of the OpenRouter credential from their shell
startup configuration and confirms automatic top-up. The credential is loaded
without printing or storing its value. The account is the same as the original
freeze: USD 185 credited and USD 170.237685988 used at recheck. The USD 180
new-spend cap, USD 0.50 cash reserve, and worst-case per-attempt liability remain
unchanged; automatic top-up is not an increase in the authorized spend cap.
The previous requirement to pre-fund the entire run is superseded by live cash
checks before every wire request. With two workers, a request waits while
another funded in-flight request holds the cash reserve. If no request is in
flight and real cash cannot cover one liability plus reserve, execution stops;
future top-up is never treated as already available cash. Top-up changes credits,
not cumulative usage, and cannot reset the experiment's spending ledger.

This user-directed resumption authorizes exactly one additional identical
three-step non-scored probe, isolated as logical attempt 3. Attempts 1 and 2
remain immutable, and no scientific outcome was observed in them. The USD 0.05
probe subcap and five fixed transient retries remain, with budget checks allowed
to stop retries early. No further probe is authorized if this attempt fails.
The prior active freeze, runner, protocol, budget and endpoint snapshot are
preserved under `results/opus-5-acquisition/freeze-history/durability-4/`.
Fresh model/endpoint metadata must agree on the standard Anthropic route,
pricing, accepted served ID and native-tool support before the new freeze.
All 1,336 condition IDs, prompt contracts, grader rules and model parameters
must remain identical. No payment setting is changed by this amendment.

`anthropic/claude-opus-5` is the twenty-first first-class configuration. Once
all registered rows close without infrastructure failures, it enters the same
primary panel and estimands as the other twenty configurations. Acquisition
time does not define a cohort, extension estimand, holdout, or separate results
section.

The configuration reuses the exact Claude Sonnet 5 inventory and measurement
contract: semantic families, compiled tasks, prompts, native tool schemas,
model-visible menus, transcript handling, exact grader, episode policy, and
analysis rules. The 1,336-condition inventory is fixed as follows: 432 primary
capacity, 216 Discovery, 144 fixed-menu robustness, 72 verification repair, 72
interface-guide repair, 72 stochasticity sentinel, 16 registered workflow
frontier, 216 structural repair holdout, and 96 matched ToolSandbox menu
conditions. These secondary blocks apply the already-used Sonnet eligibility
rules; no block is expanded merely to accommodate Opus.

## Frozen route and request policy

- Requested model: `anthropic/claude-opus-5`.
- Accepted served IDs: the requested ID and
  `anthropic/claude-opus-5-20260723`.
- Endpoint: `https://openrouter.ai/api/v1/chat/completions`.
- Provider route: `provider.only=["anthropic"]`, the standard Anthropic route.
- Fallbacks: disabled. Data collection: denied. Required parameters: enabled.
- Service tier: omitted; `anthropic/fast` is explicitly excluded.
- Temperature and seed: omitted, matching the Sonnet route contract.
- Parallel-tool-call flag: omitted; native tools and tool choice remain active.
- Completion limit: 1,024 tokens per provider request. Episode accounting limit:
  128,000 tokens. Tool-call and turn slack: two. Dynamic shortlist and
  infeasibility early stop: enabled.
- Timeout: 180 seconds. Fixed transport retries: five. Workers: two. Provider
  concurrency cap: four. Minimum request interval: 0.5 seconds.
- No proxy is configured or permitted.

Reasoning parameters are supported by the selected endpoint but are not sent:
adding one would change the shared request contract. Raw response receipts
retain any reasoning and detailed usage fields the provider returns.

## Budget and failure policy

The exact observed 1,336-row Claude Sonnet 5 token footprint is 29,309,027
prompt and 783,386 completion tokens. At the live standard-route Opus prices,
the uncached point estimate is USD 166.129785. The hard new-spend cap is USD
180.00, including the probe, with USD 20.0512 reserved for two maximum-context
in-flight requests and USD 0.50 required to remain in the account. Every paid
request is guarded against both live account usage and raw-receipt cost.

The live account snapshot at freeze has USD 14.762314012 remaining, so the
scored launch gate is closed by USD 165.737685988. No scored call may start
until the full hard-cap liability is funded. The separate probe subcap is USD
0.05.

Each planned scientific condition has one registered attempt. The transport
layer may perform only its five fixed retries for typed transient failures.
Provider or transport failures are retained as zero-weight infrastructure
evidence, never graded as model behavior, and close the scientific completion
gate. They cannot trigger a model-specific prompt, budget, or outcome-dependent
exclusion. Completed rows are append-only and skipped on resume; exact raw
provider bodies are stored in the ignored private receipt root and bound by a
public hash manifest.

## Non-scored probe

After all configuration, pricing, inventory, source, and budget hashes are
frozen, one three-request probe may verify: a forced native tool call and JSON
arguments, a tool-result continuation ending normally, a one-token completion
ending by length, served-model identity, and detailed usage/cost fields. Probe
requests use their own condition scope, are marked scientific weight zero, and
are excluded from every benchmark row and analysis.

## Analysis and stopping rules

The scored run must close all 1,336 exact condition IDs with no duplicates and
no infrastructure rows before integration. Then the authoritative panel,
pooled estimates, confidence intervals, heterogeneity, failure accounting,
costs, tables, figures, macros, paper, and documentation are regenerated over
twenty-one configurations. Until that gate closes, all released twenty-model
statistics remain the valid baseline and no partial Opus result may be quoted.

No unrelated paid experiment may begin before this integration. Any later
hosted control or repetition requires its own outcome-blind protocol, inventory,
analysis, and budget freeze.
