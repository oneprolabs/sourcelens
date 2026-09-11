import base64
import os
import time
import uuid
from pathlib import PurePosixPath

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.cache import cache
from django.utils import timezone

from .models import DataSource, DataSourceCredential, GlobalSetting, LensNode
from .plugins.snapshots import create_datasource_sync_snapshot
from .services import lensnode_group_name

WORKSPACE_ROOT = "/workspace"
DATASOURCE_SYNC_TIMEOUT_SETTING = "lens.datasource_sync.timeout_s"
DATASOURCE_CONVERSION_TIMEOUT_SETTING = "lens.datasource_conversion.timeout_s"
DATASOURCE_UPLOAD_TIMEOUT_SETTING = "lens.datasource_upload.timeout_s"
DATASOURCE_SYNC_WORKERS_SETTING = "lens.datasource_sync.workers"
DATASOURCE_CONVERSION_VISION_MODEL_SETTING = (
    "lens.datasource_conversion.vision_model_ref"
)
DATASOURCE_CONVERSION_DOCUMENT_MODEL_SETTING = (
    "lens.datasource_conversion.document_model_ref"
)
DEFAULT_DATASOURCE_SYNC_TIMEOUT_S = 21600
DEFAULT_DATASOURCE_CONVERSION_TIMEOUT_S = 86400
DEFAULT_DATASOURCE_UPLOAD_TIMEOUT_S = 600
DEFAULT_DATASOURCE_SYNC_WORKERS = 4
DATASOURCE_RESULT_POLL_S = 0.5
DATASOURCE_UPLOAD_MAX_BYTES = 50 * 1024 * 1024
DATASOURCE_UPLOAD_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".zip",
    ".tar",
    ".gz",
    ".tgz",
}


class DataSourcePathError(ValueError):
    """Raised when a datasource target path is invalid."""


class DataSourceDispatchError(RuntimeError):
    """Raised when a datasource command cannot reach a LensNode."""


def normalize_workspace_target_path(value, workspace_path=WORKSPACE_ROOT):
    """Return a safe absolute target path under a LensNode workspace."""

    raw = str(value or "").strip()
    workspace = str(workspace_path or WORKSPACE_ROOT).rstrip("/")
    if not workspace.startswith("/"):
        raise DataSourcePathError("LENS_SOURCE_WORKSPACE_PATH_INVALID")

    if not raw:
        raise DataSourcePathError("LENS_SOURCE_TARGET_PATH_REQUIRED")

    if raw == workspace:
        raise DataSourcePathError("LENS_SOURCE_TARGET_PATH_REQUIRED")

    if raw.startswith(f"{workspace}/"):
        relative = raw[len(workspace) + 1 :]
    else:
        if raw.startswith("/"):
            raise DataSourcePathError("LENS_SOURCE_TARGET_PATH_INVALID")
        relative = raw

    path = PurePosixPath(relative)
    if path.is_absolute():
        raise DataSourcePathError("LENS_SOURCE_TARGET_PATH_INVALID")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise DataSourcePathError("LENS_SOURCE_TARGET_PATH_INVALID")

    return f"{workspace}/{path.as_posix()}"


def validate_datasource_lensnode(lensnode):
    """Validate that a LensNode can execute datasource work."""

    if lensnode is None:
        raise DataSourceDispatchError("LENSNODE_REQUIRED")
    if lensnode.status != LensNode.Status.ONLINE:
        raise DataSourceDispatchError("LENSNODE_OFFLINE")
    if lensnode.enrollment_status != LensNode.EnrollmentStatus.APPROVED:
        raise DataSourceDispatchError("LENSNODE_NOT_APPROVED")
    if lensnode.token_revoked:
        raise DataSourceDispatchError("LENSNODE_TOKEN_REVOKED")


def resolve_datasource_lensnode(datasource):
    """Resolve an approved online node for datasource execution."""

    if datasource.lensnode_id:
        validate_datasource_lensnode(datasource.lensnode)
        return datasource.lensnode
    node = LensNode.objects.filter(
        status=LensNode.Status.ONLINE,
        enrollment_status=LensNode.EnrollmentStatus.APPROVED,
        token_revoked=False,
    ).order_by("updated_at").first()
    validate_datasource_lensnode(node)
    return node


