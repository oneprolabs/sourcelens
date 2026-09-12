import re
from pathlib import PurePosixPath
from urllib import parse

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from .assistant_lifecycle import (
    AssistantNotRunnableError,
    create_assistant_session,
    create_smart_collaboration_session,
    fixed_collaboration_assistants,
    smart_collaboration_assistants,
)
from .datasource.bindings import (
    DatasourceBindingsField, replace_datasource_bindings,
)
from .attachments import ATTACHMENT_MAX_PER_MESSAGE, AttachmentError
from .citations import public_run_citations, sanitize_planned_evidence
from .datasource.services import (
    DataSourceDispatchError,
    DataSourcePathError,
    check_datasource_path,
    normalize_workspace_target_path,
    validate_datasource_lensnode,
)
from .environment_variables import (
    declared_environment_references,
    environment_references,
    missing_required_environment_values,
    validate_environment_schema,
    validate_environment_values,
    validate_skill_api_policy,
)
from .model_checks import check_assistant_model_refs
from .models import (
    Assistant,
    AssistantAccess,
    AssistantMCP,
    AssistantPluginBinding,
    AssistantSkill,
    Connection,
    DataSource,
    DataSourceItem,
    DataSourceVersion,
    AssistantDataSourceBinding,
    DataSourceCredential,
    EnvironmentVariableSet,
    GlobalSetting,
    LensNode,
    MCPServer,
    Message,
    MessageAttachment,
    PluginInvocation,
    Run,
    RunExecution,
    RunOutputFile,
    RunStep,
    ScheduledTask,
    SecretMaterial,
    SecretVersion,
    Session,
    SharedQA,
    SharedQAFile,
    Skill,
    assistant_mode_for,
)
from .plugins.providers import DatasourceProviderError, get_datasource_provider
from .plugins.registry import PluginRegistryError, installed_plugin
from .plugins.skill_requirements import (
    SkillPluginRequirementError,
    validate_required_plugins,
)
from .routing_descriptions import refresh_routing_description
from .runtime_events import (
    public_step_detail,
    sanitize_loaded_mcps,
    sanitize_loaded_skills,
    sanitize_termination_detail,
)
from .services import (
    CLARIFICATION_MAX_ORIGINAL_CHARS,
    MAX_SUBAGENTS_PER_RUN,
    assistant_supports_document_attachments,
    create_execution_run,
    token_budget_profile_for_rounds,
    validate_retry_run,
)
from .skill_generation import (
    get_workspace_guide_payload,
    sync_workspace_guide_skill,
)
from .vision_capabilities import (
    resolve_model_capability,
    validate_vision_model_ref,
)

User = get_user_model()


def _task_names(lensnode):
    """Return task names reported by a LensNode."""

    names = set()
    for task in lensnode.tasks or []:
        if isinstance(task, dict) and task.get("name"):
            names.add(task["name"])
    return names


def _dir_paths(lensnode):
    """Return available directory paths reported by a LensNode."""

    paths = set()
    for item in lensnode.available_dirs or []:
        if isinstance(item, str):
            paths.add(item)
        elif isinstance(item, dict) and item.get("path"):
            paths.add(item["path"])
    return paths


def validate_retrieval_scope(value):
    """Validate retrieval_scope JSON."""

    if value is None:
        return None
    if not isinstance(value, dict):
        raise serializers.ValidationError("retrieval_scope must be an object or null")

    list_fields = [
        "include_paths",
        "exclude_paths",
        "exclude_extensions",
    ]
    for field in list_fields:
        items = value.get(field)
        if items is not None and (
            not isinstance(items, list)
            or any(not isinstance(item, str) for item in items)
        ):
            raise serializers.ValidationError(
                f"retrieval_scope.{field} must be a list of strings"
            )

    max_depth = value.get("max_depth")
    if max_depth is not None and (not isinstance(max_depth, int) or max_depth <= 0):
        raise serializers.ValidationError(
            "retrieval_scope.max_depth must be a positive integer"
        )

    include_hidden = value.get("include_hidden")
    if "include_hidden" in value and not isinstance(include_hidden, bool):
        raise serializers.ValidationError(
            "retrieval_scope.include_hidden must be a boolean"
        )

    return value


def validate_retrieval_policy(value):
    """Validate Assistant-level retrieval policy options."""

    if value is None:
        return None
    if not isinstance(value, dict):
        raise serializers.ValidationError(
            "settings.retrieval_policy must be an object or null"
        )
    include_hidden = value.get("include_hidden")
    if "include_hidden" in value and not isinstance(include_hidden, bool):
        raise serializers.ValidationError(
            "settings.retrieval_policy.include_hidden must be " "a boolean"
        )
    return value


def validate_selected_dirs(value, lensnode=None):
    """Validate selected_dirs payload against LensNode availability."""

    if not isinstance(value, list):
        raise serializers.ValidationError("selected_dirs must be a list")

    available = _dir_paths(lensnode) if lensnode else None
    for item in value:
        if not isinstance(item, dict):
            raise serializers.ValidationError("selected_dirs items must be objects")
        path = item.get("path")
        if not isinstance(path, str) or not path:
            raise serializers.ValidationError("selected_dirs.path is required")
        if available is not None and path not in available:
            raise serializers.ValidationError(
                f"selected_dirs path is not available on LensNode: {path}"
            )
        if "retrieval_scope" in item:
            validate_retrieval_scope(item.get("retrieval_scope"))
    return value


class LensNodeSerializer(serializers.ModelSerializer):
    """LensNode serializer."""

    has_token = serializers.SerializerMethodField()
    datasources = serializers.SerializerMethodField()
    active_run_count = serializers.IntegerField(read_only=True, default=0)
    queued_run_count = serializers.IntegerField(read_only=True, default=0)
    awaiting_resume_count = serializers.IntegerField(read_only=True, default=0)
    total_run_count = serializers.IntegerField(read_only=True, default=0)
    succeeded_run_count = serializers.IntegerField(read_only=True, default=0)
    failed_run_count = serializers.IntegerField(read_only=True, default=0)
    total_tokens = serializers.IntegerField(read_only=True, default=0)
    last_run_at = serializers.DateTimeField(read_only=True, allow_null=True)

    class Meta:
        model = LensNode
        fields = [
            "uuid",
            "name",
            "status",
            "connection_id",
            "workspace_path",
            "available_dirs",
            "protocol_version",
            "agent_version",
            "tasks",
            "labels",
            "last_metrics",
            "active_datasource_operations",
            "enrollment_status",
            "token_issued_at",
            "token_revoked",
            "has_token",
            "datasources",
            "last_authenticated_at",
            "last_heartbeat_at",
            "active_run_count",
            "queued_run_count",
            "awaiting_resume_count",
            "total_run_count",
            "succeeded_run_count",
            "failed_run_count",
            "total_tokens",
            "last_run_at",
            "registered_at",
            "created_at",
            "updated_at",
        ]

    def get_datasources(self, obj):
        """Return the active datasources already assigned to this node."""
        result = []
        for datasource in obj.datasources.filter(status="active"):
            usage = {
                "raw_bytes": 0,
                "derived_bytes": 0,
                "total_bytes": 0,
                "status": "complete",
            }
            measured = False
            for item in datasource.items.filter(status="active"):
                item_usage = item.storage_usage or {}
                if not item_usage:
                    continue
                measured = True
                usage["raw_bytes"] += int(item_usage.get("raw_bytes") or 0)
                usage["derived_bytes"] += int(
                    item_usage.get("derived_bytes") or 0
                )
                usage["total_bytes"] += int(item_usage.get("total_bytes") or 0)
            result.append({
                "uuid": str(datasource.uuid),
                "name": datasource.name,
                "source_type": datasource.source_type,
                "status": datasource.status,
                "storage_usage": usage if measured else {},
            })
        return result
        read_only_fields = [
            "uuid",
            "assistant",
            "assistant_name",
            "assistant_slug",
            "routing_mode",
            "routing_assistants",
            "user",
            "title_manually_edited",
            "title_generation_status",
            "pinned_at",
            "has_shareable_answer",
            "status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "uuid",
            "assistant",
            "assistant_name",
            "assistant_slug",
            "routing_mode",
            "routing_assistants",
            "user",
            "title_manually_edited",
            "title_generation_status",
            "pinned_at",
            "has_shareable_answer",
            "status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "uuid",
            "status",
            "connection_id",
            "token_issued_at",
            "last_authenticated_at",
            "last_heartbeat_at",
            "last_metrics",
            "active_datasource_operations",
            "registered_at",
            "created_at",
            "updated_at",
        ]

    def get_has_token(self, obj):
        """Whether the node currently has a usable (un-revoked) token."""

        return bool(obj.auth_token_hash) and not obj.token_revoked


class SkillBindingsField(serializers.Field):
    """Read/write field for assistant skill bindings."""

    def to_representation(self, bindings):
        bindings = bindings.select_related("skill", "environment_variable_set").all()
        return [
            {
                "skill_uuid": str(binding.skill.uuid),
                "skill_name": binding.skill.name,
                "enabled": binding.enabled,
                "load_config": binding.load_config,
                "environment_variable_set_uuid": (
                    str(binding.environment_variable_set.uuid)
                    if binding.environment_variable_set
                    else None
                ),
                "environment_variable_set_name": (
                    binding.environment_variable_set.name
                    if binding.environment_variable_set
                    else ""
                ),
            }
            for binding in bindings
        ]

    def to_internal_value(self, data):
        if not isinstance(data, list):
            raise serializers.ValidationError("Expected a list of bindings.")

        validated = []
        for item in data:
            if not isinstance(item, dict):
                raise serializers.ValidationError("Each binding must be an object.")
            if "skill_uuid" not in item:
                raise serializers.ValidationError("Missing skill_uuid in binding.")
            skill = Skill.objects.filter(uuid=item["skill_uuid"]).first()
            if skill is None:
                raise serializers.ValidationError("Skill does not exist.")
            variable_set_uuid = item.get("environment_variable_set_uuid")
            variable_set = None
            if variable_set_uuid:
                variable_set = EnvironmentVariableSet.objects.filter(
                    uuid=variable_set_uuid,
                    enabled=True,
                ).first()
                if variable_set is None:
                    raise serializers.ValidationError(
                        "The selected environment variable set is unavailable."
                    )
            environment_values = validate_environment_values(
                item.get("environment_values")
            )
            declarations = (skill.definition or {}).get("environment") or []
            declared_names = {
                declaration.get("name")
                for declaration in declarations
                if isinstance(declaration, dict)
            }
            unknown_names = sorted(set(environment_values) - declared_names)
            if unknown_names:
                raise serializers.ValidationError(
                    "Environment values must be declared by the Skill: "
                    f'{", ".join(unknown_names)}.'
                )
            effective_values = (
                variable_set.get_values() if variable_set is not None else {}
            )
            effective_values.update(environment_values)
            missing = missing_required_environment_values(
                skill,
                effective_values,
            )
            enabled = item.get("enabled", True)
            if enabled and missing:
                raise serializers.ValidationError(
                    "Add values for the required environment variables for "
                    f'"{skill.name}": {", ".join(missing)}.'
                )
            variable_set_name = str(
                item.get("environment_variable_set_name") or ""
            ).strip()
            if len(variable_set_name) > 160:
                raise serializers.ValidationError(
                    "The environment variable set name must be 160 "
                    "characters or fewer."
                )
            validated.append(
                {
                    "skill_uuid": item["skill_uuid"],
                    "enabled": enabled,
                    "load_config": item.get("load_config", {}),
                    "environment_variable_set": variable_set,
                    "environment_variable_set_name": variable_set_name,
                    "environment_values": environment_values,
                }
            )
        return validated


class McpBindingsField(serializers.Field):
    """Read/write field for assistant MCP bindings."""

    def to_representation(self, bindings):
        bindings = bindings.select_related("mcp", "environment_variable_set").all()
        return [
            {
                "mcp_uuid": str(binding.mcp.uuid),
                "mcp_name": binding.mcp.name,
                "enabled": binding.enabled,
                "load_config": binding.load_config,
                "environment_variable_set_uuid": (
                    str(binding.environment_variable_set.uuid)
                    if binding.environment_variable_set
                    else None
                ),
                "environment_variable_set_name": (
                    binding.environment_variable_set.name
                    if binding.environment_variable_set
                    else ""
                ),
            }
            for binding in bindings
        ]

    def to_internal_value(self, data):
        if not isinstance(data, list):
            raise serializers.ValidationError("Expected a list of bindings.")

        validated = []
        for item in data:
            if not isinstance(item, dict):
                raise serializers.ValidationError("Each binding must be an object.")
            if "mcp_uuid" not in item:
                raise serializers.ValidationError("Missing mcp_uuid in binding.")
            mcp = MCPServer.objects.filter(uuid=item["mcp_uuid"]).first()
            if mcp is None:
                raise serializers.ValidationError("MCP server does not exist.")
            variable_set_uuid = item.get("environment_variable_set_uuid")
            variable_set = None
            if variable_set_uuid:
                variable_set = EnvironmentVariableSet.objects.filter(
                    uuid=variable_set_uuid,
                    enabled=True,
                ).first()
                if variable_set is None:
                    raise serializers.ValidationError(
                        "The selected environment variable set is unavailable."
                    )
            environment_values = validate_environment_values(
                item.get("environment_values")
            )
            declarations = mcp.environment or []
            declared_names = {
                declaration.get("name")
                for declaration in declarations
                if isinstance(declaration, dict)
            }
            unknown_names = sorted(set(environment_values) - declared_names)
            if unknown_names:
                raise serializers.ValidationError(
                    "Environment values must be declared by the MCP server: "
                    f'{", ".join(unknown_names)}.'
                )
            effective_values = (
                variable_set.get_values() if variable_set is not None else {}
            )
            effective_values.update(environment_values)
            required_names = {
                declaration.get("name")
                for declaration in declarations
                if isinstance(declaration, dict)
                and declaration.get("required")
                and declaration.get("name")
            }
            required_names.update(
                declared_environment_references(
                    {"endpoint": mcp.endpoint, "config": mcp.config},
                    declarations,
                )
            )
            missing = sorted(
                name
                for name in required_names
                if not str(effective_values.get(name) or "")
            )
            enabled = item.get("enabled", True)
            if enabled and missing:
                raise serializers.ValidationError(
                    "Add values for the required environment variables for "
                    f'"{mcp.name}": {", ".join(missing)}.'
                )
            variable_set_name = str(
                item.get("environment_variable_set_name") or ""
            ).strip()
            if len(variable_set_name) > 160:
                raise serializers.ValidationError(
                    "The environment variable set name must be 160 "
                    "characters or fewer."
                )
            validated.append(
                {
                    "mcp_uuid": item["mcp_uuid"],
                    "enabled": enabled,
                    "load_config": item.get("load_config", {}),
                    "environment_variable_set": variable_set,
                    "environment_variable_set_name": variable_set_name,
                    "environment_values": environment_values,
                }
            )
        return validated


