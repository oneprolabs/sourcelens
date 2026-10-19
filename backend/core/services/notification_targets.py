"""SourceLens native robot targets, rich messages and recorded synchronous delivery."""

import base64
import hashlib
import hmac
import html
import re
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from agentcore_notifier.adapters.django.models import NotificationRecord
from django.utils import timezone
from rest_framework import serializers

NATIVE_TARGET_TYPES = frozenset({"feishu", "wecom", "dingtalk"})
TARGET_URLS = {
    "feishu": ("open.feishu.cn", "/open-apis/bot/v2/hook/"),
    "wecom": ("qyapi.weixin.qq.com", "/cgi-bin/webhook/send"),
    "dingtalk": ("oapi.dingtalk.com", "/robot/send"),
}
TEST_MESSAGES = {
    "zh-hans": (
        "[SourceLens] 通知测试",
        "通知目标已连接。您将在这里收到订阅的事件和诊断结果。",
    ),
    "en": (
        "[SourceLens] Notification test",
        "Target connected. Subscribed events and diagnosis results will arrive here.",
    ),
    "es": (
        "[SourceLens] Prueba de notificación",
        "Destino conectado. Aquí recibirá los eventos y diagnósticos suscritos.",
    ),
}


def validate_target_config(provider, config, previous=None):
    """Validate official endpoints and preserve write-only credentials on edits."""

    if not isinstance(config, dict):
        raise serializers.ValidationError({"config": "Expected an object."})
    merged = {**(previous or {}), **config}
    for key in ("url", "sign_secret"):
        value = config.get(key)
        if value is not None and not isinstance(value, str):
            raise serializers.ValidationError({key: "Expected text."})
        merged[key] = (value or "").strip() or (previous or {}).get(key, "")
    try:
        parts = urlsplit(merged["url"])
        host, path = TARGET_URLS[provider]
        valid_path = (
            parts.path.startswith(path) and len(parts.path) > len(path) if provider == "feishu" else parts.path == path
        )
        valid = (
            parts.scheme == "https"
            and parts.hostname == host
            and parts.port in (None, 443)
            and not parts.username
            and not parts.password
            and not parts.fragment
            and valid_path
        )
        query = dict(parse_qsl(parts.query))
        if provider != "feishu":
            valid = valid and bool(query.get("key" if provider == "wecom" else "access_token"))
    except ValueError:
        valid = False
    if not valid:
        raise serializers.ValidationError({"url": "Use the official HTTPS robot URL for this platform."})
    for key in ("message_prefix", "language"):
        if key in merged and not isinstance(merged[key], str):
            raise serializers.ValidationError({key: "Expected text."})
    if len(merged.get("message_prefix", "")) > 100:
        raise serializers.ValidationError({"message_prefix": "Use at most 100 characters."})
    if merged.get("language", "en") not in TEST_MESSAGES:
        raise serializers.ValidationError({"language": "Unsupported language."})
    return {key: merged[key] for key in ("url", "sign_secret", "message_prefix", "language") if key in merged}


def _bounded(text, byte_limit):
    """Truncate at UTF-8 boundaries and visibly indicate omitted content."""

    raw = str(text).encode("utf-8")
    return str(text) if len(raw) <= byte_limit else raw[: byte_limit - 3].decode("utf-8", errors="ignore") + "…"


def _markdown(text):
    """Keep untrusted text from producing links, mentions or platform markup."""

    return re.sub(r"([\\`*_{}\[\]()#!|~])", r"\\\1", html.escape(str(text), quote=False)).replace("@", "＠")


def build_payload(provider, title, body, *, warning=True):
    """Render the same localized business summary in each platform's native format."""

    title = _bounded(title, 250)
    lines = str(body).splitlines()
    if lines and lines[0].startswith("[SourceLens]"):
        lines = lines[1:]
    budget = 3200 if provider == "wecom" else 10000
    content = _bounded(_markdown("\n".join(lines)), budget)
    if provider == "feishu":
        return {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {
                    "template": "orange" if warning else "blue",
                    "title": {"tag": "plain_text", "content": title},
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {"tag": "lark_md", "content": content or "—"},
                    },
                    {
                        "tag": "note",
                        "elements": [{"tag": "plain_text", "content": "SourceLens"}],
                    },
                ],
            },
        }
    markdown = f"### {_markdown(title)}\n\n{content}"
    if provider == "wecom":
        return {"msgtype": "markdown", "markdown": {"content": markdown}}
    if provider == "dingtalk":
        return {
            "msgtype": "markdown",
            "markdown": {"title": title, "text": markdown},
            "at": {"isAtAll": False},
        }
    raise ValueError("Unsupported notification target")


def send_target(
    channel,
    title,
    body,
    *,
    source_id="",
    source_type="alert",
    user=None,
    warning=True,
    language=None,
):
    """Send once, require a provider acknowledgement and persist a secret-free attempt."""

    provider = channel.channel_type
    config = validate_target_config(provider, channel.config)
    prefix = config.get("message_prefix", "")
    payload = build_payload(provider, f"{prefix} {title}".strip(), body, warning=warning)
    wire_payload = dict(payload)
    url = config["url"]
    secret = config.get("sign_secret", "")
    if secret and provider == "feishu":
        timestamp = str(int(time.time()))
        signature = hmac.new(f"{timestamp}\n{secret}".encode(), digestmod=hashlib.sha256).digest()
        wire_payload.update(timestamp=timestamp, sign=base64.b64encode(signature).decode())
    elif secret and provider == "dingtalk":
        timestamp = str(int(time.time() * 1000))
        signature = hmac.new(secret.encode(), f"{timestamp}\n{secret}".encode(), hashlib.sha256).digest()
        parts = urlsplit(url)
        query = [(key, value) for key, value in parse_qsl(parts.query) if key not in {"timestamp", "sign"}]
        query.extend([("timestamp", timestamp), ("sign", base64.b64encode(signature).decode())])
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
    response_data, error, success = None, "", False
    try:
        response = requests.post(url, json=wire_payload, timeout=10, allow_redirects=False)
        if response.status_code != 200:
            error = f"{provider}: HTTP {response.status_code}"
        else:
            data = response.json()
            keys = ("code", "StatusCode") if provider == "feishu" else ("errcode",)
            code = next((data[key] for key in keys if key in data), None) if isinstance(data, dict) else None
            code = code if type(code) is int else None
            success = code == 0
            response_data = {"code": code}
            if not success:
                error = f"{provider}: provider rejected message (code={code})"
    except (requests.RequestException, ValueError):
        error = f"{provider}: request failed or invalid JSON response"
    record = NotificationRecord.objects.create(
        channel=provider,
        provider_type=provider,
        channel_link=channel if channel.pk else None,
        source_app="lens",
        source_type=source_type,
        source_id=source_id,
        user=user,
        payload=payload,
        target={
            "channel_uuid": str(channel.uuid) if channel.pk else "",
            "name": channel.name,
            "channel_type": provider,
        },
        locale=language or config.get("language", ""),
        status="success" if success else "failed",
        response=response_data,
        error_message=error,
        sent_at=timezone.now() if success else None,
    )
    return {"success": success, "error": error, "record_uuid": str(record.uuid)}
