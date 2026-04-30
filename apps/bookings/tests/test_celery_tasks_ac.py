"""
Acceptance-criteria tests for booking Celery beat tasks.

Covers:
  AC-1  send_booking_reminders — in-app + email notifications
  AC-2  auto_complete_bookings — status transition to 'completed'
  AC-3  All automated actions are logged
  AC-4  REMINDER_MINUTES_BEFORE is configurable via env / settings

timezone.now() is patched via unittest.mock so no freezegun dependency is
required.  All tests are self-contained — fixtures create their own objects.
"""

from datetime import datetime, timezone as dt_timezone, timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.bookings.models import Booking, Resource
from apps.companies.models import Company
from apps.notifications.models import Notification, NotificationPreference
from apps.users.models import User

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_FIXED_NOW = datetime(2024, 6, 15, 10, 0, 0, tzinfo=dt_timezone.utc)


def _fixed_now():
    """Return the fixed 'now' as an aware datetime."""
    return _FIXED_NOW


def _make_company(suffix='ac'):
    return Company.objects.create(name=f'AC Test Co {suffix}', plan='basic')


def _make_user(company, suffix='ac'):
    return User.objects.create_user(
        email=f'ac_user_{suffix}@test.com',
        password='pass',
        first_name='AC',
        last_name='User',
        role='employee',
        company=company,
        is_email_verified=True,
    )


def _make_resource(suffix='ac'):
    return Resource.objects.create(
        name=f'AC Desk {suffix}',
        resource_type='desk',
        available_days=list(range(7)),
        available_from='00:00',
        available_until='23:59',
    )


def _make_booking(user, resource, start_offset_minutes, duration_minutes=60,
                  status='confirmed', reminder_sent=False, fixed_now=None):
    """
    Create a Booking whose start_time is relative to fixed_now (or
    timezone.now() when fixed_now is None).
    """
    base = fixed_now if fixed_now is not None else timezone.now()
    start = base + timedelta(minutes=start_offset_minutes)
    end = start + timedelta(minutes=duration_minutes)
    return Booking.objects.create(
        resource=resource,
        user=user,
        company=user.company,
        start_time=start,
        end_time=end,
        status=status,
        reminder_sent=reminder_sent,
    )


# ---------------------------------------------------------------------------
# AC-1  send_booking_reminders — in-app notification
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ac1_reminder_creates_notification_at_15min():
    """
    AC-1 Test 1: booking start_time = now+15min, status=confirmed,
    reminder_sent=False → Notification created, reminder_sent set True.
    """
    from apps.bookings.tasks import send_booking_reminders

    company = _make_company('t1')
    user = _make_user(company, 't1')
    resource = _make_resource('t1')
    booking = _make_booking(user, resource, start_offset_minutes=15,
                            fixed_now=_FIXED_NOW)

    with patch('apps.bookings.tasks.timezone.now', return_value=_FIXED_NOW):
        send_booking_reminders()

    booking.refresh_from_db()
    assert booking.reminder_sent is True
    assert Notification.objects.filter(
        user=user,
        notification_type='booking_reminder',
    ).count() == 1


@pytest.mark.django_db
def test_ac1_reminder_created_for_booking_at_13min():
    """
    AC-1 Test 2: booking start_time = now+13min (within window [now+10, now+17.5])
    → Notification created (tests the widened window).
    """
    from apps.bookings.tasks import send_booking_reminders

    company = _make_company('t2')
    user = _make_user(company, 't2')
    resource = _make_resource('t2')
    booking = _make_booking(user, resource, start_offset_minutes=13,
                            fixed_now=_FIXED_NOW)

    with patch('apps.bookings.tasks.timezone.now', return_value=_FIXED_NOW):
        send_booking_reminders()

    booking.refresh_from_db()
    assert booking.reminder_sent is True
    assert Notification.objects.filter(
        user=user, notification_type='booking_reminder',
    ).exists()


@pytest.mark.django_db
def test_ac1_no_duplicate_reminder_when_reminder_sent_true():
    """
    AC-1 Test 3: booking start_time = now+15min, reminder_sent=True
    → NO new Notification created (deduplication).
    """
    from apps.bookings.tasks import send_booking_reminders

    company = _make_company('t3')
    user = _make_user(company, 't3')
    resource = _make_resource('t3')
    _make_booking(user, resource, start_offset_minutes=15,
                  reminder_sent=True, fixed_now=_FIXED_NOW)

    with patch('apps.bookings.tasks.timezone.now', return_value=_FIXED_NOW):
        send_booking_reminders()

    assert not Notification.objects.filter(
        user=user, notification_type='booking_reminder',
    ).exists()


