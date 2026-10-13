# BottleneckBench

**ToolBottleneckBench: Measuring Tool-Agent Performance under Controlled Pressures and Fixed Harness Budgets** (software package `bottleneckbench`)

A tool agent's success score mixes what its model can do with the budgets of
the harness that runs it. BottleneckBench measures assigned deployment pressures
under one frozen harness contract and records which limit ended each failed
episode. It uses matched task variants: synthetic enterprise tasks that keep the
request, facts, answer and evidence rule fixed while one assigned pressure
varies (registered catalog, visible menu, certified workflow or returned-state
encoding), graded by an exact checker. On the current panel, the largest model-facing
pressure effect co-occurs with completion-cap truncation, and most failures end
at a harness limit rather than at a wrong conclusion. A completion-cap sweep
(12 October 2026) re-ran eight configurations at sixteen times the cap:
truncation all but vanished and the truncation-heavy configurations' pooled
visible-menu contrast moved from −43.7 to −2.7 pp, so the cap inflates the
menu penalty but does not create all of it.

The verified panel has 21 model, route and harness configurations from seven
organizations: 13 retained configurations and eight additions, each addition
with 1,336 conditions in nine frozen blocks. Together they contribute 26,576
distinct scored condition rows (15,888 retained plus 10,688 new); these are
condition records, not independent tasks. Each analysis keeps its own
population:

- core capacity and Discovery: 21 configurations (12,096 capacity rows and
  4,536 Discovery rows);
- matched repair, repeated sentinel and workflow frontier: 20 configurations
  (2,880, 1,440 and 320 rows; Qwen3.8 Flash has no repair arms);
- pooled structural holdout: 17 configurations (2 from the registered plan plus
  15 additional), 3,672 rows over 24 held-out intents;
- concurrent ToolSandbox menu: 12 configurations, 1,152 rows;
- historical ToolSandbox external rank: 5 configurations, 160 rows; historical
  ToolSandbox menu: 5 configurations, 320 new rows plus 160 reused baselines.

The exact Verified Task Success (VTS) population (capacity, repair, sentinel,
frontier and structural rows) is 20,408 rows. Discovery rows are scored on the
intention-to-treat (ITT) outcome `Y = A · S`, and ToolSandbox rows by
ToolSandbox's own evaluator; the three success measures are never pooled. The
panel is a convenience sample: the eight additions carry confirmatory weight 0,
every panel-level analysis is descriptive, and only the registered
two-configuration structural plan carries weight 1. Nine retained
infrastructure conditions (8 capacity, 1 Discovery) stay in the analyses as
configuration-level ITT failures.

On the 21 core configurations, enlarging the visible menu from 8 to 128 schemas
lowers VTS by 12.3 points (95% CI `[-14.6, -9.9]`, Holm `p < .001`). The
penalty co-occurs with completion-cap truncation: in the same capacity rows,
truncation rises from 1.6% of episodes at V=8 to 13.6% at V=128, and across the
21 configurations the menu effect tracks the truncation rate (Pearson
r = -0.95). The eight configurations that never truncate at V=128 have effects
within `[-2.1, +2.8]` points, but they are also the strongest baselines, so cap
exhaustion and capability are confounded, and the menu contrast is not
identified without an assumption about truncated episodes. Of 3,725 failures
among the 20,408 exactly graded episodes, 91.5% end at a harness limit and 5.5%
are wrong conclusions; the 30-call frontier and structural baseline cells
contribute 896 budget-bound failures by construction, and the token-or-call
category merges the episode budget with a call limit only two above the
certified minimum. Connector-first retrieval and routed menus pass the repair
activation test, prompt repairs do not, and no repair beats every configuration
switch on success except by construction or through deterministic delegation.
A completion-budget crossover on 4 retained configurations failed its
manipulation check and is descriptive. The
[benchmark card](docs/benchmark-card.md) lists every finding with its
population and boundary.

