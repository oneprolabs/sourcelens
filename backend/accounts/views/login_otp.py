"""
Email verification code (OTP) login views.

Implements a passwordless login flow: request a code by email, then
verify it to receive JWT tokens. Unknown emails are auto-provisioned.
"""

import logging

from django.utils.translation import gettext_lazy as _

from drf_spectacular.utils import extend_schema

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import DEFAULT_ANSWER_LANGUAGE
from ..serializers import (
    AuthTokenResponseSerializer,
    SendLoginCodeSerializer,
    SuccessResponseSerializer,
    VerifyLoginCodeSerializer,
)
from ..services import OtpLoginEmailService, RegistrationService
from ..services import otp, turnstile

logger = logging.getLogger(__name__)


class SendLoginCodeView(APIView):
    """
    Send an email verification code for passwordless login.

    Validates a Cloudflare Turnstile token and applies send rate limits
    before issuing a short-lived code.
    """
    permission_classes = [AllowAny]

    @extend_schema(
        tags=['auth'],
        summary=_("Send login verification code"),
        request=SendLoginCodeSerializer,
        responses={200: SuccessResponseSerializer},
    )
    def post(self, request):
        """Issue and email a login verification code."""
        serializer = SendLoginCodeSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        email = serializer.validated_data['email']
        token = serializer.validated_data.get('turnstile_token', '')
        language = request.data.get('language', 'en-US')
        client_ip = turnstile.get_client_ip(request.META)

        ok, errors = turnstile.verify_token(token, client_ip)
        if not ok:
            logger.warning(
                "Login code blocked by Turnstile - Email: %s, Errors: %s",
                email,
                errors,
            )
            return Response(
                {
                    'success': False,
                    'error_code': 'TURNSTILE_FAILED',
                    'message': _('Human verification failed.'),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        allowed, reason = otp.can_send(email, client_ip)
        if not allowed:
            logger.info(
                "Login code rate-limited - Email: %s, Reason: %s",
                email,
                reason,
            )
            return Response(
                {
                    'success': False,
                    'error_code': 'RATE_LIMITED',
                    'message': _(
                        'Too many requests. Please try again later.'
                    ),
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        code = otp.generate_code()
        otp.store_code(email, code)
        OtpLoginEmailService.send_login_code_email(email, code, language)

        return Response(
            {
                'success': True,
                'message': _('Verification code sent.'),
            },
            status=status.HTTP_200_OK,
        )


class VerifyLoginCodeView(APIView):
    """
    Verify an email login code and return JWT tokens.

    On success an existing user is logged in or a new passwordless user
    is auto-provisioned.
    """
    permission_classes = [AllowAny]

    _INVALID_CODE_MESSAGE = _('The verification code is incorrect.')
    _ERROR_MESSAGES = {
        'expired': _('The verification code has expired.'),
        'too_many_attempts': _(
            'Too many incorrect attempts. Request a new code.'
        ),
        'invalid': _INVALID_CODE_MESSAGE,
    }

    @extend_schema(
        tags=['auth'],
        summary=_("Verify login code"),
        request=VerifyLoginCodeSerializer,
        responses={200: AuthTokenResponseSerializer},
    )
    def post(self, request):
        """Verify the code and issue JWT tokens."""
        serializer = VerifyLoginCodeSerializer(data=request.data)
        if not serializer.is_valid():
            response_data = {
                'success': False,
                'errors': serializer.errors,
            }
            if 'code' in serializer.errors:
                response_data.update(
                    {
                        'error_code': 'INVALID',
                        'message': self._INVALID_CODE_MESSAGE,
                    }
                )
            return Response(
                response_data,
                status=status.HTTP_400_BAD_REQUEST,
            )

        email = serializer.validated_data['email']
        code = serializer.validated_data['code']
        language = request.data.get('language', DEFAULT_ANSWER_LANGUAGE)
        timezone_str = request.data.get('timezone', 'Asia/Shanghai')

        ok, reason = otp.verify_code(email, code)
        if not ok:
            return Response(
                {
                    'success': False,
                    'error_code': {
                        'invalid': 'INVALID',
                        'expired': 'EXPIRED',
                        'too_many_attempts': 'TOO_MANY_ATTEMPTS',
                    }.get(reason, 'INVALID'),
                    'message': self._ERROR_MESSAGES.get(
                        reason, _('Verification failed.')
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = RegistrationService.get_or_create_otp_user(
            email=email,
            language=language,
            timezone_str=timezone_str,
        )
        refresh = RefreshToken.for_user(user)

        logger.info(
            "OTP login success - User: %s (ID: %s)",
            user.username,
            user.id,
        )

        return Response(
            {
                'success': True,
                'access': str(refresh.access_token),
                'refresh': str(refresh),
                'user': {
                    'id': user.id,
                    'email': user.email,
                    'username': user.username,
                },
            },
            status=status.HTTP_200_OK,
        )
