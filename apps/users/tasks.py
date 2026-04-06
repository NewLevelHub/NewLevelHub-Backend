from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone


@shared_task
def send_verification_email(user_id):
    """Invalidate old tokens, create a fresh one, and send the verification link."""
    from .models import User, EmailVerificationToken

    user = User.objects.filter(id=user_id).first()
    if not user or user.is_email_verified:
        return

    EmailVerificationToken.objects.filter(user=user, is_used=False).update(is_used=True)

    token = EmailVerificationToken.objects.create(
        user=user,
        expires_at=timezone.now() + timedelta(hours=24),
    )

    verification_url = f"{settings.FRONTEND_URL}/verify-email?token={token.token}"

    send_mail(
        subject='Подтвердите ваш email — New Level Hub',
        message=f'Перейдите по ссылке для подтверждения: {verification_url}',
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        html_message=(
            f'<p>Здравствуйте, {user.first_name}!</p>'
            f'<p>Для подтверждения вашего email перейдите по ссылке:</p>'
            f'<p><a href="{verification_url}">Подтвердить email</a></p>'
            f'<p>Ссылка действительна 24 часа.</p>'
            f'<p>Если вы не регистрировались на New Level Hub, проигнорируйте это письмо.</p>'
        ),
    )


@shared_task
def send_password_reset_email(user_id):
    """Отправка ссылки для сброса пароля."""
    # TODO: создать PasswordResetToken, сформировать ссылку, отправить email
    pass
