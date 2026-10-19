"""Recipient-language notification templates without process-global locale state."""

from datetime import timezone as datetime_timezone
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from uuid import UUID

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from accounts.models import normalize_answer_language

LANGUAGES = ("en-US", "zh-CN", "es")
EVENT_KEYS = (
    "message_published",
    "run_failed",
    "run_partial",
    "run_timeout",
    "run_token_limit",
    "run_round_limit",
    "run_slow",
    "sync_failed",
    "sync_partial",
    "sync_timeout",
    "processing_failed",
    "processing_partial",
    "processing_timeout",
    "token_exceeded",
    "rounds_exceeded",
)
EVENT_TEXT = {
    "en-US": (
        "Message published",
        "Q&A failed",
        "Answer incomplete",
        "Q&A timed out",
        "Token budget reached",
        "Round limit reached",
        "Run duration exceeded",
        "Sync failed",
        "Sync partially failed",
        "Sync timed out",
        "Processing failed",
        "Processing partially failed",
        "Processing timed out",
        "Token threshold exceeded",
        "Round threshold exceeded",
    ),
    "zh-CN": (
        "消息已发布",
        "问答失败",
        "回答不完整",
        "问答超时",
        "因 token 预算结束",
        "因轮次限制结束",
        "运行时长超过阈值",
        "同步失败",
        "同步部分失败",
        "同步超时",
        "处理失败",
        "部分文件处理失败",
        "处理超时",
        "累计 token 超过阈值",
        "轮次超过阈值",
    ),
    "es": (
        "Mensaje publicado",
        "Error en consulta",
        "Respuesta incompleta",
        "Consulta agotada",
        "Presupuesto de tokens agotado",
        "Límite de rondas alcanzado",
        "Duración excedida",
        "Error de sincronización",
        "Sincronización parcial",
        "Sincronización agotada",
        "Error de procesamiento",
        "Procesamiento parcial",
        "Procesamiento agotado",
        "Umbral de tokens superado",
        "Umbral de rondas superado",
    ),
}
LABEL_TEXT = {
    "en-US": (
        "Subscription",
        "Resource",
        "Operation",
        "Conditions",
        "Error code",
        "Tokens",
        "Rounds",
        "Duration (seconds)",
        "Failed files",
        "Diagnosis",
        "Root cause",
        "Recommendations",
        "Evidence",
        "Diagnosis timed out; basic alert sent.",
        "Diagnosis unavailable; basic alert sent.",
        "Topic",
        "Assistant",
        "User",
        "Occurred at",
        "Started at",
        "Question",
        "View Run details",
        "Threshold",
        "View conversation",
    ),
    "zh-CN": (
        "订阅",
        "资源",
        "操作",
        "触发条件",
        "错误代码",
        "Token 用量",
        "轮次",
        "耗时（秒）",
        "失败文件数",
        "诊断结果",
        "可能原因",
        "处理建议",
        "依据",
        "诊断超时，已发送基础告警。",
        "诊断未完成，已发送基础告警。",
        "消息主题",
        "助手",
        "用户",
        "发生时间",
        "开始时间",
        "用户问题",
        "查看 Run 详情",
        "触发阈值",
        "查看会话",
    ),
    "es": (
        "Suscripción",
        "Recurso",
        "Operación",
        "Condiciones",
        "Código de error",
        "Tokens",
        "Rondas",
        "Duración (segundos)",
        "Archivos fallidos",
        "Diagnóstico",
        "Causa probable",
        "Recomendaciones",
        "Evidencia",
        "Diagnóstico agotado; se envió la alerta básica.",
        "Diagnóstico no disponible; se envió la alerta básica.",
        "Tema",
        "Asistente",
        "Usuario",
        "Fecha del evento",
        "Hora de inicio",
        "Pregunta",
        "Ver detalles del Run",
        "Umbral",
        "Ver conversación",
    ),
}
LABEL_KEYS = (
    "rule",
    "resource",
    "operation",
    "conditions",
    "error",
    "total_tokens",
    "rounds",
    "duration_seconds",
    "failed_files",
    "diagnosis",
    "root_cause",
    "recommendations",
    "evidence",
    "timed_out",
    "failed",
    "topic",
    "assistant",
    "user",
    "occurred_at",
    "started_at",
    "question",
    "view_run",
    "threshold",
    "view_conversation",
)


