# CocoIndex browser acceptance and performance comparison

Date: 2026-09-30. Implementation: `6986b632`, issue #698.

## Verdict

The optional runtime integration works through real model-driven chat, citations,
and Run administration. Missing-index fallback works. CodeGraph remains available
and is called first in the `code_analysis` scenario.

**Neither an attributable answer-quality improvement nor a speed improvement is
established.** Indexed tool calls are slower than the existing search on this
corpus. The two matched questions that actually used the index had essentially
unchanged total latency and one additional tool call each. Evidence handling and
Token-budget completion still need work. Keep the feature opt-in.

This report covers the implemented retrieval slice and its existing application
flows. It does not certify every SourceLens feature or the unfinished indexing
administration and automatic post-sync orchestration.

## Environment and method

- Browser: **ego-browser TaskSpace 29**, `SourceLens CocoIndex full QA and performance comparison`.
  No Playwright was used.
- Local development stack, an isolated private QA assistant and temporary account.
  Existing assistants and source files were not modified.
- Same QA image in both modes, with CocoIndex **1.0.24** installed. Only
  `LENSNODE_TEXT_INDEX_ENABLED` changed for the matched comparison.
- Same model: `deepseek/DeepSeek-V4-Flash/8f94e`; balanced execution policy,
  citation display enabled, same guidance and SourceLens datasource.
- Corpus: **1,036 files / 8,708 chunks / 24,473,600-byte SQLite FTS5 index**.
  Generation: `6e32cfe836b79348af3bc125aa5ff14a64dd3081ab06be0d0b95eaf3954170d4`.
- Four identical Chinese questions per mode, each in a fresh session. All eight
  selected Runs have zero `context.message` events. Earlier warmup/history-bearing
  attempts and automation setup failures are excluded from the matched results.
- Questions cover blue/green rollback and migration compatibility, the Python vs
  development Compose concurrency defaults, `load_config` and its caller chain,
  and a deliberately absent identifier `qxzvthkprj6978`.
- Durations below are server `finished_at - created_at`, including admission.
  They exclude failed submission attempts. Model-call counts include recorded
  auxiliary calls; tool counts come from `tool.completed` events.
- One successful Run per question per mode is a small sequential sample. Model
  behavior, gateway latency and prompt caching vary. No statistical significance,
  tail-latency claim, or causal gain is inferred from these totals.

## Matched results

| Question | Off, seconds | On, seconds | Tools off/on | Models off/on | Actual indexed call in on mode | Business outcome off/on |
|---|---:|---:|---:|---:|---|---|
| Rollback and migration | 55.94 | 22.84 | 10 / 6 | 8 / 5 | None | partial / completed |
| Concurrency defaults | 10.98 | 7.04 | 2 / 2 | 3 / 3 | None | completed / completed |
| Definition and callers | 31.73 | 31.79 | 12 / 13 | 10 / 10 | One, indexed, six hits | partial / partial |
| Absent feature | 10.67 | 10.76 | 3 / 4 | 4 / 4 | One, indexed, eight broad matches | completed / completed |

All eight executor statuses are `done`; this must not be confused with the
business outcome. The on-mode structural Run stopped with `token_budget_wrapup`,
using 183,795 recorded Tokens. Its answer was delivered, but its business outcome
was `partial`. The budget-limited baseline answers also remain part of the
comparison rather than being silently discarded.

### Answer quality

- **Rollback:** off mode supplied the correct command but cited development API
  restart instructions instead of the requested two-release expand/contract
  constraint. On mode supplied the correct constraint and supporting lines in
  `AGENTS.md`. On mode never called the index, so the improvement cannot be
  attributed to indexed retrieval.
- **Defaults:** both modes correctly answered Python **1**, development Compose
  **8**, with supporting source lines. On mode added unrequested production
  information. Neither used the index.
- **Structure:** both found `config.py:124`, `_run_client()` and the CLI `main()`
  chain. Indexed retrieval found the definition-containing chunk as its first
  hit, but did not reduce subsequent searching, reading, or tool count. Both
  knowledge-QA Runs reached a partial business outcome.