The paper's numbers come from the analysis root
`results/reporting-panel-2026-10-13-final21/`: the panel is `release-panel.json`,
the row manifest is `release-analysis-inputs.json`, and
the manuscript's generated-artifact manifest (private) binds every figure,
table and macro to those inputs. The verification report
`results/reporting-panel-2026-10-13-final21-verification-v1/report.json` passed:
a separate deterministic recomputation of 16 analyses matched every typed field,
and 3 historical outputs were checked as byte-preserved. It is a recomputation
with the same algorithms, not an independent re-implementation.

## What is measured

| Layer | Pressure | Matched system move |
|---|---|---|
| Retrieval | registered catalogs of 32, 512, or 2,048 tools | connector-first retrieval |
| Visible menu | 8, 32, or 128 native tool schemas | routed exposure |
| Workflow | certified dependency-constrained programs, plus a 2/6/14/30-call frontier | public-handoff delegation |
| Returned state | lossless raw, compact, typed, or linked observations | canonical delivery |

Every matched variant preserves the request, decision-relevant facts, decision
label, operational tools, evidence rule, and permitted terminal state. Workflow
load also changes the required sources array; the entire answer is not fixed
across workflow levels.
Verified Task Success is computed by an exact evaluator; no LLM judge is used.

## Repository layout

```text
configs/       frozen price metadata used by offline analyses
docs/          benchmark card, evidence map, literature review, and system card
experiments/   release analyses and offline replay utilities
results/       immutable scored rows, frozen plans, and aggregate analyses
src/           benchmark compiler, sandbox, evaluator, and provider-neutral loop
vendor/        pinned ToolSandbox adapter subset
```

## Reproduce the released results

**Python.** The offline surface needs Python **3.11 or 3.12**;
`requirements.lock` pins its three real dependencies (`dill`, `matplotlib`,
`numpy`) plus their transitive closure with `--generate-hashes`, and installs
on both interpreters. `pyproject.toml` declares `requires-python = ">=3.11"`.
`.python-version` names 3.11 because that is the interpreter the release was
produced on; analysis outputs are byte-identical across the two.
`requires-python` is deliberately open-ended, but only 3.11 and 3.12 are tested
and locked.

```bash
make public-check
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r requirements.lock
python -m pip install -e . --no-deps
make test
```

That is the clean-checkout preflight followed by the test suite; `make verify`
runs both.

The 21-configuration analyses were generated by
`experiments/run_panel_refresh_22_analysis.py` and recomputed by
`experiments/verify_panel_refresh_22_analysis.py`. Both bind the private
acquisition tree (raw receipts, local-only plans and the full input inventory),
so neither runs from this projection; the passed recomputation is published as
`results/reporting-panel-2026-10-13-final21-verification-v1/report.json`.
The generation record that lists the exact analyzer commands stays with the
author repository:
`results/reporting-panel-2026-10-13-final21/analysis-generation.json` (private).

**Completion-cap sweep.** The sweep of 12 October 2026 is one campaign in two
directories, `results/prospective-execution/completion-cap-sweep-1/` and
`completion-cap-sweep-2/`, with frozen plans under
`results/prospective-plans/completion-cap-sweep-1/` and `-2/`. Eight
configurations re-ran the 432-condition routed-menu capacity factorial at
1,024 and 16,384 completion
tokens, two repeats per arm. The analysis reads only the scored rows, the plans
and the pilot-gate records, and recomputes usage cost from the rows and the
frozen price table, so it runs offline from this tree; write to a new path to
leave the released file untouched:

```bash
PYTHONPATH=src .venv/bin/python experiments/analyze_cap_sweep_campaign.py \
  --output results/prospective-execution/completion-cap-sweep-1/analysis/cap-sweep-campaign-recheck.json
```