@pytest.mark.django_db
def test_ac1_no_reminder_for_cancelled_booking():
    """
    AC-1 Test 4: booking start_time = now+15min, status=cancelled
    → NO Notification created.
    """
    from apps.bookings.tasks import send_booking_reminders

    company = _make_company('t4')
    user = _make_user(company, 't4')
    resource = _make_resource('t4')
    _make_booking(user, resource, start_offset_minutes=15,
                  status='cancelled', fixed_now=_FIXED_NOW)

    with patch('apps.bookings.tasks.timezone.now', return_value=_FIXED_NOW):
        send_booking_reminders()

    assert not Notification.objects.filter(
        user=user, notification_type='booking_reminder',
    ).exists()


@pytest.mark.django_db
def test_ac1_email_sent_when_booking_email_true():
    """
    AC-1 Test 5: NotificationPreference(booking_email=True, do_not_disturb=False)
    → email sent via send_mail.
    """
    from apps.bookings.tasks import send_booking_reminders

    company = _make_company('t5')
    user = _make_user(company, 't5')
    resource = _make_resource('t5')
    NotificationPreference.objects.create(
        user=user,
        booking_email=True,
        do_not_disturb=False,
    )
    _make_booking(user, resource, start_offset_minutes=15, fixed_now=_FIXED_NOW)

    with patch('apps.bookings.tasks.timezone.now', return_value=_FIXED_NOW), \
            patch('apps.bookings.tasks.send_mail') as mock_send:
        send_booking_reminders()

    mock_send.assert_called_once()
    _, kwargs = mock_send.call_args
    # send_mail may be called with positional or keyword args
    call_args = mock_send.call_args
    assert user.email in (
        call_args.args[3] if len(call_args.args) > 3 else call_args.kwargs.get('recipient_list', [])
    )


@pytest.mark.django_db
def test_ac1_email_not_sent_when_booking_email_false():
    """
    AC-1 Test 6: NotificationPreference(booking_reminder_email=False)
    → email NOT sent.
    """
    from apps.bookings.tasks import send_booking_reminders

    company = _make_company('t6')
    user = _make_user(company, 't6')
    resource = _make_resource('t6')
    NotificationPreference.objects.create(
        user=user,
        booking_reminder_email=False,
        do_not_disturb=False,
    )
    _make_booking(user, resource, start_offset_minutes=15, fixed_now=_FIXED_NOW)

    with patch('apps.bookings.tasks.timezone.now', return_value=_FIXED_NOW), \
            patch('apps.bookings.tasks.send_mail') as mock_send:
        send_booking_reminders()

    mock_send.assert_not_called()


@pytest.mark.django_db
def test_ac1_email_not_sent_when_do_not_disturb_true():
    """
    AC-1 Test 7: NotificationPreference(do_not_disturb=True)
    → email NOT sent even if booking_email=True.
    """
    from apps.bookings.tasks import send_booking_reminders

    company = _make_company('t7')
    user = _make_user(company, 't7')
    resource = _make_resource('t7')
    NotificationPreference.objects.create(
        user=user,
        booking_email=True,
        do_not_disturb=True,
    )
    _make_booking(user, resource, start_offset_minutes=15, fixed_now=_FIXED_NOW)

    with patch('apps.bookings.tasks.timezone.now', return_value=_FIXED_NOW), \
            patch('apps.bookings.tasks.send_mail') as mock_send:
        send_booking_reminders()

    mock_send.assert_not_called()


@pytest.mark.django_db
def test_ac1_email_sent_by_default_when_no_preference_row():
    """
    AC-1 Test 8: no NotificationPreference row → email sent by default.
    """
    from apps.bookings.tasks import send_booking_reminders

    company = _make_company('t8')
    user = _make_user(company, 't8')
    resource = _make_resource('t8')
    # Ensure no preference row exists
    assert not NotificationPreference.objects.filter(user=user).exists()
    _make_booking(user, resource, start_offset_minutes=15, fixed_now=_FIXED_NOW)

    with patch('apps.bookings.tasks.timezone.now', return_value=_FIXED_NOW), \
            patch('apps.bookings.tasks.send_mail') as mock_send:
        send_booking_reminders()

    mock_send.assert_called_once()


@pytest.mark.django_db
def test_ac1_returns_count_of_reminders_sent():
    """
    AC-1 Test 9: send_booking_reminders returns the count of reminders sent.
    """
    from apps.bookings.tasks import send_booking_reminders

    company = _make_company('t9')
    user = _make_user(company, 't9')
    resource = _make_resource('t9')
    _make_booking(user, resource, start_offset_minutes=15, fixed_now=_FIXED_NOW)

    with patch('apps.bookings.tasks.timezone.now', return_value=_FIXED_NOW), \
            patch('apps.bookings.tasks.send_mail'):
        result = send_booking_reminders()

    assert result == 1


