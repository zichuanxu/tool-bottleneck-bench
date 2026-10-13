# Structural Repair Holdout Protocol

## Status and provenance

This is an adaptive follow-up study, not an amendment to the running prospective
matrix. It was designed after inspecting the implementation and the repeated
sentinel, then strengthened by two outcome-blind construct audits before any
prospective outcome analysis. The sentinel showed that dynamic routing changes
behavior whereas the current verification and interface arms only add
instructions. Independently, the retained completed block showed a diagnosed
loss for the lossless dictionary-coded compact interface but not a stable loss
for linked observations. That retained result generates the adapter hypothesis;
only the new families below may confirm it. The first construct audit showed
that core low/high workflow
twins change evidence fragmentation and the exact provenance source set; pure
horizon repair must therefore use the fixed-answer workflow frontier.
The second audit found that the initial trace-state proposal both changed the
first-turn menu and left all 30 decisions with the model. That intervention
confounded workflow orchestration with visible-menu routing while failing to
remove the repeated model-decision horizon. The corrected intervention below
preserves the baseline menu and prompt, and delegates only deterministic public
handoffs to a harness executor after the model has selected a valid entry tool.
Consequently, no existing development, main, transfer, or sentinel family may
confirm the claims below. Confirmation requires a new content-addressed holdout
split.

No prospective core, full repair-matrix, frontier, or external-criterion result
was analyzed when this protocol was written.

## Scientific question

When isolated call-horizon or representation pressure causes a verified task loss, does a
repair implemented at that system layer selectively recover the stressed arm
without materially helping the matched low-pressure control?

The study compares structural system changes, not stronger prompting:

1. **Trace-state orchestration** executes deterministic, read-only public
   handoffs after the model has selected the initial operation, while preserving
   the complete public trace and tool-call budget.
2. **Canonical observation adaptation** changes how a lossless observation is
   delivered while preserving every source value and provenance edge.

## New holdout population

- Add a separate `repair_holdout` compiler namespace; do not modify or relabel
  existing split identities.
- Generate 24 read-only semantic families with indices 300--323 and seeds
  600--623. Each family instantiates a different semantic decision rule; the
  design must not obtain its nominal sample size by repeating the six existing
  read-only intent templates. Stratify six rules each across accounting and
  reconciliation, communication and SLA, document and governance, and
  scheduling and coordination. Within every stratum, freeze three positive and
  three negative decisions, for 12 of each overall. No family, intent rule,
  record identifier, lookup token, prompt, or world seed may occur in an
  existing artifact.
- Freeze the 24 intent names, predicates, required facts, connector routes, and
  exact labels before compiler implementation. The intended rule inventory is:
  `invoice_total_match`, `receipt_currency_match`, `vendor_identity_match`,
  `payment_term_compliance`, `duplicate_invoice_risk`,
  `approval_evidence_complete`, `response_within_sla`, `sender_authorized`,
  `required_recipient_covered`, `escalation_acknowledged`,
  `attachment_requirement_met`, `thread_resolution_complete`,
  `agreement_execution_ready`, `registration_current`,
  `policy_exception_present`, `document_version_current`,
  `evidence_bundle_complete`, `retention_window_valid`, `overlap_conflict`,
  `attendee_quorum_met`, `prerequisite_complete`, `review_window_valid`,
  `timezone_alignment_valid`, and `deadline_feasible`.
- The public request states the applicable predicate, both permitted labels,
  the three required facts, and the source route verbatim. The task therefore
  tests evidence acquisition and execution, not undisclosed business-policy
  guessing. Freeze the inventory as follows (`+` means the positive predicate
  is true in that family and `-` means it is false):

