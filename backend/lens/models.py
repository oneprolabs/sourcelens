import base64
import hashlib
import json
import uuid

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class TimestampedUUIDModel(models.Model):
    """Abstract model with a public UUID and timestamps."""

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class LensNode(TimestampedUUIDModel):
    """Long-lived distributed LensNode execution worker."""

    class Status(models.TextChoices):
        ONLINE = "online", "Online"
        OFFLINE = "offline", "Offline"
        DRAINING = "draining", "Draining"

    class EnrollmentStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    name = models.CharField(max_length=160)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.OFFLINE,
    )
    connection_id = models.CharField(max_length=128, blank=True, default="")
    workspace_path = models.CharField(max_length=500, blank=True, default="")
    available_dirs = models.JSONField(default=list, blank=True)
    protocol_version = models.CharField(max_length=32, blank=True, default="")
    agent_version = models.CharField(max_length=64, blank=True, default="")
    tasks = models.JSONField(default=list, blank=True)
    labels = models.JSONField(default=dict, blank=True)
    enrollment_status = models.CharField(
        max_length=16,
        choices=EnrollmentStatus.choices,
        default=EnrollmentStatus.PENDING,
    )
    auth_token_hash = models.CharField(max_length=128, blank=True, default="")
    token_issued_at = models.DateTimeField(null=True, blank=True)
    token_revoked = models.BooleanField(default=False)
    last_authenticated_at = models.DateTimeField(null=True, blank=True)
    last_heartbeat_at = models.DateTimeField(null=True, blank=True)
    # Set when the node's WebSocket drops, cleared on reconnect. A blue/green
    # API deploy recycles the container the node is connected to, so a
    # disconnect is not by itself proof the node's runs failed. Its runs are
    # only failed if it is still disconnected after a grace window (see
    # lens.tasks.check_lensnode_disconnect_grace_period); this timestamp both
    # schedules and episode-pins that deferred check.
    disconnected_at = models.DateTimeField(null=True, blank=True)
    registered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["status"], name="lens_lensnode_status_idx"),
            models.Index(
                fields=["enrollment_status"],
                name="lens_lensnode_enroll_idx",
            ),
        ]
        ordering = ["name"]

    def __str__(self):
        return self.name

def user_sees_all_assistants(user):
    """Return True when the user manages assistants (sees private ones).

    Keyed on the admin_console feature so role-granted admins (not only
    Django staff) can manage private assistants. Staff/superusers already
    resolve to all features.
    """

    if not (user and user.is_authenticated):
        return False
    if user.is_superuser or user.is_staff:
        return True
    from accounts.access import get_effective_feature_keys

    return "admin_console" in get_effective_feature_keys(user)


class AssistantQuerySet(models.QuerySet):
    """Queryset with visibility-aware filtering."""

    def visible_to(self, user):
        """Return assistants the user is allowed to see."""

        if user_sees_all_assistants(user):
            return self
        public = Q(visibility=Assistant.Visibility.PUBLIC)
        if not (user and user.is_authenticated):
            return self.filter(public)
        granted = Q(access_grants__user=user) | Q(
            access_grants__group__in=user.groups.all()
        )
        return self.filter(public | granted).distinct()


