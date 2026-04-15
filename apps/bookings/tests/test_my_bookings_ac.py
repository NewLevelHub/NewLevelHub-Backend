from datetime import timedelta

import pytest
from django.apps import apps
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.bookings.models import Booking, Resource
from apps.companies.models import Company
from apps.users.models import User

MY_BOOKINGS_URL = '/api/v1/bookings/reservations/my/'
RESERVATIONS_URL = '/api/v1/bookings/reservations/'


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='My bookings AC Co', plan='basic')


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='my-bookings-employee@test.local',
        password='pass',
        first_name='My',
        last_name='Bookings',
        role='employee',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def other_employee(db, company):
    return User.objects.create_user(
        email='my-bookings-other@test.local',
        password='pass',
        first_name='Other',
        last_name='User',
        role='employee',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def desk_resource(db):
    return Resource.objects.create(
        name='AC Desk',
        resource_type='desk',
        min_cancel_minutes=30,
        available_days=[0, 1, 2, 3, 4, 5, 6],
        available_from='07:00',
        available_until='23:00',
    )


@pytest.fixture
def meeting_room_resource(db):
    return Resource.objects.create(
        name='AC Meeting Room',
        resource_type='meeting_room',
        min_cancel_minutes=90,
        available_days=[0, 1, 2, 3, 4, 5, 6],
        available_from='07:00',
        available_until='23:00',
        capacity=8,
    )


def _results(response):
    data = response.json()
    if isinstance(data, dict) and 'results' in data:
        return data['results']
    return data


def _create_booking(*, user, company, resource, start_delta_minutes, duration_minutes=60, status_code='confirmed'):
    start = timezone.now() + timedelta(minutes=start_delta_minutes)
    end = start + timedelta(minutes=duration_minutes)
    return Booking.objects.create(
        resource=resource,
        user=user,
        company=company,
        start_time=start,
        end_time=end,
        status=status_code,
    )


@pytest.mark.django_db
class TestMyBookingsFiltersAndSorting:
    def test_status_upcoming_returns_future_and_sorted_asc(
        self, api_client, employee, company, desk_resource
    ):
        b_later = _create_booking(
            user=employee, company=company, resource=desk_resource, start_delta_minutes=360
        )
        b_earlier = _create_booking(
            user=employee, company=company, resource=desk_resource, start_delta_minutes=120
        )
        _create_booking(
            user=employee,
            company=company,
            resource=desk_resource,
            start_delta_minutes=-240,
            status_code='completed',
        )
        _create_booking(
            user=employee,
            company=company,
            resource=desk_resource,
            start_delta_minutes=-360,
            status_code='cancelled',
        )

        api_client.force_authenticate(user=employee)
        response = api_client.get(MY_BOOKINGS_URL, {'status': 'upcoming'})
        assert response.status_code == status.HTTP_200_OK
        ids = [row['id'] for row in _results(response)]
        assert ids == [b_earlier.id, b_later.id]

    def test_status_past_returns_past_and_sorted_desc(
        self, api_client, employee, company, desk_resource
    ):
        b_older = _create_booking(
            user=employee,
            company=company,
            resource=desk_resource,
            start_delta_minutes=-480,
            status_code='completed',
        )
        b_newer = _create_booking(
            user=employee,
            company=company,
            resource=desk_resource,
            start_delta_minutes=-120,
            status_code='no_show',
        )
        _create_booking(
            user=employee, company=company, resource=desk_resource, start_delta_minutes=240
        )

        api_client.force_authenticate(user=employee)
        response = api_client.get(MY_BOOKINGS_URL, {'status': 'past'})
        assert response.status_code == status.HTTP_200_OK
        ids = [row['id'] for row in _results(response)]
        assert ids == [b_newer.id, b_older.id]

    def test_status_cancelled_returns_only_cancelled(
        self, api_client, employee, company, desk_resource
    ):
        cancelled = _create_booking(
            user=employee,
            company=company,
            resource=desk_resource,
            start_delta_minutes=300,
            status_code='cancelled',
        )
        _create_booking(
            user=employee, company=company, resource=desk_resource, start_delta_minutes=400
        )
        _create_booking(
            user=employee,
            company=company,
            resource=desk_resource,
            start_delta_minutes=-200,
            status_code='completed',
        )

        api_client.force_authenticate(user=employee)
        response = api_client.get(MY_BOOKINGS_URL, {'status': 'cancelled'})
        assert response.status_code == status.HTTP_200_OK
        ids = [row['id'] for row in _results(response)]
        assert ids == [cancelled.id]

    def test_resource_type_and_date_filters_apply_together(
        self, api_client, employee, company, desk_resource, meeting_room_resource
    ):
        target = _create_booking(
            user=employee,
            company=company,
            resource=meeting_room_resource,
            start_delta_minutes=240,
        )
        _create_booking(
            user=employee, company=company, resource=desk_resource, start_delta_minutes=240
        )
        _create_booking(
            user=employee, company=company, resource=meeting_room_resource, start_delta_minutes=1440
        )

        date_from = (timezone.now() + timedelta(minutes=180)).isoformat()
        date_to = (timezone.now() + timedelta(minutes=360)).isoformat()

        api_client.force_authenticate(user=employee)
        response = api_client.get(
            MY_BOOKINGS_URL,
            {
                'resource_type': 'meeting_room',
                'date_from': date_from,
                'date_to': date_to,
            },
        )
        assert response.status_code == status.HTTP_200_OK
        ids = [row['id'] for row in _results(response)]
        assert ids == [target.id]

    def test_my_bookings_remains_scoped_to_request_user(
        self, api_client, employee, other_employee, company, desk_resource
    ):
        own = _create_booking(
            user=employee, company=company, resource=desk_resource, start_delta_minutes=220
        )
        _create_booking(
            user=other_employee, company=company, resource=desk_resource, start_delta_minutes=220
        )
        api_client.force_authenticate(user=employee)
        response = api_client.get(MY_BOOKINGS_URL, {'status': 'upcoming'})
        assert response.status_code == status.HTTP_200_OK
        ids = [row['id'] for row in _results(response)]
        assert ids == [own.id]


@pytest.mark.django_db
class TestCancelRulesAndAudit:
    def test_cannot_cancel_less_than_min_cancel_minutes(
        self, api_client, employee, company, meeting_room_resource
    ):
        booking = _create_booking(
            user=employee,
            company=company,
            resource=meeting_room_resource,
            start_delta_minutes=60,
        )
        api_client.force_authenticate(user=employee)
        response = api_client.post(
            f'{RESERVATIONS_URL}{booking.id}/cancel/',
            {'reason': 'Too late'},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        booking.refresh_from_db()
        assert booking.status == 'confirmed'

    def test_cannot_cancel_started_booking(self, api_client, employee, company, desk_resource):
        booking = _create_booking(
            user=employee,
            company=company,
            resource=desk_resource,
            start_delta_minutes=-10,
            duration_minutes=40,
        )
        api_client.force_authenticate(user=employee)
        response = api_client.post(
            f'{RESERVATIONS_URL}{booking.id}/cancel/',
            {'reason': 'Already started'},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        booking.refresh_from_db()
        assert booking.status == 'confirmed'

    def test_cancel_allowed_outside_min_window(
        self, api_client, employee, company, meeting_room_resource
    ):
        booking = _create_booking(
            user=employee,
            company=company,
            resource=meeting_room_resource,
            start_delta_minutes=180,
        )
        api_client.force_authenticate(user=employee)
        response = api_client.post(
            f'{RESERVATIONS_URL}{booking.id}/cancel/',
            {'reason': 'Plan changed'},
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        booking.refresh_from_db()
        assert booking.status == 'cancelled'
        assert booking.cancel_reason == 'Plan changed'

    def test_cancel_creates_audit_record(self, api_client, employee, company, desk_resource):
        booking = _create_booking(
            user=employee,
            company=company,
            resource=desk_resource,
            start_delta_minutes=240,
        )
        api_client.force_authenticate(user=employee)
        response = api_client.post(
            f'{RESERVATIONS_URL}{booking.id}/cancel/',
            {'reason': 'Need to reschedule'},
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        audit_model = apps.get_model('bookings', 'BookingCancellationAudit')
        assert audit_model.objects.filter(booking_id=booking.id).count() == 1
