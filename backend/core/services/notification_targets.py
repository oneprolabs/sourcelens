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


def _feishu_card(title, content, action_url, action_label):
    """Render bounded, escaped business sections using Feishu Card JSON 2.0."""

    remaining = 10000

    def text(value, limit=1000):
        """Share a UTF-8 budget across all user-authored regions of the card."""

        nonlocal remaining
        if remaining < 4:
            return ""
        value = _bounded(_markdown(value), min(limit, remaining))
        remaining -= len(value.encode("utf-8"))
        return value

    elements = []
    if content.get("summary"):
        elements.append({"tag": "markdown", "content": f"**{text(content['summary'], 600)}**"})
    for key in ("fields", "metrics"):
        items = content.get(key, [])[: 8 if key == "fields" else 6]
        for offset in range(0, len(items), 2):
            columns = []
            for label, value in items[offset : offset + 2]:
                columns.append(
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 1,
                        "elements": [{"tag": "markdown", "content": f"**{text(label, 120)}**\n{text(value, 400)}"}],
                    }
                )
            elements.append(
                {
                    "tag": "column_set",
                    "flex_mode": "stretch",
                    "background_style": "grey" if key == "metrics" else "default",
                    "columns": columns,
                }
            )
    for label, value in content.get("sections", [])[:8]:
        heading = text(label, 120)
        body = text(value, 6000)
        if not body:
            continue
        elements.extend([{"tag": "hr"}, {"tag": "markdown", "content": f"**{heading}**\n{body}" if heading else body}])
    if action_url:
        elements.extend(
            [
                {"tag": "hr"},
                {
                    "tag": "button",
                    "type": "primary",
                    "text": {"tag": "plain_text", "content": action_label},
                    "behaviors": [{"type": "open_url", "default_url": action_url}],
                },
            ]
        )
    reference = text(content.get("reference") or "SourceLens", 400)
    elements.append({"tag": "markdown", "content": f"<font color='grey'>{reference}</font>"})
    color = content.get("color")
    return {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "config": {"wide_screen_mode": True},
            "header": {
                "template": color if color in {"red", "orange", "blue"} else "orange",
                "title": {"tag": "plain_text", "content": title},
            },
            "body": {"elements": elements},
        },
    }


def build_payload(provider, title, body, *, warning=True, action_url="", action_label="", card_content=None):
    """Render the same localized business summary in each platform's native format."""

    title = _bounded(title, 250)
    lines = str(body).splitlines()
    if lines and lines[0].startswith("[SourceLens]"):
        lines = lines[1:]
    if action_url:
        parts = urlsplit(action_url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            raise ValueError("Use an absolute HTTP(S) action URL without credentials")
        if any(character in action_url for character in "\r\n<>[]() ") or len(action_url) > 2048:
            raise ValueError("Invalid notification action URL")
        action_label = _bounded(action_label or "SourceLens", 100)
        if lines and lines[-1] == f"{action_label}: {action_url}":
            lines = lines[:-1]
    if provider == "feishu" and card_content:
        return _feishu_card(title, card_content, action_url, action_label)
    budget = 3200 if provider == "wecom" else 10000
    content = _bounded(_markdown("\n".join(lines)), budget)
    if provider == "feishu":
        elements = [{"tag": "div", "text": {"tag": "lark_md", "content": content or "—"}}]
        if action_url:
            elements.append(
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "type": "primary",
                            "text": {"tag": "plain_text", "content": action_label},
                            "url": action_url,
                        }
                    ],
                }
            )
        elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": "SourceLens"}]})
        return {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {
                    "template": "orange" if warning else "blue",
                    "title": {"tag": "plain_text", "content": title},
                },
                "elements": elements,
            },
        }
    heading = f"### {_markdown(title)}\n\n"
    footer = f"\n\n[{_markdown(action_label)}]({action_url})" if action_url else ""
    limit = 4096 if provider == "wecom" else 12000
    content = _bounded(content, min(budget, limit - len((heading + footer).encode("utf-8"))))
    markdown = f"{heading}{content}{footer}"
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
    action_url="",
    action_label="",
    card_content=None,
):
    """Send once, require a provider acknowledgement and persist a secret-free attempt."""

    provider = channel.channel_type
    config = validate_target_config(provider, channel.config)
    prefix = config.get("message_prefix", "")
    payload = build_payload(
        provider,
        f"{prefix} {title}".strip(),
        body,
        warning=warning,
        action_url=action_url,
        action_label=action_label,
        card_content=card_content,
    )
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
