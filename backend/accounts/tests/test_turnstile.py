import json
from unittest.mock import patch
from urllib.parse import parse_qs

from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIRequestFactory

from accounts.services import turnstile
from accounts.urls import CustomLoginView, TurnstileStatusView
from accounts.views.login_otp import SendLoginCodeView
from core.settings.renders import CustomJSONRenderer


class TurnstileVerificationTests(SimpleTestCase):
    """Verify the explicit development bypass remains network-free."""

    @patch('accounts.services.turnstile.verify_token', return_value=(False, ['invalid-input-response']))
    def test_login_endpoints_forward_the_submitted_token(self, verify_token):
        for view in (CustomLoginView, SendLoginCodeView):
            with self.subTest(view=view.__name__):
                request = APIRequestFactory().post(
                    '/',
                    {'email': 'test@example.com', 'password': 'test-password', 'turnstile_token': 'test-token'},
                    format='json',
                    HTTP_X_REAL_IP='203.0.113.10',
                )
                response = view.as_view(authentication_classes=[])(request)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data['error_code'], 'TURNSTILE_FAILED')
                verify_token.assert_called_with('test-token', '203.0.113.10')

    @override_settings(TURNSTILE_ENABLED=True, TURNSTILE_SECRET_KEY='test-secret', TURNSTILE_BYPASS_IPS=())
    @patch('accounts.services.turnstile.urllib.request.urlopen')
    def test_public_client_uses_cloudflare_verification_result(self, urlopen):
        for success in (True, False):
            with self.subTest(success=success):
                codes = [] if success else ['timeout-or-duplicate']
                urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(
                    {'success': success, 'error-codes': codes}
                ).encode()
                self.assertEqual(turnstile.verify_token('test-token', '203.0.113.10'), (success, codes))
                payload = parse_qs(urlopen.call_args.args[0].data.decode())
                self.assertEqual(payload['response'], ['test-token'])
                self.assertEqual(payload['remoteip'], ['203.0.113.10'])

    def test_rendered_policy_matches_authentication_enforcement(self):
        for enabled, secret, ip, required in [
            (False, 'configured', '203.0.113.10', False),
            (True, 'configured', '192.0.2.10', False),
            (True, 'configured', '203.0.113.10', True),
            (True, '', '203.0.113.10', False),
        ]:
            with self.subTest(enabled=enabled, ip=ip, secret_configured=bool(secret)):
                with override_settings(
                    TURNSTILE_ENABLED=enabled,
                    TURNSTILE_SECRET_KEY=secret,
                    TURNSTILE_BYPASS_IPS=('192.0.2.10',),
                ):
                    request = APIRequestFactory().get('/', HTTP_X_REAL_IP=ip)
                    response = TurnstileStatusView.as_view(renderer_classes=[CustomJSONRenderer])(request)
                    response.render()
                    self.assertEqual(json.loads(response.content)['data']['required'], required)
                    self.assertEqual(response['Cache-Control'], 'no-store')
                    self.assertEqual(turnstile.verify_token('', ip)[0], not required)

    def test_client_ip_prefers_reverse_proxy_header(self):
        client_ip = turnstile.get_client_ip(
            {
                "HTTP_X_REAL_IP": "198.51.100.42",
                "REMOTE_ADDR": "172.20.0.4",
                "HTTP_X_FORWARDED_FOR": "203.0.113.99, 172.20.0.4",
            }
        )

        self.assertEqual(client_ip, "198.51.100.42")

    def test_client_ip_falls_back_to_socket_peer(self):
        self.assertEqual(
            turnstile.get_client_ip({"REMOTE_ADDR": "172.20.0.4"}),
            "172.20.0.4",
        )

    @override_settings(
        TURNSTILE_ENABLED=False,
        TURNSTILE_SECRET_KEY="configured-secret",
    )
    @patch("accounts.services.turnstile.urllib.request.urlopen")
    def test_disabled_verification_bypasses_cloudflare(self, urlopen):
        passed, errors = turnstile.verify_token("")

        self.assertTrue(passed)
        self.assertEqual(errors, [])
        urlopen.assert_not_called()

    @override_settings(
        TURNSTILE_ENABLED=True,
        TURNSTILE_SECRET_KEY="configured-secret",
    )
    def test_enabled_verification_rejects_a_missing_token(self):
        passed, errors = turnstile.verify_token("")

        self.assertFalse(passed)
        self.assertEqual(errors, ["missing-input-response"])

    @override_settings(
        TURNSTILE_ENABLED=True,
        TURNSTILE_SECRET_KEY="configured-secret",
        TURNSTILE_BYPASS_IPS=("192.0.2.10", "198.51.100.0/24"),
    )
    @patch("accounts.services.turnstile.urllib.request.urlopen")
    def test_allowlisted_ip_bypasses_cloudflare(self, urlopen):
        passed, errors = turnstile.verify_token("", "198.51.100.42")

        self.assertTrue(passed)
        self.assertEqual(errors, [])
        urlopen.assert_not_called()

    @override_settings(
        TURNSTILE_ENABLED=True,
        TURNSTILE_SECRET_KEY="configured-secret",
        TURNSTILE_BYPASS_IPS=("192.0.2.10",),
    )
    def test_non_allowlisted_ip_still_requires_a_token(self):
        passed, errors = turnstile.verify_token("", "198.51.100.42")

        self.assertFalse(passed)
        self.assertEqual(errors, ["missing-input-response"])
