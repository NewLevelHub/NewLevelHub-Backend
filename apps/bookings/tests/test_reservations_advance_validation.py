"""
Tests for advance_booking_days validation on POST /api/v1/bookings/reservations/
and PATCH /api/v1/bookings/reservations/<id>/.

Regression coverage for the bug where _validate_type_specific_rules was only
invoked inside BookingCreateSerializer.create() rather than in validate(), which
meant any code path that called is_valid() without save() (or PATCH via
partial_update) would silently bypass the advance-day limit.
"""
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.bookings.models import Booking, Resource
from apps.companies.models import Company
from apps.users.models import User

RESERVATIONS_URL = '/api/v1/bookings/reservations/'


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='AdvanceValidation Co', plan='basic')


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='emp_advance@advance.test',
        password='pass',
        first_name='Adv',
        last_name='Employee',
        role='employee',
        company=company,
        is_email_verified=True,
    )


def _make_desk(advance_booking_days=3, **kwargs):
    defaults = dict(
        name='Advance Test Desk',
        resource_type='desk',
        advance_booking_days=advance_booking_days,
        available_days=list(range(7)),
        available_from='00:00',
        available_until='23:59',
    )
    defaults.update(kwargs)
    return Resource.objects.create(**defaults)


def _make_parking(advance_booking_days=3, **kwargs):
    defaults = dict(
        name='Advance Test Parking',
        resource_type='parking',
        advance_booking_days=advance_booking_days,
        available_days=list(range(7)),
        available_from='00:00',
        available_until='23:59',
    )
    defaults.update(kwargs)
    return Resource.objects.create(**defaults)


def _local_start(days_ahead, hour=10, minute=0):
    """Return a tz-aware local datetime `days_ahead` days from now at the given time."""
    return (
        timezone.localtime() + timedelta(days=days_ahead)
    ).replace(hour=hour, minute=minute, second=0, microsecond=0)


