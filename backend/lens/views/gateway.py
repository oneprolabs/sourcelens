"""LensNode-authenticated AI gateway, skill package, and upload views."""

import hashlib
import json
import os
import re
import time
import uuid

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.http import FileResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from agentcore_metering.adapters.django.models import LLMUsage
from lens.citations import public_run_citations, sanitize_run_citations
from lens.document_attachments import (
    document_attachment_storage,
    get_document_attachment,
)
from lens.models import Run, RunExecution, RunOutputFile, Skill
from lens.services import (
    LensNodeDispatchError,
    create_delegated_run,
)
from lens.skill_packages import package_zip_bytes

from .base import EventStreamRenderer, LensNodeAuthMixin


# While a provider call is thinking (reasoning, composing a tool call)
# the SSE stream can carry no tokens for minutes. Periodic heartbeats
# prove transport liveness to the LensNode watchdog so it only aborts
# on a genuinely dead pipe, never on a quiet-but-alive model call.
GATEWAY_STREAM_HEARTBEAT_S = 10
RUN_BUDGET_LOCK_TIMEOUT_S = 120
RUN_BUDGET_WAIT_TIMEOUT_S = 30
OBSERVATION_ID_PATTERN = re.compile(r"^(?!0{16}$)[0-9a-f]{16}$")
GENERATION_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
EMPTY_RESPONSE_FINISH_REASON_PATTERN = re.compile(
    r"finish_reason=(?P<quote>['\"])(?P<reason>[^'\"]+)"
    r"(?P=quote)"
)