class PluginBindingsField(serializers.Field):
    """Read/write field for Assistant Plugin connection bindings."""

    def to_representation(self, bindings):
        """Return binding identities without Connection secrets."""

        return [
            {
                "connection_uuid": str(binding.connection.uuid),
                "connection_name": binding.connection.name,
                "plugin_key": binding.connection.plugin_key,
                "tools": list(binding.tools or []),
                "all_tools": True,
                "enabled": binding.enabled,
            }
            for binding in bindings.select_related("connection").all()
        ]

    def to_internal_value(self, data):
        """Validate Plugin connections and normalize legacy tool lists.

        Direct Assistant bindings grant the complete read-only capability set
        declared by the installed Plugin manifest.  The legacy ``tools``
        field is accepted for wire compatibility and validated when present,
        but it is not an allowlist for runtime dispatch.
        """

        if not isinstance(data, list):
            raise serializers.ValidationError("Expected a list of bindings.")
        validated = []
        seen = set()
        for item in data:
            if not isinstance(item, dict):
                raise serializers.ValidationError(
                    "Each binding must be an object."
                )
            connection_uuid = item.get("connection_uuid")
            connection = Connection.objects.select_related(
                "secret_version__material"
            ).filter(uuid=connection_uuid).first()
            if connection is None:
                raise serializers.ValidationError(
                    "Plugin Connection does not exist."
                )
            if connection.pk in seen:
                raise serializers.ValidationError(
                    "Plugin Connection bindings must be unique."
                )
            if connection.status != Connection.Status.ACTIVE:
                raise serializers.ValidationError(
                    "Plugin Connection is disabled."
                )
            enabled = item.get("enabled", True)
            if not isinstance(enabled, bool):
                raise serializers.ValidationError(
                    "Plugin binding enabled must be a boolean."
                )
            secret_version = connection.secret_version
            if enabled and (
                secret_version is None
                or secret_version.status != "active"
                or secret_version.material.status != "active"
            ):
                raise serializers.ValidationError(
                    "Plugin Connection secret is unavailable."
                )
            try:
                plugin = installed_plugin(connection.plugin_key)
            except PluginRegistryError as exc:
                raise serializers.ValidationError(str(exc)) from exc
            available = {tool.key for tool in plugin.tools}
            requested = item.get("tools")
            if requested is None or requested == []:
                requested = sorted(available)
            if (
                not isinstance(requested, list)
                or not requested
                or any(not isinstance(tool, str) for tool in requested)
                or len(set(requested)) != len(requested)
                or set(requested).difference(available)
            ):
                raise serializers.ValidationError(
                    "Plugin tools must be installed read-only tools."
                )
            seen.add(connection.pk)
            validated.append(
                {
                    "connection": connection,
                    "tools": requested,
                    "enabled": enabled,
                }
            )
        return validated


class AccessGrantsField(serializers.Field):
    """Read/write field for assistant access grants (group or user)."""

    def to_representation(self, grants):
        grants = grants.select_related("group", "user").all()
        result = []
        for grant in grants:
            if grant.group_id:
                result.append(
                    {
                        "type": "group",
                        "id": grant.group_id,
                        "name": grant.group.name,
                    }
                )
            elif grant.user_id:
                result.append(
                    {
                        "type": "user",
                        "id": grant.user_id,
                        "name": grant.user.get_username(),
                        "username": grant.user.get_username(),
                        "email": grant.user.email or "",
                    }
                )
        return result

    def to_internal_value(self, data):
        if not isinstance(data, list):
            raise serializers.ValidationError("Expected a list of grants.")

        validated = []
        seen = set()
        for item in data:
            if not isinstance(item, dict):
                raise serializers.ValidationError("Each grant must be an object.")
            grant_type = item.get("type")
            grant_id = item.get("id")
            if grant_type not in ("group", "user") or grant_id is None:
                raise serializers.ValidationError(
                    "Each grant needs type (group|user) and id."
                )
            key = (grant_type, grant_id)
            if key in seen:
                continue
            seen.add(key)
            validated.append({"type": grant_type, "id": grant_id})
        return validated


class AssistantListSerializer(serializers.ModelSerializer):
    """Compact assistant representation for collection responses."""

    datasource_routing = serializers.SerializerMethodField()
    datasource_bindings = DatasourceBindingsField(read_only=True)
    lensnode = serializers.UUIDField(source="lensnode.uuid", read_only=True)
    lensnode_name = serializers.CharField(source="lensnode.name", read_only=True)
    mode = serializers.CharField(read_only=True)
    collaboration_members = serializers.SerializerMethodField()
    skill_summary = serializers.SerializerMethodField()
    mcp_summary = serializers.SerializerMethodField()
    plugin_summary = serializers.SerializerMethodField()
    supports_document_attachments = serializers.SerializerMethodField()
    vision_model_capability = serializers.SerializerMethodField()
    can_process_images = serializers.SerializerMethodField()

    class Meta:
        model = Assistant
        fields = [
            "uuid",
            "datasource_routing",
            "datasource_bindings",
            "name",
            "capability",
            "slug",
            "lensnode",
            "lensnode_name",
            "mode",
            "routing_mode",
            "collaboration_members",
            "status",
            "visibility",
            "skill_summary",
            "mcp_summary",
            "plugin_summary",
            "supports_document_attachments",
            "vision_model_capability",
            "can_process_images",
        ]

    def get_datasource_routing(self, assistant):
        """Expose the data selection mode without other runtime settings."""

        return "selected"

    def get_collaboration_members(self, assistant):
        """Return prefetched Smart Assistant members for list views."""

        if not assistant.mode_handler.supports_members:
            return []
        return [
            {
                "uuid": str(member.uuid),
                "name": member.name,
                "capability": member.capability,
                "status": member.status,
            }
            for member in sorted(
                assistant.collaboration_members.all(),
                key=lambda item: (item.name, str(item.uuid)),
            )
        ]

    def get_skill_summary(self, assistant):
        """Summarize prefetched Skill bindings without per-row queries."""

        bindings = list(assistant.skill_bindings.all())
        return {
            "total": len(bindings),
            "enabled": sum(binding.enabled for binding in bindings),
        }

    def get_mcp_summary(self, assistant):
        """Summarize prefetched MCP bindings without per-row queries."""

        bindings = list(assistant.mcp_bindings.all())
        return {
            "total": len(bindings),
            "enabled": sum(binding.enabled for binding in bindings),
        }

    def get_plugin_summary(self, assistant):
        """Summarize prefetched Plugin bindings without per-row queries."""

        bindings = list(assistant.plugin_bindings.all())
        return {
            "total": len(bindings),
            "enabled": sum(binding.enabled for binding in bindings),
        }

    def get_supports_document_attachments(self, assistant):
        """Return whether an Assistant can execute with Run documents."""

        if assistant.lensnode_id:
            return assistant_supports_document_attachments(assistant)
        cache = getattr(self, "_document_capability_cache", None)
        if cache is None:
            cache = {}
            self._document_capability_cache = cache
        if assistant.capability not in cache:
            cache[assistant.capability] = assistant_supports_document_attachments(
                assistant
            )
        return cache[assistant.capability]

    def _vision_capability(self, assistant):
        """Cache model capability resolution for the current response."""

        cache = getattr(self, "_vision_capability_cache", None)
        if cache is None:
            cache = {}
            self._vision_capability_cache = cache
        key = str(assistant.multimodal_model_ref or "")
        if key not in cache:
            cache[key] = resolve_model_capability(assistant.multimodal_model_ref)
        return cache[key]

    def get_vision_model_capability(self, assistant):
        """Return the configured vision-model capability."""

        return self._vision_capability(assistant)

    def get_can_process_images(self, assistant):
        """Return whether the Assistant accepts image input."""

        capability = self._vision_capability(assistant)
        return bool(
            assistant.status == Assistant.Status.ACTIVE
            and capability.get("enabled")
            and capability.get("supports_vision")
        )