# ---------------------------------------------------------------------------
# POST — desk advance booking validation
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCreateReservationAdvanceValidation:
    """advance_booking_days is enforced on POST /api/v1/bookings/reservations/."""

    def test_desk_too_far_in_future_returns_400(self, api_client, employee):
        """
        Resource has advance_booking_days=3.
        Booking start_time is 4 days from now → must be rejected with 400.
        """
        resource = _make_desk(advance_booking_days=3)
        api_client.force_authenticate(user=employee)

        start = _local_start(days_ahead=4)
        end = start + timedelta(hours=1)

        resp = api_client.post(
            RESERVATIONS_URL,
            {
                'resource_id': resource.id,
                'start_time': start.isoformat(),
                'end_time': end.isoformat(),
            },
            format='json',
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST, resp.json()

    def test_desk_within_advance_days_returns_201(self, api_client, employee):
        """
        Resource has advance_booking_days=3.
        Booking start_time is 2 days from now → must be accepted with 201.
        """
        resource = _make_desk(advance_booking_days=3)
        api_client.force_authenticate(user=employee)

        start = _local_start(days_ahead=2)
        end = start + timedelta(hours=1)

        resp = api_client.post(
            RESERVATIONS_URL,
            {
                'resource_id': resource.id,
                'start_time': start.isoformat(),
                'end_time': end.isoformat(),
            },
            format='json',
        )

        assert resp.status_code == status.HTTP_201_CREATED, resp.json()

    def test_desk_69_days_ahead_default_14_returns_400(self, api_client, employee):
        """
        Resource has advance_booking_days=14 (model default).
        Booking start_time is 69 days from now — the reported regression case.
        Must be rejected with 400.
        """
        resource = _make_desk(advance_booking_days=14)
        api_client.force_authenticate(user=employee)

        start = _local_start(days_ahead=69)
        end = start + timedelta(hours=1)

        resp = api_client.post(
            RESERVATIONS_URL,
            {
                'resource_id': resource.id,
                'start_time': start.isoformat(),
                'end_time': end.isoformat(),
            },
            format='json',
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST, resp.json()

    def test_error_message_contains_advance_days_count(self, api_client, employee):
        """The 400 response detail must mention the configured advance_booking_days value."""
        resource = _make_desk(advance_booking_days=3)
        api_client.force_authenticate(user=employee)

        start = _local_start(days_ahead=10)
        end = start + timedelta(hours=1)

        resp = api_client.post(
            RESERVATIONS_URL,
            {
                'resource_id': resource.id,
                'start_time': start.isoformat(),
                'end_time': end.isoformat(),
            },
            format='json',
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        body_str = str(resp.json())
        assert '3' in body_str, f'Expected advance_days count "3" in error body: {body_str}'

    def test_parking_too_far_in_future_returns_400(self, api_client, employee):
        """
        Parking resource has advance_booking_days=3.
        Booking start_time is 4 days from now (whole-day) → must be rejected with 400.
        """
        resource = _make_parking(advance_booking_days=3)
        api_client.force_authenticate(user=employee)

        start = _local_start(days_ahead=4, hour=0, minute=0)
        end = (start + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

        resp = api_client.post(
            RESERVATIONS_URL,
            {
                'resource_id': resource.id,
                'start_time': start.isoformat(),
                'end_time': end.isoformat(),
            },
            format='json',
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST, resp.json()

    def test_parking_within_advance_days_returns_201(self, api_client, employee):
        """
        Parking resource has advance_booking_days=3.
        Booking start_time is 2 days from now (whole-day) → must be accepted with 201.
        """
        resource = _make_parking(advance_booking_days=3)
        api_client.force_authenticate(user=employee)

        start = _local_start(days_ahead=2, hour=0, minute=0)
        end = (start + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

        resp = api_client.post(
            RESERVATIONS_URL,
            {
                'resource_id': resource.id,
                'start_time': start.isoformat(),
                'end_time': end.isoformat(),
            },
            format='json',
        )

        assert resp.status_code == status.HTTP_201_CREATED, resp.json()


# ---------------------------------------------------------------------------
# PATCH — advance booking validation also applies to time updates
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestPatchReservationAdvanceValidation:
    """
    advance_booking_days must be re-validated when a booking's time is updated
    via PATCH /api/v1/bookings/reservations/<id>/.
    """

    def _create_booking_via_api(self, api_client, user, resource, days_ahead=1):
        """Helper that creates a valid (within-window) booking and returns its ID."""
        start = _local_start(days_ahead=days_ahead)
        end = start + timedelta(hours=1)
        resp = api_client.post(
            RESERVATIONS_URL,
            {
                'resource_id': resource.id,
                'start_time': start.isoformat(),
                'end_time': end.isoformat(),
            },
            format='json',
        )
        assert resp.status_code == status.HTTP_201_CREATED, resp.json()
        return resp.json()['id']

    def test_patch_rescheduling_beyond_advance_days_returns_400(self, api_client, employee):
        """
        A booking created within the advance window must be rejected if PATCHed
        to a date that exceeds advance_booking_days.
        """
        resource = _make_desk(advance_booking_days=3)
        api_client.force_authenticate(user=employee)

        booking_id = self._create_booking_via_api(api_client, employee, resource, days_ahead=1)

        # Attempt to reschedule to 10 days ahead (beyond 3-day limit)
        new_start = _local_start(days_ahead=10)
        new_end = new_start + timedelta(hours=1)

        resp = api_client.patch(
            f'{RESERVATIONS_URL}{booking_id}/',
            {
                'start_time': new_start.isoformat(),
                'end_time': new_end.isoformat(),
            },
            format='json',
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST, resp.json()

    def test_patch_rescheduling_within_advance_days_returns_200(self, api_client, employee):
        """
        Rescheduling a booking to a date still within advance_booking_days succeeds.
        """
        resource = _make_desk(advance_booking_days=5)
        api_client.force_authenticate(user=employee)

        booking_id = self._create_booking_via_api(api_client, employee, resource, days_ahead=1)

        # Reschedule to 4 days ahead (within 5-day limit)
        new_start = _local_start(days_ahead=4)
        new_end = new_start + timedelta(hours=1)

        resp = api_client.patch(
            f'{RESERVATIONS_URL}{booking_id}/',
            {
                'start_time': new_start.isoformat(),
                'end_time': new_end.isoformat(),
            },
            format='json',
        )

        assert resp.status_code == status.HTTP_200_OK, resp.json()
        booking = Booking.objects.get(pk=booking_id)
        assert booking.start_time == new_start
