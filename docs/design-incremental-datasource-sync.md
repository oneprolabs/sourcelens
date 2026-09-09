# Incremental datasource sync and conversion

## Objective

Separate datasource discovery and download from document conversion so a
long-running conversion cannot retain the exclusive datasource-sync lease.
The design preserves current user-visible paths and existing workspaces while
making changed-file conversion, retries, and deletion safe.

## Confirmed behaviour

- A complete upstream scan marks an absent item as `missing` on its first
  observation. It remains available locally.
- Only a second consecutive complete scan that still does not contain the
  item marks it `deleted` and removes its source file and conversion sidecar.
- An incomplete scan never increments the missing counter or deletes data.
- The existing `delete_missing` option remains the explicit opt-in for source
  deletion. When disabled, absent items are retained as today.
- Conversion retries at most three times. A fourth failure is retained for an
  explicit retry or a later source-content change.

## Compatibility boundary

The initial rollout retains the current datasource root and sidecars. New
layout metadata may describe `source/`, `derived/`, and `.sourcelens/`, but
no historical workspace is physically moved in this change. Answers and UI
citations always use the original relative source path.

## Target workflow

```text
exclusive sync: discover -> diff -> download changed -> write manifest
                                                        -> enqueue conversion

bounded conversion: convert changed -> checkpoint -> update conversion state
```

The sync task completes after durable discovery/download state is written.
Conversion is a separate, non-exclusive task with its own recovery lifecycle.

## Delivery slices

1. Add two-complete-scan deletion semantics and regression tests.
2. Persist conversion work independently and allow sync to skip inline
   conversion behind a safe default/feature flag.
3. Add plugin conversion task dispatch, progress, recovery, and a separate
   conversion lease.
4. Add retry state, no-progress timeout, and failure retry API.
5. Introduce the physical source/derived layout only after read compatibility
   is verified.

## Verification

- `pytest lensnode/tests/plugins/test_feishu_datasource.py`
- Relevant Django `lens` task and consumer tests for slices 2--4.
- Existing workspaces continue to resolve source paths and sidecars unchanged.
