"""
Acceptance-criteria tests for booking no-show detection and check-in feature.

Covers:
  AC-1  mark_no_show_bookings marks only meeting_room bookings started
        >NO_SHOW_MINUTES ago with no check-in
  AC-2  mark_no_show_bookings does NOT touch bookings with check-in,
        wrong resource type, or not yet past the threshold
  AC-3  Check-in endpoint returns 200 and sets checked_in_at
  AC-4  Check-in on a wrong-company booking returns 404
        (CompanyIsolationMixin scopes out cross-company rows)
  AC-5  Check-in on already-completed/no_show booking returns 400
  AC-6  NO_SHOW_MINUTES is configurable via settings

timezone.now() is patched via unittest.mock — no freezegun needed.
All tests are self-contained; each creates its own fixtures.
"""

from datetime import datetime, timezone as dt_timezone, timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.bookings.models import Booking, Resource
from apps.companies.models import Company
from apps.users.models import User

# ---------------------------------------------------------------------------
# Shared fixed timestamp
# ---------------------------------------------------------------------------

_FIXED_NOW = datetime(2024, 7, 10, 12, 0, 0, tzinfo=dt_timezone.utc)


def _fixed_now():
    return _FIXED_NOW


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_company(suffix):
    return Company.objects.create(name=f'NoShow Co {suffix}', plan='basic')


def _make_user(company, suffix, role='employee'):
    return User.objects.create_user(
        email=f'noshow_{suffix}@test.com',
        password='pass',
        first_name='NS',
        last_name='User',
        role=role,
        company=company,
        is_email_verified=True,
    )


def _make_resource(suffix, resource_type='meeting_room'):
    return Resource.objects.create(
        name=f'NS Resource {suffix}',
        resource_type=resource_type,
        available_days=list(range(7)),
        available_from='00:00',
        available_until='23:59',
    )


def _make_booking(user, resource, start_offset_minutes, duration_minutes=60,
                  status='confirmed', checked_in_at=None, fixed_now=None):
    """Create a booking whose start_time is relative to fixed_now."""
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
        checked_in_at=checked_in_at,
    )


# ---------------------------------------------------------------------------
# AC-1  mark_no_show_bookings — positive cases
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ac1_meeting_room_past_threshold_no_checkin_marked_no_show():
    """
    AC-1: meeting_room booking started >15 min ago, no check-in →
    status becomes no_show.
    """
    from apps.bookings.tasks import mark_no_show_bookings

    company = _make_company('a1')
    user = _make_user(company, 'a1')
    resource = _make_resource('a1', resource_type='meeting_room')
    booking = _make_booking(user, resource, start_offset_minutes=-20,
                            duration_minutes=60, fixed_now=_FIXED_NOW)

    with patch('apps.bookings.tasks.timezone.now', return_value=_FIXED_NOW), \
            patch('apps.bookings.tasks.settings') as mock_settings:
        mock_settings.NO_SHOW_MINUTES = 15
        count = mark_no_show_bookings()

    booking.refresh_from_db()
    assert booking.status == 'no_show'
    assert count == 1


@pytest.mark.django_db
def test_ac1_returns_count_of_no_show_bookings():
    """AC-1: task returns number of bookings marked as no_show."""
    from apps.bookings.tasks import mark_no_show_bookings

    company = _make_company('a1b')
    user = _make_user(company, 'a1b')
    resource = _make_resource('a1b', resource_type='meeting_room')
    _make_booking(user, resource, start_offset_minutes=-20,
                  duration_minutes=60, fixed_now=_FIXED_NOW)
    _make_booking(user, resource, start_offset_minutes=-30,
                  duration_minutes=60, fixed_now=_FIXED_NOW)

    with patch('apps.bookings.tasks.timezone.now', return_value=_FIXED_NOW), \
            patch('apps.bookings.tasks.settings') as mock_settings:
        mock_settings.NO_SHOW_MINUTES = 15
        count = mark_no_show_bookings()

    assert count == 2