| Stratum | Intent and labels | Public positive predicate | Route | Outcome |
|---|---|---|---|---|
| accounting | `invoice_total_match`: `match` / `mismatch` | `invoice_total == receipt_total and currency == USD` | accounting, files, email | + |
| accounting | `receipt_currency_match`: `currency_aligned` / `currency_misaligned` | `invoice_currency == receipt_currency and fx_status == not_required` | files, accounting, email | - |
| accounting | `vendor_identity_match`: `vendor_consistent` / `vendor_inconsistent` | `invoice_vendor == contract_vendor and registration_status == active` | accounting, files, email | + |
| accounting | `payment_term_compliance`: `compliant` / `non_compliant` | `payment_date <= due_date and approval_status == approved` | accounting, calendar, files | - |
| accounting | `duplicate_invoice_risk`: `duplicate` / `not_duplicate` | `same_invoice_number and same_amount and same_vendor` | accounting, files, email | + |
| accounting | `approval_evidence_complete`: `complete` / `incomplete` | `approver_present and receipt_present and policy_version_current` | email, files, accounting | - |
| communication | `response_within_sla`: `within_sla` / `outside_sla` | `response_at <= due_at and owner_acknowledged` | email, calendar, files | + |
| communication | `sender_authorized`: `authorized` / `unauthorized` | `sender_domain_allowed and authentication_status == pass and sender_identity_resolved` | email, files, accounting | - |
| communication | `required_recipient_covered`: `covered` / `not_covered` | `required_recipient_present and not external_recipient_present and recipient_policy == internal_only` | email, files, calendar | + |
| communication | `escalation_acknowledged`: `acknowledged` / `not_acknowledged` | `escalation_sent and acknowledged_at <= acknowledgment_due` | email, calendar, files | - |
| communication | `attachment_requirement_met`: `satisfied` / `not_satisfied` | `not attachment_required or (attachment_present and checksum_valid)` | email, files, accounting | + |
| communication | `thread_resolution_complete`: `resolved` / `unresolved` | `customer_confirmed and unresolved_item_count == 0 and resolution_status == closed` | email, files, calendar | - |
| document | `agreement_execution_ready`: `ready` / `not_ready` | `signature_status == signed and counterparty_verified and effective_date_present` | files, email, calendar | + |
| document | `registration_current`: `current` / `expired` | `registration_status == active and expiry_date >= review_date` | files, calendar, accounting | - |
| document | `policy_exception_present`: `valid_exception` / `no_valid_exception` | `exception_approved and exception_expires >= '2026-10-01' and exception_scope_matches` | files, email, calendar | + |
| document | `document_version_current`: `current_version` / `stale_version` | `observed_version == required_version and not superseded` | files, accounting, email | - |
| document | `evidence_bundle_complete`: `complete` / `incomplete` | `invoice_present and receipt_present and contract_present` | files, accounting, email | + |
| document | `retention_window_valid`: `valid_window` / `invalid_window` | `record_age_days <= retention_days or legal_hold_active` | files, calendar, email | - |
| scheduling | `overlap_conflict`: `conflict` / `no_conflict` | `intervals_overlap(proposed_interval, blocking_interval) and not priority_override` | calendar, calendar, files | + |
| scheduling | `attendee_quorum_met`: `quorum_met` / `quorum_not_met` | `confirmed_attendees >= required_quorum and organizer_confirmed` | calendar, email, files | - |
| scheduling | `prerequisite_complete`: `complete` / `incomplete` | `predecessor_complete and evidence_link_present and dependency_identity_matches` | calendar, files, email | + |
| scheduling | `review_window_valid`: `valid_window` / `invalid_window` | `valid_interval(review_interval) and duration_minutes(review_interval) <= maximum_minutes and within_business_hours` | calendar, calendar, files | - |
| scheduling | `timezone_alignment_valid`: `aligned` / `misaligned` | `declared_timezone_present and offset_matches and local_date_consistent` | calendar, email, files | + |
| scheduling | `deadline_feasible`: `feasible` / `infeasible` | `remaining_hours >= estimated_hours and not dependency_blocked` | calendar, files, email | - |
- For the workflow repair, compile only the 2- and 30-call fixed-answer frontier
  twins. For the interface repair, compile only the low-workflow raw and
  generic-compact twins needed by the prospectively registered holdout contrast.
- Apply the existing reference-agent, alternative-plan, exact-scoring,
  semantic-invariance, gold-isolation, and replay audits before a model call.
- The holdout manifest is frozen before any model response and remains private
  until analysis closure.

## Fixed low-cost model panel

Selection uses provider stability, organization diversity, and projected cost;
it does not use the running core outcomes.

| Organization | Configuration |
|---|---|
| OpenAI | `openai/gpt-oss-120b-maas` on Google Vertex |
| Google | `google/gemini-3.5-flash-lite` on Google Vertex |
| DeepSeek | `deepseek/deepseek-v4-flash-0731` on its frozen OpenRouter route |
| Alibaba | `qwen/qwen3.7-flash` on its frozen OpenRouter route |

The panel is finite and is not a population sample of models. Hard new-spend
caps are USD 4 for Google and USD 8 for OpenRouter.

## Intervention A: trace-state orchestration

The controller may read only the user-visible request, tool schemas, prior tool
calls, and returned observations. It may not read a task ID, hidden program,
gold answer, gold arguments, reference trace, or terminal-state contract.

The controller follows a generic state machine:

1. send the same system prompt, user request, eight-tool menu, and raw
   observations as the routed baseline; the model must select and parameterize
   the initial operation itself;
2. remain inactive after an invalid, unavailable, or off-route model call, so
   the intervention cannot repair selection;
3. after a successful on-route search, read the returned public record ID and
   execute its matching read operation;
4. after a read exposes exactly one public `next_handoff`, execute the named
   on-route search with its public query, then read the uniquely returned public
   record; repeat only while the observed record contains another handoff;
5. append every controller-authored call and unmodified tool result to the same
   public transcript and evaluator trace, charge every call to the unchanged
   budget, and return control to the model only after terminal evidence or a
   fail-closed ambiguity/error;
6. never execute a mutation. The holdout population is read-only, and the model
   remains responsible for the final evidence-bound answer.