def _send_lensnode_command(lensnode, payload):
    """Send a datasource command to a connected LensNode."""

    validate_datasource_lensnode(lensnode)
    channel_layer = get_channel_layer()
    if channel_layer is None:
        raise DataSourceDispatchError("LENS_CHANNEL_LAYER_UNAVAILABLE")

    message = {
        "type": "lensnode.command",
        "payload": payload,
    }
    if lensnode.connection_id:
        async_to_sync(channel_layer.send)(lensnode.connection_id, message)
        return
    async_to_sync(channel_layer.group_send)(
        lensnode_group_name(lensnode.uuid),
        message,
    )


def _lensnode_gateway_config():
    """Return the credentials LensNode needs for AI Gateway calls."""

    return {
        "ai_gateway_url": os.getenv("LENSNODE_AI_GATEWAY_URL", ""),
        "lensnode_token": os.getenv("LENSNODE_TOKEN", ""),
    }


def _wait_cache_result(cache_key, timeout_s):
    """Wait for a LensNode result cached by the WebSocket consumer."""

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        result = cache.get(cache_key)
        if result is not None:
            cache.delete(cache_key)
            return result
        time.sleep(DATASOURCE_RESULT_POLL_S)
    raise DataSourceDispatchError("LENSNODE_RESULT_TIMEOUT")


def get_datasource_sync_timeout_s():
    """Return the configured datasource sync result timeout in seconds."""

    from .models import GlobalSetting

    setting = GlobalSetting.objects.filter(
        key=DATASOURCE_SYNC_TIMEOUT_SETTING
    ).first()
    try:
        value = int(setting.value) if setting is not None else 0
    except (TypeError, ValueError):
        value = 0
    return value if value > 0 else DEFAULT_DATASOURCE_SYNC_TIMEOUT_S


def get_datasource_sync_max_workers():
    """Return the configured datasource sync worker count."""

    from .models import GlobalSetting

    setting = GlobalSetting.objects.filter(
        key=DATASOURCE_SYNC_WORKERS_SETTING
    ).first()
    try:
        value = int(setting.value) if setting is not None else 0
    except (TypeError, ValueError):
        value = 0
    return value if value > 0 else DEFAULT_DATASOURCE_SYNC_WORKERS


def get_datasource_conversion_timeout_s():
    """Return the long-running managed conversion lease timeout."""

    setting = GlobalSetting.objects.filter(
        key=DATASOURCE_CONVERSION_TIMEOUT_SETTING
    ).first()
    try:
        value = int(setting.value) if setting is not None else 0
    except (TypeError, ValueError):
        value = 0
    return value if value > 0 else DEFAULT_DATASOURCE_CONVERSION_TIMEOUT_S


def get_datasource_upload_timeout_s():
    """Return the managed workspace upload timeout in seconds."""

    setting = GlobalSetting.objects.filter(
        key=DATASOURCE_UPLOAD_TIMEOUT_SETTING
    ).first()
    try:
        value = int(setting.value) if setting is not None else 0
    except (TypeError, ValueError):
        value = 0
    return value if value > 0 else DEFAULT_DATASOURCE_UPLOAD_TIMEOUT_S


def get_datasource_upload_limits():
    """Return positive upload limits from global settings."""

    defaults = {
        "max_bytes": 50 * 1024 * 1024,
        "max_extracted_bytes": 100 * 1024 * 1024,
        "max_extracted_files": 300,
    }
    prefix = "lens.datasource_upload."
    rows = {
        row.key: row.value
        for row in GlobalSetting.objects.filter(
            key__in=[prefix + key for key in defaults]
        )
    }
    result = {}
    for key, default in defaults.items():
        value = rows.get(prefix + key, default)
        result[key] = (
            value
            if type(value) is int and value > 0
            else default
        )
    return result


def check_datasource_path(lensnode, target_path, source_type, config=None):
    """Ask a LensNode to inspect a datasource target path."""

    target_path = normalize_workspace_target_path(
        target_path,
        lensnode.workspace_path,
    )
    request_id = uuid.uuid4().hex
    _send_lensnode_command(
        lensnode,
        {
            "type": "datasource_check_path",
            "request_id": request_id,
            "target_path": target_path,
            "source_type": source_type,
            "config": config or {},
        },
    )
    return _wait_cache_result(
        f"lens:datasource_path:{request_id}",
        timeout_s=10,
    )


