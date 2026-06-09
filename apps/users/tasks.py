import logging
from datetime import timedelta
from urllib.parse import urlencode

from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
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
def notify_new_employee(user_id):
    """Notify company admins when a new employee joins via invite."""
    from apps.notifications.tasks import send_notification_email
    from apps.notifications.utils import create_notification

    UserModel = get_user_model()
    try:
        user = UserModel.objects.select_related('company').get(pk=user_id)
    except UserModel.DoesNotExist:
        return

    company = user.company
    if not company:
        return

    title = f'{user.full_name} присоединился к компании'

    admins = UserModel.objects.filter(company=company, role='company_admin', is_active=True)
    for admin in admins:
        create_notification(
            user=admin,
            notification_type='new_employee',
            title=title,
            message=f'Новый сотрудник {user.full_name} зарегистрировался по приглашению.',
            link='/team/manage',
        )
        if admin.is_email_verified:
            send_notification_email.delay(
                admin.pk,
                'new_employee',
                {
                    'subject': title,
                    'employee_name': user.full_name,
                    'employee_email': user.email,
                    'action_url': '/team/manage',
                },
            )


@shared_task
def send_verification_email(user_id, token=None):
    """Send email verification link."""
    user = User.objects.filter(id=user_id).first()
    if not user:
        return

    token_value = str(token) if token else str(create_email_verification_token(user).token)
    verify_link = f"{settings.FRONTEND_URL.rstrip('/')}/verify-email?{urlencode({'token': token_value})}"

    html_message = render_to_string('emails/email_verification.html', {
        'recipient_name': user.full_name,
        'verify_link': verify_link,
    })
    send_mail(
        subject='Подтвердите ваш email для New Level Hub',
        message=(
            f'Здравствуйте, {user.full_name},\n\n'
            'Пожалуйста, подтвердите ваш email, перейдя по следующей ссылке:\n'
            f'{verify_link}\n\n'
            'Эта ссылка действительна в течение 24 часов.'
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        html_message=html_message,
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
        f"Здравствуйте, {user.first_name},\n\n"
        f"Сбросьте ваш пароль, перейдя по следующей ссылке:\n{reset_url}\n\n"
        "Эта ссылка действительна в течение 1 часа.\n\n"
        "Если вы не запрашивали сброс пароля, вы можете безопасно игнорировать это письмо."
    )

    try:
        send_mail(
            subject='Сбросьте ваш пароль для New Level Hub',
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
