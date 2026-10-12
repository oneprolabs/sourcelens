"""Public model errors retain actionable causes without provider text."""

import unittest

from lens.model_errors import model_error_code
from lens.runtime_events import sanitize_termination_detail


class ModelErrorTests(unittest.TestCase):
    def test_provider_failures_are_classified(self):
        cases = [
            ("Timeout", None, "Request timed out", "MODEL_TIMEOUT"),
            ("RateLimitError", 429, "insufficient_quota", "MODEL_QUOTA_EXCEEDED"),
            ("BadRequestError", 400, "Insufficient Balance", "MODEL_QUOTA_EXCEEDED"),
            ("ProviderError", 402, "Payment required", "MODEL_QUOTA_EXCEEDED"),
            ("ProviderError", 403, "账户余额不足", "MODEL_QUOTA_EXCEEDED"),
            ("AuthenticationError", 401, "invalid api key", "MODEL_AUTHENTICATION_ERROR"),
            ("PermissionDeniedError", 403, "forbidden", "MODEL_PERMISSION_DENIED"),
            ("RateLimitError", 429, "Too many requests", "MODEL_RATE_LIMITED"),
            ("BadRequestError", 400, "invalid request", "MODEL_INVALID_REQUEST"),
            ("NotFoundError", 404, "model not found", "MODEL_NOT_FOUND"),
        ]
        for name, status, message, expected in cases:
            with self.subTest(name=name, message=message):
                error = type(name, (Exception,), {"status_code": status})(message)
                self.assertEqual(model_error_code(error), expected)

    def test_wrapped_provider_body_preserves_billing_reason(self):
        cause = type("RateLimitError", (Exception,), {"status_code": 429})("rejected")
        cause.body = {"error": {"code": "insufficient_quota"}}
        error = RuntimeError("stream failed")
        error.__cause__ = cause
        self.assertEqual(model_error_code(error), "MODEL_QUOTA_EXCEEDED")

    def test_unknown_reason_is_not_guessed(self):
        self.assertEqual(model_error_code(RuntimeError("unexpected failure")), "")

    def test_structured_provider_code_preserves_billing_reason(self):
        error = RuntimeError("request rejected")
        error.code = "insufficient_balance"
        self.assertEqual(model_error_code(error), "MODEL_QUOTA_EXCEEDED")

    def test_billing_url_does_not_turn_rate_limit_into_payment_failure(self):
        error = type("RateLimitError", (Exception,), {"status_code": 429})(
            "Too many requests; see https://provider.example/billing"
        )
        self.assertEqual(model_error_code(error), "MODEL_RATE_LIMITED")

    def test_transport_disconnect_is_not_reported_as_provider_timeout(self):
        self.assertEqual(model_error_code(ConnectionError("peer closed connection")), "")

    def test_temporary_quota_limits_are_rate_limited(self):
        error = type("QuotaExceededError", (Exception,), {"status_code": 429})(
            "Quota exceeded for metric: generate_content_requests, limit: 20 requests per minute."
        )
        self.assertEqual(model_error_code(error), "MODEL_RATE_LIMITED")

    def test_public_termination_preserves_safe_codes_only(self):
        for code in (
            "MODEL_QUOTA_EXCEEDED",
            "MODEL_AUTHENTICATION_ERROR",
            "MODEL_PERMISSION_DENIED",
            "MODEL_RATE_LIMITED",
            "MODEL_INVALID_REQUEST",
            "MODEL_NOT_FOUND",
        ):
            with self.subTest(code=code):
                self.assertEqual(
                    sanitize_termination_detail(
                        {
                            "reason": "runtime_failure",
                            "code": code,
                            "message": "private provider detail",
                        }
                    ),
                    {"reason": "runtime_failure", "code": code},
                )
        self.assertEqual(sanitize_termination_detail({"code": "private provider detail"}), {})