class AssistantSerializer(serializers.ModelSerializer):
    """Assistant serializer with LensNode and capability validation."""

    lensnode_uuid = serializers.UUIDField(write_only=True, required=False)
    lensnode = serializers.UUIDField(source="lensnode.uuid", read_only=True)
    lensnode_name = serializers.CharField(source="lensnode.name", read_only=True)
    collaboration_member_uuids = serializers.ListField(
        child=serializers.UUIDField(),
        write_only=True,
        required=False,
    )
    collaboration_members = serializers.SerializerMethodField()
    skill_bindings = SkillBindingsField(required=False)
    mcp_bindings = McpBindingsField(required=False)
    plugin_bindings = PluginBindingsField(required=False)
    datasource_bindings = DatasourceBindingsField(required=False)
    access_grants = AccessGrantsField(required=False)
    workspace_guide = serializers.JSONField(required=False)
    skill_summary = serializers.SerializerMethodField()
    mcp_summary = serializers.SerializerMethodField()
    plugin_summary = serializers.SerializerMethodField()
    supports_document_attachments = serializers.SerializerMethodField()
    vision_model_capability = serializers.SerializerMethodField()
    can_process_images = serializers.SerializerMethodField()
    mode = serializers.ChoiceField(
        choices=Assistant.Mode.choices,
        required=False,
        write_only=True,
    )
    kind = serializers.CharField(required=False, write_only=True)
    selected_task = serializers.CharField(required=False, write_only=True)

    class Meta:
        model = Assistant
        fields = [
            "uuid",
            "datasource_bindings",
            "name",
            "description",
            "mode",
            "capability",
            "slug",
            "lensnode",
            "lensnode_name",
            "lensnode_uuid",
            "routing_mode",
            "collaboration_member_uuids",
            "collaboration_members",
            "selected_dirs",
            "multimodal_model_ref",
            "agent_model_ref",
            "agent_rounds",
            "token_budget_profile",
            "max_concurrency",
            "settings",
            "status",
            "visibility",
            "skill_bindings",
            "mcp_bindings",
            "plugin_bindings",
            "access_grants",
            "workspace_guide",
            "kind",
            "selected_task",
            "skill_summary",
            "mcp_summary",
            "plugin_summary",
            "supports_document_attachments",
            "vision_model_capability",
            "can_process_images",
            "created_at",
            "updated_at",
        ]

        read_only_fields = [
            "uuid",
            "lensnode",
            "collaboration_members",
            "skill_summary",
            "mcp_summary",
            "plugin_summary",
            "supports_document_attachments",
            "token_budget_profile",
            "status",
            "created_at",
            "updated_at",
        ]

    def get_skill_summary(self, assistant):
        """Return skill binding summary for list views."""

        enabled = assistant.skill_bindings.filter(enabled=True).count()
        return {
            "total": assistant.skill_bindings.count(),
            "enabled": enabled,
        }

    def get_collaboration_members(self, assistant):
        """Return Smart Assistant members for Assistant list views."""

        if not assistant.mode_handler.supports_members:
            return []
        return [
            {
                "uuid": str(member.uuid),
                "name": member.name,
                "capability": member.capability,
                "status": member.status,
            }
            for member in sorted(
                assistant.collaboration_members.all(),
                key=lambda item: (item.name, str(item.uuid)),
            )
        ]

    def get_supports_document_attachments(self, assistant):
        """Return whether the Assistant can execute with Run documents."""

        if assistant.lensnode_id:
            return assistant_supports_document_attachments(assistant)
        cache = getattr(self, "_document_capability_cache", None)
        if cache is None:
            cache = {}
            self._document_capability_cache = cache
        if assistant.capability not in cache:
            cache[assistant.capability] = assistant_supports_document_attachments(
                assistant
            )
        return cache[assistant.capability]

    def get_vision_model_capability(self, assistant):
        """Return the authoritative capability of the assigned vision model."""

        return resolve_model_capability(assistant.multimodal_model_ref)

    def get_can_process_images(self, assistant):
        """Return whether the current assistant can accept image input."""

        capability = resolve_model_capability(assistant.multimodal_model_ref)
        return bool(
            assistant.status == Assistant.Status.ACTIVE
            and capability.get("enabled")
            and capability.get("supports_vision")
        )

    def get_mcp_summary(self, assistant):
        """Return MCP binding summary for list views."""

        enabled = assistant.mcp_bindings.filter(enabled=True).count()
        return {
            "total": assistant.mcp_bindings.count(),
            "enabled": enabled,
        }

    def get_plugin_summary(self, assistant):
        """Return Plugin binding summary for Assistant views."""

        enabled = assistant.plugin_bindings.filter(enabled=True).count()
        return {
            "total": assistant.plugin_bindings.count(),
            "enabled": enabled,
        }

    def to_internal_value(self, data):
        """Accept legacy type inputs without exposing duplicate fields."""

        normalized = data.copy()
        legacy_task_input = (
            not normalized.get("capability")
            and not normalized.get("kind")
            and bool(normalized.get("selected_task"))
        )
        capability = normalized.get("capability")
        if not capability:
            capability = normalized.get("kind") or normalized.get("selected_task")
        if capability == "standard":
            capability = normalized.get("selected_task") or "general_chat"
        if capability == "qa":
            capability = "knowledge_qa"
        if capability:
            normalized["capability"] = capability
        try:
            return super().to_internal_value(normalized)
        except serializers.ValidationError as exc:
            if legacy_task_input and "capability" in exc.detail:
                detail = dict(exc.detail)
                detail["selected_task"] = detail.pop("capability")
                raise serializers.ValidationError(detail) from exc
            raise

    def to_representation(self, instance):
        """Return assistant data including generated Workspace Guide state."""

        data = super().to_representation(instance)
        data["mode"] = instance.mode
        data["workspace_guide"] = get_workspace_guide_payload(instance)
        return data

    def validate(self, attrs):
        """Validate capability, node, directory, and delegation settings."""

        attrs.pop("kind", None)
        attrs.pop("selected_task", None)
        agent_rounds = attrs.get(
            "agent_rounds",
            getattr(
                self.instance,
                "agent_rounds",
                Assistant.AgentRounds.BALANCED,
            ),
        )
        attrs["token_budget_profile"] = token_budget_profile_for_rounds(
            agent_rounds
        )
        mode = attrs.pop("mode", None)
        lensnode_uuid = attrs.pop("lensnode_uuid", None)
        if lensnode_uuid is not None:
            attrs["lensnode"] = LensNode.objects.get(uuid=lensnode_uuid)
        lensnode = attrs.get(
            "lensnode",
            getattr(self.instance, "lensnode", None),
        )
        capability = attrs.get(
            "capability",
            getattr(
                self.instance,
                "capability",
                Assistant.Capability.GENERAL_CHAT,
            ),
        )
        routing_mode = attrs.get(
            "routing_mode",
            getattr(
                self.instance,
                "routing_mode",
                Assistant.RoutingMode.DIRECT,
            ),
        )
        if mode is not None:
            if "routing_mode" in attrs and attrs["routing_mode"] != mode:
                raise serializers.ValidationError(
                    {"mode": "mode and routing_mode must match."}
                )
            routing_mode = mode
            attrs["routing_mode"] = mode
        mode_behavior = assistant_mode_for(routing_mode)
        member_uuids = attrs.get("collaboration_member_uuids")
        if mode_behavior.supports_members:
            normalized_capability = mode_behavior.normalize_capability(capability)
            if capability != normalized_capability and mode is None:
                raise serializers.ValidationError(
                    {
                        "capability": (
                            "Smart mode does not expose a direct capability."
                        )
                    }
                )
            if self.instance is not None and self.instance.is_system:
                raise serializers.ValidationError(
                    {"routing_mode": "System Assistants cannot be Smart teams."}
                )
            if member_uuids is None and (
                self.instance is None
                or not self.instance.collaboration_members.exists()
            ):
                raise serializers.ValidationError(
                    {
                        "collaboration_member_uuids": (
                            "At least one collaboration member is required."
                        )
                    }
                )
            self._validate_collaboration_members(member_uuids)
            attrs["capability"] = normalized_capability
            capability = normalized_capability
            attrs["lensnode"] = None
            attrs["selected_dirs"] = []
            attrs["multimodal_model_ref"] = None
            attrs["datasource_bindings"] = []
            attrs["skill_bindings"] = []
            attrs["mcp_bindings"] = []
            attrs["plugin_bindings"] = []
            lensnode = None
        elif member_uuids is not None:
            raise serializers.ValidationError(
                {
                    "collaboration_member_uuids": (
                        "Only smart Assistants may have collaboration members."
                    )
                }
            )
        requires_workspace = (
            mode_behavior.configures_execution_resources
            and capability
            in {
                Assistant.Capability.CODE_ANALYSIS,
                Assistant.Capability.KNOWLEDGE_QA,
            }
        )
        if lensnode is not None and capability not in _task_names(lensnode):
            raise serializers.ValidationError(
                {"capability": "capability is not available on LensNode"}
            )

        selected_dirs = attrs.get(
            "selected_dirs",
            getattr(self.instance, "selected_dirs", []),
        )
        if not requires_workspace:
            attrs["selected_dirs"] = []
            if mode_behavior.requires_skill:
                skill_bindings = attrs.get("skill_bindings")
                if skill_bindings is None and self.instance is not None:
                    has_enabled_skill = self.instance.skill_bindings.filter(
                        enabled=True,
                        skill__enabled=True,
                    ).exists()
                else:
                    enabled_skill_uuids = [
                        binding.get("skill_uuid")
                        for binding in (skill_bindings or [])
                        if binding.get("enabled", True)
                    ]
                    has_enabled_skill = Skill.objects.filter(
                        uuid__in=enabled_skill_uuids,
                        enabled=True,
                    ).exists()
                plugin_bindings = attrs.get("plugin_bindings")
                if plugin_bindings is None and self.instance is not None:
                    has_enabled_plugin = (
                        self.instance.plugin_bindings.filter(
                            enabled=True,
                            connection__status=Connection.Status.ACTIVE,
                            connection__secret_version__status="active",
                            connection__secret_version__material__status=(
                                "active"
                            ),
                        ).exists()
                    )
                else:
                    has_enabled_plugin = any(
                        binding.get("enabled", True)
                        for binding in (plugin_bindings or [])
                    )
                has_enabled_plugin = (
                    has_enabled_plugin
                    or self._has_enabled_plugin_mcp_adapter(attrs)
                )
                if not has_enabled_skill and not has_enabled_plugin:
                    raise serializers.ValidationError(
                        {
                            "skill_bindings": (
                                "general_chat requires at least one enabled "
                                "Skill or Plugin tool"
                            )
                        }
                    )
        elif lensnode is not None:
            validate_selected_dirs(selected_dirs, lensnode)
        elif selected_dirs:
            raise serializers.ValidationError(
                {
                    "selected_dirs": (
                        "Auto-scheduled assistants cannot select node-local "
                        "directories."
                    )
                }
            )
        self._validate_skill_plugin_requirements(attrs)
        self._validate_plugin_tool_uniqueness(attrs)
        settings = attrs.get(
            "settings",
            getattr(self.instance, "settings", {}),
        )
        if isinstance(settings, dict):
            settings = dict(settings)
            settings["datasource_routing"] = "selected"
            attrs["settings"] = settings
            if "retrieval_policy" in settings:
                validate_retrieval_policy(settings.get("retrieval_policy"))
        if "multimodal_model_ref" in attrs:
            reason = validate_vision_model_ref(attrs["multimodal_model_ref"])
            if reason:
                raise serializers.ValidationError(
                    {
                        "multimodal_model_ref": {
                            "code": reason,
                            "message": reason,
                        }
                    }
                )
        return attrs

    def _validate_skill_plugin_requirements(self, attrs):
        """Require selected Plugin tools for enabled Skill dependencies."""

        skill_bindings = attrs.get("skill_bindings")
        if skill_bindings is None and self.instance is not None:
            skills = [
                binding.skill
                for binding in self.instance.skill_bindings.select_related(
                    "skill"
                ).filter(enabled=True, skill__enabled=True)
            ]
        else:
            skill_uuids = [
                binding.get("skill_uuid")
                for binding in (skill_bindings or [])
                if binding.get("enabled", True)
            ]
            skills = list(
                Skill.objects.filter(uuid__in=skill_uuids, enabled=True)
            )

        plugin_bindings = attrs.get("plugin_bindings")
        if plugin_bindings is None and self.instance is not None:
            plugin_bindings = [
                {
                    "connection": binding.connection,
                    "tools": list(binding.tools or []),
                    "enabled": binding.enabled,
                }
                for binding in self.instance.plugin_bindings.select_related(
                    "connection"
                ).all()
            ]

        capabilities_by_plugin = {}
        for binding in plugin_bindings or []:
            if not binding.get("enabled", True):
                continue
            connection = binding["connection"]
            plugin = installed_plugin(connection.plugin_key)
            capabilities_by_plugin.setdefault(plugin.key, set()).update(
                tool.capability
                for tool in plugin.tools
            )
        for adapter in self._plugin_mcp_adapters(attrs):
            connection = adapter.connection
            plugin = installed_plugin(connection.plugin_key)
            selected_tools = set(adapter.tools or [])
            capabilities_by_plugin.setdefault(plugin.key, set()).update(
                tool.capability
                for tool in plugin.tools
                if tool.key in selected_tools
            )

        for skill in skills:
            try:
                requirements = validate_required_plugins(
                    (skill.definition or {}).get("required_plugins")
                )
            except SkillPluginRequirementError as exc:
                raise serializers.ValidationError(
                    {"skill_bindings": str(exc)}
                ) from exc
            for requirement in requirements:
                granted = capabilities_by_plugin.get(
                    requirement["plugin"],
                    set(),
                )
                missing = sorted(
                    set(requirement["capabilities"]).difference(granted)
                )
                if missing:
                    raise serializers.ValidationError(
                        {
                            "plugin_bindings": (
                                f'Skill "{skill.name}" required_plugins '
                                f'dependency "{requirement["plugin"]}" is '
                                "missing capabilities: "
                                f'{", ".join(missing)}.'
                            )
                        }
                    )

    def _has_enabled_plugin_mcp_adapter(self, attrs):
        """Return whether the effective MCP bindings expose Plugin tools."""

        return bool(self._plugin_mcp_adapters(attrs))

    def _validate_plugin_tool_uniqueness(self, attrs):
        """Reject Tool name collisions across native and MCP bindings."""

        plugin_bindings = attrs.get("plugin_bindings")
        if plugin_bindings is None and self.instance is not None:
            plugin_bindings = [
                {
                    "connection": binding.connection,
                    "enabled": binding.enabled,
                }
                for binding in self.instance.plugin_bindings.all()
            ]
        tool_keys = []
        for binding in plugin_bindings or []:
            if not binding.get("enabled", True):
                continue
            connection = binding.get("connection")
            if connection is None:
                continue
            plugin = installed_plugin(connection.plugin_key)
            tool_keys.extend(tool.key for tool in plugin.tools)
        tool_keys.extend(
            key
            for adapter in self._plugin_mcp_adapters(attrs)
            for key in (adapter.tools or [])
        )
        if len(tool_keys) != len(set(tool_keys)):
            raise serializers.ValidationError(
                {
                    "plugin_bindings": (
                        "Plugin Tool names must be unique across native and "
                        "MCP adapter bindings."
                    )
                }
            )

    def _plugin_mcp_adapters(self, attrs):
        """Return valid Plugin adapters from effective Assistant MCP bindings."""

        mcp_bindings = attrs.get("mcp_bindings")
        if mcp_bindings is None and self.instance is not None:
            adapter_ids = self.instance.mcp_bindings.filter(
                enabled=True,
                mcp__enabled=True,
                mcp__transport=MCPServer.Transport.PLUGIN,
            ).values_list("mcp_id", flat=True)
            return list(
                MCPServer.objects.select_related(
                    "connection__secret_version__material"
                ).filter(
                    pk__in=adapter_ids,
                    connection__status=Connection.Status.ACTIVE,
                    connection__secret_version__status="active",
                    connection__secret_version__material__status="active",
                )
            )
        adapter_uuids = [
            binding.get("mcp_uuid")
            for binding in (mcp_bindings or [])
            if binding.get("enabled", True)
        ]
        return list(
            MCPServer.objects.select_related(
                "connection__secret_version__material"
            ).filter(
                uuid__in=adapter_uuids,
                enabled=True,
                transport=MCPServer.Transport.PLUGIN,
                connection__status=Connection.Status.ACTIVE,
                connection__secret_version__status="active",
                connection__secret_version__material__status="active",
            )
        )

    def _validate_collaboration_members(self, member_uuids):
        """Validate Smart Assistant members at the API boundary."""

        if member_uuids is None:
            return
        if not member_uuids:
            raise serializers.ValidationError(
                {
                    "collaboration_member_uuids": (
                        "At least one collaboration member is required."
                    )
                }
            )
        if len(set(member_uuids)) != len(member_uuids):
            raise serializers.ValidationError(
                {"collaboration_member_uuids": "Member UUIDs must be unique."}
            )
        members = list(Assistant.objects.filter(uuid__in=member_uuids))
        if len(members) != len(member_uuids):
            raise serializers.ValidationError(
                {"collaboration_member_uuids": "Assistant member not found."}
            )
        instance_uuid = str(getattr(self.instance, "uuid", ""))
        invalid = [
            member
            for member in members
            if (
                member.is_system
                or member.status != Assistant.Status.ACTIVE
                or member.routing_mode != Assistant.RoutingMode.DIRECT
                or str(member.uuid) == instance_uuid
            )
        ]
        if invalid:
            raise serializers.ValidationError(
                {
                    "collaboration_member_uuids": (
                        "Members must be active direct Assistants."
                    )
                }
            )

    def _sync_bindings(self, assistant, validated_data):
        skill_bindings = validated_data.pop("skill_bindings", None)
        mcp_bindings = validated_data.pop("mcp_bindings", None)
        plugin_bindings = validated_data.pop("plugin_bindings", None)

        binding_groups = [
            bindings
            for bindings in (skill_bindings, mcp_bindings)
            if bindings is not None
        ]
        if binding_groups:
            variable_set_ids = {
                binding["environment_variable_set"].pk
                for bindings in binding_groups
                for binding in bindings
                if binding.get("environment_variable_set") is not None
                and binding.get("environment_values")
            }
            locked_variable_sets = (
                EnvironmentVariableSet.objects.select_for_update().in_bulk(
                    variable_set_ids
                )
                if variable_set_ids
                else {}
            )
            for bindings in binding_groups:
                for binding in bindings:
                    variable_set = binding.get("environment_variable_set")
                    if variable_set is not None:
                        binding["environment_variable_set"] = locked_variable_sets.get(
                            variable_set.pk,
                            variable_set,
                        )

        if skill_bindings is not None:
            assistant.skill_bindings.all().delete()
            for binding in skill_bindings:
                skill = Skill.objects.get(uuid=binding["skill_uuid"])
                variable_set = self._sync_environment_variable_set(
                    assistant,
                    skill,
                    binding,
                )
                AssistantSkill.objects.create(
                    assistant=assistant,
                    skill=skill,
                    environment_variable_set=variable_set,
                    enabled=binding.get("enabled", True),
                    load_config=binding.get("load_config", {}),
                )

        if mcp_bindings is not None:
            assistant.mcp_bindings.all().delete()
            for binding in mcp_bindings:
                mcp = MCPServer.objects.get(uuid=binding["mcp_uuid"])
                variable_set = self._sync_environment_variable_set(
                    assistant,
                    mcp,
                    binding,
                )
                AssistantMCP.objects.create(
                    assistant=assistant,
                    mcp=mcp,
                    environment_variable_set=variable_set,
                    enabled=binding.get("enabled", True),
                    load_config=binding.get("load_config", {}),
                )

        if plugin_bindings is not None:
            assistant.plugin_bindings.all().delete()
            for binding in plugin_bindings:
                AssistantPluginBinding.objects.create(
                    assistant=assistant,
                    connection=binding["connection"],
                    tools=binding["tools"],
                    enabled=binding.get("enabled", True),
                )

    def _sync_environment_variable_set(self, assistant, resource, binding):
        """Apply inline values without mutating another Assistant's set."""

        variable_set = binding.get("environment_variable_set")
        values = binding.get("environment_values") or {}
        if not values:
            return variable_set

        merged_values = variable_set.get_values() if variable_set else {}
        merged_values.update(values)
        if (
            variable_set
            and not variable_set.skill_bindings.exists()
            and not variable_set.mcp_bindings.exists()
        ):
            variable_set.set_values(merged_values)
            variable_set.save(update_fields=["encrypted_values", "updated_at"])
            return variable_set

        requested_name = binding.get("environment_variable_set_name") or ""
        base_name = requested_name or f"{assistant.name} · {resource.name}"
        description = variable_set.description if variable_set else ""
        variable_set = EnvironmentVariableSet(
            name=self._next_environment_variable_set_name(base_name),
            description=description,
            enabled=True,
        )
        variable_set.set_values(merged_values)
        variable_set.save()
        return variable_set

    @staticmethod
    def _next_environment_variable_set_name(base_name):
        """Return an available variable-set name within the model limit."""

        normalized_base = str(base_name or "Environment").strip()
        normalized_base = normalized_base[:160] or "Environment"
        if not EnvironmentVariableSet.objects.filter(name=normalized_base).exists():
            return normalized_base
        suffix = 2
        while True:
            suffix_text = f" ({suffix})"
            candidate = normalized_base[: 160 - len(suffix_text)] + suffix_text
            if not EnvironmentVariableSet.objects.filter(name=candidate).exists():
                return candidate
            suffix += 1

    def _sync_access_grants(self, assistant, grants):
        if grants is None:
            return
        request = self.context.get("request")
        granted_by = getattr(request, "user", None)
        if granted_by is not None and not granted_by.is_authenticated:
            granted_by = None
        assistant.access_grants.all().delete()
        for grant in grants:
            if grant["type"] == "group":
                group = Group.objects.filter(pk=grant["id"]).first()
                if group is None:
                    raise serializers.ValidationError(
                        {"access_grants": f"Group {grant['id']} not found."}
                    )
                AssistantAccess.objects.create(
                    assistant=assistant,
                    group=group,
                    granted_by=granted_by,
                )
            else:
                user = get_user_model().objects.filter(pk=grant["id"]).first()
                if user is None:
                    raise serializers.ValidationError(
                        {"access_grants": f"User {grant['id']} not found."}
                    )
                AssistantAccess.objects.create(
                    assistant=assistant,
                    user=user,
                    granted_by=granted_by,
                )

    def _set_collaboration_members(self, assistant, member_uuids):
        """Persist collaboration members resolved by their public UUIDs."""

        members = Assistant.objects.filter(uuid__in=member_uuids)
        assistant.collaboration_members.set(members)

    @transaction.atomic
    def create(self, validated_data):
        """Create assistant and optional bindings."""

        collaboration_member_uuids = validated_data.pop(
            "collaboration_member_uuids", None
        )
        skill_bindings = validated_data.pop("skill_bindings", None)
        mcp_bindings = validated_data.pop("mcp_bindings", None)
        plugin_bindings = validated_data.pop("plugin_bindings", None)
        access_grants = validated_data.pop("access_grants", None)
        workspace_guide = validated_data.pop("workspace_guide", None)
        datasource_bindings = validated_data.pop("datasource_bindings", None)
        assistant = Assistant.objects.create(**validated_data)
        replace_datasource_bindings(assistant, datasource_bindings)
        self._sync_bindings(
            assistant,
            {
                "skill_bindings": skill_bindings,
                "mcp_bindings": mcp_bindings,
                "plugin_bindings": plugin_bindings,
            },
        )
        self._sync_access_grants(assistant, access_grants)
        sync_workspace_guide_skill(assistant, workspace_guide)
        if assistant.mode_handler.supports_members:
            self._set_collaboration_members(
                assistant,
                collaboration_member_uuids or [],
            )
        check_assistant_model_refs(assistant)
        refresh_routing_description(assistant)
        return assistant

    @transaction.atomic
    def update(self, instance, validated_data):
        """Update assistant and optional bindings."""

        instance = Assistant.objects.select_for_update().get(pk=instance.pk)
        datasource_bindings = validated_data.pop("datasource_bindings", None)
        replace_datasource_bindings(instance, datasource_bindings)
        was_smart = instance.mode_handler.supports_members
        collaboration_member_uuids = validated_data.pop(
            "collaboration_member_uuids", None
        )
        access_grants = validated_data.pop("access_grants", None)
        workspace_guide = validated_data.pop("workspace_guide", None)
        self._sync_bindings(instance, validated_data)
        assistant = super().update(instance, validated_data)
        self._sync_access_grants(assistant, access_grants)
        sync_workspace_guide_skill(assistant, workspace_guide)
        if assistant.mode_handler.supports_members:
            if collaboration_member_uuids is not None:
                self._set_collaboration_members(
                    assistant,
                    collaboration_member_uuids,
                )
        elif collaboration_member_uuids is not None or was_smart:
            assistant.collaboration_members.clear()
        check_assistant_model_refs(assistant)
        refresh_routing_description(assistant)
        return assistant


