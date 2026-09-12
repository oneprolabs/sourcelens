import asyncio
import collections
import json
import logging
import os
import signal
import shutil
import threading
from urllib.parse import urlencode

import httpx
from websockets.asyncio.client import connect

from .checkpoint import (
    checkpoint_enabled,
    checkpoint_ttl_hours,
    close_checkpoint_saver,
    cleanup_expired_checkpoints,
    cleanup_run_checkpoint,
    get_checkpoint_saver,
)
from .config import load_config
from .datasource_sync import DataSourceSyncError
from .datasource_sync import convert_managed_workspace
from .datasource_sync import inspect_datasource_path, list_datasource_files
from .datasource_sync import sync_datasource
from .datasource_sync import test_datasource_connection
from .datasource_sync import upload_managed_workspace
from .delegation_events import delegation_events
from .execution_queue import ExecutionClass, LensNodeExecutionQueue
from .executor import TASKS, LensNodeExecutor
from .gateway_model import RunCancelledError
from .logging_utils import (
    elapsed_since,
    format_duration,
    safe_ws_url,
    task_log,
    utc_now,
)
from .plugin_http import PluginHttpClientPool
from .plugin_package_loader import (
    PluginPackageLoadError,
    load_runtime_contract,
)
from .plugin_runtime import (
    PluginRuntimeError,
    acquire_plugin_lease,
    fetch_plugin_snapshot,
    retrieve_plugin_material,
)
from .runtime_resources import (
    cleanup_run_runtime_resources,
    cleanup_stale_runtime_resources,
    delete_skill_cache,
)
from .tls import create_config_ssl_context
from .tls import warn_if_verification_disabled
from .workspace import available_dirs

LOGGER = logging.getLogger("lensnode")
RUNTIME_CLEANUP_INTERVAL_S = 60 * 60


