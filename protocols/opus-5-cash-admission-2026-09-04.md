# Opus 5 user-directed cash admission amendment

## Material Passport

- Origin Skill: experiment-agent
- Origin Mode: run
- Origin Date: 2026-09-04
- Verification Status: UNVERIFIED (execution is incomplete)
- Version Label: opus_cash_admission_6

The user was informed that the run had paused at USD 10.515934012 available
cash versus the full-context cash requirement of USD 10.5256. The user said
they did not know the automatic-top-up threshold and instructed the run to
continue. This is treated as authorization to replace the operational
full-context cash-prefunding requirement with a live USD 0.50 admission floor.
No payment settings are inspected or changed, and no future top-up is counted
as already available credit.

The USD 180 cumulative new-spend hard cap is unchanged. Before every wire
attempt, including retries, the runner still reserves the full USD 10.0256
worst-case request liability against that cap. Unknown response costs and
unresolved request intents retain their full liability. Account usage and raw
receipt costs still bound observed spend, including reporting lag. A top-up
does not reset the experiment ledger or enlarge the cap.

For cash admission only, the requirement is now USD 0.50 in observed effective
cash, without additionally prefunding the full-context liability. This floor
is checked before dispatch; it is not a guarantee that USD 0.50 remains after
a response. Automatic top-up is user-confirmed but not independently verified.
A delayed or unsuccessful top-up can still cause a cash-floor stop or a
provider credit rejection. Neither is a model failure. No additional retry,
new provider route, or outcome-dependent recovery is authorized here.

The original scientific manifest, all nine plans, all 1,336 condition IDs,
model-visible prompts, tool schemas, route, parameters, episode limits,
graders, retry counts, and worker count remain byte-identical. The original
passing transport probe remains valid and is not repeated. The first 29
completed rows and the pause-1 provenance remain immutable. The unfinished
condition replays its two settled responses without paid reissue.

A hash-bound operational sidecar identifies the base manifest, the archived
pre-amendment runner, this protocol, and the amended runner. Validation permits
only that recorded runner replacement; every other original input and plan
must still match its original frozen hash. The sidecar is frozen before any
further scored request. The archive is at
`results/opus-5-acquisition/freeze-history/liquidity-5/acquisition-runner.py`.

OpenRouter documents credit-triggered automatic top-ups, but its credits API
reports total credits and usage, not the configured threshold. This amendment
does not assume a specific threshold. See [OpenRouter credit FAQ](https://openrouter.ai/docs/faq)
and [credits API](https://openrouter.ai/docs/api/api-reference/credits/get-credits).
