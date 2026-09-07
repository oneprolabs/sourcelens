import json
import logging
from urllib.parse import parse_qs
import uuid

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.utils import timezone

from .lensnode_auth import hash_lensnode_token
from .models import DataSource, LensNode, Run
from .run_trace import RunTraceValidationError, append_run_trace_events
from .services import (
    acknowledge_run_admitted,
    acknowledge_run_checkpoint_ready,
    append_lensnode_output,
    finish_lensnode_run,
    lensnode_group_name,
    reconcile_lensnode_active_runs,
    record_lensnode_run_event,
    resume_awaiting_runs_for_lensnode,
    schedule_lensnode_disconnect_grace_check,
)
from .tasks import reconcile_orphaned_datasource_conversions

LOGGER = logging.getLogger(__name__)
DETAIL_ITEMS_LIMIT = 200


class LensNodeConsumer(AsyncJsonWebsocketConsumer):
    """WebSocket endpoint used by LensNode execution workers."""

    async def connect(self):
        """Authenticate a LensNode by token and add it to its command group."""

        token = self._query_token()
        self.lensnode = await self._authenticate_lensnode(token)
        if self.lensnode is None:
            await self.close(code=4401)
            return

        self.group_name = lensnode_group_name(self.lensnode.uuid)
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        await self.send_json(
            {
                "type": "connected",
                "lensnode_uuid": str(self.lensnode.uuid),
                "protocol_version": self.lensnode.protocol_version,
            }
        )

    async def disconnect(self, code):
        """Mark the LensNode offline when its connection closes."""

        del code
        if getattr(self, "lensnode", None) is None:
            return
        await self.channel_layer.group_discard(self.group_name, self.channel_name)
        disconnected_at = await self._mark_disconnected(
            self.lensnode.uuid, self.channel_name
        )
        # Do not fail the node's runs here: the node reconnects on an interval
        # (e.g. across a blue/green API recycle) and re-declares its active
        # runs in the hello frame. Only a node still gone after the grace
        # window has its runs failed — schedule that deferred check now.
        if disconnected_at is not None:
            await self._schedule_disconnect_grace_check(
                self.lensnode.uuid, disconnected_at
            )

    async def receive_json(self, content, **kwargs):
        """Route inbound LensNode protocol frames."""

        del kwargs
        frame_type = content.get("type")
        if frame_type == "hello":
            await self._handle_hello(content)
        elif frame_type == "heartbeat":
            await self._handle_heartbeat(content)
        elif frame_type == "node_draining":
            await self._handle_node_draining(content)
        elif frame_type == "run_event":
            await self._handle_run_event(content)
        elif frame_type == "run_trace_events":
            await self._handle_run_trace_events(content)
        elif frame_type == "run_admitted":
            await self._handle_run_admitted(content)
        elif frame_type == "run_checkpoint_ready":
            await self._handle_run_checkpoint_ready(content)
        elif frame_type == "run_output":
            await self._handle_run_output(content)
        elif frame_type == "run_done":
            await self._handle_run_done(content)
        elif frame_type == "list_dirs_result":
            await self._handle_list_dirs_result(content)
        elif frame_type == "datasource_path_result":
            await self._handle_datasource_path_result(content)
        elif frame_type == "datasource_connection_result":
            await self._handle_datasource_connection_result(content)
        elif frame_type == "datasource_sync_event":
            await self._handle_datasource_sync_event(content)
        elif frame_type == "datasource_sync_done":
            await self._handle_datasource_sync_done(content)
        elif frame_type == "datasource_convert_event":
            await self._handle_datasource_sync_event(content)
        elif frame_type == "datasource_convert_done":
            await self._handle_datasource_conversion_done(content)
        elif frame_type == "datasource_upload_done":
            await self._handle_datasource_upload_done(content)
        else:
            await self.send_json(
                {
                    "type": "error",
                    "code": "LENSNODE_FRAME_UNKNOWN",
                    "frame_type": frame_type,
                }
            )

    async def lensnode_command(self, event):
        """Forward a control-plane command to the connected LensNode."""

        await self.send_json(event["payload"])

    def _query_token(self):
        """Return the token from the WebSocket query string."""

        raw = self.scope.get("query_string", b"").decode("utf-8")
        values = parse_qs(raw)
        return (values.get("token") or [""])[0]

    @database_sync_to_async
    def _authenticate_lensnode(self, token):
        """Authenticate a LensNode and mark it online."""

        if not token:
            return None
        lensnode = (
            LensNode.objects.filter(auth_token_hash=hash_lensnode_token(token))
            .exclude(auth_token_hash="")
            .first()
        )
        if lensnode is None:
            return None
        if lensnode.enrollment_status != LensNode.EnrollmentStatus.APPROVED:
            return None
        if lensnode.token_revoked:
            return None

        now = timezone.now()
        lensnode.status = LensNode.Status.ONLINE
        lensnode.connection_id = self.channel_name
        lensnode.last_authenticated_at = now
        lensnode.last_heartbeat_at = now
        # Reconnected — clear disconnected_at so a still-pending grace check
        # scheduled by the previous disconnect no-ops when it fires.
        lensnode.disconnected_at = None
        lensnode.save(
            update_fields=[
                "status",
                "connection_id",
                "last_authenticated_at",
                "last_heartbeat_at",
                "disconnected_at",
                "updated_at",
            ]
        )
        return lensnode

    @database_sync_to_async
    def _mark_disconnected(self, lensnode_uuid, connection_id):
        """Mark a LensNode offline if the current connection still owns it.

        Returns the disconnect timestamp when this connection was the live
        one, or None when a newer connection already replaced it — in which
        case this stale disconnect must neither flip the node offline nor
        schedule a grace check that could fail the new connection's runs.
        """

        now = timezone.now()
        updated = LensNode.objects.filter(
            uuid=lensnode_uuid,
            connection_id=connection_id,
        ).update(
            status=LensNode.Status.OFFLINE,
            connection_id="",
            disconnected_at=now,
            updated_at=now,
        )
        if updated:
            return now
        # CAS missed. If a newer connection took over, connection_id is now
        # that channel — leave it alone (a stale disconnect must not fail the
        # new connection's runs). But if the node was already flipped OFFLINE
        # with no owner (lensnode_health_task beat us to it after the node went
        # silent), stamp a disconnect episode now so the grace check still runs
        # and fails a genuinely-dead node's runs — otherwise dropping the old
        # unconditional fail call would leave them RUNNING until the much
        # slower idle reaper.
        stamped = LensNode.objects.filter(
            uuid=lensnode_uuid,
            connection_id="",
        ).update(disconnected_at=now, updated_at=now)
        return now if stamped else None

    @database_sync_to_async
    def _schedule_disconnect_grace_check(self, lensnode_uuid, disconnected_at):
        """Schedule the deferred grace check (delegates to the service)."""

        schedule_lensnode_disconnect_grace_check(lensnode_uuid, disconnected_at)

    async def _handle_hello(self, content):
        active_runs = content.get("active_runs") or []
        active_datasource_operations = content.get("active_datasource_operations")
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self._update_lensnode_report(content, require_versions=True)
        await database_sync_to_async(reconcile_orphaned_datasource_conversions)(
            self.lensnode.uuid,
            self.channel_name,
            active_datasource_operations,
        )
        await database_sync_to_async(reconcile_lensnode_active_runs)(
            self.lensnode.uuid, active_runs
        )
        # A node that (re)connected may own RUNNING runs with a resume
        # deadline whose checkpoints survived on its workspace volume.
        await database_sync_to_async(resume_awaiting_runs_for_lensnode)(
            self.lensnode.uuid,
            active_runs,
        )
        await self.send_json({"type": "hello_ack"})

    async def _handle_heartbeat(self, content):
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self._update_lensnode_report(content, require_versions=False)
        await self.send_json(
            {
                "type": "heartbeat_ack",
                "ts": timezone.now().isoformat(),
            }
        )

    async def _handle_node_draining(self, content):
        """Flip the node to DRAINING so no new runs are dispatched to it.

        Sent by a node shutting down/upgrading. The node stops heartbeating
        once draining, so this DRAINING status is not overwritten back to
        ONLINE until the node reconnects with a fresh hello.
        """

        del content
        await self._mark_draining(self.lensnode.uuid, self.channel_name)

    @database_sync_to_async
    def _mark_draining(self, lensnode_uuid, connection_id):
        """Mark a LensNode DRAINING if this connection still owns it."""

        LensNode.objects.filter(
            uuid=lensnode_uuid,
            connection_id=connection_id,
        ).update(
            status=LensNode.Status.DRAINING,
            updated_at=timezone.now(),
        )

    @database_sync_to_async
    def _update_lensnode_report(self, content, require_versions):
        """Persist LensNode-reported workspace, task, and version metadata."""

        lensnode = LensNode.objects.get(pk=self.lensnode.pk)
        lensnode.status = LensNode.Status.ONLINE
        lensnode.connection_id = self.channel_name
        lensnode.last_heartbeat_at = timezone.now()
        # A hello/heartbeat proves the node is back — clear any disconnect
        # stamp so a pending grace check no-ops.
        lensnode.disconnected_at = None
        if content.get("workspace_path") is not None:
            lensnode.workspace_path = content.get("workspace_path", "")
        if content.get("available_dirs") is not None:
            lensnode.available_dirs = content.get("available_dirs") or []
        if content.get("tasks") is not None:
            lensnode.tasks = content.get("tasks") or []
        if content.get("labels") is not None:
            lensnode.labels = content.get("labels") or {}
        if require_versions or content.get("protocol_version") is not None:
            lensnode.protocol_version = content.get("protocol_version", "")
        if require_versions or content.get("agent_version") is not None:
            lensnode.agent_version = content.get("agent_version", "")
        lensnode.save()
        self.lensnode = lensnode

    async def _handle_run_event(self, content):
        run_uuid = self._parse_uuid(content.get("run_uuid"))
        if run_uuid is None:
            await self._send_bad_frame("run_uuid is invalid")
            return
        step = await database_sync_to_async(record_lensnode_run_event)(
            run_uuid,
            content.get("step_type") or "retrieval",
            content.get("status") or "running",
            content.get("detail") or {},
        )
        if step is None:
            return
        LOGGER.info(
            "Recorded LensNode run event run_uuid=%s step_type=%s status=%s",
            run_uuid,
            content.get("step_type") or "retrieval",
            content.get("status") or "running",
        )

    async def _handle_run_trace_events(self, content):
        """Validate and append an immutable trajectory batch."""

        run_uuid = self._parse_uuid(content.get("run_uuid"))
        if run_uuid is None:
            await self._send_bad_frame("run_uuid is invalid")
            return
        try:
            result = await database_sync_to_async(append_run_trace_events)(
                run_uuid,
                self.lensnode.uuid,
                content.get("events"),
            )
        except RunTraceValidationError as exc:
            await self._send_bad_frame(str(exc))
            return
        await self.send_json(
            {
                "type": "run_trace_events_ack",
                "run_uuid": str(run_uuid),
                "inserted_count": result.inserted_count,
                "duplicate_count": result.duplicate_count,
                "last_sequence": result.last_sequence,
            }
        )

    async def _handle_run_admitted(self, content):
        """Persist a LensNode admission acknowledgement for one dispatch."""

        run_uuid = self._parse_uuid(content.get("run_uuid"))
        if run_uuid is None:
            await self._send_bad_frame("run_uuid is invalid")
            return
        await database_sync_to_async(acknowledge_run_admitted)(
            run_uuid,
            content.get("dispatch_id"),
            self.lensnode.uuid,
        )

    async def _handle_run_checkpoint_ready(self, content):
        """Persist durable checkpoint readiness for one dispatch."""

        run_uuid = self._parse_uuid(content.get("run_uuid"))
        if run_uuid is None:
            await self._send_bad_frame("run_uuid is invalid")
            return
        await database_sync_to_async(acknowledge_run_checkpoint_ready)(
            run_uuid,
            content.get("dispatch_id"),
            self.lensnode.uuid,
        )

    async def _handle_run_output(self, content):
        run_uuid = self._parse_uuid(content.get("run_uuid"))
        if run_uuid is None:
            await self._send_bad_frame("run_uuid is invalid")
            return
        run = await database_sync_to_async(append_lensnode_output)(
            run_uuid,
            content_delta=content.get("content_delta") or "",
            final_content=content.get("final_content"),
            reset=bool(content.get("reset")),
            citations=content.get("citations"),
            planned_evidence=content.get("planned_evidence"),
        )
        delta_chars = len(content.get("content_delta") or "")
        final_chars = len(content.get("final_content") or "")
        content_length = 0
        if run.output_message is not None:
            content_length = len(run.output_message.content)
        if final_chars or content_length == delta_chars:
            LOGGER.info(
                "Recorded LensNode run output run_uuid=%s delta_chars=%s "
                "final_chars=%s content_chars=%s",
                run_uuid,
                delta_chars,
                final_chars,
                content_length,
            )
        else:
            LOGGER.debug(
                "Recorded LensNode run output run_uuid=%s delta_chars=%s "
                "content_chars=%s",
                run_uuid,
                delta_chars,
                content_length,
            )

    async def _handle_run_done(self, content):
        run_uuid = self._parse_uuid(content.get("run_uuid"))
        if run_uuid is None:
            await self._send_bad_frame("run_uuid is invalid")
            return
        status = content.get("status") or Run.Status.DONE
        if status not in [
            Run.Status.AWAITING_USER_INPUT,
            Run.Status.DONE,
            Run.Status.FAILED,
        ]:
            status = Run.Status.FAILED
        run = await database_sync_to_async(finish_lensnode_run)(
            run_uuid,
            status,
            error=content.get("error") or "",
            outcome=content.get("outcome") or "",
            termination_detail=content.get("termination_detail") or {},
            final_content=content.get("final_content"),
            citations=content.get("citations"),
            planned_evidence=content.get("planned_evidence"),
        )
        parent_update = await database_sync_to_async(self._delegation_done_payload)(
            run.pk
        )
        if parent_update is not None:
            await self.channel_layer.group_send(
                lensnode_group_name(parent_update.pop("lensnode_uuid")),
                {
                    "type": "lensnode.command",
                    "payload": parent_update,
                },
            )
        LOGGER.info(
            "Finished LensNode run run_uuid=%s status=%s error=%s",
            run_uuid,
            status,
            content.get("error") or "",
        )
        if run.status in [
            Run.Status.DONE,
            Run.Status.FAILED,
            Run.Status.CANCELLED,
            Run.Status.AWAITING_USER_INPUT,
        ]:
            await self.send_json(
                {
                    "type": "run_done_ack",
                    "run_uuid": str(run_uuid),
                }
            )

    @staticmethod
    def _delegation_done_payload(run_pk):
        """Return a parent-node notification for a terminal child Run."""

        run = Run.objects.select_related(
            "output_message",
            "session__assistant",
            "parent_run__lensnode",
        ).get(pk=run_pk)
        if run.parent_run_id is None or run.parent_run.lensnode_id is None:
            return None
        return {
            "type": "delegation_done",
            "run_uuid": str(run.uuid),
            "status": run.status,
            "outcome": run.outcome,
            "assistant_name": run.session.assistant.name,
            "answer": (
                str(run.output_message.content or "") if run.output_message_id else ""
            ),
            "error": str(run.error or "")[:500],
            "lensnode_uuid": str(run.parent_run.lensnode.uuid),
        }

    def _parse_uuid(self, value):
        """Parse a UUID value or return None."""

        try:
            return uuid.UUID(str(value))
        except (TypeError, ValueError):
            return None

    async def _handle_list_dirs_result(self, content):
        """Store list_dirs response in cache for the waiting HTTP handler."""

        request_id = content.get("request_id") or ""
        dirs = content.get("dirs") or {}
        if request_id:
            await database_sync_to_async(self._cache_list_dirs_result)(request_id, dirs)

    @staticmethod
    def _cache_list_dirs_result(request_id, dirs):
        from django.core.cache import cache

        cache.set(f"lens:list_dirs:{request_id}", dirs, timeout=30)

    async def _handle_datasource_path_result(self, content):
        """Store datasource path inspection result for the waiting request."""

        request_id = content.get("request_id") or ""
        if request_id:
            await database_sync_to_async(self._cache_datasource_path_result)(
                request_id,
                content.get("result") or {},
            )

    @staticmethod
    def _cache_datasource_path_result(request_id, result):
        from django.core.cache import cache

        cache.set(f"lens:datasource_path:{request_id}", result, timeout=30)

    async def _handle_datasource_connection_result(self, content):
        """Store datasource connection test result for the waiting request."""

        request_id = content.get("request_id") or ""
        if request_id:
            await database_sync_to_async(self._cache_datasource_connection_result)(
                request_id,
                content.get("result") or {},
            )

    @staticmethod
    def _cache_datasource_connection_result(request_id, result):
        from django.core.cache import cache

        cache.set(
            f"lens:datasource_connection:{request_id}",
            result,
            timeout=30,
        )

    async def _handle_datasource_sync_event(self, content):
        """Record datasource sync progress in TaskExecution metadata."""

        task_id = content.get("task_id") or ""
        if not task_id:
            return
        await database_sync_to_async(self._record_datasource_sync_event)(
            task_id,
            content,
            self.channel_name,
        )

    @staticmethod
    def _record_datasource_sync_event(
        task_id,
        content,
        connection_id=None,
    ):
        from agentcore_task.adapters.django import TaskTracker
        from agentcore_task.adapters.django.models import TaskExecution
        from agentcore_task.constants import TaskStatus

        task = TaskExecution.objects.filter(task_id=task_id).first()
        if task and task.status in TaskStatus.get_completed_statuses():
            return
        metadata = dict(task.metadata or {}) if task else {}
        owner_connection_id = metadata.get("lensnode_connection_id") or ""
        if (
            task
            and connection_id
            and owner_connection_id
            and owner_connection_id != connection_id
        ):
            return
        progress_task_status = TaskStatus.STARTED
        queue_metadata = {}
        if task and content.get("step") == "queue":
            event_status = content.get("status")
            if event_status == "queued":
                progress_task_status = TaskStatus.PENDING
                queue_metadata = {
                    "queue_state": "QUEUED",
                    "execution_class": "exclusive",
                }
            elif event_status == "running":
                queue_metadata = {
                    "queue_state": "STARTED",
                    "execution_class": "exclusive",
                }
        steps = list(metadata.get("steps") or [])
        step = {
            "name": content.get("step") or "sync",
            "status": content.get("status") or "running",
            "message": content.get("message") or "",
            "timestamp": (content.get("timestamp") or timezone.now().isoformat()),
        }
        for key in [
            "category",
            "kind",
            "token",
            "item_type",
            "item_name",
            "file",
            "file_extension",
            "max_workers",
            "error",
            "summary",
            "progress_total",
            "progress_current",
            "progress_percent",
            "phase",
            "overall_progress_percent",
            "phase_progress",
            "progress_counts",
            "substantive_progress",
            "conversion_summary",
            "repository_summaries",
            "failed_repositories",
            "partial_success",
            "current_file",
            "current_status",
            "current_reason",
            "current_stats",
        ]:
            if key in content:
                value = content.get(key)
                if key in {"summary", "conversion_summary"}:
                    value = LensNodeConsumer._compact_step_summary(value)
                step[key] = value
        steps.append(step)
        metadata_update = {
            **queue_metadata,
            "steps": steps,
            "progress_step": step["name"],
            "progress_message": step["message"],
            "last_progress_at": step["timestamp"],
        }
        for key in [
            "phase",
            "overall_progress_percent",
            "phase_progress",
            "progress_counts",
        ]:
            if key in content:
                metadata_update[key] = content.get(key)
        if content.get("substantive_progress"):
            metadata_update["last_substantive_progress_at"] = step[
                "timestamp"
            ]
        is_conversion = (
            content.get("category") == "conversion"
            or str(content.get("step") or "").startswith("conversion")
        )
        if is_conversion:
            summary = LensNodeConsumer._merge_realtime_summary(
                metadata.get("conversion_summary") or {},
                content.get("summary") or {},
            )
            LensNodeConsumer._append_conversion_realtime_detail(
                summary,
                content,
            )
            if summary:
                metadata_update["conversion_summary"] = summary
        else:
            summary = LensNodeConsumer._merge_realtime_summary(
                metadata.get("sync_summary") or {},
                content.get("summary") or {},
            )
            LensNodeConsumer._append_sync_realtime_detail(summary, content)
            if summary:
                metadata_update["sync_summary"] = summary
        if "conversion_summary" in content:
            metadata_update["conversion_summary"] = content.get("conversion_summary")
        for key in ["progress_total", "progress_current", "progress_percent"]:
            if key in content:
                metadata_update[key] = content.get(key)
        updated_task = TaskTracker.update_task_status(
            task_id,
            progress_task_status,
            metadata=metadata_update,
        )
        if (
            queue_metadata
            and updated_task is not None
            and updated_task.metadata.get("type") == "datasource_conversion"
        ):
            datasource_uuid = updated_task.metadata.get("datasource_uuid")
            if datasource_uuid:
                DataSource.objects.filter(uuid=datasource_uuid).update(
                    last_conversion_status=progress_task_status,
                    updated_at=timezone.now(),
                )

    @staticmethod
    def _merge_realtime_summary(current, incoming):
        """Merge realtime summary counts without losing accumulated details."""

        summary = dict(incoming or current or {})
        current_details = (current or {}).get("details") or {}
        current_truncated = (current or {}).get("details_truncated") or {}
        incoming_details = (incoming or {}).get("details") or {}
        incoming_truncated = (incoming or {}).get("details_truncated") or {}
        details = LensNodeConsumer._merge_detail_groups(
            current_details,
            incoming_details,
        )
        truncated = {
            **current_truncated,
            **incoming_truncated,
        }
        if details:
            summary["details"] = details
        if truncated:
            summary["details_truncated"] = truncated
        return summary

    @staticmethod
    def _compact_step_summary(summary):
        """Return a step-safe summary without duplicated detail payloads."""

        if not isinstance(summary, dict):
            return summary
        compact = dict(summary)
        for key in [
            "details",
            "details_truncated",
            "items",
            "items_truncated",
            "changed_items",
            "changed_items_truncated",
            "conversion_summary",
        ]:
            compact.pop(key, None)
        return compact

    @staticmethod
    def _merge_detail_groups(current, incoming):
        """Merge grouped detail lists with de-duplication."""

        details = {}
        for key in set((current or {}).keys()) | set((incoming or {}).keys()):
            merged = []
            for item in list((current or {}).get(key) or []) + list(
                (incoming or {}).get(key) or []
            ):
                LensNodeConsumer._append_detail_item(merged, item)
            if merged:
                details[key] = merged
        return details

    @staticmethod
    def _append_sync_realtime_detail(summary, content):
        """Append one datasource sync item detail to the live summary."""

        step = content.get("step") or ""
        if step not in {"item_done", "item_skipped", "item_failed"}:
            return
        path = content.get("file") or ""
        name = content.get("item_name") or path.rsplit("/", 1)[-1]
        if not path and not name:
            return
        status = {
            "item_done": "synced",
            "item_skipped": "skipped",
            "item_failed": "failed",
        }[step]
        detail = {
            "status": status,
            "path": path,
            "name": name,
            "extension": content.get("file_extension") or "",
            "source_type": "feishu",
            "reason": content.get("error") or "",
        }
        groups = ["scanned"]
        if step == "item_done":
            groups.extend(["changed", "success"])
            if content.get("kind") == "document":
                groups.append("documents")
            else:
                groups.append("files")
        elif step == "item_skipped":
            groups.append("skipped")
        elif step == "item_failed":
            groups.extend(["changed", "failed"])
        LensNodeConsumer._append_summary_detail(summary, groups, detail)

    @staticmethod
    def _append_conversion_realtime_detail(summary, content):
        """Append one conversion item detail to the live summary."""

        path = content.get("current_file") or ""
        status = content.get("current_status") or ""
        if not path or status not in {"converted", "skipped", "failed"}:
            return
        detail = {
            "status": status,
            "path": path,
            "name": path.rsplit("/", 1)[-1],
            "extension": path.rsplit(".", 1)[-1].lower() if "." in path else "",
            "reason": content.get("current_reason") or "",
            "stats": content.get("current_stats") or {},
        }
        groups = ["candidates"]
        if status == "converted":
            groups.extend(["converted", "success", "markdown"])
        elif status == "skipped":
            groups.append("skipped")
        elif status == "failed":
            groups.append("failed")
        if detail["extension"] == "xlsx":
            groups.extend(["xlsx_files", "sheets", "rows"])
        cost = detail["stats"].get("cost") or {}
        if int(cost.get("model_calls") or 0) > 0:
            groups.append("model_calls")
        if int(cost.get("estimated_tokens") or 0) > 0:
            groups.append("estimated_tokens")
        if int(cost.get("total_tokens") or 0) > 0:
            groups.append("total_tokens")
        LensNodeConsumer._append_summary_detail(summary, groups, detail)

    @staticmethod
    def _append_summary_detail(summary, groups, detail):
        """Append one detail item into multiple summary groups."""

        details = dict(summary.get("details") or {})
        truncated = dict(summary.get("details_truncated") or {})
        for group in groups:
            items = list(details.get(group) or [])
            if len(items) >= DETAIL_ITEMS_LIMIT:
                truncated[group] = int(truncated.get(group) or 0) + 1
                continue
            if LensNodeConsumer._append_detail_item(items, detail):
                details[group] = items
        if details:
            summary["details"] = details
        if truncated:
            summary["details_truncated"] = truncated

    @staticmethod
    def _append_detail_item(items, detail):
        """Append one detail item when it is not already present."""

        key = (
            detail.get("status") or "",
            detail.get("path") or "",
            detail.get("name") or "",
            detail.get("reason") or "",
        )
        for item in items:
            existing = (
                item.get("status") or "",
                item.get("path") or "",
                item.get("name") or "",
                item.get("reason") or "",
            )
            if existing == key:
                return False
        items.append(detail)
        return True

    async def _handle_datasource_sync_done(self, content):
        """Complete datasource sync after LensNode reports final status."""

        request_id = content.get("request_id") or ""
        task_id = content.get("task_id") or ""
        if not request_id and not task_id:
            return
        await database_sync_to_async(self._complete_datasource_sync_done)(
            request_id,
            content,
        )

    @staticmethod
    def _complete_datasource_sync_done(request_id, content):
        from django.core.cache import cache

        from .tasks import (
            complete_datasource_sync_task,
            resolve_datasource_sync_task_id,
        )

        if request_id:
            cache.set(
                f"lens:datasource_sync:{request_id}",
                {
                    "status": content.get("status") or "failed",
                    "synced": content.get("synced") or 0,
                    "files": content.get("files") or 0,
                    "folders": content.get("folders") or 0,
                    "failed": content.get("failed") or 0,
                    "scanned": content.get("scanned") or 0,
                    "changed": content.get("changed") or 0,
                    "skipped": content.get("skipped") or 0,
                    "deleted": content.get("deleted") or 0,
                    "documents": content.get("documents") or 0,
                    "by_extension": content.get("by_extension") or {},
                    "by_type": content.get("by_type") or {},
                    "conversion_summary": content.get("conversion_summary") or {},
                    "warnings": content.get("warnings") or [],
                    "repository_summaries": content.get("repository_summaries") or [],
                    "failed_repositories": content.get("failed_repositories") or [],
                    "partial_success": bool(content.get("partial_success")),
                    "target_path": content.get("target_path") or "",
                    "error": content.get("error") or "",
                },
                timeout=60,
            )
        task_id = resolve_datasource_sync_task_id(request_id, content)
        if task_id:
            complete_datasource_sync_task(task_id, content)

    async def _handle_datasource_conversion_done(self, content):
        """Complete managed workspace conversion from LensNode result."""

        request_id = content.get("request_id") or ""
        task_id = content.get("task_id") or ""
        if not request_id and not task_id:
            return
        await database_sync_to_async(self._complete_datasource_conversion_done)(
            request_id, content, self.channel_name
        )
        await self.send_json(
            {
                "type": "datasource_terminal_ack",
                "task_id": task_id,
            }
        )

    async def _handle_datasource_upload_done(self, content):
        """Complete a Managed Workspace upload from LensNode."""

        request_id = content.get("request_id") or ""
        task_id = content.get("task_id") or ""
        if not request_id and not task_id:
            return
        await database_sync_to_async(self._complete_datasource_upload_done)(
            request_id,
            content,
            self.channel_name,
        )
        await self.send_json(
            {
                "type": "datasource_terminal_ack",
                "task_id": task_id,
            }
        )

    @staticmethod
    def _complete_datasource_upload_done(request_id, content, connection_id):
        from .tasks import (
            complete_datasource_conversion_task,
            resolve_datasource_upload_task_id,
        )

        task_id = resolve_datasource_upload_task_id(request_id, content)
        if task_id:
            complete_datasource_conversion_task(
                task_id,
                content,
                connection_id=connection_id,
            )

    @staticmethod
    def _complete_datasource_conversion_done(
        request_id,
        content,
        connection_id,
    ):
        from .tasks import (
            complete_datasource_conversion_task,
            resolve_datasource_conversion_task_id,
        )

        task_id = resolve_datasource_conversion_task_id(
            request_id,
            content,
        )
        if task_id:
            complete_datasource_conversion_task(
                task_id,
                content,
                connection_id=connection_id,
            )

    async def _send_bad_frame(self, message):
        await self.send_json(
            {
                "type": "error",
                "code": "LENSNODE_FRAME_INVALID",
                "message": message,
            }
        )

    async def decode_json(self, text_data):
        """Decode JSON frames with a stable error shape."""

        try:
            return json.loads(text_data)
        except json.JSONDecodeError:
            return {"type": "__invalid_json__"}
