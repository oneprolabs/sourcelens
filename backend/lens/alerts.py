"""Unified business alert matching, durable outbox and notification delivery."""

import logging
import re
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import CharField, DateTimeField, F, Max, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Cast, Coalesce, Replace
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import AlertDelivery, AlertEvent, AlertOutbox, AlertRule, Assistant, DataSource, Run, RunTraceEvent

logger = logging.getLogger(__name__)
TASK_SUBJECTS = {"lens_datasource": "sync", "lens_datasource_conversion": "processing"}
RUNNING_EVENTS = {"token_exceeded", "rounds_exceeded", "run_slow"}
SUBJECT_EVENTS = {
    "message": {"message_published"},
    "run": {"run_failed", "run_partial", "run_timeout", "run_token_limit", "run_round_limit", *RUNNING_EVENTS},
    "sync": {"sync_failed", "sync_partial", "sync_timeout"},
    "processing": {"processing_failed", "processing_partial", "processing_timeout"},
}
EVENT_LABELS = dict(AlertRule.Event.choices)
TERMINAL_TASKS = {"SUCCESS", "FAILURE", "REVOKED", "success", "failed", "cancelled"}


def _error_code(value):
    """Keep machine codes; arbitrary provider errors belong in protected task logs."""

    text = str(value or "")
    return text[:120] if re.fullmatch(r"[A-Z][A-Z0-9_:. -]{0,119}", text) else ""


def _wake_dispatcher():
    """Best-effort wakeup; database rows remain available to the periodic sweep."""

    from .tasks import dispatch_alerts_task

    try:
        dispatch_alerts_task.delay()
    except Exception:
        logger.warning("Alert dispatcher unavailable; periodic recovery will retry", exc_info=True)


def _enqueue_occurrence(subject, source_uuid, payload, phase="terminal"):
    """Write the occurrence in the caller's transaction before publishing work."""

    source_key = f"{subject}:{source_uuid}:{phase}"
    outbox, created = AlertOutbox.objects.get_or_create(
        source_key=source_key,
        defaults={"subject": subject, "source_uuid": str(source_uuid), "payload": payload},
    )
    if not created and outbox.payload != payload:
        outbox.payload = payload
        outbox.processed_at = None
        outbox.save(update_fields=["payload", "processed_at", "updated_at"])
    if phase == "terminal" and (created or outbox.processed_at is None):
        transaction.on_commit(_wake_dispatcher)
    return outbox


def _run_token_total(run):
    """Sum persisted metering for this Run and all descendants."""

    from agentcore_metering.adapters.django.models import LLMUsage

    run_uuids, frontier = [str(run.uuid)], [run.pk]
    while frontier:
        children = list(Run.objects.filter(parent_run_id__in=frontier).values_list("pk", "uuid"))
        frontier = [pk for pk, _ in children]
        run_uuids.extend(str(value) for _, value in children)
    total = LLMUsage.objects.filter(metadata__run_uuid__in=run_uuids).aggregate(total=Sum("total_tokens"))["total"]
    return int(total or 0)


def _run_payload(run):
    """Snapshot safe routing context; measure usage only when matching a rule."""

    assistant = run.session.assistant
    termination = run.termination_detail or {}
    return {
        "occurred_at": (run.finished_at or timezone.now()).isoformat(),
        "run_uuid": str(run.uuid),
        "assistant_uuid": str(assistant.uuid),
        "assistant_name": assistant.name,
        "lensnode_uuid": (
            str(run.lensnode.uuid)
            if run.lensnode_id
            else (str(assistant.lensnode.uuid) if assistant.lensnode_id else "")
        ),
        "user_id": run.session.user_id,
        "username": run.session.user.username,
        "status": run.status,
        "outcome": run.outcome,
        "error": _error_code(run.error),
        "termination": {key: termination[key] for key in ("reason", "trigger", "code") if key in termination},
        "duration_seconds": max(
            0, int(((run.finished_at or timezone.now()) - (run.started_at or run.created_at)).total_seconds())
        ),
    }