@pytest.mark.django_db
def test_ac1_logs_no_show_count(caplog):
    """AC-1: task logs at INFO level with the count of no_show bookings."""
    from apps.bookings.tasks import mark_no_show_bookings

    company = _make_company('a1c')
    user = _make_user(company, 'a1c')
    resource = _make_resource('a1c', resource_type='meeting_room')
    _make_booking(user, resource, start_offset_minutes=-20,
                  duration_minutes=60, fixed_now=_FIXED_NOW)

    with patch('apps.bookings.tasks.timezone.now', return_value=_FIXED_NOW), \
            patch('apps.bookings.tasks.settings') as mock_settings, \
            patch('apps.bookings.tasks.logger') as mock_logger:
        mock_settings.NO_SHOW_MINUTES = 15
        mark_no_show_bookings()

    mock_logger.info.assert_called_once()
    call_msg = str(mock_logger.info.call_args)
    assert 'no_show' in call_msg


# ---------------------------------------------------------------------------
# AC-2  mark_no_show_bookings — negative cases (must NOT be touched)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ac2_booking_with_checkin_not_marked_no_show():
    """
    AC-2: meeting_room booking started >15 min ago BUT has a check-in →
    status stays confirmed.
    """
    from apps.bookings.tasks import mark_no_show_bookings

    company = _make_company('a2a')
    user = _make_user(company, 'a2a')
    resource = _make_resource('a2a', resource_type='meeting_room')
    check_time = _FIXED_NOW - timedelta(minutes=10)
    booking = _make_booking(user, resource, start_offset_minutes=-20,
                            duration_minutes=60, fixed_now=_FIXED_NOW,
                            checked_in_at=check_time)

    with patch('apps.bookings.tasks.timezone.now', return_value=_FIXED_NOW), \
            patch('apps.bookings.tasks.settings') as mock_settings:
        mock_settings.NO_SHOW_MINUTES = 15
        count = mark_no_show_bookings()

    booking.refresh_from_db()
    assert booking.status == 'confirmed'
    assert count == 0


@pytest.mark.django_db
def test_ac2_desk_booking_not_marked_no_show():
    """
    AC-2: desk booking started >15 min ago, no check-in →
    NOT marked no_show (only meeting_room is targeted).
    """
    from apps.bookings.tasks import mark_no_show_bookings

    company = _make_company('a2b')
    user = _make_user(company, 'a2b')
    resource = _make_resource('a2b', resource_type='desk')
    booking = _make_booking(user, resource, start_offset_minutes=-20,
                            duration_minutes=480, fixed_now=_FIXED_NOW)

    with patch('apps.bookings.tasks.timezone.now', return_value=_FIXED_NOW), \
            patch('apps.bookings.tasks.settings') as mock_settings:
        mock_settings.NO_SHOW_MINUTES = 15
        count = mark_no_show_bookings()

    booking.refresh_from_db()
    assert booking.status == 'confirmed'
    assert count == 0


@pytest.mark.django_db
def test_ac2_parking_booking_not_marked_no_show():
    """
    AC-2: parking booking started >15 min ago, no check-in →
    NOT marked no_show (only meeting_room is targeted).
    """
    from apps.bookings.tasks import mark_no_show_bookings

    company = _make_company('a2c')
    user = _make_user(company, 'a2c')
    resource = _make_resource('a2c', resource_type='parking')
    booking = _make_booking(user, resource, start_offset_minutes=-20,
                            duration_minutes=480, fixed_now=_FIXED_NOW)

    with patch('apps.bookings.tasks.timezone.now', return_value=_FIXED_NOW), \
            patch('apps.bookings.tasks.settings') as mock_settings:
        mock_settings.NO_SHOW_MINUTES = 15
        count = mark_no_show_bookings()

    booking.refresh_from_db()
    assert booking.status == 'confirmed'
    assert count == 0


