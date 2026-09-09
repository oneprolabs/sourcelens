"""Datasource CRUD, search, and synchronization views."""

import json
import os
import uuid as uuid_mod

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from lens.datasource_services import (
    DATASOURCE_UPLOAD_EXTENSIONS,
    DataSourceDispatchError,
    DataSourcePathError,
    check_datasource_path,
    list_datasource_files,
)
from lens.models import (
    CredentialLease,
    DataSource,
    ExecutionSnapshot,
    PluginInvocation,
    ScheduledTask,
)
from lens.plugins.datasource_access import (
    datasource_access_failure_detail,
    validate_connection_datasource_access,
)
from lens.plugins.http import PluginHttpClientError
from lens.plugins.providers import (
    DatasourceProviderError,
    get_datasource_provider,
)
from lens.periodic_tasks import ensure_datasource_periodic_task
from lens.serializers import (
    DataSourceConversionRequestSerializer,
    DataSourceSerializer,
)
from lens.services import (
    cancel_datasource_conversion_on_lensnode,
    cancel_datasource_sync_on_lensnode,
)
from lens.tasks import (
    DATASOURCE_CANCELLING_STATUS,
    datasource_conversion_task,
    datasource_upload_task,
    register_datasource_conversion_task,
    register_datasource_sync_task,
    register_datasource_upload_task,
    source_sync_task,
)
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from .base import BaseAdminViewSet