class LensNodeClient:
    """Async daemon that connects LensNode to the control plane."""

    def __init__(self, config):
        self.config = config
        if getattr(config, "node_options", ""):
            os.environ["NODE_OPTIONS"] = config.node_options
        workspace_path = getattr(config, "workspace_path", None)
        if workspace_path:
            cleanup_stale_runtime_resources(
                workspace_path,
                max_age_s=int(checkpoint_ttl_hours() * 3600),
            )
        self.ssl_context = create_config_ssl_context(config)
        self.gateway_http_client = httpx.Client(
            timeout=config.request_timeout_s,
            verify=self.ssl_context,
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=20,
                keepalive_expiry=60.0,
            ),
        )
        self.plugin_http_pool = PluginHttpClientPool(
            timeout=config.request_timeout_s,
            verify=self.ssl_context,
            max_connections=100,
            max_keepalive_connections=20,
            keepalive_expiry=60.0,
        )
        self.executor = LensNodeExecutor(
            config,
            http_client=self.gateway_http_client,
            plugin_http_pool=self.plugin_http_pool,
        )
        self.stopping = asyncio.Event()
        # Set while draining on shutdown/upgrade: stop accepting new runs and
        # stop heartbeating (a heartbeat would flip the node back to ONLINE
        # server-side and undo the DRAINING state). See stop().
        self.draining = asyncio.Event()
        self.websocket = None
        self.connected_at = None
        self.connect_started_at = None
        self.heartbeat_count = 0
        self.last_report_signature = None
        self.running_tasks = {}
        self.admitted_runs = set()
        self.execution_queue = LensNodeExecutionQueue(
            max_standard_concurrency=getattr(
                config,
                "max_concurrent_runs",
                1,
            ),
            max_exclusive_concurrency=getattr(
                config,
                "max_concurrent_datasource_syncs",
                1,
            ),
        )
        self.datasource_conversion_cancels = {}
        self.datasource_upload_cancels = {}
        self.active_datasource_operations = {}
        self._datasource_operations_lock = threading.Lock()
        self.datasource_sync_cancels = {}
        self.running_commands = {}
        self._checkpoint_resume_ready = None
        # Durable outbound buffer, persistent across reconnects. A run started
        # on one connection keeps executing across a blue/green API recycle and
        # emits run_event/run_output/run_done frames while the socket is down;
        # buffering them here (instead of on a per-connection queue that is
        # discarded on disconnect) means the next connection's send loop
        # flushes them rather than losing them. The send loop pops a frame
        # before sending and re-queues it at the front on a mid-send failure,
        # so an outage preserves frames and their order.
        #
        # Bounded so a prolonged outage (run threads keep emitting with no
        # consumer draining) can't grow it without limit and OOM-kill the node
        # — better to drop the oldest frames of one run than to lose every run.
        self._outbox = collections.deque()
        self._outbox_ready = asyncio.Event()
        self._outbox_max = int(os.getenv("LENSNODE_OUTBOX_MAX_FRAMES", "10000"))
        self._outbox_dropped = 0
        self._pending_trace_frames = collections.OrderedDict()
        self._pending_terminal_frames = collections.OrderedDict()
        self._pending_datasource_terminal_frames = collections.OrderedDict()

    def _enqueue(self, payload):
        """Append an outbound frame to the durable outbox.

        Not coroutine-safe with respect to threads; background run threads
        must schedule it via loop.call_soon_threadsafe(self._enqueue, payload).
        Drops the oldest frame when the buffer is full (see __init__). Safe
        against the send loop: that loop pops its in-flight frame out before
        awaiting, so a drop here never races the frame being sent.
        """

        if payload.get("type") == "run_trace_events":
            for event in payload.get("events") or []:
                sequence = event.get("sequence")
                if isinstance(sequence, int) and not isinstance(
                    sequence,
                    bool,
                ):
                    key = (str(payload.get("run_uuid") or ""), sequence)
                    self._pending_trace_frames[key] = payload
                    self._pending_trace_frames.move_to_end(key)
            while len(self._pending_trace_frames) > self._outbox_max:
                self._pending_trace_frames.popitem(last=False)
        if payload.get("type") == "run_done" and payload.get("run_uuid"):
            run_uuid = str(payload["run_uuid"])
            self._pending_terminal_frames[run_uuid] = payload
            self._pending_terminal_frames.move_to_end(run_uuid)
            while len(self._pending_terminal_frames) > self._outbox_max:
                self._pending_terminal_frames.popitem(last=False)
        if payload.get("type") in {
            "datasource_sync_done",
            "datasource_convert_done",
            "datasource_upload_done",
        } and payload.get("task_id"):
            task_id = str(payload["task_id"])
            self._pending_datasource_terminal_frames[task_id] = payload
            self._pending_datasource_terminal_frames.move_to_end(task_id)
            while len(self._pending_datasource_terminal_frames) > self._outbox_max:
                self._pending_datasource_terminal_frames.popitem(last=False)
        while len(self._outbox) >= self._outbox_max:
            self._outbox.popleft()
            self._outbox_dropped += 1
            if self._outbox_dropped == 1 or self._outbox_dropped % 1000 == 0:
                LOGGER.warning(
                    task_log(
                        (
                            "Dropping buffered frames for LensNode "
                            f"{self.config.name}. Current status is "
                            "outbox_full."
                        ),
                        details=[
                            f"MaxFrames: {self._outbox_max}",
                            f"TotalDropped: {self._outbox_dropped}",
                        ],
                    )
                )
        self._outbox.append(payload)
        self._outbox_ready.set()

    async def run_forever(self):
        """Run the client with reconnect backoff until stopped."""

        cleanup_task = asyncio.create_task(self._runtime_cleanup_loop())
        try:
            backoff_s = 1
            while not self.stopping.is_set():
                url = self._ws_url()
                self.connect_started_at = utc_now()
                LOGGER.info(
                    task_log(
                        (
                            "Starting to connect LensNode control channel "
                            f"{self.config.name}. The timeout is set to "
                            "10 secs."
                        ),
                        self.connect_started_at,
                        [
                            f"ControlPlaneWebSocket: {safe_ws_url(url)}",
                            f"WorkspacePath: {self.config.workspace_path}",
                        ],
                    )
                )
                try:
                    connected = await self._run_connection(url)
                    if connected:
                        backoff_s = 1
                except Exception as exc:
                    if self.stopping.is_set():
                        break
                    self._log_connection_error(exc)

                if self.stopping.is_set():
                    break
                LOGGER.info(
                    task_log(
                        (
                            "Starting to reconnect LensNode control channel "
                            f"{self.config.name}. The timeout is set to "
                            f"{format_duration(backoff_s)}."
                        )
                    )
                )
                await asyncio.sleep(backoff_s)
                backoff_s = min(backoff_s * 2, 30)
        finally:
            cleanup_task.cancel()
            await asyncio.gather(cleanup_task, return_exceptions=True)

    async def _runtime_cleanup_loop(self):
        """Periodically remove abandoned per-Run runtime directories."""

        workspace_path = getattr(self.config, "workspace_path", None)
        if not workspace_path:
            return
        while not self.stopping.is_set():
            await asyncio.sleep(RUNTIME_CLEANUP_INTERVAL_S)
            if self.stopping.is_set():
                break
            await asyncio.to_thread(
                cleanup_stale_runtime_resources,
                workspace_path,
                int(checkpoint_ttl_hours() * 3600),
            )
            active_run_uuids = [
                run_uuid
                for run_uuid in self.running_tasks
                if not run_uuid.startswith("datasource:")
            ]
            await asyncio.to_thread(
                cleanup_expired_checkpoints,
                workspace_path,
                active_run_uuids,
            )

    async def stop(self):
        """Gracefully drain in-flight runs, then stop the client.

        Triggered by SIGTERM/SIGINT on shutdown/upgrade (see _run_client).
        This is the single, reusable drain entry point: a future control-plane
        drain/upgrade command (issue #27) can call this same method. It stops
        accepting new runs, announces draining so the control plane routes new
        runs elsewhere, lets in-flight runs finish within the drain timeout,
        then closes the socket and cancels only what is still running past the
        deadline. An idle node (no in-flight runs) drains and exits at once.
        """

        if self.stopping.is_set() or self.draining.is_set():
            return
        self.draining.set()
        LOGGER.info(
            task_log(
                f"Draining LensNode {self.config.name} before shutdown.",
                details=[
                    f"InFlightRuns: {len(self.running_tasks)}",
                    "DrainTimeout: " f"{format_duration(self.config.drain_timeout_s)}",
                ],
            )
        )
        # Announce draining so the control plane flips this node out of
        # dispatch. Enqueued (not sent directly) so it stays ordered behind
        # buffered frames and never races the send loop on the socket; the
        # heartbeat loop has already stopped (it gates on draining), so this is
        # the node's last state frame and the DRAINING status sticks.
        self._enqueue(
            {
                "type": "node_draining",
                "lensnode_name": self.config.name,
            }
        )
        try:
            await self._drain_running_tasks()
            await self._flush_outbox()
        finally:
            self.stopping.set()
            if self.websocket is not None:
                try:
                    await self.websocket.close()
                except Exception:
                    LOGGER.exception("Failed to close LensNode websocket")
            for task in list(self.running_tasks.values()):
                task.cancel()
            if self.running_tasks:
                await asyncio.gather(
                    *self.running_tasks.values(),
                    return_exceptions=True,
                )
            await self.executor.drain_pending_workers()
            try:
                close_checkpoint_saver()
            finally:
                try:
                    self.plugin_http_pool.close()
                finally:
                    self.gateway_http_client.close()

    async def _drain_running_tasks(self):
        """Wait for in-flight runs to finish, bounded by the drain timeout."""

        tasks = list(self.running_tasks.values())
        if not tasks:
            return
        done, pending = await asyncio.wait(tasks, timeout=self.config.drain_timeout_s)
        if pending:
            LOGGER.warning(
                task_log(
                    (
                        f"Drain deadline reached for LensNode "
                        f"{self.config.name}; cancelling runs still active."
                    ),
                    details=[f"StillActive: {len(pending)}"],
                )
            )

    async def _flush_outbox(self, attempts=50):
        """Let the send loop flush buffered frames before the socket closes.

        The send loop runs until stopping is set (not draining), so it is still
        active here — this just yields long enough for node_draining and any
        final run_done frames to reach the server before we close.
        """

        while self._outbox and attempts > 0:
            await asyncio.sleep(0.1)
            attempts -= 1

    def _ws_url(self):
        """Return the token-authenticated WebSocket URL."""

        separator = "&" if "?" in self.config.control_ws_url else "?"
        return (
            f"{self.config.control_ws_url}"
            f"{separator}{urlencode({'token': self.config.token})}"
        )

    async def _run_connection(self, url):
        """Run one WebSocket connection until it closes."""

        connected = False
        connection_options = {
            "open_timeout": 10,
            "ping_interval": None,
        }
        if url.lower().startswith("wss://"):
            connection_options["ssl"] = self.ssl_context
        async with connect(
            url,
            **connection_options,
        ) as websocket:
            self.websocket = websocket
            try:
                await self._on_connected()
                connected = True
                tasks = [
                    asyncio.create_task(self._send_loop(websocket)),
                    asyncio.create_task(self._heartbeat_loop()),
                    asyncio.create_task(self._receive_loop(websocket)),
                ]
                done, pending = await asyncio.wait(
                    tasks,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                for task in done:
                    if task.cancelled():
                        continue
                    exception = task.exception()
                    if exception is not None:
                        raise exception
            finally:
                self.websocket = None
        return connected

    async def _on_connected(self):
        """Record connection state and send initial report."""

        self.connected_at = utc_now()
        duration = elapsed_since(self.connect_started_at or self.connected_at)
        LOGGER.info(
            task_log(
                (
                    "Finish connecting LensNode control channel "
                    f"{self.config.name}. Actual duration: {duration}."
                ),
                self.connected_at,
            )
        )
        await self._send_hello()
        self._restore_pending_trace_frames()
        self._restore_pending_terminal_frames()
        self._restore_pending_datasource_terminal_frames()

    def _restore_pending_trace_frames(self):
        """Requeue unacknowledged trace events in per-run sequence order.

        Frames emitted while a connection is down can be newer than frames
        that were already popped by the send loop but never acknowledged.
        Rebuilding each run's trace segment prevents the newer frames from
        overtaking those older pending frames after reconnect.
        """

        trace_frames = collections.OrderedDict()
        known_frame_ids = set()
        covered_sequences = collections.defaultdict(set)
        for frame in self._outbox:
            if frame.get("type") != "run_trace_events":
                continue
            run_uuid = str(frame.get("run_uuid") or "")
            sequences = tuple(
                event.get("sequence") for event in frame.get("events") or []
            )
            if run_uuid and sequences:
                trace_frames[(run_uuid, sequences)] = frame
                known_frame_ids.add(id(frame))
                covered_sequences[run_uuid].update(sequences)
        for (run_uuid, sequence), frame in self._pending_trace_frames.items():
            if id(frame) in known_frame_ids or sequence in covered_sequences[run_uuid]:
                continue
            key = (run_uuid, (sequence,))
            trace_frames.setdefault(key, frame)
            known_frame_ids.add(id(frame))
            covered_sequences[run_uuid].add(sequence)

        ordered_by_run = collections.defaultdict(list)
        for (run_uuid, _sequences), frame in trace_frames.items():
            ordered_by_run[run_uuid].append(frame)
        for frames in ordered_by_run.values():
            frames.sort(
                key=lambda frame: min(
                    event.get("sequence", 0) for event in frame.get("events") or []
                )
            )

        rebuilt = collections.deque()
        emitted_runs = set()
        for frame in self._outbox:
            run_uuid = str(frame.get("run_uuid") or "")
            if run_uuid in ordered_by_run and run_uuid not in emitted_runs:
                rebuilt.extend(ordered_by_run[run_uuid])
                emitted_runs.add(run_uuid)
            if frame.get("type") == "run_trace_events":
                continue
            rebuilt.append(frame)
        for run_uuid, frames in ordered_by_run.items():
            if run_uuid not in emitted_runs:
                rebuilt.extend(frames)
        self._outbox = rebuilt
        if self._outbox:
            self._outbox_ready.set()

    def _restore_pending_terminal_frames(self):
        """Requeue terminal Run frames until the control plane confirms them."""

        queued_run_uuids = {
            str(frame.get("run_uuid") or "")
            for frame in self._outbox
            if frame.get("type") == "run_done"
        }
        for run_uuid, frame in self._pending_terminal_frames.items():
            if run_uuid not in queued_run_uuids:
                self._outbox.append(frame)
        if self._outbox:
            self._outbox_ready.set()

    def _restore_pending_datasource_terminal_frames(self):
        """Requeue datasource terminal frames until the server receives them."""

        queued_task_ids = {
            str(frame.get("task_id") or "")
            for frame in self._outbox
            if frame.get("type")
            in {
                "datasource_sync_done",
                "datasource_convert_done",
                "datasource_upload_done",
            }
        }
        for task_id, frame in self._pending_datasource_terminal_frames.items():
            if task_id not in queued_task_ids:
                self._outbox.append(frame)
        if self._outbox:
            self._outbox_ready.set()

    async def _receive_loop(self, websocket):
        """Receive and dispatch control-plane messages."""

        try:
            async for raw_message in websocket:
                await self._handle_message(raw_message)
        finally:
            self._log_disconnected(None, None)

    async def _handle_message(self, raw_message):
        """Dispatch one inbound control-plane message."""

        try:
            message = json.loads(raw_message)
        except json.JSONDecodeError:
            LOGGER.warning(
                task_log(
                    (
                        "Ignored control-plane message for LensNode "
                        f"{self.config.name}. Current status is invalid_json."
                    )
                )
            )
            return

        message_type = message.get("type")
        if message_type == "run_start":
            await self._start_command(message)
        elif message_type == "delegation_done":
            delegation_events.publish(message)
        elif message_type == "skill_cache_invalidate":
            await asyncio.to_thread(
                delete_skill_cache,
                getattr(self.config, "workspace_path", None),
                message.get("skill_uuid"),
            )
        elif message_type == "list_dirs":
            await self._handle_list_dirs(message)
        elif message_type == "datasource_check_path":
            await self._handle_datasource_check_path(message)
        elif message_type == "datasource_list_files":
            await self._handle_datasource_list_files(message)
        elif message_type == "datasource_test_connection":
            await self._handle_datasource_test_connection(message)
        elif message_type == "datasource_sync":
            await self._start_datasource_sync(message)
        elif message_type == "plugin_datasource_sync":
            await self._start_datasource_sync(message, plugin=True)
        elif message_type == "datasource_convert":
            await self._start_datasource_conversion(message)
        elif message_type == "datasource_upload":
            await self._start_datasource_upload(message)
        elif message_type == "datasource_convert_cancel":
            task_id = str(message.get("task_id") or "")
            cancel_event = self.datasource_conversion_cancels.get(task_id)
            if cancel_event is not None:
                cancel_event.set()
            LOGGER.info(
                task_log(
                    (
                        "Requested safe cancellation for command "
                        f"datasource_convert {task_id}."
                    )
                )
            )
        elif message_type == "datasource_cancel":
            task_id = str(message.get("task_id") or "")
            cancel_event = (
                self.datasource_sync_cancels.get(task_id)
                or self.datasource_upload_cancels.get(task_id)
            )
            if cancel_event is not None:
                cancel_event.set()
            LOGGER.info(
                task_log(
                    (
                        "Cancelled command datasource_sync "
                        f"{task_id} by control plane."
                    )
                )
            )
        elif message_type == "run_cancel":
            run_uuid = str(message.get("run_uuid") or "")
            task = self.running_tasks.get(run_uuid)
            command = None
            if task is not None:
                command = self.running_commands.get(run_uuid)
                if command is not None:
                    if not command.get("_explicit_cancel"):
                        command["_explicit_cancel"] = True
                        task.cancel()
                else:
                    task.cancel()
            if task is None:
                cleanup_run_checkpoint(
                    run_uuid,
                    getattr(self.config, "workspace_path", None),
                )
                cleanup_run_runtime_resources(
                    getattr(self.config, "workspace_path", None),
                    run_uuid,
                )
            elif command is None:
                cleanup_run_checkpoint(
                    run_uuid,
                    getattr(self.config, "workspace_path", None),
                )
            LOGGER.info(
                task_log(
                    ("Cancelled command run_start " f"{run_uuid} by control plane.")
                )
            )
        elif message_type == "run_done_ack":
            run_uuid = str(message.get("run_uuid") or "")
            self._pending_terminal_frames.pop(run_uuid, None)
            self._outbox = collections.deque(
                frame
                for frame in self._outbox
                if not (
                    frame.get("type") == "run_done"
                    and str(frame.get("run_uuid") or "") == run_uuid
                )
            )
            workspace_path = getattr(self.config, "workspace_path", None)
            cleanup_deferred = self.executor.defer_cleanup_until_worker_stops(
                run_uuid,
                workspace_path,
            )
            if not cleanup_deferred:
                cleanup_run_checkpoint(run_uuid, workspace_path)
                cleanup_run_runtime_resources(workspace_path, run_uuid)
        elif message_type == "datasource_terminal_ack":
            task_id = str(message.get("task_id") or "")
            frame = self._pending_datasource_terminal_frames.get(task_id)
            if frame is not None:
                self._acknowledge_datasource_terminal_frame(frame)
                self._outbox = collections.deque(
                    item
                    for item in self._outbox
                    if not (
                        item.get("type")
                        in {
                            "datasource_sync_done",
                            "datasource_convert_done",
                            "datasource_upload_done",
                        }
                        and str(item.get("task_id") or "") == task_id
                    )
                )
        elif message_type == "run_trace_events_ack":
            run_uuid = str(message.get("run_uuid") or "")
            last_sequence = message.get("last_sequence")
            if isinstance(last_sequence, int) and not isinstance(
                last_sequence,
                bool,
            ):
                acknowledged = [
                    key
                    for key in self._pending_trace_frames
                    if key[0] == run_uuid and key[1] <= last_sequence
                ]
                for key in acknowledged:
                    self._pending_trace_frames.pop(key, None)
        elif message_type == "connected":
            LOGGER.info(
                task_log(
                    (
                        "Connected LensNode session "
                        f"{message.get('lensnode_uuid')}. "
                        f"ProtocolVersion: {message.get('protocol_version')}."
                    )
                )
            )
        elif message_type == "hello_ack":
            LOGGER.info(
                task_log(f"Confirmed LensNode capability report {self.config.name}.")
            )
        elif message_type == "heartbeat_ack":
            LOGGER.debug("Received control frame: %s", message_type)
        elif message_type == "error":
            LOGGER.warning(
                task_log(
                    (
                        "Checked control-plane response for LensNode "
                        f"{self.config.name}. Current status is error."
                    ),
                    details=[f"ErrorFrame: {message}"],
                )
            )
        else:
            LOGGER.debug(
                task_log(
                    (
                        "Ignored control-plane command for LensNode "
                        f"{self.config.name}. Current status is unknown."
                    ),
                    details=[f"FrameType: {message_type}"],
                )
            )

    async def _start_command(self, message):
        """Start one LensNode command if local capacity allows it."""

        run_uuid = str(message.get("run_uuid") or "")
        if not run_uuid:
            return
        if self.draining.is_set():
            await self._send_busy(run_uuid, "LENSNODE_DRAINING")
            return
        if run_uuid in self.running_tasks:
            dispatch_id = message.get("dispatch_id")
            active_dispatch_id = self.running_commands.get(
                run_uuid,
                {},
            ).get("dispatch_id")
            if dispatch_id and run_uuid in self.admitted_runs:
                self._send_run_admitted(run_uuid, dispatch_id)
                LOGGER.info(
                    "Acknowledged duplicate delivery for active run %s.",
                    run_uuid,
                )
                return
            if dispatch_id and str(dispatch_id) == str(active_dispatch_id):
                LOGGER.info(
                    "Ignored duplicate delivery for queued run %s.",
                    run_uuid,
                )
                return
            if message.get("resume"):
                LOGGER.info(
                    "Ignored duplicate resume for active run %s.",
                    run_uuid,
                )
                return
            await self._send_busy(run_uuid, "LENSNODE_RUN_ACTIVE")
            return
        pending_sequences = [
            sequence
            for pending_run_uuid, sequence in self._pending_trace_frames
            if pending_run_uuid == run_uuid
        ]
        if pending_sequences:
            message = {
                **message,
                "trace_cursor": max(
                    int(message.get("trace_cursor") or 0),
                    max(pending_sequences),
                ),
            }

        LOGGER.info(
            task_log(
                f"Starting to run command: run_start {run_uuid}.",
                details=[
                    f"Task: {message.get('task')}",
                    f"TargetDirs: {len(message.get('target_dirs') or [])}",
                ],
            )
        )
        task = asyncio.create_task(self._execute_command(run_uuid, message))
        self.running_commands[run_uuid] = message
        self.running_tasks[run_uuid] = task
        task.add_done_callback(lambda item: self._consume_task_exception(item))

    def _send_run_admitted(self, run_uuid, dispatch_id):
        """Acknowledge one accepted dispatch without exposing its payload."""

        self._enqueue(
            {
                "type": "run_admitted",
                "run_uuid": run_uuid,
                "dispatch_id": str(dispatch_id),
            }
        )

    async def _handle_list_dirs(self, message):
        """List immediate subdirectories for requested paths and reply."""

        from pathlib import Path

        request_id = str(message.get("request_id") or "")
        paths = message.get("paths") or []
        result = {}
        for path in paths:
            p = Path(path)
            subdirs = []
            if p.is_dir():
                try:
                    for sub in sorted(p.iterdir(), key=lambda x: x.name):
                        if sub.is_dir() and not sub.name.startswith("."):
                            subdirs.append({"path": str(sub), "name": sub.name})
                            if len(subdirs) >= 30:
                                break
                except PermissionError:
                    pass
            result[path] = subdirs
        self._enqueue(
            {
                "type": "list_dirs_result",
                "request_id": request_id,
                "dirs": result,
            }
        )

    async def _handle_datasource_check_path(self, message):
        """Inspect a datasource path and reply to the control plane."""

        request_id = str(message.get("request_id") or "")
        try:
            result = inspect_datasource_path(
                message,
                workspace_path=self.config.workspace_path,
            )
        except DataSourceSyncError as exc:
            result = {
                "path": str(message.get("target_path") or ""),
                "source_compatible": False,
                "status": "blocked",
                "message": str(exc),
            }
        self._enqueue(
            {
                "type": "datasource_path_result",
                "request_id": request_id,
                "result": result,
            }
        )

    async def _handle_datasource_list_files(self, message):
        """Read one datasource manifest and reply with safe file metadata."""

        request_id = str(message.get("request_id") or "")
        try:
            result = await asyncio.to_thread(
                list_datasource_files,
                message,
                self.config.workspace_path,
            )
        except DataSourceSyncError as exc:
            result = {"error": str(exc)}
        self._enqueue(
            {
                "type": "datasource_files_result",
                "request_id": request_id,
                "result": result,
            }
        )

    async def _handle_datasource_test_connection(self, message):
        """Test datasource connectivity and reply to the control plane."""

        request_id = str(message.get("request_id") or "")
        try:
            result = await asyncio.to_thread(
                test_datasource_connection,
                message,
            )
        except DataSourceSyncError as exc:
            result = {
                "status": "failed",
                "message_code": str(exc),
                "message": str(exc),
            }
        self._enqueue(
            {
                "type": "datasource_connection_result",
                "request_id": request_id,
                "result": result,
            }
        )

    async def _start_datasource_sync(self, message, plugin=False):
        """Start one datasource sync without blocking WebSocket receive."""

        request_id = str(message.get("request_id") or "")
        task_id = str(message.get("task_id") or request_id)
        if not task_id:
            return

        task_key = f"datasource:{task_id}"
        if task_key in self.running_tasks:
            return
        cancel_event = threading.Event()
        self.datasource_sync_cancels[task_id] = cancel_event
        self._record_datasource_operation(
            task_id,
            message.get("datasource_uuid"),
            "sync",
            "starting",
        )
        message = {**message, "cancel_event": cancel_event}
        task = asyncio.create_task(self._execute_datasource_sync(message, plugin))
        self.running_tasks[task_key] = task
        task.add_done_callback(lambda item: self._consume_task_exception(item))

    async def _execute_datasource_sync(self, message, plugin=False):
        """Execute a datasource sync command in a worker thread."""

        request_id = str(message.get("request_id") or "")
        task_id = str(message.get("task_id") or request_id)
        task_key = f"datasource:{task_id}"
        loop = asyncio.get_running_loop()

        def emit(event):
            payload = {
                "type": "datasource_sync_event",
                "request_id": request_id,
                "task_id": task_id,
                **event,
            }
            loop.call_soon_threadsafe(self._enqueue, payload)

        try:
            if plugin:
                slot_acquired = False

                def report_plugin_queued():
                    self._enqueue(
                        {
                            "type": "datasource_sync_event",
                            "request_id": request_id,
                            "task_id": task_id,
                            "step": "queue",
                            "status": "queued",
                            "message": (
                                "Waiting for exclusive LensNode execution " "capacity."
                            ),
                        }
                    )

                await self._acquire_execution(
                    ExecutionClass.EXCLUSIVE,
                    cancel_event=message.get("cancel_event"),
                    on_queued=report_plugin_queued,
                )
                slot_acquired = True
                self._enqueue(
                    {
                        "type": "datasource_sync_event",
                        "request_id": request_id,
                        "task_id": task_id,
                        "step": "queue",
                        "status": "running",
                        "message": "Acquired exclusive LensNode capacity.",
                    }
                )
                try:
                    result = await asyncio.to_thread(
                        self._execute_plugin_datasource_sync,
                        message,
                        emit,
                    )
                finally:
                    if slot_acquired:
                        await self.execution_queue.release(ExecutionClass.EXCLUSIVE)
                if result.get("status") in {"failed", "cancelled"}:
                    self._enqueue(
                        {
                            "type": "datasource_sync_event",
                            "request_id": request_id,
                            "task_id": task_id,
                            "step": result.get("status"),
                            "status": result.get("status"),
                            "message": result.get("error")
                            or "Plugin datasource synchronization ended.",
                        }
                    )
                self._enqueue(
                    {
                        "type": "datasource_sync_done",
                        "request_id": request_id,
                        "task_id": task_id,
                        "status": result.get("status") or "success",
                        **result,
                    }
                )
                return
            command = {
                **message,
                "ai_gateway_url": self.config.ai_gateway_url,
                "lensnode_token": self.config.token,
                "gateway_http_client": self.gateway_http_client,
                "tls_skip_verify": getattr(self.config, "tls_skip_verify", False),
                "tls_ca_file": getattr(self.config, "tls_ca_file", None),
            }
            slot_acquired = False

            def report_queued():
                self._enqueue(
                    {
                        "type": "datasource_sync_event",
                        "request_id": request_id,
                        "task_id": task_id,
                        "step": "queue",
                        "status": "queued",
                        "message": (
                            "Waiting for exclusive LensNode execution " "capacity."
                        ),
                    }
                )

            await self._acquire_execution(
                ExecutionClass.EXCLUSIVE,
                cancel_event=message.get("cancel_event"),
                on_queued=report_queued,
            )
            slot_acquired = True
            self._enqueue(
                {
                    "type": "datasource_sync_event",
                    "request_id": request_id,
                    "task_id": task_id,
                    "step": "queue",
                    "status": "running",
                    "message": "Acquired exclusive LensNode capacity.",
                }
            )
            try:
                result = await asyncio.to_thread(
                    sync_datasource,
                    command,
                    self.config.workspace_path,
                    emit,
                )
            finally:
                if slot_acquired:
                    await self.execution_queue.release(ExecutionClass.EXCLUSIVE)
            self._enqueue(
                {
                    "type": "datasource_sync_done",
                    "request_id": request_id,
                    "task_id": task_id,
                    "status": result.get("status") or "success",
                    **result,
                }
            )
        except RunCancelledError:
            self._enqueue(
                {
                    "type": "datasource_sync_event",
                    "request_id": request_id,
                    "task_id": task_id,
                    "step": "cancelled",
                    "status": "cancelled",
                    "message": "Datasource synchronization cancelled.",
                }
            )
            self._enqueue(
                {
                    "type": "datasource_sync_done",
                    "request_id": request_id,
                    "task_id": task_id,
                    "status": "cancelled",
                    "error": "DATASOURCE_SYNC_CANCELLED",
                    "completion_reason": "DATASOURCE_SYNC_CANCELLED",
                    "stop_confirmation_source": "lensnode_callback",
                }
            )
        except DataSourceSyncError as exc:
            error_code = str(exc) or "DATASOURCE_SYNC_FAILED"
            if error_code == "LENS_SOURCE_SYNC_CANCELLED":
                self._enqueue(
                    {
                        "type": "datasource_sync_event",
                        "request_id": request_id,
                        "task_id": task_id,
                        "step": "cancelled",
                        "status": "cancelled",
                        "message": "Datasource synchronization cancelled.",
                    }
                )
                self._enqueue(
                    {
                        "type": "datasource_sync_done",
                        "request_id": request_id,
                        "task_id": task_id,
                        "status": "cancelled",
                        "error": "DATASOURCE_SYNC_CANCELLED",
                        "completion_reason": "DATASOURCE_SYNC_CANCELLED",
                        "stop_confirmation_source": "lensnode_callback",
                    }
                )
                return
            self._enqueue(
                {
                    "type": "datasource_sync_event",
                    "request_id": request_id,
                    "task_id": task_id,
                    "step": "failed",
                    "status": "failed",
                    "message": error_code,
                }
            )
            self._enqueue(
                {
                    "type": "datasource_sync_done",
                    "request_id": request_id,
                    "task_id": task_id,
                    "status": "failed",
                    "error": error_code,
                }
            )
        except Exception:
            LOGGER.exception(
                "Datasource synchronization failed task_id=%s",
                task_id,
            )
            self._enqueue(
                {
                    "type": "datasource_sync_event",
                    "request_id": request_id,
                    "task_id": task_id,
                    "step": "failed",
                    "status": "failed",
                    "message": "DATASOURCE_SYNC_FAILED",
                }
            )
            self._enqueue(
                {
                    "type": "datasource_sync_done",
                    "request_id": request_id,
                    "task_id": task_id,
                    "status": "failed",
                    "error": "DATASOURCE_SYNC_FAILED",
                }
            )
        finally:
            self.datasource_sync_cancels.pop(task_id, None)
            self.running_tasks.pop(task_key, None)

    def _execute_plugin_datasource_sync(self, message, emit=None):
        """Execute a trusted Plugin datasource using snapshot data."""

        material = None
        try:
            snapshot = fetch_plugin_snapshot(
                self.gateway_http_client,
                self.config.ai_gateway_url,
                self.config.token,
                message.get("snapshot_uuid"),
            )
            plugin_key = str(snapshot.get("plugin_key") or "")
            plugin_version = str(snapshot.get("plugin_version") or "")
            runtime = load_runtime_contract(plugin_key, plugin_version)
            if (
                not callable(runtime.build_datasource_command)
                or not callable(runtime.sync_datasource)
            ):
                return {
                    "status": "failed",
                    "error": "PLUGIN_DATASOURCE_UNSUPPORTED",
                }
            lease = acquire_plugin_lease(
                self.gateway_http_client,
                self.config.ai_gateway_url,
                self.config.token,
                message.get("snapshot_uuid"),
            )
            material = retrieve_plugin_material(
                self.gateway_http_client,
                self.config.ai_gateway_url,
                self.config.token,
                lease["lease_uuid"],
            )
        except PluginPackageLoadError:
            return {"status": "failed", "error": "PLUGIN_UNSUPPORTED"}
        except PluginRuntimeError as exc:
            return {"status": "failed", "error": str(exc)}
        except httpx.HTTPError:
            return {"status": "failed", "error": "PLUGIN_RUNTIME_UNAVAILABLE"}
        try:
            command = runtime.build_datasource_command(
                snapshot,
                material,
                message.get("trigger") or "plugin",
            )
            plugin_http_pool = getattr(
                self,
                "plugin_http_pool",
                None,
            )
            if plugin_http_pool is not None and callable(runtime.http_origins):
                resolved = snapshot.get("resolved_config") or {}
                endpoint = resolved.get("endpoint")
                connection_scope = (
                    snapshot.get("connection_uuid")
                    or snapshot.get("snapshot_uuid")
                    or message.get("snapshot_uuid")
                )
                command["provider_http_client"] = plugin_http_pool.bind(
                    plugin_key,
                    connection_scope,
                    runtime.http_origins(endpoint),
                )
            if message.get("cancel_event") is not None:
                command["cancel_event"] = message["cancel_event"]
            return runtime.sync_datasource(
                command,
                self.config.workspace_path,
                emit,
                sync_datasource,
            )
        except PluginRuntimeError as exc:
            return {"status": "failed", "error": str(exc)}
        except DataSourceSyncError as exc:
            if str(exc) == "LENS_SOURCE_SYNC_CANCELLED":
                return {
                    "status": "cancelled",
                    "error": "DATASOURCE_SYNC_CANCELLED",
                    "completion_reason": "DATASOURCE_SYNC_CANCELLED",
                }
            return {"status": "failed", "error": "PLUGIN_SYNC_FAILED"}
        except Exception:
            LOGGER.exception("Plugin datasource runtime failed")
            return {"status": "failed", "error": "PLUGIN_EXECUTION_FAILED"}
        finally:
            if material is not None:
                material["value"] = ""

    async def _start_datasource_conversion(self, message):
        """Start one managed workspace conversion in a worker thread."""

        request_id = str(message.get("request_id") or "")
        task_id = str(message.get("task_id") or request_id)
        if not task_id:
            return
        task_key = f"datasource-convert:{task_id}"
        if task_key in self.running_tasks:
            return
        cancel_event = threading.Event()
        self.datasource_conversion_cancels[task_id] = cancel_event
        self._record_datasource_operation(
            task_id,
            message.get("datasource_uuid"),
            "conversion",
            "starting",
        )
        task = asyncio.create_task(
            self._execute_datasource_conversion(
                {**message, "cancel_event": cancel_event}
            )
        )
        self.running_tasks[task_key] = task
        task.add_done_callback(lambda item: self._consume_task_exception(item))

    async def _execute_datasource_conversion(self, message):
        """Execute managed workspace conversion and emit safe results."""

        request_id = str(message.get("request_id") or "")
        task_id = str(message.get("task_id") or request_id)
        task_key = f"datasource-convert:{task_id}"
        loop = asyncio.get_running_loop()

        def emit(event):
            self._update_datasource_operation(task_id, event)
            payload = {
                "type": "datasource_convert_event",
                "request_id": request_id,
                "task_id": task_id,
                **event,
            }
            loop.call_soon_threadsafe(self._enqueue, payload)

        try:
            command = {
                **message,
                "ai_gateway_url": self.config.ai_gateway_url,
                "lensnode_token": self.config.token,
                "gateway_http_client": self.gateway_http_client,
                "tls_skip_verify": getattr(
                    self.config,
                    "tls_skip_verify",
                    False,
                ),
                "tls_ca_file": getattr(
                    self.config,
                    "tls_ca_file",
                    None,
                ),
            }
            slot_acquired = False

            def report_queued():
                self._enqueue(
                    {
                        "type": "datasource_convert_event",
                        "request_id": request_id,
                        "task_id": task_id,
                        "step": "queue",
                        "status": "queued",
                        "message": (
                            "Waiting for exclusive LensNode execution " "capacity."
                        ),
                    }
                )

            await self._acquire_execution(
                ExecutionClass.EXCLUSIVE,
                cancel_event=message.get("cancel_event"),
                on_queued=report_queued,
            )
            slot_acquired = True
            self._enqueue(
                {
                    "type": "datasource_convert_event",
                    "request_id": request_id,
                    "task_id": task_id,
                    "step": "queue",
                    "status": "running",
                    "message": "Acquired exclusive LensNode capacity.",
                }
            )
            try:
                result = await asyncio.to_thread(
                    convert_managed_workspace,
                    command,
                    self.config.workspace_path,
                    emit,
                )
            finally:
                if slot_acquired:
                    await self.execution_queue.release(ExecutionClass.EXCLUSIVE)
            self._enqueue(
                {
                    "type": "datasource_convert_done",
                    "request_id": request_id,
                    "task_id": task_id,
                    **result,
                }
            )
        except RunCancelledError:
            self._enqueue(
                {
                    "type": "datasource_convert_done",
                    "request_id": request_id,
                    "task_id": task_id,
                    "status": "cancelled",
                    "error": "DATASOURCE_CONVERSION_CANCELLED",
                    "completion_reason": "DATASOURCE_CONVERSION_CANCELLED",
                    "stop_confirmation_source": "lensnode_callback",
                }
            )
        except DataSourceSyncError as exc:
            LOGGER.warning(
                "Managed datasource conversion rejected task_id=%s: %s",
                task_id,
                exc,
            )
            self._enqueue(
                {
                    "type": "datasource_convert_done",
                    "request_id": request_id,
                    "task_id": task_id,
                    "status": "failed",
                    "error": str(exc) or "DATASOURCE_CONVERSION_FAILED",
                }
            )
        except Exception:
            LOGGER.exception(
                "Managed datasource conversion failed task_id=%s",
                task_id,
            )
            self._enqueue(
                {
                    "type": "datasource_convert_done",
                    "request_id": request_id,
                    "task_id": task_id,
                    "status": "failed",
                    "error": "DATASOURCE_CONVERSION_FAILED",
                }
            )
        finally:
            self.datasource_conversion_cancels.pop(task_id, None)
            self.running_tasks.pop(task_key, None)

    async def _start_datasource_upload(self, message):
        """Start one managed workspace upload in a worker thread."""

        request_id = str(message.get("request_id") or "")
        task_id = str(message.get("task_id") or request_id)
        if not task_id:
            return
        task_key = f"datasource-upload:{task_id}"
        if task_key in self.running_tasks:
            return
        cancel_event = threading.Event()
        self.datasource_upload_cancels[task_id] = cancel_event
        self._record_datasource_operation(
            task_id,
            message.get("datasource_uuid"),
            "upload",
            "starting",
        )
        task = asyncio.create_task(
            self._execute_datasource_upload(
                {**message, "cancel_event": cancel_event}
            )
        )
        self.running_tasks[task_key] = task
        task.add_done_callback(lambda item: self._consume_task_exception(item))

    async def _execute_datasource_upload(self, message):
        """Execute an upload and emit its conversion result."""

        request_id = str(message.get("request_id") or "")
        task_id = str(message.get("task_id") or request_id)
        task_key = f"datasource-upload:{task_id}"
        slot_acquired = False
        try:
            await self._acquire_execution(
                ExecutionClass.EXCLUSIVE,
                cancel_event=message.get("cancel_event"),
            )
            slot_acquired = True
            result = await asyncio.to_thread(
                upload_managed_workspace,
                message,
                self.config.workspace_path,
            )
            self._enqueue(
                {
                    "type": "datasource_upload_done",
                    "request_id": request_id,
                    "task_id": task_id,
                    **result,
                }
            )
        except DataSourceSyncError as exc:
            self._enqueue(
                {
                    "type": "datasource_upload_done",
                    "request_id": request_id,
                    "task_id": task_id,
                    "status": "failed",
                    "error": str(exc),
                }
            )
        except Exception:
            LOGGER.exception("Managed workspace upload failed task_id=%s", task_id)
            self._enqueue(
                {
                    "type": "datasource_upload_done",
                    "request_id": request_id,
                    "task_id": task_id,
                    "status": "failed",
                    "error": "DATASOURCE_UPLOAD_FAILED",
                }
            )
        finally:
            if slot_acquired:
                await self.execution_queue.release(ExecutionClass.EXCLUSIVE)
            self.datasource_upload_cancels.pop(task_id, None)
            self.running_tasks.pop(task_key, None)

    async def _acquire_execution(
        self,
        execution_class,
        cancel_event=None,
        on_queued=None,
    ):
        """Wait for execution capacity while honoring safe cancellation."""

        if cancel_event is None:
            return await self.execution_queue.acquire(
                execution_class,
                on_queued=on_queued,
            )
        acquire_task = asyncio.create_task(
            self.execution_queue.acquire(
                execution_class,
                on_queued=on_queued,
            )
        )
        try:
            while not acquire_task.done():
                if cancel_event is not None and cancel_event.is_set():
                    acquire_task.cancel()
                    await asyncio.gather(
                        acquire_task,
                        return_exceptions=True,
                    )
                    raise RunCancelledError(
                        "Managed datasource conversion was cancelled while " "queued."
                    )
                await asyncio.wait((acquire_task,), timeout=0.1)
            return acquire_task.result()
        except BaseException:
            if not acquire_task.done():
                acquire_task.cancel()
                await asyncio.gather(
                    acquire_task,
                    return_exceptions=True,
                )
            elif not acquire_task.cancelled() and acquire_task.exception() is None:
                await self.execution_queue.release(execution_class)
            raise

    async def _send_busy(self, run_uuid, reason):
        """Report a run that cannot start because local capacity is full."""

        self._enqueue(
            {
                "type": "run_event",
                "run_uuid": run_uuid,
                "step_type": "retrieval",
                "status": "failed",
                "detail": {
                    "message": task_log(
                        (
                            "Failed running command run_start "
                            f"{run_uuid}. Current status is busy."
                        ),
                        details=[f"Reason: {reason}"],
                    ),
                    "error": reason,
                },
            }
        )
        self._enqueue(
            {
                "type": "run_done",
                "run_uuid": run_uuid,
                "status": "failed",
                "error": reason,
            }
        )

    async def _execute_command(self, run_uuid, message):
        """Run one LensNode command without blocking WebSocket receive."""

        loop = asyncio.get_running_loop()

        def emit(payload):
            loop.call_soon_threadsafe(self._enqueue, payload)

        completed = False
        slot_acquired = False
        try:

            def report_queued():
                self._enqueue(
                    {
                        "type": "run_event",
                        "run_uuid": run_uuid,
                        "step_type": "retrieval",
                        "status": "running",
                        "detail": {
                            "queue_state": "QUEUED",
                            "message": ("Waiting for LensNode execution capacity."),
                        },
                    }
                )

            execution_class = (
                ExecutionClass.DELEGATED
                if message.get("parent_run_uuid")
                else ExecutionClass.STANDARD
            )
            await self._acquire_execution(
                execution_class,
                on_queued=report_queued,
            )
            slot_acquired = True
            self._enqueue(
                {
                    "type": "run_event",
                    "run_uuid": run_uuid,
                    "step_type": "retrieval",
                    "status": "running",
                    "detail": {"queue_state": "STARTED"},
                }
            )
            self.admitted_runs.add(run_uuid)
            if message.get("dispatch_id"):
                self._send_run_admitted(run_uuid, message["dispatch_id"])
            await self.executor.execute(message, emit)
            completed = True
        finally:
            try:
                if completed:
                    # Let terminal frames enter the outbox before the run stops
                    # being reported as active. A cancelled run has no terminal
                    # frame and must leave running_tasks immediately.
                    await asyncio.sleep(0)
                else:
                    await self.executor.drain_pending_workers()
            finally:
                if slot_acquired:
                    await self.execution_queue.release(execution_class)
                self.running_tasks.pop(run_uuid, None)
                self.running_commands.pop(run_uuid, None)
                self.admitted_runs.discard(run_uuid)

    def _consume_task_exception(self, task):
        """Consume task exceptions so cancelled runs do not leak warnings."""

        if task.cancelled():
            return
        try:
            task.exception()
        except asyncio.CancelledError:
            return

    def _record_datasource_operation(
        self,
        task_id,
        datasource_uuid,
        operation,
        phase,
    ):
        """Record one datasource operation for reconnect reconciliation."""

        with self._datasource_operations_lock:
            self.active_datasource_operations[str(task_id)] = {
                "task_id": str(task_id),
                "datasource_uuid": str(datasource_uuid or ""),
                "operation": operation,
                "phase": phase,
                "last_progress": {},
            }

    def _update_datasource_operation(self, task_id, event):
        """Keep the reconnect report aligned with durable progress events."""

        with self._datasource_operations_lock:
            operation = self.active_datasource_operations.get(str(task_id))
            if operation is None:
                return
            operation["phase"] = str(event.get("step") or "running")
            operation["last_progress"] = {
                key: event[key]
                for key in [
                    "progress_current",
                    "progress_total",
                    "progress_percent",
                    "timestamp",
                ]
                if key in event
            }

    def _remove_datasource_operation(self, task_id):
        """Stop reporting a datasource operation after terminal delivery."""

        with self._datasource_operations_lock:
            self.active_datasource_operations.pop(str(task_id), None)

    def _acknowledge_datasource_terminal_frame(self, payload):
        """Clear reconnect state after a datasource terminal frame is sent."""

        if payload.get("type") not in {
            "datasource_sync_done",
            "datasource_convert_done",
            "datasource_upload_done",
        }:
            return
        task_id = str(payload.get("task_id") or "")
        if not task_id:
            return
        if self._pending_datasource_terminal_frames.get(task_id) is payload:
            self._pending_datasource_terminal_frames.pop(task_id, None)
            self._remove_datasource_operation(task_id)

    def _reported_active_datasource_operations(self):
        """Return a stable snapshot of live datasource operations."""

        with self._datasource_operations_lock:
            return [
                dict(operation)
                for operation in self.active_datasource_operations.values()
            ]

    def _reported_active_runs(self):
        """Return runs executing or awaiting terminal-frame delivery."""

        active_runs = {
            key
            for key in self.running_tasks
            if not key.startswith(("datasource:", "datasource-convert:"))
        }
        active_runs.update(
            str(payload["run_uuid"])
            for payload in self._outbox
            if payload.get("type") == "run_done" and payload.get("run_uuid")
        )
        active_runs.update(self._pending_terminal_frames)
        return sorted(active_runs)

    async def _send_hello(self):
        """Send initial LensNode capabilities.

        Sent directly on the socket (not via the durable outbox) and only from
        _on_connected, before the send loop starts — so it is always this
        connection's first frame and never persists across a reconnect. A hello
        left buffered in the durable outbox would otherwise be replayed on the
        next connection carrying a stale active_runs snapshot, triggering a
        spurious reconcile pass server-side.
        """

        dirs = available_dirs(self.config.workspace_path)
        LOGGER.info(
            task_log(
                ("Starting to report LensNode capabilities " f"{self.config.name}."),
                details=[
                    f"WorkspacePath: {self.config.workspace_path}",
                    f"AvailableDirs: {len(dirs)}",
                    f"Tasks: {', '.join(task['name'] for task in TASKS)}",
                ],
            )
        )
        active_runs = self._reported_active_runs()
        active_datasource_operations = self._reported_active_datasource_operations()
        checkpoint_resume_ready = self._checkpoint_resume_available()
        await self.websocket.send(
            json.dumps(
                {
                    "type": "hello",
                    "lensnode_name": self.config.name,
                    "protocol_version": self.config.protocol_version,
                    "agent_version": self.config.agent_version,
                    "workspace_path": self.config.workspace_path,
                    "available_dirs": dirs,
                    "tasks": TASKS,
                    "active_runs": active_runs,
                    "active_datasource_operations": active_datasource_operations,
                    "labels": {
                        "mode": "local",
                        "datasource_sync_capacity": max(
                            1,
                            int(
                                getattr(
                                    self.config,
                                    "max_concurrent_datasource_syncs",
                                    1,
                                )
                            ),
                        ),
                        "run_document_attachments": True,
                        "run_checkpoint_resume": checkpoint_resume_ready,
                        "run_admission_checkpoint_v1": True,
                        "run_checkpoint_ttl_hours": checkpoint_ttl_hours(),
                    },
                },
                ensure_ascii=False,
            )
        )

    def _checkpoint_resume_available(self):
        """Return whether durable checkpoint storage is usable."""

        if self._checkpoint_resume_ready is not None:
            return self._checkpoint_resume_ready
        workspace_path = getattr(self.config, "workspace_path", None)
        if not checkpoint_enabled() or not workspace_path:
            self._checkpoint_resume_ready = False
            return False
        try:
            get_checkpoint_saver(workspace_path)
        except Exception:
            LOGGER.exception("Disabling run checkpoint resume: storage is unavailable")
            self._checkpoint_resume_ready = False
            return False
        self._checkpoint_resume_ready = True
        return True

    def _resource_metrics(self):
        """Return lightweight host resource usage for the control plane."""
        cpu_percent = None
        try:
            cpu_count = os.cpu_count() or 1
            cpu_percent = min(100.0, max(0.0, os.getloadavg()[0] / cpu_count * 100))
        except (AttributeError, OSError):
            pass
        memory_percent = None
        try:
            values = {}
            with open("/proc/meminfo", encoding="utf-8") as stream:
                for line in stream:
                    key, value = line.split(":", 1)
                    values[key] = int(value.strip().split()[0])
            total = values.get("MemTotal", 0)
            available = values.get("MemAvailable", values.get("MemFree", 0))
            if total:
                memory_percent = (total - available) / total * 100
        except (OSError, ValueError):
            pass
        disk_percent = None
        try:
            usage = shutil.disk_usage(self.config.workspace_path)
            disk_percent = usage.used / usage.total * 100 if usage.total else None
        except OSError:
            pass
        return {
            key: round(value, 1)
            for key, value in {
                "cpu_percent": cpu_percent,
                "memory_percent": memory_percent,
                "disk_percent": disk_percent,
            }.items()
            if value is not None
        }

    async def _heartbeat_loop(self):
        """Periodically report workspace state while connected.

        Stops once draining: a heartbeat sets the node ONLINE server-side,
        which would undo the DRAINING state and let new runs be dispatched
        here mid-shutdown.
        """

        while not self.stopping.is_set() and not self.draining.is_set():
            await asyncio.sleep(self.config.heartbeat_interval_s)
            dirs = available_dirs(self.config.workspace_path)
            signature = (
                tuple(item["path"] for item in dirs),
                tuple(task["name"] for task in TASKS),
            )
            self.heartbeat_count += 1
            if self.heartbeat_count == 1 or signature != self.last_report_signature:
                LOGGER.info(
                    task_log(
                        (
                            "Monitoring LensNode heartbeat "
                            f"{self.config.name}. Current status is "
                            "connected. "
                            f"Elapsed time so far: "
                            f"{elapsed_since(self.connected_at or utc_now())}."
                        ),
                        details=[
                            f"AvailableDirs: {len(dirs)}",
                            f"Tasks: {len(TASKS)}",
                        ],
                    )
                )
            self.last_report_signature = signature
            self._enqueue(
                {
                    "type": "heartbeat",
                    "available_dirs": dirs,
                    "tasks": TASKS,
                    "metrics": self._resource_metrics(),
                }
            )

    async def _send_loop(self, websocket):
        """Drain the durable outbox to the control plane over one connection.

        Pops a frame BEFORE awaiting the send (so a concurrent _enqueue that
        drops the oldest frame when the buffer is full can never touch the
        in-flight one), and on a mid-send failure re-queues it at the FRONT so
        the frame and its order survive for the next connection's send loop.
        Waits on _outbox_ready when the outbox is empty.

        Re-delivery is at-least-once: a frame whose bytes reached the server
        before the send raised is re-sent on reconnect. The backend tolerates
        this — a run's final_content frame reconciles the accumulated output —
        matching the reference ws_client design.
        """

        while not self.stopping.is_set():
            while self._outbox:
                payload = self._outbox.popleft()
                try:
                    await websocket.send(json.dumps(payload, ensure_ascii=False))
                except Exception:
                    self._outbox.appendleft(payload)
                    self._outbox_ready.set()
                    raise
            self._outbox_ready.clear()
            if self._outbox:
                continue
            await self._outbox_ready.wait()

    def _log_connection_error(self, error):
        """Log WebSocket connection errors."""

        LOGGER.warning(
            task_log(
                (
                    "Checked LensNode control channel "
                    f"{self.config.name}. Current status is error."
                ),
                details=[f"Error: {error}"],
            )
        )

    def _log_disconnected(self, status_code, message):
        """Log WebSocket close events."""

        details = [
            f"CloseStatus: {status_code}",
            f"CloseMessage: {message}",
        ]
        if self.connected_at:
            details.append(f"ConnectedDuration: {elapsed_since(self.connected_at)}")
        LOGGER.info(
            task_log(
                (
                    "Stopped LensNode control channel "
                    f"{self.config.name}. Current status is disconnected."
                ),
                details=details,
            )
        )


async def _run_client():
    """Run LensNode and bind process signals to graceful shutdown."""

    config = load_config()
    warn_if_verification_disabled(config, LOGGER)
    client = LensNodeClient(config)
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(
            signum,
            lambda: asyncio.create_task(client.stop()),
        )
    await client.run_forever()


def _init_sentry():
    """Initialize Sentry error tracking if configured.

    Reuses the backend's SENTRY_* environment variables so LensNode and
    the backend report into the same Sentry project.
    """

    enabled = os.getenv("SENTRY_ENABLED", "").lower() in ("1", "true", "yes")
    dsn = os.getenv("SENTRY_DSN", "")
    if not enabled or not dsn:
        return

    try:
        import sentry_sdk
    except ImportError:
        LOGGER.warning("sentry-sdk not installed; skipping Sentry init.")
        return

    sentry_sdk.init(
        dsn=dsn,
        environment=os.getenv("SENTRY_ENVIRONMENT", "production"),
        release=os.getenv("SENTRY_RELEASE", "") or None,
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
        send_default_pii=os.getenv("SENTRY_SEND_DEFAULT_PII", "false").lower()
        in ("1", "true", "yes"),
    )


def main():
    """CLI entrypoint."""

    logging.basicConfig(
        level="INFO",
        format="%(message)s",
    )
    logging.getLogger("websockets").setLevel(logging.WARNING)
    _init_sentry()
    asyncio.run(_run_client())


if __name__ == "__main__":
    main()
