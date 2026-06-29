"""
Tests for QA WARN fixes on DEV-73 bookings:

B-15 — naive datetime without timezone info must return 400
B-21 — GET /reservations/my/?user=<id> must always return only request.user's bookings
B-26 — cancel scenario must use stable, explicit dates (not random slots)
B-28 — employee cannot cancel another user's booking (must be 403)
"""
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.bookings.models import Booking, Resource
from apps.companies.models import Company
from apps.notifications.models import Notification
from apps.users.models import User

RESERVATIONS_URL = '/api/v1/bookings/reservations/'
MY_BOOKINGS_URL = '/api/v1/bookings/reservations/my/'


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='QA Fix Co', plan='basic')


@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email='superadmin@qa.test',
        password='pass',
        first_name='Super',
        last_name='Admin',
        role='superadmin',
        is_email_verified=True,
    )


@pytest.fixture
def company_admin(db, company):
    return User.objects.create_user(
        email='admin@qa.test',
        password='pass',
        first_name='Company',
        last_name='Admin',
        role='company_admin',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='employee@qa.test',
        password='pass',
        first_name='Emp',
        last_name='Loyee',
        role='employee',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def other_employee(db, company):
    return User.objects.create_user(
        email='other_employee@qa.test',
        password='pass',
        first_name='Other',
        last_name='Employee',
        role='employee',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def stable_resource(db):
    """A resource with wide availability covering our fixed test slots."""
    return Resource.objects.create(
        name='Stable QA Resource',
        resource_type='desk',
        assigned_company=None,
        available_days=[0, 1, 2, 3, 4, 5, 6],
        available_from='08:00',
        available_until='22:00',
    )


def _fixed_future_slot(hours_from_now=24):
    """Return a tz-aware datetime that is always inside resource availability hours."""
    now = timezone.now()
    candidate = now + timedelta(hours=hours_from_now)
    local = timezone.localtime(candidate)
    # Snap to 10:00 on that day to ensure it's within 08:00-22:00
    target = local.replace(hour=10, minute=0, second=0, microsecond=0)
    return target


# ---------------------------------------------------------------------------
# B-15: naive datetime without timezone info must return 400
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestNaiveDatetimeRejected:
    """B-15 — naive datetime (no tz offset) must be rejected with 400."""

    def test_naive_start_time_returns_400(self, api_client, employee, stable_resource):
        api_client.force_authenticate(user=employee)
        # Send an ISO string with NO timezone offset — naive datetime
        response = api_client.post(
            RESERVATIONS_URL,
            {
                'resource_id': stable_resource.id,
                'start_time': '2025-04-16T10:00:00',  # No +05:00 or Z
                'end_time': '2025-04-16T11:00:00+05:00',
            },
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_naive_end_time_returns_400(self, api_client, employee, stable_resource):
        api_client.force_authenticate(user=employee)
        response = api_client.post(
            RESERVATIONS_URL,
            {
                'resource_id': stable_resource.id,
                'start_time': '2025-04-16T10:00:00+05:00',
                'end_time': '2025-04-16T11:00:00',  # No tz
            },
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_naive_both_times_returns_400(self, api_client, employee, stable_resource):
        api_client.force_authenticate(user=employee)
        response = api_client.post(
            RESERVATIONS_URL,
            {
                'resource_id': stable_resource.id,
                'start_time': '2025-04-16T10:00:00',
                'end_time': '2025-04-16T11:00:00',
            },
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_tz_aware_datetime_accepted(self, api_client, employee, stable_resource):
        """Sanity check — tz-aware datetime must still work."""
        api_client.force_authenticate(user=employee)
        start = _fixed_future_slot(hours_from_now=25)
        end = start + timedelta(hours=1)
        response = api_client.post(
            RESERVATIONS_URL,
            {
                'resource_id': stable_resource.id,
                'start_time': start.isoformat(),
                'end_time': end.isoformat(),
            },
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED


# ---------------------------------------------------------------------------
# B-21: GET /reservations/my/ must ignore ?user= query param
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestMyBookingsIgnoresUserParam:
    """B-21 — my_bookings rejects ?user= with 400; always returns only request.user's bookings."""

    def test_my_bookings_user_param_returns_400(
        self, api_client, employee, other_employee, stable_resource, company
    ):
        """Passing ?user=<other_id> must return 400 — the parameter is unsupported."""
        start = _fixed_future_slot(hours_from_now=49)
        Booking.objects.create(
            resource=stable_resource,
            user=other_employee,
            company=company,
            start_time=start,
            end_time=start + timedelta(hours=1),
            status='confirmed',
        )

        api_client.force_authenticate(user=employee)
        response = api_client.get(MY_BOOKINGS_URL, {'user': other_employee.id})
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_my_bookings_nonexistent_user_param_returns_400(
        self, api_client, employee, stable_resource, company
    ):
        """Passing ?user=999999999 (nonexistent) must return 400 — parameter is unsupported."""
        start = _fixed_future_slot(hours_from_now=73)
        Booking.objects.create(
            resource=stable_resource,
            user=employee,
            company=company,
            start_time=start,
            end_time=start + timedelta(hours=1),
            status='confirmed',
        )

        api_client.force_authenticate(user=employee)
        response = api_client.get(MY_BOOKINGS_URL, {'user': 999999999})
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_my_bookings_returns_only_own(
        self, api_client, employee, other_employee, stable_resource, company
    ):
        """No query param — each user sees only their own bookings."""
        start_e = _fixed_future_slot(hours_from_now=97)
        start_o = _fixed_future_slot(hours_from_now=121)
        Booking.objects.create(
            resource=stable_resource,
            user=employee,
            company=company,
            start_time=start_e,
            end_time=start_e + timedelta(hours=1),
            status='confirmed',
        )
        Booking.objects.create(
            resource=stable_resource,
            user=other_employee,
            company=company,
            start_time=start_o,
            end_time=start_o + timedelta(hours=1),
            status='confirmed',
        )

        api_client.force_authenticate(user=employee)
        response = api_client.get(MY_BOOKINGS_URL)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        results = data.get('results', data) if isinstance(data, dict) else data
        user_ids = {r['user']['id'] for r in results}
        assert user_ids == {employee.id}


# ---------------------------------------------------------------------------
# B-26: Cancel scenario — stable booking with fixed future dates
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCancelBookingStable:
    """B-26 — cancel booking uses stable, fixed dates that always fit the availability window."""

    def test_owner_can_cancel_own_booking(
        self, api_client, employee, stable_resource, company
    ):
        start = _fixed_future_slot(hours_from_now=145)
        end = start + timedelta(hours=1)
        booking = Booking.objects.create(
            resource=stable_resource,
            user=employee,
            company=company,
            start_time=start,
            end_time=end,
            status='confirmed',
        )
        api_client.force_authenticate(user=employee)
        response = api_client.post(
            f'{RESERVATIONS_URL}{booking.id}/cancel/',
            {'reason': 'Changed plans'},
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        booking.refresh_from_db()
        assert booking.status == 'cancelled'
        assert booking.cancel_reason == 'Changed plans'
        assert booking.cancelled_by == employee
        assert Notification.objects.filter(
            user=employee,
            notification_type='booking_cancelled',
            url=f'/bookings/{booking.id}',
        ).exists()

    def test_company_admin_can_cancel_employee_booking(
        self, api_client, company_admin, employee, stable_resource, company
    ):
        start = _fixed_future_slot(hours_from_now=169)
        end = start + timedelta(hours=1)
        booking = Booking.objects.create(
            resource=stable_resource,
            user=employee,
            company=company,
            start_time=start,
            end_time=end,
            status='confirmed',
        )
        api_client.force_authenticate(user=company_admin)
        response = api_client.post(
            f'{RESERVATIONS_URL}{booking.id}/cancel/',
            {'reason': 'Admin override'},
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        booking.refresh_from_db()
        assert booking.status == 'cancelled'
        assert Notification.objects.filter(
            user=employee,
            notification_type='booking_cancelled',
        ).exists()

    def test_superadmin_can_cancel_any_booking(
        self, api_client, superadmin, employee, stable_resource, company
    ):
        start = _fixed_future_slot(hours_from_now=193)
        end = start + timedelta(hours=1)
        booking = Booking.objects.create(
            resource=stable_resource,
            user=employee,
            company=company,
            start_time=start,
            end_time=end,
            status='confirmed',
        )
        api_client.force_authenticate(user=superadmin)
        response = api_client.post(
            f'{RESERVATIONS_URL}{booking.id}/cancel/',
            {'reason': 'Superadmin override'},
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        booking.refresh_from_db()
        assert booking.status == 'cancelled'


# ---------------------------------------------------------------------------
# B-28: Employee cannot cancel another user's booking
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCancelPermissions:
    """B-28 — employee cannot cancel another user's (or admin's) booking."""

    def test_employee_cannot_cancel_other_employee_booking(
        self, api_client, employee, other_employee, stable_resource, company
    ):
        start = _fixed_future_slot(hours_from_now=217)
        end = start + timedelta(hours=1)
        booking = Booking.objects.create(
            resource=stable_resource,
            user=other_employee,
            company=company,
            start_time=start,
            end_time=end,
            status='confirmed',
        )
        api_client.force_authenticate(user=employee)
        response = api_client.post(
            f'{RESERVATIONS_URL}{booking.id}/cancel/',
            {'reason': 'Trying to cancel other booking'},
            format='json',
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        booking.refresh_from_db()
        assert booking.status == 'confirmed'

    def test_employee_cannot_cancel_admin_booking(
        self, api_client, employee, company_admin, stable_resource, company
    ):
        start = _fixed_future_slot(hours_from_now=241)
        end = start + timedelta(hours=1)
        booking = Booking.objects.create(
            resource=stable_resource,
            user=company_admin,
            company=company,
            start_time=start,
            end_time=end,
            status='confirmed',
        )
        api_client.force_authenticate(user=employee)
        response = api_client.post(
            f'{RESERVATIONS_URL}{booking.id}/cancel/',
            {'reason': 'Trying to cancel admin booking'},
            format='json',
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        booking.refresh_from_db()
        assert booking.status == 'confirmed'

    def test_unauthenticated_cannot_cancel(
        self, api_client, employee, stable_resource, company
    ):
        start = _fixed_future_slot(hours_from_now=265)
        end = start + timedelta(hours=1)
        booking = Booking.objects.create(
            resource=stable_resource,
            user=employee,
            company=company,
            start_time=start,
            end_time=end,
            status='confirmed',
        )
        response = api_client.post(
            f'{RESERVATIONS_URL}{booking.id}/cancel/',
            {'reason': 'Unauthenticated attempt'},
            format='json',
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
