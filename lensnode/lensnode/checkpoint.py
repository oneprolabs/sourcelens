"""Durable agent-run checkpoints for in-flight resume after a node restart.

Checkpoints are written to a SQLite file under the workspace (a host
persistent volume), so a node process crash or container recreate keeps
them as long as the volume survives. Each run is one LangGraph thread keyed
by its run_uuid; on a terminal state the thread is deleted so checkpoints
never accumulate.

The saver is a process-wide singleton: SqliteSaver 3.x serializes its own
access across threads, and checkpointing happens at agent node boundaries
so the local writes are cheap.
"""

import json
import logging
import math
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field

from langchain_core.messages import ToolMessage
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.sqlite import SqliteSaver

LOGGER = logging.getLogger("lensnode")

_CHECKPOINT_FILE = "lensnode.sqlite"
_METADATA_SCHEMA_VERSION = 2
_TRACE_SCHEMA_VERSION = 1
_saver = None
_saver_lock = threading.Lock()
_database_lock = threading.RLock()


class CheckpointResumeError(RuntimeError):
    """A resume command cannot be proven safe from durable state."""

    code = "CHECKPOINT_UNAVAILABLE"


@dataclass(frozen=True)
class ResumeState:
    """Checkpoint and immutable runtime metadata needed for a resume."""

    messages: tuple
    route_decision: dict
    history_assistant_turns: int
    decision_gates: dict = field(default_factory=dict)
    checkpoint_step: int = -1
    checkpoint_id: str = ""
    capability_state: dict = field(default_factory=dict)
    runtime_evidence: dict = field(default_factory=dict)
    guardrail_state: dict = field(default_factory=dict)
    pending_write_tool_call_ids: frozenset = field(
        default_factory=frozenset
    )
    last_trace_seq: int = 0
    current_attempt: int = 1
    open_call_ids: tuple = ()
    open_span_ids: tuple = ()
    parent_call_map: dict = field(default_factory=dict)
    consulted_sources: dict = field(default_factory=dict)


def checkpoint_enabled() -> bool:
    """Return whether run checkpoints are enabled for this node."""

    raw = os.getenv("LENSNODE_CHECKPOINT_ENABLED", "1").strip().lower()
    return raw not in {"0", "false", "off", "no"}


def checkpoint_ttl_hours() -> float:
    """Return the effective local retention window for orphan checkpoints."""

    raw = os.getenv("LENSNODE_CHECKPOINT_TTL_HOURS", "24")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 24.0
    return max(value, 1.0) if math.isfinite(value) else 24.0


def checkpoint_dir(workspace_path) -> str:
    """Return the directory holding the checkpoint SQLite file."""

    configured = os.getenv("LENSNODE_CHECKPOINT_DIR", "").strip()
    if configured:
        return configured
    return os.path.join(workspace_path, ".checkpoints")