def capture_run_alert(run):
    """Persist terminal top-level Runs without interpreting success as failure."""

    if run.parent_run_id or run.status not in {Run.Status.DONE, Run.Status.FAILED} or not run.finished_at:
        return None
    payload = _run_payload(run)
    if (
        AlertRule.objects.filter(enabled=True, subject="run")
        .filter(Q(token_threshold__isnull=False) | Q(rounds_threshold__isnull=False))
        .exists()
    ):
        payload["total_tokens"] = _run_token_total(run)
        payload["rounds"] = RunTraceEvent.objects.filter(run=run).aggregate(value=Max("turn"))["value"] or 0
    return _enqueue_occurrence("run", run.uuid, payload)


def schedule_run_alert_evaluation(run_uuid):
    """Persist alert work synchronously and wake Celery only after commit."""

    run = (
        Run.objects.select_related("session__assistant__lensnode", "session__user", "lensnode")
        .filter(uuid=run_uuid)
        .first()
    )
    if run:
        return capture_run_alert(run)
    return None


def capture_task_alert(task):
    """Capture terminal sync/processing results, including successful partial work."""

    subject = TASK_SUBJECTS.get(task.module)
    if not subject or task.status not in TERMINAL_TASKS:
        return None
    metadata = task.metadata if isinstance(task.metadata, dict) else {}
    result = task.result or {}
    if not isinstance(result, dict):
        result = {}
    summary = metadata.get("conversion_summary") or result.get("conversion_summary") or {}
    summary = summary if isinstance(summary, dict) else {}
    sync = metadata.get("sync_summary") or result
    sync = sync if isinstance(sync, dict) else {}
    error = _error_code(task.error)
    reason = _error_code(metadata.get("completion_reason"))
    try:
        datasource_uuid = uuid.UUID(str(metadata.get("datasource_uuid")))
    except (ValueError, TypeError, AttributeError):
        datasource_uuid = None
    datasource = DataSource.objects.filter(uuid=datasource_uuid).select_related("lensnode").first()
    try:
        failed_files = max(0, int(summary.get("failed") or 0))
    except (ValueError, TypeError, OverflowError):
        failed_files = 0
    payload = {
        "occurred_at": (task.finished_at or timezone.now()).isoformat(),
        "status": task.status,
        "error": error,
        "completion_reason": reason,
        "timed_out": bool(metadata.get("timeout_cancel_requested_at") or metadata.get("timeout_cancelled_at")),
        "datasource_uuid": str(datasource.uuid) if datasource else "",
        "datasource_name": datasource.name if datasource else "",
        "lensnode_uuid": str(datasource.lensnode.uuid) if datasource and datasource.lensnode_id else "",
        "failed_files": failed_files,
        "partial_success": bool(sync.get("partial_success") or sync.get("failed") or summary.get("failed")),
        "trigger": str(metadata.get("trigger") or ""),
    }
    return _enqueue_occurrence(subject, task.task_id, payload)


def _matched_events(rule, payload, run=None):
    """Return conditions in priority order, coalescing generic failures with causes."""

    if rule.subject == "message":
        return ["message_published"] if rule.topic == payload.get("topic") else []
    events, subject = [], rule.subject
    status = str(payload.get("status") or "").lower()
    failed = status in {"failed", "failure"}
    cancelled = status in {"cancelled", "revoked"}
    reasons = set((payload.get("termination") or {}).values())
    code = f"{payload.get('error', '')} {payload.get('completion_reason', '')}".upper()
    timeout = "TIMEOUT" in code or "soft_deadline" in reasons or bool(payload.get("timed_out"))
    if cancelled and not timeout:
        return []
    if timeout:
        events.append(f"{subject}_timeout")
    if subject == "run":
        if reasons & {"token_budget", "token_capped", "token_budget_wrapup"}:
            events.append("run_token_limit")
        if "turn_limit" in reasons:
            events.append("run_round_limit")
        if payload.get("outcome") == "partial":
            events.append("run_partial")
    elif payload.get("partial_success") and not failed:
        events.append(f"{subject}_partial")
    if failed:
        events.append(f"{subject}_failed")
    if run and set(rule.events) & {"token_exceeded", "rounds_exceeded"}:
        if "total_tokens" not in payload:
            payload["total_tokens"] = _run_token_total(run)
        if "rounds" not in payload:
            payload["rounds"] = RunTraceEvent.objects.filter(run=run).aggregate(value=Max("turn"))["value"] or 0
        if rule.token_threshold and payload["total_tokens"] > rule.token_threshold:
            events.append("token_exceeded")
        if rule.rounds_threshold and payload["rounds"] > rule.rounds_threshold:
            events.append("rounds_exceeded")
    if rule.duration_threshold and payload.get("duration_seconds", 0) > rule.duration_threshold:
        events.append("run_slow")
    return [event for event in events if event in rule.events]