class Assistant(TimestampedUUIDModel):
    """Externally visible capability bound to one LensNode."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    class Visibility(models.TextChoices):
        PUBLIC = "public", "Public"
        PRIVATE = "private", "Private"

    class TokenBudgetProfile(models.TextChoices):
        STANDARD = "standard", "Standard"
        DEEP = "deep", "Deep"
        UNLIMITED = "unlimited", "Unlimited"

    class Capability(models.TextChoices):
        GENERAL_CHAT = "general_chat", "General Chat"
        CODE_ANALYSIS = "code_analysis", "Code Analysis"
        KNOWLEDGE_QA = "knowledge_qa", "Knowledge Q&A"

    class Mode(models.TextChoices):
        """Product-level Assistant modes."""

        DIRECT = "direct", "Standard Mode"
        SMART = "smart", "Smart Collaboration"

    RoutingMode = Mode

    objects = AssistantQuerySet.as_manager()

    name = models.CharField(max_length=160)
    description = models.TextField(blank=True, default="")
    routing_description = models.TextField(blank=True, default="")
    capability = models.CharField(
        max_length=24,
        choices=Capability.choices,
        default=Capability.GENERAL_CHAT,
    )
    routing_mode = models.CharField(
        max_length=16,
        choices=RoutingMode.choices,
        default=RoutingMode.DIRECT,
        db_index=True,
    )
    slug = models.SlugField(max_length=180, unique=True)
    visibility = models.CharField(
        max_length=16,
        choices=Visibility.choices,
        default=Visibility.PRIVATE,
    )
    lensnode = models.ForeignKey(
        LensNode,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assistants",
    )
    collaboration_members = models.ManyToManyField(
        "self",
        symmetrical=False,
        blank=True,
        related_name="collaboration_coordinators",
    )

    class AgentRounds(models.TextChoices):
        FLASH = "flash", "极速"
        FAST = "fast", "快速"
        BALANCED = "balanced", "均衡"
        DEEP = "deep", "深度"
        MAX = "max", "极限"

    selected_dirs = models.JSONField(default=list, blank=True)
    workspace_guide = models.TextField(blank=True, default="")
    preprocess_model_ref = models.UUIDField(null=True, blank=True)
    postprocess_model_ref = models.UUIDField(null=True, blank=True)
    multimodal_model_ref = models.UUIDField(null=True, blank=True)
    agent_model_ref = models.UUIDField(null=True, blank=True)
    agent_rounds = models.CharField(
        max_length=16,
        choices=AgentRounds.choices,
        default=AgentRounds.BALANCED,
    )
    token_budget_profile = models.CharField(
        max_length=16,
        choices=TokenBudgetProfile.choices,
        default=TokenBudgetProfile.STANDARD,
    )
    max_concurrency = models.PositiveSmallIntegerField(default=5)
    settings = models.JSONField(default=dict, blank=True)
    is_system = models.BooleanField(default=False)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
    )

    class Meta:
        indexes = [
            models.Index(
                fields=["lensnode"],
                name="lens_assistant_lensnode_idx",
            ),
        ]
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def mode(self):
        """Return the product mode while keeping routing_mode compatible."""

        return self.routing_mode

    @mode.setter
    def mode(self, value):
        """Set the product mode through the compatibility column."""

        self.routing_mode = value

    @property
    def mode_handler(self):
        """Return the polymorphic behavior object for this Assistant."""

        return assistant_mode_for(self.routing_mode)

    @property
    def is_smart(self):
        """Return whether this Assistant is a Smart Collaboration preset."""

        return self.mode_handler.key == self.Mode.SMART

    def __init__(self, *args, **kwargs):
        """Accept the removed task argument while old callers migrate."""

        selected_task = kwargs.pop("selected_task", None)
        if selected_task and "capability" not in kwargs:
            kwargs["capability"] = selected_task
        super().__init__(*args, **kwargs)

    @property
    def selected_task(self):
        """Expose the derived LensNode task for transitional callers."""

        return self.capability

    @selected_task.setter
    def selected_task(self, value):
        """Map legacy task assignments to the unified capability field."""

        self.capability = value

    def save(self, *args, **kwargs):
        """Keep legacy task updates and routing metadata synchronized."""

        update_fields = kwargs.get("update_fields")
        if update_fields and "selected_task" in update_fields:
            kwargs["update_fields"] = [
                "capability" if field == "selected_task" else field
                for field in update_fields
            ]
            update_fields = kwargs["update_fields"]
        result = super().save(*args, **kwargs)
        routing_fields = {"capability", "description", "selected_dirs"}
        if not update_fields or routing_fields.intersection(update_fields):
            _refresh_assistant_routing_description(self)
        return result

    def is_accessible_by(self, user):
        """Return True when the user may view/use this assistant."""

        if self.visibility == Assistant.Visibility.PUBLIC:
            return True
        if not (user and user.is_authenticated):
            return False
        if user_sees_all_assistants(user):
            return True
        if self.access_grants.filter(user=user).exists():
            return True
        return self.access_grants.filter(group__in=user.groups.all()).exists()

    def is_runnable_by(self, user):
        """Return True when the user may start work with this assistant."""

        return (
            self.status == Assistant.Status.ACTIVE
            and self.is_accessible_by(user)
        )


class AssistantMode:
    """Polymorphic product behavior shared by Assistant modes."""

    key = Assistant.Mode.DIRECT
    configures_execution_resources = True
    requires_skill = True
    supports_members = False

    def normalize_capability(self, capability):
        """Return the execution capability accepted by this mode."""

        return capability

    def execution_capability(self, capability):
        """Return the capability used by the runtime coordinator."""

        return capability


class DirectAssistantMode(AssistantMode):
    """Behavior for an Assistant that executes directly."""


class SmartAssistantMode(AssistantMode):
    """Behavior for a reusable Smart Collaboration Assistant."""

    key = Assistant.Mode.SMART
    configures_execution_resources = False
    requires_skill = False
    supports_members = True

    def normalize_capability(self, capability):
        """Smart mode delegates through the general_chat coordinator."""

        return Assistant.Capability.GENERAL_CHAT

    def execution_capability(self, capability):
        """Return the internal coordinator capability for Smart mode."""

        return Assistant.Capability.GENERAL_CHAT


_ASSISTANT_MODES = {
    Assistant.Mode.DIRECT: DirectAssistantMode(),
    Assistant.Mode.SMART: SmartAssistantMode(),
}


def assistant_mode_for(mode):
    """Return the behavior object for a stored Assistant mode."""

    return _ASSISTANT_MODES.get(mode, _ASSISTANT_MODES[Assistant.Mode.DIRECT])


class AssistantAccess(TimestampedUUIDModel):
    """Authorization grant for a private assistant (group or user).

    Exactly one of group/user is set. Extra columns (level, granted_by)
    are reserved for future access levels and audit.
    """

    LEVEL_VIEW = "view"

    assistant = models.ForeignKey(
        Assistant,
        on_delete=models.CASCADE,
        related_name="access_grants",
    )
    group = models.ForeignKey(
        "auth.Group",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="assistant_grants",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="assistant_grants",
    )
    level = models.CharField(max_length=16, default=LEVEL_VIEW)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=(
                    Q(group__isnull=False, user__isnull=True)
                    | Q(group__isnull=True, user__isnull=False)
                ),
                name="assistant_access_group_xor_user",
            ),
            models.UniqueConstraint(
                fields=["assistant", "group"],
                condition=Q(group__isnull=False),
                name="uniq_assistant_group",
            ),
            models.UniqueConstraint(
                fields=["assistant", "user"],
                condition=Q(user__isnull=False),
                name="uniq_assistant_user",
            ),
        ]
        indexes = [
            models.Index(
                fields=["assistant"],
                name="lens_aaccess_assistant_idx",
            ),
        ]


class Skill(TimestampedUUIDModel):
    """Global skill resource."""

    name = models.CharField(max_length=160)
    package_name = models.CharField(max_length=180, blank=True, default="")
    kind = models.CharField(max_length=32, default="standard")
    definition = models.JSONField(default=dict, blank=True)
    version = models.CharField(max_length=64, blank=True, default="1")
    enabled = models.BooleanField(default=True)
    package_path = models.CharField(max_length=700, blank=True, default="")
    package_hash = models.CharField(max_length=128, blank=True, default="")
    package_size = models.PositiveIntegerField(default=0)
    package_manifest = models.JSONField(default=dict, blank=True)
    source_type = models.CharField(max_length=32, blank=True, default="manual")
    source_url = models.CharField(max_length=1000, blank=True, default="")
    source_ref = models.CharField(max_length=255, blank=True, default="")
    source_path = models.CharField(max_length=500, blank=True, default="")
    latest_source_ref = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )
    source_checked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        """Refresh routing descriptions after changing a shared Skill."""

        result = super().save(*args, **kwargs)
        for binding in self.assistantskill_set.select_related("assistant"):
            _refresh_assistant_routing_description(binding.assistant)
        return result


class EnvironmentVariableSet(TimestampedUUIDModel):
    """Reusable encrypted environment values for Skill and MCP bindings."""

    name = models.CharField(max_length=160, unique=True)
    description = models.TextField(blank=True, default="")
    encrypted_values = models.TextField(blank=True, default="")
    enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def set_values(self, values):
        """Encrypt and store a mapping of environment-variable values."""

        payload = json.dumps(values or {}, sort_keys=True).encode("utf-8")
        self.encrypted_values = (
            _datasource_fernet().encrypt(payload).decode("utf-8")
        )

    def get_values(self):
        """Return decrypted environment-variable values."""

        if not self.encrypted_values:
            return {}
        try:
            payload = _datasource_fernet().decrypt(
                self.encrypted_values.encode("utf-8")
            )
            values = json.loads(payload.decode("utf-8"))
            return values if isinstance(values, dict) else {}
        except (InvalidToken, json.JSONDecodeError):
            return {}

    @property
    def keys(self):
        """Return configured variable names without exposing their values."""

        return sorted(
            key for key, value in self.get_values().items() if str(value or "")
        )

    def __str__(self):
        return self.name


class MCPServer(TimestampedUUIDModel):
    """Global MCP server resource."""

    class Transport(models.TextChoices):
        URL = "url", "URL"
        STDIO = "stdio", "STDIO"
        PLUGIN = "plugin", "Plugin Adapter"

    name = models.CharField(max_length=160)
    transport = models.CharField(max_length=16, choices=Transport.choices)
    endpoint = models.CharField(max_length=500, blank=True, default="")
    config = models.JSONField(default=dict, blank=True)
    environment = models.JSONField(default=list, blank=True)
    connection = models.ForeignKey(
        "Connection",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="mcp_adapters",
    )
    tools = models.JSONField(default=list, blank=True)
    version = models.CharField(max_length=64, blank=True, default="1")
    enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        """Refresh routing descriptions after changing a shared MCP."""

        result = super().save(*args, **kwargs)
        for binding in self.assistantmcp_set.select_related("assistant"):
            _refresh_assistant_routing_description(binding.assistant)
        return result


class DataSource(TimestampedUUIDModel):
    """Cataloged external data resource bound to a LensNode."""

    class SourceType(models.TextChoices):
        GIT = "git", "Git"
        FEISHU = "feishu", "Feishu"
        JIRA = "jira", "Jira"
        MANAGED_WORKSPACE = "managed_workspace", "Managed Workspace"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        DISABLED = "disabled", "Disabled"

    class AvailabilityStatus(models.TextChoices):
        UNKNOWN = "unknown", "Unknown"
        AVAILABLE = "available", "Available"
        UNAVAILABLE = "unavailable", "Unavailable"
        ERROR = "error", "Error"

    name = models.CharField(max_length=160)
    source_type = models.CharField(max_length=32, choices=SourceType.choices)
    lensnode = models.ForeignKey(
        LensNode,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="datasources",
    )
    config = models.JSONField(default=dict, blank=True)
    sync_policy = models.JSONField(default=dict, blank=True)
    target_path = models.CharField(max_length=500, blank=True, default="")
    credential = models.ForeignKey(
        "DataSourceCredential",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="datasources",
    )
    connection = models.ForeignKey(
        "Connection",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="datasources",
    )
    plugin_key = models.CharField(max_length=64, blank=True, default="")
    datasource_config = models.JSONField(default=dict, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    availability_status = models.CharField(
        max_length=16,
        choices=AvailabilityStatus.choices,
        default=AvailabilityStatus.UNKNOWN,
    )
    availability_checked_at = models.DateTimeField(null=True, blank=True)
    availability_message = models.TextField(blank=True, default="")
    last_conversion_status = models.CharField(
        max_length=20,
        blank=True,
        default="",
    )
    last_conversion_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
    )

    class Meta:
        indexes = [
            models.Index(fields=["status"], name="lens_datasource_status_idx"),
            models.Index(
                fields=["lensnode"],
                name="lens_datasource_lensnode_idx",
            ),
        ]
        ordering = ["name"]

    def __str__(self):
        return self.name


class DataSourceCredential(TimestampedUUIDModel):
    """Encrypted datasource credential used only during node execution."""

    class Provider(models.TextChoices):
        GITHUB = "github", "GitHub"
        GITLAB = "gitlab", "GitLab"
        FEISHU = "feishu", "Feishu"
        GENERIC = "generic", "Generic"

    class AuthType(models.TextChoices):
        NONE = "none", "Public Access"
        HTTPS_TOKEN = "https_token", "HTTPS Token"
        FEISHU_APP = "feishu_app", "Feishu App"

    name = models.CharField(max_length=160)
    provider = models.CharField(
        max_length=32,
        choices=Provider.choices,
        default=Provider.GENERIC,
    )
    auth_type = models.CharField(
        max_length=32,
        choices=AuthType.choices,
        default=AuthType.HTTPS_TOKEN,
    )
    encrypted_secret = models.TextField(blank=True, default="")
    endpoint_url = models.CharField(max_length=500, blank=True, default="")
    sync_scope = models.CharField(max_length=64, blank=True, default="")
    scope_config = models.JSONField(default=dict, blank=True)
    validation_status = models.CharField(max_length=32, blank=True, default="")
    validation_message = models.TextField(blank=True, default="")
    validated_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name"]

    def set_secret(self, value):
        """Encrypt and store a plaintext credential secret."""

        self.encrypted_secret = (
            _datasource_fernet()
            .encrypt(str(value or "").encode("utf-8"))
            .decode("utf-8")
        )

    def get_secret(self):
        """Return the decrypted credential secret."""

        if not self.encrypted_secret:
            return ""
        try:
            return (
                _datasource_fernet()
                .decrypt(self.encrypted_secret.encode("utf-8"))
                .decode("utf-8")
            )
        except InvalidToken:
            return ""

    @property
    def has_secret(self):
        """Return whether this credential has an encrypted secret."""

        return bool(self.encrypted_secret)

    def __str__(self):
        return self.name


class SecretMaterial(TimestampedUUIDModel):
    """Stable identity for encrypted integration authentication material."""

    name = models.CharField(max_length=160)
    status = models.CharField(max_length=16, default="active")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class SecretVersion(TimestampedUUIDModel):
    """One encrypted version of reusable authentication material."""

    material = models.ForeignKey(
        SecretMaterial,
        on_delete=models.CASCADE,
        related_name="versions",
    )
    encrypted_value = models.TextField()
    status = models.CharField(max_length=16, default="active")

    class Meta:
        ordering = ["-created_at"]

    def set_value(self, value):
        """Encrypt and store one secret version value."""

        self.encrypted_value = (
            _datasource_fernet()
            .encrypt(str(value or "").encode("utf-8"))
            .decode("utf-8")
        )

    def get_value(self):
        """Return the decrypted secret value for trusted runtime use."""

        if not self.encrypted_value:
            return ""
        try:
            return _datasource_fernet().decrypt(
                self.encrypted_value.encode("utf-8")
            ).decode("utf-8")
        except InvalidToken:
            return ""


class Connection(TimestampedUUIDModel):
    """Current approved plugin authentication and platform access policy."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        DISABLED = "disabled", "Disabled"

    name = models.CharField(max_length=160)
    plugin_key = models.CharField(max_length=64)
    endpoint = models.URLField(max_length=500)
    config = models.JSONField(default=dict, blank=True)
    allowed_scope = models.JSONField(default=dict, blank=True)
    secret_version = models.ForeignKey(
        SecretVersion,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="connections",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class LegacyIntegrationMigration(TimestampedUUIDModel):
    """Audit one reversible legacy credential or datasource migration."""

    class SourceKind(models.TextChoices):
        CREDENTIAL = "credential", "Credential"
        DATASOURCE = "datasource", "Datasource"

    class Status(models.TextChoices):
        MIGRATED = "migrated", "Migrated"
        MANUAL_REVIEW = "manual_review", "Manual Review"
        ROLLED_BACK = "rolled_back", "Rolled Back"

    source_kind = models.CharField(max_length=16, choices=SourceKind.choices)
    source_uuid = models.UUIDField()
    status = models.CharField(max_length=24, choices=Status.choices)
    reason = models.CharField(max_length=64, blank=True, default="")
    connection = models.ForeignKey(
        Connection,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="legacy_migration_records",
    )
    datasource = models.ForeignKey(
        DataSource,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="legacy_migration_records",
    )
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["source_kind", "source_uuid"]
        constraints = [
            models.UniqueConstraint(
                fields=["source_kind", "source_uuid"],
                name="lens_legacy_migration_source_uniq",
            )
        ]


class ExecutionSnapshot(TimestampedUUIDModel):
    """Immutable resolved configuration used by a started plugin operation."""

    class Kind(models.TextChoices):
        DATASOURCE_SYNC = "datasource_sync", "Datasource Sync"
        TOOL_INVOKE = "tool_invoke", "Tool Invoke"

    kind = models.CharField(max_length=32, choices=Kind.choices)
    connection = models.ForeignKey(
        Connection,
        on_delete=models.PROTECT,
        related_name="execution_snapshots",
    )
    datasource = models.ForeignKey(
        DataSource,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="execution_snapshots",
    )
    run = models.ForeignKey(
        "Run",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="plugin_execution_snapshots",
    )
    secret_version = models.ForeignKey(
        SecretVersion,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="execution_snapshots",
    )
    plugin_key = models.CharField(max_length=64)
    plugin_version = models.CharField(max_length=32)
    protocol_version = models.PositiveIntegerField()
    tool_key = models.CharField(max_length=128, blank=True, default="")
    invocation_id = models.CharField(max_length=128, blank=True, default="")
    resolved_config = models.JSONField(default=dict)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(
                        kind="datasource_sync",
                        datasource__isnull=False,
                        run__isnull=True,
                        tool_key="",
                        invocation_id="",
                    )
                    | (
                        Q(
                            kind="tool_invoke",
                            datasource__isnull=True,
                            run__isnull=False,
                        )
                        & ~Q(tool_key="")
                        & ~Q(invocation_id="")
                    )
                ),
                name="lens_snapshot_owner_kind_ck",
            ),
            models.UniqueConstraint(
                fields=["run", "invocation_id"],
                condition=Q(kind="tool_invoke"),
                name="lens_snap_run_invocation_uniq",
            ),
        ]


