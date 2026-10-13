# Literature Review and Novelty Audit — 2026-09-02

## Superseding revision note, 2026-09-04

The current per-reference metadata and abstract-scope audit is
`docs/reference-audit-2026-09-04.json`. All 82 primary URLs were checked; official
ICLR pages independently confirm DoVer, FuncBenchGen, HAL and tau-bench, superseding
the older venue-status statements below. Samer Saab Jr's suffix is corrected.
The later `docs/reference-claim-audit-2026-09-04.md` adds primary full-text
locators for the ten-neighbor comparison and corrects its Harness-Bench,
Model or Harness?, and ToolRobustBench descriptions. The cyclic-subtask work
now cites its verified arXiv version; an inaccessible decision page is not
treated as evidence for or against journal acceptance. The uncited
selection-aware entry was removed from the bibliography, leaving 81 cited
entries. The older 82-record audit remains a dated snapshot, not an
all-claims certification.

The current manuscript treats the positive V x W coefficient as descriptive
statistical support, not an identified shared mechanism. It calls the visible-load
sensitivity a truncation-resolution bound under fixed nontruncated trajectories,
not a larger-cap intervention. Preprints establish adjacent reported work, not
independent replication of BottleneckBench's conclusions. The older search notes
below are retained as provenance and do not supersede these boundaries.

## Scope and status policy

This review uses conference proceedings, ACL Anthology, OpenReview, and arXiv
records as primary sources. A venue is named only when an official proceedings
or conference page was verified. Work for which only an arXiv record could be
verified is labeled **arXiv preprint**, even if its manuscript or comments
mention a submission. Such preprints are important novelty threats, but no
single unreviewed preprint should carry BottleneckBench's central motivation.

## Scientific gap

Existing benchmarks show that tool-using agents fail under large catalogs,
long workflows, noisy interfaces, and stateful environments. Recent work also
localizes failures and validates repairs. The remaining question is therefore
not simply *whether* a tool agent fails or whether a post-hoc diagnostic can
name a stage. It is:

> When registry availability, model-visible competition, certified sequential
> burden, and lossless observation representation are varied inside the same
> executable goal, which pressure changes verified capacity, how do the
> pressures interact, and is a layer-matched system change more valuable than
> replacing the entire model--provider--harness configuration?

BottleneckBench should be framed as a controlled **joint identification and
decision** benchmark for this question. It is not the first tool-retrieval
benchmark, menu benchmark, long-horizon benchmark, stage-wise diagnostic,
counterfactual replay system, or targeted repair study.

## Highest-priority 2026 frontier