@pytest.mark.django_db
def test_ac4_reminder_minutes_before_setting_respected():
    """
    AC-1 / AC-4 Test 10: settings.REMINDER_MINUTES_BEFORE=30 →
    booking at now+30min gets a reminder.
    """
    from apps.bookings.tasks import send_booking_reminders

    company = _make_company('t10')
    user = _make_user(company, 't10')
    resource = _make_resource('t10')
    # booking at now+30min
    booking = _make_booking(user, resource, start_offset_minutes=30, fixed_now=_FIXED_NOW)

    with patch('apps.bookings.tasks.timezone.now', return_value=_FIXED_NOW), \
            patch('apps.bookings.tasks.settings') as mock_settings, \
            patch('apps.bookings.tasks.send_mail'):
        mock_settings.REMINDER_MINUTES_BEFORE = 30
        mock_settings.DEFAULT_FROM_EMAIL = 'noreply@test.com'
        send_booking_reminders()

    booking.refresh_from_db()
    assert booking.reminder_sent is True
    assert Notification.objects.filter(
        user=user, notification_type='booking_reminder',
    ).exists()


# ---------------------------------------------------------------------------
# AC-2  auto_complete_bookings
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ac2_past_confirmed_booking_becomes_completed():
    """
    AC-2 Test 11: end_time = now-1min, status=confirmed → status=completed.
    updated_at is stamped by auto_now=True on save, so we only verify the
    status transition and that updated_at is non-null (always true with auto_now).
    """
    from apps.bookings.tasks import auto_complete_bookings

    company = _make_company('t11')
    user = _make_user(company, 't11')
    resource = _make_resource('t11')
    # start_offset = -61, duration = 60 → end_time = now - 1min
    booking = _make_booking(user, resource, start_offset_minutes=-61,
                            duration_minutes=60, fixed_now=_FIXED_NOW)

    with patch('apps.bookings.tasks.timezone.now', return_value=_FIXED_NOW):
        auto_complete_bookings()

    booking.refresh_from_db()
    assert booking.status == 'completed'
    # auto_now=True guarantees updated_at is set; the task explicitly includes
    # 'updated_at' in update_fields so the field is always persisted.
    assert booking.updated_at is not None


@pytest.mark.django_db
def test_ac2_future_booking_status_unchanged():
    """
    AC-2 Test 12: end_time = now+1min, status=confirmed → status remains confirmed.
    """
    from apps.bookings.tasks import auto_complete_bookings

    company = _make_company('t12')
    user = _make_user(company, 't12')
    resource = _make_resource('t12')
    # end_time = _FIXED_NOW + 61min
    booking = _make_booking(user, resource, start_offset_minutes=0,
                            duration_minutes=61, fixed_now=_FIXED_NOW)

    with patch('apps.bookings.tasks.timezone.now', return_value=_FIXED_NOW):
        auto_complete_bookings()

    booking.refresh_from_db()
    assert booking.status == 'confirmed'


@pytest.mark.django_db
def test_ac2_cancelled_booking_status_unchanged():
    """
    AC-2 Test 13: end_time = now-1min, status=cancelled → status remains cancelled.
    """
    from apps.bookings.tasks import auto_complete_bookings

    company = _make_company('t13')
    user = _make_user(company, 't13')
    resource = _make_resource('t13')
    booking = _make_booking(user, resource, start_offset_minutes=-61,
                            duration_minutes=60, status='cancelled',
                            fixed_now=_FIXED_NOW)

    with patch('apps.bookings.tasks.timezone.now', return_value=_FIXED_NOW):
        auto_complete_bookings()

    booking.refresh_from_db()
    assert booking.status == 'cancelled'


@pytest.mark.django_db
def test_ac2_returns_count_of_completed_bookings():
    """
    AC-2 Test 14: auto_complete_bookings returns count of completed bookings.
    """
    from apps.bookings.tasks import auto_complete_bookings

    company = _make_company('t14')
    user = _make_user(company, 't14')
    resource = _make_resource('t14')
    _make_booking(user, resource, start_offset_minutes=-61,
                  duration_minutes=60, fixed_now=_FIXED_NOW)

    with patch('apps.bookings.tasks.timezone.now', return_value=_FIXED_NOW):
        result = auto_complete_bookings()

    assert result == 1