class CredentialLease(TimestampedUUIDModel):
    """Short-lived authorization handle bound to one execution snapshot."""

    snapshot = models.ForeignKey(
        ExecutionSnapshot,
        on_delete=models.PROTECT,
        related_name="credential_leases",
    )
    lensnode = models.ForeignKey(
        LensNode,
        on_delete=models.PROTECT,
        related_name="credential_leases",
    )
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class PluginInvocation(TimestampedUUIDModel):
    """Secret-free audit record for one authorized Plugin execution."""

    class Status(models.TextChoices):
        AUTHORIZED = "authorized", "Authorized"
        MATERIALIZED = "materialized", "Materialized"

    snapshot = models.OneToOneField(
        ExecutionSnapshot,
        on_delete=models.PROTECT,
        related_name="invocation_audit",
    )
    connection = models.ForeignKey(
        Connection,
        on_delete=models.PROTECT,
        related_name="plugin_invocations",
    )
    datasource = models.ForeignKey(
        DataSource,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="plugin_invocations",
    )
    run = models.ForeignKey(
        "Run",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="plugin_invocations",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="plugin_invocations",
    )
    lensnode = models.ForeignKey(
        LensNode,
        on_delete=models.PROTECT,
        related_name="plugin_invocations",
    )
    kind = models.CharField(max_length=32, choices=ExecutionSnapshot.Kind.choices)
    plugin_key = models.CharField(max_length=64)
    tool_key = models.CharField(max_length=128, blank=True, default="")
    capability = models.CharField(max_length=128, blank=True, default="")
    resource_summary = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.AUTHORIZED,
    )
    materialized_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