class DataSourceViewSet(BaseAdminViewSet):
    """CRUD for datasources."""

    queryset = DataSource.objects.all()
    serializer_class = DataSourceSerializer
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_queryset(self):
        """Return datasources filtered by optional search query."""

        queryset = (
            super()
            .get_queryset()
            .select_related(
                "connection",
                "lensnode",
                "credential",
            )
        )
        filters = self._datasource_search_filters(
            self.request.query_params.get("filters")
        )
        plugin_key = str(
            self.request.query_params.get("plugin_key") or ""
        ).strip()
        if plugin_key and plugin_key.lower() != "all":
            queryset = queryset.filter(plugin_key=plugin_key)
        for item in filters:
            queryset = queryset.filter(
                self._datasource_search_query(
                    item.get("value", ""),
                    item.get("key", ""),
                )
            )
        if filters:
            return queryset
        search = str(self.request.query_params.get("search") or "").strip()
        if not search:
            return queryset
        search_key = str(self.request.query_params.get("search_key") or "").strip()
        return queryset.filter(self._datasource_search_query(search, search_key))

    @staticmethod
    def _datasource_search_filters(value):
        """Parse datasource search filters from query params."""

        if not value:
            return []
        try:
            raw_filters = json.loads(value)
        except (TypeError, ValueError):
            return []
        if not isinstance(raw_filters, list):
            return []
        filters = []
        for item in raw_filters:
            if not isinstance(item, dict):
                continue
            keyword = str(item.get("value") or "").strip()
            if not keyword:
                continue
            filters.append(
                {
                    "key": str(item.get("key") or "").strip(),
                    "value": keyword,
                }
            )
        return filters

    @staticmethod
    def _datasource_search_query(search, search_key):
        """Build a whitelisted datasource search query."""

        sync_status_datasource_ids = ScheduledTask.objects.filter(
            task_type=ScheduledTask.TaskType.SOURCE_SYNC,
            target_type="datasource",
            last_status__icontains=search,
        ).values("target_id")
        queries = {
            "name": Q(name__icontains=search),
            "source_type": Q(source_type__icontains=search),
            "repository": (
                Q(config__repo_url__icontains=search)
                | Q(config__document_url__icontains=search)
                | Q(config__folder_url__icontains=search)
                | Q(config__app_token__icontains=search)
            ),
            "lensnode": Q(lensnode__name__icontains=search),
            "target_path": Q(target_path__icontains=search),
            "status": (
                Q(status__icontains=search)
                | Q(last_error__icontains=search)
                | Q(uuid__in=sync_status_datasource_ids)
            ),
            "sync_policy": (
                Q(sync_policy__mode__icontains=search)
                | Q(sync_policy__cron__icontains=search)
                | Q(sync_policy__timezone__icontains=search)
            ),
        }
        if search_key in queries:
            return queries[search_key]
        query = Q()
        for item in queries.values():
            query |= item
        return query

    def create(self, request, *args, **kwargs):
        """Create datasource and return the initial sync task id."""

        self._initial_sync_task_id = ""
        response = super().create(request, *args, **kwargs)
        task_id = getattr(self, "_initial_sync_task_id", "")
        if task_id and isinstance(response.data, dict):
            response.data["initial_sync_task_id"] = task_id
        return response

    def destroy(self, request, *args, **kwargs):
        """Reject deletion while a datasource sync is still active."""

        from agentcore_task.adapters.django.models import TaskExecution
        from agentcore_task.constants import TaskStatus

        datasource = self.get_object()
        with transaction.atomic():
            datasource = DataSource.objects.select_for_update().get(
                pk=datasource.pk
            )
            active_statuses = [
                TaskStatus.PENDING,
                *TaskStatus.get_running_statuses(),
                DATASOURCE_CANCELLING_STATUS,
            ]
            active_sync = TaskExecution.objects.filter(
                module="lens_datasource",
                metadata__datasource_uuid=str(datasource.uuid),
                status__in=active_statuses,
            ).exists()
            if active_sync:
                return Response(
                    {"detail": "DATASOURCE_SYNC_IN_PROGRESS"},
                    status=status.HTTP_409_CONFLICT,
                )
            return super().destroy(request, *args, **kwargs)

    def perform_destroy(self, instance):
        """Delete datasource audit records before the catalog row."""

        from django_celery_beat.models import PeriodicTask, PeriodicTasks

        with transaction.atomic():
            snapshot_ids = list(
                ExecutionSnapshot.objects.filter(
                    datasource=instance,
                ).values_list("id", flat=True)
            )
            if snapshot_ids:
                CredentialLease.objects.filter(
                    snapshot_id__in=snapshot_ids,
                ).delete()
                PluginInvocation.objects.filter(
                    snapshot_id__in=snapshot_ids,
                ).delete()
                ExecutionSnapshot.objects.filter(
                    id__in=snapshot_ids,
                ).delete()
            PluginInvocation.objects.filter(datasource=instance).delete()

            periodic_name = f"lens-source-sync-{instance.uuid}"
            periodic_deleted, _ = PeriodicTask.objects.filter(
                name=periodic_name,
            ).delete()
            ScheduledTask.objects.filter(
                task_type=ScheduledTask.TaskType.SOURCE_SYNC,
                target_type="datasource",
                target_id=instance.uuid,
            ).delete()
            if periodic_deleted:
                PeriodicTasks.update_changed()

            instance.delete()

    def perform_create(self, serializer):
        """Create datasource, register schedule, and enqueue initial sync."""

        self._validate_plugin_datasource_access(serializer)
        datasource = serializer.save()
        ensure_datasource_periodic_task(datasource)
        if datasource.source_type == DataSource.SourceType.MANAGED_WORKSPACE:
            return
        if datasource.status == DataSource.Status.DISABLED:
            return
        task_id = uuid_mod.uuid4().hex
        self._initial_sync_task_id = task_id
        user = self.request.user
        transaction.on_commit(
            lambda: self._enqueue_datasource_sync(
                datasource,
                task_id,
                "initial",
                user,
            )
        )

    def perform_update(self, serializer):
        """Update datasource and register its sync schedule."""

        self._validate_plugin_datasource_access(serializer)
        datasource = serializer.save()
        ensure_datasource_periodic_task(datasource)

    @staticmethod
    def _validate_plugin_datasource_access(serializer):
        """Reject Plugin datasource writes with unreadable resources."""

        connection = serializer.validated_data.get(
            "connection",
            getattr(serializer.instance, "connection", None),
        )
        if connection is None:
            return
        provider_required = getattr(
            get_datasource_provider(connection.plugin_key),
            "requires_datasource_access_validation",
            False,
        )
        if not provider_required:
            return
        try:
            result = validate_connection_datasource_access(
                connection,
                serializer.validated_data.get(
                    "datasource_config",
                    getattr(
                        serializer.instance,
                        "datasource_config",
                        None,
                    ),
                ),
            )
        except (DatasourceProviderError, PluginHttpClientError) as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        if not result.get("valid"):
            raise ValidationError(
                {
                    "detail": datasource_access_failure_detail(result),
                    "resources": result.get("resources") or [],
                }
            )

    @staticmethod
    def _enqueue_datasource_sync(datasource, task_id, trigger, user=None):
        """Register and enqueue one datasource sync task."""

        if datasource.source_type == DataSource.SourceType.MANAGED_WORKSPACE:
            raise ValueError("DATASOURCE_SYNC_NOT_SUPPORTED")
        if datasource.status == DataSource.Status.DISABLED:
            raise ValueError("DATASOURCE_DISABLED")
        celery_task_id = uuid_mod.uuid4().hex
        task_execution = register_datasource_sync_task(
            datasource,
            task_id,
            trigger,
            created_by=user,
        )
        metadata = dict(task_execution.metadata or {})
        metadata["celery_task_id"] = celery_task_id
        task_execution.metadata = metadata
        task_execution.save(update_fields=["metadata"])
        source_sync_task.apply_async(
            args=[str(datasource.uuid), trigger, task_id],
            task_id=celery_task_id,
        )
        return task_execution

    @action(detail=True, methods=["post"])
    def sync(self, request, uuid=None):
        """Enqueue datasource synchronization on its LensNode."""

        datasource = self.get_object()
        if datasource.source_type == DataSource.SourceType.MANAGED_WORKSPACE:
            return Response(
                {"detail": "DATASOURCE_SYNC_NOT_SUPPORTED"},
                status=status.HTTP_409_CONFLICT,
            )
        if datasource.status == DataSource.Status.DISABLED:
            return Response(
                {"detail": "DATASOURCE_DISABLED"},
                status=status.HTTP_409_CONFLICT,
            )
        task_id = uuid_mod.uuid4().hex
        task_execution = self._enqueue_datasource_sync(
            datasource,
            task_id,
            "manual",
            request.user,
        )
        return Response(
            {
                "uuid": str(datasource.uuid),
                "task_id": task_id,
                "task_execution_id": task_execution.id,
                "status": datasource.status,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=["post"])
    def convert(self, request, uuid=None):
        """Enqueue explicit conversion for a managed workspace."""

        datasource = self.get_object()
        if datasource.source_type != DataSource.SourceType.MANAGED_WORKSPACE:
            return Response(
                {"detail": "DATASOURCE_CONVERSION_NOT_SUPPORTED"},
                status=status.HTTP_409_CONFLICT,
            )
        if datasource.status == DataSource.Status.DISABLED:
            return Response(
                {"detail": "DATASOURCE_DISABLED"},
                status=status.HTTP_409_CONFLICT,
            )
        serializer = DataSourceConversionRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        conversion = serializer.validated_data["conversion"]
        force = serializer.validated_data["force"]
        task_id = uuid_mod.uuid4().hex
        celery_task_id = uuid_mod.uuid4().hex
        task_execution = register_datasource_conversion_task(
            datasource,
            task_id,
            conversion,
            force=force,
            created_by=request.user,
            metadata={"celery_task_id": celery_task_id},
        )
        datasource.last_conversion_status = "PENDING"
        datasource.save(update_fields=["last_conversion_status", "updated_at"])
        datasource_conversion_task.apply_async(
            args=[
                str(datasource.uuid),
                conversion,
                force,
                task_id,
            ],
            task_id=celery_task_id,
        )
        return Response(
            {
                "uuid": str(datasource.uuid),
                "task_id": task_id,
                "task_execution_id": task_execution.id,
                "status": task_execution.status,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=["post"], url_path="upload")
    def upload(self, request, uuid=None):
        """Queue one file upload into a Managed Workspace."""

        datasource = self.get_object()
        if datasource.source_type != DataSource.SourceType.MANAGED_WORKSPACE:
            return Response(
                {"detail": "DATASOURCE_UPLOAD_NOT_SUPPORTED"},
                status=status.HTTP_409_CONFLICT,
            )
        if datasource.status == DataSource.Status.DISABLED:
            return Response(
                {"detail": "DATASOURCE_DISABLED"},
                status=status.HTTP_409_CONFLICT,
            )
        uploaded = request.FILES.get("file")
        if uploaded is None:
            return Response(
                {"detail": "DATASOURCE_UPLOAD_FILE_REQUIRED"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if uploaded.size > 25 * 1024 * 1024:
            return Response(
                {"detail": "DATASOURCE_UPLOAD_TOO_LARGE"},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        filename = os.path.basename(str(uploaded.name or "")).strip()
        if not filename or filename in {".", ".."}:
            return Response(
                {"detail": "DATASOURCE_UPLOAD_FILENAME_INVALID"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        lowered_filename = filename.lower()
        if not any(
            lowered_filename.endswith(extension)
            for extension in DATASOURCE_UPLOAD_EXTENSIONS
        ):
            return Response(
                {"detail": "DATASOURCE_UPLOAD_FILE_TYPE_UNSUPPORTED"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        task_id = uuid_mod.uuid4().hex
        storage_name = default_storage.save(
            f"datasource-uploads/{datasource.uuid}/{task_id}/{filename}",
            ContentFile(uploaded.read()),
        )
        register_datasource_upload_task(
            datasource,
            task_id,
            filename,
            created_by=request.user,
            metadata={"storage_name": storage_name},
        )
        datasource_upload_task.apply_async(
            args=[str(datasource.uuid), storage_name, filename],
            task_id=task_id,
        )
        return Response(
            {
                "uuid": str(datasource.uuid),
                "task_id": task_id,
                "filename": filename,
                "status": "PENDING",
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=["post"], url_path="set-enabled")
    def set_enabled(self, request, uuid=None):
        """Enable or disable a datasource and sync its schedule."""

        enabled = request.data.get("enabled")
        if not isinstance(enabled, bool):
            return Response(
                {"detail": "enabled must be a boolean."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        datasource = self.get_object()
        next_status = (
            DataSource.Status.ACTIVE if enabled else DataSource.Status.DISABLED
        )
        if datasource.status != next_status:
            datasource.status = next_status
            datasource.save(update_fields=["status", "updated_at"])
        ensure_datasource_periodic_task(datasource)
        return Response(DataSourceSerializer(datasource).data)

    @action(detail=True, methods=["post"], url_path="refresh-availability")
    def refresh_availability(self, request, uuid=None):
        """Refresh a managed workspace path without modifying its contents."""

        datasource = self.get_object()
        if datasource.source_type != DataSource.SourceType.MANAGED_WORKSPACE:
            return Response(
                {"detail": "DATASOURCE_AVAILABILITY_NOT_SUPPORTED"},
                status=status.HTTP_409_CONFLICT,
            )
        try:
            result = check_datasource_path(
                datasource.lensnode,
                datasource.target_path,
                datasource.source_type,
            )
        except (DataSourcePathError, DataSourceDispatchError) as exc:
            datasource.availability_status = DataSource.AvailabilityStatus.ERROR
            datasource.availability_message = str(exc)
        else:
            is_available = bool(result.get("exists") and result.get("is_directory"))
            datasource.availability_status = (
                DataSource.AvailabilityStatus.AVAILABLE
                if is_available
                else DataSource.AvailabilityStatus.UNAVAILABLE
            )
            datasource.availability_message = str(result.get("message") or "")
        datasource.availability_checked_at = timezone.now()
        datasource.save(
            update_fields=[
                "availability_status",
                "availability_checked_at",
                "availability_message",
                "updated_at",
            ]
        )
        return Response(DataSourceSerializer(datasource).data)

    @action(detail=True, methods=["get"], url_path="files")
    def files(self, request, uuid=None):
        """Return the current manifest-backed datasource file catalog."""

        datasource = self.get_object()
        try:
            page = max(1, int(request.query_params.get("page") or 1))
            page_size = min(
                100,
                max(1, int(request.query_params.get("page_size") or 20)),
            )
        except (TypeError, ValueError):
            return Response(
                {"detail": "DATASOURCE_FILE_QUERY_INVALID"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            result = list_datasource_files(
                datasource,
                page=page,
                page_size=page_size,
                query=request.query_params.get("query") or "",
                sync_status=request.query_params.get("sync_status") or "",
                conversion_status=(
                    request.query_params.get("conversion_status") or ""
                ),
            )
        except (DataSourcePathError, DataSourceDispatchError) as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if result.get("error"):
            return Response(
                {"detail": result["error"]},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(result)

    @action(detail=True, methods=["get"], url_path="sync-tasks")
    def sync_tasks(self, request, uuid=None):
        """List sync task executions for this datasource (paginated)."""

        from agentcore_task.adapters.django.models import TaskExecution
        from agentcore_task.adapters.django.serializers import (
            TaskExecutionListSerializer,
        )

        datasource = self.get_object()
        queryset = TaskExecution.objects.filter(
            module="lens_datasource",
            metadata__datasource_uuid=str(datasource.uuid),
        ).order_by("-created_at")
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = TaskExecutionListSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = TaskExecutionListSerializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["get"], url_path="conversion-tasks")
    def conversion_tasks(self, request, uuid=None):
        """List managed workspace conversion tasks (paginated)."""

        from agentcore_task.adapters.django.models import TaskExecution
        from agentcore_task.adapters.django.serializers import (
            TaskExecutionListSerializer,
        )

        datasource = self.get_object()
        queryset = TaskExecution.objects.filter(
            module="lens_datasource_conversion",
            metadata__datasource_uuid=str(datasource.uuid),
        ).order_by("-created_at")
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = TaskExecutionListSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = TaskExecutionListSerializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["post"], url_path="cancel-sync")
    def cancel_sync(self, request, uuid=None):
        """Cancel the latest running synchronization for this datasource."""

        from agentcore_task.adapters.django.models import TaskExecution
        from agentcore_task.constants import TaskStatus
        from core.celery import app

        datasource = self.get_object()
        with transaction.atomic():
            datasource = DataSource.objects.select_for_update().get(
                pk=datasource.pk
            )
            task = (
                TaskExecution.objects.select_for_update()
                .filter(
                    module="lens_datasource",
                    metadata__datasource_uuid=str(datasource.uuid),
                    status__in=[
                        TaskStatus.PENDING,
                        *TaskStatus.get_running_statuses(),
                        DATASOURCE_CANCELLING_STATUS,
                    ],
                )
                .order_by("-created_at")
                .first()
            )
            if task is None:
                return Response(
                    {"detail": "No running datasource sync task."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            metadata = dict(task.metadata or {})
            celery_task_id = metadata.get("celery_task_id") or task.task_id
            app.control.revoke(celery_task_id, terminate=False)
            cancel_datasource_sync_on_lensnode(
                datasource.lensnode,
                task.task_id,
            )

            metadata["manual_revoked_at"] = timezone.now().isoformat()
            metadata["manual_revoked_by"] = request.user.pk
            dispatched = bool(
                metadata.get("lock_token")
                or metadata.get("datasource_sync_request_id")
                or task.status in TaskStatus.get_running_statuses()
            )
            task.status = (
                DATASOURCE_CANCELLING_STATUS
                if dispatched
                else TaskStatus.REVOKED
            )
            task.finished_at = None if dispatched else timezone.now()
            task.error = "" if dispatched else "DATASOURCE_SYNC_CANCELLED"
            metadata["cancellation_state"] = task.status
            if not dispatched:
                metadata["completion_reason"] = "DATASOURCE_SYNC_CANCELLED"
                metadata["stop_confirmation_source"] = "queued_before_dispatch"
            task.metadata = metadata
            task.save(
                update_fields=[
                    "status",
                    "finished_at",
                    "error",
                    "metadata",
                ]
            )
        return Response(
            {
                "uuid": str(datasource.uuid),
                "task_id": task.task_id,
                "task_execution_id": task.id,
                "status": task.status,
            }
        )

    @action(detail=True, methods=["post"], url_path="cancel-conversion")
    def cancel_conversion(self, request, uuid=None):
        """Cancel the latest active managed workspace conversion."""

        from agentcore_task.adapters.django.models import TaskExecution
        from agentcore_task.constants import TaskStatus
        from core.celery import app

        datasource = self.get_object()
        task = (
            TaskExecution.objects.filter(
                module="lens_datasource_conversion",
                metadata__datasource_uuid=str(datasource.uuid),
                status__in=[
                    TaskStatus.PENDING,
                    *TaskStatus.get_running_statuses(),
                    DATASOURCE_CANCELLING_STATUS,
                ],
            )
            .order_by("-created_at")
            .first()
        )
        if task is None:
            return Response(
                {"detail": "No running datasource conversion task."},
                status=status.HTTP_404_NOT_FOUND,
            )
        metadata = dict(task.metadata or {})
        celery_task_id = metadata.get("celery_task_id") or task.task_id
        app.control.revoke(celery_task_id, terminate=False)
        cancel_datasource_conversion_on_lensnode(
            datasource.lensnode,
            task.task_id,
        )
        now = timezone.now()
        metadata["manual_revoked_at"] = now.isoformat()
        metadata["manual_revoked_by"] = request.user.pk
        queued = task.status == TaskStatus.PENDING
        task.status = TaskStatus.REVOKED if queued else DATASOURCE_CANCELLING_STATUS
        task.finished_at = now if queued else None
        task.error = "DATASOURCE_CONVERSION_CANCELLED" if queued else ""
        metadata["cancellation_state"] = (
            "REVOKED" if queued else DATASOURCE_CANCELLING_STATUS
        )
        if queued:
            metadata["completion_reason"] = "DATASOURCE_CONVERSION_CANCELLED"
            metadata["stop_confirmation_source"] = "queued_before_dispatch"
        task.metadata = metadata
        task.save(
            update_fields=[
                "status",
                "finished_at",
                "error",
                "metadata",
            ]
        )
        datasource.last_conversion_status = task.status
        datasource.last_conversion_at = now if queued else None
        datasource.save(
            update_fields=[
                "last_conversion_status",
                "last_conversion_at",
                "updated_at",
            ]
        )
        return Response(
            {
                "uuid": str(datasource.uuid),
                "task_id": task.task_id,
                "task_execution_id": task.id,
                "status": task.status,
            }
        )
