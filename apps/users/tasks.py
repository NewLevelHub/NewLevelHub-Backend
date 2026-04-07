import logging
from datetime import timedelta
from urllib.parse import urlencode

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils import timezone

from .models import EmailVerificationToken, User

logger = logging.getLogger(__name__)


def create_email_verification_token(user, invalidate_existing=False):
    """
    Create a fresh verification token with a 24h TTL.
    Optionally invalidate all active tokens for this user.
    """
    if invalidate_existing:
        EmailVerificationToken.objects.filter(user=user, is_used=False).update(is_used=True)
    return EmailVerificationToken.objects.create(
        user=user,
        expires_at=timezone.now() + timedelta(hours=24),
    )


@shared_task
def send_verification_email(user_id, token=None):
    """Send email verification link."""
    user = User.objects.filter(id=user_id).first()
    if not user:
        return

    token_value = str(token) if token else str(create_email_verification_token(user).token)
    verify_link = f"{settings.FRONTEND_URL.rstrip('/')}/verify-email?{urlencode({'token': token_value})}"

    send_mail(
        subject='Verify your email',
        message=(
            f'Hi {user.full_name},\n\n'
            'Please verify your email by opening this link:\n'
            f'{verify_link}\n\n'
            'This link expires in 24 hours.'
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_password_reset_email(self, token_id):
    """Отправка ссылки для сброса пароля."""
    from .models import PasswordResetToken

    try:
        reset_token = PasswordResetToken.objects.select_related('user').get(id=token_id)
    except PasswordResetToken.DoesNotExist:
        logger.warning('send_password_reset_email: PasswordResetToken id=%s not found', token_id)
        return

    user = reset_token.user
    reset_url = f"{settings.FRONTEND_URL}/reset-password?token={reset_token.token}"

    html_message = render_to_string(
        'emails/password_reset.html',
        {'user': user, 'reset_url': reset_url},
    )
    plain_message = (
        f"Hi {user.first_name},\n\n"
        f"Reset your password by visiting the link below:\n{reset_url}\n\n"
        "This link expires in 1 hour.\n\n"
        "If you did not request a password reset, you can safely ignore this email."
    )

    try:
        send_mail(
            subject='Reset your New Level Hub password',
            message=plain_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            html_message=html_message,
            fail_silently=False,
        )
        logger.info(
            'send_password_reset_email: successfully sent to %s (token_id=%s)',
            user.email,
            token_id,
        )
    except Exception as exc:
        logger.error(
            'send_password_reset_email: failed to send email to %s (token_id=%s): %s',
            user.email,
            token_id,
            exc,
        )
        raise self.retry(exc=exc)
