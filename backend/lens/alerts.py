"""Evaluate admin alert rules for terminal QA Runs and dispatch notifications."""

import logging

from django.db import transaction
from django.db.models import Max, Sum

from .models import AlertEvent, AlertRule, Run, RunTraceEvent

logger = logging.getLogger(__name__)

SOURCE_APP = "lens"
SOURCE_TYPE = "alert"

EVENT_LABELS = {
    AlertRule.Event.RUN_FAILED: "Answer failed",
    AlertRule.Event.TOKEN_EXCEEDED: "Token budget exceeded",
    AlertRule.Event.ROUNDS_EXCEEDED: "Rounds exceeded",
}


def schedule_run_alert_evaluation(run_uuid):
    """Evaluate alerts after the transaction that made a Run terminal commits."""

    transaction.on_commit(
        lambda run_uuid=run_uuid: evaluate_run_alerts(run_uuid)
    )


def evaluate_run_alerts(run_uuid):
    """Create and notify alerts for one terminal top-level Run.

    Delegated child Runs are skipped: they are internal execution details,
    not user-facing Q&A turns. The operation is idempotent per
    (rule, run, event type) so at-least-once terminal frames cannot
    produce duplicate alerts.
    """

    try:
        run = (
            Run.objects.select_related(
                "session",
                "session__assistant",
                "session__user",
                "input_message",
            )
            .filter(uuid=run_uuid, parent_run_id__isnull=True)
            .first()
        )
    except Exception:  # pragma: no cover - defensive lookup guard
        logger.exception("alert evaluation: failed to load run %s", run_uuid)
        return
    if run is None or run.finished_at is None:
        return

    rules = list(
        AlertRule.objects.filter(enabled=True).prefetch_related("assistants")
    )
    if not rules:
        return

    assistant = run.session.assistant if run.session else None
    assistant_id = assistant.pk if assistant else None
    tokens = None
    rounds = None
    for rule in rules:
        if assistant_id is None or not rule.applies_to_assistant(assistant_id):
            continue
        checks = _run_checks(run, rule, tokens, rounds)
        tokens = checks["tokens"]
        rounds = checks["rounds"]
        for event_type in checks["triggered"]:
            _record_and_dispatch(rule, run, event_type, checks["detail"])


def _run_checks(run, rule, tokens, rounds):
    """Return per-run metrics and the events this rule should fire."""

    if tokens is None:
        tokens = _run_token_total(run)
    if rounds is None:
        rounds = _run_rounds(run)
    events = set(rule.events or [])
    triggered = []
    if AlertRule.Event.RUN_FAILED in events and run.status == Run.Status.FAILED:
        triggered.append(AlertEvent.EventType.RUN_FAILED)
    if (
        AlertRule.Event.TOKEN_EXCEEDED in events
        and rule.token_threshold
        and tokens > rule.token_threshold
    ):
        triggered.append(AlertEvent.EventType.TOKEN_EXCEEDED)
    if (
        AlertRule.Event.ROUNDS_EXCEEDED in events
        and rule.rounds_threshold
        and rounds > rule.rounds_threshold
    ):
        triggered.append(AlertEvent.EventType.ROUNDS_EXCEEDED)
    return {
        "tokens": tokens,
        "rounds": rounds,
        "triggered": triggered,
        "detail": _event_detail(run, rule, tokens, rounds),
    }


def _event_detail(run, rule, tokens, rounds):
    """Build the bounded, user-visible detail stored on each AlertEvent."""

    session = run.session
    assistant = session.assistant if session else None
    user = session.user if session else None
    question = (run.input_message.content if run.input_message else "") or ""
    return {
        "run_uuid": str(run.uuid),
        "session_uuid": str(session.uuid) if session else None,
        "assistant_name": assistant.name if assistant else None,
        "assistant_slug": assistant.slug if assistant else None,
        "username": user.username if user else None,
        "question": question[:160],
        "run_status": run.status,
        "outcome": run.outcome,
        "error": run.error[:500],
        "termination": run.termination_detail or {},
        "total_tokens": tokens,
        "rounds": rounds,
        "token_threshold": rule.token_threshold,
        "rounds_threshold": rule.rounds_threshold,
    }


def _run_token_total(run):
    """Sum metered tokens for a Run and its delegated children."""

    from agentcore_metering.adapters.django.models import LLMUsage

    run_uuids = [str(run.uuid)]
    frontier = [run.pk]
    while frontier:
        children = list(
            Run.objects.filter(parent_run_id__in=frontier).values_list(
                "pk", "uuid"
            )
        )
        frontier = [pk for pk, _uuid in children]
        run_uuids.extend(str(run_uuid) for _pk, run_uuid in children)
    total = LLMUsage.objects.filter(
        metadata__run_uuid__in=run_uuids,
    ).aggregate(total=Sum("total_tokens"))["total"]
    return int(total or 0)


