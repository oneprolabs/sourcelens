"""Stable, non-sensitive Assistant descriptions for Smart Collaboration."""

_ROUTING_TEXT = {
    "en": {
        "capability": "Capability",
        "suitable_for": "Best suited for",
        "overview": "Assistant overview",
        "skills": "Available Skills",
        "mcps": "Available MCPs",
        "data_sources": "Available data sources",
        "unknown_capability": "Specialized capability",
        "unknown_request": "specialized requests",
    },
    "es": {
        "capability": "Capacidad",
        "suitable_for": "Adecuado para",
        "overview": "Resumen del asistente",
        "skills": "Skills disponibles",
        "mcps": "MCP disponibles",
        "data_sources": "Fuentes de datos disponibles",
        "unknown_capability": "Capacidad especializada",
        "unknown_request": "solicitudes especializadas",
    },
    "zh": {
        "capability": "能力",
        "suitable_for": "适合处理",
        "overview": "助手概述",
        "skills": "可用 Skills",
        "mcps": "可用 MCP",
        "data_sources": "可用数据源",
        "unknown_capability": "专用能力",
        "unknown_request": "专用请求",
    },
}

_CAPABILITY_DESCRIPTIONS = {
    "general_chat": {
        "en": "general chat and requests related to connected tools",
        "es": "conversación general y solicitudes relacionadas con herramientas conectadas",
        "zh": "通用对话及已连接工具相关请求",
    },
    "code_analysis": {
        "en": "code analysis, implementation review, and engineering troubleshooting",
        "es": "análisis de código, revisión de implementación y resolución de problemas de ingeniería",
        "zh": "代码分析、实现审查和工程排障请求",
    },
    "knowledge_qa": {
        "en": "questions about configured workspaces or knowledge bases",
        "es": "preguntas sobre áreas de trabajo o bases de conocimiento configuradas",
        "zh": "已配置工作区或知识库中的问答请求",
    },
}

_CAPABILITY_NAMES = {
    "general_chat": {
        "en": "General Chat",
        "es": "Conversación general",
        "zh": "通用对话",
    },
    "code_analysis": {
        "en": "Code Analysis",
        "es": "Análisis de código",
        "zh": "代码分析",
    },
    "knowledge_qa": {
        "en": "Knowledge Q&A",
        "es": "Preguntas y respuestas de conocimiento",
        "zh": "知识库问答",
    },
}


def _compact(value, limit=320):
    """Return a bounded, single-line representation of configured text."""

    return " ".join(str(value or "").split())[:limit]


def _resource_names(bindings, relation):
    """Return enabled resource names without exposing resource configuration."""

    names = []
    for binding in bindings.all():
        if not binding.enabled:
            continue
        resource = getattr(binding, relation)
        if getattr(resource, "enabled", True):
            name = _compact(getattr(resource, "name", ""), limit=80)
            if name:
                names.append(name)
    return names[:8]


def _datasource_names(assistant):
    """Return the enabled data source names without their configuration."""

    names = []
    for binding in assistant.datasource_bindings.all():
        datasource = getattr(binding, "datasource", None)
        if datasource is None:
            continue
        status = getattr(datasource, "status", "")
        if status and status != "active":
            continue
        name = _compact(getattr(datasource, "name", ""), limit=80)
        if name:
            names.append(name)
    return names[:8]


def _language_key(answer_language):
    """Return the supported routing-description language key."""

    normalized = str(answer_language or "").strip().lower()
    if normalized.startswith("zh") or normalized == "chinese":
        return "zh"
    if normalized.startswith("es") or normalized == "spanish":
        return "es"
    return "en"


def _field_sentence(label, value, language):
    """Return one localized label/value sentence."""

    if language == "zh":
        return f"{label}：{value}。"
    return f"{label}: {value}."


def build_routing_description(assistant, answer_language="en-US"):
    """Build a localized routing synopsis from Assistant configuration."""

    language = _language_key(answer_language)
    text = _ROUTING_TEXT[language]
    if getattr(assistant, "is_smart", False):
        smart_text = {
            "en": "Smart collaboration team",
            "es": "equipo de colaboración inteligente",
            "zh": "智能协作团队",
        }[language]
        parts = [_field_sentence(text["capability"], smart_text, language)]
        description = _compact(assistant.description)
        if description:
            parts.append(
                _field_sentence(text["overview"], description, language)
            )
        return "".join(parts)[:1000]
    capability = _CAPABILITY_DESCRIPTIONS.get(
        assistant.capability,
        {},
    ).get(language, text["unknown_request"])
    capability_name = _CAPABILITY_NAMES.get(
        assistant.capability,
        {},
    ).get(language, text["unknown_capability"])
    parts = [
        _field_sentence(text["capability"], capability_name, language),
        _field_sentence(text["suitable_for"], capability, language),
    ]
    description = _compact(assistant.description)
    if description:
        parts.append(_field_sentence(text["overview"], description, language))
    skills = _resource_names(assistant.skill_bindings, "skill")
    if skills:
        separator = "、" if language == "zh" else ", "
        parts.append(
            _field_sentence(text["skills"], separator.join(skills), language)
        )
    mcps = _resource_names(assistant.mcp_bindings, "mcp")
    if mcps:
        separator = "、" if language == "zh" else ", "
        parts.append(
            _field_sentence(text["mcps"], separator.join(mcps), language)
        )
    datasources = _datasource_names(assistant)
    if datasources:
        separator = "、" if language == "zh" else ", "
        parts.append(
            _field_sentence(
                text["data_sources"],
                separator.join(datasources),
                language,
            )
        )
    return "".join(parts)[:1000]


def refresh_routing_description(assistant):
    """Persist the stable English routing synopsis after configuration changes."""

    description = build_routing_description(assistant)
    if assistant.routing_description != description:
        assistant.routing_description = description
        assistant.save(update_fields=["routing_description", "updated_at"])
    return description
