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

An evidence recheck clears the rejected batch and instructs the agent to regenerate
every intended final file, including unchanged files. The manifest and
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
and excludes file-write and artifact bookkeeping results. Missing or truncated
review material produces an inconclusive advisory result and
does not trigger regeneration or claim verification passed. Complete evidence
can still trigger one recheck. A complete-evidence review that remains negative
produces a partial outcome with verification diagnostics.
Bounded source retention can recover material replaced by previews or compaction;
see [Agent runtime](../reference/agent-runtime.md) for evidence and finalization rules.

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

## Automatic host staging

The model-visible `save_deliverable` tool is replaced by a final-output directory.
Models use file tools to write `/deliverables/<filename>`; Shell commands use
`deliverables/<filename>` relative to the Run cwd. Skill scripts retain their
Skill cwd and receive `SOURCELENS_DELIVERABLE_DIR` in their environment. Structured
script/transform arguments may use `{{SOURCELENS_DELIVERABLE_DIR}}/<filename>`;
the host resolves this explicit placeholder without shell expansion. Scratch outside that
directory stays private. Older Skill instructions mentioning the removed tool
are superseded by the runtime's automatic-delivery contract.

The host scans regular files before reviewing a proposed final answer and once
more during terminal wrap-up. It retains content checks, staged uploads, UUID
manifests and final-message publication. Unchanged uploads reuse their UUID but
still recheck current evidence; changed and removed files invalidate old entries.
Any collection, validation or upload failure withholds the whole batch and can
request at most two model corrections. Final wrap-up can stage files after the
last affordable model call without spending model tokens on a save operation.

At the token reserve, an artifact request with one pending file write can finish
that write and, if successful, make one further model call to complete the answer.
The allowance tracks response identity, so compaction or inserted guidance cannot
extend it. Failed or repeated writes and text-only requests receive no extension;
the hard token cap and upload checks still apply.

An answer recheck quarantines the previous output directory and clears its
manifest and append receipts. The replacement answer must regenerate every
intended attachment, preventing rejected files from entering the new batch.
Collection rejects symlinks, uses the existing per-file size limit, and accepts
at most 64 files. Failed/cancelled runs retain the backend's no-publication policy.
No schema migration is required; this changes node-side selection and staging,
not the existing backend publication and download contract.

When the files have just passed their content checks but the separate chat
notice introduces an unsupported personnel claim, replace that notice with a
brief attachment-only message. Retain the checked files and all source/review
coverage flags. Unsupported file content still requests bounded repair and
cannot publish. Final Decision checks still run against the selected answer.

Historical duplicate artifacts are not rewritten or deleted. Superseded bytes
remain stored for audit; retention and cleanup are separate work. Material that
still exceeds the review budget remains inconclusive rather than being silently
discarded to obtain a positive verdict.

## Verification

Backend regression coverage exercises real uploads, final-only visibility,
renamed replacements, multi-file batches, downloads, terminal idempotency,
foreign-manifest rejection, failure, cancellation, and legacy compatibility.
LensNode regressions cover normalized-path replacement, batch invalidation,
checkpoint recheck limits, terminal manifests, evidence preservation, bounded
JSON, and incomplete-review behavior. Browser acceptance uses ego-browser
against isolated services to check admin status, final-only chat files, and
HTML preview.
