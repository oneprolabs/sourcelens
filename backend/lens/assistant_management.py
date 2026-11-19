"""Tenant administrator checks and bounded assistant visibility changes."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.permissions import BasePermission

from accounts.access import get_user_membership
from accounts.audit import record_audit
from accounts.delegation import get_active_delegation, is_delegated_request
from accounts.models import TenantGroup
from accounts.permissions import HasPlatformSuperAdmin
from lens.model_scope import model_queryset
from lens.models import Assistant, AssistantAccess, Run, user_sees_all_assistants


class StaleAssistantBatch(APIException):
    """Require a refresh before overwriting a concurrent assistant edit."""

    status_code = 409
    default_detail = "Assistant configuration changed. Refresh and try again."


def assistant_configuration_scope(request):
    """Return request-local management authority and its owning tenant ID."""

    cached = getattr(request, "_assistant_configuration_scope", None)
    if cached is not None:
        return cached
    allowed, tenant_id = False, None
    user = request.user
    api_key = isinstance(request.auth, dict) and request.auth.get("principal_type") == "tenant_api_key"
    if user.is_authenticated and user.is_active and not api_key:
        membership = get_user_membership(user)
        platform_without_delegation = not is_delegated_request() and HasPlatformSuperAdmin().has_permission(
            request, None
        )
        if membership is not None:
            allowed = (
                not platform_without_delegation
                and membership.status == "active"
                and membership.role == "tenant_admin"
                and membership.tenant.status == "active"
            )
            tenant_id = membership.tenant.pk
        elif not is_delegated_request():
            allowed = user_sees_all_assistants(user)
    request._assistant_configuration_scope = (allowed, tenant_id)
    return allowed, tenant_id


def can_read_assistant_configuration(request, assistant):
    """Keep public usage access separate from authority over a configuration."""

    allowed, tenant_id = assistant_configuration_scope(request)
    return allowed and assistant.tenant_id == tenant_id


class CanManageAssistantConfiguration(BasePermission):
    """Require explicit tenant authority while retaining legacy unowned administration."""

    def has_permission(self, request, view):
        """Check write authority before serializers or lifecycle mutations run."""

        allowed, tenant_id = assistant_configuration_scope(request)
        if not allowed:
            return False
        if tenant_id is not None:
            management_tenant(request, write=request.method not in ("GET", "HEAD", "OPTIONS"))
        return True


def management_tenant(request, *, write=False):
    """Require explicit tenant authority, including readonly delegation checks."""

    if isinstance(request.auth, dict) and request.auth.get("principal_type") == "tenant_api_key":
        raise PermissionDenied("Tenant API keys cannot administer assistants.")
    if HasPlatformSuperAdmin().has_permission(request, None) and not is_delegated_request():
        raise PermissionDenied("Tenant delegation is required.")
    membership = get_user_membership(request.user)
    if not (
        request.user.is_active
        and membership
        and membership.role == "tenant_admin"
        and membership.status == "active"
        and membership.tenant.status == "active"
    ):
        raise PermissionDenied("An active tenant administrator is required.")
    delegation = get_active_delegation(request.user)
    if write and delegation and delegation.mode == "readonly":
        raise PermissionDenied("This delegation is read only.")
    return membership.tenant


def management_checks(assistants, tenant, *, include_usage=False):
    """Check current model ownership/type/activation and explicitly assigned nodes."""

    now = timezone.now()
    questions = {
        row["session__assistant_id"]: row["count"]
        for row in (
            Run.objects.filter(
                tenant=tenant,
                session__tenant=tenant,
                session__source="user",
                session__assistant_id__in=[assistant.pk for assistant in assistants],
                source=Run.Source.USER,
                parent_run__isnull=True,
                retry_of_run__isnull=True,
                created_at__gte=now - timedelta(days=30),
                created_at__lte=now,
            )
            .values("session__assistant_id")
            .annotate(count=Count("pk"))
            if include_usage
            else []
        )
    }
    fields = ("agent_model_ref", "decision_model_ref", "multimodal_model_ref")
    refs = {getattr(row, field) for row in assistants for field in fields if getattr(row, field)}
    models = {
        row["uuid"]: row
        for row in model_queryset(tenant)
        .filter(uuid__in=refs)
        .values("uuid", "model_type", "is_active", "config__model")
    }
    result = {}
    for assistant in assistants:
        issues = []
        for field in fields:
            ref = getattr(assistant, field)
            if not ref:
                if field == "agent_model_ref":
                    issues.append({"code": "model_missing", "field": field})
                continue
            model = models.get(ref)
            expected = "decision" if field == "decision_model_ref" else "llm"
            if not model or not model["is_active"] or model["model_type"] != expected:
                issues.append({"code": "model_unavailable", "field": field})
        node = assistant.lensnode
        if node and (node.status != "online" or node.enrollment_status != "approved" or node.token_revoked):
            issues.append({"code": "node_unavailable", "field": "lensnode"})
        names = {}
        for field in fields:
            ref = getattr(assistant, field)
            model = models.get(ref)
            if model and isinstance(model["config__model"], str):
                names[str(ref)] = model["config__model"][:160]
        result[str(assistant.uuid)] = {
            "issues": issues,
            "models": names,
            **({"questions_30d": questions.get(assistant.pk, 0)} if include_usage else {}),
        }
    return result


def access_subjects(tenant, *, search="", page=1, page_size=30):
    """Return bounded identities from this tenant for restricted visibility."""

    users = get_user_model().objects.filter(is_active=True, membership__tenant=tenant, membership__status="active")
    groups = TenantGroup.objects.filter(tenant=tenant)
    if search:
        users = users.filter(Q(username__icontains=search) | Q(email__icontains=search))
        groups = groups.filter(name__icontains=search)
    start = (page - 1) * page_size
    end = start + page_size
    return {
        "users": {
            "count": users.count(),
            "results": [
                {"id": row["pk"], "name": row["username"]}
                for row in users.order_by("username", "pk").values("pk", "username")[start:end]
            ],
        },
        "groups": {
            "count": groups.count(),
            "results": [
                {"id": row["auth_group_id"], "name": row["name"]}
                for row in groups.order_by("name", "pk").values("auth_group_id", "name")[start:end]
            ],
        },
    }


class AccessSubjectQuerySerializer(serializers.Serializer):
    """Validate the compact, tenant-scoped selector query."""

    search = serializers.CharField(required=False, allow_blank=True, max_length=160, default="")
    page = serializers.IntegerField(min_value=1, max_value=100000, default=1)
    page_size = serializers.IntegerField(min_value=1, max_value=100, default=30)


class StrictSubjectId(serializers.Field):
    """Accept JSON positive integers without bool or float coercion."""

    def to_internal_value(self, value):
        """Reject malformed grant identifiers at the API boundary."""

        if type(value) is not int or value <= 0:
            raise ValidationError("A positive integer is required.")
        return value


class VisibilityGrantSerializer(serializers.Serializer):
    """Identify one user or group without accepting arbitrary grant fields."""

    type = serializers.ChoiceField(choices=("user", "group"))
    id = StrictSubjectId()


class AssistantVersionSerializer(serializers.Serializer):
    """Pin a batch member to its last observed configuration version."""

    uuid = serializers.UUIDField()
    updated_at = serializers.DateTimeField()


class BulkVisibilitySerializer(serializers.Serializer):
    """Validate one explicit and bounded visibility operation."""

    assistants = AssistantVersionSerializer(many=True, allow_empty=False, max_length=1000)
    visibility = serializers.ChoiceField(choices=Assistant.Visibility.choices)
    access_grants = VisibilityGrantSerializer(many=True, required=False, allow_empty=False, max_length=1000)

    def to_internal_value(self, data):
        """Reject explicit member assignments before validating their identities."""

        if is_delegated_request() and "access_grants" in data:
            raise PermissionDenied("Assistant member grants are managed by the tenant.")
        return super().to_internal_value(data)

    def validate(self, attrs):
        """Require explicit subjects for restricted access and unique assistants."""

        uuids = [row["uuid"] for row in attrs["assistants"]]
        if len(uuids) != len(set(uuids)):
            raise ValidationError({"assistants": "Duplicate assistants are not allowed."})
        if attrs["visibility"] == Assistant.Visibility.PRIVATE and not is_delegated_request():
            if not attrs.get("access_grants"):
                raise ValidationError({"access_grants": "Select at least one member or group."})
            if len(uuids) * len(attrs["access_grants"]) > 10000:
                raise ValidationError({"access_grants": "Use smaller batches for more than 10,000 grant assignments."})
        elif attrs["visibility"] != Assistant.Visibility.PRIVATE and "access_grants" in attrs:
            raise ValidationError({"access_grants": "Grants are only accepted for selected-member visibility."})
        return attrs


@transaction.atomic
def bulk_visibility(request, tenant, *, assistants, visibility, access_grants=None):
    """Validate every row and subject before atomically applying eligible changes."""

    replace_grants = visibility == Assistant.Visibility.PRIVATE and access_grants is not None
    versions = {row["uuid"]: row["updated_at"] for row in assistants}
    rows = list(
        Assistant.objects.select_for_update()
        .filter(uuid__in=versions, tenant=tenant, is_system=False)
        .prefetch_related("access_grants")
        .order_by("uuid")
    )
    if len(rows) != len(versions):
        raise ValidationError({"assistants": "One or more assistants are unavailable."})
    grants = {(item["type"], item["id"]) for item in (access_grants or [])}
    users = {pk for kind, pk in grants if kind == "user"}
    groups = {pk for kind, pk in grants if kind == "group"}
    valid_users = set(
        get_user_model()
        .objects.filter(pk__in=users, is_active=True, membership__tenant=tenant, membership__status="active")
        .values_list("pk", flat=True)
    )
    valid_groups = set(
        TenantGroup.objects.filter(tenant=tenant, auth_group_id__in=groups).values_list("auth_group_id", flat=True)
    )
    if valid_users != users or valid_groups != groups:
        raise ValidationError({"access_grants": "One or more subjects are unavailable in this tenant."})
    changed = []
    for row in rows:
        if row.status != Assistant.Status.ACTIVE:
            continue
        if row.updated_at != versions[row.uuid]:
            raise StaleAssistantBatch()
        existing = {
            ("user", item.user_id) if item.user_id else ("group", item.group_id) for item in row.access_grants.all()
        }
        if row.visibility != visibility or (replace_grants and existing != grants):
            changed.append(row)
    now = timezone.now()
    for row in changed:
        row.visibility = visibility
        row.updated_at = now
    Assistant.objects.bulk_update(changed, ["visibility", "updated_at"])
    if replace_grants and changed:
        AssistantAccess.objects.filter(assistant__in=changed).delete()
        AssistantAccess.objects.bulk_create(
            [
                AssistantAccess(assistant=row, granted_by=request.user, **{f"{kind}_id": pk})
                for row in changed
                for kind, pk in sorted(grants)
            ]
        )
    delegation = get_active_delegation(request.user)
    record_audit(
        request=request,
        tenant=tenant,
        actor=delegation.platform_user if delegation else request.user,
        delegation=delegation,
        action="assistant.bulk_visibility",
        resource_type="assistant",
        metadata={
            "visibility": visibility,
            "updated": len(changed),
            "skipped": len(rows) - len(changed),
            "assistant_uuids": [str(row.uuid) for row in changed],
        },
    )
    return {"updated": len(changed), "skipped": len(rows) - len(changed)}


class BulkEnablementSerializer(serializers.Serializer):
    """Accept explicit versions and a runtime switch."""

    assistants = AssistantVersionSerializer(many=True, allow_empty=False, max_length=1000)
    enabled = serializers.BooleanField()

    def validate_assistants(self, value):
        """Reject duplicate identities before taking row locks."""

        if len({item["uuid"] for item in value}) != len(value):
            raise ValidationError("Duplicate assistants are not allowed.")
        return value


def enablement_issues(assistant, request):
    """Validate stored runtime dependencies without probing providers or filesystems."""

    from lens.assistant_lifecycle import AssistantNotRunnableError, fixed_collaboration_assistants
    from lens.catalog import usable_catalog_resources
    from lens.model_scope import authorized_model_ref
    from lens.models import MCPServer, Skill
    from lens.plugins.registry import PluginRegistryError
    from lens.serializers import AssistantSerializer
    from lens.services import (
        LensNodeDispatchError,
        build_loaded_mcps,
        build_loaded_plugins,
        build_loaded_skills,
        resolve_loaded_mcp_environment,
        resolve_loaded_skill_environment,
        validate_loaded_environments,
    )

    def issue(field):
        """Expose a field name instead of credentials or validation values."""
        return [{"code": "configuration_invalid", "field": field}]

    checks = management_checks([assistant], assistant.tenant)[str(assistant.uuid)]["issues"]
    if checks:
        return checks
    model_fields = (
        "agent_model_ref",
        "multimodal_model_ref",
        "decision_model_ref",
        "preprocess_model_ref",
        "postprocess_model_ref",
    )
    for field in model_fields:
        value = getattr(assistant, field)
        if value:
            try:
                authorized_model_ref(value, assistant.tenant, "decision" if field == "decision_model_ref" else "llm")
            except ValueError:
                return issue(field)
    serializer = AssistantSerializer(
        assistant,
        data={field: str(getattr(assistant, field)) for field in model_fields if getattr(assistant, field)},
        partial=True,
        context={"request": request},
    )
    try:
        valid = serializer.is_valid()
    except PluginRegistryError:
        return issue("plugin_bindings")
    if not valid:
        return [item for field in serializer.errors for item in issue(str(field))]
    if assistant.mode_handler.supports_members:
        try:
            members = fixed_collaboration_assistants(assistant, request.user)
        except AssistantNotRunnableError:
            return issue("collaboration_members")
        return issue("collaboration_members") if any(enablement_issues(member, request) for member in members) else []
    bindings = list(assistant.datasource_bindings.select_related("datasource", "item"))
    for binding in bindings:
        source = binding.datasource
        if (
            source.tenant_id != assistant.tenant_id
            or (binding.item and binding.item.datasource_id != source.pk)
            or (binding.required and (source.status != "active" or (binding.item and binding.item.status != "active")))
        ):
            return issue("datasource_bindings")
    if assistant.capability in {"knowledge_qa", "code_analysis"} and not any(
        row.datasource.status == "active" and (not row.item or row.item.status == "active") for row in bindings
    ):
        return issue("datasource_bindings")
    for relation, model, field in (
        (assistant.skill_bindings, Skill, "skill"),
        (assistant.mcp_bindings, MCPServer, "mcp"),
    ):
        selected = list(relation.filter(enabled=True).select_related(field, "environment_variable_set"))
        resource_ids = {getattr(row, f"{field}_id") for row in selected}
        if usable_catalog_resources(model.objects.filter(pk__in=resource_ids), assistant.tenant).count() != len(
            resource_ids
        ):
            return issue(f"{field}_bindings")
        if any(
            row.environment_variable_set
            and (
                row.environment_variable_set.tenant_id != assistant.tenant_id
                or not row.environment_variable_set.enabled
            )
            for row in selected
        ):
            return issue("environment")
    skills = build_loaded_skills(assistant)
    mcps = build_loaded_mcps(assistant)

    try:
        plugins = build_loaded_plugins(assistant)
    except PluginRegistryError:
        return issue("plugin_bindings")
    connections = list(assistant.plugin_bindings.filter(enabled=True).select_related("connection"))
    connections.extend(
        assistant.mcp_bindings.filter(enabled=True, mcp__transport="plugin").select_related("mcp__connection")
    )
    loaded_connections = {item["connection_uuid"] for item in plugins}
    for binding in connections:
        connection = binding.connection if hasattr(binding, "connection_id") else binding.mcp.connection
        if connection is None or str(connection.uuid) not in loaded_connections:
            return issue("plugin_bindings")
    if assistant.capability == "general_chat" and not skills and not plugins:
        return issue("skill_bindings")
    try:
        validate_loaded_environments(
            resolve_loaded_skill_environment(skills), resolve_loaded_mcp_environment(mcps), mcps
        )
    except LensNodeDispatchError:
        return issue("environment")
    return []


@transaction.atomic
def bulk_enablement(request, tenant, *, assistants, enabled):
    """Apply eligible state changes atomically while explaining skipped rows."""

    versions = {item["uuid"]: item["updated_at"] for item in assistants}
    rows = list(
        Assistant.objects.select_for_update(of=("self",))
        .filter(tenant=tenant, uuid__in=versions, is_system=False)
        .select_related("lensnode", "tenant")
        .order_by("uuid")
    )
    if len(rows) != len(versions):
        raise ValidationError({"assistants": "One or more assistants are unavailable."})
    for row in rows:
        if row.status == Assistant.Status.ACTIVE and row.updated_at != versions[row.uuid]:
            raise StaleAssistantBatch()
    changed = []
    skipped = []
    for row in rows:
        item = {"uuid": str(row.uuid)}
        if row.status != Assistant.Status.ACTIVE:
            skipped.append({**item, "reason": "archived"})
        elif row.is_enabled == enabled:
            skipped.append({**item, "reason": "unchanged"})
        else:
            issues = enablement_issues(row, request) if enabled else []
            if issues:
                skipped.append({**item, "reason": "configuration", "issues": issues})
            else:
                row.is_enabled = enabled
                row.updated_at = timezone.now()
                changed.append(row)
    Assistant.objects.bulk_update(changed, ["is_enabled", "updated_at"])
    delegation = get_active_delegation(request.user)
    record_audit(
        action="assistant.bulk_enablement",
        request=request,
        tenant=tenant,
        actor=delegation.platform_user if delegation else request.user,
        delegation=delegation,
        resource_type="assistant",
        metadata={
            "enabled": enabled,
            "assistant_uuids": [str(row.uuid) for row in changed],
            "updated": len(changed),
            "skipped": len(skipped),
        },
    )
    return {"updated": len(changed), "skipped": len(skipped), "skipped_items": skipped}
