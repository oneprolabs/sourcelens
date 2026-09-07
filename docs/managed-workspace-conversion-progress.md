# Managed Workspace Conversion Progress

Managed Workspace conversion tasks expose phase-aware progress through the
task execution `metadata` object and a datasource's `current_sync` field.
Existing `progress_percent`, `progress_current`, `progress_total`, and
`progress_message` fields remain available for compatibility.

## Progress fields

| Field | Meaning |
| --- | --- |
| `phase` | Stable current phase enum. |
| `overall_progress_percent` | Overall conversion progress, from 0 to 100. Only terminal `SUCCESS` reports 100. |
| `phase_progress` | The current phase's `{current, total, unit}` counters. Completion is scoped to this phase. |
| `progress_counts` | Lifecycle counts for the whole conversion. |
| `last_substantive_progress_at` | UTC timestamp of the latest progress event that changed conversion work or phase. |

The current phase values are `DISCOVERING_FILES`, `PARSING_DOCUMENTS`,
`PROCESSING_EMBEDDED_IMAGES`, `FINALIZING`, and `COMPLETED`. Not every
conversion uses every phase. `PROCESSING_EMBEDDED_IMAGES` scopes its counters
to the current file, identified by `current_file`. LensNode ends at
`FINALIZING` and 99%; SourceLens emits `COMPLETED` only after it records
terminal task status `SUCCESS`.

While file discovery is still enumerating the workspace, its final population
is not yet known. Periodic discovery events therefore use `null` for totals
whose final value is unknown, while still advancing `phase_progress.current`
and `last_substantive_progress_at`. Once discovery finishes, the final file
total is emitted before document processing begins.

## Counter semantics

`progress_counts.total` counts all discovered workspace files.
`candidates` counts files supported by the enabled conversion policy.
`processed` counts every discovered file whose outcome is known, including
unsupported files. `converted`, `failed`, and `skipped` classify processed
candidates; `unsupported` classifies processed non-candidates.

`PARSING_DOCUMENTS.phase_progress` always uses the stable population of
convertible files. A message saying `Processed N/M convertible files` never
means the overall task has completed. Callers must use task status to determine
terminal state and `overall_progress_percent` to render overall progress.