def _datasource_fernet():
    """Return the symmetric encryptor for datasource credentials."""

    digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def _refresh_assistant_routing_description(assistant):
    """Refresh one Assistant's non-sensitive smart-routing synopsis."""

    from .routing_descriptions import refresh_routing_description

    refresh_routing_description(assistant)


class AssistantSkill(models.Model):
    """Assistant to skill binding."""

    assistant = models.ForeignKey(
        Assistant,
        on_delete=models.CASCADE,
        related_name="skill_bindings",
    )
    skill = models.ForeignKey(Skill, on_delete=models.PROTECT)
    environment_variable_set = models.ForeignKey(
        EnvironmentVariableSet,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="skill_bindings",
    )
    enabled = models.BooleanField(default=True)
    load_config = models.JSONField(default=dict, blank=True)

    class Meta:
        unique_together = [("assistant", "skill")]

    def save(self, *args, **kwargs):
        """Refresh the owning Assistant after changing its Skill binding."""

        result = super().save(*args, **kwargs)
        _refresh_assistant_routing_description(self.assistant)
        return result

    def delete(self, *args, **kwargs):
        """Refresh the owning Assistant after deleting its Skill binding."""

        assistant = self.assistant
        result = super().delete(*args, **kwargs)
        _refresh_assistant_routing_description(assistant)
        return result