def _run_rounds(run):
    """Return the highest agent turn reached by a Run."""

    highest = RunTraceEvent.objects.filter(
        run=run,
        turn__isnull=False,
    ).aggregate(highest=Max("turn"))["highest"]
    return int(highest or 0)


def _record_and_dispatch(rule, run, event_type, detail):
    """Persist one alert event and enqueue its notification exactly once."""

    event, created = AlertEvent.objects.get_or_create(
        rule=rule,
        run=run,
        event_type=event_type,
        defaults={"detail": detail},
    )
    if not created:
        return event
    status, error = _dispatch(rule, run, event_type, detail)
    event.status = status
    event.error_message = error
    event.save(update_fields=["status", "error_message"])
    return event


def _dispatch(rule, run, event_type, detail):
    """Enqueue the notification and return (status, error message)."""

    if not rule.channel_uuid:
        return AlertEvent.Status.SKIPPED, "No notification channel configured."

    from agentcore_notifier.adapters.django.models import NotificationChannel

    channel = NotificationChannel.objects.filter(
        uuid=rule.channel_uuid,
        is_active=True,
    ).first()
    if channel is None:
        return AlertEvent.Status.SKIPPED, "Notification channel is missing or inactive."
    if channel.channel_type not in {
        NotificationChannel.TYPE_WEBHOOK,
        NotificationChannel.TYPE_EMAIL,
    }:
        return AlertEvent.Status.SKIPPED, "Notification channel type is not supported for alerts."

    title, lines = _message(rule, event_type, detail)
    source_id = f"{run.uuid}:{event_type}"
    try:
        if channel.channel_type == NotificationChannel.TYPE_EMAIL:
            recipients = [str(item).strip() for item in (rule.email_recipients or [])]
            recipients = [item for item in recipients if item]
            if not recipients:
                return AlertEvent.Status.SKIPPED, "No email recipients configured."
            from agentcore_notifier.adapters.django.tasks.send import (
                send_notification,
            )

            send_notification.delay(
                notification_type="email",
                source_app=SOURCE_APP,
                source_type=SOURCE_TYPE,
                source_id=source_id,
                channel_uuid=str(channel.uuid),
                params={
                    "subject": title,
                    "body": "\n".join(lines),
                    "to": recipients,
                },
            )
        else:
            provider_type = str(
                (channel.config or {}).get("provider_type") or "feishu"
            )
            from agentcore_notifier.adapters.django.tasks.send import (
                send_notification,
            )

            send_notification.delay(
                notification_type="webhook",
                source_app=SOURCE_APP,
                source_type=SOURCE_TYPE,
                source_id=source_id,
                channel_uuid=str(channel.uuid),
                params={
                    "provider_type": provider_type,
                    "payload": _webhook_payload(
                        provider_type,
                        title,
                        lines,
                    ),
                },
            )
    except Exception as exc:  # pragma: no cover - dispatch guard
        logger.exception("alert dispatch failed for run %s", run.uuid)
        return AlertEvent.Status.FAILED, str(exc)[:500]
    return AlertEvent.Status.DISPATCHED, ""


def _message(rule, event_type, detail):
    """Return the (title, lines) notification body for one alert event."""

    label = EVENT_LABELS.get(event_type, event_type)
    title = f"[SourceLens] {label}"
    lines = [
        f"Alert: {label}",
        f"Assistant: {detail.get('assistant_name') or '-'}",
        f"User: {detail.get('username') or '-'}",
    ]
    if event_type == AlertEvent.EventType.TOKEN_EXCEEDED:
        lines.append(
            f"Tokens: {detail.get('total_tokens')} "
            f"(threshold {detail.get('token_threshold')})"
        )
    elif event_type == AlertEvent.EventType.ROUNDS_EXCEEDED:
        lines.append(
            f"Rounds: {detail.get('rounds')} "
            f"(threshold {detail.get('rounds_threshold')})"
        )
    elif event_type == AlertEvent.EventType.RUN_FAILED:
        lines.append(f"Error: {detail.get('error') or '-'}")
    question = (detail.get("question") or "").strip()
    if question:
        lines.append(f"Question: {question}")
    lines.append(f"Run: {detail.get('run_uuid')}")
    return title, lines


def _webhook_payload(provider_type, title, lines):
    """Return a provider-native text payload for webhook channels."""

    text = "\n".join(lines)
    if provider_type in {"wecom", "wechat"}:
        return {"msgtype": "text", "text": {"content": text}}
    return {"msg_type": "text", "content": {"text": text}}
