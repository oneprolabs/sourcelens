"""Tenant-safe task progress shared by datasource list and detail reads."""

from agentcore_task.adapters.django.serializers import TaskExecutionListSerializer

PROGRESS_FIELDS = {
    "datasource_uuid",
    "trigger",
    "progress_percent",
    "phase",
    "overall_progress_percent",
    "phase_progress",
    "progress_counts",
    "last_substantive_progress_at",
    "progress_step",
    "progress_message",
    "sync_summary",
    "conversion_summary",
    "source_type",
    "target_path",
    "filename",
    "byte_size",
    "content_type",
    "is_latest_version",
    "deleted",
    "duplicate",
    "error",
}


class DataSourceTaskSerializer(TaskExecutionListSerializer):
    """Restrict requested operational progress to a server-side allowlist."""

    def get_metadata(self, obj):
        """Always enforce the server-side metadata projection."""
        metadata = super().get_metadata(obj) or {}
        projected = {key: value for key, value in metadata.items() if key in PROGRESS_FIELDS}
        conversion = projected.get("conversion_summary")
        if isinstance(conversion, dict):
            projected["conversion_summary"] = {
                key: value
                for key, value in conversion.items()
                if key in {"total", "candidates", "converted", "success", "failed", "skipped", "unsupported"}
                and type(value) in (int, float)
                and value >= 0
            }
        else:
            projected.pop("conversion_summary", None)
        return projected