- **Absent feature:** off mode attached an unrelated README citation and used an
  overconfident nonexistence statement before saying no evidence was found. On
  mode avoided that citation and correctly found no exact identifier, but claimed
  that all related searches had no matches even though its broader indexed query
  returned eight unrelated passages. This is still an evidence-reporting defect.
- Requested response-length limits were not consistently followed. No combined
  quality score is claimed from these four examples.

The absence scenario's broad indexed query was **not** an empty-result fallback.
`TEXT_INDEX_NO_MATCHES` was verified by the separate exact-nonce tool benchmark.

## Tool performance, measured without active chat Runs

Six query cases, alternating tool order, one discarded warmup and five measured
rounds per case/tool. Both tools used `max_results=8` and the same datasource.
A fresh `build_agent_tools` registry was constructed for every invocation because
the original literal tool suppresses identical repeated queries within one Run.
Registry construction was outside the measured invocation interval for both tools.

| Query case | Literal median, ms | Indexed median, ms |
|---|---:|---:|
| Chinese blue/green deployment and rollback terms | 153.63 | 600.16 |
| Chinese incremental/sync/deletion terms | 144.79 | 551.03 |
| `runtime contract` | 200.98 | 552.07 |
| `LENSNODE_MAX_CONCURRENT_RUNS` | 130.02 | 681.85 |
| `artifact download` | 195.97 | 676.49 |
| Exact absent identifier | 171.77 | 715.58 |

Indexed retrieval was **2.75–5.24 times slower** in these invocation medians.
The absent identifier returned zero hits and `TEXT_INDEX_NO_MATCHES`, with the
literal fallback included in its duration. The result shapes/ranking differ, so
these are tool latency measurements, not equivalent-relevance measurements.

A separate `cProfile` run of the query path spent 1.106 of 1.137 seconds in
`authorized_scopes`, including **25,016 filesystem stat calls**. Profiling adds
its own overhead; these are attribution figures, not benchmark latency. The
measured bottleneck is repeated corpus-wide path/permission validation. Future
optimization must retain path, symlink, authorization, and freshness guarantees.

## Browser flows checked

| Flow | Observed result |
|---|---|
| Normal login and private assistant creation | Passed through the UI with a temporary QA account |
| Assistant model, datasource, policy and citation configuration | Saved and used by real Runs |
| New session, submit, queue, Agent activity and final answer | Passed; two off-mode submissions also returned HTTP 503 before retry succeeded |
| Citation opening | Opened source snapshots, file identity and line ranges for off and indexed answers; indexed `config.py` evidence includes the definition |
| Reload/session history | Prior questions, answers and citations restored |
| Stop execution | UI displayed the stop acknowledgement; QA Run `c73c0e56-35b8-4019-9e92-3ba4b2c7414c` became `cancelled` |
| Run search and detail | Filtered QA Runs; displayed duration, model/tool counts, indexed tool name and partial business outcome |
| Missing published index | Temporarily renamed only the QA SQLite file; explicit indexed-tool request returned `TEXT_INDEX_UNAVAILABLE`, fell back, and produced a correct cited answer |
| CodeGraph coexistence | Changed only the QA assistant to `code_analysis`; with indexing enabled, the first two calls were `mcp__codegraph__codegraph_explore`, followed by source search/read; answer delivered |
| Assistant archive | Archived the QA assistant through the UI |

CodeGraph is contributed by the existing plugin only for `code_analysis` Runs.
The matched `knowledge_qa` structural questions therefore do not measure
CodeGraph performance. The separate code-analysis Run took 26.23 seconds and is
not mixed into the A/B timing table. Its CodeGraph calls returned success; this
checks routing/coexistence, not exhaustive graph-edge correctness.

### Additional findings

1. **Dispatch stability:** two off-mode POSTs to session `/runs/` returned 503.
   Later UI submissions succeeded. The precise dispatch error was not captured;
   its root cause remains unresolved. These failures precede enabling the index.
2. **Evidence metrics UI:** an off-mode Run detail displayed zero verified
   citations in its evidence summary while listing five citation entries below.
   Reproduced before enabling the index; not treated as a new CocoIndex defect.
3. **Completion wording:** the chat displays a completed activity state for Runs
   whose administration detail reports a partial business outcome. Operators
   should inspect business outcome and termination reason, not only `done`.
