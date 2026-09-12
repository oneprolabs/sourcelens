from unittest.mock import patch

import pytest

from lensnode import document_convert


class Response:
    def __init__(self, status_code):
        self.status_code = status_code


class Error(Exception):
    def __init__(self, code):
        super().__init__(str(code))
        self.response = Response(code)


def context():
    return {
        "conversion": {"vision_model_ref": "model"},
        "ai_gateway_url": "http://gateway",
        "lensnode_token": "token",
    }


def test_413_is_not_retried():
    ctx = context()
    with patch("lensnode.gateway_model.describe_image_result", side_effect=Error(413)) as call:
        with pytest.raises(RuntimeError, match="VISUAL_PAYLOAD_TOO_LARGE"):
            document_convert.describe_image_bytes(b"x", "image/png", ctx)
    assert call.call_count == 1
    assert ctx["conversion_cost"]["raw_attempts"] == 1
    assert ctx["conversion_cost"]["retry_attempts"] == 0


def test_504_retries_are_bounded_and_accounted():
    ctx = context()
    with patch("lensnode.gateway_model.describe_image_result", side_effect=Error(504)) as call:
        with patch("lensnode.document_convert.time.sleep"):
            with pytest.raises(RuntimeError, match="VISUAL_UPSTREAM_TIMEOUT"):
                document_convert.describe_image_bytes(b"x", "image/png", ctx)
    assert call.call_count == 3
    assert ctx["conversion_cost"]["raw_attempts"] == 3
    assert ctx["conversion_cost"]["failed_requests"] == 3
    assert ctx["conversion_cost"]["retry_attempts"] == 2
