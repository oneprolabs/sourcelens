"""Platform-only task management with lean collection reads."""

from agentcore_task.adapters.django.views import TaskExecutionViewSet

from accounts.audit import record_audit
from accounts.permissions import HasPlatformSuperAdmin


class TaskExecutionListViewSet(TaskExecutionViewSet):
    """Protect every inherited action and keep task list reads compact."""

    permission_classes = [HasPlatformSuperAdmin]

    def get_queryset(self):
        """Load metadata only when the list serializer will include it."""

        queryset = super().get_queryset()
        if self.action != "list":
            return queryset
        deferred = ["task_args", "task_kwargs", "result", "error", "traceback"]
        include_metadata = self.request.query_params.get("include_metadata", "true")
        if include_metadata.lower() in {"0", "false", "no", "off"}:
            deferred.append("metadata")
        return queryset.defer(*deferred)

    def finalize_response(self, request, response, *args, **kwargs):
        """Audit successful manual syncs, including legacy sync-on-read routes."""
        action = getattr(self, "action", "")
        sync_requested = action == "sync" or (
            action in {"status", "by_task_id"} and request.query_params.get("sync", "true").lower() == "true"
        )
        if sync_requested and response.status_code == 200:
            record_audit(
                action="task.sync",
                actor=request.user,
                request=request,
                resource_type="task",
                resource_id=response.data.get("id", ""),
            )
        return super().finalize_response(request, response, *args, **kwargs)
