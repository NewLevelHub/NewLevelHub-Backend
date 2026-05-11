"""Integration tests for GET /api/v1/bookings/resources/{id}/schedule/?date= and ?week=."""

from datetime import date, datetime, timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.bookings.models import Booking, Resource
from apps.companies.models import Company
from apps.users.models import User


RESOURCES_URL = '/api/v1/bookings/resources/'


def schedule_url(resource_id):
    return f'{RESOURCES_URL}{resource_id}/schedule/'


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='ScheduleTestCo', plan='basic')


@pytest.fixture
def admin(db, company):
    return User.objects.create_user(
        email='schedadmin@test.test',
        password='pass',
        first_name='Admin',
        last_name='User',
        role='company_admin',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def resource(db):
    return Resource.objects.create(
        name='Test Room',
        resource_type='meeting_room',
        capacity=4,
        available_from='08:00',
        available_until='22:00',
        available_days=list(range(7)),
    )


def _make_booking(resource, admin, start_offset_hours, duration_hours=2):
    """Create a confirmed booking relative to now."""
    now = timezone.now()
    start = now + timedelta(hours=start_offset_hours)
    end = start + timedelta(hours=duration_hours)
    return Booking.objects.create(
        resource=resource,
        user=admin,
        company=admin.company,
        start_time=start,
        end_time=end,
        status='confirmed',
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_schedule_returns_occupied_for_normal_booking(api_client, admin, resource):
    """Booking ending in 2 hours should have status 'occupied'."""
    # Booking starts now+1h, ends now+3h — well over 15 min remaining
    _make_booking(resource, admin, start_offset_hours=1, duration_hours=2)

    api_client.force_authenticate(user=admin)
    local_tz = timezone.get_current_timezone()
    today = timezone.now().astimezone(local_tz).date().isoformat()
    resp = api_client.get(schedule_url(resource.pk), {'date': today})

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]['status'] == 'occupied'


@pytest.mark.django_db
def test_schedule_returns_soon_available(api_client, admin, resource):
    """Booking ending in 5 minutes should have status 'soon_available'."""
    now = timezone.now()
    # Booking started 55 min ago, ends in 5 min
    start = now - timedelta(minutes=55)
    end = now + timedelta(minutes=5)
    Booking.objects.create(
        resource=resource,
        user=admin,
        company=admin.company,
        start_time=start,
        end_time=end,
        status='confirmed',
    )

    api_client.force_authenticate(user=admin)
    local_tz = timezone.get_current_timezone()
    today = now.astimezone(local_tz).date().isoformat()
    resp = api_client.get(schedule_url(resource.pk), {'date': today})

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]['status'] == 'soon_available'


@pytest.mark.django_db
def test_schedule_defaults_to_today(api_client, admin, resource):
    """No date param — endpoint returns today's bookings."""
    _make_booking(resource, admin, start_offset_hours=1, duration_hours=1)

    api_client.force_authenticate(user=admin)
    resp = api_client.get(schedule_url(resource.pk))

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1


@pytest.mark.django_db
def test_schedule_filters_by_date(api_client, admin, resource):
    """Booking tomorrow must not appear when querying today."""
    now = timezone.now()
    local_tz = timezone.get_current_timezone()
    today = now.astimezone(local_tz).date().isoformat()

    # Booking tomorrow
    tomorrow_start = now + timedelta(days=1, hours=1)
    tomorrow_end = tomorrow_start + timedelta(hours=1)
    Booking.objects.create(
        resource=resource,
        user=admin,
        company=admin.company,
        start_time=tomorrow_start,
        end_time=tomorrow_end,
        status='confirmed',
    )

    api_client.force_authenticate(user=admin)
    resp = api_client.get(schedule_url(resource.pk), {'date': today})

    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.django_db
def test_schedule_invalid_date_returns_400(api_client, admin, resource):
    """Invalid date format must return 400."""
    api_client.force_authenticate(user=admin)
    resp = api_client.get(schedule_url(resource.pk), {'date': 'not-a-date'})
    assert resp.status_code == 400
    assert 'Invalid date format' in resp.json()['detail']


