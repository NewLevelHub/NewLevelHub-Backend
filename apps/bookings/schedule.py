"""Busy intervals for resources (confirmed bookings + admin blocks)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.utils import timezone

from .models import Booking, ResourceBlock


def is_soon_available(end_time, now) -> bool:
    """Return True when end_time is in the future but within SOON_AVAILABLE_MINUTES."""
    threshold = timedelta(minutes=settings.SOON_AVAILABLE_MINUTES)
    return now < end_time <= now + threshold


def get_schedule_status(booking, now) -> str:
    """Return 'soon_available' or 'occupied' for a single booking slot."""
    if is_soon_available(booking.end_time, now):
        return 'soon_available'
    return 'occupied'


def local_today() -> date:
    return timezone.localdate()


def day_range_aware(d: date) -> tuple[datetime, datetime]:
    """Half-open [day 00:00, next day 00:00) in current timezone."""
    start = timezone.make_aware(datetime.combine(d, time.min))
    end = start + timedelta(days=1)
    return start, end


def seven_day_range_from_today() -> tuple[datetime, datetime]:
    """Seven calendar days starting today (local): [today 00:00, today+7 00:00)."""
    start_d = local_today()
    start, _ = day_range_aware(start_d)
    end, _ = day_range_aware(start_d + timedelta(days=7))
    return start, end


def week_range_for_date(anchor: date) -> tuple[datetime, datetime]:
    """ISO-style week: Monday 00:00 .. next Monday 00:00 (local), containing anchor."""
    monday = anchor - timedelta(days=anchor.weekday())
    start, _ = day_range_aware(monday)
    end, _ = day_range_aware(monday + timedelta(days=7))
    return start, end


def busy_slots_for_resource(resource_id: int, range_start, range_end) -> list[dict]:
    """
    Intersections with [range_start, range_end).
    Each item: start, end (datetime), booking_id (int|None), user_name (str|None).
    Blocks use booking_id=None and user_name=None.
    """
    bookings = (
        Booking.objects.filter(
            resource_id=resource_id,
            status='confirmed',
            start_time__lt=range_end,
            end_time__gt=range_start,
        )
        .select_related('user')
        .order_by('start_time')
    )
    blocks = (
        ResourceBlock.objects.filter(
            resource_id=resource_id,
            start_time__lt=range_end,
            end_time__gt=range_start,
        )
        .order_by('start_time')
    )

    out: list[dict] = []
    for b in bookings:
        out.append(
            {
                'start': b.start_time,
                'end': b.end_time,
                'booking_id': b.id,
                'user_name': b.user.full_name if b.user_id else None,
            }
        )
    for bl in blocks:
        out.append(
            {
                'start': bl.start_time,
                'end': bl.end_time,
                'booking_id': None,
                'user_name': None,
            }
        )
    out.sort(key=lambda x: x['start'])
    return out
