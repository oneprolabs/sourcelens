from unittest.mock import patch

from django.core import mail
from django.test import SimpleTestCase, override_settings

from accounts.services.email import (
    OtpLoginEmailService,
    PasswordResetEmailService,
    get_email_delivery_options,
)


class EmailDeliveryOptionsTests(SimpleTestCase):
    @override_settings(
        EMAIL_HOST_USER='env-user@example.com',
        DEFAULT_FROM_EMAIL='noreply@example.com',
    )
    def test_uses_email_host_user_when_configured(self):
        from_email, connection = get_email_delivery_options()

        assert from_email == 'env-user@example.com'
        assert connection is None

    @override_settings(
        EMAIL_HOST_USER='',
        DEFAULT_FROM_EMAIL='noreply@example.com',
    )
    def test_uses_default_from_email_when_host_user_is_empty(self):
        from_email, connection = get_email_delivery_options()

        assert from_email == 'noreply@example.com'
        assert connection is None


@override_settings(
    DEFAULT_FROM_EMAIL='noreply@example.com',
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    EMAIL_HOST_USER='',
    FRONTEND_URL='http://localhost:8000',
    PASSWORD_RESET_TIMEOUT=86400,
)
class PasswordResetEmailServiceTests(SimpleTestCase):
    def setUp(self):
        mail.outbox.clear()

    @patch('accounts.services.email.EmailMultiAlternatives.send', return_value=0)
    def test_zero_accepted_messages_is_a_delivery_failure(self, send):
        """Do not claim administrative recovery mail was accepted when the backend sent zero."""
        sent = PasswordResetEmailService.send_password_reset_email(
            email='person@example.com', uid='uid-value', token='token-value'
        )
        assert sent is False

    def test_renders_and_sends_english_password_reset_email(self):
        sent = PasswordResetEmailService.send_password_reset_email(
            email='person@example.com',
            uid='uid-value',
            token='token-value',
            language='en-US',
        )

        assert sent is True
        assert len(mail.outbox) == 1
        message = mail.outbox[0]
        reset_url = (
            'http://localhost:8000/reset-password/'
            'uid-value/token-value/'
        )
        assert reset_url in message.body
        assert reset_url in message.alternatives[0][0]
        assert 'Set or reset password' in message.alternatives[0][0]

    def test_renders_and_sends_chinese_password_reset_email(self):
        sent = PasswordResetEmailService.send_password_reset_email(
            email='person@example.com',
            uid='uid-value',
            token='token-value',
            language='zh-CN',
        )

        assert sent is True
        assert len(mail.outbox) == 1
        html = mail.outbox[0].alternatives[0][0]
        assert '设置或重置密码' in html
        assert '24 小时后失效' in html

    def test_renders_and_sends_spanish_password_reset_email(self):
        sent = PasswordResetEmailService.send_password_reset_email(
            email='person@example.com',
            uid='uid-value',
            token='token-value',
            language='es',
        )

        assert sent is True
        assert len(mail.outbox) == 1
        html = mail.outbox[0].alternatives[0][0]
        assert 'Configura o restablece tu contraseña' in html
        assert 'Este enlace caduca en 24 horas' in html

    def test_unknown_language_uses_english_template(self):
        sent = PasswordResetEmailService.send_password_reset_email(
            email='person@example.com',
            uid='uid-value',
            token='token-value',
            language='fr-FR',
        )

        assert sent is True
        assert 'Set or reset password' in mail.outbox[0].alternatives[0][0]


@override_settings(
    DEFAULT_FROM_EMAIL='noreply@example.com',
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    EMAIL_HOST_USER='',
    FRONTEND_URL='http://localhost:8000',
    OTP_CODE_TTL_SECONDS=300,
)
class VerificationCodeEmailStyleTests(SimpleTestCase):
    def setUp(self):
        mail.outbox.clear()

    def test_login_code_email_visual_style(self):
        login_sent = OtpLoginEmailService.send_login_code_email(
            email='person@example.com',
            code='123456',
            language='en-US',
        )

        assert login_sent is True
        login_html = mail.outbox[0].alternatives[0][0]
        shared_style = (
            'background-color:#f1f5f9',
            'max-width:520px',
            'background-color:#1c319f',
            'border:2px dashed #bfdbfe',
        )
        assert all(marker in login_html for marker in shared_style)
        assert 'Your verification code' in login_html
        assert '123456' in login_html

    def test_login_code_email_supports_spanish(self):
        sent = OtpLoginEmailService.send_login_code_email(
            email='person@example.com',
            code='123456',
            language='es',
        )

        assert sent is True
        html = mail.outbox[0].alternatives[0][0]
        assert 'Tu código de verificación' in html
        assert 'Caduca en 5 minutos' in html