The defaults (10,000 bootstrap draws, seed 20261012, and the reporting panel and
released visible-menu effects under `results/reporting-panel-2026-10-13-final21/`)
reproduce
`results/prospective-execution/completion-cap-sweep-1/analysis/cap-sweep-campaign.json`
except for the `public_release_redaction` records that the released files carry.
The sweep's cost ledgers, runner logs and lock files stay private, as for the
crossover.

Rerunning the external ToolSandbox block is optional and is the only step that
needs the vendored dependency set:

```bash
python -m pip install -r requirements-toolsandbox.lock
python -m pip install -e vendor/tool-sandbox --no-deps
```

`requirements-toolsandbox.lock` is **Python 3.11 only** (one of its transitive
pins declares `requires-python <3.12`) and is not needed for any command in the
block above.

`make public-check` verifies every projected
file against the repository-local `public-files.json`, including exact paths,
sizes, and SHA-256 hashes. It is a clean-checkout preflight, not a post-build
gate. It tolerates the byproducts the documented commands create (`.venv`,
`__pycache__`, `.pytest_cache` and `dist/`), so it still passes after a README-following install and test
run. It fails on any *other* extra or modified file, so run it before adding
anything of your own, or run it inside a fresh projection. The check remains
valid after Git initialization because `.git/` metadata is outside the release
manifest.

## Public release scope

This repository is the public projection of the author repository. It carries
the complete offline surface: the generator, the exact grader, the analysis
code, the 21-configuration analysis root with its scored rows and row
manifest, the verification report, the frozen plans and the historical rows of
the earlier campaigns, and the completion-cap sweep of 12 October 2026 (its
plans, scored rows, pilot-gate verdicts, execution notes and joint analysis).
Left out, by design:

- the manuscript sources, generated figures and submission tooling;
- the hosted-acquisition runners and their tests, which need provider
  credentials and the private execution directories, and the panel filter
  script, which names configurations outside the current panel;
- the local-only acquisition directories (raw receipts, transport snapshots
  and cost ledgers), the sweep's cost ledgers, runner logs and locks, and the
  driver provenance file
  `internal/driver-provenance.json` (private) and the generation record
  `analysis-generation.json` (private), which carry route and pricing
  metadata of the private execution tree.

The campaign directories released on 6 September 2026 are retained as
historical evidence; the 21-configuration analyses do not read them.

## Acquisition boundary

The public command surface is offline and requires no credential. It replays
the released analyses from hash-bound inputs. Released acquisition scripts are
provenance and guarded execution code, not part of offline replay; resumption
requires their private frozen evidence and explicit paid-run admission.
Private operational payloads are intentionally outside this public projection.
Offline Make targets unset the four named provider variables and, in addition,
every `*_API_KEY`, `*_TOKEN`, and `*_CREDENTIALS` variable present in the
calling environment, together with `GOOGLE_APPLICATION_CREDENTIALS`.

## Evidence and limitations

Start with [the benchmark card](docs/benchmark-card.md) and [claim–artifact
map](docs/claim-artifact-map.md). The core capacity intervals condition on one
hosted realization per condition. Every effect is conditional on the frozen
budgets (1,024 completion tokens per request, 128,000 reported tokens per
episode); the completion-budget crossover (4,096 tokens) failed its
manipulation check, the sixteenfold sweep (16,384 tokens, eight configurations)
passed it and varied only the completion cap on the routed-menu lane, and the
episode budget and call limit were never varied. The 12-configuration concurrent
ToolSandbox menu study and the two 5-configuration historical ToolSandbox
strata are scored by ToolSandbox's own evaluator and are never pooled with the
main analysis; the historical menu stratum uses a nonconcurrent baseline. No
independent human semantic or evaluator audit is claimed.

## License and citation

Repository-authored code, benchmark definitions, documentation, and aggregate
analysis artifacts are released under the MIT License. Vendored components and
conference style files retain their upstream terms; see
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md). Citation metadata is in
`CITATION.cff`, including the public repository URL:
https://github.com/zichuanxu/tool-bottleneck-bench.