class PluginInvocationSerializer(serializers.ModelSerializer):
    """Read-only, secret-free audit representation for Plugin executions."""

    snapshot_uuid = serializers.UUIDField(source="snapshot.uuid")
    connection_uuid = serializers.UUIDField(source="connection.uuid")
    connection_name = serializers.CharField(source="connection.name")
    datasource_uuid = serializers.SerializerMethodField()
    run_uuid = serializers.SerializerMethodField()
    actor_username = serializers.CharField(
        source="actor.username",
        allow_null=True,
    )
    lensnode_uuid = serializers.UUIDField(source="lensnode.uuid")
    lensnode_name = serializers.CharField(source="lensnode.name")

    class Meta:
        model = PluginInvocation
        fields = [
            "uuid",
            "snapshot_uuid",
            "kind",
            "plugin_key",
            "tool_key",
            "capability",
            "connection_uuid",
            "connection_name",
            "datasource_uuid",
            "run_uuid",
            "actor_username",
            "lensnode_uuid",
            "lensnode_name",
            "resource_summary",
            "status",
            "materialized_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_datasource_uuid(self, invocation):
        """Return the optional datasource identity."""

        return str(invocation.datasource.uuid) if invocation.datasource else None

    def get_run_uuid(self, invocation):
        """Return the optional Run identity."""

        return str(invocation.run.uuid) if invocation.run else None


class ConnectionSerializer(serializers.ModelSerializer):
    """Admin serializer for reusable Plugin connections."""

    endpoint = serializers.URLField(required=False, allow_blank=True)
    secret_value = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=False,
    )
    has_secret = serializers.SerializerMethodField()
    secret_hint = serializers.SerializerMethodField()
    secret_version_uuid = serializers.SerializerMethodField()
    datasource_count = serializers.SerializerMethodField()
    assistant_count = serializers.SerializerMethodField()

    class Meta:
        model = Connection
        fields = [
            "uuid",
            "name",
            "plugin_key",
            "endpoint",
            "config",
            "allowed_scope",
            "secret_value",
            "has_secret",
            "secret_hint",
            "secret_version_uuid",
            "datasource_count",
            "assistant_count",
            "status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "uuid",
            "has_secret",
            "secret_hint",
            "secret_version_uuid",
            "datasource_count",
            "assistant_count",
            "created_at",
            "updated_at",
        ]

    def get_has_secret(self, connection):
        """Return whether the current connection has secret material."""

        version = connection.secret_version
        return bool(version and version.encrypted_value)

    def get_secret_hint(self, connection):
        """Return a non-recoverable display hint for the stored secret."""

        version = connection.secret_version
        if version is None or not version.encrypted_value:
            return ""
        value = version.get_value()
        if len(value) < 8:
            return "••••••••"
        return f"••••••••{value[-4:]}"

    def get_secret_version_uuid(self, connection):
        """Return the opaque SecretVersion identity for audit views."""

        if connection.secret_version is None:
            return None
        return str(connection.secret_version.uuid)

    def get_datasource_count(self, connection):
        """Return how many datasources use this connection."""

        count = getattr(connection, "datasource_usage_count", None)
        return count if count is not None else connection.datasources.count()

    def get_assistant_count(self, connection):
        """Return how many Assistants use this connection."""

        count = getattr(connection, "assistant_usage_count", None)
        return (
            count
            if count is not None
            else connection.assistant_bindings.count()
        )

    def validate(self, attrs):
        """Validate provider, endpoint, and non-secret JSON configuration."""

        plugin_key = attrs.get(
            "plugin_key",
            getattr(self.instance, "plugin_key", ""),
        )
        config = attrs.get(
            "config",
            getattr(self.instance, "config", {}),
        )
        allowed_scope = attrs.get(
            "allowed_scope",
            getattr(self.instance, "allowed_scope", {}),
        )
        if not isinstance(config, dict):
            raise serializers.ValidationError(
                {"config": "config must be an object"}
            )
        if not isinstance(allowed_scope, dict):
            raise serializers.ValidationError(
                {"allowed_scope": "allowed_scope must be an object"}
            )
        _validate_plugin_json(config, "config")
        _validate_plugin_json(allowed_scope, "allowed_scope")
        try:
            installed_plugin(plugin_key)
            provider = get_datasource_provider(plugin_key)
        except PluginRegistryError as exc:
            raise serializers.ValidationError({"plugin_key": str(exc)})
        except DatasourceProviderError as exc:
            raise serializers.ValidationError({"plugin_key": str(exc)})
        try:
            attrs["endpoint"] = provider.validate_connection(
                attrs.get("endpoint", getattr(self.instance, "endpoint", "")),
                config,
            )
        except DatasourceProviderError as exc:
            raise serializers.ValidationError({"endpoint": str(exc)})
        try:
            attrs["allowed_scope"] = provider.validate_connection_scope(
                allowed_scope
            )
        except DatasourceProviderError as exc:
            raise serializers.ValidationError({"allowed_scope": str(exc)})
        status_value = attrs.get(
            "status",
            getattr(self.instance, "status", Connection.Status.ACTIVE),
        )
        secret_value = attrs.get("secret_value", "")
        current_version = getattr(self.instance, "secret_version", None)
        if (
            status_value == Connection.Status.ACTIVE
            and not secret_value
            and (
                current_version is None
                or current_version.status != "active"
                or current_version.material.status != "active"
                or not current_version.encrypted_value
            )
        ):
            raise serializers.ValidationError({
                "secret_value": "active connection requires a secret"
            })
        return attrs

    def create(self, validated_data):
        """Create a connection and optionally its first encrypted version."""

        secret_value = validated_data.pop("secret_value", "")
        with transaction.atomic():
            connection = Connection.objects.create(**validated_data)
            if secret_value:
                connection.secret_version = _create_secret_version(
                    connection.name,
                    secret_value,
                )
                connection.save(update_fields=["secret_version", "updated_at"])
        return connection

    def update(self, instance, validated_data):
        """Update metadata and rotate SecretVersion when supplied."""

        secret_value = validated_data.pop("secret_value", "")
        with transaction.atomic():
            for field, value in validated_data.items():
                setattr(instance, field, value)
            if secret_value:
                material = (
                    instance.secret_version.material
                    if instance.secret_version is not None
                    else None
                )
                instance.secret_version = _create_secret_version(
                    instance.name,
                    secret_value,
                    material=material,
                )
            instance.save()
        return instance


def _create_secret_version(name, value, material=None):
    """Create encrypted SecretMaterial and SecretVersion records."""

    material = material or SecretMaterial.objects.create(name=f"{name} secret")
    version = SecretVersion(material=material, status="active")
    version.set_value(value)
    version.save()
    return version


def _validate_plugin_json(value, field_name):
    """Reject credential-shaped fields in persisted Plugin configuration."""

    forbidden = {
        "access_token",
        "api_key",
        "app_secret",
        "authorization",
        "password",
        "private_key",
        "secret",
        "token",
    }
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).lower() in forbidden:
                raise serializers.ValidationError(
                    {field_name: "secret fields must use secret_value"}
                )
            _validate_plugin_json(nested, field_name)
    elif isinstance(value, list):
        for nested in value:
            _validate_plugin_json(nested, field_name)


class DataSourceItemSerializer(serializers.ModelSerializer):
    """Serialize an independently managed datasource child."""

    class Meta:
        model = DataSourceItem
        fields = (
            "uuid", "name", "source_type", "config", "storage_key",
            "status", "current_version", "created_at", "updated_at",
        )
        read_only_fields = ("uuid", "created_at", "updated_at")
        extra_kwargs = {
            "storage_key": {"read_only": True},
        }


class DataSourceVersionSerializer(serializers.ModelSerializer):
    """Serialize immutable processed datasource versions."""

    class Meta:
        model = DataSourceVersion
        fields = (
            "uuid", "version", "storage_key", "status", "checksum",
            "created_at", "updated_at",
        )
        read_only_fields = fields


class DataSourceSerializer(serializers.ModelSerializer):
    """Datasource serializer."""

    lensnode_uuid = serializers.UUIDField(write_only=True, required=False)
    credential_uuid = serializers.UUIDField(
        write_only=True,
        required=False,
        allow_null=True,
    )
    connection_uuid = serializers.UUIDField(
        write_only=True,
        required=False,
        allow_null=True,
    )
    connection = serializers.UUIDField(source="connection.uuid", read_only=True)
    connection_name = serializers.CharField(
        source="connection.name",
        read_only=True,
    )
    connection_endpoint = serializers.CharField(
        source="connection.endpoint",
        read_only=True,
    )
    lensnode = serializers.UUIDField(source="lensnode.uuid", read_only=True)
    lensnode_name = serializers.CharField(
        source="lensnode.name",
        read_only=True,
    )
    credential = serializers.UUIDField(
        source="credential.uuid",
        read_only=True,
    )
    credential_configured = serializers.SerializerMethodField()
    current_sync = serializers.SerializerMethodField()
    sync_state = serializers.SerializerMethodField()

    def validate(self, attrs):
        """Validate datasource config by source type."""

        source_type = attrs.get(
            "source_type",
            getattr(self.instance, "source_type", ""),
        )
        config = attrs.get("config", getattr(self.instance, "config", {}))
        sync_policy = attrs.get(
            "sync_policy",
            getattr(self.instance, "sync_policy", {}),
        )
        target_path = attrs.get(
            "target_path",
            getattr(self.instance, "target_path", ""),
        )
        lensnode_uuid = attrs.pop("lensnode_uuid", None)
        if lensnode_uuid is not None:
            try:
                attrs["lensnode"] = LensNode.objects.get(uuid=lensnode_uuid)
            except LensNode.DoesNotExist:
                raise serializers.ValidationError(
                    {"lensnode_uuid": "LensNode does not exist"}
                )
        lensnode = attrs.get(
            "lensnode",
            getattr(self.instance, "lensnode", None),
        )
        credential_uuid = attrs.pop("credential_uuid", None)
        if "credential_uuid" in self.initial_data and credential_uuid is None:
            attrs["credential"] = None
        elif credential_uuid is not None:
            try:
                attrs["credential"] = DataSourceCredential.objects.get(
                    uuid=credential_uuid
                )
            except DataSourceCredential.DoesNotExist:
                raise serializers.ValidationError(
                    {"credential_uuid": "Credential does not exist"}
                )
        connection_uuid = attrs.pop("connection_uuid", None)
        if "connection_uuid" in self.initial_data and connection_uuid is None:
            attrs["connection"] = None
        elif connection_uuid is not None:
            try:
                attrs["connection"] = Connection.objects.get(uuid=connection_uuid)
            except Connection.DoesNotExist:
                raise serializers.ValidationError(
                    {"connection_uuid": "Connection does not exist"}
                )

        if not isinstance(config, dict):
            raise serializers.ValidationError({"config": "config must be an object"})
        if not isinstance(sync_policy, dict):
            raise serializers.ValidationError(
                {"sync_policy": "sync_policy must be an object"}
            )

        _validate_datasource_config_secret_fields(config)
        _validate_sync_policy(sync_policy)
        if lensnode is not None:
            try:
                validate_datasource_lensnode(lensnode)
                attrs["target_path"] = normalize_workspace_target_path(
                    target_path,
                    lensnode.workspace_path,
                )
                _validate_unique_datasource_target_path(
                    attrs["target_path"],
                    lensnode,
                    self.instance,
                    source_type,
                )
            except (DataSourcePathError, DataSourceDispatchError) as exc:
                raise serializers.ValidationError({"target_path": str(exc)})

        credential = (
            attrs["credential"]
            if "credential" in attrs
            else getattr(self.instance, "credential", None)
        )
        connection = (
            attrs["connection"]
            if "connection" in attrs
            else getattr(self.instance, "connection", None)
        )
        datasource_config = attrs.get(
            "datasource_config",
            getattr(self.instance, "datasource_config", {}),
        )
        plugin_key = attrs.get(
            "plugin_key",
            getattr(self.instance, "plugin_key", ""),
        )
        _validate_datasource_config_secret_fields(datasource_config)
        if connection is None and (plugin_key or datasource_config):
            raise serializers.ValidationError(
                {
                    "connection_uuid": (
                        "Plugin datasource configuration requires a "
                        "Connection"
                    )
                }
            )
        if connection is not None:
            if source_type == DataSource.SourceType.MANAGED_WORKSPACE:
                raise serializers.ValidationError(
                    {"connection_uuid": "Managed workspace does not use connections"}
                )
            if connection.status != Connection.Status.ACTIVE:
                raise serializers.ValidationError(
                    {"connection_uuid": "Plugin Connection is disabled"}
                )
            secret_version = connection.secret_version
            if (
                secret_version is None
                or secret_version.status != "active"
                or secret_version.material.status != "active"
                or not secret_version.encrypted_value
            ):
                raise serializers.ValidationError(
                    {
                        "connection_uuid": (
                            "Plugin Connection secret is unavailable"
                        )
                    }
                )
            if plugin_key and plugin_key != connection.plugin_key:
                raise serializers.ValidationError(
                    {"plugin_key": "Plugin key differs from connection"}
                )
            plugin_key = connection.plugin_key
            try:
                plugin = installed_plugin(plugin_key)
                if plugin.datasource is None:
                    raise serializers.ValidationError(
                        {
                            "plugin_key": (
                                "Plugin does not support datasources"
                            )
                        }
                    )
                provider = get_datasource_provider(plugin_key)
            except (DatasourceProviderError, PluginRegistryError) as exc:
                raise serializers.ValidationError({"plugin_key": str(exc)})
            try:
                provider.validate_datasource_source_type(source_type)
            except DatasourceProviderError as exc:
                raise serializers.ValidationError({"source_type": str(exc)})
            try:
                provider.validate_connection(
                    connection.endpoint,
                    connection.config,
                )
                attrs["datasource_config"] = provider.validate_datasource_config(
                    connection.allowed_scope,
                    datasource_config,
                )
            except DatasourceProviderError as exc:
                raise serializers.ValidationError({"datasource_config": str(exc)})
            attrs["plugin_key"] = plugin_key
            attrs["credential"] = None
            config = {}
            attrs["config"] = {}
            credential = None
        if (
            connection is None
            and source_type == DataSource.SourceType.MANAGED_WORKSPACE
        ):
            if credential is not None:
                raise serializers.ValidationError(
                    {"credential_uuid": ("Managed workspace does not use credentials")}
                )
            if config:
                raise serializers.ValidationError(
                    {"config": ("Managed workspace does not use connection config")}
                )
            if sync_policy:
                raise serializers.ValidationError(
                    {"sync_policy": ("Managed workspace does not use sync policy")}
                )
            if self._managed_workspace_path_changed(
                lensnode,
                attrs["target_path"],
                source_type,
            ):
                self._validate_managed_workspace_path(
                    attrs,
                    lensnode,
                    source_type,
                )
            attrs["credential"] = None
            attrs["config"] = {}
            attrs["sync_policy"] = {}
        elif connection is None and source_type == DataSource.SourceType.GIT:
            _validate_datasource_credential_type(
                credential,
                {
                    DataSourceCredential.AuthType.HTTPS_TOKEN,
                    DataSourceCredential.AuthType.NONE,
                },
            )
            _validate_git_config(
                config,
                self.instance,
                credential,
            )
        elif connection is None and source_type == DataSource.SourceType.FEISHU:
            _validate_datasource_credential_type(
                credential,
                DataSourceCredential.AuthType.FEISHU_APP,
            )
            _validate_feishu_config(
                config,
                self.instance,
                credential,
            )
        elif connection is None:
            raise serializers.ValidationError(
                {"source_type": "Unsupported datasource source_type"}
            )

        if (
            source_type != DataSource.SourceType.MANAGED_WORKSPACE
            and self.instance is not None
            and self.instance.source_type == DataSource.SourceType.MANAGED_WORKSPACE
        ):
            attrs["availability_status"] = DataSource.AvailabilityStatus.UNKNOWN
            attrs["availability_checked_at"] = None
            attrs["availability_message"] = ""

        return attrs

    def _managed_workspace_path_changed(
        self,
        lensnode,
        target_path,
        source_type,
    ):
        """Return whether managed workspace availability must be checked."""

        if self.instance is None:
            return True
        if self.instance.source_type != source_type:
            return True
        if self.instance.lensnode_id != lensnode.pk:
            return True
        try:
            current_path = normalize_workspace_target_path(
                self.instance.target_path,
                lensnode.workspace_path,
            )
        except DataSourcePathError:
            return True
        return current_path != target_path

    @staticmethod
    def _validate_managed_workspace_path(attrs, lensnode, source_type):
        """Require an existing directory for a managed workspace source."""

        try:
            result = check_datasource_path(
                lensnode,
                attrs["target_path"],
                source_type,
            )
        except (DataSourcePathError, DataSourceDispatchError) as exc:
            raise serializers.ValidationError({"target_path": str(exc)})
        if not result.get("exists") or not result.get("is_directory"):
            raise serializers.ValidationError(
                {"target_path": "MANAGED_WORKSPACE_DIRECTORY_REQUIRED"}
            )
        attrs["availability_status"] = DataSource.AvailabilityStatus.AVAILABLE
        attrs["availability_checked_at"] = timezone.now()
        attrs["availability_message"] = str(result.get("message") or "")

    def get_credential_configured(self, datasource):
        """Return whether a datasource has a stored credential."""

        credential = getattr(datasource, "credential", None)
        return bool(credential and credential.has_secret)

    def get_current_sync(self, datasource):
        """Return the latest running datasource sync task, if any."""

        current_sync_by_uuid = self.context.get(
            "datasource_current_sync_by_uuid"
        )
        if current_sync_by_uuid is not None:
            task = current_sync_by_uuid.get(str(datasource.uuid))
        else:
            from agentcore_task.adapters.django.models import TaskExecution
            from agentcore_task.constants import TaskStatus

            task = (
                TaskExecution.objects.filter(
                    module__in=[
                        "lens_datasource",
                        "lens_datasource_conversion",
                    ],
                    metadata__datasource_uuid=str(datasource.uuid),
                    status__in=[
                        TaskStatus.PENDING,
                        *TaskStatus.get_running_statuses(),
                        "CANCELLING",
                    ],
                )
                .order_by("-created_at")
                .first()
            )
        if task is None:
            return None
        return {
            "id": task.id,
            "task_id": task.task_id,
            "task_name": task.task_name,
            "status": task.status,
            "started_at": task.started_at,
            "created_at": task.created_at,
            "progress_step": (task.metadata or {}).get("progress_step", ""),
            "progress_message": (task.metadata or {}).get(
                "progress_message",
                "",
            ),
            "progress_percent": (task.metadata or {}).get(
                "progress_percent",
                None,
            ),
            "phase": (task.metadata or {}).get("phase", ""),
            "overall_progress_percent": (task.metadata or {}).get(
                "overall_progress_percent",
                None,
            ),
            "phase_progress": (task.metadata or {}).get(
                "phase_progress",
                {},
            ),
            "progress_counts": (task.metadata or {}).get(
                "progress_counts",
                {},
            ),
            "last_substantive_progress_at": (task.metadata or {}).get(
                "last_substantive_progress_at",
                None,
            ),
        }

    def get_sync_state(self, datasource):
        """Return datasource sync status independent from enabled state."""

        from .periodic_tasks import estimate_datasource_next_run

        sync_state_by_uuid = self.context.get(
            "datasource_sync_state_by_uuid"
        )
        if sync_state_by_uuid is not None:
            record = sync_state_by_uuid.get(str(datasource.uuid))
        else:
            record = ScheduledTask.objects.filter(
                task_type=ScheduledTask.TaskType.SOURCE_SYNC,
                target_type="datasource",
                target_id=datasource.uuid,
            ).first()
        if record is None:
            return {
                "enabled": datasource.status != DataSource.Status.DISABLED,
                "last_status": "",
                "last_error": datasource.last_error,
                "last_run_at": None,
                "last_metrics": {},
                "next_run_at": None,
            }
        return {
            "enabled": record.enabled,
            "last_status": record.last_status or "",
            "last_error": record.last_error or datasource.last_error,
            "last_run_at": record.last_run_at,
            "last_metrics": record.last_metrics or {},
            "next_run_at": estimate_datasource_next_run(datasource, record),
        }

    def to_representation(self, instance):
        """Return datasource data without plaintext credential values."""

        data = super().to_representation(instance)
        config = dict(data.get("config") or {})
        config.pop("access_token", None)
        config.pop("app_id", None)
        config.pop("app_secret", None)
        config.pop("app_token", None)
        data["config"] = config
        return data

    def create(self, validated_data):
        """Create a datasource bound to reusable credentials."""

        return DataSource.objects.create(**validated_data)

    def update(self, instance, validated_data):
        """Update datasource metadata and credential binding."""

        return super().update(instance, validated_data)

    class Meta:
        model = DataSource
        fields = [
            "uuid",
            "name",
            "source_type",
            "lensnode",
            "lensnode_uuid",
            "lensnode_name",
            "credential",
            "credential_uuid",
            "connection",
            "connection_uuid",
            "connection_name",
            "connection_endpoint",
            "plugin_key",
            "datasource_config",
            "config",
            "credential_configured",
            "current_sync",
            "sync_state",
            "sync_policy",
            "target_path",
            "last_synced_at",
            "last_error",
            "availability_status",
            "availability_checked_at",
            "availability_message",
            "last_conversion_status",
            "last_conversion_at",
            "status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "uuid",
            "credential",
            "credential_configured",
            "current_sync",
            "sync_state",
            "last_error",
            "availability_status",
            "availability_checked_at",
            "availability_message",
            "last_conversion_status",
            "last_conversion_at",
            "created_at",
            "updated_at",
        ]
        extra_kwargs = {
            "target_path": {
                "allow_blank": True,
                "required": False,
            },
        }