def list_datasource_files(datasource, page=1, page_size=20, **filters):
    """Return a paginated catalog from the datasource's storage."""

    if datasource.lensnode_id is None:
        from .datasource_catalog import list_stored_datasource_files

        return list_stored_datasource_files(
            datasource, page=page, page_size=page_size, **filters
        )

    datasource = DataSource.objects.select_related("lensnode").get(
        pk=datasource.pk
    )
    target_path = normalize_workspace_target_path(
        datasource.target_path,
        datasource.lensnode.workspace_path,
    )
    request_id = uuid.uuid4().hex
    _send_lensnode_command(
        datasource.lensnode,
        {
            "type": "datasource_list_files",
            "request_id": request_id,
            "datasource_uuid": str(datasource.uuid),
            "source_type": datasource.source_type,
            "target_path": target_path,
            "page": page,
            "page_size": page_size,
            "query": filters.get("query") or "",
            "sync_status": filters.get("sync_status") or "",
            "conversion_status": filters.get("conversion_status") or "",
        },
    )
    return _wait_cache_result(
        f"lens:datasource_files:{request_id}",
        timeout_s=15,
    )


def test_datasource_connection(
    lensnode,
    source_type,
    config=None,
    datasource_uuid=None,
    credential_uuid=None,
):
    """Ask a LensNode to test datasource connection settings."""

    config = dict(config or {})
    _inject_existing_datasource_credential(
        config,
        credential_uuid,
    )
    request_id = uuid.uuid4().hex
    _send_lensnode_command(
        lensnode,
        {
            "type": "datasource_test_connection",
            "request_id": request_id,
            "source_type": source_type,
            "config": config,
        },
    )
    return _wait_cache_result(
        f"lens:datasource_connection:{request_id}",
        timeout_s=120,
    )


def dispatch_datasource_sync(datasource, task_id, trigger="scheduled"):
    """Dispatch one datasource synchronization to its LensNode."""

    request_id = dispatch_datasource_sync_async(
        datasource,
        task_id=task_id,
        trigger=trigger,
    )
    return _wait_cache_result(
        f"lens:datasource_sync:{request_id}",
        timeout_s=get_datasource_sync_timeout_s(),
    )


def dispatch_datasource_sync_async(
    datasource, task_id, trigger="scheduled", *, lensnode=None
):
    """Dispatch one datasource synchronization without waiting for result."""

    datasource = DataSource.objects.select_related(
        "credential",
        "lensnode",
    ).get(
        pk=datasource.pk
    )
    if datasource.source_type == DataSource.SourceType.MANAGED_WORKSPACE:
        raise DataSourceDispatchError("DATASOURCE_SYNC_NOT_SUPPORTED")
    lensnode = lensnode or resolve_datasource_lensnode(datasource)
    validate_datasource_lensnode(lensnode)
    if datasource.connection_id and datasource.plugin_key:
        snapshot = create_datasource_sync_snapshot(
            datasource, lensnode=lensnode
        )
        request_id = uuid.uuid4().hex
        _send_lensnode_command(
            lensnode,
            {
                "type": "plugin_datasource_sync",
                "request_id": request_id,
                "task_id": task_id,
                "datasource_uuid": str(datasource.uuid),
                "snapshot_uuid": str(snapshot.uuid),
                "plugin_key": snapshot.plugin_key,
                "plugin_version": snapshot.plugin_version,
                "protocol_version": snapshot.protocol_version,
                "trigger": trigger,
            },
        )
        cache.set(
            f"lens:datasource_sync_request:{request_id}",
            task_id,
            timeout=get_datasource_sync_timeout_s(),
        )
        return request_id
    config = datasource_runtime_config(datasource)
    sync_policy = datasource.sync_policy or {}
    conversion = datasource_conversion_policy(sync_policy)
    target_path = datasource.target_path or (
        f"{lensnode.workspace_path}/datasources/{datasource.uuid}"
    )
    request_id = uuid.uuid4().hex
    _send_lensnode_command(
        lensnode,
        {
            "type": "datasource_sync",
            "request_id": request_id,
            "task_id": task_id,
            "datasource_uuid": str(datasource.uuid),
            "source_type": datasource.source_type,
            "name": datasource.name,
            "config": config,
            "conversion": conversion,
            **_lensnode_gateway_config(),
            "sync_policy": sync_policy,
            "target_path": target_path,
            "trigger": trigger,
            "max_workers": get_datasource_sync_max_workers(),
            "excluded_datasource_roots": excluded_datasource_roots(
                datasource,
                lensnode,
            ),
        },
    )
    cache.set(
        f"lens:datasource_sync_request:{request_id}",
        task_id,
        timeout=get_datasource_sync_timeout_s(),
    )
    return request_id


