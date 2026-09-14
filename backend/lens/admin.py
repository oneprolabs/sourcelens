from django.contrib import admin

from .models import (
    Assistant,
    AssistantMCP,
    AssistantSkill,
    DataSource,
    GlobalSetting,
    MCPServer,
    Message,
    LensNode,
    Run,
    RunExecution,
    RunStep,
    ScheduledTask,
    Session,
    SessionCleanupOperation,
    Skill,
)


@admin.register(LensNode)
class LensNodeAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "status",
        "enrollment_status",
        "protocol_version",
        "agent_version",
        "last_heartbeat_at",
    )
    search_fields = ("name",)


@admin.register(Assistant)
class AssistantAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "lensnode", "capability", "status")
    search_fields = ("name", "slug")


@admin.register(DataSource)
class DataSourceAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "source_type",
        "status",
        "last_synced_at",
        "last_error",
    )
    search_fields = ("name",)


@admin.register(Skill)
class SkillAdmin(admin.ModelAdmin):
    list_display = ("name", "uuid", "kind", "enabled")
    search_fields = ("name", "package_name", "uuid")


@admin.register(MCPServer)
class MCPServerAdmin(admin.ModelAdmin):
    list_display = ("name", "transport", "enabled")
    search_fields = ("name",)


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = ("uuid", "assistant", "user", "status", "created_at")
    search_fields = ("title",)


@admin.register(SessionCleanupOperation)
class SessionCleanupOperationAdmin(admin.ModelAdmin):
    list_display = ("session_uuid", "lensnode_uuid", "status", "attempts", "next_retry_at")
    list_filter = ("status",)
    search_fields = ("session_uuid", "lensnode_uuid")


admin.site.register(AssistantSkill)
admin.site.register(AssistantMCP)
admin.site.register(Message)
admin.site.register(Run)
admin.site.register(RunStep)
admin.site.register(RunExecution)
admin.site.register(ScheduledTask)
admin.site.register(GlobalSetting)