@pytest.mark.django_db
def test_schedule_start_end_have_timezone_offset(api_client, admin, resource):
    """start and end must contain '+05:00', not 'Z'."""
    _make_booking(resource, admin, start_offset_hours=1, duration_hours=1)

    api_client.force_authenticate(user=admin)
    local_tz = timezone.get_current_timezone()
    today = timezone.now().astimezone(local_tz).date().isoformat()
    resp = api_client.get(schedule_url(resource.pk), {'date': today})

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    slot = data[0]
    assert '+05:00' in slot['start'], f"Expected +05:00 offset in start, got: {slot['start']}"
    assert '+05:00' in slot['end'], f"Expected +05:00 offset in end, got: {slot['end']}"
    assert not slot['start'].endswith('Z'), f"start should not end with Z: {slot['start']}"
    assert not slot['end'].endswith('Z'), f"end should not end with Z: {slot['end']}"


@pytest.mark.django_db
def test_schedule_empty_day_returns_empty_list(api_client, admin, resource):
    """A day with no bookings returns an empty list."""
    api_client.force_authenticate(user=admin)
    resp = api_client.get(schedule_url(resource.pk), {'date': '2020-01-01'})
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.django_db
def test_unauthenticated_returns_401(api_client, resource):
    """Unauthenticated requests must get 401."""
    resp = api_client.get(schedule_url(resource.pk))
    assert resp.status_code == 401


@pytest.mark.django_db
def test_schedule_response_shape(api_client, admin, resource):
    """Each slot must have exactly booking_id, start, end, status fields."""
    _make_booking(resource, admin, start_offset_hours=1, duration_hours=1)

    api_client.force_authenticate(user=admin)
    local_tz = timezone.get_current_timezone()
    today = timezone.now().astimezone(local_tz).date().isoformat()
    resp = api_client.get(schedule_url(resource.pk), {'date': today})

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    slot = data[0]
    assert set(slot.keys()) == {'booking_id', 'start', 'end', 'status'}
    assert isinstance(slot['booking_id'], int)
    assert slot['status'] in ('occupied', 'soon_available')


@pytest.mark.django_db
def test_schedule_guest_returns_403(api_client, db, resource):
    """Guest users must get 403."""
    guest = User.objects.create_user(
        email='guest@sched.test',
        password='pass',
        first_name='G',
        last_name='U',
        role='guest',
    )
    api_client.force_authenticate(user=guest)
    resp = api_client.get(schedule_url(resource.pk))
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# ?week= parameter tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_schedule_week_returns_slots_across_seven_days(api_client, admin, resource):
    """?week= must return all confirmed bookings within the Mon–Sun week of the given date."""
    now = timezone.now()
    local_tz = timezone.get_current_timezone()
    today = now.astimezone(local_tz).date()
    monday = today - timedelta(days=today.weekday())

    # Two bookings on different days of the same week
    for day_offset in (0, 3):  # Monday and Thursday
        day = monday + timedelta(days=day_offset)
        start = timezone.make_aware(
            datetime(day.year, day.month, day.day, 10, 0), local_tz
        )
        Booking.objects.create(
            resource=resource,
            user=admin,
            company=admin.company,
            start_time=start,
            end_time=start + timedelta(hours=1),
            status='confirmed',
        )

    api_client.force_authenticate(user=admin)
    resp = api_client.get(schedule_url(resource.pk), {'week': monday.isoformat()})

    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.django_db
