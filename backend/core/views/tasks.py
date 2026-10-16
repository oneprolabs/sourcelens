"""SourceLens read projections for the agentcore task list."""

from agentcore_task.adapters.django.views import TaskExecutionViewSet


class TaskExecutionListViewSet(TaskExecutionViewSet):
    """Keep upstream filtering and serialization without unused task bodies."""

    def get_queryset(self):
        """Load metadata only when the list serializer will include it."""

        deferred = ["task_args", "task_kwargs", "result", "error", "traceback"]
        include_metadata = self.request.query_params.get("include_metadata", "true")
        if include_metadata.lower() in {"0", "false", "no", "off"}:
            deferred.append("metadata")
        return super().get_queryset().defer(*deferred)
