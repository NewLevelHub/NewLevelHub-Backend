"""
Tests for type-specific booking validation rules and resource-level
min/max_duration validation.

Rules:
  - Desk: start_time must be within 14 days from now
  - Meeting room: min 30 min, max 4 hours duration
  - Parking: whole-day only (00:00–23:59 or next day 00:00), max 7 days ahead
  - Capsule: min 1h, max 8h duration
  - Resource-level min_duration / max_duration override
"""
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.bookings.models import Resource
from apps.companies.models import Company
from apps.users.models import User

RESERVATIONS_URL = '/api/v1/bookings/reservations/'


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='Validation Co', plan='basic')


@pytest.fixture
def employee(company):
    return User.objects.create_user(
        email='val_employee@test.com',
        password='pass',
        first_name='Val',
        last_name='Emp',
        role='employee',
        company=company,
        is_email_verified=True,
    )


def _next_weekday(days_ahead=1):
    """Return a future date that is Mon-Fri, at least `days_ahead` from now."""
    now = timezone.localtime()
    target = now + timedelta(days=days_ahead)
    # Shift to Monday if weekend
    while target.weekday() >= 5:
        target += timedelta(days=1)
    return target


def _make_resource(resource_type, **kwargs):
    defaults = {
        'name': f'Test {resource_type}',
        'resource_type': resource_type,
        'available_days': [0, 1, 2, 3, 4],
        'available_from': '00:00',
        'available_until': '23:59',
    }
    defaults.update(kwargs)
    return Resource.objects.create(**defaults)


# ─── Desk validation ───────────────────────────────────────────────────

@pytest.mark.django_db
class TestDeskValidation:
    """Desk: start_time must be within 14 days from now."""

    def test_desk_booking_within_14_days_succeeds(self, api_client, employee):
        resource = _make_resource('desk')
        api_client.force_authenticate(user=employee)
        day = _next_weekday(1)
        start = day.replace(hour=10, minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=1)

        resp = api_client.post(RESERVATIONS_URL, {
            'resource_id': resource.id,
            'start_time': start.isoformat(),
            'end_time': end.isoformat(),
        }, format='json')

        assert resp.status_code == status.HTTP_201_CREATED

    def test_desk_booking_beyond_14_days_returns_400(self, api_client, employee):
        resource = _make_resource('desk')
        api_client.force_authenticate(user=employee)
        day = _next_weekday(15)
        start = day.replace(hour=10, minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=1)

        resp = api_client.post(RESERVATIONS_URL, {
            'resource_id': resource.id,
            'start_time': start.isoformat(),
            'end_time': end.isoformat(),
        }, format='json')

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert '14 days' in str(resp.json()).lower() or '14' in str(resp.json())

    def test_desk_booking_exactly_14_days_ahead_succeeds(self, api_client, employee):
        """D-02: boundary is inclusive — exactly now + 14 days must return 201."""
        # Allow all 7 days so weekend boundary dates are not rejected
        resource = _make_resource('desk', available_days=list(range(7)))
        api_client.force_authenticate(user=employee)
        now = timezone.now()
        # Exactly 14 days from now (truncate sub-second so it does not exceed boundary)
        start = (now + timedelta(days=14)).replace(second=0, microsecond=0)
        end = start + timedelta(hours=1)

        resp = api_client.post(RESERVATIONS_URL, {
            'resource_id': resource.id,
            'start_time': start.isoformat(),
            'end_time': end.isoformat(),
        }, format='json')

        assert resp.status_code == status.HTTP_201_CREATED, resp.json()

    def test_desk_booking_15_days_returns_400(self, api_client, employee):
        """D-02 extra: one full day past the boundary must return 400 (boundary is date-based)."""
        # today+14d (any time) is valid; today+15d is not
        resource = _make_resource('desk', available_days=list(range(7)))
        api_client.force_authenticate(user=employee)
        now = timezone.now()
        start = (now + timedelta(days=15)).replace(microsecond=0)
        end = start + timedelta(hours=1)

        resp = api_client.post(RESERVATIONS_URL, {
            'resource_id': resource.id,
            'start_time': start.isoformat(),
            'end_time': end.isoformat(),
        }, format='json')

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert '14 days' in str(resp.json()).lower() or '14' in str(resp.json())


