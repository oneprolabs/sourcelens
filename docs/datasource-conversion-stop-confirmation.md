# Datasource conversion executor stop confirmation

Cancelling a conversion immediately records `REVOKED`. This is the platform's
cancellation outcome. It does not establish that LensNode has stopped executing.
Cleanup and replacement operations must check the original task's independent
stop evidence.

## API

Both endpoints require the existing datasource admin permissions and scope the
task lookup to that datasource and the conversion module.

```http
POST /api/lens/admin/datasources/{datasource_uuid}/cancel-conversion/
Content-Type: application/json

{"task_id": "original-task-id"}
```

The exact `task_id` selects the original task even after revocation. Repeating
the request resends its cancellation/confirmation command while stop remains
unconfirmed. An unknown ID returns 404 and never falls back to another task.
A confirmed task returns its existing evidence without cancelling anything.
Omitting `task_id` preserves the existing latest-active-task cancellation API.

```http
GET /api/lens/admin/datasources/{datasource_uuid}/conversion-tasks/{task_id}/stop-status/
```

Cancellation and status responses contain the same contract:

```json
{
  "uuid": "datasource-uuid",
  "task_id": "original-task-id",
  "task_execution_id": 123,
  "status": "REVOKED",
  "stop_confirmation": {
    "state": "confirmed",
    "task_id": "original-task-id",
    "lensnode_uuid": "owning-node-uuid",
    "connection_id": "authenticated-control-connection",
    "executor_instance_id": "executor-boot-uuid",
    "source": "lensnode_callback",
    "confirmed_at": "2026-10-12T08:00:00+00:00"
  }
}
```

`state` is `unconfirmed` until explicit stop evidence is received. Unconfirmed
responses have null source, confirmation timestamp, connection and executor
instance; the expected owning node may be known. GET is read-only. Confirmation
is stored in the task's metadata, independently of task status, result and
finish time. `confirmed_at` is the server receipt time; `executor_stopped_at`
in task metadata retains the executor's reported exit/fence timestamp.

## Executor protocol and reconciliation

Cancellation commands carry the exact task ID and datasource UUID and target
the node recorded on the task, including when the datasource now uses another
node. LensNode validates the identity against local work before cancelling it.

- Running work receives a cooperative cancellation signal. Its terminal
  `datasource_convert_done` is emitted only after the actual conversion thread
  exits. Cancelling the asyncio wrapper retains thread ownership and capacity
  until that thread exits.
- Queued work exits the execution queue before acknowledgement. A persistent
  task fence prevents duplicate or delayed dispatch from starting it again.
- Absent work is fenced before sending `datasource_convert_stopped` with source
  `lensnode_absent_fenced`. A cancellation missing datasource identity cannot
  establish an absent-work confirmation.
- Original terminal results are persisted and replayed, including summaries,
  instead of being replaced with empty cancellation results. Both terminal
  frame types remain in the pending outbox until `datasource_terminal_ack`.

Both frame types carry `executor_stopped: true`, datasource UUID, executor boot
identity, stop source and timestamp. The server validates the task/module,
datasource, authenticated node and currently bound connection. A known request
ID must identify the same task. Late terminal callbacks merge missing evidence
without changing `REVOKED`, existing results, errors, finish time, checkpoints or
history. They may populate a missing summary, but cannot replace an existing
one or update a newer conversion's datasource state. Duplicates retain the first
confirmation's provenance. A dedicated stopped frame changes evidence only.
If a terminal acknowledgement is lost, the same authenticated node may replay
the frame on its current connection without changing the first confirmation's
connection or provenance. Cancellation grace cleanup also retains confirmed
evidence when it finalizes the task outcome.

Reconnect reconciliation includes revoked tasks without confirmation and
resends their exact cancellation commands. The existing health/startup cleanup
also retries these tasks on online nodes, throttled to at least 30 seconds
between periodic attempts. The next cleanup tick determines actual retry
latency. Offline nodes stay unconfirmed until connectivity returns. Neither a
timeout, disconnection, empty operation snapshot nor the cancellation grace
period establishes confirmation.

## Compatibility and storage

No database migration is required. Older task records and older LensNode
callbacks remain unconfirmed until an upgraded LensNode explicitly acknowledges
them; fields such as `manual_immediate` and `cancel_grace_expired` are not proof.
Backend and LensNode must both be upgraded to use the new contract.

LensNode stores task fences and original terminal frames in
`{workspace_path}/.lensnode/conversion-stops.sqlite3` using the Python standard
library. Keep the workspace volume persistent across daemon/container restarts.
Fences are retained so an arbitrarily delayed command cannot start a task
already reported stopped. Do not remove the store while old commands can still
be replayed. The contract assumes the existing single daemon owns a node's
workspace; concurrently running daemons against the same node/workspace are
unsupported.
