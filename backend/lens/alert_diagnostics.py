"""Durable, bounded diagnosis jobs separate from the notification transport queue."""

import hashlib
import json
import logging
from datetime import timedelta
from types import SimpleNamespace

from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from .models import AlertDelivery, AlertDiagnosis, AlertEvent, GlobalSetting, Run

logger = logging.getLogger(__name__)
FINAL_STATUSES = {"completed", "failed", "timed_out"}


def delivery_identity(source_key, destination, phase="initial"):
    """Merge identical recipients only when locale, phase and diagnosis policy agree."""

    identity = [
        source_key,
        destination["channel_uuid"],
        sorted({email.casefold() for email in destination["recipients"]}),
        destination.get("language", "en-US"),
        phase,
        destination.get("diagnosis_policy", ["direct"]),
    ]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()


def attach_diagnosis(delivery, rule, source_key):
    """Share one analysis across rules without publishing inside their transaction."""

    if rule.diagnosis_mode == "direct" or delivery.status != "pending":
        return
    language = delivery.destination["language"]
    identity = [source_key, language, rule.diagnosis_max_tokens, rule.diagnosis_timeout_seconds]
    key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
    diagnosis, _ = AlertDiagnosis.objects.get_or_create(
        key=key,
        defaults={
            "source_key": source_key,
            "subject": delivery.event.subject,
            "source_uuid": delivery.event.source_uuid,
            "language": language,
            "max_tokens": rule.diagnosis_max_tokens,
            "timeout_seconds": rule.diagnosis_timeout_seconds,
            "deadline_at": timezone.now() + timedelta(seconds=rule.diagnosis_timeout_seconds),
        },
    )
    delivery.diagnosis = diagnosis
    if rule.diagnosis_mode == "diagnose_first":
        delivery.status = "diagnosing"
    delivery.save(update_fields=["diagnosis", "status", "updated_at"])


def _diagnostic_evidence(diagnosis):
    """Reuse safe Run evidence helpers; omit questions, answers and raw task logs."""

    event = diagnosis.deliveries.select_related("event").first().event
    safe_keys = {
        "status",
        "outcome",
        "error",
        "termination",
        "duration_seconds",
        "total_tokens",
        "rounds",
        "failed_files",
        "completion_reason",
        "timed_out",
        "partial_success",
        "matched_events",
    }
    evidence = {
        "E-ALERT": {"kind": diagnosis.subject, "data": {k: v for k, v in event.detail.items() if k in safe_keys}}
    }
    model_ref = GlobalSetting.objects.filter(key="diagnostics.model_ref").values_list("value", flat=True).first()
    if diagnosis.subject == "run":
        from .run_diagnostics import _diagnostic_model_snapshot, _execution_evidence, _step_evidence

        run = Run.objects.select_related("execution").filter(uuid=diagnosis.source_uuid).first()
        if not run:
            raise ValueError("SOURCE_UNAVAILABLE")
        model_ref, _ = _diagnostic_model_snapshot(run)
        evidence["E-EXECUTION"] = {"kind": "execution", "data": _execution_evidence(getattr(run, "execution", None))}
        for index, step in enumerate(run.steps.order_by("sequence")[:10]):
            data = _step_evidence(step)
            data["events"] = data["events"][:5]
            evidence[f"E-STEP-{index}"] = {"kind": "run_step", "data": data}
    else:
        from agentcore_task.adapters.django.models import TaskExecution

        task = TaskExecution.objects.filter(task_id=diagnosis.source_uuid).first()
        if not task:
            raise ValueError("SOURCE_UNAVAILABLE")
        evidence["E-TASK"] = {"kind": "task", "data": {"module": task.module, "status": task.status}}
    if not model_ref:
        raise ValueError("MODEL_UNAVAILABLE")
    payload = {"evidence": evidence, "missing_evidence": ["raw_logs", "question_and_answer"]}
    if len(json.dumps(payload, ensure_ascii=False)) > 24000:
        payload["evidence"] = {k: v for k, v in evidence.items() if not k.startswith("E-STEP-")}
        payload["missing_evidence"].append("steps_over_input_budget")
    if len(json.dumps(payload, ensure_ascii=False)) > 24000:
        payload["evidence"].pop("E-EXECUTION", None)
        payload["missing_evidence"].append("execution_over_input_budget")
    if len(json.dumps(payload, ensure_ascii=False)) > 24000:
        raise ValueError("INPUT_BUDGET_EXCEEDED")
    return payload, str(model_ref)


