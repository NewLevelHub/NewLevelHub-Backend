from datetime import datetime, timedelta

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from .models import Booking, RecurringBooking, Resource
from .serializers import BookingCreateSerializer


@shared_task
def send_booking_reminder(booking_id):
    """Напоминание за 15 мин до начала бронирования."""
    # TODO: получить Booking, отправить уведомление пользователю и участникам
    pass


@shared_task
def auto_cancel_no_show():
    """Автоотмена бронирований конференц-залов, если no-show > 15 мин."""
    # TODO: найти confirmed bookings meeting_room, start_time + 15min < now, отменить
    pass


@shared_task
def complete_expired_bookings():
    """Автозавершение бронирований по окончании времени."""
    # TODO: Booking.objects.filter(status='confirmed', end_time__lt=now) → status='completed'
    pass


@shared_task
def generate_recurring_bookings():
    """Периодическая задача: создание Booking из RecurringBooking шаблонов."""
    today = timezone.localdate()
    for recurring_booking in (
        RecurringBooking.objects
        .filter(is_active=True, valid_until__isnull=False, valid_until__gte=today)
        .select_related('resource', 'user', 'company')
    ):
        next_period_end = recurring_booking.valid_until + timedelta(days=7)
        create_bookings_for_recurring(
            recurring_booking,
            start_date=recurring_booking.valid_until + timedelta(days=1),
            end_date=next_period_end,
        )
        recurring_booking.valid_until = next_period_end
        recurring_booking.save(update_fields=['valid_until', 'updated_at'])


def _matching_dates(*, day_of_week, start_date, end_date):
    days_until_first = (day_of_week - start_date.weekday()) % 7
    current = start_date + timedelta(days=days_until_first)
    while current <= end_date:
        yield current
        current += timedelta(days=7)


def _combine_aware(target_date, target_time):
    naive = datetime.combine(target_date, target_time)
    return timezone.make_aware(naive, timezone.get_current_timezone())


def create_bookings_for_recurring(recurring_booking, *, start_date, end_date):
    skipped_dates = []
    if start_date > end_date:
        return skipped_dates

    validator = BookingCreateSerializer(context={})

    with transaction.atomic():
        resource = Resource.objects.select_for_update().get(pk=recurring_booking.resource_id)
        for booking_date in _matching_dates(
            day_of_week=recurring_booking.day_of_week,
            start_date=start_date,
            end_date=end_date,
        ):
            start_time = _combine_aware(booking_date, recurring_booking.start_time)
            end_time = _combine_aware(booking_date, recurring_booking.end_time)
            try:
                validator._ensure_no_conflicts(
                    resource=resource,
                    start_time=start_time,
                    end_time=end_time,
                )
            except BookingCreateSerializer.BookingConflictException:
                skipped_dates.append(booking_date.isoformat())
                continue

            Booking.objects.get_or_create(
                resource=resource,
                user=recurring_booking.user,
                company=recurring_booking.company,
                recurring_booking=recurring_booking,
                start_time=start_time,
                end_time=end_time,
                defaults={'status': 'confirmed', 'description': ''},
            )

    return skipped_dates