def _rule_matches_scope(rule, payload):
    """Match UUID scopes captured when the operation completed."""

    for relation, field in (
        ("assistants", "assistant_uuid"),
        ("datasources", "datasource_uuid"),
        ("lensnodes", "lensnode_uuid"),
    ):
        targets = {str(item.uuid) for item in getattr(rule, relation).all()}
        if targets and payload.get(field) not in targets:
            return False
    return True


def personal_subscription_allowed(subscription, payload=None):
    """Recheck resource visibility and Run ownership at evaluation and delivery."""

    user, rule = subscription.user, subscription.rule
    if (
        not user
        or not user.is_active
        or not user.email
        or rule.subject not in {"run", "message"}
        or not rule.personal_subscription_enabled
    ):
        return False
    if rule.subject == "message":
        return True
    visible = Assistant.objects.visible_to(user).filter(status=Assistant.Status.ACTIVE)
    if payload is not None:
        return payload.get("user_id") == user.pk and visible.filter(uuid=payload.get("assistant_uuid")).exists()
    targets = rule.assistants.all()
    return not targets.exists() or targets.filter(pk__in=visible.values("pk")).exists()


def subscription_destination(subscription):
    """Resolve email addresses from current users and group membership."""

    from agentcore_notifier.adapters.django.models import NotificationChannel

    channel_uuid = subscription.rule.personal_channel_uuid if subscription.personal else subscription.channel_uuid
    channel = NotificationChannel.objects.filter(uuid=channel_uuid).first() if channel_uuid else None
    recipients = {subscription.user.email} if subscription.personal else set(subscription.email_recipients or [])
    if not subscription.personal:
        recipients.update(subscription.users.filter(is_active=True).exclude(email="").values_list("email", flat=True))
        recipients.update(
            get_user_model()
            .objects.filter(is_active=True, groups__in=subscription.groups.all())
            .exclude(email="")
            .values_list("email", flat=True)
        )
    from .alert_content import notification_language

    return {
        "language": notification_language(subscription),
        "name": subscription.name or (channel.name if channel else ""),
        "channel_uuid": str(channel_uuid or ""),
        "channel_type": channel.channel_type if channel else "",
        "recipients": sorted(recipients) if channel and channel.channel_type == "email" else [],
    }