class AssistantMCP(models.Model):
    """Assistant to MCP binding."""

    assistant = models.ForeignKey(
        Assistant,
        on_delete=models.CASCADE,
        related_name="mcp_bindings",
    )
    mcp = models.ForeignKey(MCPServer, on_delete=models.PROTECT)
    environment_variable_set = models.ForeignKey(
        EnvironmentVariableSet,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="mcp_bindings",
    )
    enabled = models.BooleanField(default=True)
    load_config = models.JSONField(default=dict, blank=True)

    class Meta:
        unique_together = [("assistant", "mcp")]

    def save(self, *args, **kwargs):
        """Refresh the owning Assistant after changing its MCP binding."""

        result = super().save(*args, **kwargs)
        _refresh_assistant_routing_description(self.assistant)
        return result

    def delete(self, *args, **kwargs):
        """Refresh the owning Assistant after deleting its MCP binding."""

        assistant = self.assistant
        result = super().delete(*args, **kwargs)
        _refresh_assistant_routing_description(assistant)
        return result


class AssistantPluginBinding(models.Model):
    """Assistant access to one reusable Plugin connection.

    ``tools`` is retained as a legacy compatibility field. Direct Assistant
    runtime loading always uses the complete read-only Plugin manifest.
    """

    assistant = models.ForeignKey(
        Assistant,
        on_delete=models.CASCADE,
        related_name="plugin_bindings",
    )
    connection = models.ForeignKey(
        Connection,
        on_delete=models.PROTECT,
        related_name="assistant_bindings",
    )
    tools = models.JSONField(default=list, blank=True)
    enabled = models.BooleanField(default=True)

    class Meta:
        unique_together = [("assistant", "connection")]


