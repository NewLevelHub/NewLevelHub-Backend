"""
TDD tests for booking Celery beat tasks:
  - send_booking_reminders
  - auto_complete_bookings
"""
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.bookings.models import Booking, Resource
from apps.companies.models import Company
from apps.notifications.models import Notification
from apps.users.models import User


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def company(db):
    return Company.objects.create(name='Task Test Co', plan='basic')


@pytest.fixture
def user(db, company):
    return User.objects.create_user(
        email='taskuser@test.com',
        password='pass',
        first_name='Task',
        last_name='User',
        role='employee',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def resource(db):
    return Resource.objects.create(
        name='Task Desk',
        resource_type='desk',
        available_days=list(range(7)),
        available_from='00:00',
        available_until='23:59',
    )


def _make_booking(user, resource, start_offset_minutes, duration_minutes=60, status='confirmed'):
    """Helper: create a booking starting `start_offset_minutes` from now."""
    now = timezone.now()
    start = now + timedelta(minutes=start_offset_minutes)
    end = start + timedelta(minutes=duration_minutes)
    return Booking.objects.create(
        resource=resource,
        user=user,
        company=user.company,
        start_time=start,
        end_time=end,
        status=status,
    )


# ---------------------------------------------------------------------------
# send_booking_reminders tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_reminder_sent_for_booking_starting_in_15_minutes(user, resource):
    """Booking starting in exactly 15 min → Notification created, reminder_sent set True."""
    from apps.bookings.tasks import send_booking_reminders

    booking = _make_booking(user, resource, start_offset_minutes=15)
    assert not booking.reminder_sent

    send_booking_reminders()

    booking.refresh_from_db()
    assert booking.reminder_sent is True
    assert Notification.objects.filter(
        user=user,
        notification_type='booking_reminder',
    ).count() == 1


@pytest.mark.django_db
def test_reminder_sent_for_booking_within_window(user, resource):
    """Booking starting in 14 min (within ±2.5 min window of 15) → Notification created."""
    from apps.bookings.tasks import send_booking_reminders

    booking = _make_booking(user, resource, start_offset_minutes=14)

    send_booking_reminders()

    booking.refresh_from_db()
    assert booking.reminder_sent is True
    assert Notification.objects.filter(user=user, notification_type='booking_reminder').exists()


@pytest.mark.django_db
def test_reminder_not_sent_for_booking_starting_in_30_minutes(user, resource):
    """Booking starting in 30 min → outside window, no Notification created."""
    from apps.bookings.tasks import send_booking_reminders

    booking = _make_booking(user, resource, start_offset_minutes=30)

    send_booking_reminders()

    booking.refresh_from_db()
    assert booking.reminder_sent is False
    assert not Notification.objects.filter(user=user, notification_type='booking_reminder').exists()


@pytest.mark.django_db
def test_reminder_not_sent_twice(user, resource):
    """Booking already has reminder_sent=True → no duplicate Notification created."""
    from apps.bookings.tasks import send_booking_reminders

    booking = _make_booking(user, resource, start_offset_minutes=15)
    booking.reminder_sent = True
    booking.save()

    send_booking_reminders()

    # Should still be True but no new notification
    assert not Notification.objects.filter(user=user, notification_type='booking_reminder').exists()


@pytest.mark.django_db
def test_reminder_not_sent_for_cancelled_booking(user, resource):
    """Cancelled booking within the time window → no Notification."""
    from apps.bookings.tasks import send_booking_reminders

    _make_booking(user, resource, start_offset_minutes=15, status='cancelled')

    send_booking_reminders()

    assert not Notification.objects.filter(user=user, notification_type='booking_reminder').exists()


@pytest.mark.django_db
def test_reminder_not_sent_for_completed_booking(user, resource):
    """Completed booking within the time window → no Notification."""
    from apps.bookings.tasks import send_booking_reminders

    _make_booking(user, resource, start_offset_minutes=15, status='completed')

    send_booking_reminders()

    assert not Notification.objects.filter(user=user, notification_type='booking_reminder').exists()


@pytest.mark.django_db
def test_reminder_sets_reminder_sent_flag(user, resource):
    """After task runs, reminder_sent becomes True on the booking."""
    from apps.bookings.tasks import send_booking_reminders

    booking = _make_booking(user, resource, start_offset_minutes=15)
    assert booking.reminder_sent is False

    send_booking_reminders()

    booking.refresh_from_db()
    assert booking.reminder_sent is True


@pytest.mark.django_db
def test_reminder_notification_has_correct_type(user, resource):
    """Created notification uses the 'booking_reminder' type."""
    from apps.bookings.tasks import send_booking_reminders

    _make_booking(user, resource, start_offset_minutes=15)

    send_booking_reminders()

    notif = Notification.objects.get(user=user, notification_type='booking_reminder')
    assert notif.notification_type == 'booking_reminder'
    assert notif.user == user


@pytest.mark.django_db
def test_reminder_respects_custom_reminder_minutes(user, resource):
    """REMINDER_MINUTES_BEFORE=30 → booking at +30 min gets reminder."""
    from apps.bookings.tasks import send_booking_reminders

    booking = _make_booking(user, resource, start_offset_minutes=30)

    with patch('apps.bookings.tasks.settings') as mock_settings:
        mock_settings.REMINDER_MINUTES_BEFORE = 30
        send_booking_reminders()

    booking.refresh_from_db()
    assert booking.reminder_sent is True
    assert Notification.objects.filter(user=user, notification_type='booking_reminder').exists()


# ---------------------------------------------------------------------------
# auto_complete_bookings tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_auto_complete_past_confirmed_booking(user, resource):
    """Confirmed booking with end_time in the past → status becomes 'completed'."""
    from apps.bookings.tasks import auto_complete_bookings

    booking = _make_booking(user, resource, start_offset_minutes=-120, duration_minutes=60)
    # end_time = now - 60 min → past
    assert booking.status == 'confirmed'

    auto_complete_bookings()

    booking.refresh_from_db()
    assert booking.status == 'completed'


@pytest.mark.django_db
def test_auto_complete_does_not_touch_future_booking(user, resource):
    """Confirmed booking with end_time in the future → status unchanged."""
    from apps.bookings.tasks import auto_complete_bookings

    booking = _make_booking(user, resource, start_offset_minutes=10, duration_minutes=60)
    # end_time = now + 70 min → future

    auto_complete_bookings()

    booking.refresh_from_db()
    assert booking.status == 'confirmed'


@pytest.mark.django_db
def test_auto_complete_does_not_touch_cancelled_booking(user, resource):
    """Cancelled booking with past end_time → status stays 'cancelled'."""
    from apps.bookings.tasks import auto_complete_bookings

    booking = _make_booking(user, resource, start_offset_minutes=-120, duration_minutes=60, status='cancelled')

    auto_complete_bookings()

    booking.refresh_from_db()
    assert booking.status == 'cancelled'


@pytest.mark.django_db
def test_auto_complete_does_not_touch_already_completed_booking(user, resource):
    """Already completed booking → no double-save / status unchanged."""
    from apps.bookings.tasks import auto_complete_bookings

    booking = _make_booking(user, resource, start_offset_minutes=-120, duration_minutes=60, status='completed')

    auto_complete_bookings()

    booking.refresh_from_db()
    assert booking.status == 'completed'


@pytest.mark.django_db
def test_auto_complete_handles_multiple_bookings(user, resource):
    """Multiple past confirmed bookings → all become 'completed'."""
    from apps.bookings.tasks import auto_complete_bookings

    b1 = _make_booking(user, resource, start_offset_minutes=-180, duration_minutes=30)
    b2 = _make_booking(user, resource, start_offset_minutes=-120, duration_minutes=30)
    b3 = _make_booking(user, resource, start_offset_minutes=-60, duration_minutes=30)

    auto_complete_bookings()

    for b in [b1, b2, b3]:
        b.refresh_from_db()
        assert b.status == 'completed'


@pytest.mark.django_db
def test_auto_complete_only_affects_past_confirmed(user, resource):
    """Mix of past and future, confirmed and cancelled → only past confirmed → completed."""
    from apps.bookings.tasks import auto_complete_bookings

    past_confirmed = _make_booking(user, resource, start_offset_minutes=-120, duration_minutes=30)
    future_confirmed = _make_booking(user, resource, start_offset_minutes=60, duration_minutes=30)
    past_cancelled = _make_booking(
        user, resource, start_offset_minutes=-90, duration_minutes=30, status='cancelled'
    )

    auto_complete_bookings()

    past_confirmed.refresh_from_db()
    future_confirmed.refresh_from_db()
    past_cancelled.refresh_from_db()

    assert past_confirmed.status == 'completed'
    assert future_confirmed.status == 'confirmed'
    assert past_cancelled.status == 'cancelled'


# ---------------------------------------------------------------------------
# auto_complete_bookings — notification tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_auto_complete_creates_inapp_notification(user, resource):
    """Auto-completing a booking creates a booking_completed in-app notification."""
    from apps.bookings.tasks import auto_complete_bookings

    _make_booking(user, resource, start_offset_minutes=-120, duration_minutes=60)

    with patch('apps.notifications.tasks.send_notification_email.delay'):
        auto_complete_bookings()

    assert Notification.objects.filter(
        user=user,
        notification_type='booking_completed',
    ).count() == 1


@pytest.mark.django_db
def test_auto_complete_suppresses_notification_when_dnd(user, resource):
    """No in-app notification is created when DND is active for the user."""
    from apps.bookings.tasks import auto_complete_bookings
    from apps.notifications.models import NotificationPreference

    NotificationPreference.objects.update_or_create(
        user=user,
        defaults={'do_not_disturb': True},
    )
    _make_booking(user, resource, start_offset_minutes=-120, duration_minutes=60)

    with patch('apps.notifications.tasks.send_notification_email.delay'):
        auto_complete_bookings()

    assert not Notification.objects.filter(
        user=user,
        notification_type='booking_completed',
    ).exists()


@pytest.mark.django_db
def test_auto_complete_sends_email_when_preference_enabled(user, resource):
    """Email task is queued when booking_completed_email=True."""
    from apps.bookings.tasks import auto_complete_bookings
    from apps.notifications.models import NotificationPreference

    NotificationPreference.objects.update_or_create(
        user=user,
        defaults={'booking_completed_email': True},
    )
    _make_booking(user, resource, start_offset_minutes=-120, duration_minutes=60)

    with patch('apps.notifications.tasks.send_notification_email.delay') as mock_delay:
        auto_complete_bookings()

    mock_delay.assert_called_once()
    args = mock_delay.call_args[0]
    assert args[0] == user.id
    assert args[1] == 'booking_completed'


@pytest.mark.django_db
def test_auto_complete_email_task_skips_when_preference_disabled(user, resource):
    """send_notification_email skips sending when booking_completed_email=False (opt-in default)."""
    from apps.notifications.tasks import send_notification_email
    from apps.notifications.models import NotificationPreference

    NotificationPreference.objects.update_or_create(
        user=user,
        defaults={'booking_completed_email': False},
    )

    with patch('apps.notifications.tasks.send_mail') as mock_send:
        send_notification_email(
            user.id,
            'booking_completed',
            {'resource_name': 'Test Desk', 'end_time': '09.06.2026 10:00'},
        )

    mock_send.assert_not_called()