The controller supplies only arguments copied from the immediately preceding
public search result or `next_handoff`; it neither invents facts nor reads a
reference program. It does not delete, summarize, or rewrite intermediate
observations. Construct tests must mutate hidden programs while holding the
public trace fixed and prove that controller behavior is unchanged, mutate a
public handoff and prove that behavior changes accordingly, and verify that all
automated calls remain in exact scoring and budget accounting.

The workflow estimand uses four cells: 2/30 certified calls crossed with routed
baseline/trace-state controller on raw native observations. Two additional
raw-observation cells apply the canonical adapter at 2 and 30 calls as an
off-target repair control. The matched repair must be more selective for
workflow pressure than this wrong-layer intervention.

At both horizons, the final evidence record contains the same complete
three-fact bundle and has the same source identifier. Request, exact answer,
route, connector, search/get schemas, visible menu, raw representation, and
read-only terminal state are fixed. Only mandatory fact-free locator handoffs
change.

## Intervention B: canonical observation adaptation

The adapter accepts only the serialized observation and its public field,
provenance, type, and relation dictionaries. It receives no request, task
identity, model output, or hidden benchmark object. It emits a canonical
sequence of records containing record type, source identifier, JSON-pointer
path, value, and relation edges.

Population-wide tests must prove:

- deterministic output for equal observations;
- exact reconstruction of all source values and provenance edges;
- query and task independence;
- equal information content across raw and generic-compact inputs;
- no gold labels, required tools, reference order, or expected answer in output.

The interface estimand uses four 2-call cells: raw/generic-compact crossed with
native/canonical delivery under baseline orchestration. One additional compact
cell applies the trace-state controller with native delivery as an off-target
repair control; its raw counterpart is already the 2-call workflow-controller
cell. The matched adapter must be more selective for interface pressure than
this wrong-layer intervention.

Across both estimands, the nine unique operational cells are:

| Calls | Representation | Orchestration | Delivery | Role |
|---:|---|---|---|---|
| 2 | raw | baseline | native | shared low-pressure baseline |
| 30 | raw | baseline | native | workflow stress |
| 2 | raw | trace-state | native | workflow low-pressure control / interface off-target control |
| 30 | raw | trace-state | native | workflow matched repair |
| 2 | raw | baseline | canonical | interface low-pressure control / workflow off-target control |
| 30 | raw | baseline | canonical | workflow off-target repair at stress |
| 2 | generic compact | baseline | native | interface stress |
| 2 | generic compact | baseline | canonical | interface matched repair |
| 2 | generic compact | trace-state | native | interface off-target repair at stress |

## Prospective power and workload

The original 12-family sketch was too coarse for a moderate selective repair.
Using a one-sided worst-Holm level of 0.025 and a second-order $t$ approximation,
the 80%-power minimum detectable effect for any *single* family-level contrast
is 17.6 points when its across-intent standard deviation is 0.20 (26.3 points
at 0.30). With 24 distinct semantic intent clusters, the corresponding values
are 11.9 and 17.8 points. These are component-wise planning calculations, not
an 80%-power guarantee for the four-component intersection--union activation
rule. Joint activation is governed by its weakest component and by dependence
among the four estimates; it is therefore reported only from the frozen test,
without post-hoc family expansion. This outcome-blind expansion was made before
any holdout plan or model response and does not assume an effect direction
beyond the registered hypothesis. The inferential population is this finite,
frozen rule inventory; no claim extends to an unrestricted population of user
intents.

The complete design has `24 families x 4 configurations x 9 unique cells = 864`
episodes. No outcome-dependent early stopping or model removal is permitted.

For each layer and model-family pair, compute:

- diagnosed loss = low-pressure baseline minus stressed baseline;
- stress repair gain = repaired stress minus stressed baseline;
- low-pressure repair gain = repaired low minus low-pressure baseline;
- repair selectivity = stress repair gain minus low-pressure repair gain;
- off-target selectivity = the same difference-in-differences for the other
  layer's structural intervention;
- matched specificity = repair selectivity minus off-target selectivity;
- residual loss = low-pressure baseline minus repaired stress.

Layer activation uses the same intersection-union rule as the main analysis:
diagnosed loss, stress repair gain, repair selectivity, and matched specificity
must all be positive.
The maximum of their one-sided intent-cluster sign-flip p-values enters a Holm
family over the two structural repairs. Because every family has a unique
intent, family and intent are the same resampling unit. Report cluster-bootstrap
intervals, per-configuration effects, leave-one-organization-out and
leave-one-semantic-stratum-out sensitivity, tokens, latency, and schema bytes.

## Claim boundary

The running prompt-only arms remain reported under their exact names. They are
not relabeled as structural repairs. A statement that the benchmark prescribes
an effective call-horizon or interface repair is allowed only if the
corresponding new holdout interaction, including matched-versus-off-target
specificity, activates. The compact-interface
hypothesis is explicitly prospective on the new families even though its choice
was motivated by the retained completed block. The core workflow contrast
remains a bundled workflow-and-evidence-load result regardless of the holdout
outcome. Otherwise the paper may report diagnosis and an unresolved repair
target, but not diagnosis-to-remedy validation for that layer.