@transaction.atomic
def _evaluate_occurrence(outbox, running=False):
    """Lock one durable occurrence and atomically create every destination job."""

    outbox = AlertOutbox.objects.select_for_update().get(pk=outbox.pk)
    if outbox.processed_at and not running:
        return
    payload = dict(outbox.payload)
    run = Run.objects.filter(uuid=outbox.source_uuid).first() if outbox.subject == "run" else None
    rules = AlertRule.objects.filter(
        enabled=True,
        subject=outbox.subject,
        created_at__lte=(parse_datetime(payload.get("occurred_at", "")) or outbox.created_at),
    ).prefetch_related(
        "assistants", "datasources", "lensnodes", "subscriptions__users", "subscriptions__groups", "subscriptions__user"
    )
    for rule in rules:
        if (rule.evaluation_mode == "running") != running or not _rule_matches_scope(rule, payload):
            continue
        matched = _matched_events(rule, payload, run)
        if not matched:
            continue
        if not running and run and AlertEvent.objects.filter(rule=rule, run=run, source_key__isnull=True).exists():
            continue
        source_key = outbox.source_key if not running else f"{outbox.source_key}:{matched[0]}"
        event, created = AlertEvent.objects.get_or_create(
            rule=rule,
            source_key=source_key,
            defaults={
                "subject": rule.subject,
                "source_uuid": outbox.source_uuid,
                "run": run,
                "event_type": matched[0],
                "status": "pending",
                "rule_snapshot": {"name": rule.name, "events": rule.events, "diagnosis_mode": rule.diagnosis_mode},
                "detail": {
                    **payload,
                    "matched_events": matched,
                    "token_threshold": rule.token_threshold,
                    "rounds_threshold": rule.rounds_threshold,
                    "duration_threshold": rule.duration_threshold,
                },
            },
        )
        if not created:
            continue
        if rule.channel_uuid and not rule.subscriptions.filter(personal=False).exists():
            rule.subscriptions.create(
                name="Legacy destination", channel_uuid=rule.channel_uuid, email_recipients=rule.email_recipients
            )
        for subscription in rule.subscriptions.filter(enabled=True).select_for_update():
            if subscription.personal and not personal_subscription_allowed(subscription, payload):
                continue
            destination = subscription_destination(subscription)
            delivery_status = "pending"
            if rule.cooldown_seconds and payload.get("trigger") == "scheduled" and payload.get("datasource_uuid"):
                recent = AlertDelivery.objects.filter(
                    subscription=subscription,
                    event__detail__datasource_uuid=payload.get("datasource_uuid"),
                    status__in=["pending", "sending", "sent"],
                    created_at__gte=timezone.now() - timedelta(seconds=rule.cooldown_seconds),
                )
                if recent.exists():
                    delivery_status = "suppressed"
            delivery_fields = {
                "event": event,
                "subscription": subscription,
                "destination": destination,
                "status": delivery_status,
            }
            if delivery_status == "pending":
                from .alert_diagnostics import attach_diagnosis, delivery_identity

                dedupe_key = delivery_identity(source_key, destination)
                original, unique = AlertDelivery.objects.get_or_create(dedupe_key=dedupe_key, defaults=delivery_fields)
                if unique:
                    attach_diagnosis(original, rule, source_key)
                else:
                    AlertDelivery.objects.create(
                        **{
                            **delivery_fields,
                            "status": "suppressed",
                            "destination": {**destination, "deduplicated_by": str(original.uuid)},
                        },
                        error_message="Duplicate destination for this occurrence",
                    )
            else:
                AlertDelivery.objects.create(**delivery_fields)
        _refresh_event_status(event)
    outbox.processed_at = timezone.now()
    outbox.save(update_fields=["processed_at", "updated_at"])


def evaluate_run_alerts(run_uuid):
    """Evaluate a Run synchronously without performing external network calls."""

    outbox = schedule_run_alert_evaluation(run_uuid)
    if outbox:
        _evaluate_occurrence(outbox)


def process_alert_outbox(limit=100):
    """Consume a bounded batch; one malformed occurrence cannot starve the queue."""

    for outbox in AlertOutbox.objects.filter(processed_at__isnull=True, source_key__endswith=":terminal").order_by(
        "created_at"
    )[:limit]:
        try:
            _evaluate_occurrence(outbox)
        except Exception:
            logger.exception("Alert occurrence evaluation failed: %s", outbox.uuid)


@transaction.atomic
def _refresh_event_status(event):
    """Summarize destination results without equating queueing with delivery."""

    event = AlertEvent.objects.select_for_update().get(pk=event.pk)
    statuses = set(event.deliveries.values_list("status", flat=True))
    if statuses & {"pending", "sending", "diagnosing"}:
        status = "pending"
    elif statuses == {"sent"}:
        status = "sent"
    elif "sent" in statuses:
        status = "partial"
    elif "failed" in statuses:
        status = "failed"
    else:
        status = "skipped"
    AlertEvent.objects.filter(pk=event.pk).update(status=status)


