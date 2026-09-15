# PR #593 acceptance test cases and execution report

## Scope and environment

- Date: 2026-09-15.
- Branch: `fix/local-plugin-runtime`; commit: `0cf91a36`.
- PR: https://github.com/oneprolabs/sourcelens/pull/593.
- Related issue: https://github.com/oneprolabs/sourcelens/issues/596.
- Scope: the existing PR, including manual uploads, datasource task handling,
  datasource details, run workspace isolation, and delegated run lifecycle.
- Browser: Ego Lite through `ego-browser`; task space **1**,
  `SourceLens PR 593 acceptance`, page `p1`.
- Target: `http://localhost:8000` (local development).
- Execution state: authentication prerequisite blocked. The browser displays
  the SourceLens login form. The task space was handed to the user for login.
- No protected workflow has been executed yet. No browser failures in the
  changed functionality have been established.

## Prerequisites and test data

1. Sign in with an account authorized to manage datasources and assistants.
2. Confirm the local UI, API, worker, scheduler, and LensNode are running the
   target revision. API reload alone does not reload Celery or LensNode code.
3. Use an online development LensNode and a working LLM configuration.
4. Create isolated resources prefixed `QA-593`; do not change existing business
   datasources, credentials, model settings, or scheduled tasks for testing.
5. Prepare small files with distinct searchable markers: TXT, Markdown, CSV,
   JSON, a valid PDF, a PNG, and ZIP/TAR/TGZ archives containing text files.
   Prepare a second version with the same filename but different content,
   a harmless unsupported `.exe` file, and an invalid ZIP file.
6. For collaboration, use two test assistants with distinct bound datasource
   markers. Confirm the requested execution actually delegates to them.

## Browser test cases

All cases below are pending authentication unless a result is explicitly
recorded in the execution log. Expectations are requirements to test, not
claims that the implementation already meets them.

| ID | Priority | Steps | Expected result |
| --- | --- | --- | --- |
| AUTH-01 | P0 | Open the local application while signed out. | A usable login form appears, with email verification and password login options. |
| UP-01 | P0 | Create a `QA-593` datasource; choose manual upload and an online node; save. | Creation succeeds without connection or credential fields; the datasource is labeled manual upload. |
| UP-02 | P0 | Select TXT, Markdown, CSV, and JSON files together in the upload editor. Submit once and wait for terminal tasks. | Every selected file has its own task and persists with the expected filename and content. |
| UP-03 | P1 | Upload a valid PDF and PNG to the test datasource. | Both are accepted by the picker and processing completes, or a specific actionable processing error is shown; accepted extensions alone do not count as success. |
| UP-04 | P0 | Upload a ZIP with two marked text files; inspect the file view and original-file list after completion. | Extracted files are available; original filename, upload time, and size remain visible as metadata. Runtime archive deletion requires supplemental inspection. |
| UP-05 | P1 | Upload valid TAR and TGZ archives with distinct contents. | Both formats process successfully and expose the expected extracted content. |
| UP-06 | P1 | Drag supported files into the editor, then attempt an unsupported harmless `.exe` file. | Supported files enter the selection; unsupported input is rejected with understandable feedback. |
| UP-07 | P0 | Upload the second version of an existing filename; refresh and reopen details. | The current original-file list does not duplicate the filename; task history retains version records and the current content is the new version. |
| UP-08 | P0 | Add another file through edit mode; navigate between available steps and save. | Existing files remain, the new file is added, and step navigation preserves edited values. |
| UP-09 | P0 | Delete one uploaded test file/version using the editor, then refresh. | The chosen upload and its content disappear; other uploads remain usable. |
| UP-10 | P1 | Submit an invalid ZIP and inspect its task. | The task reaches a clear failure state; the interface remains usable and subsequent valid uploads can succeed. |
| UI-01 | P0 | Inspect upload datasource card and detail footer. | No sync, enable/disable sync, availability refresh, or separate upload action is offered; edit remains available. |
| UI-02 | P1 | Open upload details and processing history. | Node identity is displayed; original files show names, times, and sizes; per-file tasks identify filenames. |
| UI-03 | P1 | Open a managed workspace datasource in read-only mode. | Details show its node and managed directory; inappropriate sync/upload controls are absent. |
| UI-04 | P1 | Open the connection creation interface. | Manual upload is not presented as a reusable connection type. |
| UI-05 | P1 | Repeat datasource list, upload editor, and detail inspection in Chinese, English, and Spanish. | Labels resolve without translation keys; filenames, timestamps, and task statuses remain readable. |
| UI-06 | P1 | Inspect the upload editor and detail view at desktop and 390px widths. | Required controls remain reachable; long filenames and original-file columns do not cause inaccessible horizontal overflow. |
| DS-01 | P1 | On an isolated syncable test datasource, inspect the default interval; try 599 seconds, then 600 seconds. | Default interval is one day; values below 600 are rejected; 600 can be saved. |
| DS-02 | P0 | Start a test datasource sync, cancel it, and reopen its history. | Cancellation reaches a terminal state promptly and does not leave a permanently cancelling task. |
| DS-03 | P1 | Inspect scheduling information for the new upload datasource. | No automatic sync is shown. Absence of a scheduled database record requires supplemental inspection. |
| RUN-01 | P0 | Bind only the test datasource to a test assistant; ask for its marker and filename. | The response retrieves the bound content and cites a resource-relative path without exposing runtime host prefixes. |
| RUN-02 | P0 | Start a second conversation with a separately bound test datasource and ask for the first marker. | It cannot retrieve the first conversation's unbound content; a successful second run does not overwrite the first run's resources. |
| RUN-03 | P0 | Ask Smart Collaboration to consult both test assistants and combine their distinct markers. | Actual delegated tasks appear, complete, and contribute results to the parent answer. A direct answer without delegation does not satisfy this case. |
| RUN-04 | P0 | After RUN-03 completes, refresh and reopen the parent conversation and progress details. | The parent answer and delegated summaries remain accessible without orphan child conversations or replay errors. |
| RUN-05 | P1 | Stop a test collaboration while a child is active; inspect the parent result and activity history. | Cancellation is understandable and unfinished child work is not reported as successfully completed. |

