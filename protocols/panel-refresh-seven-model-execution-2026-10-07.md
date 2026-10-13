# Seven-model execution and bounded interruption recovery

## Material Passport

- Origin Skill: academic-research-suite / experiment-agent
- Origin Mode: run
- Origin Date: 2026-10-07
- Verification Status: PREPARED
- Version Label: seven_model_execution_v1

## Authorization

The author requested simultaneous execution of the remaining five specified
models and delivery of the final paper after completing the experiments.
After seeing the $318 additional estimate, $540 additional ceiling and $560
seven-model ceiling, the author replied `余额不足不用管，我设置了自动充值`.
Do not require the whole ceiling as prepaid balance. This is not authorization
to exceed $560, change the account, or buy credits manually. Real provider errors,
including unsuccessful credit replenishment, still stop the affected worker.

Model allocations, including all probes and recovery reservations, are:
Opus 5.5 $200; Sonnet 5.5 $100; Qwen3.8 Max $100; Gemini 3.8 Flash $40;
GPT-6.1 Sol $100; GPT-6 Luna $5; DeepSeek V4.1 Flash $15. They sum to $560
and are not transferable. All seven models may run concurrently, each with one
request at a time. No other model or paid experiment is authorized.

## Fixed experiment and execution

Reuse the 63 frozen plans for the seven requested configurations, substituting
only the already frozen DeepSeek data-policy amendment for that model. Each
model has nine blocks / 1,336 conditions. Prompts, tools, seeds, scoring,
completion/episode limits, reasoning defaults, pinned providers, and the
zero-automatic-retry rule remain unchanged. DeepSeek alone uses the previously
authorized `data_collection: allow`; the other six retain `deny`.

Use a detached local supervisor with separate model processes, exclusive model
locks, disk logs, and atomic status files. It does not automatically restart a
failed worker. Each worker checks a 48-hour launch window before dispatch and
retains the frozen 180-second request timeout. Capture generation headers before
reading the body when available, to aid reconciliation if the local process is
interrupted. The supervisor monitors process exits; only exact complete validated
inventories are marked complete. Unknown future costs still stop the worker.

## Two explicitly authorized recovery requests

At approximately 12:34:15–16 UTC on 2026-10-07 both earlier processes disappeared
without a terminal state update. The local exit cause is unverified. Each left
one durable request intent without a raw response or recorded generation ID.
The observed account total at 12:39:19 UTC equals the prior account baseline plus
all known receipt costs, but this alone does not establish a zero charge for
either missing response.

The author explicitly approved: `可以各补发一次。完成实验` after being told that
the two missing requests together retain approximately $0.58 of maximum fee
liability. Hash-bind precisely those two pending intents in the authorization.
Keep each original intent immutable, reserve its full liability against its
model's ceiling, and never fabricate a raw response or zero-cost receipt.
Replay successful earlier turns; supplement only the missing turn using the
same logical request and a distinct `authorized-recovery-v1:` receipt phase.
Permit at most one such supplementary wire attempt per authorized original
intent. If its own receipt is missing or unsuccessful, stop; no second supplement.
This is an outcome-independent, user-approved local-interruption amendment,
not a model-response retry or a change to the scoring contract.

## Completion and paper handoff

All new model rows must pass exact plan/condition closure and infrastructure,
provider/model identity, usage, and raw-receipt checks before paper integration.
Then regenerate one unified 21-configuration panel, all derived analyses and
figures, and the author/anonymous PDFs and uploadable source bundle. Current
delivery files must contain only the selected panel. Preserve private historical
raw evidence byte-for-byte; do not publish it or claim missing outcomes exist.
No push, publication, browser use, or Overleaf upload is authorized here.