def run_detail_url(event, *, personal=False) -> str:
    """Link managed alerts to Run inspection and personal alerts to the owned conversation."""

    if event.subject != "run":
        return ""
    try:
        parts = urlsplit(settings.FRONTEND_URL)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            return ""
        base_path = parts.path.rstrip("/")
        if personal:
            if not event.run_id:
                return ""
            session = event.run.session
            path = f"{base_path}/lens/assistants/{quote(session.assistant.slug, safe='')}/history"
            query = {"session": str(session.uuid)}
        else:
            run_uuid = UUID(str(event.detail.get("run_uuid") or event.source_uuid or event.run.uuid))
            path = f"{base_path}/management/lens/runs"
            query = {"run": str(run_uuid)}
        return urlunsplit((parts.scheme, parts.netloc, path, urlencode(query), ""))
    except (ValueError, AttributeError, TypeError):
        return ""


def run_link_label(language: str, *, personal=False) -> str:
    """Keep navigation captions consistent with the recipient's destination."""

    key = "view_conversation" if personal else "view_run"
    return dict(zip(LABEL_KEYS, LABEL_TEXT[normalize_answer_language(language)]))[key]


def _event_time(value) -> str:
    """Display an explicit UTC timestamp independently of worker-local timezone state."""

    parsed = parse_datetime(value) if isinstance(value, str) else value
    if not parsed:
        return "-"
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, datetime_timezone.utc)
    return parsed.astimezone(datetime_timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def notification_language(subscription):
    """Personal mail follows the current profile; managed targets choose a locale."""

    if subscription.personal:
        return normalize_answer_language(getattr(getattr(subscription.user, "profile", None), "language", None))
    return normalize_answer_language(subscription.language)


def _diagnosis_sections(delivery, language):
    """Share localized diagnostic content between text and structured cards."""

    labels = dict(zip(LABEL_KEYS, LABEL_TEXT[language]))
    diagnosis = delivery.diagnosis
    mode = delivery.event.rule_snapshot.get("diagnosis_mode", "direct")
    if not diagnosis or (mode != "diagnose_first" and delivery.phase != "diagnosis"):
        return []
    if diagnosis.status != "completed" or diagnosis.language != language:
        return [(labels["diagnosis"], labels["timed_out" if diagnosis.status == "timed_out" else "failed"])]
    sections = [(labels["diagnosis"], diagnosis.result.get("summary", "")[:1200])]
    cause = diagnosis.result.get("root_cause") or {}
    if cause:
        sections.extend(
            [
                (labels["root_cause"], cause.get("description", "")[:1200]),
                (labels["evidence"], ", ".join(cause.get("evidence_refs", []))),
            ]
        )
    sections.extend(
        (labels["recommendations"], item.get("action", "")[:800])
        for item in diagnosis.result.get("recommendations", [])[:3]
    )
    return sections


def notification_card_content(delivery, language):
    """Describe business fields and sections without parsing user-authored plain text."""

    language = normalize_answer_language(language)
    labels = dict(zip(LABEL_KEYS, LABEL_TEXT[language]))
    events = dict(zip(EVENT_KEYS, EVENT_TEXT[language]))
    event, detail = delivery.event, delivery.event.detail
    fields = []
    sections = []
    if event.subject == "run":
        username = detail.get("username") or ""
        display_name = detail.get("user_display_name") or username or "-"
        user = f"{display_name} ({username})" if username and display_name != username else display_name
        fields.extend([(labels["assistant"], detail.get("assistant_name") or "-"), (labels["user"], user)])
    elif event.subject == "message":
        translated = (detail.get("translations") or {}).get(language, {})
        fields.append((labels["topic"], detail.get("topic") or "-"))
        sections.append(("", translated.get("body") or str(detail.get("body") or "")))
    else:
        fields.append((labels["resource"], detail.get("datasource_name") or "-"))
    fields.append((labels["occurred_at"], _event_time(detail.get("occurred_at") or event.created_at)))
    if event.subject == "run":
        fields.append((labels["started_at"], _event_time(detail.get("started_at"))))
    fields.append((labels["rule"], event.rule_snapshot.get("name") or "-"))
    metrics = [
        (labels[key], str(detail[key]))
        for key in ("total_tokens", "rounds", "duration_seconds", "failed_files")
        if detail.get(key) not in (None, "", 0)
    ]
    threshold_key = {
        "token_exceeded": "token_threshold",
        "rounds_exceeded": "rounds_threshold",
        "run_slow": "duration_threshold",
    }.get(event.event_type)
    if threshold_key and detail.get(threshold_key) is not None:
        metrics.append((labels["threshold"], str(detail[threshold_key])))
    if detail.get("error"):
        sections.append((labels["error"], detail["error"]))
    sections.extend(_diagnosis_sections(delivery, language))
    if event.subject == "run":
        sections.append((labels["question"], str(detail.get("question") or "-")[:2001]))
    return {
        "color": (
            "blue"
            if event.subject == "message"
            else "red" if event.event_type.endswith(("_failed", "_timeout")) else "orange"
        ),
        "summary": ", ".join(events.get(value, value) for value in detail.get("matched_events", [])),
        "fields": fields,
        "metrics": metrics,
        "sections": sections,
        "reference": f"SourceLens · {'Run' if event.subject == 'run' else labels['operation']}: {event.source_uuid}",
    }


def render_notification(delivery, language):
    """Render bounded plain text while retaining user-authored names and messages."""

    language = normalize_answer_language(language)
    events = dict(zip(EVENT_KEYS, EVENT_TEXT[language]))
    labels = dict(zip(LABEL_KEYS, LABEL_TEXT[language]))
    event, detail = delivery.event, delivery.event.detail
    translated = (detail.get("translations") or {}).get(language, {}) if event.subject == "message" else {}
    heading = translated.get("title") or detail.get("title") or events.get(event.event_type, event.event_type)
    title = f"[SourceLens] {heading}"
    lines = [
        title,
        f"{labels['rule']}: {event.rule_snapshot.get('name', '')}",
        f"{labels['assistant'] if event.subject == 'run' else labels['resource']}: "
        f"{detail.get('assistant_name') or detail.get('datasource_name') or '-'}",
        f"{labels['occurred_at']}: {_event_time(detail.get('occurred_at') or event.created_at)}",
        f"{labels['conditions']}: {', '.join(events.get(value, value) for value in detail.get('matched_events', []))}",
    ]
    if event.subject == "run":
        username = detail.get("username") or ""
        display_name = detail.get("user_display_name") or username or "-"
        user = f"{display_name} ({username})" if username and display_name != username else display_name
        lines.extend(
            [
                f"{labels['user']}: {user}",
                f"{labels['started_at']}: {_event_time(detail.get('started_at'))}",
                f"Run: {detail.get('run_uuid') or event.source_uuid}",
            ]
        )
    else:
        lines.append(f"{labels['operation']}: {event.source_uuid}")
    if event.subject == "message":
        lines = [
            title,
            f"{labels['topic']}: {detail.get('topic', '')}",
            translated.get("body") or str(detail.get("body") or ""),
        ]
    for key in ("error", "total_tokens", "rounds", "duration_seconds", "failed_files"):
        if detail.get(key) is not None and detail.get(key) != "":
            lines.append(f"{labels[key]}: {detail[key]}")
    diagnosis_sections = _diagnosis_sections(delivery, language)
    if diagnosis_sections:
        if delivery.diagnosis.status == "completed" and delivery.diagnosis.language == language:
            title = f"{title} · {labels['diagnosis']}"
        lines.extend(["", *(f"{label}: {content}" for label, content in diagnosis_sections)])
    if event.subject == "run":
        # Keep alert details ahead of question excerpts when transports truncate the body.
        lines.extend(["", f"{labels['question']}:\n{str(detail.get('question') or '-')[:2001]}"])
    lines[0] = title
    personal = bool(delivery.subscription and delivery.subscription.personal)
    url = run_detail_url(event, personal=personal)
    footer = f"\n\n{run_link_label(language, personal=personal)}: {url}" if url else ""
    return title, "\n".join(lines)[: 16000 - len(footer)] + footer