class Session(TimestampedUUIDModel):
    """Conversation session for a user and assistant."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    class RoutingMode(models.TextChoices):
        DIRECT = "direct", "Direct"
        SMART = "smart", "Smart Collaboration"

    class TitleGenerationStatus(models.TextChoices):
        SKIPPED = "skipped", "Skipped"
        PENDING = "pending", "Pending"
        GENERATING = "generating", "Generating"
        GENERATED = "generated", "Generated"
        FAILED = "failed", "Failed"

    assistant = models.ForeignKey(Assistant, on_delete=models.PROTECT)
    routing_mode = models.CharField(
        max_length=16,
        choices=RoutingMode.choices,
        default=RoutingMode.DIRECT,
    )
    allowed_assistant_uuids = models.JSONField(default=list, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE
    )
    title = models.CharField(max_length=160, blank=True, default="")
    title_manually_edited = models.BooleanField(
        default=False,
        db_default=False,
    )
    title_generation_status = models.CharField(
        max_length=16,
        choices=TitleGenerationStatus.choices,
        default=TitleGenerationStatus.SKIPPED,
        db_default=TitleGenerationStatus.SKIPPED,
    )
    pinned_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title or str(self.uuid)


class Message(models.Model):
    """Message within a session."""

    class Role(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"
        SYSTEM = "system", "System"

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    session = models.ForeignKey(Session, on_delete=models.CASCADE)
    role = models.CharField(max_length=16, choices=Role.choices)
    content = models.TextField(blank=True, default="")
    run = models.ForeignKey(
        "Run",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="messages",
    )
    sequence = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["session", "sequence"],
                name="lens_message_session_seq_idx",
            ),
        ]
        unique_together = [("session", "sequence")]
        ordering = ["sequence"]


def message_attachment_upload_to(instance, filename):
    """Return the storage path for a message image attachment."""

    return f"lens/attachments/{instance.uuid}/{filename}"


class MessageAttachment(TimestampedUUIDModel):
    """User-uploaded image attached to a question message.

    Uploaded in a first step bound only to the session, then linked to
    the USER input message when the run is created. Keeping the link to
    the message makes the image-question relationship queryable for the
    lifetime of the run (Run.input_message.attachments), so a later
    public-share feature can snapshot the right images.
    """

    class Kind(models.TextChoices):
        IMAGE = "image", "Image"

    session = models.ForeignKey(
        Session,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    message = models.ForeignKey(
        Message,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="lens_attachments",
    )
    kind = models.CharField(
        max_length=16,
        choices=Kind.choices,
        default=Kind.IMAGE,
    )
    file = models.ImageField(upload_to=message_attachment_upload_to)
    original_name = models.CharField(max_length=255, blank=True, default="")
    mime_type = models.CharField(max_length=80, blank=True, default="")
    byte_size = models.PositiveIntegerField(default=0)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "created_at"]
        indexes = [
            models.Index(
                fields=["message", "order"],
                name="lens_attach_msg_order_idx",
            ),
            models.Index(
                fields=["session"],
                name="lens_attach_session_idx",
            ),
        ]


class Run(models.Model):
    """Execution run for a session message."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        STREAMING = "streaming", "Streaming"
        AWAITING_USER_INPUT = "awaiting_user_input", "Awaiting user input"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    class Outcome(models.TextChoices):
        COMPLETED = "completed", "Completed"
        PARTIAL = "partial", "Partial"
        BLOCKED = "blocked", "Blocked"

    class Feedback(models.TextChoices):
        POSITIVE = "positive", "Positive"
        NEGATIVE = "negative", "Negative"

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    session = models.ForeignKey(Session, on_delete=models.CASCADE)
    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.QUEUED,
        db_index=True,
    )
    input_message = models.ForeignKey(
        Message,
        on_delete=models.PROTECT,
        related_name="request_runs",
    )
    output_message = models.ForeignKey(
        Message,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="response_runs",
    )
    retry_of_run = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="retry_runs",
    )
    parent_run = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="delegated_runs",
    )
    lensnode = models.ForeignKey(
        LensNode,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="runs",
    )
    metering_ref = models.UUIDField(null=True, blank=True)
    error = models.TextField(blank=True, default="")
    outcome = models.CharField(
        max_length=16,
        choices=Outcome.choices,
        blank=True,
        default="",
    )
    termination_detail = models.JSONField(default=dict, blank=True)
    citations = models.JSONField(default=list, blank=True)
    planned_evidence = models.JSONField(default=dict, blank=True)
    feedback = models.CharField(
        max_length=16,
        choices=Feedback.choices,
        blank=True,
        default="",
    )
    feedback_updated_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    last_activity_at = models.DateTimeField(null=True, blank=True)
    resume_by = models.DateTimeField(null=True, blank=True)
    clarification_answered_at = models.DateTimeField(null=True, blank=True)
    idempotency_key = models.CharField(max_length=128, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["session", "idempotency_key"],
                condition=~Q(idempotency_key=""),
                name="lens_run_idem_nonempty_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=["session", "status"],
                name="lens_run_session_status_idx",
            ),
            models.Index(fields=["lensnode"], name="lens_run_lensnode_idx"),
            models.Index(fields=["parent_run"], name="lens_run_parent_idx"),
        ]
        ordering = ["-started_at", "-created_at"]


class RunStep(models.Model):
    """Execution step for a run."""

    class StepType(models.TextChoices):
        QUERY_REWRITE = "query_rewrite", "Query Rewrite"
        MULTIMODAL = "multimodal", "Multimodal"
        RETRIEVAL = "retrieval", "Retrieval"
        GENERAL_CHAT = "general_chat", "General Chat"
        ANSWER = "answer", "Answer"
        STREAM = "stream", "Stream"

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    run = models.ForeignKey(
        Run, on_delete=models.CASCADE, related_name="steps"
    )
    step_type = models.CharField(max_length=32, choices=StepType.choices)
    detail = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices)
    sequence = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["run", "sequence"],
                name="lens_runstep_run_seq_idx",
            ),
        ]
        unique_together = [("run", "sequence")]
        ordering = ["sequence"]


class RunTraceEvent(models.Model):
    """Immutable, ordered observation event for one run."""

    uuid = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    run = models.ForeignKey(
        Run,
        on_delete=models.CASCADE,
        related_name="trace_events",
    )
    event_id = models.UUIDField()
    sequence = models.PositiveBigIntegerField()
    attempt = models.PositiveIntegerField(default=1)
    event_type = models.CharField(max_length=128)
    timestamp = models.DateTimeField()
    checkpoint_id = models.CharField(max_length=128, blank=True, default="")
    turn = models.PositiveIntegerField(null=True, blank=True)
    step = models.PositiveIntegerField(null=True, blank=True)
    call_id = models.CharField(max_length=128, blank=True, default="")
    parent_call_id = models.CharField(
        max_length=128,
        blank=True,
        default="",
    )
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["run", "event_id"],
                name="lens_trace_run_event_uniq",
            ),
            models.UniqueConstraint(
                fields=["run", "sequence"],
                name="lens_trace_run_seq_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=["run", "sequence"],
                name="lens_trace_run_seq_idx",
            ),
            models.Index(
                fields=["run", "call_id"],
                name="lens_trace_run_call_idx",
            ),
            models.Index(
                fields=["run", "event_type"],
                name="lens_trace_run_type_idx",
            ),
        ]


