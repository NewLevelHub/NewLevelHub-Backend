import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


@shared_task
def notify_service_request_status_change(request_id):
    """Уведомить пользователя о смене статуса заявки."""
    pass


@shared_task
def send_announcement_emails(announcement_id):
    """
    Рассылка email по объявлению (is_pinned=True, notify_email=True).

    Получатели:
      - building-wide (company=null): все активные пользователи
      - company-scoped: все активные члены компании
    """
    from apps.users.models import User
    from .models import Announcement

    try:
        announcement = Announcement.objects.select_related('company', 'author').get(
            id=announcement_id
        )
    except Announcement.DoesNotExist:
        logger.warning('send_announcement_emails: Announcement id=%s not found', announcement_id)
        return

    if announcement.company_id:
        recipients = list(
            User.objects.filter(company_id=announcement.company_id, is_active=True)
            .values_list('email', flat=True)
        )
    else:
        recipients = list(
            User.objects.filter(is_active=True).values_list('email', flat=True)
        )

    if not recipients:
        return

    subject = f'[NewLevelHub] {announcement.title}'
    message = (
        f'{announcement.title}\n\n'
        f'{announcement.body}\n\n'
        f'— {announcement.author.full_name if announcement.author else "NewLevelHub"}'
    )

    for email in recipients:
        try:
            send_mail(
                subject=subject,
                message=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                fail_silently=True,
            )
        except Exception as exc:
            logger.error(
                'send_announcement_emails: failed to send to %s (announcement_id=%s): %s',
                email, announcement_id, exc,
            )

    logger.info(
        'send_announcement_emails: sent to %d recipients for announcement_id=%s',
        len(recipients), announcement_id,
    )