class DataSourceConversionRequestSerializer(serializers.Serializer):
    """Validate one explicit managed workspace conversion request."""

    conversion = serializers.JSONField(required=False)
    force = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        """Apply the shared datasource conversion policy contract."""

        conversion = attrs.get("conversion")
        if conversion is None:
            conversion = {"document": True}
        _validate_conversion_policy(conversion, field_name="conversion")
        if not any(
            conversion.get(key) for key in ["document", "image", "embedded_image"]
        ):
            raise serializers.ValidationError(
                {"conversion": ("At least one conversion type must be enabled")}
            )
        attrs["conversion"] = conversion
        return attrs


def _validate_datasource_config_secret_fields(config):
    """Reject secret-like config fields outside the credential input."""

    forbidden_keys = {
        "access_token",
        "app_id",
        "app_secret",
        "app_token",
        "password",
        "private_key",
        "secret",
        "token",
    }
    for key, value in config.items():
        if key in forbidden_keys:
            raise serializers.ValidationError(
                {"config": "secret fields must be stored as credentials"}
            )
        if isinstance(value, dict):
            _validate_datasource_config_secret_fields(value)


class DataSourceCredentialSerializer(serializers.ModelSerializer):
    """Credential serializer that never exposes plaintext secrets."""

    secret = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
    )
    app_id = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
    )
    app_secret = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
    )
    folder_url = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
    )
    folder_token = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
    )
    organization_url = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
    )
    has_secret = serializers.BooleanField(read_only=True)
    datasource_count = serializers.SerializerMethodField()
    datasource_bindings = serializers.SerializerMethodField()
    masked_app_id = serializers.SerializerMethodField()
    masked_secret = serializers.SerializerMethodField()
    scope_summary = serializers.SerializerMethodField()

    class Meta:
        model = DataSourceCredential
        fields = [
            "uuid",
            "name",
            "provider",
            "auth_type",
            "endpoint_url",
            "sync_scope",
            "scope_config",
            "secret",
            "app_id",
            "app_secret",
            "folder_url",
            "folder_token",
            "organization_url",
            "has_secret",
            "datasource_count",
            "datasource_bindings",
            "masked_app_id",
            "masked_secret",
            "scope_summary",
            "validation_status",
            "validation_message",
            "validated_at",
            "last_used_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "uuid",
            "has_secret",
            "datasource_count",
            "datasource_bindings",
            "masked_app_id",
            "masked_secret",
            "scope_summary",
            "validation_status",
            "validation_message",
            "validated_at",
            "last_used_at",
            "created_at",
            "updated_at",
        ]

    def get_datasource_count(self, credential):
        """Return how many datasources reference this credential."""

        return credential.datasources.count()

    def get_datasource_bindings(self, credential):
        """Return datasources currently bound to this credential."""

        return [
            {
                "uuid": str(datasource.uuid),
                "name": datasource.name,
                "source_type": datasource.source_type,
                "status": datasource.status,
            }
            for datasource in credential.datasources.all()
        ]

    def get_masked_app_id(self, credential):
        """Return Feishu App ID without exposing App Secret."""

        if (
            credential.auth_type == DataSourceCredential.AuthType.FEISHU_APP
            and credential.has_secret
        ):
            app_id, _, _ = credential.get_secret().partition(":")
            return app_id
        return ""

    def get_masked_secret(self, credential):
        """Return a placeholder for encrypted credential secret."""

        return "********" if credential.has_secret else ""

    def get_scope_summary(self, credential):
        """Return a compact credential scope summary."""

        config = credential.scope_config or {}
        return {
            "endpoint_url": credential.endpoint_url,
            "sync_scope": credential.sync_scope,
            "folder_url": config.get("folder_url") or "",
            "folder_token": config.get("folder_token") or "",
            "organization_url": config.get("organization_url") or "",
        }

    def validate(self, attrs):
        """Validate secret inputs by credential auth type."""

        auth_type = attrs.get(
            "auth_type",
            getattr(self.instance, "auth_type", ""),
        )
        provider = attrs.get(
            "provider",
            getattr(self.instance, "provider", ""),
        )
        endpoint_url = str(
            attrs.get(
                "endpoint_url",
                getattr(self.instance, "endpoint_url", ""),
            )
            or ""
        ).strip()
        scope_config = dict(
            attrs.get(
                "scope_config",
                getattr(self.instance, "scope_config", {}),
            )
            or {}
        )
        folder_url = str(attrs.pop("folder_url", "") or "").strip()
        folder_token = str(attrs.pop("folder_token", "") or "").strip()
        organization_url = str(attrs.pop("organization_url", "") or "").strip()
        if folder_url:
            scope_config["folder_url"] = folder_url
        if folder_token:
            scope_config["folder_token"] = folder_token
        if organization_url:
            scope_config["organization_url"] = organization_url
        attrs["scope_config"] = scope_config
        if provider == DataSourceCredential.Provider.GITHUB and not endpoint_url:
            attrs["endpoint_url"] = "https://github.com"
        elif provider == DataSourceCredential.Provider.GITLAB and not endpoint_url:
            attrs["endpoint_url"] = "https://gitlab.com"
        elif provider == DataSourceCredential.Provider.FEISHU and not endpoint_url:
            attrs["endpoint_url"] = "https://open.feishu.cn"
        elif endpoint_url:
            attrs["endpoint_url"] = endpoint_url

        has_existing = bool(self.instance and self.instance.has_secret)
        has_secret = bool(str(attrs.get("secret") or "").strip())
        has_app_id = bool(str(attrs.get("app_id") or "").strip())
        has_app_secret = bool(str(attrs.get("app_secret") or "").strip())
        has_feishu = bool(has_app_id and has_app_secret)
        auth_type_changed = bool(self.instance and auth_type != self.instance.auth_type)
        if auth_type == DataSourceCredential.AuthType.FEISHU_APP:
            if provider != DataSourceCredential.Provider.FEISHU:
                raise serializers.ValidationError(
                    {"provider": "feishu app credential requires feishu provider"}
                )
            folder_url_value = scope_config.get("folder_url") or ""
            if folder_url_value and not _is_feishu_drive_folder_url(folder_url_value):
                raise serializers.ValidationError(
                    {
                        "folder_url": (
                            "Feishu URL must be a Drive folder URL, for "
                            "example https://xxx.feishu.cn/drive/folder/..."
                        )
                    }
                )
            attrs["sync_scope"] = attrs.get("sync_scope") or "feishu_folder"
            if has_app_id != has_app_secret:
                raise serializers.ValidationError(
                    {"app_secret": "app_id and app_secret must be submitted together"}
                )
            if not has_feishu and (not has_existing or auth_type_changed):
                raise serializers.ValidationError(
                    {"app_secret": "app_id and app_secret are required"}
                )
        elif auth_type not in {
            DataSourceCredential.AuthType.HTTPS_TOKEN,
            DataSourceCredential.AuthType.NONE,
        }:
            raise serializers.ValidationError(
                {"auth_type": "credential auth_type is not supported"}
            )
        elif provider not in {
            DataSourceCredential.Provider.GITHUB,
            DataSourceCredential.Provider.GITLAB,
            DataSourceCredential.Provider.GENERIC,
        }:
            raise serializers.ValidationError(
                {"provider": "git credential provider must be github or gitlab"}
            )
        elif (
            auth_type == DataSourceCredential.AuthType.HTTPS_TOKEN
            and not has_secret
            and (not has_existing or auth_type_changed)
        ):
            raise serializers.ValidationError({"secret": "secret is required"})
        if auth_type in {
            DataSourceCredential.AuthType.HTTPS_TOKEN,
            DataSourceCredential.AuthType.NONE,
        }:
            attrs["sync_scope"] = attrs.get("sync_scope") or "service"
        return attrs

    def create(self, validated_data):
        """Create a credential and encrypt the supplied secret."""

        secret = _credential_secret_from_validated_data(validated_data)
        credential = DataSourceCredential(**validated_data)
        if secret:
            credential.set_secret(secret)
        credential.save()
        return credential

    def update(self, instance, validated_data):
        """Update metadata and optionally replace the encrypted secret."""

        secret = _credential_secret_from_validated_data(validated_data)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        if (
            instance.auth_type == DataSourceCredential.AuthType.NONE
            and instance.encrypted_secret
        ):
            instance.encrypted_secret = ""
        if secret:
            instance.set_secret(secret)
        instance.save()
        return instance


def _credential_secret_from_validated_data(validated_data):
    """Remove write-only secret fields and return the secret payload."""

    secret = str(validated_data.pop("secret", "") or "").strip()
    app_id = str(validated_data.pop("app_id", "") or "").strip()
    app_secret = str(validated_data.pop("app_secret", "") or "").strip()
    if app_id or app_secret:
        return f"{app_id}:{app_secret}"
    return secret


def _is_feishu_drive_folder_url(value):
    """Return whether a URL points to a Feishu Drive folder."""

    parsed = parse.urlsplit(str(value or "").strip())
    if parsed.scheme not in {"http", "https"}:
        return False
    if not parsed.netloc.endswith(".feishu.cn"):
        return False
    parts = [part for part in parsed.path.split("/") if part]
    return len(parts) >= 3 and parts[0] == "drive" and parts[1] == "folder"


def _credential_provider(repo_url):
    """Infer credential provider from a Git repository URL."""

    value = str(repo_url or "").lower()
    if "github.com" in value:
        return DataSourceCredential.Provider.GITHUB
    if "gitlab" in value:
        return DataSourceCredential.Provider.GITLAB
    return DataSourceCredential.Provider.GENERIC


