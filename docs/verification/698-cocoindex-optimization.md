# CocoIndex retrieval cost optimization

Date: 2026-09-30. Issue #698. Baseline: `0b9aca00`.

## Result

On the existing 1,036-file corpus, indexed tool invocation medians decreased by
**42.0–45.5% for matching queries**, and **29.2% for the no-match/fallback case**.
All **144 paired complete-result hashes** matched the baseline. A separately
profiled query reduced filesystem stat calls from **25,016 to 106** (99.6%).

Indexed retrieval still takes longer than literal search on this corpus. These
measurements establish lower tool latency under the measured conditions; they
do not establish better model answers or lower end-to-end chat latency.

## Implementation

The previous query path performed live path and permission checks for every
manifest file before asking SQLite for matches. On Docker-mounted source files,
the repeated filesystem operations dominated query work.

The query now derives a logical allowlist from the current manifest, selected
directory, and trusted retrieval policy. SQLite returns ranked candidate IDs and
paths first. Candidate files undergo live symlink, nested-datasource, containment,
file-existence, and policy checks before stored chunk text is loaded. Rejected
candidates are skipped and the result is filled from subsequent candidates.

Permission results are reused only for chunks of the same file within a query.
Subsequent queries reload the manifest and recheck policy and filesystem state.
Existing source hash, source identity, text hash, and conversion checks remain
in place before results are disclosed to the Agent.

This deliberately limits the optimization to the measured bottleneck. A
cross-query authorization cache would require invalidation for policy, manifest,
and filesystem changes. Persistent subprocess reuse would add a lifecycle and
cancellation mechanism. Neither is needed for the measured reduction. The
independent query subprocess and its 45-second timeout remain in place.

CocoIndex still prepares text, SQLite FTS5 stores and ranks local text chunks,
and CodeGraph handles structural code queries. No embeddings, vectorization,
pgvector, or PostgreSQL index storage were introduced.

## Measurement method

- Same immutable corpus and index as the preceding browser report: **1,036 files,
  8,708 chunks, 24,473,600 bytes**. Generation:
  `6e32cfe836b79348af3bc125aa5ff14a64dd3081ab06be0d0b95eaf3954170d4`.
- Existing QA image `sourcelens-lensnode:cocoindex-698-qa`, CocoIndex 1.0.24,
  network disabled, source and index mounted read-only. Each invocation uses the
  real `build_agent_tools` registry and the normal subprocess retrieval path.
- Four serial blocks in baseline/optimized/optimized/baseline order. Every block
  has six queries, one warmup, and five measured repetitions per query/tool.
  Tool order alternates within blocks. A fresh registry per invocation avoids
  the literal tool's repeated-query suppression; registry construction is outside
  the measured interval for both tools.
- Combined medians use **10 measured observations per query/tool/version**.
  Result comparisons include warmups: 144 baseline/optimized pairs across both
  tools, comparing SHA-256 of complete JSON results, including citations/text.
- Explicit `PYTHONPATH=/opt/lensnode` selects mounted code for the benchmark
  process as well as its subprocess. The baseline package is an archive of
  `0b9aca00`; optimized runs use the working-tree package.
- Profiling runs are separate from latency measurements. Benchmarks were not
  run concurrently with the test suite or profiler.

### Combined medians

| Query | Indexed before, ms | Indexed after, ms | Reduction | Literal before, ms | Literal after, ms |
|---|---:|---:|---:|---:|---:|
| Chinese blue/green rollback terms | 505.41 | 281.11 | 44.4% | 122.97 | 139.49 |
| Chinese incremental/sync/deletion terms | 518.74 | 288.19 | 44.4% | 131.35 | 157.33 |
| `runtime contract` | 539.07 | 306.14 | 43.2% | 195.40 | 214.82 |
| `LENSNODE_MAX_CONCURRENT_RUNS` | 545.08 | 316.00 | 42.0% | 122.08 | 130.92 |
| `artifact download` | 505.85 | 275.91 | 45.5% | 142.31 | 170.81 |
| Absent identifier `qxzvthkprj6978` | 661.65 | 468.47 | 29.2% | 164.92 | 162.56 |