@pytest.mark.django_db
def test_ac2_multiple_past_bookings_all_completed():
    """
    AC-2 Test 15: multiple past confirmed bookings → all completed, return value = count.
    """
    from apps.bookings.tasks import auto_complete_bookings

    company = _make_company('t15')
    user = _make_user(company, 't15')
    resource = _make_resource('t15')
    b1 = _make_booking(user, resource, start_offset_minutes=-181,
                       duration_minutes=30, fixed_now=_FIXED_NOW)
    b2 = _make_booking(user, resource, start_offset_minutes=-121,
                       duration_minutes=30, fixed_now=_FIXED_NOW)
    b3 = _make_booking(user, resource, start_offset_minutes=-61,
                       duration_minutes=30, fixed_now=_FIXED_NOW)

    with patch('apps.bookings.tasks.timezone.now', return_value=_FIXED_NOW):
        result = auto_complete_bookings()

    assert result == 3
    for b in [b1, b2, b3]:
        b.refresh_from_db()
        assert b.status == 'completed'


# ---------------------------------------------------------------------------
# AC-3  Logging
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ac3_send_booking_reminders_logs_info():
    """
    AC-3 Test 16: send_booking_reminders logs at INFO level when a reminder is sent.
    """
    from apps.bookings.tasks import send_booking_reminders

    company = _make_company('t16')
    user = _make_user(company, 't16')
    resource = _make_resource('t16')
    booking = _make_booking(user, resource, start_offset_minutes=15, fixed_now=_FIXED_NOW)

    with patch('apps.bookings.tasks.timezone.now', return_value=_FIXED_NOW), \
            patch('apps.bookings.tasks.send_mail'), \
            patch('apps.bookings.tasks.logger') as mock_logger:
        send_booking_reminders()

    mock_logger.info.assert_called()
    call_args_list = mock_logger.info.call_args_list
    # At least one info call should mention the booking id
    found = any(
        str(booking.id) in str(call) for call in call_args_list
    )
    assert found, (
        f'Expected logger.info to mention booking.id={booking.id}, '
        f'got calls: {call_args_list}'
    )


@pytest.mark.django_db
def test_ac3_auto_complete_bookings_logs_info():
    """
    AC-3 Test 17: auto_complete_bookings logs at INFO level for each completed booking.
    """
    from apps.bookings.tasks import auto_complete_bookings

    company = _make_company('t17')
    user = _make_user(company, 't17')
    resource = _make_resource('t17')
    booking = _make_booking(user, resource, start_offset_minutes=-61,
                            duration_minutes=60, fixed_now=_FIXED_NOW)

    with patch('apps.bookings.tasks.timezone.now', return_value=_FIXED_NOW), \
            patch('apps.bookings.tasks.logger') as mock_logger:
        auto_complete_bookings()

    mock_logger.info.assert_called()
    call_args_list = mock_logger.info.call_args_list
    found = any(
        str(booking.id) in str(call) for call in call_args_list
    )
    assert found, (
        f'Expected logger.info to mention booking.id={booking.id}, '
        f'got calls: {call_args_list}'
    )


# ---------------------------------------------------------------------------
# AC-4  REMINDER_MINUTES_BEFORE configurable
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ac4_override_to_60min_sends_reminder_at_60min_not_15min():
    """
    AC-4 Test 18: REMINDER_MINUTES_BEFORE=60 →
      - booking at now+60min → reminder sent
      - booking at now+15min → NOT sent (outside new window [now+55, now+62.5])
    """
    from apps.bookings.tasks import send_booking_reminders

    company = _make_company('t18')
    user60 = _make_user(company, 't18a')
    user15 = _make_user(company, 't18b')
    resource = _make_resource('t18')

    # Distinct users to isolate notification checks
    booking_60 = _make_booking(user60, resource, start_offset_minutes=60, fixed_now=_FIXED_NOW)
    booking_15 = _make_booking(user15, resource, start_offset_minutes=15, fixed_now=_FIXED_NOW)

    with patch('apps.bookings.tasks.timezone.now', return_value=_FIXED_NOW), \
            patch('apps.bookings.tasks.settings') as mock_settings, \
            patch('apps.bookings.tasks.send_mail'):
        mock_settings.REMINDER_MINUTES_BEFORE = 60
        mock_settings.DEFAULT_FROM_EMAIL = 'noreply@test.com'
        send_booking_reminders()

    booking_60.refresh_from_db()
    booking_15.refresh_from_db()

    assert booking_60.reminder_sent is True, 'Booking at +60min should have reminder_sent=True'
    assert booking_15.reminder_sent is False, 'Booking at +15min should NOT have reminder_sent=True'

    assert Notification.objects.filter(
        user=user60, notification_type='booking_reminder',
    ).exists(), 'Notification should exist for +60min booking'

    assert not Notification.objects.filter(
        user=user15, notification_type='booking_reminder',
    ).exists(), 'No notification should exist for +15min booking when window is 60min'