@pytest.mark.django_db
def test_ac2_meeting_room_not_past_threshold_not_marked():
    """
    AC-2: meeting_room booking started only 10 min ago (threshold is 15 min) →
    NOT marked no_show.
    """
    from apps.bookings.tasks import mark_no_show_bookings

    company = _make_company('a2d')
    user = _make_user(company, 'a2d')
    resource = _make_resource('a2d', resource_type='meeting_room')
    # Started 10 min ago, threshold is 15 min
    booking = _make_booking(user, resource, start_offset_minutes=-10,
                            duration_minutes=60, fixed_now=_FIXED_NOW)

    with patch('apps.bookings.tasks.timezone.now', return_value=_FIXED_NOW), \
            patch('apps.bookings.tasks.settings') as mock_settings:
        mock_settings.NO_SHOW_MINUTES = 15
        count = mark_no_show_bookings()

    booking.refresh_from_db()
    assert booking.status == 'confirmed'
    assert count == 0


@pytest.mark.django_db
def test_ac2_already_cancelled_booking_not_affected():
    """
    AC-2: meeting_room booking with status=cancelled →
    task does not touch it (filter is status='confirmed').
    """
    from apps.bookings.tasks import mark_no_show_bookings

    company = _make_company('a2e')
    user = _make_user(company, 'a2e')
    resource = _make_resource('a2e', resource_type='meeting_room')
    booking = _make_booking(user, resource, start_offset_minutes=-20,
                            duration_minutes=60, status='cancelled',
                            fixed_now=_FIXED_NOW)

    with patch('apps.bookings.tasks.timezone.now', return_value=_FIXED_NOW), \
            patch('apps.bookings.tasks.settings') as mock_settings:
        mock_settings.NO_SHOW_MINUTES = 15
        count = mark_no_show_bookings()

    booking.refresh_from_db()
    assert booking.status == 'cancelled'
    assert count == 0


@pytest.mark.django_db
def test_ac2_already_completed_booking_not_affected():
    """
    AC-2: meeting_room booking with status=completed →
    task does not touch it.
    """
    from apps.bookings.tasks import mark_no_show_bookings

    company = _make_company('a2f')
    user = _make_user(company, 'a2f')
    resource = _make_resource('a2f', resource_type='meeting_room')
    booking = _make_booking(user, resource, start_offset_minutes=-20,
                            duration_minutes=60, status='completed',
                            fixed_now=_FIXED_NOW)

    with patch('apps.bookings.tasks.timezone.now', return_value=_FIXED_NOW), \
            patch('apps.bookings.tasks.settings') as mock_settings:
        mock_settings.NO_SHOW_MINUTES = 15
        count = mark_no_show_bookings()

    booking.refresh_from_db()
    assert booking.status == 'completed'
    assert count == 0


# ---------------------------------------------------------------------------
# AC-3  Check-in endpoint — success
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ac3_checkin_returns_200_and_sets_checked_in_at():
    """
    AC-3: POST /api/v1/bookings/reservations/<id>/check-in/ by booking owner
    returns 200 and sets checked_in_at.
    """
    company = _make_company('a3a')
    user = _make_user(company, 'a3a')
    resource = _make_resource('a3a', resource_type='meeting_room')
    booking = _make_booking(user, resource, start_offset_minutes=-5,
                            duration_minutes=60, fixed_now=_FIXED_NOW)

    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post(f'/api/v1/bookings/reservations/{booking.id}/check-in/')

    assert response.status_code == 200
    data = response.json()
    assert data['checked_in_at'] is not None
    assert data['status'] == 'confirmed'

    booking.refresh_from_db()
    assert booking.checked_in_at is not None


@pytest.mark.django_db
def test_ac3_checkin_by_company_admin_returns_403():
    """
    AC-3: company_admin cannot check in on another user's booking.
    Check-in is a physical presence confirmation — only the booking owner
    (or superadmin for emergency override) may perform it.
    """
    company = _make_company('a3b')
    admin = _make_user(company, 'a3b_admin', role='company_admin')
    employee = _make_user(company, 'a3b_emp')
    resource = _make_resource('a3b', resource_type='meeting_room')
    booking = _make_booking(employee, resource, start_offset_minutes=-5,
                            duration_minutes=60, fixed_now=_FIXED_NOW)

    client = APIClient()
    client.force_authenticate(user=admin)

    response = client.post(f'/api/v1/bookings/reservations/{booking.id}/check-in/')

    assert response.status_code == 403