class RunExecution(models.Model):
    """Per-run execution snapshot dispatched to a LensNode."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        DISPATCHED = "dispatched", "Dispatched"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    run = models.OneToOneField(
        Run,
        on_delete=models.CASCADE,
        related_name="execution",
    )
    lensnode = models.ForeignKey(LensNode, on_delete=models.PROTECT)
    task = models.CharField(max_length=160)
    loaded_skills = models.JSONField(default=list, blank=True)
    loaded_mcps = models.JSONField(default=list, blank=True)
    loaded_plugins = models.JSONField(default=list, blank=True)
    target_dirs = models.JSONField(default=list, blank=True)
    runtime_snapshot = models.JSONField(default=dict, blank=True)
    agent_rounds = models.CharField(
        max_length=16,
        choices=Assistant.AgentRounds.choices,
        null=True,
        default=None,
    )
    run_timeout_s = models.PositiveIntegerField(null=True, default=None)
    dispatch_id = models.UUIDField(null=True, blank=True, editable=False)
    admitted_at = models.DateTimeField(null=True, blank=True)
    checkpoint_ready_at = models.DateTimeField(null=True, blank=True)
    token_budget_profile = models.CharField(
        max_length=16,
        choices=Assistant.TokenBudgetProfile.choices,
        default=Assistant.TokenBudgetProfile.STANDARD,
    )
    token_budget_max_tokens = models.PositiveIntegerField(default=200000)
    token_budget_final_reserve_tokens = models.PositiveIntegerField(
        default=40000
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.QUEUED,
    )
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["lensnode"], name="lens_runexec_lensnode_idx"
            ),
        ]


class RunDiagnosticEvidence(TimestampedUUIDModel):
    """Immutable, privacy-bounded evidence captured for one Run."""

    run = models.OneToOneField(
        Run,
        on_delete=models.CASCADE,
        related_name="diagnostic_evidence",
    )
    schema_version = models.PositiveSmallIntegerField(default=1)
    payload = models.JSONField(default=dict)
    payload_hash = models.CharField(max_length=64)

    def save(self, *args, **kwargs):
        """Persist a new snapshot and reject later mutation."""

        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Run diagnostic evidence is immutable.")
        if not self.payload_hash:
            serialized = json.dumps(
                self.payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            self.payload_hash = hashlib.sha256(serialized.encode()).hexdigest()
        super().save(*args, **kwargs)


class RunDiagnostic(TimestampedUUIDModel):
    """Asynchronous evidence-backed analysis of one terminal Run."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    run = models.ForeignKey(
        Run,
        on_delete=models.CASCADE,
        related_name="diagnostics",
    )
    evidence = models.ForeignKey(
        RunDiagnosticEvidence,
        on_delete=models.PROTECT,
        related_name="diagnostics",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="requested_run_diagnostics",
    )
    language = models.CharField(
        max_length=16,
        default="en",
        help_text="UI language active when the diagnosis was requested.",
    )
    progress = models.JSONField(
        default=dict,
        blank=True,
        help_text="Execution stage and detail while the diagnosis is pending.",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.QUEUED,
    )
    deterministic_findings = models.JSONField(default=list, blank=True)
    model_ref = models.UUIDField(null=True, blank=True)
    model_config_hash = models.CharField(max_length=64, blank=True, default="")
    prompt_version = models.CharField(
        max_length=32, default="run-diagnosis-v1"
    )
    result = models.JSONField(default=dict, blank=True)
    usage = models.JSONField(default=dict, blank=True)
    error_code = models.CharField(max_length=64, blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    superseded_by = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="superseded_diagnostics",
    )
    idempotency_key = models.CharField(max_length=128, default="initial-v1")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["run", "idempotency_key"],
                name="lens_diag_run_idem_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=["run", "status"],
                name="lens_diag_run_status_idx",
            ),
        ]
        permissions = [
            (
                "run_diagnostics",
                "Can run evidence-backed diagnostics",
            ),
        ]