def test_schedule_week_excludes_other_weeks(api_client, admin, resource):
    """Bookings outside the requested week must not appear in the response."""
    now = timezone.now()
    local_tz = timezone.get_current_timezone()
    today = now.astimezone(local_tz).date()
    monday = today - timedelta(days=today.weekday())
    next_monday = monday + timedelta(weeks=1)

    # Booking on next week's Monday
    next_week_start = timezone.make_aware(
        datetime(next_monday.year, next_monday.month, next_monday.day, 10, 0), local_tz
    )
    Booking.objects.create(
        resource=resource,
        user=admin,
        company=admin.company,
        start_time=next_week_start,
        end_time=next_week_start + timedelta(hours=1),
        status='confirmed',
    )

    api_client.force_authenticate(user=admin)
    resp = api_client.get(schedule_url(resource.pk), {'week': monday.isoformat()})

    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.django_db
def test_schedule_week_invalid_format_returns_400(api_client, admin, resource):
    """Invalid ?week= format must return 400 with a helpful message."""
    api_client.force_authenticate(user=admin)
    resp = api_client.get(schedule_url(resource.pk), {'week': 'not-a-date'})
    assert resp.status_code == 400
    assert 'Invalid week format' in resp.json()['detail']


@pytest.mark.django_db
def test_schedule_week_takes_priority_over_date(api_client, admin, resource):
    """When both ?week= and ?date= are supplied, ?week= wins."""
    now = timezone.now()
    local_tz = timezone.get_current_timezone()
    today = now.astimezone(local_tz).date()
    monday = today - timedelta(days=today.weekday())

    # Booking on Monday of this week
    week_start = timezone.make_aware(
        datetime(monday.year, monday.month, monday.day, 9, 0), local_tz
    )
    Booking.objects.create(
        resource=resource,
        user=admin,
        company=admin.company,
        start_time=week_start,
        end_time=week_start + timedelta(hours=1),
        status='confirmed',
    )

    api_client.force_authenticate(user=admin)
    # ?week= covers this week; ?date= points to a week ago (no booking there)
    last_week_monday = monday - timedelta(weeks=1)
    resp = api_client.get(schedule_url(resource.pk), {
        'week': monday.isoformat(),
        'date': last_week_monday.isoformat(),
    })

    assert resp.status_code == 200
    # week wins — Monday booking must appear
    assert len(resp.json()) == 1


@pytest.mark.django_db
def test_schedule_week_past_week_returns_occupied_not_error(api_client, admin, resource):
    """Slots from a past week return 200 with status 'occupied' and do not raise errors."""
    past_monday = date(2020, 1, 6)  # A known Monday in the past
    local_tz = timezone.get_current_timezone()
    start = timezone.make_aware(
        datetime(2020, 1, 7, 10, 0), local_tz  # Tuesday
    )
    Booking.objects.create(
        resource=resource,
        user=admin,
        company=admin.company,
        start_time=start,
        end_time=start + timedelta(hours=1),
        status='confirmed',
    )

    api_client.force_authenticate(user=admin)
    resp = api_client.get(schedule_url(resource.pk), {'week': past_monday.isoformat()})

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]['status'] == 'occupied'


@pytest.mark.django_db
def test_schedule_week_mid_week_anchor_resolves_to_monday(api_client, admin, resource):
    """?week= with a Wednesday anchor must return the same Mon–Sun week as using Monday."""
    now = timezone.now()
    local_tz = timezone.get_current_timezone()
    today = now.astimezone(local_tz).date()
    monday = today - timedelta(days=today.weekday())
    wednesday = monday + timedelta(days=2)

    # Booking on Friday of this week
    friday = monday + timedelta(days=4)
    fri_start = timezone.make_aware(
        datetime(friday.year, friday.month, friday.day, 14, 0), local_tz
    )
    Booking.objects.create(
        resource=resource,
        user=admin,
        company=admin.company,
        start_time=fri_start,
        end_time=fri_start + timedelta(hours=1),
        status='confirmed',
    )

    api_client.force_authenticate(user=admin)
    resp_monday = api_client.get(schedule_url(resource.pk), {'week': monday.isoformat()})
    resp_wednesday = api_client.get(schedule_url(resource.pk), {'week': wednesday.isoformat()})

    assert resp_monday.status_code == 200
    assert resp_wednesday.status_code == 200
    assert resp_monday.json() == resp_wednesday.json()