@pytest.mark.django_db
def test_ac3_checkin_by_superadmin_returns_200():
    """
    AC-3: superadmin can check in on any booking (emergency override).
    Returns 200 and sets checked_in_at.
    """
    company = _make_company('a3sa')
    employee = _make_user(company, 'a3sa_emp')
    resource = _make_resource('a3sa', resource_type='meeting_room')
    booking = _make_booking(employee, resource, start_offset_minutes=-5,
                            duration_minutes=60, fixed_now=_FIXED_NOW)

    superadmin = User.objects.create_user(
        email='superadmin_a3sa@test.com',
        password='pass',
        first_name='Super',
        last_name='Admin',
        role='superadmin',
        is_email_verified=True,
    )

    client = APIClient()
    client.force_authenticate(user=superadmin)

    response = client.post(f'/api/v1/bookings/reservations/{booking.id}/check-in/')

    assert response.status_code == 200
    booking.refresh_from_db()
    assert booking.checked_in_at is not None


@pytest.mark.django_db
def test_ac3_checkin_prevents_no_show():
    """
    AC-3: After a successful check-in, mark_no_show_bookings
    does NOT mark the booking as no_show.
    """
    from apps.bookings.tasks import mark_no_show_bookings

    company = _make_company('a3c')
    user = _make_user(company, 'a3c')
    resource = _make_resource('a3c', resource_type='meeting_room')
    check_time = _FIXED_NOW - timedelta(minutes=2)
    # Booking started 20 min ago, checked in 2 min ago
    booking = _make_booking(user, resource, start_offset_minutes=-20,
                            duration_minutes=60, fixed_now=_FIXED_NOW,
                            checked_in_at=check_time)

    with patch('apps.bookings.tasks.timezone.now', return_value=_FIXED_NOW):
        count = mark_no_show_bookings()

    booking.refresh_from_db()
    assert booking.status == 'confirmed'
    assert count == 0


# ---------------------------------------------------------------------------
# AC-4  Check-in on wrong-company booking returns 404
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ac4_checkin_wrong_company_returns_404():
    """
    AC-4: User from company A tries to check in to a booking belonging to
    company B — CompanyIsolationMixin scopes the queryset so it returns 404.
    """
    company_a = _make_company('a4a')
    company_b = _make_company('a4b')
    user_a = _make_user(company_a, 'a4a')
    user_b = _make_user(company_b, 'a4b')
    resource = _make_resource('a4', resource_type='meeting_room')
    booking = _make_booking(user_b, resource, start_offset_minutes=-5,
                            duration_minutes=60, fixed_now=_FIXED_NOW)

    client = APIClient()
    client.force_authenticate(user=user_a)

    response = client.post(f'/api/v1/bookings/reservations/{booking.id}/check-in/')

    assert response.status_code == 404


@pytest.mark.django_db
def test_ac4_unauthenticated_checkin_returns_401():
    """
    AC-4: Unauthenticated request to check-in endpoint returns 401.
    """
    company = _make_company('a4c')
    user = _make_user(company, 'a4c')
    resource = _make_resource('a4c', resource_type='meeting_room')
    booking = _make_booking(user, resource, start_offset_minutes=-5,
                            duration_minutes=60, fixed_now=_FIXED_NOW)

    client = APIClient()
    response = client.post(f'/api/v1/bookings/reservations/{booking.id}/check-in/')

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# AC-5  Check-in on invalid status returns 400
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ac5_checkin_already_completed_returns_400():
    """
    AC-5: Check-in attempt on a completed booking → 400.
    """
    company = _make_company('a5a')
    user = _make_user(company, 'a5a')
    resource = _make_resource('a5a', resource_type='meeting_room')
    booking = _make_booking(user, resource, start_offset_minutes=-70,
                            duration_minutes=60, status='completed',
                            fixed_now=_FIXED_NOW)

    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post(f'/api/v1/bookings/reservations/{booking.id}/check-in/')

    assert response.status_code == 400