class RunDiagnosticTurn(TimestampedUUIDModel):
    """Controlled follow-up bound to one diagnosis and evidence snapshot."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    diagnostic = models.ForeignKey(
        RunDiagnostic,
        on_delete=models.CASCADE,
        related_name="turns",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="requested_run_diagnostic_turns",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.QUEUED,
    )
    question = models.TextField(max_length=2000)
    answer = models.TextField(blank=True, default="")
    evidence_refs = models.JSONField(default=list, blank=True)
    usage = models.JSONField(default=dict, blank=True)
    error_code = models.CharField(max_length=64, blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    idempotency_key = models.CharField(max_length=64)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["diagnostic", "idempotency_key"],
                name="lens_diag_turn_idem_uniq",
            ),
        ]
        ordering = ["created_at"]


class RunTraceExport(TimestampedUUIDModel):
    """Outbox state for optional non-blocking Langfuse export."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        RETRYING = "retrying", "Retrying"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped"

    run = models.OneToOneField(
        Run,
        on_delete=models.CASCADE,
        related_name="trace_export",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error_category = models.CharField(
        max_length=64,
        blank=True,
        default="",
    )
    exported_at = models.DateTimeField(null=True, blank=True)


class ScheduledTask(TimestampedUUIDModel):
    """Scheduled task mirror for UI reporting."""

    class TaskType(models.TextChoices):
        SOURCE_SYNC = "source_sync", "Source Sync"
        LENSNODE_CLEANUP = "lensnode_cleanup", "LensNode Cleanup"
        RUN_RETENTION = "run_retention", "Run Retention"
        LENSNODE_HEALTH = "lensnode_health", "LensNode Health"

    class Status(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        RUNNING = "running", "Running"

    name = models.CharField(max_length=200)
    task_type = models.CharField(max_length=32, choices=TaskType.choices)
    periodic_task_ref = models.IntegerField(null=True, blank=True)
    target_type = models.CharField(max_length=64, null=True, blank=True)
    target_id = models.UUIDField(null=True, blank=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(
        max_length=16,
        choices=Status.choices,
        null=True,
        blank=True,
    )
    last_error = models.TextField(blank=True, default="")
    last_metrics = models.JSONField(default=dict, blank=True)
    enabled = models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["task_type"],
                name="lens_sched_task_type_idx",
            ),
            models.Index(
                fields=["target_type", "target_id"],
                name="lens_sched_target_idx",
            ),
        ]


class GlobalSetting(models.Model):
    """Global JSON setting."""

    key = models.CharField(max_length=190, primary_key=True)
    value = models.JSONField(default=dict, blank=True)
    description = models.CharField(max_length=255, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)


class SharedQA(TimestampedUUIDModel):
    """Immutable snapshot of one Q&A turn shared with eligible viewers.

    The question and answer text are snapshotted at share time so the
    public page stays stable and decoupled from the private session
    lifecycle (deleting the source session/run does not break the share).
    """

    class Status(models.TextChoices):
        PUBLISHED = "published", "Published"
        HIDDEN = "hidden", "Hidden"

    token = models.CharField(max_length=64, unique=True)
    run = models.ForeignKey(
        Run,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="shares",
    )
    assistant = models.ForeignKey(
        Assistant,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="shared_qas",
    )
    assistant_name = models.CharField(max_length=160, blank=True, default="")
    assistant_slug = models.SlugField(max_length=180, blank=True, default="")
    question = models.TextField(blank=True, default="")
    answer = models.TextField(blank=True, default="")
    content_language = models.CharField(
        max_length=16,
        blank=True,
        default="",
        help_text="Language used by the generated Q&A content.",
    )
    title = models.CharField(max_length=200, blank=True, default="")
    is_listed = models.BooleanField(default=False)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PUBLISHED,
    )
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="shared_qas",
    )
    view_count = models.PositiveIntegerField(default=0)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=[
                    "assistant",
                    "content_language",
                    "is_listed",
                    "status",
                    "-published_at",
                ],
                name="lens_sharedqa_list_idx",
            ),
        ]
        ordering = ["-published_at", "-created_at"]

    def __str__(self):
        return self.title or str(self.token)


def deliverable_storage():
    """Return the named 'deliverables' storage backend.

    Resolved lazily so swapping it from local disk to object storage
    (django-storages S3/OSS) is a settings change only — see issue #14.
    """

    from django.core.files.storage import storages

    return storages["deliverables"]


def shared_qa_file_upload_to(instance, filename):
    """Return an isolated storage path for one shared file snapshot."""

    return f"shared-qa/{instance.share.uuid}/{instance.kind}/{filename}"


def run_output_file_upload_to(instance, filename):
    """Return the deliverables path: <assistant>/<session>/<filename>.

    Grouping by assistant then session keeps one conversation's files
    together and gives session-scoped cleanup a natural unit.
    """

    return f"{instance.assistant.uuid}/{instance.session.uuid}/{filename}"


class RunOutputFile(TimestampedUUIDModel):
    """A file a LensNode run produced and delivered for user download.

    The bytes are uploaded to the control plane at produce time and held
    in the 'deliverables' storage; the control plane never reads the
    node's volume. Grouped by assistant/session for isolation and
    session-scoped cleanup, and linked to the assistant message so the
    frontend can offer the download under that answer.
    """

    run = models.ForeignKey(
        Run,
        on_delete=models.CASCADE,
        related_name="output_files",
    )
    message = models.ForeignKey(
        Message,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="output_files",
    )
    session = models.ForeignKey(
        Session,
        on_delete=models.CASCADE,
        related_name="output_files",
    )
    assistant = models.ForeignKey(
        Assistant,
        on_delete=models.CASCADE,
        related_name="output_files",
    )
    file = models.FileField(
        storage=deliverable_storage,
        upload_to=run_output_file_upload_to,
    )
    filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=120, blank=True, default="")
    byte_size = models.PositiveIntegerField(default=0)
    content_hash = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["message"], name="lens_outfile_msg_idx"),
            models.Index(fields=["session"], name="lens_outfile_sess_idx"),
        ]

    def __str__(self):
        return self.filename or str(self.uuid)


class SharedQAFile(TimestampedUUIDModel):
    """Share-owned immutable copy of one user-visible turn file."""

    class Kind(models.TextChoices):
        INPUT = "input", "Input attachment"
        OUTPUT = "output", "Output deliverable"

    share = models.ForeignKey(
        SharedQA,
        on_delete=models.CASCADE,
        related_name="files",
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    source_uuid = models.UUIDField(null=True, blank=True)
    file = models.FileField(
        storage=deliverable_storage,
        upload_to=shared_qa_file_upload_to,
    )
    filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=120, blank=True, default="")
    byte_size = models.PositiveIntegerField(default=0)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["kind", "order", "created_at"]
        indexes = [
            models.Index(
                fields=["share", "kind", "order"],
                name="lens_sharefile_order_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["share", "kind", "source_uuid"],
                name="lens_sharefile_source_uniq",
            ),
        ]

    def __str__(self):
        return self.filename or str(self.uuid)