4. **Ranking:** broad OR keyword queries can surface locale/release-note snippets
   unrelated to the requested feature. Indexed passage availability alone does
   not establish answer quality.

## Storage and regression scope

SQLite schema inspection found metadata, document, chunk and FTS tables, with
**no vector or embedding columns**. The integration continues to use node-local
text preparation and FTS5; no vectorization or PostgreSQL vector storage was
introduced by this task.

After restoring the development node, a final disposable container ran with
`--network none` and the source workspace mounted read-only. Reindexing reported
**0 changed files / 0 deleted files**, the same 1,036 documents and generation.
Six additional real-tool assertions passed offline: indexed retrieval and reading
its citation path, exact-no-match fallback, exclusion of `config.py` from a scoped
query, missing-index fallback, stale-index fallback, and oversized-query rejection.
The stale case changed hashes only in a disposable copy of the QA SQLite database;
neither source files nor the published QA index were modified by that fault check.
These checks required no network or PostgreSQL service.

This browser pass did not rerun the entire unit suite or change runtime code.
The preceding [full local test report](698-cocoindex-local.md) records 30 focused
CocoIndex tests, the complete backend/frontend/LensNode runs, and their reproduced
baseline failures. Incremental update/deletion, stale source, conversion, scope,
and publication-failure coverage belongs to that test pass. Browser stale-source
fault injection, all connectors, mobile layouts, public sharing, and unfinished
index lifecycle UI were not certified here.

## Evidence identifiers and reproduction

| Case | Off Run | On Run |
|---|---|---|
| Rollback | `0253ef32-0e39-440c-8dcf-be9992949a4d` | `7a76195d-f282-4a1c-9f3e-6372524c4275` |
| Defaults | `9640214b-4d39-4f09-9194-f767c4897019` | `387bf3e8-352d-4252-b094-a8df07ac5213` |
| Structure | `ed9c2771-775b-49a8-bf0d-91b5536464ea` | `7011e8dd-8b2f-40e8-9b15-e9cecc6053c5` |
| Absent feature | `1f121cec-5c76-4613-a33d-c603c4b26b96` | `e1b1398c-f9c0-4058-bbad-1a996539958f` |

- Missing-index Run: `d11a86c7-c29a-4a32-a04b-435d59bd4a43`.
- CodeGraph Run: `c8678403-57a5-41ab-a623-4719e196ecd8`.
- Disposable local artifacts: `/tmp/sourcelens-698-browser/`, including
  `matched-runs.json`, `matched-metrics.json`, `runs.json`, `history-isolation.txt`,
  browser snapshots, `cases.json`, `benchmark-tools.py`, `tool-benchmark-idle.json`,
  `profile-query.py`, `profile-query.txt`, `schema.json`, `offline-reindex.json`,
  `offline-checks.py`, and `offline-checks.json`.
- The earlier repeat-guard benchmark and history-bearing chat attempts are
  discarded. Only `tool-benchmark-idle.json` is used for the table above.

To repeat, build the optional image with `INSTALL_COCOINDEX=true`, index a fixed
manifest-backed datasource into private state, and use a dedicated assistant.
Run the exact questions in `cases.json` in independent sessions with the feature
off/on, checking zero `context.message` events and actual tool results. Wait for
all Runs to terminate before changing the node configuration. Run the benchmark
script while the node is idle; do not reuse the same tool registry across repeats.
Do not count a click receipt as successful submission: verify that a Run was
created and inspect both its executor status and business outcome.

## Cleanup and remaining work

TaskSpace **29** was finished and closed. The QA assistant is archived; the test
account is inactive with staff/superuser privileges removed and its password
invalidated. The temporary credential file was deleted. QA Runs remain for
administrator inspection.

The development node was restored to **`sourcelens-lensnode:latest`**, with
**`text_index_enabled=False`**, verified online with zero active Runs. Private QA
index artifacts remain outside the repository; the original source corpus and
production configuration were not changed.

Before broader enablement: optimize the measured authorization/path bottleneck
without weakening safeguards, improve tool selection and evidence discipline,
resolve budget-limited completion, and repeat a larger randomized question set.
The automatic indexing lifecycle and administration deliverables in #698 remain
open. The issue is not ready to close on the basis of this acceptance pass.
