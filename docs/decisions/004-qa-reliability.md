# Q&A evidence and gateway reliability

Date: 2026-10-18

## Problem

Production investigations found cold LiteLLM import deadlocks, broad literal
searches, duplicate source/conversion hits, and final synthesis discarding early
results. Follow-up answers also crossed platform boundaries or confused a
documented condition with an incident's cause.

## Runtime contracts

- Django initializes LiteLLM during application startup, before concurrent
  gateway requests can trigger its cyclic imports and logging filters. This adds
  provider import time to startup instead of the first request.
- A disconnected SSE consumer signals its worker to stop and returns without
  awaiting a synchronous network read. The worker owns generator cleanup and
  budget-lock release. It suppresses late queue writes and closes the generator
  on its own thread once the active read returns.
- Cancelling an asyncio Future cannot interrupt an already blocked synchronous
  provider read. Such a read still relies on the configured provider timeout;
  these changes do not promise immediate cancellation at the provider.
- Gateway error logs include the Run UUID when available. Gateway transport
  errors, model usage records, and Q&A outcomes remain different measurements;
  an all-success usage table does not establish successful Q&A.

## Retrieval contracts

`search_workspace` treats each default query alternative as a case-insensitive
literal phrase, preserving spaces, punctuation and Chinese words. Only `|` or
`｜` separates alternatives. Use `regex=True` for regular expressions. Existing
clients relying on implicit word or Chinese n-gram expansion must send explicit
alternatives. Tool documentation and runtime guidance describe this change.

A source with a conversion sidecar uses the converted text as canonical search
content, matching what `read_workspace_file` reads. Results retain original
source paths and converted line numbers. Overlapping content hits are unique by
source path and line; repeated text at different source locations is retained.
All selected roots participate before ranking and limiting results.

Source include paths and the requested glob are intersected on the original
document path, including when canonical text lives in a conversion sidecar.
Ripgrep searches a superset containing those artifacts; the result gate applies
the intersection, retaining recursive, basename and brace-alternative glob semantics.
Navigation applies the same scope before its result limit.

A failed content search returns at most eight navigation paths with a combined
2,000-character path budget and respects the requested glob. This sample is not
answer evidence. Use `find_files` for a larger inventory. Its explicit results
remain valid evidence for tasks that actually ask to list files.

## Final synthesis contracts

Large histories share a bounded context among user context, prior assistant text
and each tool result, rather than retaining only the last 36,000 characters.
Reported pagination/read metadata is extracted from nested JSON tool envelopes.
It is labelled as reported metadata, not a unique-coverage verdict. Excerpts
explicitly indicate omitted material.

Within an execution attempt, bounded tool excerpts, arguments and metadata are
also retained outside replaceable graph messages. If graph summarization removes
an observed result, final synthesis restores it as quoted evidence. Original user
context is restored when absent. This cannot recover results never observed by
the current execution attempt, and excerpts do not prove complete row coverage.

Both ordinary execution and forced synthesis require source applicability to the
user's established platform and operation. Documentation alone cannot locate an
implementation's throw site or establish state ownership. A documented condition
is not an incident root cause without the required instance evidence.

## Timing and validation

Terminal frames carry the original `node_enqueued_at` and the actual attempt's
`node_sent_at`. The backend logs its receipt timestamp and monotonic handler
latency. Queue wait is measured using node timestamps; node-to-backend timestamp
differences also include clock skew. Existing trace `duration_ms` measures that
node execution attempt, while database Run duration includes admission, waiting
and terminal delivery. Historical delays cannot be retrospectively assigned to
one component from the new instrumentation.

Regression tests cover literal phrases, canonical conversion content, bounded
navigation, missing ripgrep restrictions, early pagination evidence, graph
summarization, concurrent cold startup, disconnect cleanup and terminal timing.
Four anonymous real-model synthesis checks covered pagination counts, platform
applicability, conditional incident explanations and missing implementation
sources. They validate synthesis behavior, not full production retrieval or UI.

Production acceptance still requires deploying both API and LensNode, verifying
the previously merged review-budget/parser fixes in the actual images, and
re-running representative questions against their configured sources.

## Protocol recovery and bounded review material (2026-10-19)

An unparsed DSML response is discarded before publication or tool execution.
Ordinary model calls can retry once with the original valid conversation and an
explicit native-tool-call correction; calls without tools request text only.
The malformed response is never inserted as an assistant turn. Both streaming
and non-streaming paths account for rejected-response usage, honor cancellation
and the existing gateway budget, and record the protocol retry separately from
length retries. A second protocol failure propagates. Control and summarization
calls keep their existing failure/fallback behavior instead of retrying.

When structured answer-review evidence exceeds its configured character limit,
repeated values are shared through an explicit reference table and uniform
object lists can use a column/row representation. JSON tool content is decoded
as structured evidence with an encoding marker. The representation retains all
facts and coverage flags; it does not summarize or select evidence. Reserved
encoding keys avoid collisions with source keys. The original character limit
still applies: evidence that cannot fit losslessly follows the existing
incomplete-review path. Review completeness and source completeness remain
independent.

## Activity-report follow-up (2026-10-19)

Production activity reports exposed a separate compaction failure: a summary
request without tools returned DSML text and the gateway rejected it. Summary
protocol failures now emit `deepagents.summarization.failed`, retain the original
history, and disable further compaction attempts for that middleware instance.
Invalid or placeholder summaries take the same path. Main-agent protocol
validation, cancellation, hard budget exhaustion, and unexpected exceptions
continue to propagate. Retaining history can increase context size; this fallback
does not guarantee that a long investigation fits the provider context window.

Large ledger metadata previously reduced evidence pages to one record before it
was omitted. Page sizing now budgets metadata before record reduction and includes
read progress before persisting it. A pending artifact write at the token reserve
gets one bounded write-and-publish opportunity, subject to the existing hard cap
and upload checks. Skill formatting cannot justify unsupported personnel claims.
See `docs/reference/activity-report-contract.md` for the detailed contracts.

Offline pagination replay of the four affected source snapshots (105–122 records)
completed in 7–9 pages while retaining GitHub coverage gaps. This validates saved
evidence traversal, not real-model report quality or recovery of missing sources.
No production deployment or online rerun is implied by these local checks.

Report validation failures occur before upload and do not consume transport
capability retries. The feedback identifies rejected statements so the model can
repair and revalidate the same file; every attempt still runs validation, and
actual upload failures retain their retry limit. Publication grace tracks the
latest response identity rather than a history count that compaction can reduce.
