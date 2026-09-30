# CocoIndex local verification — 2026-09-30

## Scope and environment

Verified the runtime slice in issue #698 against baseline `5976f614` and the
implementation commits `ccb26c3b` / `1216b4b6`, including the fixes described below.
All execution used isolated local Docker containers. The existing development
node remains disabled: CocoIndex is not installed there and
`LENSNODE_TEXT_INDEX_ENABLED` is unset/false.

The runtime container used the existing LensNode image, the working tree mounted
read-only, and a disposable Python 3.12 virtual environment with CocoIndex 1.0.24
and pytest. The existing runtime dependencies were loaded from the image. The
real datasource workspace was mounted read-only; index state was private to the
verification container. Runtime networking was disconnected for the repeated
build and retrieval checks.

Backend regression uses a separate PostgreSQL 17 database and Redis, isolated
from development data. These services support existing backend tests; CocoIndex
uses no PostgreSQL connection. Frontend tests/build use an isolated copy of the
working tree and the existing frontend container image.

## Results

| Check | Result |
|---|---|
| CocoIndex focused tests | 30 passed |
| LensNode complete suite | 1,114 passed; 17 failed |
| Baseline comparison for LensNode failures | Same 17 failures reproduced on `5976f614`; 219 other tests in the three affected modules passed |
| Backend complete suite with isolated PostgreSQL/Redis | 1,133 passed; 1 failed; 151 subtests passed |
| Frontend complete unit suite | 656 passed |
| Frontend production build | Passed; existing bundle size warning remains |
| Release-note and installer Python tests | 40 passed, 36 subtests passed |
| AI model setup Shell tests | Passed |
| Runtime operation Shell tests | Passed using the repository's fake Docker executable |
| Optional compose configuration | Passed |

The remaining backend failure is
`AdminRunTrajectoryAPITests.test_admin_pages_parent_and_child_events_with_one_global_cursor`: the first cursor page returns event ID `[1]` where the fixture expects `[3]`.
The same failure was reproduced independently on baseline `5976f614` using the
same isolated PostgreSQL/Redis services.

The 17 LensNode failures are in `test_history.py`, `test_planned_runtime.py`, and
`test_retrieval_gate.py`. Their `SimpleNamespace` runtime substitutes do not
implement the trajectory contract's `new_span` / `exit_span` methods. The failure
set was compared using JUnit reports and is identical to the baseline. These
unrelated trajectory fixtures were not changed as part of this integration.

## Real datasource and Agent-tool checks

Used the existing local SourceLens Git datasource with its full retained manifest
(1,254 records). No original datasource files were modified.

- Initial publication: **1,036 searchable files**, **8,708 chunks**.
- Published SQLite size: **24,473,600 bytes**.
- Repeated offline publication: **0 changed files**, **0 deleted files**, identical
  generation `6e32cfe836b79348af3bc125aa5ff14a64dd3081ab06be0d0b95eaf3954170d4`.
- The real `build_agent_tools` registry with environment-loaded configuration
  exposed `search_indexed_workspace`.
- English `LensNode` and Chinese `数据源` queries both returned indexed hits.
- Returned mount-relative paths opened successfully using `read_workspace_file`.
  Citation recording, line ranges, and completion trace events were verified.
- Retrieval ran with the image's original interpreter, which has no CocoIndex
  installed, demonstrating that published-index queries do not require the engine.
- Disabled configuration omitted the indexed tool and retained ordinary search.
- SQLite schema inspection found text/metadata/FTS tables, with no vector or
  embedding columns. Repeated indexing and retrieval succeeded offline.

The focused real-engine tests additionally cover unchanged memo reuse, changed
file processing, confirmed deletion, stale-hit fallback, incomplete catalogs,
source changes during preparation, component errors, publication failure/retry,
concurrent writer exclusion, path scope, conversion hashes, and CLI-to-tool reads.

## Findings fixed during verification

1. **Duplicate retained manifest records.** The real catalog repeats an identical
   `AGENTS.md` entry. The adapter now collapses identical documents, preserving
   their source identity, while rejecting conflicting identities at one path.
   Added positive and negative regression cases.
2. **Native output polluted CLI JSON.** Offline CocoIndex Rust diagnostics bypass
   Python's `redirect_stdout`. The CLI now redirects the native stdout descriptor
   as well as Python output while executing library code. A subprocess regression
   failed before the fix and passes after it; the offline real build now returns
   one valid JSON object on stdout.
3. **Existing test import prevented complete collection.** Corrected
   `test_upload_limits.py` to import `lensnode.datasource_sync`, consistently with
   the installed package and the rest of the LensNode tests. No upload behavior
   changed.

## Reproduction

With optional CocoIndex and pytest installed in the runtime test interpreter:

```sh
cd lensnode
python -m pytest -q -c pyproject.toml --rootdir=. -p no:cacheprovider tests --tb=short
python -m pytest -q -c pyproject.toml -p no:cacheprovider \
  tests/test_text_index_documents.py tests/test_text_index_pipeline.py \
  tests/test_text_index_tool.py
```

Selecting LensNode's own project file prevents the repository-root pytest import
mode from treating the outer `lensnode/` project directory as the import package.

Backend full run, with a dedicated test database and Redis available:

```sh
cd backend
DJANGO_SETTINGS_MODULE=core.settings python -m pytest -q -p no:cacheprovider --tb=short
```

Frontend and deployment configuration:

```sh
cd frontend
npm run test:unit
npm run build
```

```sh
docker compose -f docker-compose.dev.yml -f docker-compose.text-index.yml config --quiet
python -m pytest -q -p no:cacheprovider scripts/test_release_notes.py tests/test_installer_platform.py
bash tests/test_configure_ai_model.sh
bash scripts/test_sourcelensctl.sh
```

Local logs, JUnit reports, and the real retrieval script/results are stored under
`/tmp/sourcelens-698-verification/`. This is a disposable local artifact directory.

## Acceptance boundary

The implemented runtime slice works when explicitly enabled in the isolated
local environment. It is not enabled in the existing development node or deployed
to production. There is no new administration page in this slice, and no browser
or model-driven chat acceptance was performed. Automatic post-sync indexing,
platform UI/lifecycle orchestration, and production installer integration remain
open in #698. The repository-wide suite is not reported as completely green.