@pytest.mark.django_db
def test_ac5_checkin_already_no_show_returns_400():
    """
    AC-5: Check-in attempt on a no_show booking → 400.
    """
    company = _make_company('a5b')
    user = _make_user(company, 'a5b')
    resource = _make_resource('a5b', resource_type='meeting_room')
    booking = _make_booking(user, resource, start_offset_minutes=-20,
                            duration_minutes=60, status='no_show',
                            fixed_now=_FIXED_NOW)

    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post(f'/api/v1/bookings/reservations/{booking.id}/check-in/')

    assert response.status_code == 400


@pytest.mark.django_db
def test_ac5_checkin_already_cancelled_returns_400():
    """
    AC-5: Check-in attempt on a cancelled booking → 400.
    """
    company = _make_company('a5c')
    user = _make_user(company, 'a5c')
    resource = _make_resource('a5c', resource_type='meeting_room')
    booking = _make_booking(user, resource, start_offset_minutes=-5,
                            duration_minutes=60, status='cancelled',
                            fixed_now=_FIXED_NOW)

    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post(f'/api/v1/bookings/reservations/{booking.id}/check-in/')

    assert response.status_code == 400


@pytest.mark.django_db
def test_ac5_double_checkin_returns_400():
    """
    AC-5: Second check-in attempt on an already-checked-in booking → 400.
    """
    company = _make_company('a5d')
    user = _make_user(company, 'a5d')
    resource = _make_resource('a5d', resource_type='meeting_room')
    check_time = _FIXED_NOW - timedelta(minutes=2)
    booking = _make_booking(user, resource, start_offset_minutes=-5,
                            duration_minutes=60, fixed_now=_FIXED_NOW,
                            checked_in_at=check_time)

    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post(f'/api/v1/bookings/reservations/{booking.id}/check-in/')

    assert response.status_code == 400


# ---------------------------------------------------------------------------
# AC-6  NO_SHOW_MINUTES is configurable
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ac6_no_show_minutes_30_does_not_mark_at_20_min():
    """
    AC-6: When NO_SHOW_MINUTES=30, a booking that started 20 min ago
    is NOT yet marked as no_show.
    """
    from apps.bookings.tasks import mark_no_show_bookings

    company = _make_company('a6a')
    user = _make_user(company, 'a6a')
    resource = _make_resource('a6a', resource_type='meeting_room')
    booking = _make_booking(user, resource, start_offset_minutes=-20,
                            duration_minutes=60, fixed_now=_FIXED_NOW)

    with patch('apps.bookings.tasks.timezone.now', return_value=_FIXED_NOW), \
            patch('apps.bookings.tasks.settings') as mock_settings:
        mock_settings.NO_SHOW_MINUTES = 30
        count = mark_no_show_bookings()

    booking.refresh_from_db()
    assert booking.status == 'confirmed'
    assert count == 0


@pytest.mark.django_db
def test_ac6_no_show_minutes_30_marks_at_35_min():
    """
    AC-6: When NO_SHOW_MINUTES=30, a booking that started 35 min ago
    IS marked as no_show.
    """
    from apps.bookings.tasks import mark_no_show_bookings

    company = _make_company('a6b')
    user = _make_user(company, 'a6b')
    resource = _make_resource('a6b', resource_type='meeting_room')
    booking = _make_booking(user, resource, start_offset_minutes=-35,
                            duration_minutes=60, fixed_now=_FIXED_NOW)

    with patch('apps.bookings.tasks.timezone.now', return_value=_FIXED_NOW), \
            patch('apps.bookings.tasks.settings') as mock_settings:
        mock_settings.NO_SHOW_MINUTES = 30
        count = mark_no_show_bookings()

    booking.refresh_from_db()
    assert booking.status == 'no_show'
    assert count == 1


