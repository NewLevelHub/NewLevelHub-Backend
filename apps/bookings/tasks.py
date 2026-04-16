import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task
def send_booking_reminders():
    """
    Beat task (every 5 min): send reminders for bookings starting in
    REMINDER_MINUTES_BEFORE minutes. Window: [now+10min, now+17.5min] to
    cover bookings created within the same beat cycle as the reminder window.
    """
    from apps.bookings.models import Booking
    from apps.notifications.models import Notification, NotificationPreference

    now = timezone.now()
    reminder_minutes = settings.REMINDER_MINUTES_BEFORE
    BEAT_INTERVAL_MINUTES = 5
    window_start = now + timedelta(minutes=reminder_minutes - BEAT_INTERVAL_MINUTES)
    window_end = now + timedelta(minutes=reminder_minutes) + timedelta(minutes=2.5)

    bookings = Booking.objects.filter(
        status='confirmed',
        reminder_sent=False,
        start_time__gte=window_start,
        start_time__lte=window_end,
    ).select_related('user', 'resource')

    sent_count = 0

    for booking in bookings:
        user = booking.user
        resource_name = booking.resource.name
        start_local = timezone.localtime(booking.start_time)

        # Create in-app notification
        Notification.objects.create(
            user=user,
            notification_type='booking_reminder',
            title=f'Напоминание: {resource_name}',
            body=(
                f'Ваше бронирование начинается через {reminder_minutes} минут '
                f'({start_local:%H:%M}).'
            ),
            url='',
        )

        # Send email if user has preferences with booking_email=True (or no preferences yet)
        try:
            prefs = user.notification_preferences
            email_enabled = prefs.booking_email and not prefs.do_not_disturb
        except NotificationPreference.DoesNotExist:
            email_enabled = True  # default: send email when no prefs row exists

        if email_enabled and user.email:
            try:
                send_mail(
                    subject=f'Напоминание о бронировании: {resource_name}',
                    message=(
                        f'Здравствуйте, {user.full_name}!\n\n'
                        f'Ваше бронирование ({resource_name}) начинается через '
                        f'{reminder_minutes} минут — в {start_local:%H:%M}.\n\n'
                        f'С уважением,\nNewLevelHub'
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[user.email],
                    fail_silently=True,
                )
            except Exception:
                logger.exception('Failed to send reminder email for booking %s', booking.id)

        # Mark as sent
        booking.reminder_sent = True
        booking.save(update_fields=['reminder_sent'])

        logger.info('Reminder sent for booking %s to user %s', booking.id, booking.user_id)
        sent_count += 1

    return sent_count


@shared_task
def auto_complete_bookings():
    """
    Beat task (every 5 min): auto-complete confirmed bookings whose end_time is in the past.
    """
    from apps.bookings.models import Booking

    now = timezone.now()
    bookings = Booking.objects.filter(status='confirmed', end_time__lt=now)

    completed_count = 0

    for booking in bookings:
        booking.status = 'completed'
        booking.save(update_fields=['status', 'updated_at'])
        logger.info('Auto-completed booking %s', booking.id)
        completed_count += 1

    return completed_count


@shared_task
def send_booking_reminder(booking_id):
    """Напоминание за N мин до начала бронирования (one-off, legacy stub)."""
    # TODO: получить Booking, отправить уведомление пользователю и участникам
    pass


@shared_task
def mark_no_show_bookings():
    """
    Beat task (every 5 min): mark meeting_room bookings as no_show if
    started >NO_SHOW_MINUTES ago with no check-in.
    Frees up the resource by moving the booking out of 'confirmed' status.
    """
    from apps.bookings.models import Booking

    threshold = timezone.now() - timedelta(minutes=settings.NO_SHOW_MINUTES)
    bookings = Booking.objects.filter(
        resource__resource_type='meeting_room',
        status='confirmed',
        start_time__lte=threshold,
        checked_in_at__isnull=True,
    )
    count = bookings.update(status='no_show', updated_at=timezone.now())
    logger.info('[no_show] Marked %d bookings as no_show', count)
    return count


@shared_task
def auto_cancel_no_show():
    """Автоотмена бронирований конференц-залов, если no-show > 15 мин. (legacy stub — use mark_no_show_bookings)"""
    return mark_no_show_bookings()


@shared_task
def generate_recurring_bookings():
    """Периодическая задача: создание Booking из RecurringBooking шаблонов."""
    # TODO: для каждого активного RecurringBooking создать реальный Booking
    #       на ближайшую подходящую дату, если ещё не создан
    pass
