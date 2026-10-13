# Execution notes: completion-cap-sweep-1

Protocol: `protocols/completion-cap-sweep-2026-10-12.md` (hash bound into every plan).
This file records what happened during execution; it does not rewrite the policy.

## Attempt 1 (2026-10-12 08:40 JST): rejected before any request

The first launch used plans restricted to the eight endpoint cells. The runner's
live validation (`_validate_live_plan`) requires the condition matrix to equal the
frozen generator's full output, so both pilot jobs exited with
"condition matrix does not match frozen generator" after 0 rows and USD 0.00.
The runner also read "0 rows" as a failed gate; that verdict is void. The
attempt's files are kept as `pilot-attempt-1.log`, `pilot-gate-attempt-1.json`
and `cost-ledger-attempt-1.json`. Amendment 1 of the protocol (full 432-condition
plans, gate read from the first 36 V=128 episodes of the live high-arm jobs) was
written before any hosted row, and the plans were rebuilt from the amended text.

## Attempt 2 (2026-10-12 08:50 JST): pilot gate PASSED

The pilot runner (`--pilot`) started the high-arm (cap16384) repeat-1 jobs of
Gemini 3.8 Flash and Qwen3.7 Flash at 08:50 JST. By 09:00 JST each had
completed 36 V=128 episodes with 0 truncations (0.0% against the 5% ceiling);
both gates PASS and the two jobs run on as repeat 1 of the high arm. The
runner writes `pilot-gate.json` only when those two jobs exit, so the verdict
is in `pilot.log` first. Live spend at the verdict: USD 6.78 (Gemini 6.58,
Qwen3.7 0.20); 0 transport failures.

## Lane-group launch (2026-10-12 09:0x JST)

With the gate passed, the four remaining configurations (Qwen3.8 Flash,
GLM-5.3 Flash, Claude Haiku 5.5, GPT-6 Luna) were launched as lane group
`others` (`--repeats 2 --lane-group others`, log `others.log`, ledger
`cost-ledger-others.json`) while the pilot process still holds
`.campaign.lock`. Each runner meters the ceiling from every row file in the
campaign, so the USD 100 ceiling is shared. The pilot configurations' low arm
and repeat 2 start after the pilot process exits (`--slugs gemini-38-flash
qwen37-flash --lane-group pilot-slugs`); their high-arm repeat-1 files resume
as no-ops because `bottleneckbench run` skips rows already present.
Runner change before this launch: a lane group now also names its own ledger
file (bookkeeping only; the analyzer recomputes cost from rows).

## Pilot jobs complete; pilot-slugs group launched (2026-10-12 09:26 JST)

The two pilot jobs finished as repeat 1 of the high arm: Gemini 3.8 Flash
432 rows, USD 9.27, 0 truncations, 0 transport failures, 28.3 min; Qwen3.7
Flash 432 rows, USD 0.34, 0 truncations, 0 transport failures, 35.2 min.
`pilot-gate.json` (decided_at 00:25:54Z) records both PASS verdicts. The
`pilot-slugs` lane group started at 09:26:03 JST once the pilot runner had
exited; its first two jobs re-opened the finished high-arm files and exited as
no-ops (same 432 rows, same cost), so `cost-ledger-pilot-slugs.json` lists
those two jobs again. Spend is recomputed from the row files by every runner,
so this repetition does not double count. Live spend at launch: USD 13.89.
Ten jobs were running at 09:30 JST: both arms of the four `others`
configurations and the low arm of the two pilot configurations, all repeat 1.
Throughput at 09:38 JST (rows per minute from file growth): Haiku and GPT-6
Luna about 5.5 per arm, Gemini about 15, Qwen3.7 about 13, Qwen3.8 about 1.7,
GLM-5.3 Flash (4 workers at Z.ai) about 1.4; the slow lanes set the finish
near 20:00 JST.

## Correction to the throughput line above (2026-10-12 09:30 JST)

The "rows per minute from file growth" figures above were read during lane
start-up and understate steady-state throughput. Measured from the
`executor.completed_at` timestamps once lanes were warm: Claude Haiku 5.5 about
26 rows/min per arm (its repeat-1 jobs finished 432 rows in 17 min and repeat 2
started 09:29 JST), GPT-6 Luna about 23, Gemini 3.8 Flash about 15, Qwen3.7
Flash about 13, Qwen3.8 Flash and GLM-5.3 Flash about 5 to 7. Median provider
latency per request is 3.4 to 4.6 s on the fast routes and 7.0 to 7.7 s on
Z.ai (GLM) and the Alibaba route (Qwen3.8); episodes take 3 to 4 requests. The
ten jobs together use about 5% of one CPU core on this machine, so the campaign
is bound by provider latency at the frozen worker counts, not by the client.
Revised finish estimate for the slow lanes: about 12:15 JST.

## pilot-slugs group finished (2026-10-12 10:25 JST)

Gemini 3.8 Flash and Qwen3.7 Flash completed both arms and both repeats with
0 transport failures and nothing quarantined; the group's eight ledger entries
include the two no-op re-opens noted above. Cumulative spend at that point:
USD 47.81. The `others` group had finished Claude Haiku 5.5, GPT-6 Luna, and
Qwen3.8 Flash repeat 1 and was still running GLM-5.3 Flash repeat 1 and
Qwen3.8 Flash repeat 2; GLM-5.3 Flash repeat 2 had not started.

## Campaign finished (2026-10-12 13:11 JST)

The `others` group ended with exit code 0 and the line
`campaign finished: spent $51.97 of $100.00; 16 jobs; 0 quarantined`
(`others.log`). All 24 lane files (six configurations, two caps, two repeats)
hold 432 rows each, 10,368 rows in total. Across the campaign every job
reported 0.0% transport failures and nothing was quarantined. Every high-cap
job reported `trunc=0`; the low-cap truncation counts are in the per-job lines
of the three logs. Total spend over the three lane groups: USD 51.97 of the
USD 100 ceiling (`cost-ledger*.json`). The last job to finish was GLM-5.3
Flash cap 16384 repeat 2 (432 rows, USD 0.917).