# ─── Past start_time validation ───────────────────────────────────────

@pytest.mark.django_db
class TestPastStartTimeValidation:
    """D-04: booking start_time in the past must return 400 for all resource types."""

    def test_desk_start_in_past_returns_400(self, api_client, employee):
        # available_days covers all days so weekday is never the rejection reason
        resource = _make_resource('desk', available_days=list(range(7)))
        api_client.force_authenticate(user=employee)
        now = timezone.now()
        start = (now - timedelta(hours=1)).replace(microsecond=0)
        end = start + timedelta(hours=1)

        resp = api_client.post(RESERVATIONS_URL, {
            'resource_id': resource.id,
            'start_time': start.isoformat(),
            'end_time': end.isoformat(),
        }, format='json')

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert 'future' in str(resp.json()).lower()

    def test_desk_start_yesterday_returns_400(self, api_client, employee):
        resource = _make_resource('desk', available_days=list(range(7)))
        api_client.force_authenticate(user=employee)
        now = timezone.now()
        start = (now - timedelta(days=1)).replace(microsecond=0)
        end = start + timedelta(hours=1)

        resp = api_client.post(RESERVATIONS_URL, {
            'resource_id': resource.id,
            'start_time': start.isoformat(),
            'end_time': end.isoformat(),
        }, format='json')

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert 'future' in str(resp.json()).lower()

    def test_meeting_room_start_in_past_returns_400(self, api_client, employee):
        resource = _make_resource('meeting_room', capacity=4, available_days=list(range(7)))
        api_client.force_authenticate(user=employee)
        now = timezone.now()
        start = (now - timedelta(hours=1)).replace(microsecond=0)
        end = start + timedelta(hours=1)

        resp = api_client.post(RESERVATIONS_URL, {
            'resource_id': resource.id,
            'start_time': start.isoformat(),
            'end_time': end.isoformat(),
        }, format='json')

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert 'future' in str(resp.json()).lower()


# ─── Meeting room validation ──────────────────────────────────────────

@pytest.mark.django_db
class TestMeetingRoomValidation:
    """Meeting room: min 30 min, max 4 hours duration."""

    def test_meeting_room_30_min_succeeds(self, api_client, employee):
        resource = _make_resource('meeting_room', capacity=4)
        api_client.force_authenticate(user=employee)
        day = _next_weekday(1)
        start = day.replace(hour=10, minute=0, second=0, microsecond=0)
        end = start + timedelta(minutes=30)

        resp = api_client.post(RESERVATIONS_URL, {
            'resource_id': resource.id,
            'start_time': start.isoformat(),
            'end_time': end.isoformat(),
        }, format='json')

        assert resp.status_code == status.HTTP_201_CREATED

    def test_meeting_room_4_hours_succeeds(self, api_client, employee):
        resource = _make_resource('meeting_room', capacity=4)
        api_client.force_authenticate(user=employee)
        day = _next_weekday(1)
        start = day.replace(hour=10, minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=4)

        resp = api_client.post(RESERVATIONS_URL, {
            'resource_id': resource.id,
            'start_time': start.isoformat(),
            'end_time': end.isoformat(),
        }, format='json')

        assert resp.status_code == status.HTTP_201_CREATED

    def test_meeting_room_under_30_min_returns_400(self, api_client, employee):
        resource = _make_resource('meeting_room', capacity=4)
        api_client.force_authenticate(user=employee)
        day = _next_weekday(1)
        start = day.replace(hour=10, minute=0, second=0, microsecond=0)
        end = start + timedelta(minutes=20)

        resp = api_client.post(RESERVATIONS_URL, {
            'resource_id': resource.id,
            'start_time': start.isoformat(),
            'end_time': end.isoformat(),
        }, format='json')

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert '30 min' in str(resp.json()).lower() or 'minimum' in str(resp.json()).lower()

    def test_meeting_room_over_4_hours_returns_400(self, api_client, employee):
        resource = _make_resource('meeting_room', capacity=4)
        api_client.force_authenticate(user=employee)
        day = _next_weekday(1)
        start = day.replace(hour=10, minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=5)

        resp = api_client.post(RESERVATIONS_URL, {
            'resource_id': resource.id,
            'start_time': start.isoformat(),
            'end_time': end.isoformat(),
        }, format='json')

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert '4 hour' in str(resp.json()).lower() or 'maximum' in str(resp.json()).lower()