def _send_notification(**kwargs):
    """Use notifier's synchronous result inside our durable Celery delivery job."""

    from agentcore_notifier.adapters.django.tasks.send import send_notification

    return send_notification(**kwargs)


def _delivery_result(delivery):
    """Validate the live subscription and send a bounded business summary."""

    from agentcore_notifier.adapters.django.models import NotificationChannel

    subscription = delivery.subscription
    if not subscription or not subscription.enabled or not subscription.rule.enabled:
        return {"skipped": True, "reason": "Subscription disabled or removed"}
    if delivery.event.subject == "message" and (
        subscription.rule.subject != "message" or subscription.rule.topic != delivery.event.detail.get("topic")
    ):
        return {"skipped": True, "reason": "Subscribed topic changed"}
    if subscription.personal and not personal_subscription_allowed(subscription, delivery.event.detail):
        return {"skipped": True, "reason": "Subscription access revoked"}
    destination = subscription_destination(subscription)
    if destination["channel_uuid"] != delivery.destination.get("channel_uuid"):
        return {"skipped": True, "reason": "Destination changed"}
    channel = NotificationChannel.objects.filter(uuid=destination["channel_uuid"] or None, is_active=True).first()
    if not channel or channel.channel_type not in {"email", "webhook"}:
        return {"skipped": True, "reason": "Notification channel unavailable"}
    from .alert_content import notification_language, render_notification

    language = notification_language(subscription)
    if delivery.phase == "diagnosis" and (
        subscription.rule.diagnosis_mode != "notify_then_diagnose"
        or not delivery.diagnosis
        or delivery.diagnosis.language != language
    ):
        return {"skipped": True, "reason": "Diagnosis policy or language changed"}
    delivery.destination = {**delivery.destination, "language": language}
    AlertDelivery.objects.filter(pk=delivery.pk).update(destination=delivery.destination)
    title, body = render_notification(delivery, language)
    if channel.channel_type == "email":
        recipients = sorted(set(destination["recipients"]) & set(delivery.destination.get("recipients") or []))
        if not recipients:
            return {"skipped": True, "reason": "No current email recipients"}
        params = {"subject": title, "body": body, "to": recipients}
    else:
        provider = str((channel.config or {}).get("provider_type") or "feishu")
        params = {"provider_type": provider, "payload": _webhook_payload(provider, body)}
    return _send_notification(
        notification_type=channel.channel_type,
        channel_uuid=str(channel.uuid),
        source_app="lens",
        source_type="alert",
        source_id=str(delivery.uuid),
        params=params,
    )


def _webhook_payload(provider, text):
    """Format text for supported notifier providers."""

    if provider in {"wecom", "wechat", "dingtalk"}:
        return {"msgtype": "text", "text": {"content": text}}
    return {"msg_type": "text", "content": {"text": text}}


def send_alert_delivery(delivery_uuid):
    """Claim a destination without holding a database lock during network I/O."""

    now, token = timezone.now(), uuid.uuid4()
    eligible = Q(status="pending", next_attempt_at__lte=now) | Q(
        status="sending", claimed_at__lt=now - timedelta(minutes=5)
    )
    claimed = (
        AlertDelivery.objects.filter(uuid=delivery_uuid, attempts__lt=5)
        .filter(eligible)
        .update(status="sending", claimed_at=now, claim_token=token, attempts=F("attempts") + 1)
    )
    if not claimed:
        return
    delivery = AlertDelivery.objects.select_related("event", "subscription__rule", "subscription__user").get(
        uuid=delivery_uuid
    )
    try:
        result = _delivery_result(delivery) or {}
    except Exception:
        logger.exception("Alert delivery failed: %s", delivery.uuid)
        result = {"success": False, "error": "Notification transport error"}
    attempts = delivery.attempts
    if result.get("skipped"):
        status, error = "skipped", str(result.get("reason") or "Skipped")
    elif result.get("success"):
        status, error = "sent", ""
    else:
        status, error = ("failed" if attempts >= 5 else "pending"), "Notification provider rejected the delivery"
    AlertDelivery.objects.filter(pk=delivery.pk, claim_token=token).update(
        status=status,
        attempts=attempts,
        error_message=error[:500],
        claim_token=None,
        sent_at=timezone.now() if status == "sent" else None,
        next_attempt_at=timezone.now() + timedelta(seconds=min(60 * 2 ** (attempts - 1), 3600)),
    )
    _refresh_event_status(delivery.event)


