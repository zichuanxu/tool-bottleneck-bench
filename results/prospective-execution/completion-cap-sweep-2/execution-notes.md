# completion-cap-sweep-2: execution notes

> Public release redaction (2026-10-13): the entries of one configuration
> outside the reporting panel were removed from these notes, together with the
> plan, job, row and spend totals and the analysis figures that included it.
> SHA-256 of the original notes: `ad8c05845be3e3f4033c2aa0aa17b7db7ef4ad21d60d8222e57d526725152057`.

Second campaign directory of the completion-cap sweep, governed by
`protocols/completion-cap-sweep-2026-10-12-amendment-2.md` (stable hash
`3efd8bb632be…`, bound into the plans under
`results/prospective-plans/completion-cap-sweep-2/`). Configurations: GLM-5.3
(Z.ai, 4 workers), DeepSeek V4 Pro 0813 (OpenRouter, CoreWeave FP8, 8 workers);
arms cap1024 and cap16384; two repeats; ceiling USD 230. The analyzer reads this directory
together with `completion-cap-sweep-1` as one sweep (amendment 2, A2.6).

## Launch (2026-10-12 11:41 JST)

* Plans frozen offline at 11:33 JST (`build_cap_sweep_campaign.py --extension
  --freeze-plans`): 432 conditions per plan, 288 endpoint-cell
  conditions.
* Executed on the lab host `carly` in a `python:3.12-slim`
  container (`xu_bb_capsweep2`, 4 CPUs, 8 GB, user UID, default bridge
  network), project directory (path withheld):
  `snapshot/` holds the code (src/ at commit 97c9a2e unmodified, the three
  campaign scripts, both protocol files, the campaign-2 plans; joint
  sha256 prefix `7b096e30e69988ef`), `runs/` receives the rows, `secrets/.env`
  (mode 600) holds the two key names' values and is bind-mounted read-only as
  the snapshot's `.env`. Credentials never entered any log or artifact.
* One runner process: `run_cap_sweep_campaign.py --pilot --then-full --repeats 2
  --parallel-repeats` with `CAP_SWEEP_CAMPAIGN=completion-cap-sweep-2`. The gate
  reads the first 36 V=128 episodes of each high-arm repeat-1 job; once all
  verdicts are in, the remaining jobs of the passed configurations start in the
  same process (single writer per file), both arms and both repeats concurrently.
* Dry run without credentials beforehand returned the expected
  `missing credentials` exit; a frozen plan loaded inside the container.

## Gate progress

* 11:42 JST: all routes serving; first rows within a minute.
* 11:47 JST: DeepSeek V4 Pro 0813 reached 36 V=128 episodes, 0 truncated.
* GLM-5.3 is the slow lane (about 2.3 rows per minute on the 4-worker route
  with reasoning on); its verdict is expected around 12:35 JST.
* 12:05 JST: the DeepSeek high-arm job shows no new row since 11:59 JST at 311
  rows (watching; the runner retries transient errors five times and
  quarantines a lane above 20% failures).
* 11:59 to 12:14 JST: the DeepSeek high-arm job wrote no row for 15 minutes
  while its eight in-flight requests hung on the CoreWeave route (OpenRouter's
  public endpoint status showed full uptime). The requests hit the 900-second
  transport timeout, the retries succeeded, and the lane resumed at 12:14 JST
  with no transport-error row; the first resumed row carries a 912-second wall
  time. Data unaffected; wall-time telemetry for those episodes is inflated.
* 12:30 JST: GLM-5.3 reached 36 V=128 episodes, 0 truncated. Gate PASSED for every
  configuration; the runner continued in the same process into the remaining
  jobs (both arms, both repeats, concurrently). By then the DeepSeek high-arm
  repeat-1 job had already completed its 432 rows. None of the high-cap V=128
  gate episodes in either campaign directory was truncated.

## Interim state (2026-10-12 13:16 JST)

Finished jobs so far (runner log, `ok` lines): DeepSeek V4 Pro 0813 cap 16384
repeat 1 (USD 12.971, 1 truncated of 432 rows), DeepSeek cap 1024 repeat 1
(USD 11.002, 76 truncated) and repeat 2 (USD 10.955, 68 truncated). All report
0.0% transport failures. DeepSeek cap 16384 repeat 2 (425 rows) is in the same
OpenRouter stall pattern recorded above (no rows written for more than ten
minutes, no transport-error rows); the earlier occurrences resumed by
themselves once the 900 s request timeout fired and the retries succeeded. The
four GLM-5.3 jobs hold 258, 240, 195 and 114 rows and advance at about 12 rows
per minute combined, so the campaign is expected to end near 14:30 JST.

A provisional joint analysis (2,000 bootstrap draws) over the completed
campaign-1 rows and the campaign-2 rows copied at 13:13 JST was run only
to check the wording of the verdict-dependent sentences in the manuscript; its
output is overwritten by the final analysis (10,000 draws, seed 20261012) once
the campaign finishes, and no number from it is kept.

## Campaign finished (2026-10-12 14:54 JST)

The runner reported the campaign finished with no quarantined job, and the
container `xu_bb_capsweep2` exited with code 0 at 05:54:15Z (`runner.log`,
`launch.log`, copied into this directory). Every lane file holds 432 rows. Per
job (runner log `ok` lines): DeepSeek V4 Pro 0813 cap 1024 USD 11.002 / 10.955
with 76 / 68 truncated, cap 16384 USD 12.971 / 13.020 with 1 / 0; GLM-5.3 cap
1024 USD 10.104 / 9.855 with 126 / 132 truncated, cap 16384 USD 14.386 / 14.400
with 2 / 3 truncated. Every job reported 0.0% transport failures. The
OpenRouter stalls recorded above all resumed by themselves after the 900 s
request timeout; no row carries a transport error.

The rows were copied to this directory by `rsync -a` at 14:56 JST (the
`.campaign.lock` file was left behind), and the joint analysis over both
campaign directories was run once with the registered settings (10,000
bootstrap draws, seed 20261012). The released joint analysis is
`results/prospective-execution/completion-cap-sweep-1/analysis/cap-sweep-campaign.json`.