The last indexed case includes the literal fallback. Literal control variation
shows that host load and filesystem caching still affect this small local
sample. No tail-latency, concurrency, cold-start, statistical significance, or
larger-corpus claim is made. Manifest parsing and logical filtering still scale
with manifest size; SQLite may rank many candidates for broad terms. Live checks
scale with inspected candidate files, including rejected candidates.

## Correctness and regression checks

- Focused CocoIndex/index tests: **38 passed**.
- Full LensNode suite: **1,122 passed, 17 failed, 2 warnings**. The failure set
  exactly matches the prior full-suite report: trajectory test doubles in
  `test_history.py`, `test_planned_runtime.py`, and `test_retrieval_gate.py` lack
  the current span interface. Those failures were previously reproduced on
  pre-integration baseline `5976f614`. The suite is not entirely green.
- A performance guard adds 80 unrelated documents and asserts that querying
  matching files performs no stats on those unrelated files. It fails on the
  old package and passes on the optimized implementation.
- Repeated-query authorization regressions cover file symlinks, directory
  symlinks, nested datasource markers, changed path exclusions, changed extension
  exclusions, and manifest deletion. A successful earlier query does not grant
  access after any of these changes.
- A SQLite trace test rejects the first ranked candidate, refills from the next,
  and verifies that only the authorized candidate's chunk text is selected.
- **Six network-disabled real-tool checks passed**: indexed citation path reads,
  exact-no-match fallback, path exclusion, missing-index fallback, stale-index
  fallback, and query size rejection. Stale-index testing changes only a
  disposable database copy.

No browser or model-driven chat rerun was performed for this optimization.
The earlier [ego-browser TaskSpace 29 report](698-cocoindex-browser.md) remains
the browser evidence. Its answer-quality and platform findings remain open.
Backend/frontend suites were not rerun because this change is confined to
LensNode retrieval; their previous results are in the
[full local report](698-cocoindex-local.md).

## Artifacts and reproduction

Local artifacts are retained under `/tmp/sourcelens-698-opt/`:

- `benchmark-tools.py`, `balanced-summary.json`, and
  `{final,reverse}-{baseline,optimized}-benchmark.json` with raw observations.
- `profile-query.py`, `profile-baseline.txt`, and `profile-query.txt`.
- `focused.log`, `lensnode.log`, `lensnode.xml`,
  `performance-regression-baseline.log`, and `offline-checks.json`.
- Baseline source archive under `baseline/lensnode/lensnode`.

These are local, temporary artifacts, not portable repository fixtures. To
repeat the optimized real-tool benchmark with the retained corpus:

```sh
docker run --rm --network none \
  --volumes-from sourcelens-lensnode-dev:ro \
  -v /tmp/sourcelens-698-browser/index-state:/text-index:ro \
  -v /tmp/sourcelens-698-opt/benchmark-tools.py:/tmp/benchmark-tools.py:ro \
  -e PYTHONPATH=/opt/lensnode \
  -e LENSNODE_TEXT_INDEX_STATE_PATH=/text-index \
  -e LENSNODE_WORKSPACE_PATH=/workspace \
  --entrypoint python sourcelens-lensnode:cocoindex-698-qa \
  /tmp/benchmark-tools.py
```

For baseline runs, additionally mount
`/tmp/sourcelens-698-opt/baseline/lensnode/lensnode:/opt/lensnode/lensnode:ro`.
Full tests ran in an isolated container using the QA image, mounted current
source/tests, a temporary pytest environment, and the backend plugin decision
module required by runtime-contract tests:

```sh
/tmp/test-venv/bin/python -m pytest -q -c pyproject.toml --rootdir=. \
  -p no:cacheprovider tests --tb=short --junitxml=/verification/lensnode.xml
```

The existing development node keeps its original `sourcelens-lensnode:latest`
image and disabled index feature. No production deployment or feature enablement
is part of this optimization. Issue #698 remains open for indexing lifecycle,
administration/deployment integration, and the recorded evidence-quality work.
