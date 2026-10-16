"""
Cloudflare Turnstile verification service.

Verifies a client-submitted Turnstile token against Cloudflare's
siteverify endpoint using only the standard library, so no extra
dependency is required.
"""

import ipaddress
import json
import logging
import urllib.parse
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

VERIFY_TIMEOUT_SECONDS = 5


def get_client_ip(meta):
    """Return the client IP supplied by the trusted reverse proxy.

    The nginx configurations overwrite X-Real-IP with their observed peer
    address before forwarding requests to the private backend. Do not parse
    X-Forwarded-For here because callers can prepend an untrusted value.
    """
    return meta.get('HTTP_X_REAL_IP') or meta.get('REMOTE_ADDR')


def is_bypassed_ip(remote_ip):
    """Return whether an IP is allowed to skip Turnstile verification."""
    if not remote_ip:
        return False
    try:
        address = ipaddress.ip_address(remote_ip.strip())
    except ValueError:
        return False

    configured = getattr(settings, 'TURNSTILE_BYPASS_IPS', ())
    if isinstance(configured, str):
        configured = configured.split(',')
    for network in configured:
        try:
            if address in ipaddress.ip_network(str(network).strip(), strict=False):
                return True
        except ValueError:
            logger.warning("Ignoring invalid Turnstile bypass network: %s", network)
    return False


def is_verification_required(remote_ip=None):
    """Return the shared verification policy for the current client."""
    return bool(
        getattr(settings, 'TURNSTILE_ENABLED', True)
        and getattr(settings, 'TURNSTILE_SECRET_KEY', '')
        and not is_bypassed_ip(remote_ip)
    )


def verify_token(token, remote_ip=None):
    """
    Verify a Turnstile token with Cloudflare.

    Args:
        token: The token returned by the Turnstile widget.
        remote_ip: Optional client IP for additional validation.

    Returns:
        tuple: (success, error_codes)
            - (True, []) when verification passes or is bypassed.
            - (False, [..]) with Cloudflare error codes on failure.
    """
    if not is_verification_required(remote_ip):
        logger.info("Turnstile verification is not required for this request.")
        return True, []

    secret = settings.TURNSTILE_SECRET_KEY
    if not token:
        return False, ['missing-input-response']

    payload = {'secret': secret, 'response': token}
    if remote_ip:
        payload['remoteip'] = remote_ip

    data = urllib.parse.urlencode(payload).encode('utf-8')
    request = urllib.request.Request(
        settings.TURNSTILE_VERIFY_URL,
        data=data,
        method='POST',
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=VERIFY_TIMEOUT_SECONDS,
        ) as response:
            body = json.loads(response.read().decode('utf-8'))
    except Exception as exc:
        logger.error(
            "Turnstile verification request failed: %s: %s",
            type(exc).__name__,
            exc,
        )
        return False, ['network-error']

    success = bool(body.get('success', False))
    error_codes = body.get('error-codes', []) or []
    if not success:
        logger.warning(
            "Turnstile verification rejected: %s",
            error_codes,
        )
    return success, error_codes
