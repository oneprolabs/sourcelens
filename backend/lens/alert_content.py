"""Recipient-language notification templates without process-global locale state."""

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
        "Rule",
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
    ),
    "zh-CN": (
        "规则",
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
    ),
    "es": (
        "Regla",
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
)


def notification_language(subscription):
    """Personal mail follows the current profile; managed targets choose a locale."""

    if subscription.personal:
        return normalize_answer_language(getattr(getattr(subscription.user, "profile", None), "language", None))
    return normalize_answer_language(subscription.language)


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
        f"{labels['resource']}: {detail.get('assistant_name') or detail.get('datasource_name') or '-'}",
        f"{labels['operation']}: {event.source_uuid}",
        f"{labels['conditions']}: {', '.join(events.get(value, value) for value in detail.get('matched_events', []))}",
    ]
    if event.subject == "message":
        lines = [
            title,
            f"{labels['topic']}: {detail.get('topic', '')}",
            translated.get("body") or str(detail.get("body") or ""),
        ]
    for key in ("error", "total_tokens", "rounds", "duration_seconds", "failed_files"):
        if detail.get(key):
            lines.append(f"{labels[key]}: {detail[key]}")
    diagnosis = delivery.diagnosis
    mode = event.rule_snapshot.get("diagnosis_mode", "direct")
    if diagnosis and (mode == "diagnose_first" or delivery.phase == "diagnosis"):
        if diagnosis.status == "completed" and diagnosis.language == language:
            title = f"{title} · {labels['diagnosis']}"
            lines.extend(["", f"{labels['diagnosis']}: {diagnosis.result.get('summary', '')[:1200]}"])
            cause = diagnosis.result.get("root_cause") or {}
            if cause:
                lines.append(f"{labels['root_cause']}: {cause.get('description', '')[:1200]}")
                lines.append(f"{labels['evidence']}: {', '.join(cause.get('evidence_refs', []))}")
            for item in diagnosis.result.get("recommendations", [])[:3]:
                lines.append(f"{labels['recommendations']}: {item.get('action', '')[:800]}")
        else:
            lines.extend(["", labels["timed_out" if diagnosis.status == "timed_out" else "failed"]])
    lines[0] = title
    return title, "\n".join(lines)[:16000]