## Supplemental checks and browser limits

These checks need read-only runtime inspection or targeted automated tests.
They must not be reported as passed solely from a successful browser answer.

| ID | Check | Required evidence |
| --- | --- | --- |
| INT-01 | Delegation dispatch includes the correct parent session UUID. | Dispatch payload or the focused backend regression test. |
| INT-02 | Child runtime root nests below the parent run. | `sessions/<parent-session>/runs/<parent-run>/delegations/<child-run>` observed during a live delegated run. |
| INT-03 | Older dispatches without a parent session use scratch storage. | Targeted runtime regression test; the current UI does not produce legacy payloads. |
| INT-04 | Completed delegated roots are removed while ordinary retained runs remain. | Before/after runtime directory inspection correlated to actual run IDs. |
| INT-05 | Recent descendants protect a stale parent against retention cleanup. | Controlled isolated cleanup test; do not age or sweep existing user workspaces. |
| INT-06 | Only terminal children are aggregated and reaped. | Backend regression test with both active and terminal children, or correlated read-only records. |
| INT-07 | Upload archives are temporary and unrelated versions survive deletion. | Runtime file inspection after upload/delete terminal events. |
| INT-08 | Uploads do not get scheduled sync records. | Read-only periodic-task inspection for the test datasource UUID. |
| INT-09 | Recovery rebinds queued datasource work to a new node connection. | Isolated failure-injection environment; do not restart a shared node with unrelated active work. |
| INT-10 | Missing collaboration model falls back to the default, with a clear error if neither exists. | Isolated configuration test; do not alter the user's shared model configuration. |

## Execution log

| ID | Method | Result | Evidence |
| --- | --- | --- | --- |
| AUTH-01 | Ego Lite, task space 1, `p1` | PASS | Snapshot displays the SourceLens login heading, required email input, send-code button, and password-login switch. |
| AUTH-GATE | Ego Lite, task space 1, `p1` | PASS | User authenticated and the management console opened successfully. |
| UP-01 | Ego Lite, task space 1, `p1` | PASS | Existing datasource `对对对` is labeled `手动上传`, uses node `local-dev-lensnode`, and exposes only an upload action plus edit/details flow. |
| UP-02 | Ego Lite, task space 1, `p1` | PASS (smoke) | Selected `ego-593.txt` and `ego-593.zip` in one file chooser operation; UI reported `上传任务已开始` and showed `ego-593.zip · 正在同步`. |
| UP-04 | Ego Lite, task space 1, `p1` | PASS (partial) | Detail view shows `ego-593.zip` and extracted `ego-593.txt`, with upload time and size in the `原始上传文件` section. Content search and archive cleanup were not tested. |
| UI-01 | Ego Lite, task space 1, `p1` | PASS | Detail footer contains `编辑` and no sync, refresh-availability, or separate upload button; list card exposes upload as the intended entry point. |
| UI-02 | Ego Lite, task space 1, `p1` | PASS | Detail view shows `手动上传`, node identity, original filenames, timestamps, and sizes. |
| UI-03 | Ego Lite, task space 1, `p1` | PASS (smoke) | Managed workspace card displays `托管工作区`, node availability, and `检查路径`; no upload action is shown on that card. |
| UP-03/05/06/07/08/09/10, UI-04/05/06, DS-01/02/03, RUN-01/02/03/04/05 | Not executed | PENDING | Requires additional isolated test data, full task completion, or collaboration setup. |
| UP-02/04 task completion | Ego Lite, task space 1, `p1` | BLOCKED | After waiting, the list showed `LENS_SOURCE_SYNC_BUSY`; the UI did not reach a terminal success state during this run. The metadata display itself worked. |
| INT checks | Not executed in this browser task | PENDING | Require the evidence described above. |

Previous ship-it validation at the same commit reported 6 passing backend
delegated-service tests and 4 passing LensNode delegated/cleanup tests, plus
Python compilation and diff checks. These results are historical context and
are not counted as Ego Lite acceptance results in this report.

## Resumption and cleanup

- After the user confirms login, resume task space **1** using
  `takeOverTaskSpace(1)` and inspect the current page before acting.
- Record actual outcomes and evidence per case; distinguish failures,
  environment blockers, partial checks, and unexecuted cases.
- Remove only resources created for this test after recording results.
- Do not mark this acceptance run complete until the authenticated cases have
  been exercised or explicitly recorded as blocked with concrete evidence.
- On successful completion, finish the task space once. Do not finish it
  while it is handed off and awaiting login.