# ─── Parking validation ───────────────────────────────────────────────

@pytest.mark.django_db
class TestParkingValidation:
    """Parking: whole-day only (00:00–23:59 or next day 00:00), max 7 days ahead."""

    def test_parking_whole_day_succeeds(self, api_client, employee):
        resource = _make_resource('parking')
        api_client.force_authenticate(user=employee)
        day = _next_weekday(1)
        start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        end = (start + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

        resp = api_client.post(RESERVATIONS_URL, {
            'resource_id': resource.id,
            'start_time': start.isoformat(),
            'end_time': end.isoformat(),
        }, format='json')

        assert resp.status_code == status.HTTP_201_CREATED

    def test_parking_partial_day_returns_400(self, api_client, employee):
        resource = _make_resource('parking')
        api_client.force_authenticate(user=employee)
        day = _next_weekday(1)
        start = day.replace(hour=9, minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=3)

        resp = api_client.post(RESERVATIONS_URL, {
            'resource_id': resource.id,
            'start_time': start.isoformat(),
            'end_time': end.isoformat(),
        }, format='json')

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert 'whole day' in str(resp.json()).lower() or 'whole-day' in str(resp.json()).lower()

    def test_parking_beyond_7_days_returns_400(self, api_client, employee):
        # Explicitly set advance_booking_days=7 to test the 7-day enforcement.
        # The model default is 14 (shared across types); each resource controls its own window.
        resource = _make_resource('parking', advance_booking_days=7)
        api_client.force_authenticate(user=employee)
        day = _next_weekday(8)
        start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        end = (start + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

        resp = api_client.post(RESERVATIONS_URL, {
            'resource_id': resource.id,
            'start_time': start.isoformat(),
            'end_time': end.isoformat(),
        }, format='json')

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert '7 days' in str(resp.json()).lower() or '7' in str(resp.json())

    def test_parking_within_7_days_whole_day_succeeds(self, api_client, employee):
        resource = _make_resource('parking')
        api_client.force_authenticate(user=employee)
        day = _next_weekday(2)
        start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        end = (start + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

        resp = api_client.post(RESERVATIONS_URL, {
            'resource_id': resource.id,
            'start_time': start.isoformat(),
            'end_time': end.isoformat(),
        }, format='json')

        assert resp.status_code == status.HTTP_201_CREATED


# ─── Capsule validation ───────────────────────────────────────────────

@pytest.mark.django_db
class TestCapsuleValidation:
    """Capsule: min 1h, max 8h duration."""

    def test_capsule_1_hour_succeeds(self, api_client, employee):
        resource = _make_resource('capsule', capsule_zone='quiet')
        api_client.force_authenticate(user=employee)
        day = _next_weekday(1)
        start = day.replace(hour=10, minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=1)

        resp = api_client.post(RESERVATIONS_URL, {
            'resource_id': resource.id,
            'start_time': start.isoformat(),
            'end_time': end.isoformat(),
        }, format='json')

        assert resp.status_code == status.HTTP_201_CREATED

    def test_capsule_under_1_hour_returns_400(self, api_client, employee):
        resource = _make_resource('capsule', capsule_zone='quiet')
        api_client.force_authenticate(user=employee)
        day = _next_weekday(1)
        start = day.replace(hour=10, minute=0, second=0, microsecond=0)
        end = start + timedelta(minutes=45)

        resp = api_client.post(RESERVATIONS_URL, {
            'resource_id': resource.id,
            'start_time': start.isoformat(),
            'end_time': end.isoformat(),
        }, format='json')

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert '1 hour' in str(resp.json()).lower() or 'minimum' in str(resp.json()).lower()

    def test_capsule_over_8_hours_returns_400(self, api_client, employee):
        resource = _make_resource('capsule', capsule_zone='quiet')
        api_client.force_authenticate(user=employee)
        day = _next_weekday(1)
        start = day.replace(hour=10, minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=9)

        resp = api_client.post(RESERVATIONS_URL, {
            'resource_id': resource.id,
            'start_time': start.isoformat(),
            'end_time': end.isoformat(),
        }, format='json')

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert '8 hour' in str(resp.json()).lower() or 'maximum' in str(resp.json()).lower()

    def test_capsule_8_hours_succeeds(self, api_client, employee):
        resource = _make_resource('capsule', capsule_zone='quiet')
        api_client.force_authenticate(user=employee)
        day = _next_weekday(1)
        start = day.replace(hour=10, minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=8)

        resp = api_client.post(RESERVATIONS_URL, {
            'resource_id': resource.id,
            'start_time': start.isoformat(),
            'end_time': end.isoformat(),
        }, format='json')

        assert resp.status_code == status.HTTP_201_CREATED


# ─── Resource-level min/max duration ──────────────────────────────────

@pytest.mark.django_db
class TestResourceLevelDurationValidation:
    """Resource min_duration_minutes and max_duration_minutes override."""

    def test_booking_shorter_than_resource_min_duration_returns_400(self, api_client, employee):
        resource = _make_resource(
            'desk',
            min_duration_minutes=60,
            max_duration_minutes=480,
        )
        api_client.force_authenticate(user=employee)
        day = _next_weekday(1)
        start = day.replace(hour=10, minute=0, second=0, microsecond=0)
        end = start + timedelta(minutes=30)

        resp = api_client.post(RESERVATIONS_URL, {
            'resource_id': resource.id,
            'start_time': start.isoformat(),
            'end_time': end.isoformat(),
        }, format='json')

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        body = str(resp.json()).lower()
        assert 'minimum booking duration' in body or 'min' in body

    def test_booking_longer_than_resource_max_duration_returns_400(self, api_client, employee):
        resource = _make_resource(
            'desk',
            min_duration_minutes=30,
            max_duration_minutes=120,
        )
        api_client.force_authenticate(user=employee)
        day = _next_weekday(1)
        start = day.replace(hour=10, minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=3)

        resp = api_client.post(RESERVATIONS_URL, {
            'resource_id': resource.id,
            'start_time': start.isoformat(),
            'end_time': end.isoformat(),
        }, format='json')

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        body = str(resp.json()).lower()
        assert 'maximum booking duration' in body or 'max' in body

    def test_booking_within_resource_duration_limits_succeeds(self, api_client, employee):
        resource = _make_resource(
            'desk',
            min_duration_minutes=30,
            max_duration_minutes=120,
        )
        api_client.force_authenticate(user=employee)
        day = _next_weekday(1)
        start = day.replace(hour=10, minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=1)

        resp = api_client.post(RESERVATIONS_URL, {
            'resource_id': resource.id,
            'start_time': start.isoformat(),
            'end_time': end.isoformat(),
        }, format='json')

        assert resp.status_code == status.HTTP_201_CREATED


# ─── Auth checks ──────────────────────────────────────────────────────

@pytest.mark.django_db
class TestBookingValidationAuth:
    def test_unauthenticated_returns_401(self, api_client):
        resp = api_client.post(RESERVATIONS_URL, {}, format='json')
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_guest_returns_403(self, api_client, company):
        guest = User.objects.create_user(
            email='guest@test.com',
            password='pass',
            first_name='G',
            last_name='G',
            role='guest',
            is_email_verified=True,
        )
        api_client.force_authenticate(user=guest)
        resp = api_client.post(RESERVATIONS_URL, {}, format='json')
        assert resp.status_code == status.HTTP_403_FORBIDDEN