# ---------------------------------------------------------------------------
# AC-7  Check-in before booking start_time returns 400
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ac7_checkin_before_start_time_returns_400():
    """
    AC-7: Check-in attempt before the booking start_time → 400.
    Prevents users from checking in early to bypass no-show detection.
    """
    company = _make_company('a7a')
    user = _make_user(company, 'a7a')
    resource = _make_resource('a7a', resource_type='meeting_room')
    # start_offset_minutes=+30 means booking starts 30 min in the future
    booking = _make_booking(user, resource, start_offset_minutes=30,
                            duration_minutes=60, fixed_now=_FIXED_NOW)

    client = APIClient()
    client.force_authenticate(user=user)

    # Patch timezone.now in views to simulate calling before start
    with patch('apps.bookings.views.timezone.now', return_value=_FIXED_NOW):
        response = client.post(f'/api/v1/bookings/reservations/{booking.id}/check-in/')

    assert response.status_code == 400
    data = response.json()
    assert 'detail' in str(data)


# ---------------------------------------------------------------------------
# AC-8  run-no-show endpoint permission checks
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ac8_run_no_show_returns_403_for_employee():
    """
    AC-8: POST /api/v1/bookings/reservations/run-no-show/ by an employee → 403.
    """
    company = _make_company('a8a')
    user = _make_user(company, 'a8a', role='employee')

    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post('/api/v1/bookings/reservations/run-no-show/')

    assert response.status_code == 403


@pytest.mark.django_db
def test_ac8_run_no_show_returns_403_for_company_admin():
    """
    AC-8: POST /api/v1/bookings/reservations/run-no-show/ by a company_admin → 403.
    Only superadmin may trigger this endpoint.
    """
    company = _make_company('a8b')
    admin = _make_user(company, 'a8b', role='company_admin')

    client = APIClient()
    client.force_authenticate(user=admin)

    response = client.post('/api/v1/bookings/reservations/run-no-show/')

    assert response.status_code == 403


@pytest.mark.django_db
def test_ac8_run_no_show_returns_200_for_superadmin():
    """
    AC-8: POST /api/v1/bookings/reservations/run-no-show/ by superadmin → 200.
    """
    superadmin = User.objects.create_user(
        email='superadmin_a8@test.com',
        password='pass',
        first_name='Super',
        last_name='Admin',
        role='superadmin',
        is_email_verified=True,
    )

    client = APIClient()
    client.force_authenticate(user=superadmin)

    response = client.post('/api/v1/bookings/reservations/run-no-show/')

    assert response.status_code == 200
    assert 'no_show_marked' in response.json()


# ---------------------------------------------------------------------------
# AC-9  mark_no_show_bookings does NOT touch bookings with end_time in the past
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ac9_ended_booking_not_marked_no_show():
    """
    AC-9: A meeting_room booking that started >NO_SHOW_MINUTES ago AND has already
    ended (end_time in the past) is NOT marked no_show by mark_no_show_bookings.
    Ended bookings are handled by auto_complete_bookings, not this task.
    """
    from apps.bookings.tasks import mark_no_show_bookings

    company = _make_company('a9a')
    user = _make_user(company, 'a9a')
    resource = _make_resource('a9a', resource_type='meeting_room')
    # duration_minutes=10 means end_time = start + 10 min = _FIXED_NOW - 20 + 10 = _FIXED_NOW - 10
    # So end_time is 10 min in the past at _FIXED_NOW — booking has already ended
    booking = _make_booking(user, resource, start_offset_minutes=-20,
                            duration_minutes=10, fixed_now=_FIXED_NOW)

    with patch('apps.bookings.tasks.timezone.now', return_value=_FIXED_NOW), \
            patch('apps.bookings.tasks.settings') as mock_settings:
        mock_settings.NO_SHOW_MINUTES = 15
        count = mark_no_show_bookings()

    booking.refresh_from_db()
    assert booking.status == 'confirmed'
    assert count == 0