def get_checkpoint_saver(workspace_path) -> SqliteSaver:
    """Return the process-wide SqliteSaver, creating it on first use."""

    global _saver
    if _saver is None:
        with _saver_lock:
            if _saver is None:
                directory = checkpoint_dir(workspace_path)
                os.makedirs(directory, mode=0o700, exist_ok=True)
                os.chmod(directory, 0o700)
                path = os.path.join(directory, _CHECKPOINT_FILE)
                connection = sqlite3.connect(
                    path, check_same_thread=False
                )
                os.chmod(path, 0o600)
                # Rollback journal, not WAL: WAL coordination across the
                # Docker Desktop bind mount can leave commits invisible to
                # other connections, which makes terminal cleanup appear to
                # not run. The checkpoint writes are small and local, so
                # rollback journal is plenty.
                saver = SqliteSaver(connection)
                # Metadata and LangGraph checkpoints share one connection,
                # so every transaction must also share one re-entrant lock.
                saver.lock = _database_lock
                saver.setup()
                connection.execute("PRAGMA journal_mode=DELETE")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS lensnode_run_metadata (
                        run_uuid TEXT PRIMARY KEY,
                        route_decision TEXT NOT NULL,
                        history_assistant_turns INTEGER NOT NULL,
                        runtime_state TEXT NOT NULL DEFAULT '{}',
                        decision_gates TEXT NOT NULL DEFAULT '{}',
                        checkpoint_id TEXT,
                        schema_version INTEGER NOT NULL DEFAULT 1,
                        updated_at REAL NOT NULL,
                        orphaned_at REAL
                    )
                    """
                )
                columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(lensnode_run_metadata)"
                    ).fetchall()
                }
                if "runtime_state" not in columns:
                    connection.execute(
                        "ALTER TABLE lensnode_run_metadata "
                        "ADD COLUMN runtime_state TEXT NOT NULL DEFAULT '{}'"
                    )
                if "orphaned_at" not in columns:
                    connection.execute(
                        "ALTER TABLE lensnode_run_metadata "
                        "ADD COLUMN orphaned_at REAL"
                    )
                if "checkpoint_id" not in columns:
                    connection.execute(
                        "ALTER TABLE lensnode_run_metadata "
                        "ADD COLUMN checkpoint_id TEXT"
                    )
                if "decision_gates" not in columns:
                    connection.execute(
                        "ALTER TABLE lensnode_run_metadata "
                        "ADD COLUMN decision_gates TEXT NOT NULL DEFAULT '{}'"
                    )
                if "schema_version" not in columns:
                    connection.execute(
                        "ALTER TABLE lensnode_run_metadata "
                        "ADD COLUMN schema_version INTEGER "
                        "NOT NULL DEFAULT 1"
                    )
                # A fresh process means every retained thread may have just
                # become orphaned. Start a full local retention window now so
                # it cannot expire before the control plane's advertised
                # resume deadline.
                connection.execute(
                    """
                    UPDATE lensnode_run_metadata
                    SET orphaned_at = ?
                    WHERE orphaned_at IS NULL
                    """,
                    (time.time(),),
                )
                connection.commit()
                _saver = saver
                LOGGER.info("Agent run checkpoints enabled: %s", path)
    return _saver


def close_checkpoint_saver() -> bool:
    """Close and clear the process-wide checkpoint saver."""

    global _saver
    with _saver_lock:
        if _saver is None:
            return False
        with _database_lock:
            _saver.conn.close()
            _saver = None
    return True


def thread_config(run_uuid):
    """Return the LangGraph invoke config that pins a run to its thread."""

    return {
        "configurable": {
            "thread_id": str(run_uuid),
            "checkpoint_ns": "",
        },
    }


def save_resume_metadata(
    run_uuid,
    workspace_path,
    *,
    route_decision=None,
    history_assistant_turns=0,
):
    """Persist the runtime decisions that must not change on resume."""

    saver = get_checkpoint_saver(workspace_path)
    with _database_lock:
        saver.conn.execute(
            """
            INSERT INTO lensnode_run_metadata (
                run_uuid,
                route_decision,
                history_assistant_turns,
                runtime_state,
                updated_at,
                orphaned_at
            ) VALUES (?, ?, ?, ?, ?, NULL)
            ON CONFLICT(run_uuid) DO UPDATE SET
                route_decision = excluded.route_decision,
                history_assistant_turns = excluded.history_assistant_turns,
                updated_at = excluded.updated_at,
                orphaned_at = NULL
            """,
            (
                str(run_uuid),
                json.dumps(route_decision or {}, sort_keys=True),
                max(int(history_assistant_turns or 0), 0),
                "{}",
                time.time(),
            ),
        )
        saver.conn.commit()


def save_decision_gates(run_uuid, workspace_path, decision_gates):
    """Persist advisory Decision gate verdicts for replay on resume."""

    saver = get_checkpoint_saver(workspace_path)
    with _database_lock:
        cursor = saver.conn.execute(
            """
            UPDATE lensnode_run_metadata
            SET decision_gates = ?, updated_at = ?
            WHERE run_uuid = ?
            """,
            (
                json.dumps(decision_gates or {}, sort_keys=True),
                time.time(),
                str(run_uuid),
            ),
        )
        saver.conn.commit()
    return cursor.rowcount == 1


def save_initial_checkpoint(run_uuid, workspace_path, messages):
    """Persist a resumable state before a non-graph model call starts."""

    saver = get_checkpoint_saver(workspace_path)
    saved = empty_checkpoint()
    saved["channel_values"] = {"messages": list(messages or [])}
    with _database_lock:
        saved_config = saver.put(
            thread_config(run_uuid),
            saved,
            {"source": "input", "step": -1, "parents": {}},
            {},
        )
        checkpoint_id = _config_checkpoint_id(saved_config)
        cursor = saver.conn.execute(
            """
            UPDATE lensnode_run_metadata
            SET checkpoint_id = ?, schema_version = ?, updated_at = ?
            WHERE run_uuid = ?
            """,
            (
                checkpoint_id,
                _METADATA_SCHEMA_VERSION,
                time.time(),
                str(run_uuid),
            ),
        )
        if cursor.rowcount != 1:
            raise CheckpointResumeError(
                "Cannot bind checkpoint without checkpoint metadata."
            )
        saver.conn.commit()
    return saved_config


def save_runtime_state(
    run_uuid,
    workspace_path,
    *,
    capability_state=None,
    runtime_evidence=None,
    guardrail_state=None,
    trace_state=None,
    consulted_sources=None,
):
    """Persist execution-gate state that must survive a process restart."""

    saver = get_checkpoint_saver(workspace_path)
    with _database_lock:
        snapshot = saver.get_tuple(thread_config(run_uuid))
        if snapshot is None:
            raise CheckpointResumeError(
                "Cannot persist runtime state without a checkpoint."
            )
        checkpoint_id = _config_checkpoint_id(snapshot.config)
        row = saver.conn.execute(
            """
            SELECT runtime_state
            FROM lensnode_run_metadata
            WHERE run_uuid = ?
            """,
            (str(run_uuid),),
        ).fetchone()
        try:
            payload = json.loads(row[0]) if row is not None else {}
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        for key, value in (
            ("capability_state", capability_state),
            ("runtime_evidence", runtime_evidence),
            ("guardrail_state", guardrail_state),
            ("trace_state", trace_state),
            ("consulted_sources", consulted_sources),
        ):
            if value is not None:
                payload[key] = value
        cursor = saver.conn.execute(
            """
            UPDATE lensnode_run_metadata
            SET runtime_state = ?, checkpoint_id = ?, schema_version = ?,
                updated_at = ?
            WHERE run_uuid = ?
            """,
            (
                json.dumps(payload, sort_keys=True),
                checkpoint_id,
                _METADATA_SCHEMA_VERSION,
                time.time(),
                str(run_uuid),
            ),
        )
        if cursor.rowcount != 1:
            raise CheckpointResumeError(
                "Cannot persist runtime state without checkpoint metadata."
            )
        saver.conn.commit()


def load_resume_state(run_uuid, workspace_path) -> ResumeState:
    """Load a complete checkpoint or reject the resume without executing."""

    if not checkpoint_enabled():
        raise CheckpointResumeError(
            "Cannot resume run because checkpointing is disabled."
        )
    try:
        saver = get_checkpoint_saver(workspace_path)
        with _database_lock:
            snapshot = saver.get_tuple(thread_config(run_uuid))
            row = saver.conn.execute(
                """
                SELECT route_decision, history_assistant_turns, runtime_state,
                       checkpoint_id, schema_version, decision_gates
                FROM lensnode_run_metadata
                WHERE run_uuid = ?
                """,
                (str(run_uuid),),
            ).fetchone()
            checkpoint_version_valid = bool(
                snapshot is not None
                and row is not None
                and row[3]
                and _checkpoint_is_ancestor(
                    saver,
                    str(row[3]),
                    snapshot,
                )
            )
    except CheckpointResumeError:
        raise
    except Exception as exc:
        raise CheckpointResumeError(
            "Cannot resume run because its checkpoint could not be read."
        ) from exc
    if snapshot is None:
        raise CheckpointResumeError(
            "Cannot resume run because its checkpoint is missing."
        )
    if row is None:
        raise CheckpointResumeError(
            "Cannot resume run because its checkpoint metadata is missing."
        )
    if row[4] != _METADATA_SCHEMA_VERSION:
        raise CheckpointResumeError(
            "Cannot resume run because its checkpoint schema is unsupported."
        )
    if not checkpoint_version_valid:
        raise CheckpointResumeError(
            "Cannot resume run because its checkpoint version does not match "
            "its runtime metadata."
        )
    try:
        route_decision = json.loads(row[0])
        runtime_state = json.loads(row[2])
        decision_gates = json.loads(row[5] or "{}")
    except (TypeError, ValueError) as exc:
        raise CheckpointResumeError(
            "Cannot resume run because its checkpoint metadata is invalid."
        ) from exc
    if not isinstance(runtime_state, dict):
        raise CheckpointResumeError(
            "Cannot resume run because its runtime state is invalid."
        )
    trace_state = _validated_trace_state(runtime_state.get("trace_state"))
    channel_values = snapshot.checkpoint.get("channel_values") or {}
    checkpoint_step = (snapshot.metadata or {}).get("step", -1)
    if not isinstance(checkpoint_step, int) or isinstance(
        checkpoint_step,
        bool,
    ):
        raise CheckpointResumeError(
            "Cannot resume run because its checkpoint step is invalid."
        )
    if not isinstance(decision_gates, dict):
        raise CheckpointResumeError(
            "Cannot resume run because its decision gates are invalid."
        )
    return ResumeState(
        messages=tuple(channel_values.get("messages") or ()),
        route_decision=route_decision,
        history_assistant_turns=max(int(row[1] or 0), 0),
        decision_gates=decision_gates,
        checkpoint_step=checkpoint_step,
        checkpoint_id=str(row[3]),
        capability_state=runtime_state.get("capability_state") or {},
        runtime_evidence=runtime_state.get("runtime_evidence") or {},
        guardrail_state=runtime_state.get("guardrail_state") or {},
        pending_write_tool_call_ids=_pending_write_tool_call_ids(
            snapshot.pending_writes
        ),
        last_trace_seq=trace_state["last_trace_seq"],
        current_attempt=trace_state["current_attempt"],
        open_call_ids=tuple(trace_state["open_call_ids"]),
        open_span_ids=tuple(trace_state["open_span_ids"]),
        parent_call_map=trace_state["parent_call_map"],
        consulted_sources=runtime_state.get("consulted_sources") or {},
    )


def _validated_trace_state(value):
    """Return safe trajectory continuation metadata or fail closed."""

    if value is None:
        return {
            "trace_schema_version": _TRACE_SCHEMA_VERSION,
            "last_trace_seq": 0,
            "current_attempt": 1,
            "open_call_ids": [],
            "open_span_ids": [],
            "parent_call_map": {},
        }
    if not isinstance(value, dict):
        raise CheckpointResumeError(
            "Cannot resume run because its trajectory state is invalid."
        )
    if value.get("trace_schema_version") != _TRACE_SCHEMA_VERSION:
        raise CheckpointResumeError(
            "Cannot resume run because its trajectory schema is unsupported."
        )
    last_trace_seq = value.get("last_trace_seq")
    current_attempt = value.get("current_attempt")
    open_call_ids = value.get("open_call_ids")
    open_span_ids = value.get("open_span_ids")
    parent_call_map = value.get("parent_call_map")
    valid_numbers = (
        isinstance(last_trace_seq, int)
        and not isinstance(last_trace_seq, bool)
        and last_trace_seq >= 0
        and isinstance(current_attempt, int)
        and not isinstance(current_attempt, bool)
        and current_attempt >= 1
    )
    valid_collections = (
        isinstance(open_call_ids, list)
        and all(isinstance(item, str) for item in open_call_ids)
        and isinstance(open_span_ids, list)
        and all(isinstance(item, str) for item in open_span_ids)
        and isinstance(parent_call_map, dict)
        and all(
            isinstance(key, str) and isinstance(item, str)
            for key, item in parent_call_map.items()
        )
    )
    if not valid_numbers or not valid_collections:
        raise CheckpointResumeError(
            "Cannot resume run because its trajectory state is invalid."
        )
    return {
        "trace_schema_version": _TRACE_SCHEMA_VERSION,
        "last_trace_seq": last_trace_seq,
        "current_attempt": current_attempt,
        "open_call_ids": open_call_ids,
        "open_span_ids": open_span_ids,
        "parent_call_map": parent_call_map,
    }


def _config_checkpoint_id(config):
    """Return the required checkpoint identifier from a saver config."""

    configurable = (config or {}).get("configurable") or {}
    checkpoint_id = configurable.get("checkpoint_id")
    if not checkpoint_id:
        raise CheckpointResumeError("Checkpoint has no version identifier.")
    return str(checkpoint_id)


def _checkpoint_is_ancestor(saver, ancestor_id, snapshot):
    """Return whether metadata is bound to this head or an ancestor."""

    current = snapshot
    visited = set()
    while current is not None:
        checkpoint_id = _config_checkpoint_id(current.config)
        if checkpoint_id == ancestor_id:
            return True
        if checkpoint_id in visited or current.parent_config is None:
            return False
        visited.add(checkpoint_id)
        current = saver.get_tuple(current.parent_config)
    return False


def _pending_write_tool_call_ids(pending_writes):
    """Return tool call IDs whose results are durable pending writes."""

    tool_call_ids = set()

    def collect(value):
        if isinstance(value, ToolMessage):
            tool_call_id = getattr(value, "tool_call_id", None)
            if tool_call_id:
                tool_call_ids.add(str(tool_call_id))
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                collect(item)

    for pending_write in pending_writes or ():
        if len(pending_write) < 3 or pending_write[1] != "messages":
            continue
        collect(pending_write[2])
    return frozenset(tool_call_ids)


def _cleanup_expired_checkpoints(saver, active_run_uuids=()):
    """Remove locally orphaned checkpoints after the bounded resume TTL."""

    now = time.time()
    cutoff = now - checkpoint_ttl_hours() * 3600
    active = {str(run_uuid) for run_uuid in active_run_uuids}
    with _database_lock:
        rows = saver.conn.execute(
            """
            SELECT run_uuid, orphaned_at
            FROM lensnode_run_metadata
            """
        ).fetchall()
        for run_uuid, orphaned_at in rows:
            if run_uuid in active:
                if orphaned_at is not None:
                    saver.conn.execute(
                        """
                        UPDATE lensnode_run_metadata
                        SET orphaned_at = NULL
                        WHERE run_uuid = ?
                        """,
                        (run_uuid,),
                    )
            elif orphaned_at is None:
                saver.conn.execute(
                    """
                    UPDATE lensnode_run_metadata
                    SET orphaned_at = ?
                    WHERE run_uuid = ?
                    """,
                    (now, run_uuid),
                )
        run_uuids = [
            run_uuid
            for run_uuid, orphaned_at in rows
            if run_uuid not in active
            and orphaned_at is not None
            and orphaned_at < cutoff
        ]
        for run_uuid in run_uuids:
            saver.delete_thread(run_uuid)
        if run_uuids:
            saver.conn.executemany(
                "DELETE FROM lensnode_run_metadata WHERE run_uuid = ?",
                [(run_uuid,) for run_uuid in run_uuids],
            )
        saver.conn.commit()
    return len(run_uuids)


def cleanup_expired_checkpoints(workspace_path, active_run_uuids=()):
    """Periodically remove expired checkpoints not active in this process."""

    if not checkpoint_enabled() or not workspace_path:
        return 0
    try:
        saver = get_checkpoint_saver(workspace_path)
        return _cleanup_expired_checkpoints(saver, active_run_uuids)
    except Exception:
        LOGGER.exception("Failed to clean up expired run checkpoints")
        return 0


def cleanup_run_checkpoint(run_uuid, workspace_path=None):
    """Delete the checkpoint thread for a terminal run, if any."""

    if not checkpoint_enabled():
        return
    if workspace_path is None:
        LOGGER.debug(
            "Skipping checkpoint cleanup for %s: no workspace path",
            run_uuid,
        )
        return
    try:
        saver = get_checkpoint_saver(workspace_path)
        with _database_lock:
            saver.delete_thread(str(run_uuid))
            saver.conn.execute(
                "DELETE FROM lensnode_run_metadata WHERE run_uuid = ?",
                (str(run_uuid),),
            )
            saver.conn.commit()
        LOGGER.info("Cleaned up checkpoint for run %s", run_uuid)
    except Exception:
        LOGGER.exception(
            "Failed to clean up checkpoint for run %s", run_uuid
        )