def _validate_sync_policy(sync_policy):
    """Validate datasource schedule and conversion policy."""

    _validate_conversion_policy(sync_policy.get("conversion") or {})
    mode = sync_policy.get("mode") or "interval"
    if mode not in {"interval", "crontab"}:
        raise serializers.ValidationError(
            {"sync_policy": "mode must be interval or crontab"}
        )
    if mode == "crontab":
        _validate_cron_expression(sync_policy.get("cron"))
        timezone = sync_policy.get("timezone")
        if timezone is not None and not isinstance(timezone, str):
            raise serializers.ValidationError(
                {"sync_policy": "timezone must be a string"}
            )
        return
    interval = sync_policy.get("interval_seconds")
    if interval is not None and (not isinstance(interval, int) or interval <= 0):
        raise serializers.ValidationError(
            {"sync_policy": "interval_seconds must be a positive integer"}
        )


def _validate_conversion_policy(conversion, field_name="sync_policy"):
    """Validate datasource conversion settings."""

    if not isinstance(conversion, dict):
        raise serializers.ValidationError({field_name: "conversion must be an object"})
    for key in [
        "document",
        "image",
        "embedded_image",
        "pdf_extract_images",
        "pdf_extract_images_on_text_pages",
        "pdf_render_scanned_pages",
    ]:
        value = conversion.get(key)
        if value is not None and not isinstance(value, bool):
            raise serializers.ValidationError(
                {field_name: f"conversion.{key} must be a boolean"}
            )
    if conversion.get("embedded_image") and not conversion.get("document"):
        raise serializers.ValidationError(
            {field_name: ("conversion.embedded_image requires " "conversion.document")}
        )
    for key in [
        "max_images",
        "max_file_size_mb",
        "max_pages",
        "pdf_max_images_per_page",
        "pdf_max_pages",
        "pdf_min_text_chars",
        "pdf_render_dpi",
        "max_spreadsheet_cells",
        "max_spreadsheet_xml_bytes",
        "image_max_upload_bytes",
    ]:
        value = conversion.get(key)
        if value is not None and (not isinstance(value, int) or value <= 0):
            raise serializers.ValidationError(
                {field_name: f"conversion.{key} must be positive"}
            )
    ratio = conversion.get("pdf_min_image_area_ratio")
    if ratio is not None and (
        not isinstance(ratio, (int, float)) or ratio <= 0 or ratio > 1
    ):
        raise serializers.ValidationError(
            {
                field_name: (
                    "conversion.pdf_min_image_area_ratio must be " "between 0 and 1"
                )
            }
        )
    for key in ["vision_model_ref", "document_model_ref", "queue"]:
        value = conversion.get(key)
        if value is not None and not isinstance(value, str):
            raise serializers.ValidationError(
                {field_name: f"conversion.{key} must be a string"}
            )


def _validate_unique_datasource_target_path(
    target_path,
    lensnode,
    instance,
    source_type,
):
    """Reject conflicting datasource paths on the same LensNode."""

    query = DataSource.objects.filter(lensnode=lensnode)
    if instance is not None:
        query = query.exclude(pk=instance.pk)
    target = PurePosixPath(target_path)
    for datasource in query.only("source_type", "target_path"):
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
            or datasource.source_type == DataSource.SourceType.MANAGED_WORKSPACE
        )
        paths_overlap = _paths_overlap(existing, target)
        if existing == target or (managed_overlap and paths_overlap):
            raise serializers.ValidationError(
                {"target_path": ("Another datasource uses an overlapping target path")}
            )


def _paths_overlap(first, second):
    """Return whether either path contains the other path."""

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


def _validate_datasource_credential_type(credential, auth_type):
    """Validate that a selected credential matches the datasource type."""

    if credential is None:
        return
    allowed_types = (
        set(auth_type) if isinstance(auth_type, (list, tuple, set)) else {auth_type}
    )
    if credential.auth_type not in allowed_types:
        raise serializers.ValidationError(
            {"credential_uuid": "Credential type does not match datasource"}
        )


def _validate_cron_expression(value):
    """Validate a simple five-field crontab expression."""

    parts = str(value or "").split()
    if len(parts) != 5:
        raise serializers.ValidationError(
            {"sync_policy": "cron must contain five fields"}
        )
    for part in parts:
        if not all(char.isdigit() or char in "*,/-" for char in part):
            raise serializers.ValidationError(
                {"sync_policy": "cron contains unsupported characters"}
            )


def _validate_git_config(config, instance=None, credential=None):
    scope_type = config.get("scope_type") or (
        "organization" if config.get("repositories") else "repository"
    )
    if scope_type not in {"repository", "organization"}:
        raise serializers.ValidationError(
            {"config": "git scope_type must be repository or organization"}
        )
    if scope_type == "organization":
        repositories = config.get("repositories") or []
        if not isinstance(repositories, list) or not repositories:
            raise serializers.ValidationError(
                {"config": "git organization repositories are required"}
            )
        seen_targets = set()
        for repository in repositories:
            if not isinstance(repository, dict):
                raise serializers.ValidationError(
                    {"config": "git repository item must be an object"}
                )
            if not repository.get("repo_url"):
                raise serializers.ValidationError(
                    {"config": "git repository repo_url is required"}
                )
            if not repository.get("branch"):
                raise serializers.ValidationError(
                    {"config": "git repository branch is required"}
                )
            target_subdir = str(
                repository.get("target_subdir")
                or repository.get("name")
                or repository.get("path")
                or ""
            ).strip()
            if not target_subdir:
                raise serializers.ValidationError(
                    {"config": "git repository target_subdir is required"}
                )
            if target_subdir in seen_targets:
                raise serializers.ValidationError(
                    {"config": "git repository target_subdir must be unique"}
                )
            seen_targets.add(target_subdir)
    elif not config.get("repo_url"):
        raise serializers.ValidationError({"config": "git config.repo_url is required"})
    auth_scheme = config.get("auth_scheme", "none")
    if auth_scheme not in ["none", "token"]:
        raise serializers.ValidationError(
            {"config": "git auth_scheme must be none or token"}
        )
    if auth_scheme == "none" and credential:
        if credential.auth_type != DataSourceCredential.AuthType.NONE:
            raise serializers.ValidationError(
                {"credential_uuid": ("Git credential must not be set without auth")}
            )
    if auth_scheme == "token" and not credential:
        raise serializers.ValidationError(
            {"credential_uuid": "Git HTTPS Token credential is required"}
        )
    if (
        auth_scheme == "token"
        and credential
        and credential.auth_type != DataSourceCredential.AuthType.HTTPS_TOKEN
    ):
        raise serializers.ValidationError(
            {"credential_uuid": "Git HTTPS Token credential is required"}
        )


def _validate_feishu_config(config, instance=None, credential=None):
    if not credential:
        raise serializers.ValidationError(
            {"credential_uuid": "Feishu credential is required"}
        )
    sync_mode = config.get("sync_mode") or "document_list"
    if sync_mode not in {"document_list", "drive_folder"}:
        raise serializers.ValidationError(
            {"config": "feishu sync_mode must be document_list or drive_folder"}
        )
    if sync_mode == "drive_folder":
        scope_config = credential.scope_config or {}
        if not (
            config.get("folder_url")
            or config.get("folder_token")
            or scope_config.get("folder_url")
            or scope_config.get("folder_token")
        ):
            raise serializers.ValidationError(
                {"config": ("feishu config.folder_url or folder_token is required")}
            )
        max_depth = config.get("max_depth", 10)
        if not isinstance(max_depth, int) or max_depth <= 0:
            raise serializers.ValidationError(
                {"config": "feishu max_depth must be a positive integer"}
            )
        return

    if not (
        config.get("document_url") or config.get("wiki_token") or config.get("doc_ids")
    ):
        raise serializers.ValidationError(
            {
                "config": (
                    "feishu config.document_url, wiki_token or doc_ids " "is required"
                )
            }
        )
    doc_ids = config.get("doc_ids", [])
    if not isinstance(doc_ids, list):
        raise serializers.ValidationError({"config": "feishu doc_ids must be a list"})


class SkillSerializer(serializers.ModelSerializer):
    """Skill serializer."""

    update_available = serializers.SerializerMethodField()

    class Meta:
        model = Skill
        fields = [
            "uuid",
            "name",
            "package_name",
            "kind",
            "definition",
            "version",
            "enabled",
            "package_hash",
            "package_size",
            "package_manifest",
            "source_type",
            "source_url",
            "source_ref",
            "source_path",
            "latest_source_ref",
            "source_checked_at",
            "update_available",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "uuid",
            "kind",
            "package_name",
            "package_hash",
            "package_size",
            "package_manifest",
            "source_type",
            "source_url",
            "source_ref",
            "source_path",
            "latest_source_ref",
            "source_checked_at",
            "created_at",
            "updated_at",
        ]

    def get_update_available(self, obj):
        """Return whether a newer source tag is known for this Skill."""

        return bool(
            obj.source_type == "github"
            and obj.latest_source_ref
            and obj.latest_source_ref != obj.source_ref
        )

    def validate_definition(self, value):
        """Validate and normalize the Skill environment declaration."""

        if not isinstance(value, dict):
            raise serializers.ValidationError("The Skill definition must be an object.")
        normalized = dict(value)
        environment = validate_environment_schema(normalized.get("environment"))
        normalized["environment"] = environment
        try:
            normalized["required_plugins"] = validate_required_plugins(
                normalized.get("required_plugins")
            )
        except SkillPluginRequirementError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        api = validate_skill_api_policy(normalized.get("api"), environment)
        if api:
            normalized["api"] = api
        else:
            normalized.pop("api", None)
        return normalized


