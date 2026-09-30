# CocoIndex text processing and local full-text retrieval

- Status: runtime foundation implemented; platform orchestration remains tracked in [issue #698](https://github.com/oneprolabs/sourcelens/issues/698).
- Date: 2026-09-30
- Baseline: CocoIndex 1.0.24; SourceLens 5976f614.

## Product constraint and responsibilities

CocoIndex outputs must not be vectorized or stored as vectors in PostgreSQL.
This integration generates text chunks and provenance, with local SQLite FTS5
retrieval. It has no embedding model, vector columns, external indexing service,
or new PostgreSQL writes. CocoIndex uses its own LMDB for incremental state.

SourceLens retains ingestion Plugins, Connections, document converters, task
execution, access policy, and citations. CodeGraph provides symbols, callers,
dependency paths, and impact analysis. The existing workspace search handles
literal/regex/file queries. `search_indexed_workspace` provides ranked lexical
passages from prepared text; it cannot infer semantic equivalence or structural
code relationships. Chinese text is indexed with adjacent character bigrams;
query terms are ORed and ranked with FTS5 BM25. Results across separate indexes
are ordered by reciprocal rank, not by treating independent BM25 scales as equal.

## First delivery

The feature is disabled by default. The optional `lensnode[cocoindex]` extra
pins the engine and is loaded only by explicit indexing commands. Querying a
published index uses Python's SQLite support, without importing CocoIndex.

An administrator runs `python -m lensnode.text_index index` for one manifest-backed
datasource. This runs in its own process; it does not extend the datasource sync
barrier. No automatic watcher or implicit indexing during question answering is
installed. Operators should run the first build after source sync/conversion has
finished. Automatic scheduling, status UI, cancellation/recovery administration,
and datasource lifecycle cleanup remain work in issue #698.

## Input and publication contract

- The complete retained manifest is the desired corpus. `changed_paths` alone
  cannot represent this corpus. Catalogs without an explicit item list, catalogs
  reporting an incomplete scan/failure, and invalid paths fail the build.
  Identical repeated records are processed once; conflicting identities at one
  path reject publication.
- Missing-but-retained files stay indexed; confirmed `deleted` entries are removed.
  Non-indexable extensions and internal paths are excluded. Failed conversions
  block publication rather than silently removing previously searchable material.
- Read original UTF-8 text/code or successful PDF/DOCX/PPTX/XLSX conversion sidecars
  whose stored source hash still matches the original. Sidecar line positions are
  marked `converted_text`; they are never represented as original PDF page numbers.
- The initial limits are 10,000 catalog items, 2 MiB per searchable text file,
  32 MiB per converted original, and 64 MiB of searchable text per batch. Exceeding
  limits aborts publication. Other sources remain available through ordinary
  workspace search. Uploads/workspaces without a compatible manifest are not yet
  indexable through this first delivery.
- CocoIndex mounts each file at a stable path and memoizes preparation. It writes
  plain JSON chunks into a private staging directory. Deleted components remove
  their prepared files. The full catalog is rechecked after processing.
- SourceLens copies the last published SQLite index to a private temporary file,
  updates only changed/deleted file records and FTS rows, commits it, then atomically
  replaces the published file. The copy still costs O(index size), while document
  processing and FTS row updates are incremental. No speedup is assumed.
- A datasource-local `flock` serializes writers across pipeline profiles. The index
  path is keyed by canonical datasource root and UUID. Index state is node-local
  and must not be shared across hosts or exposed under the Agent workspace.
- CocoIndex background errors, invalid staging data, source changes before
  publication, and publication failure keep the previous searchable generation.
  Interrupted temporary files can be removed while no writer is active. A completed
  staging update can safely be retried. `--full-reprocess` repairs lost derived output.

The pinned 1.0.24 SDK exposes `stats.total.num_errors`. At implementation time the
live error-handling documentation used `num_errored`; the real-engine tests enforce
the pinned SDK contract.

## Retrieval and evidence contract

The Agent tool accepts only a query and result count. Its directory bindings and
retrieval policy come from the trusted Run command. Before retrieving text, the
worker derives permitted paths from the current manifest and the existing
`is_path_allowed` rules, then includes that allowlist in the FTS query. It never
searches all indexes and returns unauthorized rows for the Agent to filter.

Each result includes source identity, source/text hashes in storage, original path,
line range, conversion marker, and the published generation. The worker rereads
matching source content and verifies hashes before returning text. The tool records
consulted sources, returns mount-relative paths, and permits the existing file-read
tool to resolve those paths only inside the same selected directories. Ambiguous
mount names are rejected. No independent broadening of item-level authorization
is introduced; that remains the control plane's responsibility when producing Run
bindings.

A missing/stale index, missing configuration, invalid profile, worker timeout,
empty FTS result, or worker failure falls back to the existing workspace search.
A changed source after a hit was checked remains possible because current Run
materialization links mutable local directories. This delivery detects stale hits
and preserves existing synchronization barriers; it does not claim immutable
historical snapshots. Versioned source retention and generation-bound reads would
be required for that stronger guarantee.

The query subprocess is bounded to 45 seconds, queries to 2,000 characters, and
results to 20 chunks / approximately 12,000 characters. No-match queries return
no evidence. Error codes and trace fields report fallback reason, timing, count,
profile and generations without credentials or raw exception messages.

## Local operation

Build the optional image and enable the tool explicitly in development:

```sh
docker compose -f docker-compose.dev.yml -f docker-compose.text-index.yml build lensnode
LENSNODE_TEXT_INDEX_ENABLED=true docker compose \
  -f docker-compose.dev.yml -f docker-compose.text-index.yml up -d --no-deps lensnode
```

The overlay keeps the base compose project name, installs `.[cocoindex]`, and mounts
an independent named volume at `/text-index`. Default images omit the optional
extra. The production blue/green installer is not changed by this first delivery;
do not use bare production `compose up` to enable it. A standalone node can build
`lensnode/Dockerfile` with `--build-arg INSTALL_COCOINDEX=true` and provide the same
environment/volume settings through its own deployment configuration.

Configuration:

```text
LENSNODE_TEXT_INDEX_ENABLED=true
LENSNODE_TEXT_INDEX_STATE_PATH=/text-index
LENSNODE_WORKSPACE_PATH=/workspace
```

Prepare a datasource after its normal synchronization completes, replacing the
example root and UUID with its actual node-local values:

```sh
docker exec sourcelens-lensnode-dev python -m lensnode.text_index index \
  --root /workspace/datasources/<datasource-uuid> --datasource <datasource-uuid>
```

The command returns JSON with `status`, generation, profile, file count, changed
files, and deleted files. Python and native library diagnostics are routed to
stderr so stdout remains a JSON response. There is no model download, credential configuration,
or PostgreSQL extension setup. Repeat the command after updates; use
`--full-reprocess` only when explicitly rebuilding all derived text.

Turn off `LENSNODE_TEXT_INDEX_ENABLED` and recreate/restart the node to remove the
optional Agent tool. The ordinary retrieval path continues to work. Keep or remove
the private state volume independently after stopping index writers. Deleting a
datasource from the platform does not yet garbage-collect this private state;
operator cleanup is required until the lifecycle phase in #698 is implemented.

## Verification

Focused tests cover real CocoIndex memo reuse, incremental FTS updates, deletion,
source races, component/publication failures, writer exclusion, scope/path
validation, Chinese lexical search, profile mismatch, source citation ranges,
tool fallback, and actual CLI-to-Agent subprocess integration. These tests use
local fixtures and no model or external database. Broader product/UI acceptance
and performance comparison remain in #698.

The full local verification results, real datasource checks, and baseline failures
are recorded in [the local verification report](../verification/698-cocoindex-local.md).

## Sources

- https://github.com/cocoindex-io/cocoindex/releases/tag/v1.0.24
- https://cocoindex.io/docs/connectors/localfs/
- https://cocoindex.io/docs/ops/text/
- https://cocoindex.io/docs/programming_guide/function/
- https://cocoindex.io/docs/advanced_topics/exception_handlers/
- https://www.sqlite.org/fts5.html