def dispatch_datasource_conversion_async(
    datasource,
    task_id,
    conversion,
    force=False,
):
    """Dispatch managed workspace conversion without running sync adapters."""

    datasource = DataSource.objects.select_related("lensnode").get(
        pk=datasource.pk
    )
    if datasource.source_type != DataSource.SourceType.MANAGED_WORKSPACE:
        raise DataSourceDispatchError("DATASOURCE_CONVERSION_NOT_SUPPORTED")
    validate_datasource_lensnode(datasource.lensnode)
    conversion = dict(conversion or {})
    for key, value in datasource_conversion_defaults().items():
        if value and not conversion.get(key):
            conversion[key] = value
    request_id = uuid.uuid4().hex
    _send_lensnode_command(
        datasource.lensnode,
        {
            "type": "datasource_convert",
            "request_id": request_id,
            "task_id": task_id,
            "datasource_uuid": str(datasource.uuid),
            "source_type": datasource.source_type,
            "name": datasource.name,
            "conversion": conversion,
            **_lensnode_gateway_config(),
            "target_path": datasource.target_path,
            "force": bool(force),
            "max_workers": get_datasource_sync_max_workers(),
            "excluded_datasource_roots": excluded_datasource_roots(
                datasource
            ),
        },
    )
    cache.set(
        f"lens:datasource_conversion_request:{request_id}",
        task_id,
        timeout=get_datasource_sync_timeout_s(),
    )
    return request_id


def dispatch_datasource_upload_async(
    datasource,
    task_id,
    filename,
    content,
    conversion=None,
):
    """Dispatch one managed workspace upload to its LensNode."""

    datasource = DataSource.objects.select_related("lensnode").get(
        pk=datasource.pk
    )
    if datasource.source_type != DataSource.SourceType.MANAGED_WORKSPACE:
        raise DataSourceDispatchError("DATASOURCE_UPLOAD_NOT_SUPPORTED")
    validate_datasource_lensnode(datasource.lensnode)
    upload_limits = get_datasource_upload_limits()
    if len(content) > upload_limits["max_bytes"]:
        raise DataSourceDispatchError("DATASOURCE_UPLOAD_TOO_LARGE")
    upload_conversion = {"document": True, "image": True}
    upload_conversion.update(
        datasource_conversion_policy(datasource.sync_policy)
    )
    upload_conversion.update(conversion or {})
    request_id = uuid.uuid4().hex
    _send_lensnode_command(
        datasource.lensnode,
        {
            "type": "datasource_upload",
            "request_id": request_id,
            "task_id": task_id,
            "datasource_uuid": str(datasource.uuid),
            "source_type": datasource.source_type,
            "target_path": datasource.target_path,
            "filename": filename,
            "content_base64": base64.b64encode(content).decode("ascii"),
            "upload_limits": upload_limits,
            "conversion": upload_conversion,
            **_lensnode_gateway_config(),
            "excluded_datasource_roots": excluded_datasource_roots(
                datasource
            ),
        },
    )
    cache.set(
        f"lens:datasource_upload_request:{request_id}",
        task_id,
        timeout=get_datasource_upload_timeout_s(),
    )
    return request_id


def datasource_conversion_policy(sync_policy):
    """Return datasource conversion policy with global defaults applied."""

    conversion = dict((sync_policy or {}).get("conversion") or {})
    defaults = datasource_conversion_defaults()
    for key, value in defaults.items():
        if value and not conversion.get(key):
            conversion[key] = value
    return conversion