class EnvironmentVariableSetSerializer(serializers.ModelSerializer):
    """Encrypted environment-variable set serializer."""

    values = serializers.JSONField(write_only=True, required=False)
    keys = serializers.ListField(read_only=True)
    usages = serializers.SerializerMethodField()

    class Meta:
        model = EnvironmentVariableSet
        fields = [
            "uuid",
            "name",
            "description",
            "values",
            "keys",
            "usages",
            "enabled",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "uuid",
            "keys",
            "usages",
            "created_at",
            "updated_at",
        ]

    def get_usages(self, variable_set):
        """Return secret-safe Skill and MCP binding references."""

        usages = [
            {
                "type": "skill",
                "resource_uuid": str(binding.skill.uuid),
                "resource_name": binding.skill.name,
                "assistant_uuid": str(binding.assistant.uuid),
                "assistant_name": binding.assistant.name,
            }
            for binding in variable_set.skill_bindings.all()
        ]
        usages.extend(
            {
                "type": "mcp",
                "resource_uuid": str(binding.mcp.uuid),
                "resource_name": binding.mcp.name,
                "assistant_uuid": str(binding.assistant.uuid),
                "assistant_name": binding.assistant.name,
            }
            for binding in variable_set.mcp_bindings.all()
        )
        return sorted(
            usages,
            key=lambda item: (
                item["type"],
                item["resource_name"].casefold(),
                item["assistant_name"].casefold(),
            ),
        )

    def validate_values(self, value):
        """Validate environment variable names and scalar values."""

        return validate_environment_values(value)

    def create(self, validated_data):
        """Create a set and encrypt its values."""

        values = validated_data.pop("values", {})
        variable_set = EnvironmentVariableSet(**validated_data)
        variable_set.set_values(values)
        variable_set.save()
        return variable_set

    def update(self, instance, validated_data):
        """Update metadata and optionally replace encrypted values."""

        values = validated_data.pop("values", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        if values is not None:
            instance.set_values(values)
        instance.save()
        return instance


class MCPServerSerializer(serializers.ModelSerializer):
    """MCP server serializer."""

    connection_uuid = serializers.SlugRelatedField(
        source="connection",
        slug_field="uuid",
        queryset=Connection.objects.all(),
        required=False,
        allow_null=True,
    )
    environment_references = serializers.SerializerMethodField()
    secret_mask = "********"
    sensitive_key_names = {
        "apikey",
        "authorization",
        "credential",
        "credentials",
        "password",
        "passwd",
        "privatekey",
        "secret",
        "token",
    }

    class Meta:
        model = MCPServer
        fields = [
            "uuid",
            "name",
            "transport",
            "endpoint",
            "config",
            "environment",
            "environment_references",
            "connection_uuid",
            "tools",
            "version",
            "enabled",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["uuid", "created_at", "updated_at"]

    def validate_config(self, value):
        """Require MCP configuration to remain a key-value object."""

        if not isinstance(value, dict):
            raise serializers.ValidationError("MCP configuration must be an object.")
        return value

    def validate_environment(self, value):
        """Validate MCP environment declarations like Skill declarations."""

        return validate_environment_schema(value)

    def get_environment_references(self, instance):
        """Return referenced variable names without exposing configuration."""

        return sorted(
            declared_environment_references(
                {"endpoint": instance.endpoint, "config": instance.config},
                instance.environment,
            )
        )

    def validate(self, attrs):
        """Require every MCP environment reference to be declared."""

        attrs = super().validate(attrs)
        if "config" in attrs:
            attrs["config"] = self._merge_sensitive_values(
                attrs["config"],
                getattr(self.instance, "config", {}) or {},
            )
        endpoint = attrs.get(
            "endpoint",
            getattr(self.instance, "endpoint", ""),
        )
        config = attrs.get(
            "config",
            getattr(self.instance, "config", {}),
        )
        environment = attrs.get(
            "environment",
            getattr(self.instance, "environment", []),
        )
        declared = {
            item["name"]
            for item in environment or []
            if isinstance(item, dict) and item.get("name")
        }
        references = environment_references({"endpoint": endpoint, "config": config})
        existing_references = environment_references(
            {
                "endpoint": getattr(self.instance, "endpoint", ""),
                "config": getattr(self.instance, "config", {}) or {},
            }
        )
        existing_declared = {
            item.get("name")
            for item in getattr(self.instance, "environment", []) or []
            if isinstance(item, dict) and item.get("name")
        }
        legacy_references = existing_references - existing_declared
        undeclared = sorted(references - declared - legacy_references)
        if undeclared:
            raise serializers.ValidationError(
                {
                    "environment": (
                        "Declare every referenced MCP environment variable: "
                        f"{', '.join(undeclared)}."
                    )
                }
            )
        transport = attrs.get(
            "transport",
            getattr(self.instance, "transport", MCPServer.Transport.URL),
        )
        connection = attrs.get(
            "connection",
            getattr(self.instance, "connection", None),
        )
        tools = attrs.get(
            "tools",
            getattr(self.instance, "tools", []),
        )
        if transport == MCPServer.Transport.PLUGIN:
            self._validate_plugin_adapter(
                attrs,
                connection,
                tools,
                endpoint,
                config,
                environment,
            )
        elif connection is not None or tools:
            raise serializers.ValidationError(
                {
                    "connection_uuid": (
                        "Only Plugin MCP adapters may reference a Connection."
                    ),
                    "tools": (
                        "Only Plugin MCP adapters may select Plugin tools."
                    ),
                }
            )
        return attrs

    def _validate_plugin_adapter(
        self,
        attrs,
        connection,
        tools,
        endpoint,
        config,
        environment,
    ):
        """Restrict Plugin adapters to the native Connection tool runtime."""

        errors = {}
        if connection is None:
            errors["connection_uuid"] = "Plugin adapter Connection is required."
        elif (
            connection.status != Connection.Status.ACTIVE
            or connection.secret_version is None
            or connection.secret_version.status != "active"
            or connection.secret_version.material.status != "active"
        ):
            errors["connection_uuid"] = "Plugin adapter Connection is disabled."
        if endpoint:
            errors["endpoint"] = "Plugin adapters cannot define an MCP endpoint."
        if config:
            errors["config"] = "Plugin adapters cannot define MCP configuration."
        if environment:
            errors["environment"] = (
                "Plugin adapters cannot define MCP environment variables."
            )
        if not isinstance(tools, list) or not tools:
            errors["tools"] = "Select at least one Plugin tool."
        elif (
            any(not isinstance(key, str) or not key.strip() for key in tools)
            or len(tools) != len(set(tools))
        ):
            errors["tools"] = "Plugin tools must be unique non-empty strings."
        if errors or connection is None:
            raise serializers.ValidationError(errors)
        try:
            plugin = installed_plugin(connection.plugin_key)
        except PluginRegistryError as exc:
            raise serializers.ValidationError(
                {"connection_uuid": str(exc)}
            ) from exc
        definitions = {tool.key: tool for tool in plugin.tools}
        unsupported = sorted(set(tools).difference(definitions))
        if unsupported:
            raise serializers.ValidationError(
                {"tools": f"Unknown Plugin tools: {', '.join(unsupported)}."}
            )
        if any(
            definitions[key].side_effect != "none"
            for key in tools
        ):
            raise serializers.ValidationError(
                {"tools": "Plugin MCP adapters only support read-only tools."}
            )
        attrs["version"] = plugin.version

    def to_representation(self, instance):
        """Mask sensitive MCP configuration values in every response."""

        payload = super().to_representation(instance)
        payload["config"] = self._mask_sensitive_values(instance.config or {})
        if payload.get("connection_uuid") is not None:
            payload["connection_uuid"] = str(payload["connection_uuid"])
        return payload

    @classmethod
    def _normalize_key(cls, key):
        return "".join(
            character.lower() for character in str(key or "") if character.isalnum()
        )

    @classmethod
    def _is_sensitive_key(cls, key):
        normalized = cls._normalize_key(key)
        if normalized in cls.sensitive_key_names:
            return True
        segmented_key = re.sub(
            r"([a-z0-9])([A-Z])",
            r"\1_\2",
            str(key or ""),
        )
        segments = [
            segment.lower() for segment in re.findall(r"[A-Za-z0-9]+", segmented_key)
        ]
        if any(
            segment
            in {
                "authorization",
                "credential",
                "credentials",
                "password",
                "passwd",
                "secret",
            }
            for segment in segments
        ):
            return True
        if any(
            pair in {("api", "key"), ("private", "key")}
            for pair in zip(segments, segments[1:])
        ):
            return True
        return bool(segments and segments[-1] == "token")

    @classmethod
    def _mask_sensitive_values(cls, value):
        if isinstance(value, dict):
            masked = {}
            for key, item in value.items():
                if cls._is_sensitive_key(key) and item not in (None, ""):
                    masked[key] = cls.secret_mask
                else:
                    masked[key] = cls._mask_sensitive_values(item)
            return masked
        if isinstance(value, list):
            return [cls._mask_sensitive_values(item) for item in value]
        return value

    @classmethod
    def _merge_sensitive_values(cls, incoming, existing):
        merged = {}
        for key, value in incoming.items():
            existing_key = key
            if isinstance(existing, dict) and key not in existing:
                normalized_key = cls._normalize_key(key)
                existing_key = next(
                    (
                        candidate
                        for candidate in existing
                        if cls._normalize_key(candidate) == normalized_key
                    ),
                    key,
                )
            existing_value = (
                existing.get(existing_key) if isinstance(existing, dict) else None
            )
            if value == cls.secret_mask and existing_value in (None, ""):
                raise serializers.ValidationError(
                    "Masked sensitive values require an existing value. "
                    "Provide the new sensitive value."
                )
            if (
                cls._is_sensitive_key(key)
                and value in (None, "", cls.secret_mask)
                and existing_value not in (None, "")
            ):
                merged[existing_key] = existing_value
            elif isinstance(value, dict):
                merged[existing_key] = cls._merge_sensitive_values(
                    value,
                    existing_value if isinstance(existing_value, dict) else {},
                )
            elif isinstance(value, list):
                existing_items = (
                    existing_value if isinstance(existing_value, list) else []
                )
                if cls._contains_sensitive_placeholder(
                    value,
                    existing_items,
                ) and not cls._masked_list_matches(value, existing_items):
                    raise serializers.ValidationError(
                        "Lists containing masked sensitive values cannot be "
                        "reordered or structurally edited. Provide every "
                        "sensitive value to replace the list."
                    )
                merged[existing_key] = cls._merge_sensitive_list(
                    value,
                    existing_items,
                )
            else:
                merged[existing_key] = value
        return merged

    @classmethod
    def _merge_sensitive_list(cls, incoming, existing):
        merged = []
        for index, item in enumerate(incoming):
            existing_item = existing[index] if index < len(existing) else None
            if isinstance(item, dict):
                merged.append(
                    cls._merge_sensitive_values(
                        item,
                        existing_item if isinstance(existing_item, dict) else {},
                    )
                )
            elif isinstance(item, list):
                merged.append(
                    cls._merge_sensitive_list(
                        item,
                        existing_item if isinstance(existing_item, list) else [],
                    )
                )
            else:
                merged.append(item)
        return merged

    @classmethod
    def _contains_sensitive_placeholder(cls, value, existing=None):
        if isinstance(value, dict):
            existing_items = existing if isinstance(existing, dict) else {}
            for key, item in value.items():
                existing_key = key
                if key not in existing_items:
                    normalized_key = cls._normalize_key(key)
                    existing_key = next(
                        (
                            candidate
                            for candidate in existing_items
                            if cls._normalize_key(candidate) == normalized_key
                        ),
                        key,
                    )
                existing_item = existing_items.get(existing_key)
                if (
                    cls._is_sensitive_key(key)
                    and item in (None, "", cls.secret_mask)
                    and existing_item not in (None, "")
                ):
                    return True
                if cls._contains_sensitive_placeholder(item, existing_item):
                    return True
            return False
        if isinstance(value, list):
            existing_items = existing if isinstance(existing, list) else []
            return any(
                cls._contains_sensitive_placeholder(
                    item,
                    existing_items[index] if index < len(existing_items) else None,
                )
                for index, item in enumerate(value)
            )
        return False

    @classmethod
    def _masked_list_matches(cls, incoming, existing):
        if isinstance(incoming, dict):
            if not isinstance(existing, dict) or incoming.keys() != existing.keys():
                return False
            return all(
                (
                    cls._is_sensitive_key(key)
                    and item in (None, "", cls.secret_mask)
                    and existing[key] not in (None, "")
                )
                or cls._masked_list_matches(item, existing[key])
                for key, item in incoming.items()
            )
        if isinstance(incoming, list):
            return (
                isinstance(existing, list)
                and len(incoming) == len(existing)
                and all(
                    cls._masked_list_matches(item, existing[index])
                    for index, item in enumerate(incoming)
                )
            )
        return incoming == existing


class GlobalSettingSerializer(serializers.ModelSerializer):
    """Global setting serializer."""

    def validate(self, attrs):
        """Validate known high-risk setting schemas."""

        key = attrs.get("key", getattr(self.instance, "key", ""))
        value = attrs.get("value", getattr(self.instance, "value", {}))

        positive_integer_keys = {
            "lens.datasource_upload.max_bytes": "max_bytes",
            "lens.datasource_upload.max_extracted_bytes": "max_extracted_bytes",
            "lens.datasource_upload.max_extracted_files": "max_extracted_files",
            "retention.run_days": "run_days",
            "lensnode.defaults.timeout": "timeout",
            "lensnode.defaults.idle_timeout": "idle_timeout",
            "lensnode.health.offline_threshold_s": "offline_threshold_s",
            "lensnode_cleanup.interval_seconds": "interval_seconds",
            "lensnode_health.interval_seconds": "interval_seconds",
            "run_retention.interval_seconds": "interval_seconds",
            "lens.datasource_sync.timeout_s": "timeout_s",
            "lens.datasource_sync.workers": "workers",
        }
        if key in positive_integer_keys:
            if type(value) is not int or value <= 0:
                name = positive_integer_keys[key]
                raise serializers.ValidationError(
                    {"value": f"{name} must be a positive integer"}
                )

        model_ref_keys = {
            "lens.smart_collaboration.model_ref": "model_ref",
            "lens.skills.generator_model_ref": "generator_model_ref",
            "lens.datasource_conversion.vision_model_ref": ("vision_model_ref"),
            "lens.datasource_conversion.document_model_ref": ("document_model_ref"),
        }
        if key in model_ref_keys:
            if value not in [None, ""] and not isinstance(value, str):
                raise serializers.ValidationError(
                    {"value": f"{model_ref_keys[key]} must be a string or empty"}
                )

        if key == "lens.history_budget":
            if not isinstance(value, dict):
                raise serializers.ValidationError({"value": "must be a JSON object"})
            for sub_key in ("pairs", "message_chars", "total_chars"):
                if sub_key in value and (
                    not isinstance(value[sub_key], int) or value[sub_key] <= 0
                ):
                    raise serializers.ValidationError(
                        {"value": (f"{sub_key} must be a positive integer")}
                    )

        return attrs

    class Meta:
        model = GlobalSetting
        fields = ["key", "value", "description", "updated_at"]
        read_only_fields = ["updated_at"]


class MessageAttachmentSerializer(serializers.ModelSerializer):
    """Read serializer for a question image attachment."""

    url = serializers.SerializerMethodField()

    class Meta:
        model = MessageAttachment
        fields = [
            "uuid",
            "url",
            "kind",
            "mime_type",
            "width",
            "height",
            "byte_size",
            "original_name",
            "order",
        ]
        read_only_fields = fields

    def get_url(self, obj):
        """Return the authenticated fetch path for the image bytes."""

        return reverse("lens-attachment", kwargs={"uuid": obj.uuid})


class RunOutputFileSerializer(serializers.ModelSerializer):
    """Read serializer for a delivered run output file."""

    url = serializers.SerializerMethodField()

    class Meta:
        model = RunOutputFile
        fields = [
            "uuid",
            "url",
            "filename",
            "content_type",
            "byte_size",
            "created_at",
        ]
        read_only_fields = fields

    def get_url(self, obj):
        """Return the authenticated download path for the file bytes."""

        return reverse("lens-output-file", kwargs={"uuid": obj.uuid})


class MessageSerializer(serializers.ModelSerializer):
    """Session message serializer."""

    run = serializers.UUIDField(source="run.uuid", read_only=True)
    thinking = serializers.SerializerMethodField()
    completed_at = serializers.SerializerMethodField()
    feedback = serializers.SerializerMethodField()
    feedback_updated_at = serializers.SerializerMethodField()
    attachments = serializers.SerializerMethodField()
    output_files = RunOutputFileSerializer(many=True, read_only=True)
    citations = serializers.SerializerMethodField()
    planned_evidence = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = [
            "uuid",
            "role",
            "content",
            "sequence",
            "run",
            "thinking",
            "completed_at",
            "feedback",
            "feedback_updated_at",
            "attachments",
            "output_files",
            "citations",
            "planned_evidence",
            "created_at",
        ]
        read_only_fields = [
            "uuid",
            "role",
            "content",
            "sequence",
            "run",
            "thinking",
            "completed_at",
            "feedback",
            "feedback_updated_at",
            "attachments",
            "output_files",
            "citations",
            "planned_evidence",
            "created_at",
        ]

    def get_citations(self, obj):
        """Return trusted citation metadata without captured source text."""

        if obj.role != Message.Role.ASSISTANT:
            return []
        run = self._run_for_message(obj)
        return public_run_citations(run.citations) if run else []

    def get_planned_evidence(self, obj):
        """Return the user-safe evidence quality summary."""

        if obj.role != Message.Role.ASSISTANT:
            return {}
        run = self._run_for_message(obj)
        return sanitize_planned_evidence(run.planned_evidence) if run else {}

    def get_attachments(self, obj):
        """Return persistent images plus unexpired transient documents."""

        images = MessageAttachmentSerializer(
            obj.attachments.all(),
            many=True,
        ).data
        if obj.role != Message.Role.USER or obj.run_id is None:
            return images
        from .document_attachments import document_attachment_response

        documents_by_run = self.context.get("document_attachments_by_run")
        if documents_by_run is None:
            from .document_attachments import get_run_document_attachments

            documents = get_run_document_attachments(
                obj.run.uuid,
                fail_silently=True,
            )
        else:
            documents = documents_by_run.get(str(obj.run.uuid), [])
        documents = [document_attachment_response(item) for item in documents]
        return sorted(
            [*images, *documents],
            key=lambda item: item.get("order", 0),
        )

    def get_completed_at(self, obj):
        """Return the terminal Run timestamp for assistant messages."""

        if obj.role != Message.Role.ASSISTANT:
            return None
        run = self._run_for_message(obj)
        return run.finished_at if run else None

    def get_feedback(self, obj):
        """Return the current feedback for an assistant response."""

        if obj.role != Message.Role.ASSISTANT:
            return None
        run = self._run_for_message(obj)
        return run.feedback if run else None

    def get_feedback_updated_at(self, obj):
        """Return when feedback for an assistant response last changed."""

        if obj.role != Message.Role.ASSISTANT:
            return None
        run = self._run_for_message(obj)
        return run.feedback_updated_at if run else None

    @staticmethod
    def _run_for_message(obj):
        """Return the Run linked from either side of an output message."""

        if obj.run_id:
            return obj.run
        return next(iter(obj.response_runs.all()), None)

    def get_thinking(self, obj):
        """Return a persisted reasoning summary for assistant messages.

        Surfaces safe runtime events and the terminal business outcome.
        Raw tool arguments, model output, and internal trace metadata stay
        server-side.
        """

        run = self._run_for_message(obj)
        if obj.role != Message.Role.ASSISTANT or run is None:
            return None
        steps = []
        for step in run.steps.all():
            steps.extend(public_step_detail(step.detail)["events"])
        for child in run.delegated_runs.all():
            assistant_name = child.session.assistant.name[:160]
            delegated_task = str(child.input_message.content or "").strip()[:2000]
            for step in child.steps.all():
                for event in public_step_detail(step.detail)["events"]:
                    event = {
                        **event,
                        "assistant_name": assistant_name,
                        "delegated_task": delegated_task,
                    }
                    if event.get("event_type") == "activity.recorded":
                        event["payload"] = {
                            **(event.get("payload") or {}),
                            "assistant_name": assistant_name,
                            "delegated_task": delegated_task,
                        }
                    steps.append(event)
        if not steps and not run.outcome:
            return None
        duration = None
        if run.started_at and run.finished_at:
            duration = (run.finished_at - run.started_at).total_seconds()
        return {
            "duration_seconds": duration,
            "steps": steps,
            "outcome": run.outcome,
            "status": run.status,
            "clarification_answered_at": (run.clarification_answered_at),
            "termination_detail": sanitize_termination_detail(run.termination_detail),
        }


class RunStepSerializer(serializers.ModelSerializer):
    """Run step serializer."""

    detail = serializers.SerializerMethodField()

    def get_detail(self, obj):
        """Return the user-visible subset of persisted runtime events."""

        return public_step_detail(obj.detail)

    class Meta:
        model = RunStep
        fields = [
            "uuid",
            "step_type",
            "detail",
            "status",
            "sequence",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class RunExecutionSerializer(serializers.ModelSerializer):
    """Run execution snapshot serializer."""

    lensnode = serializers.UUIDField(source="lensnode.uuid", read_only=True)
    loaded_skills = serializers.SerializerMethodField()
    loaded_mcps = serializers.SerializerMethodField()

    def get_loaded_skills(self, obj):
        """Return Skill identities without definitions or environment data."""

        return sanitize_loaded_skills(obj.loaded_skills)

    def get_loaded_mcps(self, obj):
        """Return MCP identities without endpoints, headers, or config."""

        return sanitize_loaded_mcps(obj.loaded_mcps)

    class Meta:
        model = RunExecution
        fields = [
            "uuid",
            "lensnode",
            "task",
            "loaded_skills",
            "loaded_mcps",
            "target_dirs",
            "agent_rounds",
            "run_timeout_s",
            "token_budget_profile",
            "token_budget_max_tokens",
            "token_budget_final_reserve_tokens",
            "status",
            "started_at",
            "finished_at",
        ]
        read_only_fields = fields


class RunSerializer(serializers.ModelSerializer):
    """Run serializer with nested execution details."""

    steps = RunStepSerializer(many=True, read_only=True)
    execution = RunExecutionSerializer(read_only=True)
    lensnode = serializers.UUIDField(source="lensnode.uuid", read_only=True)
    retry_of_run_uuid = serializers.UUIDField(
        source="retry_of_run.uuid",
        read_only=True,
    )
    termination_detail = serializers.SerializerMethodField()
    citations = serializers.SerializerMethodField()
    planned_evidence = serializers.SerializerMethodField()

    def get_termination_detail(self, obj):
        """Return allowlisted terminal metadata only."""

        return sanitize_termination_detail(obj.termination_detail)

    def get_citations(self, obj):
        """Return trusted citation metadata without source snapshots."""

        return public_run_citations(obj.citations)

    def get_planned_evidence(self, obj):
        """Return the user-safe evidence quality summary."""

        return sanitize_planned_evidence(obj.planned_evidence)

    class Meta:
        model = Run
        fields = [
            "uuid",
            "status",
            "input_message",
            "output_message",
            "retry_of_run_uuid",
            "lensnode",
            "metering_ref",
            "error",
            "outcome",
            "termination_detail",
            "citations",
            "planned_evidence",
            "feedback",
            "feedback_updated_at",
            "started_at",
            "finished_at",
            "resume_by",
            "created_at",
            "idempotency_key",
            "steps",
            "execution",
        ]
        read_only_fields = fields


class RunClarificationAnswerSerializer(serializers.Serializer):
    """Validate one plain-text answer to a persisted clarification."""

    request_id = serializers.CharField(max_length=128)
    answer = serializers.CharField(max_length=4_000)
    enqueue = serializers.BooleanField(required=False, default=True)

    def validate_answer(self, value):
        """Reject empty or whitespace-only clarification answers."""

        answer = value.strip()
        if not answer:
            raise serializers.ValidationError("Answer cannot be empty.")
        return answer


class RunFeedbackSerializer(serializers.ModelSerializer):
    """Validate and persist one user's feedback for a completed run."""

    feedback = serializers.ChoiceField(
        choices=Run.Feedback.choices,
        allow_blank=True,
    )

    class Meta:
        model = Run
        fields = ["feedback", "feedback_updated_at"]
        read_only_fields = ["feedback_updated_at"]

    def validate(self, attrs):
        """Only completed runs with an answer can receive feedback."""

        run = self.instance
        if run.status != Run.Status.DONE or run.output_message is None:
            raise serializers.ValidationError("RUN_NOT_FEEDBACK_ELIGIBLE")
        return attrs

    def update(self, instance, validated_data):
        """Update feedback without changing its timestamp on no-op calls."""

        feedback = validated_data["feedback"]
        if feedback == instance.feedback:
            return instance
        instance.feedback = feedback
        instance.feedback_updated_at = timezone.now()
        instance.save(
            update_fields=[
                "feedback",
                "feedback_updated_at",
                "updated_at",
            ]
        )
        return instance


class SessionSerializer(serializers.ModelSerializer):
    """Session serializer."""

    assistant_name = serializers.CharField(
        source="assistant.name",
        read_only=True,
    )
    assistant_slug = serializers.CharField(
        source="assistant.slug",
        read_only=True,
    )
    assistant_mode = serializers.CharField(
        source="assistant.mode",
        read_only=True,
    )
    has_shareable_answer = serializers.SerializerMethodField()
    routing_assistants = serializers.SerializerMethodField()

    class Meta:
        model = Session
        fields = [
            "uuid",
            "assistant",
            "assistant_name",
            "assistant_slug",
            "assistant_mode",
            "routing_mode",
            "allowed_assistant_uuids",
            "routing_assistants",
            "user",
            "title",
            "title_manually_edited",
            "title_generation_status",
            "pinned_at",
            "has_shareable_answer",
            "status",
            "created_at",
            "updated_at",
        ]

    def get_routing_assistants(self, obj):
        """Return the frozen assistant range for a smart-routing session."""

        if obj.routing_mode != Session.RoutingMode.SMART:
            return []
        selected = {str(value) for value in (obj.allowed_assistant_uuids or [])}
        return [
            {
                "uuid": str(item.uuid),
                "name": item.name,
                "capability": item.capability,
            }
            for item in Assistant.objects.filter(uuid__in=selected)
            .exclude(is_system=True)
            .order_by("name")
        ]

    def validate_title(self, value):
        """Reject empty manual titles and normalize their whitespace."""

        title = " ".join(value.split())
        if not title:
            raise serializers.ValidationError("SESSION_TITLE_REQUIRED")
        return title

    def validate_allowed_assistant_uuids(self, value):
        """Keep a smart-routing range inside the current user's access."""

        if self.instance is None:
            return value
        if self.instance.routing_mode != Session.RoutingMode.SMART:
            raise serializers.ValidationError("Only smart sessions support this.")
        if (
            not self.instance.assistant.is_system
            and self.instance.assistant.routing_mode
            == Assistant.RoutingMode.SMART
            and set(str(item) for item in value)
            != set(self.instance.allowed_assistant_uuids or [])
        ):
            raise serializers.ValidationError(
                "Smart Assistant session members cannot be changed."
            )
        try:
            assistants = smart_collaboration_assistants(
                self.context["request"].user,
                value,
                allow_empty=True,
            )
        except AssistantNotRunnableError as exc:
            raise serializers.ValidationError(
                "Each assistant must be active and accessible."
            ) from exc
        return [str(item.uuid) for item in assistants]

    def get_has_shareable_answer(self, obj):
        """Return the list annotation or calculate the fallback value."""

        annotated = getattr(obj, "has_shareable_answer", None)
        if annotated is not None:
            return annotated
        return obj.run_set.filter(
            status=Run.Status.DONE,
            output_message__isnull=False,
        ).exists()

    def update(self, instance, validated_data):
        """Protect an explicit title from later automatic generation."""

        if "title" in validated_data:
            instance.title_manually_edited = True
            instance.title_generation_status = Session.TitleGenerationStatus.SKIPPED
        return super().update(instance, validated_data)


class SessionCreateSerializer(serializers.Serializer):
    """Session creation payload."""

    assistant_uuid = serializers.UUIDField(required=False)
    routing_mode = serializers.ChoiceField(
        choices=Session.RoutingMode.choices,
        default=Session.RoutingMode.DIRECT,
    )
    allowed_assistant_uuids = serializers.ListField(
        child=serializers.UUIDField(), required=False, default=list
    )
    title = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=160,
    )

    def create(self, validated_data):
        request = self.context["request"]
        try:
            if validated_data["routing_mode"] == Session.RoutingMode.SMART:
                return create_smart_collaboration_session(
                    request.user,
                    validated_data.get("title", ""),
                    validated_data.get("allowed_assistant_uuids", []),
                )
            return create_assistant_session(
                validated_data["assistant_uuid"],
                request.user,
                validated_data.get("title", ""),
            )
        except AssistantNotRunnableError:
            raise PermissionDenied("You do not have access to this assistant.")

    def validate(self, attrs):
        """Require exactly the fields used by the selected routing mode."""

        if attrs["routing_mode"] == Session.RoutingMode.DIRECT:
            if not attrs.get("assistant_uuid"):
                raise serializers.ValidationError(
                    {"assistant_uuid": "This field is required."}
                )
        else:
            if attrs.get("assistant_uuid"):
                raise serializers.ValidationError(
                    {
                        "assistant_uuid": (
                            "Use an Assistant URL or the ad-hoc Smart "
                            "Collaboration entry, not both."
                        )
                    }
                )
            attrs.pop("assistant_uuid", None)
        return attrs


class RunCreateSerializer(serializers.Serializer):
    """Run creation payload."""

    question = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=CLARIFICATION_MAX_ORIGINAL_CHARS,
    )
    idempotency_key = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=128,
    )
    retry_of_run_uuid = serializers.UUIDField(required=False, allow_null=True)
    enqueue = serializers.BooleanField(required=False, default=True)
    run_inline = serializers.BooleanField(required=False, default=False)
    attachment_uuids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
    )
    routing_assistant_uuid = serializers.UUIDField(required=False)
    routing_assistant_uuids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        max_length=MAX_SUBAGENTS_PER_RUN,
    )

    def validate(self, attrs):
        """Require text or an attachment, and cap attachment count."""

        question = (attrs.get("question") or "").strip()
        attachments = attrs.get("attachment_uuids") or []
        if not question and not attachments:
            raise serializers.ValidationError(
                "Provide a question or at least one attachment."
            )
        if len(attachments) > ATTACHMENT_MAX_PER_MESSAGE:
            raise serializers.ValidationError(
                "At most " f"{ATTACHMENT_MAX_PER_MESSAGE} attachments per message."
            )
        if len(set(attachments)) != len(attachments):
            raise serializers.ValidationError("Attachment UUIDs must be unique.")
        routing_assistant_uuid = attrs.get("routing_assistant_uuid")
        routing_assistant_uuids = attrs.get("routing_assistant_uuids")
        if routing_assistant_uuid is not None and routing_assistant_uuids is not None:
            raise serializers.ValidationError(
                "Use routing_assistant_uuid or routing_assistant_uuids, not both."
            )
        selected_assistant_uuids = (
            routing_assistant_uuids
            if routing_assistant_uuids is not None
            else (
                [routing_assistant_uuid] if routing_assistant_uuid is not None else []
            )
        )
        if len(set(selected_assistant_uuids)) != len(selected_assistant_uuids):
            raise serializers.ValidationError(
                {"routing_assistant_uuids": "Assistant UUIDs must be unique."}
            )
        if selected_assistant_uuids:
            session = self.context["session"]
            if session.routing_mode != Session.RoutingMode.SMART:
                raise serializers.ValidationError(
                    {"routing_assistant_uuids": ("Only smart sessions support this.")}
                )
            if not {str(value) for value in selected_assistant_uuids}.issubset(
                {str(value) for value in (session.allowed_assistant_uuids or [])}
            ):
                raise serializers.ValidationError(
                    {
                        "routing_assistant_uuids": (
                            "Assistant is outside this session's allowed range."
                        )
                    }
                )
            try:
                smart_collaboration_assistants(
                    self.context["request"].user,
                    selected_assistant_uuids,
                )
            except AssistantNotRunnableError as exc:
                raise serializers.ValidationError(
                    {"routing_assistant_uuids": "Assistant is unavailable."}
                ) from exc
        retry_uuid = attrs.get("retry_of_run_uuid")
        if retry_uuid is not None:
            try:
                retry_of_run = Run.objects.get(uuid=retry_uuid)
                validate_retry_run(self.context["session"], retry_of_run)
            except (Run.DoesNotExist, ValueError):
                raise serializers.ValidationError(
                    {"retry_of_run_uuid": "Invalid Retry Run."}
                )
            attrs["retry_of_run"] = retry_of_run
        return attrs

    def create(self, validated_data):
        session = self.context["session"]
        request = self.context.get("request")
        run_inline = validated_data.get("run_inline", False)
        try:
            run = create_execution_run(
                session=session,
                question=validated_data.get("question", ""),
                idempotency_key=validated_data.get("idempotency_key", ""),
                retry_of_run=validated_data.get("retry_of_run"),
                enqueue=(validated_data.get("enqueue", True) and not run_inline),
                attachment_uuids=[
                    str(value) for value in validated_data.get("attachment_uuids", [])
                ],
                user=request.user if request else None,
                routing_assistant_uuid=validated_data.get("routing_assistant_uuid"),
                routing_assistant_uuids=validated_data.get("routing_assistant_uuids"),
            )
        except AssistantNotRunnableError:
            raise PermissionDenied("You do not have access to this assistant.")
        except AttachmentError as exc:
            raise serializers.ValidationError(str(exc))
        if run_inline:
            from .execution import execute_answer_run

            try:
                execute_answer_run(run, dispatch=False)
            except Exception:
                run.refresh_from_db()
            else:
                run.refresh_from_db()
        return run


