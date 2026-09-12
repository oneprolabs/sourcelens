"""LensNode enrollment, token, and datasource-path viewset."""

from pathlib import PurePosixPath

from django.db.models import Count, Max, Q
from django.db.models.functions import Now
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from lens.datasource.services import (
    DataSourceDispatchError,
    DataSourcePathError,
    check_datasource_path,
    normalize_workspace_target_path,
    test_datasource_connection,
)
from lens.lensnode_auth import issue_lensnode_token
from lens.models import DataSource, LensNode, Run, RunTraceEvent
from lens.serializers import LensNodeSerializer
from .base import BaseAdminViewSet


class LensNodeViewSet(BaseAdminViewSet):
    """CRUD and enrollment actions for LensNode workers."""

    queryset = LensNode.objects.annotate(
        active_run_count=Count(
            "runs",
            filter=Q(
                runs__status__in=[
                    Run.Status.RUNNING,
                    Run.Status.STREAMING,
                ]
            ),
            distinct=True,
        ),
        queued_run_count=Count(
            "runs",
            filter=Q(runs__status=Run.Status.QUEUED),
            distinct=True,
        ),
        awaiting_resume_count=Count(
            "runs",
            filter=Q(
                runs__status__in=[
                    Run.Status.RUNNING,
                    Run.Status.STREAMING,
                ],
                runs__resume_by__gt=Now(),
            ),
            distinct=True,
        ),
        total_run_count=Count("runs", distinct=True),
        succeeded_run_count=Count(
            "runs",
            filter=Q(runs__status=Run.Status.DONE),
            distinct=True,
        ),
        failed_run_count=Count(
            "runs",
            filter=Q(runs__status=Run.Status.FAILED),
            distinct=True,
        ),
        last_run_at=Max("runs__created_at"),
    ).order_by("name")
    serializer_class = LensNodeSerializer

    def list(self, request, *args, **kwargs):
        """Return paginated nodes with fleet-wide health and workload."""

        response = super().list(request, *args, **kwargs)
        node_ids = [
            row.get("uuid") for row in response.data.get("results", [])
        ]
        token_totals = {}
        events = RunTraceEvent.objects.filter(
            run__lensnode__uuid__in=node_ids,
            event_type="model.completed",
        ).values("run__lensnode__uuid", "payload")
        for event in events:
            usage = (event.get("payload") or {}).get("usage") or {}
            try:
                tokens = max(int(usage.get("total_tokens") or 0), 0)
            except (TypeError, ValueError):
                tokens = 0
            node_id = str(event["run__lensnode__uuid"])
            token_totals[node_id] = token_totals.get(node_id, 0) + tokens
        for row in response.data.get("results", []):
            row["total_tokens"] = token_totals.get(str(row["uuid"]), 0)
        node_summary = LensNode.objects.aggregate(
            total=Count("pk"),
            online=Count("pk", filter=Q(status=LensNode.Status.ONLINE)),
            offline=Count("pk", filter=Q(status=LensNode.Status.OFFLINE)),
            draining=Count("pk", filter=Q(status=LensNode.Status.DRAINING)),
        )
        run_summary = Run.objects.filter(lensnode__isnull=False).aggregate(
            active_runs=Count(
                "pk",
                filter=Q(
                    status__in=[
                        Run.Status.RUNNING,
                        Run.Status.STREAMING,
                    ]
                ),
            ),
            queued_runs=Count(
                "pk",
                filter=Q(status=Run.Status.QUEUED),
            ),
            awaiting_resume=Count(
                "pk",
                filter=Q(
                    status__in=[Run.Status.RUNNING, Run.Status.STREAMING],
                    resume_by__gt=Now(),
                ),
            ),
        )
        if isinstance(response.data, dict):
            response.data["fleet_summary"] = {
                **node_summary,
                **run_summary,
            }
        return response

    def create(self, request, *args, **kwargs):
        """Onboard a node: create record, auto-approve, issue token once.

        Nodes are deployed remotely and connect back over WebSocket with
        the issued token, so the only meaningful input here is the name.
        The plaintext token is returned a single time for the compose file.
        """

        name = (request.data.get("name") or "").strip()
        if not name:
            return Response(
                {"detail": "Name is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        lensnode = LensNode.objects.create(
            name=name,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
        )
        token = issue_lensnode_token(lensnode)
        data = LensNodeSerializer(lensnode).data
        data["token"] = token
        return Response(data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def approve(self, request, uuid=None):
        """Approve a pending LensNode for token-based access."""

        lensnode = self.get_object()
        lensnode.enrollment_status = LensNode.EnrollmentStatus.APPROVED
        lensnode.save(update_fields=["enrollment_status", "updated_at"])
        return Response(LensNodeSerializer(lensnode).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, uuid=None):
        """Reject a LensNode enrollment request."""

        lensnode = self.get_object()
        lensnode.enrollment_status = LensNode.EnrollmentStatus.REJECTED
        lensnode.token_revoked = True
        lensnode.save(
            update_fields=[
                "enrollment_status",
                "token_revoked",
                "updated_at",
            ]
        )
        return Response(LensNodeSerializer(lensnode).data)

    @action(detail=True, methods=["post"], url_path="issue-token")
    def issue_token(self, request, uuid=None):
        """Issue a plaintext LensNode token once."""

        lensnode = self.get_object()
        if lensnode.enrollment_status != LensNode.EnrollmentStatus.APPROVED:
            return Response(
                {
                    "detail": (
                        "LensNode must be approved before issuing a token."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        token = issue_lensnode_token(lensnode)
        return Response(
            {
                "lensnode_uuid": str(lensnode.uuid),
                "token": token,
                "token_issued_at": lensnode.token_issued_at,
            }
        )

    @action(detail=True, methods=["post"], url_path="revoke-token")
    def revoke_token(self, request, uuid=None):
        """Revoke the current LensNode token."""

        lensnode = self.get_object()
        lensnode.token_revoked = True
        lensnode.status = LensNode.Status.OFFLINE
        lensnode.connection_id = ""
        lensnode.save(
            update_fields=[
                "token_revoked",
                "status",
                "connection_id",
                "updated_at",
            ]
        )
        return Response(LensNodeSerializer(lensnode).data)

    @action(detail=True, methods=["post"], url_path="list-dirs")
    def list_dirs(self, request, uuid=None):
        """Ask a connected LensNode to list immediate subdirectories."""

        import time
        import uuid as uuid_mod
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer
        from django.core.cache import cache
        from lens.services import lensnode_group_name

        lensnode = self.get_object()
        if lensnode.status != LensNode.Status.ONLINE:
            return Response(
                {"error": "LensNode is offline"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        paths = request.data.get("paths") or []
        if not paths:
            return Response({"dirs": {}})

        request_id = uuid_mod.uuid4().hex
        channel_layer = get_channel_layer()
        if channel_layer is None:
            return Response(
                {"error": "Channel layer not configured"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        async_to_sync(channel_layer.group_send)(
            lensnode_group_name(lensnode.uuid),
            {
                "type": "lensnode_command",
                "payload": {
                    "type": "list_dirs",
                    "request_id": request_id,
                    "paths": paths,
                },
            },
        )

        cache_key = f"lens:list_dirs:{request_id}"
        for _ in range(30):
            result = cache.get(cache_key)
            if result is not None:
                cache.delete(cache_key)
                return Response({"dirs": result})
            time.sleep(0.2)

        return Response(
            {"error": "timeout"},
            status=status.HTTP_408_REQUEST_TIMEOUT,
        )

    @action(detail=True, methods=["post"], url_path="check-datasource-path")
    def check_datasource_path(self, request, uuid=None):
        """Ask a connected LensNode to inspect a datasource target path."""

        lensnode = self.get_object()
        try:
            target_path = normalize_workspace_target_path(
                request.data.get("target_path") or "",
                lensnode.workspace_path,
            )
            conflict = _datasource_target_path_conflict(
                lensnode,
                target_path,
                request.data.get("datasource_uuid") or None,
                request.data.get("source_type") or DataSource.SourceType.GIT,
            )
            if conflict is not None:
                return Response(
                    {
                        "status": "blocked",
                        "message_code": "datasource_path_in_use",
                        "message": (
                            "Another datasource already uses this target path"
                        ),
                        "datasource_uuid": str(conflict.uuid),
                        "datasource_name": conflict.name,
                    }
                )
            result = check_datasource_path(
                lensnode,
                target_path,
                request.data.get("source_type") or DataSource.SourceType.GIT,
                config=request.data.get("config") or {},
            )
        except DataSourcePathError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except DataSourceDispatchError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(result)

    @action(
        detail=True,
        methods=["post"],
        url_path="test-datasource-connection",
    )
    def test_datasource_connection(self, request, uuid=None):
        """Ask a connected LensNode to test datasource connection settings."""

        lensnode = self.get_object()
        try:
            result = test_datasource_connection(
                lensnode,
                request.data.get("source_type") or DataSource.SourceType.GIT,
                config=request.data.get("config") or {},
                datasource_uuid=request.data.get("datasource_uuid") or None,
                credential_uuid=request.data.get("credential_uuid") or None,
            )
        except DataSourceDispatchError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(result)


def _datasource_target_path_conflict(
    lensnode,
    target_path,
    datasource_uuid,
    source_type,
):
    """Return another datasource using the same target path on this
    LensNode."""

    query = DataSource.objects.filter(lensnode=lensnode)
    if datasource_uuid:
        query = query.exclude(uuid=datasource_uuid)
    normalized_target = normalize_workspace_target_path(
        target_path,
        lensnode.workspace_path,
    )
    target = PurePosixPath(normalized_target)
    for datasource in query.only(
        "uuid",
        "name",
        "source_type",
        "target_path",
    ):
        if not datasource.target_path:
            continue
        try:
            existing = PurePosixPath(
                normalize_workspace_target_path(
                    datasource.target_path,
                    lensnode.workspace_path,
                )
            )
        except DataSourcePathError:
            continue
        managed_overlap = (
            source_type == DataSource.SourceType.MANAGED_WORKSPACE
            or datasource.source_type
            == DataSource.SourceType.MANAGED_WORKSPACE
        )
        if existing == target or (
            managed_overlap and _datasource_paths_overlap(existing, target)
        ):
            return datasource
    return None


def _datasource_paths_overlap(first, second):
    """Return whether either datasource path contains the other."""

    try:
        first.relative_to(second)
        return True
    except ValueError:
        pass
    try:
        second.relative_to(first)
        return True
    except ValueError:
        return False
