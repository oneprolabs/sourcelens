"""Native notification target contracts and provider wire formats."""

import base64
import hashlib
import hmac
import json
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import requests
from agentcore_notifier.adapters.django.models import NotificationChannel, NotificationRecord
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from core.services.notification_targets import build_payload, send_target

URLS = {
    "feishu": "https://open.feishu.cn/open-apis/bot/v2/hook/test-key",
    "wecom": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test-key",
    "dingtalk": "https://oapi.dingtalk.com/robot/send?access_token=test-key",
}


class NotificationTargetTests(TestCase):
    """Verify real API persistence and records with only HTTP transport mocked."""

    def setUp(self):
        self.client = APIClient()
        self.admin = get_user_model().objects.create_user(username="target-admin", is_staff=True)
        self.client.force_authenticate(self.admin)
        self.root = "/api/v1/admin/notifications/channels/"

    def create_target(self, provider):
        response = self.client.post(
            self.root,
            {
                "name": provider,
                "channel_type": provider,
                "config": {"url": URLS[provider], "sign_secret": "secret"},
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["channel_type"], provider)
        self.assertNotIn("url", response.data["config"])
        self.assertNotIn("sign_secret", response.data["config"])
        return NotificationChannel.objects.get(uuid=response.data["uuid"])

    def test_native_types_are_persisted_and_secrets_survive_edits(self):
        for provider in URLS:
            with self.subTest(provider=provider):
                channel = self.create_target(provider)
                response = self.client.put(
                    f"{self.root}{channel.uuid}/",
                    {
                        "name": "Renamed",
                        "config": {"url": "", "sign_secret": "", "language": "es"},
                    },
                    format="json",
                )
                self.assertEqual(response.status_code, 200, response.data)
                channel.refresh_from_db()
                self.assertEqual(channel.channel_type, provider)
                self.assertEqual(channel.config["url"], URLS[provider])
                self.assertEqual(channel.config["sign_secret"], "secret")
        response = self.client.get(self.root)
        self.assertEqual(response.data["total"], 3)
        self.assertNotIn("test-key", str(response.data))
        self.assertNotIn("'secret'", str(response.data))

    def test_official_urls_only_and_type_is_immutable(self):
        for bad in (
            "http://127.0.0.1/",
            "https://open.feishu.cn.evil.test/",
            URLS["wecom"],
        ):
            response = self.client.post(
                self.root,
                {"name": "Bad", "channel_type": "feishu", "config": {"url": bad}},
                format="json",
            )
            self.assertEqual(response.status_code, 400)
        channel = self.create_target("feishu")
        response = self.client.put(f"{self.root}{channel.uuid}/", {"channel_type": "wecom"}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_regular_user_cannot_manage_or_test_targets(self):
        self.client.force_authenticate(get_user_model().objects.create_user(username="target-user"))
        self.assertEqual(self.client.get(self.root).status_code, 403)
        self.assertEqual(
            self.client.post(self.root + "validate/", {}, format="json").status_code,
            403,
        )

    @patch("core.services.notification_targets.requests.post")
    def test_signatures_strict_acknowledgements_and_records(self, post):
        for provider in URLS:
            channel = self.create_target(provider)
            post.return_value = Mock(
                status_code=200,
                json=Mock(return_value={"code" if provider == "feishu" else "errcode": 0}),
            )
            with patch("core.services.notification_targets.time.time", return_value=1700000000):
                result = send_target(channel, "Title", "Body", source_id="delivery-123")
            self.assertTrue(result["success"], result)
            args = post.call_args
            self.assertFalse(args.kwargs["allow_redirects"])
            if provider == "dingtalk":
                query = parse_qs(urlsplit(args.args[0]).query)
                timestamp = query["timestamp"][0]
                expected = base64.b64encode(
                    hmac.new(b"secret", f"{timestamp}\nsecret".encode(), hashlib.sha256).digest()
                ).decode()
                self.assertEqual(query["sign"], [expected])
            if provider == "feishu":
                expected = base64.b64encode(hmac.new(b"1700000000\nsecret", digestmod=hashlib.sha256).digest()).decode()
                self.assertEqual(args.kwargs["json"]["sign"], expected)
            record = NotificationRecord.objects.filter(channel_link=channel).get()
            self.assertEqual(record.channel, provider)
            self.assertEqual(record.source_id, "delivery-123")
            self.assertNotIn("secret", str(record.payload))
            self.assertNotIn("test-key", str(record.metadata))
            for response in ({"errcode": 310000, "errmsg": "blocked"}, {}, []):
                post.return_value.json.return_value = response
                self.assertFalse(send_target(channel, "Title", "Body")["success"])

    @patch("core.services.notification_targets.requests.post")
    def test_validation_uses_saved_secrets_and_localized_rich_payload(self, post):
        channel = self.create_target("feishu")
        post.return_value = Mock(status_code=200, json=Mock(return_value={"code": 0}))
        response = self.client.post(
            self.root + "validate/",
            {
                "channel_type": "feishu",
                "channel_uuid": str(channel.uuid),
                "config": {"language": "es"},
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["msg_type"], "interactive")
        self.assertIn("Prueba", str(payload))
        self.assertIn("sign", payload)
        self.assertEqual(NotificationRecord.objects.get().channel_link, channel)

    @patch("core.services.notification_targets.requests.post")
    def test_saved_target_test_endpoint_and_disabled_state(self, post):
        channel = self.create_target("dingtalk")
        post.return_value = Mock(status_code=200, json=Mock(return_value={"errcode": 0}))
        response = self.client.post(f"{self.root}{channel.uuid}/test/", {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        channel.is_active = False
        channel.save()
        response = self.client.post(f"{self.root}{channel.uuid}/test/", {}, format="json")
        self.assertEqual(response.status_code, 400)
        post.assert_called_once()

    def test_legacy_webhook_and_email_api_contracts_remain_supported(self):
        for provider in ("email", "webhook"):
            response = self.client.post(
                self.root,
                {"name": "Legacy", "channel_type": provider, "config": {}},
                format="json",
            )
            self.assertEqual(response.status_code, 201, response.data)
            detail = self.client.get(f"{self.root}{response.data['uuid']}/")
            self.assertEqual(detail.data["channel_type"], provider)

    @patch("core.services.notification_targets.requests.post")
    def test_transport_failures_are_recorded_without_credentials(self, post):
        channel = self.create_target("feishu")
        post.side_effect = requests.Timeout(URLS["feishu"] + "?secret=secret")
        result = send_target(channel, "Title", "Body")
        self.assertFalse(result["success"])
        record = NotificationRecord.objects.get()
        self.assertEqual(record.status, "failed")
        self.assertNotIn("test-key", record.error_message)
        self.assertNotIn("secret", record.error_message)
        self.assertEqual(record.target["channel_uuid"], str(channel.uuid))

    def test_malformed_target_types_are_rejected(self):
        for target_type in (None, [], {}):
            for endpoint in (self.root, self.root + "validate/"):
                response = self.client.post(endpoint, {"channel_type": target_type}, format="json")
                self.assertEqual(response.status_code, 400, response.data)

    def test_rich_payload_limits_and_untrusted_mentions(self):
        for provider in URLS:
            payload = build_payload(
                provider,
                "Title",
                "<at id=all></at> <@all> [click](https://evil.test)\n" + "错误" * 20000,
            )
            if provider == "feishu":
                self.assertEqual(payload["msg_type"], "interactive")
                text = payload["card"]["elements"][0]["text"]["content"]
            elif provider == "wecom":
                self.assertEqual(payload["msgtype"], "markdown")
                text = payload["markdown"]["content"]
                self.assertLessEqual(len(text.encode()), 4096)
            else:
                self.assertEqual(payload["msgtype"], "markdown")
                text = payload["markdown"]["text"]
                self.assertLessEqual(len(text.encode()), 12000)
                self.assertFalse(payload["at"]["isAtAll"])
            self.assertNotIn("<at", text)
            self.assertNotIn("<@all>", text)
            self.assertNotIn("](https://evil.test)", text)
            self.assertIn("…", text)

    def test_run_action_is_clickable_and_survives_bounded_untrusted_content(self):
        url = "https://lens.example.test/management/lens/runs?run=123"
        label = "查看 Run 详情"
        for provider in URLS:
            with self.subTest(provider=provider):
                payload = build_payload(
                    provider,
                    "[SourceLens] 问答失败",
                    "<at id=all></at> [fake](https://evil.test)\n" + "长问题" * 10000,
                    action_url=url,
                    action_label=label,
                )
                if provider == "feishu":
                    action = next(item for item in payload["card"]["elements"] if item["tag"] == "action")
                    self.assertEqual(action["actions"][0]["url"], url)
                    self.assertEqual(action["actions"][0]["text"]["content"], label)
                    text = payload["card"]["elements"][0]["text"]["content"]
                else:
                    text = payload["markdown"]["content" if provider == "wecom" else "text"]
                    self.assertIn(f"[{label}]({url})", text)
                    self.assertLessEqual(len(text.encode()), 4096 if provider == "wecom" else 12000)
                self.assertNotIn("](https://evil.test)", text)
                self.assertNotIn("<at", text)

    def test_structured_feishu_card_bounds_and_escapes_every_user_content_region(self):
        untrusted = "<at id=all></at> [fake](https://evil.test) " + "长内容" * 10000
        payload = build_payload(
            "feishu",
            "Title",
            "Plain text fallback",
            action_url="https://lens.example.test/management/lens/runs?run=123",
            card_content={
                "color": "red",
                "summary": untrusted,
                "fields": [("Assistant", untrusted), ("User", untrusted)],
                "metrics": [("Tokens", "12500"), ("Threshold", "10000")],
                "sections": [("Question", untrusted), ("Diagnosis", untrusted)],
                "reference": untrusted,
            },
        )
        self.assertEqual(payload["card"]["schema"], "2.0")
        text = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("<at", text)
        self.assertNotIn("](https://evil.test)", text)
        self.assertLess(len(text.encode()), 28000)
        self.assertIn("…", text)
        self.assertIn("https://lens.example.test/management/lens/runs?run=123", text)
