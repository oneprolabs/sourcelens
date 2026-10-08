# Reviewed deliverable publication

Date: 2026-10-08

## Problem

Production runs uploaded artifacts before Jev reviewed their final answer. A
rejected answer could generate another artifact, leaving both versions visible.
The review input also kept only recent tool messages and sliced serialized
evidence, so write operations could displace the source evidence and a long
answer could consume the entire reviewer budget.

## Decision

New LensNode uploads use `staged=true`. Publication reuses the existing nullable
`RunOutputFile.message` association, so no database schema migration is needed.
Unlinked files stay private; linked files are published. The API derives
`candidate` for unlinked files on active or awaiting-input runs, and
`superseded` for unlinked files on finished runs. These are display states,
not stored database fields. The runtime tracks an upload manifest by normalized scratch path.
Saving the same path replaces its manifest entry; distinct paths support real
multi-file answers.

An evidence recheck clears the rejected batch and instructs the agent to save
every intended final file again, including unchanged files. The manifest and
recheck count persist in runtime checkpoint evidence. A successful terminal
frame supplies `deliverable_uuids`; the backend atomically validates and
publishes only those candidates by associating them with the final answer.
The remaining files stay unlinked. Failed or blocked runs do not publish
candidates. Awaiting-user-input runs retain candidates for resumption.

Uploads and terminal publication serialize on the Run row. Terminal-frame
redelivery cannot change the published set, and uploads after termination are
rejected. Missing manifests publish no staged files. Normal users, history
reuse, PDF exports, and shared answers see only published files. Staff retain
access to candidates and superseded bytes for audit; the admin file list shows
their status.

Answer review uses bounded valid JSON, reserves evidence alongside the answer,
and excludes file-write and artifact bookkeeping results. Truncation,
offloaded previews, and context compaction explicitly mark review coverage
incomplete. Incomplete coverage produces an inconclusive advisory result and
does not trigger regeneration or claim verification passed. Complete evidence
can still trigger one recheck. A complete-evidence review that remains negative
produces a partial outcome with verification diagnostics.

## Rollout and limits

Deploy the backend before upgrading LensNode; no new migration is required.
Existing files and legacy uploads keep their existing message association,
so old nodes remain compatible but retain their previous immediate-publication
behavior until upgraded. Do not roll the backend back to a version that ignores
`staged` while upgraded nodes are running.

Checkpoint owns the transient batch and recheck count. The backend still needs
the durable final message association after checkpoint cleanup, for downloads,
sharing, and history reuse. It validates the terminal manifest without reading
the node's checkpoint storage. This supersedes the initially proposed explicit
status column at the user's request to keep batch state in checkpoint.

Historical duplicate artifacts are not rewritten or deleted. Superseded bytes
remain stored for audit; retention and cleanup are separate work. Large or
compacted source material currently degrades review to inconclusive rather
than performing claim-by-claim review.

## Verification

Backend regression coverage exercises real uploads, final-only visibility,
renamed replacements, multi-file batches, downloads, terminal idempotency,
foreign-manifest rejection, failure, cancellation, and legacy compatibility.
LensNode regressions cover normalized-path replacement, batch invalidation,
checkpoint recheck limits, terminal manifests, evidence preservation, bounded
JSON, and incomplete-review behavior. Browser acceptance uses ego-browser
against isolated services to check admin status, final-only chat files, and
HTML preview.
