# Citation-claim verification supplement, 2026-09-04

This supplements, rather than rewrites, the same-day 82-record metadata and
abstract audit in `reference-audit-2026-09-04.json`. The revised bibliography
contains 81 entries: the uncited `xu2026selectionaware` entry was removed. No
in-text citation was removed with it. The compiled bibliography and citation-key
closure are checked separately in the manuscript verification record.

## Detailed comparison checks

These primary-source sections were read for the non-obvious claims in the
per-conjunct comparison. Checks establish textual support, not independent
replication of another paper's experiments. A missing property in this reading
audit is not evidence that a work lacks it.

| Citation | Primary source and locator | Checked support and revision |
|---|---|---|
| FuncBenchGen | [arXiv full text](https://arxiv.org/html/2509.26553), abstract and Section 1 | The reported variable-value restatement comparison is 62.5% to 81.3% for GPT-5. The distractor design is not a registered-versus-visible retrieval intervention. |
| ToolMenuBench | [arXiv full text](https://arxiv.org/html/2606.15508), Sections III-A, V-A and V-B | Gold next-tool exposure is measured before acting; downstream metrics include goal-state success and wrong-tool calls. Replaced quotation-like paraphrases with an explicit description of these different metrics. |
| AFT-Bench | [version 2 full text](https://arxiv.org/html/2608.23628v2), Sections 4.1, 4.5 and 5.2 | The controlled interface comparison holds the task, backend, initial state, injected failure and agent/model fixed. Matching recovery mechanisms to their failure settings is narrower than a full crossed wrong-layer repair experiment; the partial mark remains. |
| ToolRobustBench | [arXiv full text](https://arxiv.org/html/2608.23635), Sections 3.1, 5.4, 5.6 and Table 8 | The unchanged 21/30 CoT result concerns B-family tasks; the 21/30 to 30/30 alias result concerns A-family tasks. Clarified that these are distinct intervention sets, not a paired comparison on identical tasks. |
| APIFlow-Bench | [arXiv full text](https://arxiv.org/html/2608.29128), Sections 4, 5 and 8.4 | Oracle admission, state/answer checks and five-epoch reliability support the corresponding comparison marks. The 44-point versus 7-point spread is a reported all-five versus best-of-five comparison, not a ToolBottleneckBench result. |
| Harness-Bench | [arXiv full text](https://arxiv.org/html/2605.27922), Section 3.4 | Scoring combines deterministic validators when available with rubric-based judgment elsewhere. Replaced the weaker claim about unavailable checks with this documented mixed-grader limitation; task review alone is not a formal horizon certificate. |
| Model or Harness? | [arXiv full text](https://arxiv.org/html/2607.28802), abstract, Sections 3, 4 and 6 | The failure-localization taxonomy includes a judge-agreement validation study. Corrected the assertion that it has no experiment; it does not supply the controlled task-repair contrast used by this table. |
| LayerRAG-Bench | [arXiv full text](https://arxiv.org/html/2607.27353), Tables 4 and 5 | Schema repair reports 0.000 to 0.913 on schema drift, with no success on the listed off-target failure layers. This is a layer-specific repair result, not a claim that every interface repair generalizes. |
| CAFE | [arXiv full text](https://arxiv.org/html/2607.10380), Sections 3.3, 3.4, 4 and 6 | The evaluation uses shared-rubric human/LLM ratings and component/interactions attribution with mixed models. Exact executable grading is not established by that protocol. Judge repetitions do not by themselves constitute repeated hosted trajectories. |
| ToolSandbox | [NAACL paper](https://aclanthology.org/2025.findings-naacl.65.pdf), Section 2.3, Table 3 and Appendix A.2.1 | The published paper specifies 0/3/10/all distractor additions, prioritizing related domains and textual similarity. Milestone/minefield trajectory scoring differs from conjunctive exact VTS. The four-trial simulator-error audit warrants a partial repeated-run mark, replacing the earlier missing-evidence dash. |

The table caption and accompanying prose now state that no neighbor column
documents every property *in this audit*. They do not infer universal absence
from missing evidence, or claim ToolBottleneckBench is superior to the union of
earlier designs. An obsolete reference to an introductory list of ten properties
was removed after the introduction was shortened.

## Publication-status correction

For `gharzeddine2026cyclicsubtask`, the verified source is
[arXiv:2604.22820v2](https://arxiv.org/abs/2604.22820v2). The current arXiv record
reports a journal status, but the independent
[OpenReview decision page](https://openreview.net/forum?id=FAkarhXCfI) was blocked
by an access challenge. An indexed older submission PDF cannot settle a later
decision. The bibliography therefore cites the verified preprint version and
does not assert either acceptance or rejection. The author suffix correction
from the earlier audit is retained.

## Verification boundary

Additional primary abstracts were checked against the related-work claims:

| Citation | Primary record | Claim-support boundary |
|---|---|---|
| Benchmarking the Benchmarks | [arXiv:2607.02577](https://arxiv.org/abs/2607.02577) | The audit covers four tool-calling benchmark families and identifies evaluator/human disagreement, including brittle deterministic matching and incorrect ground truth. This supports the attributed motivation, not a claim that all deterministic graders are invalid. |
| The Recall Trap | [arXiv:2608.14838](https://arxiv.org/abs/2608.14838) | A higher-recall retriever can perform worse under the tested fixed-context setup. The paper also reports reversals and null settings; the manuscript's conditional wording does not generalize the ordering to every retriever or budget. |
| COVER | [arXiv:2608.28475](https://arxiv.org/abs/2608.28475) | The evaluation fixes the information boundary, downstream system and admissible alternatives before outcomes. It supports the methodological analogy to predeclared contracts, not an empirical tool-use replication. |
| First-chunk ranking | [arXiv:2608.26130](https://arxiv.org/abs/2608.26130) | The tested single-turn localization study does not resolve a systematic accuracy gain from first-chunk rank. Replaced "rank does not" affect accuracy with the narrower study-specific null description; absence of a resolved gain is not proof of no effect. |
| Less Context, Better Agents | [arXiv:2606.10209](https://arxiv.org/abs/2606.10209) | Pruning and summarization are evaluated in a specified enterprise expense workflow, including repeated runs. This establishes context-management interventions, not a universal benefit from removing context. |
| HarnessFix | [arXiv:2606.06324v2](https://arxiv.org/abs/2606.06324v2) | The framework links trace diagnoses to scoped harness patches and regression-aware validation. It is an intervention precedent, not evidence that ToolBottleneckBench's limit-bearing failures have the same causes. |
| LOCA-bench | [arXiv:2602.07962](https://arxiv.org/abs/2602.07962) | Controlled environment growth preserves task semantics while evaluating model/scaffold combinations and context management. It supports the distinction between observed limits and tested management interventions. |
| Scaffold Effects on GAIA | [arXiv:2606.08529](https://arxiv.org/abs/2606.08529) | The preprint compares three scaffolds within five models on fixed GAIA tasks with three attempts per question. The manuscript attributes within-model scaffold effects without treating that study as independent validation of this benchmark. |

The adjacent representation paragraph was also disaggregated after checking
seven primary abstracts. It no longer describes every cited outcome as an
accuracy change at a fixed task:

| Citation | Primary record | Supported distinction |
|---|---|---|
| LongFuncEval | [arXiv:2505.10570](https://arxiv.org/abs/2505.10570) | Catalog size, tool-response length and conversation length are separately varied; reported degradation is specific to the evaluated settings. |
| How Good Are LLMs at Processing Tool Outputs? | [EACL 2026](https://aclanthology.org/2026.eacl-long.134/) | The effect of JSON-processing strategies depends on output size, content and reasoning complexity. |
| Masking Stale Observations | [arXiv:2606.00408](https://arxiv.org/abs/2606.00408) | Masking gains vary across retriever/backbone regimes; the paper does not establish an unconditional benefit. |
| Natural Language Tools replication | [arXiv:2607.03953](https://arxiv.org/abs/2607.03953) | The reported aggregate accuracy gain includes smaller or reversed effects for some frontier models. |
| Notation Matters | [arXiv:2605.29676v2](https://arxiv.org/abs/2605.29676v2) | Token savings are accompanied by accuracy and parsing tradeoffs, not universal success improvement. |
| Measure Before You Manage | [arXiv:2608.31057](https://arxiv.org/abs/2608.31057) | Nominally equal budgets do not imply equal delivered context or management cost; calibration gains need not transfer to held-out tasks. |
| The Devil Is in the Interface | [arXiv:2608.11386](https://arxiv.org/abs/2608.11386) | Architecture affects repeated-attempt consistency, exploration and efficiency; some comparisons retain similar task performance. These outcomes are no longer collapsed into a generic accuracy claim. |

All remaining metadata fields inherit the same-day primary-record checks in the
earlier audit, with the corrected preprint entry above. This supplement verifies
the listed detailed comparisons, not every sentence in all 81 cited papers, all
implementation details of their code, or the correctness of their reported
results. Non-archival findings remain attributed; they do not independently
validate ToolBottleneckBench's measurements. Neither audit certifies submission
readiness while the requested Opus inventory remains incomplete.
