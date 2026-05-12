import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


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

    if not announcement.notify_email:
        return

    if announcement.company_id:
        send_bulk_email.delay(announcement_id)
        return

    recipients = list(User.objects.filter(is_active=True, is_email_verified=True).values_list('email', flat=True))
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


@shared_task
def notify_announcement_subscribers(announcement_id):
    """
    Async in-app notification fan-out for a new announcement.
    Runs as a Celery task so the API response is not blocked by per-user DB writes.
    """
    from apps.notifications.utils import create_notification
    from apps.users.models import User
    from .models import Announcement

    try:
        announcement = Announcement.objects.get(pk=announcement_id)
    except Announcement.DoesNotExist:
        logger.warning('notify_announcement_subscribers: Announcement id=%s not found', announcement_id)
        return

    author_id = announcement.author_id
    headline = (announcement.title or '').strip()
    preview = (announcement.body or '').strip()
    if preview:
        preview = preview[:500]
    parts = []
    if headline:
        parts.append(headline)
    if preview:
        parts.append(preview)
    body_text = '\n\n'.join(parts) if parts else 'Откройте раздел объявлений.'

    if announcement.company_id:
        qs = User.objects.filter(
            company_id=announcement.company_id,
            is_active=True,
        ).exclude(pk=author_id)
    else:
        qs = User.objects.filter(
            is_active=True,
            role__in=['superadmin', 'company_admin', 'employee', 'reception'],
        ).exclude(pk=author_id)

    for recipient in qs.iterator(chunk_size=100):
        create_notification(
            user=recipient,
            notification_type='announcement',
            title='Новое объявление',
            message=body_text,
            link='/announcements/',
        )

    logger.info(
        'notify_announcement_subscribers: sent in-app notifications for announcement_id=%s',
        announcement_id,
    )
