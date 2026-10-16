"""Classify provider failures into safe, actionable public error codes."""

import json


def model_error_code(error):
    """Inspect structured status and wrapped causes without returning provider text."""

    errors = []
    while error is not None and all(error is not item for item in errors) and len(errors) < 8:
        errors.append(error)
        error = error.__cause__ or error.__context__
    text = " ".join(
        f"{type(item).__name__} {item} {getattr(item, 'code', '')} "
        f"{json.dumps(getattr(item, 'body', None), default=str, ensure_ascii=False)}"
        for item in errors
    ).lower()
    statuses = {
        str(getattr(item, "status_code", None) or getattr(getattr(item, "response", None), "status_code", ""))
        for item in errors
    }
    if "402" in statuses or any(
        marker in text
        for marker in (
            "insufficient_quota",
            "insufficient_balance",
            "insufficient balance",
            "insufficient credit",
            "credit balance",
            "exceeded your current quota",
            "payment required",
            "billing_hard_limit_reached",
            "billing_not_active",
            "budgetexceedederror",
            "arrearage",
            "balance is insufficient",
            "余额不足",
            "欠费",
            "额度不足",
            "配额不足",
        )
    ):
        return "MODEL_QUOTA_EXCEEDED"
    if "401" in statuses or any(
        marker in text
        for marker in (
            "authenticationerror",
            "invalid api key",
            "invalid_api_key",
            "unauthorized",
        )
    ):
        return "MODEL_AUTHENTICATION_ERROR"
    if "403" in statuses or "permissiondenied" in text:
        return "MODEL_PERMISSION_DENIED"
    if (
        any(isinstance(item, TimeoutError) for item in errors)
        or any(marker in text for marker in ("timeout", "timed out", "超时"))
        or statuses.intersection({"408", "504"})
    ):
        return "MODEL_TIMEOUT"
    if "429" in statuses or any(marker in text for marker in ("ratelimit", "rate limit", "too many requests")):
        return "MODEL_RATE_LIMITED"
    if "404" in statuses or "notfounderror" in text:
        return "MODEL_NOT_FOUND"
    if "400" in statuses or any(
        marker in text
        for marker in (
            "badrequest",
            "invalid request",
            "provider_invalid_request",
            "context_length_exceeded",
        )
    ):
        return "MODEL_INVALID_REQUEST"
    return ""