| Work and verified status | Experimental design and finding relevant here | Novelty consequence and safe comparison |
|---|---|---|
| **ToolRobustBench** (Zheng, Wu, and Chang; arXiv:2608.23635, 2026-08-23) | 15,456 single-family perturbation records over interface, user-intent, observation, and runtime families; stage-resolved selection/schema/argument/output/end-to-end scoring; selected mixed-family interaction cells; 98 counterfactual stage corrections recover 71 failures; two 30-task interventions show that a matched selection repair succeeds while a wrong-layer reasoning prompt does not | This work **already performs stage attribution, interaction analysis, counterfactual correction, and matched-versus-wrong-layer repair**. BottleneckBench cannot claim any of those ideas individually. The residual distinction is joint identification of non-corrupted deployment pressures: separate registered availability and visible competition, a certified multi-step workflow frontier, lossless observation interfaces, all crossed within executable semantic twins, followed by population-level system-repair-versus-configuration comparisons. ToolRobustBench is mainly single-call perturbation robustness rather than this four-pressure, stateful joint design. |
| **AFT-Bench / Callability Is Not Operability** (Wang; arXiv:2608.23628v2, 2026-08-30) | Holds task, backend, initial state, injected failure, agent, and model fixed while varying selective discovery, lifecycle/recovery, effect semantics, machine-readable results, and verification; a deterministic mechanism phase is followed by a persistent SQLite replication over 3 models and 6 workloads (18 cells; 2,385 rows) | Kills any claim that controlled interface interventions or operability gates are new. BottleneckBench instead jointly crosses clean R/V/W/D pressures and their interactions rather than injecting one operational uncertainty at a time. |
| **ToolMenuBench** (Babu and Iyer; arXiv:2606.15508, 2026) | 26,460 end-to-end runs: 7 settings, 7 models, registry sizes 25/100/250, 6 filters, and 30 tasks; separates gold-next-tool visibility, conditional selection, and downstream trajectory effects | Kills any claim that menu filtering, dynamic exposure, or availability-versus-selection decomposition is new. BottleneckBench treats visible-menu load as one component of a larger same-goal R/V/W/D surface and additionally tests workflow and observation pressures. |
| **PlanBench-XL** (Liu et al.; arXiv:2606.22388, 2026-06-21) | 327 retail tasks in a 1,665-tool ecosystem, retrieval capped at 30 tools, shortest paths of 5--9 actions, roughly 25 turns on average, and executable blockers | Strongest catalog-plus-long-planning neighbor. Its broad tasks demonstrate collapse under combined retrieval/planning load; BottleneckBench trades ecological breadth for paired factor identification and fixed-goal repair specificity. |
| **APIFlow-Bench** (Wan et al.; arXiv:2608.29128, 2026-08-29) | 467 tasks across 13 API worlds, including chains up to 20 subtasks, with 7 polymorphic tools, deterministic provenance-sensitive state/answer grading, 19 models, and 44,362 transcripts; many failed clean-slice runs reach the right state but fail delivery | Strong new workflow and delivery benchmark. It isolates long dependent API chains with a fixed small tool interface, not registered/visible load or a full four-pressure factorial. Its state-versus-delivery split supports BottleneckBench's exact terminal and evidence obligations. The manuscript says it is under review at a workshop; this review treats it only as an arXiv preprint. |
| **Skill Following** (Cho and Park; arXiv:2609.00549, 2026-09-01) | Conditions same-task skill-enabled versus skill-disabled outcomes on executions that actually retrieved a skill; 17 models reveal that positive aggregate retrieval lift can coexist with negative actual-use effect | Directly reinforces the need to separate availability, selection, and downstream use. It does not cross visible-menu, certified-workflow, or observation pressure, so the joint BottleneckBench estimand remains distinct. The arXiv record reports Findings of EMNLP 2026 acceptance; no Anthology record was independently verified in this audit. |
| **Calibration Is the Bottleneck** (Zhao et al.; arXiv:2609.00949, 2026-09-01) | Decomposes multi-turn failures into action-class miscalibration and tool-call execution failure over TOOL_CALL/ASK/REFUSE/CONFIRM; shows that a state grader can mask action-class errors | Requires BottleneckBench to describe its first-failure order as an obligation audit convention, not a causal root-cause or complete action-class taxonomy. The arXiv record reports Findings of EMNLP 2026 acceptance; no Anthology record was independently verified in this audit. |
| **E-Commerce Bench** (Fan et al.; arXiv:2608.30730, 2026-08-31) | A deterministic 365-day business environment with dynamic events, negotiation, and thousands of adaptive steps; 18 evaluated models | Establishes a much broader ecological long horizon. BottleneckBench's 30-call result must be framed as a fixed-goal controlled frontier, not the longest or most realistic agent horizon. |
| **Result-Only MCP Actionability Audit** (Mehan; arXiv:2609.00072, 2026-08-31) | Audits 21 induced failures from 10 reachable servers and finds that typed results expose failure more often than concrete cause, executable repair, or replay constraints | Provides a narrow complementary D/recovery boundary. It is not an agent benchmark and cannot carry the main novelty claim. |
| **Harness-Bench** (Yao et al.; arXiv:2605.27922, 2026) | 106 tasks crossed over 6 harnesses and 8 API backends, plus Codex, producing 5,194 trajectories | Establishes that model capability cannot be inferred independently of scaffold/provider configuration. BottleneckBench should call each evaluated unit a model--provider--harness configuration and present repair-versus-configuration choice as a descriptive decision analysis. |
| **WorkSurface-Bench** (Liang et al.; arXiv:2607.25765, 2026-07-28) | 1,151 enterprise tasks; 4 backbones by 6 settings yield 27,624 trajectories; gold-constrained routing can be near perfect while answer performance remains much lower; matched hints and irrelevant-tool removal probe routing | Kills broad claims of first multi-surface routing diagnosis or first matched routing intervention. It studies knowledge-surface routing, whereas BottleneckBench identifies multiple tool-pipeline pressures and their interactions inside the same executable goal. |
| **AgentNoiseBench** (Wang et al.; arXiv:2602.11348v2, 2026) | Injects solvability-preserving user and tool noise into tool-use and search benchmarks; evaluates 24 models with four trials per task and reports a 20.8-point average accuracy drop (unrelated to this work's −20.8 pp visible-menu contrast) | Establishes broad robustness failure under corrupted inputs/outputs. BottleneckBench studies clean, valid deployment pressures, so it must not call its factors “noise” or imply that robustness perturbation is new. No official conference record was independently verified here; cite it as an arXiv preprint. |
| **Selection-Aware Stress Testing for Interactive Agents** (Xu et al.; arXiv:2608.30916, 2026-08-31) | Reweights discovery tasks and then requires independent confirmation with support, stability, and joint bounds; a 3.75-point discovery gain in a 480-episode tau-bench study disappears on confirmation, and a second model also does not confirm | This is primarily an inference threat: any stress cell selected after inspecting outcomes needs a fresh confirmation population. BottleneckBench's incomplete 97/768 prospective replication must remain explicitly incomplete and cannot be presented as replicated evidence. |
| **ATLAS** (Chen et al.; arXiv:2608.30685, 2026-08-31) | Dual-horizon diagnostics combine request-level trajectory signals with interaction-horizon user/service signals, calibrated judges, production traffic, offline replay, and online A/B tests | Kills broad claims of first industrial or multi-timescale tool-agent diagnosis. BottleneckBench's narrower value is controlled same-world identification with exact executable scoring rather than production post-hoc monitoring. |
| **Ignorance or Incompetence?** (Tian et al.; arXiv:2608.30322, 2026-08-31) | Uses byte-identical tasks with and without a compact private artifact, provenance/leak audits, and executable witnesses to distinguish missing knowledge from execution inability; 15 calibration tasks lead to 7 retained tasks | Kills broad “first availability-versus-capability separation” wording. It provides a single knowledge gate; BottleneckBench separates multiple tool-pipeline layers and estimates their joint surface. |
| **Measure Before You Manage** (Chen et al.; arXiv:2608.31057, 2026-08-31) | Analyzes 55 archived coding-agent trajectories with object-aware compression/retrieval and shows that equal token budgets need not imply equal delivered working context or management cost | Supports reporting schema bytes, delivered context, and management overhead in addition to tokens. It studies coding-agent working memory rather than tool registry/menu/workflow/observation interventions. |

### Direct verdict on ToolRobustBench

ToolRobustBench is the most serious overlap. It has all three elements that an
earlier draft treated as distinctive: stage attribution, mixed-pressure
interaction analysis, and repair-based validation. The safe paper language is:

> ToolRobustBench aligns solvability-preserving perturbations with intra-call
> stages, models cascade sources, evaluates selected mixed-family interactions,
> and validates attribution through counterfactual and targeted repairs.
> BottleneckBench studies a complementary regime: non-corrupted deployment
> pressures over multi-step executable semantic twins, with separate registry
> and visible-menu estimands, a certified workflow frontier, lossless
> observation interfaces, and population-level repair-versus-configuration
> comparisons.

Do **not** write that prior benchmarks merely report end-to-end failure, or that
BottleneckBench is the first to locate a failure layer, test interactions, or
validate a diagnosis through repair.

## Formal 2026 baselines

The following venue labels are supported by official conference or ACL
Anthology pages and are safe to use as peer-reviewed baselines.

| Work | Verified venue | Role in the comparison |
|---|---|---|
| TRAJECT-Bench | ICLR 2026 | trajectory-aware tool-use evaluation; establishes that final success alone is insufficient |
| MCP-Bench | ICLR 2026 | complex real-world tasks through MCP servers; ecological MCP coverage |
| The Tool Decathlon (Toolathlon) | ICLR 2026 | diverse, realistic, long-horizon task execution |
| Benchmarking LLM Tool-Use in the Wild (WildToolBench) | ICLR 2026 | in-the-wild tool-use evaluation and ecological diversity |
| ETOM | Findings of EACL 2026 | five-level MCP orchestration evaluation |
| How Good Are LLMs at Processing Tool Outputs? | EACL 2026 long paper | controlled evidence that output representation and interpretation matter |
| ToolScope | ACL 2026 long paper | tool merging and context-aware filtering |
| TPS-Bench | ACL 2026 long paper | planning and scheduling in compounding tool tasks |
| Dynamic Tool Dependency Retrieval | Findings of ACL 2026 | dependency-aware retrieval |
| Beyond Single-Shot: Multi-step Tool Retrieval via Query Planning | Findings of ACL 2026 | iterative query planning for tool retrieval |

ToolMenuBench, PlanBench-XL, Harness-Bench, MCP-Atlas, DuMateBench,
ToolRobustBench, AFT-Bench, APIFlow-Bench, WorkSurface-Bench,
AgentNoiseBench, Selection-Aware Stress Testing, ATLAS, Ignorance or
Incompetence, Measure Before You Manage, Skill Following, Calibration Is the
Bottleneck, E-Commerce Bench, the result-only MCP actionability audit, and DoVer are cited as arXiv
preprints unless a future official proceedings record is verified.

## Additional adjacent evidence

| Evidence family | Representative work | What BottleneckBench may and may not infer |
|---|---|---|
| Retrieval and visible exposure | ToolRet, ToolScope, DTDR, ToolQP, How Many Tools Should an Agent See?, REALFIN | These establish retrieval and exposure effects. BottleneckBench may claim a joint paired estimand, not first discovery of tool-count or routing effects. |
| Long-horizon and stateful execution | TaskBench, ToolHop, Toolathlon, ToolSandbox, AppWorld, Agent-Diff, E-Bench, DuMateBench, WildClawBench | These establish ecological and executable validity. BottleneckBench's 2/6/14/30-call frontier is a controlled workload intervention, not the broadest or most realistic environment. |
| Failure diagnosis and repair | ToolFailBench, AgentDiagnose, AgentRx, DoVer, CausalFlow, AgentCheck, CUADebug, Beyond End-to-End Success, AgentChaosBench | Post-hoc and intervention-based diagnosis has strong precedent. The differentiator is prospective factor assignment plus exact matched, low-pressure, and wrong-layer controls over a shared semantic population. |
| Model versus scaffold | Harness-Bench, DuMateBench, AnalysisBench, Declarative Skills, AgentSuite | Results are configuration-relative. Model-only language should be avoided unless the scaffold and provider are demonstrably held fixed. |
| Benchmark and evaluator validity | TASTE, AgentProp-Bench, AgentSuite, Tool cloning, ToolSandbox, AppWorld | Exact scoring and semantic twins improve internal validity but do not establish human semantic validity or universal ecosystem representativeness. |
| Production and serving | ATLAS, AgentSysBench | Behavioral capacity and serving-system bottlenecks are different constructs; latency and orchestration findings should not be conflated with task capacity. |

## Search-bounded novelty claim

As of 2026-09-02, the defensible novelty claim is the **joint measurement and
decision contract**, not any single technique:

1. separate registered availability from model-visible selection;
2. cross visible-menu, certified workflow, and lossless returned-state
   pressures within executable semantic twins;
3. estimate interactions and first-failure migration under a shared semantic
   population; and
4. compare layer-matched system repairs with low-pressure and wrong-layer
   controls, then contrast their success/cost trade-off with replacing the
   complete model--provider--harness configuration.

This remains a search-bounded claim, not a claim of universal firstness. The
estimated effects are population- and configuration-relative, not immutable
“capacity laws.”

## Implications for motivation and related work

- Lead with the diagnosis-to-decision gap: existing work can show failure and
  several recent benchmarks can localize it, but deployment still needs a
  controlled way to decide which system layer to change under interacting
  pressures.
- Treat ToolRobustBench as the closest mechanism overlap, AFT-Bench as the
  closest controlled-interface intervention, ToolMenuBench as the closest
  menu decomposition, PlanBench-XL as the closest catalog-plus-planning
  benchmark, and Harness-Bench as the key configuration confounder.
- Use APIFlow-Bench to motivate delivery-aware workflow scoring,
  WorkSurface-Bench to motivate routing-versus-answer separation,
  AgentNoiseBench to distinguish corruption robustness from clean pressure,
  and Measure Before You Manage to justify bytes/context-overhead metrics.
- Use Selection-Aware Stress Testing to justify the hard boundary around the
  incomplete prospective replication. Never treat a selected discovery cell
  as a validated finding without a completed independent population.
- Preserve the positive visible-by-workflow interaction. It rejects a simple
  compounding-loss story and is a scientific finding rather than a leaderboard
  fact.
- Keep the structural controller claim narrow: it addresses deterministic,
  public, machine-readable handoffs, not general planning.
- Keep the ToolSandbox menu intervention auxiliary because its baseline is
  nonconcurrent, effects are heterogeneous, and binary completion does not
  decline.
- Do not infer that a 2,048-entry catalog contains 2,048 independent real-world
  capabilities. Here catalog size is an assigned retrieval load.

## Bibliographic corrections made in this audit

- ETOM authors: Jia-Kai Dong, I-Wei Huang, Chun-Tin Wu, and Yi-tien Tsai
  (ACL Anthology record 2026.findings-eacl.75).
- *How Good Are LLMs at Processing Tool Outputs?* authors: Kiran Kate, Yara
  Rizk, Poulami Ghosh, Ashu Gulati, Tathagata Chakraborti, Zidane Wright, and
  Mayank Agarwal (ACL Anthology record 2026.eacl-long.134).
- MCP-Atlas begins with Chaithanya Bandi and Razvan-Gabriel Dumitru; the full
  arXiv author list is retained rather than an incorrect shortened list.
- DuMateBench is Niu et al., arXiv:2608.26546. The unrelated MCP workflow-engine
  paper remains a separate Parmar entry.
- DoVer is recorded as arXiv:2512.06749; no official ICLR proceedings record
  was verified, and the current manuscript itself is marked “Preprint.”

## Primary sources

### Highest-priority frontier

- ToolRobustBench: https://arxiv.org/abs/2608.23635
- Callability Is Not Operability (AFT-Bench): https://arxiv.org/abs/2608.23628
- ToolMenuBench: https://arxiv.org/abs/2606.15508
- PlanBench-XL: https://arxiv.org/abs/2606.22388
- APIFlow-Bench: https://arxiv.org/abs/2608.29128
- Harness-Bench: https://arxiv.org/abs/2605.27922
- WorkSurface-Bench: https://arxiv.org/abs/2607.25765
- AgentNoiseBench: https://arxiv.org/abs/2602.11348
- Selection-Aware Stress Testing: https://arxiv.org/abs/2608.30916
- ATLAS: https://arxiv.org/abs/2608.30685
- Ignorance or Incompetence?: https://arxiv.org/abs/2608.30322
- Measure Before You Manage: https://arxiv.org/abs/2608.31057
- DuMateBench: https://arxiv.org/abs/2608.26546
- MCP-Atlas: https://arxiv.org/abs/2602.00933
- DoVer: https://arxiv.org/abs/2512.06749
- Skill Following: https://arxiv.org/abs/2609.00549
- Calibration Is the Bottleneck: https://arxiv.org/abs/2609.00949
- E-Commerce Bench: https://arxiv.org/abs/2608.30730
- Result-Only MCP Actionability Audit: https://arxiv.org/abs/2609.00072

### Official conference and anthology records

- TRAJECT-Bench, ICLR 2026: https://iclr.cc/virtual/2026/poster/10009304
- MCP-Bench, ICLR 2026: https://iclr.cc/virtual/2026/poster/10008216
- The Tool Decathlon, ICLR 2026: https://iclr.cc/virtual/2026/poster/10006492
- Benchmarking LLM Tool-Use in the Wild, ICLR 2026: https://iclr.cc/virtual/2026/poster/10006500
- ETOM, Findings of EACL 2026: https://aclanthology.org/2026.findings-eacl.75/
- How Good Are LLMs at Processing Tool Outputs?, EACL 2026: https://aclanthology.org/2026.eacl-long.134/
- ToolScope, ACL 2026: https://aclanthology.org/2026.acl-long.1573/
- TPS-Bench, ACL 2026: https://aclanthology.org/2026.acl-long.1614/
- Dynamic Tool Dependency Retrieval, Findings of ACL 2026: https://aclanthology.org/2026.findings-acl.1680/
- Beyond Single-Shot, Findings of ACL 2026: https://aclanthology.org/2026.findings-acl.2090/

### Additional context

- ToolSandbox: https://aclanthology.org/2025.findings-naacl.65/
- AppWorld: https://aclanthology.org/2024.acl-long.850/
- Agent-Diff: https://arxiv.org/abs/2602.11224
- REALFIN: https://openreview.net/forum?id=0ni0z8Ttg6
- E-Bench: https://arxiv.org/abs/2607.23722
- WildClawBench: https://arxiv.org/abs/2605.10912
- AgentCheck: https://arxiv.org/abs/2607.11098
- CUADebug: https://arxiv.org/abs/2608.02643
- Beyond End-to-End Success: https://arxiv.org/abs/2608.20563
- AgentChaosBench: https://arxiv.org/abs/2608.14680