def execute_alert_diagnosis(diagnosis_uuid):
    """Claim once and validate evidence references before accepting model output."""

    from agentcore_metering.adapters.django import LLMTracker

    from accounts.models import answer_language_name

    from .run_diagnostics import _diagnostic_system_prompt, _parse_json_object, validate_diagnostic_result

    now = timezone.now()
    if not AlertDiagnosis.objects.filter(uuid=diagnosis_uuid, status="pending", deadline_at__gt=now).update(
        status="running"
    ):
        return False
    diagnosis = AlertDiagnosis.objects.get(uuid=diagnosis_uuid)
    try:
        evidence, model_ref = _diagnostic_evidence(diagnosis)
        AlertDiagnosis.objects.filter(pk=diagnosis.pk, status="running").update(evidence=evidence)
        content, usage = LLMTracker.call_and_track(
            model_uuid=model_ref,
            messages=[
                {
                    "role": "system",
                    "content": _diagnostic_system_prompt(diagnosis.language)
                    + " This is an operational alert, not an answer-quality review."
                    f" Respond in {answer_language_name(diagnosis.language)}."
                    " Use only the supplied evidence IDs. Never quote private user content or credentials.",
                },
                {"role": "user", "content": json.dumps(evidence, ensure_ascii=False)},
            ],
            max_tokens=diagnosis.max_tokens,
            node_name="alert_diagnostics",
            json_repair=False,
            json_attempts=1,
            state={"source_type": "lens", "metadata": {"alert_diagnosis_uuid": str(diagnosis.uuid)}},
        )
        result = validate_diagnostic_result(_parse_json_object(content), SimpleNamespace(payload=evidence))
        changed = AlertDiagnosis.objects.filter(
            pk=diagnosis.pk, status="running", deadline_at__gt=timezone.now()
        ).update(status="completed", result=result, usage=usage or {}, finished_at=timezone.now(), error_code="")
    except Exception as exc:
        code = (
            str(exc)
            if isinstance(exc, ValueError) and str(exc) in {"MODEL_UNAVAILABLE", "SOURCE_UNAVAILABLE"}
            else "DIAGNOSIS_FAILED"
        )
        AlertDiagnosis.objects.filter(pk=diagnosis.pk, status="running", deadline_at__gt=timezone.now()).update(
            status="failed", error_code=code, finished_at=timezone.now()
        )
        logger.warning("Alert diagnosis failed: uuid=%s category=%s", diagnosis.uuid, code)
        changed = False
    advance_alert_diagnoses()
    return bool(changed)


def dispatch_alert_diagnoses(limit=25):
    """Recover broker failures while workers claim each analysis at most once."""

    from .tasks import execute_alert_diagnosis_task

    advance_alert_diagnoses()
    now = timezone.now()
    due = (
        AlertDiagnosis.objects.filter(status="pending", deadline_at__gt=now)
        .filter(Q(dispatched_at__isnull=True) | Q(dispatched_at__lt=now - timedelta(seconds=60)))
        .order_by("created_at")[:limit]
    )
    for diagnosis in due:
        if not AlertDiagnosis.objects.filter(pk=diagnosis.pk, dispatched_at=diagnosis.dispatched_at).update(
            dispatched_at=now
        ):
            continue
        seconds = max(1, int((diagnosis.deadline_at - timezone.now()).total_seconds()))
        try:
            execute_alert_diagnosis_task.apply_async(
                args=[str(diagnosis.uuid)],
                expires=diagnosis.deadline_at,
                soft_time_limit=seconds,
                time_limit=seconds + 5,
            )
        except Exception:
            AlertDiagnosis.objects.filter(pk=diagnosis.pk, dispatched_at=now).update(dispatched_at=None)
            logger.warning("Alert diagnosis broker unavailable: uuid=%s", diagnosis.uuid)


@transaction.atomic
def advance_alert_diagnoses():
    """Release waiting alerts on deadline and add one supplement after base acceptance."""

    from .alerts import _refresh_event_status

    now = timezone.now()
    AlertDiagnosis.objects.filter(status__in=["pending", "running"], deadline_at__lte=now).update(
        status="timed_out", error_code="DIAGNOSIS_TIMEOUT", finished_at=now
    )
    waiting = AlertDelivery.objects.filter(status="diagnosing", diagnosis__status__in=FINAL_STATUSES)
    event_ids = set(waiting.values_list("event_id", flat=True))
    waiting.update(status="pending", next_attempt_at=now)
    supplements = AlertDelivery.objects.filter(
        event_id=OuterRef("event_id"), subscription_id=OuterRef("subscription_id"), phase="diagnosis"
    )
    originals = (
        AlertDelivery.objects.filter(
            status="sent",
            phase="initial",
            subscription__isnull=False,
            diagnosis__status="completed",
            event__rule_snapshot__diagnosis_mode="notify_then_diagnose",
        )
        .annotate(has_supplement=Exists(supplements))
        .filter(has_supplement=False)
        .select_related("event", "diagnosis", "subscription")[:100]
    )
    for original in originals:
        destination = original.destination
        key = delivery_identity(original.event.source_key, destination, "diagnosis")
        supplement, created = AlertDelivery.objects.get_or_create(
            dedupe_key=key,
            defaults={
                "event": original.event,
                "subscription": original.subscription,
                "phase": "diagnosis",
                "diagnosis": original.diagnosis,
                "destination": destination,
            },
        )
        if created:
            event_ids.add(original.event_id)
        elif supplement.event_id != original.event_id or supplement.subscription_id != original.subscription_id:
            AlertDelivery.objects.get_or_create(
                event=original.event,
                subscription=original.subscription,
                phase="diagnosis",
                defaults={
                    "status": "suppressed",
                    "destination": destination,
                    "error_message": "Duplicate diagnosis destination",
                },
            )
    for event in AlertEvent.objects.filter(pk__in=event_ids):
        _refresh_event_status(event)
    if event_ids:
        from .alerts import _wake_dispatcher

        transaction.on_commit(_wake_dispatcher)
