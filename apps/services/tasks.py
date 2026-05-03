import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


@shared_task
def notify_service_request_status_change(request_id):
    """Notify request creator about status changes (e.g. called from async pipelines)."""
    from apps.services.models import ServiceRequest
    from apps.notifications.utils import create_notification

    try:
        sr = ServiceRequest.objects.select_related('created_by').get(pk=request_id)
    except ServiceRequest.DoesNotExist:
        return

    if not sr.created_by_id:
        return

    create_notification(
        user=sr.created_by,
        notification_type='service_request_update',
        title='Обновление заявки на сервис',
        message=f'Статус: {sr.get_status_display()}',
        link='/service-requests/',
    )


@shared_task
def send_announcement_emails(announcement_id):
    """
    Email for important (pinned) announcements when notify_email=True.

    - Company-scoped: delegates to notifications.send_bulk_email (templates, prefs).
    - Building-wide (company=null): send_bulk_email skips these; fan out via SMTP here.
    """
    from apps.notifications.tasks import send_bulk_email
    from apps.users.models import User
    from .models import Announcement

    try:
        announcement = Announcement.objects.select_related('company', 'author').get(pk=announcement_id)
    except Announcement.DoesNotExist:
        logger.warning('send_announcement_emails: Announcement id=%s not found', announcement_id)
        return

    if not announcement.notify_email or not announcement.is_pinned:
        return

    if announcement.company_id:
        send_bulk_email.delay(announcement_id)
        return

    recipients = list(User.objects.filter(is_active=True).values_list('email', flat=True))
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