def datasource_conversion_defaults():
    """Return global datasource conversion defaults."""

    keys = [
        DATASOURCE_CONVERSION_VISION_MODEL_SETTING,
        DATASOURCE_CONVERSION_DOCUMENT_MODEL_SETTING,
    ]
    rows = {
        row.key: row.value
        for row in GlobalSetting.objects.filter(key__in=keys)
    }
    return {
        "vision_model_ref": rows.get(
            DATASOURCE_CONVERSION_VISION_MODEL_SETTING
        )
        or "",
        "document_model_ref": rows.get(
            DATASOURCE_CONVERSION_DOCUMENT_MODEL_SETTING
        )
        or "",
    }


def excluded_datasource_roots(datasource, lensnode=None):
    """Return other datasource roots under this datasource root."""

    lensnode = lensnode or datasource.lensnode
    if lensnode is None or not datasource.target_path:
        return []
    root = normalize_workspace_target_path(
        datasource.target_path,
        lensnode.workspace_path,
    )
    rows = DataSource.objects.filter(lensnode=lensnode).exclude(
        pk=datasource.pk
    )
    roots = []
    root_path = PurePosixPath(root)
    for other in rows:
        if not other.target_path:
            continue
        other_path = PurePosixPath(
            normalize_workspace_target_path(
                other.target_path,
                datasource.lensnode.workspace_path,
            )
        )
        if other_path == root_path:
            continue
        try:
            other_path.relative_to(root_path)
        except ValueError:
            continue
        roots.append(str(other_path))
    return roots


def datasource_runtime_config(datasource):
    """Return datasource config with transient execution credentials."""

    config = dict(datasource.config or {})
    credential = getattr(datasource, "credential", None)
    if datasource.source_type == DataSource.SourceType.GIT and credential:
        if credential.endpoint_url:
            config.setdefault("endpoint_url", credential.endpoint_url)
        if credential.provider:
            config.setdefault("provider", credential.provider)
        if credential.sync_scope:
            config.setdefault("credential_sync_scope", credential.sync_scope)
        if credential.scope_config:
            config.setdefault("credential_scope", credential.scope_config)
        secret = credential.get_secret()
        if secret:
            config["access_token"] = secret
            credential.last_used_at = timezone.now()
            credential.save(update_fields=["last_used_at", "updated_at"])
    if datasource.source_type == DataSource.SourceType.FEISHU and credential:
        if credential.endpoint_url:
            config.setdefault("endpoint_url", credential.endpoint_url)
        if credential.sync_scope:
            config.setdefault("credential_sync_scope", credential.sync_scope)
        if credential.scope_config:
            for key, value in credential.scope_config.items():
                config.setdefault(key, value)
        secret = credential.get_secret()
        if secret:
            app_id, _, app_secret = secret.partition(":")
            config["app_id"] = app_id
            config["app_secret"] = app_secret
            credential.last_used_at = timezone.now()
            credential.save(update_fields=["last_used_at", "updated_at"])
    return config


def _inject_existing_datasource_credential(
    config,
    credential_uuid=None,
):
    """Add a selected encrypted credential to transient test config."""

    if not credential_uuid:
        return
    credential = DataSourceCredential.objects.filter(
        uuid=credential_uuid,
    ).first()
    if credential is None:
        return
    if (
        source_type_from_credential(credential) == DataSource.SourceType.FEISHU
    ):
        if credential.scope_config:
            for key, value in credential.scope_config.items():
                config.setdefault(key, value)
        secret = credential.get_secret()
        if not secret:
            return
        app_id, _, app_secret = secret.partition(":")
        config["app_id"] = app_id
        config["app_secret"] = app_secret
    else:
        if credential.endpoint_url:
            config.setdefault("endpoint_url", credential.endpoint_url)
        if credential.provider:
            config.setdefault("provider", credential.provider)
        if credential.sync_scope:
            config.setdefault("credential_sync_scope", credential.sync_scope)
        if credential.scope_config:
            config.setdefault("credential_scope", credential.scope_config)
        secret = credential.get_secret()
        if not secret:
            return
        config["access_token"] = secret


def source_type_from_credential(credential):
    """Infer datasource type from a credential auth type."""

    if credential.auth_type == DataSourceCredential.AuthType.FEISHU_APP:
        return DataSource.SourceType.FEISHU
    return DataSource.SourceType.GIT


test_datasource_connection.__test__ = False