def _answer_snippet(text, limit=160):
    """Collapse whitespace and truncate answer text for list previews."""

    return " ".join((text or "").split())[:limit]


class SharedQAFileSerializer(serializers.ModelSerializer):
    """Share-scoped metadata for one immutable file snapshot."""

    url = serializers.SerializerMethodField()

    class Meta:
        model = SharedQAFile
        fields = [
            "uuid",
            "url",
            "filename",
            "content_type",
            "byte_size",
            "order",
        ]
        read_only_fields = fields

    def get_url(self, obj):
        """Return a path authorized by both share token and file UUID."""

        return reverse(
            "lens-public-qa-file",
            kwargs={
                "token": obj.share.token,
                "uuid": obj.uuid,
            },
        )


class SharedQAPublicSerializer(serializers.ModelSerializer):
    """Authenticated, read-only snapshot of one shared Q&A turn."""

    input_attachments = serializers.SerializerMethodField()
    output_files = serializers.SerializerMethodField()

    class Meta:
        model = SharedQA
        fields = [
            "token",
            "title",
            "question",
            "content_language",
            "input_attachments",
            "answer",
            "output_files",
            "assistant_name",
            "assistant_slug",
            "view_count",
            "published_at",
        ]
        read_only_fields = fields

    def get_input_attachments(self, obj):
        """Return snapshotted files submitted with the question."""

        files = obj.files.filter(kind=SharedQAFile.Kind.INPUT)
        return SharedQAFileSerializer(files, many=True).data

    def get_output_files(self, obj):
        """Return snapshotted final files delivered with the answer."""

        files = obj.files.filter(kind=SharedQAFile.Kind.OUTPUT)
        return SharedQAFileSerializer(files, many=True).data


class SharedQAListSerializer(serializers.ModelSerializer):
    """Public list item for an assistant's Q&A gallery."""

    answer_snippet = serializers.SerializerMethodField()

    class Meta:
        model = SharedQA
        fields = [
            "token",
            "title",
            "content_language",
            "answer_snippet",
            "view_count",
            "published_at",
        ]
        read_only_fields = fields

    def get_answer_snippet(self, obj):
        """Return a short plain-text preview of the answer."""

        return _answer_snippet(obj.answer)


class SharedQAMineSerializer(serializers.ModelSerializer):
    """A user's own shared Q&A with publish/list state and content."""

    run_uuid = serializers.UUIDField(source="run.uuid", read_only=True)

    class Meta:
        model = SharedQA
        fields = [
            "uuid",
            "token",
            "run_uuid",
            "title",
            "question",
            "answer",
            "content_language",
            "assistant_name",
            "assistant_slug",
            "is_listed",
            "status",
            "view_count",
            "published_at",
        ]
        read_only_fields = fields


class SharedQAAdminSerializer(serializers.ModelSerializer):
    """Admin moderation view of a shared Q&A."""

    assistant_visibility = serializers.CharField(
        source="assistant.visibility",
        read_only=True,
        default="",
    )
    published_by = serializers.SerializerMethodField()
    answer_snippet = serializers.SerializerMethodField()

    class Meta:
        model = SharedQA
        fields = [
            "uuid",
            "token",
            "title",
            "content_language",
            "answer_snippet",
            "assistant_name",
            "assistant_slug",
            "assistant_visibility",
            "is_listed",
            "status",
            "published_by",
            "view_count",
            "published_at",
            "created_at",
        ]
        read_only_fields = [
            "uuid",
            "token",
            "title",
            "content_language",
            "answer_snippet",
            "assistant_name",
            "assistant_slug",
            "assistant_visibility",
            "published_by",
            "view_count",
            "published_at",
            "created_at",
        ]

    def get_published_by(self, obj):
        """Return the publisher's username (internal use only)."""

        return obj.published_by.username if obj.published_by else ""

    def get_answer_snippet(self, obj):
        """Return a short plain-text preview of the answer."""

        return _answer_snippet(obj.answer)


class SharedQAAdminDetailSerializer(SharedQAAdminSerializer):
    """Admin moderation detail with the complete Q&A content."""

    class Meta(SharedQAAdminSerializer.Meta):
        fields = SharedQAAdminSerializer.Meta.fields + [
            "question",
            "answer",
        ]
        read_only_fields = SharedQAAdminSerializer.Meta.read_only_fields + [
            "question",
            "answer",
        ]