def reconcile_alert_sources(limit=100):
    """Recover bulk terminal updates and sample configured running thresholds."""

    from agentcore_task.adapters.django.models import TaskExecution

    since = timezone.now() - timedelta(days=1)
    for subject in ("run", "sync", "processing"):
        known = AlertOutbox.objects.filter(subject=subject, source_key__endswith=":terminal").values("source_uuid")
        if subject == "run":
            known = (
                AlertOutbox.objects.filter(subject=subject, source_key__endswith=":terminal")
                .annotate(normalized_uuid=Replace("source_uuid", Value("-"), Value("")))
                .values("normalized_uuid")
            )
        if subject == "run":
            sources = (
                Run.objects.filter(status__in=["done", "failed"], finished_at__gte=since, parent_run__isnull=True)
                .annotate(alert_uuid=Replace(Cast("uuid", CharField()), Value("-"), Value("")))
                .exclude(alert_uuid__in=known)
                .order_by("finished_at")[:limit]
            )
            for run in sources.select_related("session__assistant__lensnode", "session__user", "lensnode"):
                capture_run_alert(run)
        else:
            module = next(key for key, value in TASK_SUBJECTS.items() if value == subject)
            sources = (
                TaskExecution.objects.filter(module=module, status__in=TERMINAL_TASKS, finished_at__gte=since)
                .exclude(task_id__in=known)
                .order_by("finished_at")[:limit]
            )
            for task in sources:
                capture_task_alert(task)
    if (
        AlertRule.objects.filter(enabled=True, subject="run")
        .filter(Q(token_threshold__isnull=False) | Q(rounds_threshold__isnull=False))
        .exists()
    ):
        checked = (
            AlertOutbox.objects.filter(subject="run", source_key__endswith=":terminal")
            .annotate(normalized_uuid=Replace("source_uuid", Value("-"), Value("")))
            .filter(normalized_uuid=OuterRef("alert_uuid"))
            .values("updated_at")[:1]
        )
        completed = (
            Run.objects.filter(status__in=["done", "failed"], finished_at__gte=since, parent_run__isnull=True)
            .annotate(
                alert_uuid=Replace(Cast("uuid", CharField()), Value("-"), Value("")),
                checked_at=Coalesce(Subquery(checked, output_field=DateTimeField()), "created_at"),
            )
            .order_by("checked_at", "pk")[:limit]
        )
        for run in completed.select_related("session__assistant__lensnode", "session__user", "lensnode"):
            outbox = capture_run_alert(run)
            AlertOutbox.objects.filter(pk=outbox.pk).update(updated_at=timezone.now())
    if AlertRule.objects.filter(enabled=True, subject="run", evaluation_mode="running").exists():
        checked = (
            AlertOutbox.objects.filter(subject="run", source_key__endswith=":running")
            .annotate(normalized_uuid=Replace("source_uuid", Value("-"), Value("")))
            .filter(normalized_uuid=OuterRef("alert_uuid"))
            .values("updated_at")[:1]
        )
        runs = (
            Run.objects.filter(status__in=["running", "streaming"], parent_run__isnull=True)
            .annotate(
                alert_uuid=Replace(Cast("uuid", CharField()), Value("-"), Value("")),
                checked_at=Coalesce(Subquery(checked, output_field=DateTimeField()), "created_at"),
            )
            .select_related("session__assistant__lensnode", "session__user", "lensnode")
            .order_by("checked_at", "pk")[:limit]
        )
        for run in runs:
            outbox = _enqueue_occurrence("run", run.uuid, _run_payload(run), phase="running")
            outbox.payload = _run_payload(run)
            outbox.save(update_fields=["payload", "updated_at"])
            _evaluate_occurrence(outbox, running=True)
