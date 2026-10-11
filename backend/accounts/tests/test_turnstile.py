from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from accounts.services import turnstile


class TurnstileVerificationTests(SimpleTestCase):
    """Verify the explicit development bypass remains network-free."""

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
