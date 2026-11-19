"""Committed platform control notices with isolated, retryable email delivery."""

import logging
from functools import partial

from django.core.mail import EmailMultiAlternatives, get_connection
from django.db import transaction
from django.utils import timezone

from accounts.audit import record_audit
from accounts.models import ApiKeyControlNotice, TenantMembership
from accounts.services.email import get_email_delivery_options

logger = logging.getLogger(__name__)


def notice_payload(notice):
    """Return delivery state without recipient identities, content or transport errors."""
    return {
        "uuid": str(notice.pk),
        "tenant_uuid": str(notice.tenant_id),
        "tenant_name": notice.tenant.name,
        "status": notice.status,
        "attempts": notice.attempts,
        "error_code": notice.error_code,
        "action": notice.action,
        "created_at": notice.created_at.isoformat(),
    }


def enqueue_notice(notice_uuid):
    """Try the broker after commit while retaining the pending row if it is unavailable."""
    from accounts.tasks import deliver_api_key_notice_task

    try:
        deliver_api_key_notice_task.delay(str(notice_uuid))
    except Exception:
        logger.warning("API key control notice dispatch unavailable: %s", notice_uuid)


def create_control_notice(*, tenant, actor, action, keys):
    """Store only the safe key identification summary inside the control transaction."""
    notice = ApiKeyControlNotice.objects.create(
        tenant=tenant,
        actor=actor,
        action=action,
        keys=[
            {"uuid": str(key.pk), "name": key.name, "key_prefix": key.key_prefix, "key_display": key.key_display}
            for key in keys
        ],
    )
    transaction.on_commit(partial(enqueue_notice, str(notice.pk)))
    return notice


def _mail_content(notice):
    """Render plain text in the tenant language without secrets or other tenants' content."""
    language = notice.tenant.default_language
    title, lead = {
        "zh-CN": (
            "API Key 平台管控通知",
            (
                "平台管理员已停用以下 API Key。"
                if notice.action == "disable"
                else "平台管理员已吊销以下 API Key，无法恢复。"
            ),
        ),
        "es": (
            "Control de claves API",
            (
                "El administrador de la plataforma ha desactivado estas claves API."
                if notice.action == "disable"
                else "El administrador de la plataforma ha revocado estas claves API de forma irreversible."
            ),
        ),
    }.get(
        language,
        (
            "API key platform control notice",
            (
                "The platform administrator disabled these API keys."
                if notice.action == "disable"
                else "The platform administrator permanently revoked these API keys."
            ),
        ),
    )
    subject = f"[SourceLens] {title} · {notice.tenant.name}".replace("\r", " ").replace("\n", " ")
    lines = [
        f"- {key['name']} ({key.get('key_display') or str(key.get('key_prefix', '')) + '••••'}; {key['uuid']})"
        for key in notice.keys
    ]
    return subject, "\n".join([lead, "", *lines])


@transaction.atomic
def deliver_notice(notice_uuid):
    """Serialize deliveries and retry only administrators without a completed send."""
    notice = (
        ApiKeyControlNotice.objects.select_for_update(of=("self",))
        .select_related("tenant")
        .filter(pk=notice_uuid)
        .first()
    )
    if notice is None or notice.status != "pending":
        return
    notice.attempts += 1
    notice.last_attempt_at = timezone.now()
    delivered = {str(value) for value in notice.delivered_user_ids}
    recipients = list(
        TenantMembership.objects.filter(
            tenant_id=notice.tenant_id,
            role="tenant_admin",
            status="active",
            user__is_active=True,
        )
        .exclude(user__email="")
        .select_related("user")
        .order_by("user_id")
    )
    subject, body = _mail_content(notice)
    sender, connection = get_email_delivery_options()
    failed = False
    for membership in recipients:
        identity = str(membership.user_id)
        if identity in delivered:
            continue
        try:
            connection = connection or get_connection(timeout=10)
            sent = EmailMultiAlternatives(
                subject=subject,
                body=body,
                from_email=sender,
                to=[membership.user.email],
                connection=connection,
            ).send()
            if sent != 1:
                raise OSError("Delivery unavailable")
            delivered.add(identity)
        except Exception:
            failed = True
    notice.delivered_user_ids = sorted(delivered)
    notice.status = "failed" if failed or not recipients else "sent"
    notice.error_code = "NO_ACTIVE_ADMINS" if not recipients else "DELIVERY_FAILED" if failed else ""
    notice.save(
        update_fields=["attempts", "last_attempt_at", "delivered_user_ids", "status", "error_code", "updated_at"]
    )
    record_audit(
        action="api_key.notice_delivery",
        tenant=notice.tenant,
        actor_type="system",
        resource_type="api_key_notice",
        resource_id=str(notice.pk),
        result="failed" if notice.status == "failed" else "success",
        metadata={"status": notice.status, "delivered_count": len(delivered), "error_code": notice.error_code},
    )
