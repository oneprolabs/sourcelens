"""Server-issued model grants for datasource jobs that have no assistant Run."""

import time

from django.core import signing

from lens.model_scope import authorized_model_ref
from lens.vision_capabilities import validate_vision_model_ref

MODEL_FIELDS = ("vision_model_ref", "document_model_ref")
GRANT_SALT = "lens.datasource.model-grant.v1"
TASK_MODULES = ("lens_datasource", "lens_datasource_conversion", "lens_datasource_upload")


def validate_conversion_models(conversion, tenant):
    """Allow explicit datasource overrides to spend only tenant-owned models."""

    for field in MODEL_FIELDS:
        ref = (conversion or {}).get(field)
        if not ref:
            continue
        authorized_model_ref(ref, tenant)
        if field == "vision_model_ref":
            reason = validate_vision_model_ref(ref)
            if reason:
                raise ValueError(reason)


def conversion_model_grant(datasource, lensnode, task_id, conversion, overrides):
    """Freeze provenance and credential scope before dispatch, never in client data."""

    from .services import get_datasource_conversion_timeout_s, get_datasource_sync_timeout_s

    validate_conversion_models(overrides, datasource.tenant)
    models = {}
    for field in MODEL_FIELDS:
        ref = conversion.get(field)
        if not ref:
            continue
        platform = not bool(overrides.get(field))
        ref = authorized_model_ref(ref, None if platform else datasource.tenant)
        if field == "vision_model_ref":
            reason = validate_vision_model_ref(ref)
            if reason:
                raise ValueError(reason)
            models[ref] = {"platform": platform}
    if not models:
        return ""
    if datasource.tenant is None or datasource.tenant.status != "active":
        raise ValueError("Model tenant is inactive.")
    return signing.dumps(
        {
            "datasource": str(datasource.uuid),
            "tenant": str(datasource.tenant_id) if datasource.tenant_id else None,
            "node": str(lensnode.uuid),
            "task": str(task_id),
            "models": models,
            "expires": time.time() + get_datasource_conversion_timeout_s() + get_datasource_sync_timeout_s(),
        },
        salt=GRANT_SALT,
        compress=True,
    )


def conversion_tracker_state(token, lensnode, model_ref):
    """Recheck a signed job grant and return trusted billing and credential state."""

    from agentcore_task.adapters.django.models import TaskExecution
    from agentcore_task.constants import TaskStatus

    from lens.models import DataSource

    grant = signing.loads(token, salt=GRANT_SALT)
    if grant["expires"] <= time.time() or grant["node"] != str(lensnode.uuid):
        raise ValueError("Conversion authorization expired or belongs to another node.")
    model = grant["models"].get(str(model_ref))
    if model is None:
        raise ValueError("Model is not bound to this conversion.")
    source = DataSource.objects.select_related("tenant").get(uuid=grant["datasource"])
    tenant_id = str(source.tenant_id) if source.tenant_id else None
    if (
        tenant_id != grant["tenant"]
        or source.status == DataSource.Status.DISABLED
        or source.lensnode_id not in (None, lensnode.pk)
    ):
        raise ValueError("Conversion source is unavailable.")
    if source.tenant and source.tenant.status != "active":
        raise ValueError("Model tenant is inactive.")
    task = TaskExecution.objects.filter(
        task_id=grant["task"],
        module__in=TASK_MODULES,
        metadata__datasource_uuid=str(source.uuid),
        metadata__lensnode_uuid=str(lensnode.uuid),
        status__in=[TaskStatus.PENDING, *TaskStatus.get_running_statuses()],
    ).first()
    if task is None:
        raise ValueError("Conversion task is no longer active.")
    authorized_model_ref(model_ref, None if model["platform"] else source.tenant)
    reason = validate_vision_model_ref(model_ref)
    if reason:
        raise ValueError(reason)
    return {
        "source_type": "datasource_conversion",
        "lensnode_uuid": str(lensnode.uuid),
        "user_id": task.created_by_id,
        "_model_scope": (source.tenant,),
        "_platform_model": model["platform"],
        "metadata": {"datasource_uuid": str(source.uuid), "task_id": task.task_id},
    }