class LensNodeAIGatewayView(LensNodeAuthMixin, APIView):
    """AI gateway endpoint authenticated by the LensNode token."""

    authentication_classes = []
    permission_classes = []
    renderer_classes = [JSONRenderer, EventStreamRenderer]

    def post(self, request):
        """Proxy one metered LLM call on behalf of a LensNode."""

        lensnode = self._authenticate_lensnode(request)
        if lensnode is None:
            return Response(
                {"detail": "Invalid LensNode token."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        model_ref = request.data.get("model_ref")
        messages = request.data.get("messages")
        if not model_ref or not isinstance(messages, list):
            return Response(
                {"detail": "model_ref and messages are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from agentcore_metering.adapters.django import LLMTracker

        tracker_state = {
            "source_type": "lensnode_gateway",
            "lensnode_uuid": str(lensnode.uuid),
        }
        correlation = {}
        run_uuid = request.data.get("run_uuid")
        trace_context = request.data.get("trace_context")
        if trace_context is not None and not self._valid_trace_context(
            trace_context,
            run_uuid,
        ):
            return Response(
                {"detail": "Invalid trace_context."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if run_uuid:
            try:
                run = Run.objects.select_related("session").get(
                    uuid=run_uuid
                )
            except (Run.DoesNotExist, ValidationError):
                return Response(
                    {"detail": "Run not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )
            if run.lensnode_id != lensnode.id:
                return Response(
                    {"detail": "Run does not belong to this LensNode."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            tracker_state["user_id"] = run.session.user_id
            correlation["run_uuid"] = str(run.uuid)
            correlation["is_subagent"] = bool(
                request.data.get("is_subagent")
            )
        if correlation:
            tracker_state["metadata"] = correlation
            tracker_state["litellm_metadata"] = {
                "session_id": str(run.session.uuid),
                "trace_user_id": str(run.session.user_id),
                "trace_name": "sourcelens.run",
                "trace_id": run.uuid.hex,
                "existing_trace_id": run.uuid.hex,
                "trace_metadata": correlation.copy(),
            }
            if trace_context is not None:
                tracker_state["litellm_metadata"].update(trace_context)
                tracker_state["otel_traceparent"] = (
                    f"00-{run.uuid.hex}-"
                    f"{trace_context['parent_observation_id']}-01"
                )
        budget_lock = self._acquire_parent_budget_lock(run)
        if run_uuid and budget_lock == "exhausted":
            return Response(
                {
                    "code": "RUN_TOKEN_BUDGET_EXHAUSTED",
                    "detail": "The parent Run token budget has been exhausted.",
                },
                status=status.HTTP_409_CONFLICT,
            )
        if run_uuid and budget_lock is None:
            return Response(
                {
                    "code": "RUN_TOKEN_BUDGET_BUSY",
                    "detail": "Another model call is reserving this Run budget.",
                },
                status=status.HTTP_409_CONFLICT,
            )
        effective_max_tokens = request.data.get("max_tokens")
        if isinstance(budget_lock, tuple) and budget_lock[4] > 0:
            remaining_tokens = budget_lock[4]
            effective_max_tokens = min(
                int(effective_max_tokens or remaining_tokens),
                remaining_tokens,
            )
        if request.data.get("stream"):
            return self._stream_response(
                lensnode,
                model_ref,
                messages,
                tracker_state,
                request.data,
                budget_lock,
                effective_max_tokens,
            )

        try:
            content, usage = LLMTracker.call_and_track(
                messages=messages,
                model_uuid=model_ref,
                node_name=f"lensnode:{lensnode.uuid}",
                state=tracker_state,
                tools=request.data.get("tools"),
                tool_choice=request.data.get("tool_choice"),
                temperature=request.data.get("temperature"),
                max_tokens=effective_max_tokens,
                reasoning_effort=request.data.get("reasoning_effort"),
                return_message=bool(request.data.get("return_message")),
            )
        except ValueError as exc:
            empty_response = self._empty_response_error_payload(exc)
            if empty_response is None:
                raise
            return Response(
                empty_response,
                status=status.HTTP_502_BAD_GATEWAY,
            )
        finally:
            self._release_parent_budget_lock(budget_lock)
        data = {
            "usage": usage,
            "lensnode_uuid": str(lensnode.uuid),
        }
        if request.data.get("return_message"):
            data["message"] = content
            data["content"] = content.get("content", "")
        else:
            data["content"] = content
        return Response(data)

    @staticmethod
    def _acquire_parent_budget_lock(run):
        """Serialize metered calls for one Run tree at its budget boundary."""

        if run is None:
            return None
        root = run
        while root.parent_run_id:
            root = Run.objects.only("uuid", "parent_run_id").get(
                pk=root.parent_run_id
            )
        limit = int(
            RunExecution.objects.only("token_budget_max_tokens").get(
                run_id=root.pk
            ).token_budget_max_tokens
            or 0
        )
        if not limit:
            return "disabled"
        token = uuid.uuid4().hex
        key = f"lens:run-budget-lock:{root.uuid}"
        deadline = time.monotonic() + RUN_BUDGET_WAIT_TIMEOUT_S
        while time.monotonic() < deadline:
            if cache.add(key, token, RUN_BUDGET_LOCK_TIMEOUT_S):
                usage_ids = [str(root.uuid)]
                frontier = [root.pk]
                while frontier:
                    children = list(
                        Run.objects.filter(parent_run_id__in=frontier).values_list(
                            "pk", "uuid"
                        )
                    )
                    frontier = [pk for pk, _uuid in children]
                    usage_ids.extend(
                        str(run_uuid) for _pk, run_uuid in children
                    )
                used = LLMUsage.objects.filter(
                    metadata__run_uuid__in=usage_ids,
                ).aggregate(total=Sum("total_tokens"))["total"] or 0
                if int(used) >= limit:
                    cache.delete(key)
                    return "exhausted"
                return key, token, root.uuid, limit, limit - int(used)
            time.sleep(0.05)
        return None

    @staticmethod
    def _release_parent_budget_lock(lock):
        """Release a budget lock only when it is still owned by this call."""

        if lock is None or lock == "disabled":
            return
        key, token, _root_uuid, _limit, _remaining = lock
        if cache.get(key) == token:
            cache.delete(key)

    @staticmethod
    def _empty_response_error_payload(error):
        """Return a bounded error contract for an empty model response."""

        message = str(error)
        if not message.startswith("LLM returned empty response ("):
            return None
        finish_reason = EMPTY_RESPONSE_FINISH_REASON_PATTERN.search(message)
        if finish_reason is None:
            return None
        return {
            "code": "MODEL_EMPTY_RESPONSE",
            "finish_reason": finish_reason.group("reason"),
            "has_reasoning_content": "has_reasoning_content=True" in message,
        }

    @staticmethod
    def _valid_trace_context(trace_context, run_uuid):
        """Validate the bounded LensNode-to-Gateway trace contract."""

        if not run_uuid or not isinstance(trace_context, dict):
            return False
        if set(trace_context) != {
            "parent_observation_id",
            "generation_name",
        }:
            return False
        parent_id = trace_context.get("parent_observation_id")
        generation_name = trace_context.get("generation_name")
        return bool(
            isinstance(parent_id, str)
            and OBSERVATION_ID_PATTERN.fullmatch(parent_id)
            and isinstance(generation_name, str)
            and GENERATION_NAME_PATTERN.fullmatch(generation_name)
        )

    def _stream_response(
        self,
        lensnode,
        model_ref,
        messages,
        tracker_state,
        payload,
        budget_lock=None,
        effective_max_tokens=None,
    ):
        """Stream a metered LLM call as SSE chunks.

        Runs the sync LLMTracker generator in a thread and yields events via
        an async generator so Daphne (ASGI) flushes each token immediately
        instead of buffering the full response.
        """

        from agentcore_metering.adapters.django import LLMTracker
        import asyncio
        import logging as _logging

        _log = _logging.getLogger("lens.gateway_stream")
        lensnode_uuid_str = str(lensnode.uuid)

        async def event_stream():
            loop = asyncio.get_running_loop()
            queue = asyncio.Queue()

            def run_in_thread():
                try:
                    generator = LLMTracker.call_and_track(
                        messages=messages,
                        model_uuid=model_ref,
                        node_name=f"lensnode:{lensnode_uuid_str}",
                        state=tracker_state,
                        stream=True,
                        tools=payload.get("tools"),
                        tool_choice=payload.get("tool_choice"),
                        temperature=payload.get("temperature"),
                        max_tokens=effective_max_tokens,
                        reasoning_effort=payload.get("reasoning_effort"),
                    )
                    token_count = 0
                    while True:
                        try:
                            kind, text = next(generator)
                        except StopIteration as exc:
                            result = exc.value or {}
                            tool_calls = result.pop("_tool_calls", None) or []
                            finish_reason = result.pop(
                                "_finish_reason", None
                            )
                            _log.debug(
                                "gateway stream done: token_count=%d "
                                "tool_calls=%d",
                                token_count,
                                len(tool_calls),
                            )
                            loop.call_soon_threadsafe(
                                queue.put_nowait,
                                ("done", {
                                    "type": "done",
                                    "usage": result,
                                    "lensnode_uuid": lensnode_uuid_str,
                                    "tool_calls": tool_calls,
                                    "finish_reason": finish_reason,
                                }),
                            )
                            return
                        except Exception as exc:
                            error_code = self._gateway_stream_error_code(exc)
                            _log.error(
                                "gateway stream exception: type=%s error=%s "
                                "token_count=%d",
                                type(exc).__name__,
                                exc,
                                token_count,
                            )
                            loop.call_soon_threadsafe(
                                queue.put_nowait,
                                (
                                    "event",
                                    {
                                        "type": "error",
                                        "error": {
                                            "code": error_code,
                                            "message": str(exc),
                                        },
                                    },
                                ),
                            )
                            return
                        token_count += 1
                        loop.call_soon_threadsafe(
                            queue.put_nowait,
                            ("event", {
                                "type": "token",
                                "kind": kind,
                                "content": text,
                            }),
                        )
                except Exception as exc:
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        (
                            "event",
                            {
                                "type": "error",
                                "error": {
                                    "code": (
                                        self._gateway_stream_error_code(
                                            exc
                                        )
                                    ),
                                    "message": str(exc),
                                },
                            },
                        ),
                    )

            future = loop.run_in_executor(None, run_in_thread)
            try:
                while True:
                    try:
                        item = await asyncio.wait_for(
                            queue.get(), timeout=GATEWAY_STREAM_HEARTBEAT_S
                        )
                    except asyncio.TimeoutError:
                        yield self._sse({"type": "heartbeat"})
                        continue
                    if item[0] == "event":
                        yield self._sse(item[1])
                        if item[1].get("type") == "error":
                            return
                    elif item[0] == "done":
                        yield self._sse(item[1])
                        return
                    elif item[0] == "error":
                        raise item[1]
            finally:
                await future
                self._release_parent_budget_lock(budget_lock)

        response = StreamingHttpResponse(
            event_stream(),
            content_type="text/event-stream",
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response

    def _sse(self, event):
        """Serialize one SSE event."""

        return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    def _gateway_stream_error_code(self, exc):
        """Return a stable error code for a gateway stream exception."""

        name = type(exc).__name__.upper()
        message = str(exc).upper()
        if "TIMEOUT" in name or "TIMEOUT" in message or "TIMED OUT" in message:
            return "MODEL_TIMEOUT"
        stream_markers = [
            "CHUNKED",
            "INCOMPLETE",
            "PEER CLOSED",
            "REMOTE PROTOCOL",
            "CONNECTION RESET",
            "CONNECTION CLOSED",
        ]
        if any(
            marker in name or marker in message
            for marker in stream_markers
        ):
            return "MODEL_STREAM_ERROR"
        return "MODEL_STREAM_ERROR"


class LensNodeDelegationView(LensNodeAuthMixin, APIView):
    """Create and inspect cross-node Smart Collaboration child Runs."""

    authentication_classes = []
    permission_classes = []

    def post(self, request, run_uuid):
        """Create an idempotent delegated Run for an allowed assistant."""

        lensnode = self._authenticate_lensnode(request)
        if lensnode is None:
            return Response(
                {"detail": "Invalid LensNode token."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        parent = Run.objects.select_related("session").filter(
            uuid=run_uuid,
            lensnode=lensnode,
        ).first()
        if parent is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        assistant_uuid = request.data.get("assistant_uuid")
        question = str(request.data.get("question") or "").strip()
        delegation_key = str(
            request.data.get("delegation_key") or ""
        ).strip()
        delegation_group_key = str(
            request.data.get("delegation_group_key") or delegation_key
        ).strip()
        if not assistant_uuid or not question or not delegation_key:
            return Response(
                {
                    "detail": (
                        "assistant_uuid, question, and delegation_key "
                        "are required."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(delegation_key) > 96:
            return Response(
                {"detail": "delegation_key is too long."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(delegation_group_key) > 96:
            return Response(
                {"detail": "delegation_group_key is too long."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            child = create_delegated_run(
                parent,
                assistant_uuid,
                question,
                delegation_key=delegation_key,
                delegation_group_key=delegation_group_key,
            )
        except (LensNodeDispatchError, ValidationError) as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            self._run_payload(child),
            status=status.HTTP_202_ACCEPTED,
        )

    def get(self, request, run_uuid, child_uuid):
        """Return the current state and final answer of one child Run."""

        lensnode = self._authenticate_lensnode(request)
        if lensnode is None:
            return Response(
                {"detail": "Invalid LensNode token."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        child = Run.objects.select_related(
            "output_message",
            "session__assistant",
            "lensnode",
            "parent_run",
        ).filter(
            uuid=child_uuid,
            parent_run__uuid=run_uuid,
            parent_run__lensnode=lensnode,
        ).first()
        if child is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(self._run_payload(child))

    @staticmethod
    def _run_payload(run):
        """Return the bounded child-Run contract consumed by LensNode."""

        return {
            "run_uuid": str(run.uuid),
            "status": run.status,
            "outcome": run.outcome,
            "assistant_name": run.session.assistant.name,
            "lensnode_uuid": (
                str(run.lensnode.uuid) if run.lensnode_id else ""
            ),
            "answer": (
                str(run.output_message.content or "")
                if run.output_message_id
                else ""
            ),
            "citations": public_run_citations(run.citations),
            "error": str(run.error or "")[:500],
        }


class LensNodeSkillPackageView(LensNodeAuthMixin, APIView):
    """Skill package endpoint authenticated by the LensNode token."""

    authentication_classes = []
    permission_classes = []

    def get(self, request, uuid):
        """Return a packaged Skill archive for LensNode cache fill."""

        lensnode = self._authenticate_lensnode(request)
        if lensnode is None:
            return Response(
                {"detail": "Invalid LensNode token."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        skill = get_object_or_404(Skill, uuid=uuid, enabled=True)
        package_hash = request.query_params.get("hash") or ""
        if package_hash and package_hash != skill.package_hash:
            return Response(
                {"detail": "Skill package hash mismatch."},
                status=status.HTTP_404_NOT_FOUND,
            )
        archive = package_zip_bytes(skill)
        response = FileResponse(
            archive,
            as_attachment=True,
            filename=f"{skill.uuid}.zip",
            content_type="application/zip",
        )
        response["X-Skill-Package-Hash"] = skill.package_hash
        return response


class LensNodeRunAttachmentView(LensNodeAuthMixin, APIView):
    """Serve one Run-bound document to its assigned LensNode."""

    authentication_classes = []
    permission_classes = []

    def get(self, request, run_uuid, uuid):
        """Return document bytes after node, Run, and attachment checks."""

        lensnode = self._authenticate_lensnode(request)
        if lensnode is None:
            return Response(
                {"detail": "Invalid LensNode token."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        if not Run.objects.filter(
            uuid=run_uuid,
            lensnode=lensnode,
        ).exists():
            return Response(status=status.HTTP_404_NOT_FOUND)
        attachment = get_document_attachment(uuid)
        if attachment is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if attachment.get("run_uuid") != str(run_uuid):
            return Response(status=status.HTTP_404_NOT_FOUND)
        if attachment.get("lensnode_uuid") != str(lensnode.uuid):
            return Response(status=status.HTTP_404_NOT_FOUND)
        storage = document_attachment_storage()
        if not storage.exists(attachment["storage_name"]):
            return Response(status=status.HTTP_404_NOT_FOUND)
        response = FileResponse(
            storage.open(attachment["storage_name"], "rb"),
            as_attachment=True,
            filename=attachment["original_name"] or "document",
            content_type=(
                attachment["mime_type"] or "application/octet-stream"
            ),
        )
        response["Content-Length"] = attachment["byte_size"]
        response["X-Attachment-Hash"] = attachment["content_hash"]
        return response


class LensNodeHistoryArtifactView(LensNodeAuthMixin, APIView):
    """Serve one trusted prior deliverable to the assigned LensNode."""

    authentication_classes = []
    permission_classes = []

    def get(self, request, run_uuid, uuid):
        """Return bytes only within the current Run's conversation."""

        lensnode = self._authenticate_lensnode(request)
        if lensnode is None:
            return Response(
                {"detail": "Invalid LensNode token."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        current_run = Run.objects.select_related(
            "input_message",
            "session",
        ).filter(uuid=run_uuid, lensnode=lensnode).first()
        if current_run is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        output = (
            RunOutputFile.objects.select_related(
                "run",
                "run__input_message",
            )
            .filter(
                uuid=uuid,
                session_id=current_run.session_id,
                assistant_id=current_run.session.assistant_id,
                run__status=Run.Status.DONE,
                run__outcome__in=["", Run.Outcome.COMPLETED],
                run__input_message__sequence__lt=(
                    current_run.input_message.sequence
                ),
            )
            .first()
        )
        if (
            output is None
            or not output.file
            or len(output.content_hash or "") != 64
            or any(
                character not in "0123456789abcdef"
                for character in (output.content_hash or "").lower()
            )
            or output.byte_size > settings.DELIVERABLE_MAX_BYTES
        ):
            return Response(status=status.HTTP_404_NOT_FOUND)
        response = FileResponse(
            output.file.open("rb"),
            as_attachment=True,
            filename=output.filename or "artifact",
            content_type=output.content_type or "application/octet-stream",
        )
        response["Content-Length"] = output.byte_size
        response["X-Attachment-Hash"] = output.content_hash
        return response


class LensNodeDeliverableUploadView(LensNodeAuthMixin, APIView):
    """Receive a run deliverable file produced by a LensNode.

    The node POSTs the file at produce time (same direction and token
    auth as the AI gateway), so the bytes live on control-plane storage
    and the node's volume stays private. The file is saved to the
    'deliverables' storage and recorded as a RunOutputFile linked to the
    run's answer message so the frontend can offer it for download.
    """

    authentication_classes = []
    permission_classes = []
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        """Store an uploaded deliverable and record it against the run."""

        lensnode = self._authenticate_lensnode(request)
        if lensnode is None:
            return Response(
                {"detail": "Invalid LensNode token."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        run_uuid = request.data.get("run_uuid")
        upload = request.FILES.get("file")
        if not run_uuid or upload is None:
            return Response(
                {"detail": "run_uuid and file are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if upload.size > settings.DELIVERABLE_MAX_BYTES:
            return Response(
                {
                    "detail": (
                        "Deliverable exceeds the "
                        f"{settings.DELIVERABLE_MAX_BYTES}-byte limit."
                    )
                },
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        try:
            run = Run.objects.select_related(
                "output_message", "session", "session__assistant"
            ).get(uuid=run_uuid)
        except Run.DoesNotExist:
            return Response(
                {"detail": "Run not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if run.lensnode_id != lensnode.id:
            return Response(
                {"detail": "Run does not belong to this LensNode."},
                status=status.HTTP_403_FORBIDDEN,
            )

        digest = hashlib.sha256()
        for chunk in upload.chunks():
            digest.update(chunk)
        upload.seek(0)

        filename = request.data.get("filename") or upload.name or "file"
        # Strip any path components so a node cannot influence the stored
        # path beyond the <assistant>/<session>/ prefix built by upload_to.
        filename = os.path.basename(filename)[:255] or "file"
        content_type = (
            request.data.get("content_type")
            or getattr(upload, "content_type", "")
            or ""
        )
        content_type = content_type[:120]

        output = RunOutputFile(
            run=run,
            message=run.output_message,
            session=run.session,
            assistant=run.session.assistant,
            filename=filename,
            content_type=content_type,
            byte_size=upload.size,
            content_hash=digest.hexdigest(),
        )
        # upload_to builds <assistant>/<session>/<filename> in the
        # 'deliverables' storage; save=False defers the row write.
        output.file.save(filename, upload, save=False)
        try:
            output.save()
        except Exception:
            # Roll back the just-written bytes so a failed row does not
            # leave orphaned files the purge signal can never reach.
            output.file.delete(save=False)
            raise
        return Response(
            {
                "uuid": str(output.uuid),
                "filename": output.filename,
                "byte_size": output.byte_size,
            },
            status=status.HTTP_201_CREATED,
        )
