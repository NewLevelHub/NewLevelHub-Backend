from datetime import timedelta

import pytest
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.bookings.models import Booking, Resource
from apps.companies.models import Company
from apps.notifications.models import Notification
from apps.users.models import User

RESERVATIONS_URL = '/api/v1/bookings/reservations/'


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company_a(db):
    return Company.objects.create(name='Company A', plan='basic')


@pytest.fixture
def company_b(db):
    return Company.objects.create(name='Company B', plan='basic')


@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email='superadmin@bookings-admin.test',
        password='pass',
        first_name='Super',
        last_name='Admin',
        role='superadmin',
        is_email_verified=True,
    )


@pytest.fixture
def company_admin_a(db, company_a):
    return User.objects.create_user(
        email='company_admin_a@bookings-admin.test',
        password='pass',
        first_name='Company',
        last_name='AdminA',
        role='company_admin',
        company=company_a,
        is_email_verified=True,
    )


@pytest.fixture
def employee_a(db, company_a):
    return User.objects.create_user(
        email='employee_a@bookings-admin.test',
        password='pass',
        first_name='Employee',
        last_name='A',
        role='employee',
        company=company_a,
        is_email_verified=True,
    )


@pytest.fixture
def employee_b(db, company_b):
    return User.objects.create_user(
        email='employee_b@bookings-admin.test',
        password='pass',
        first_name='Employee',
        last_name='B',
        role='employee',
        company=company_b,
        is_email_verified=True,
    )


@pytest.fixture
def resource_a(db):
    return Resource.objects.create(
        name='Desk A',
        resource_type='desk',
        available_days=[0, 1, 2, 3, 4, 5, 6],
    )


@pytest.fixture
def resource_b(db):
    return Resource.objects.create(
        name='Meeting B',
        resource_type='meeting_room',
        capacity=8,
        available_days=[0, 1, 2, 3, 4, 5, 6],
    )


def _extract_results(response):
    data = response.json()
    if isinstance(data, dict) and 'results' in data:
        return data['results']
    return data


@pytest.fixture
def bookings_dataset(db, company_a, company_b, employee_a, employee_b, resource_a, resource_b):
    start_base = timezone.now().replace(minute=0, second=0, microsecond=0) + timedelta(days=2)
    booking_a_confirmed = Booking.objects.create(
        resource=resource_a,
        user=employee_a,
        company=company_a,
        start_time=start_base,
        end_time=start_base + timedelta(hours=1),
        status='confirmed',
    )
    booking_a_cancelled = Booking.objects.create(
        resource=resource_b,
        user=employee_a,
        company=company_a,
        start_time=start_base + timedelta(days=1),
        end_time=start_base + timedelta(days=1, hours=1),
        status='cancelled',
    )
    booking_b_confirmed = Booking.objects.create(
        resource=resource_b,
        user=employee_b,
        company=company_b,
        start_time=start_base + timedelta(days=3),
        end_time=start_base + timedelta(days=3, hours=1),
        status='confirmed',
    )
    return {
        'booking_a_confirmed': booking_a_confirmed,
        'booking_a_cancelled': booking_a_cancelled,
        'booking_b_confirmed': booking_b_confirmed,
    }


@pytest.mark.django_db
class TestAdminReservationsList:
    def test_superadmin_sees_all_reservations(self, api_client, superadmin, bookings_dataset):
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(RESERVATIONS_URL, {'page_size': 100})

        assert response.status_code == status.HTTP_200_OK
        results = _extract_results(response)
        returned_ids = {item['id'] for item in results}
        expected_ids = {
            bookings_dataset['booking_a_confirmed'].id,
            bookings_dataset['booking_a_cancelled'].id,
            bookings_dataset['booking_b_confirmed'].id,
        }
        assert expected_ids.issubset(returned_ids)

    @pytest.mark.parametrize(
        ('query_params_builder', 'assertion'),
        [
            (
                lambda ds: {'company_id': ds['booking_a_confirmed'].company_id},
                lambda rows, ds: all(row['company'] == ds['booking_a_confirmed'].company_id for row in rows),
            ),
            (
                lambda ds: {'user_id': ds['booking_b_confirmed'].user_id},
                lambda rows, ds: all(row['user'] == ds['booking_b_confirmed'].user_id for row in rows),
            ),
            (
                lambda ds: {'resource_id': ds['booking_a_cancelled'].resource_id},
                lambda rows, ds: all(row['resource'] == ds['booking_a_cancelled'].resource_id for row in rows),
            ),
            (
                lambda ds: {'resource_type': 'meeting_room'},
                lambda rows, ds: Booking.objects.filter(
                    id__in=[row['id'] for row in rows],
                    resource__resource_type='meeting_room',
                ).count() == len(rows),
            ),
            (
                lambda ds: {'status': 'cancelled'},
                lambda rows, ds: all(row['status'] == 'cancelled' for row in rows),
            ),
            (
                lambda ds: {
                    'date_from': (ds['booking_b_confirmed'].start_time - timedelta(minutes=1)).isoformat()
                },
                lambda rows, ds: all(
                    parse_datetime(row['start_time']) >= ds['booking_b_confirmed'].start_time - timedelta(minutes=1)
                    for row in rows
                ),
            ),
            (
                lambda ds: {
                    'date_to': (ds['booking_a_confirmed'].end_time + timedelta(minutes=1)).isoformat()
                },
                lambda rows, ds: all(
                    parse_datetime(row['end_time']) <= ds['booking_a_confirmed'].end_time + timedelta(minutes=1)
                    for row in rows
                ),
            ),
        ],
    )
    def test_superadmin_filters_supported(
        self,
        api_client,
        superadmin,
        bookings_dataset,
        query_params_builder,
        assertion,
    ):
        api_client.force_authenticate(user=superadmin)
        params = query_params_builder(bookings_dataset)
        params['page_size'] = 100
        response = api_client.get(RESERVATIONS_URL, params)

        assert response.status_code == status.HTTP_200_OK
        results = _extract_results(response)
        assert len(results) >= 1
        assert assertion(results, bookings_dataset)

    def test_company_admin_sees_only_own_company_bookings(
        self,
        api_client,
        company_admin_a,
        bookings_dataset,
    ):
        api_client.force_authenticate(user=company_admin_a)
        response = api_client.get(RESERVATIONS_URL, {'page_size': 100})

        assert response.status_code == status.HTTP_200_OK
        results = _extract_results(response)
        returned_ids = {item['id'] for item in results}
        assert bookings_dataset['booking_a_confirmed'].id in returned_ids
        assert bookings_dataset['booking_a_cancelled'].id in returned_ids
        assert bookings_dataset['booking_b_confirmed'].id not in returned_ids


@pytest.mark.django_db
class TestAdminCancelReservation:
    def test_superadmin_can_admin_cancel_with_reason_and_notifies_user(
        self,
        api_client,
        superadmin,
        bookings_dataset,
    ):
        booking = bookings_dataset['booking_a_confirmed']
        api_client.force_authenticate(user=superadmin)

        response = api_client.post(
            f'{RESERVATIONS_URL}{booking.id}/admin-cancel/',
            {'reason': 'Violation of company policy'},
            format='json',
        )

        assert response.status_code == status.HTTP_200_OK
        booking.refresh_from_db()
        assert booking.status == 'cancelled'
        assert booking.cancel_reason == 'Violation of company policy'
        assert booking.cancelled_by_id == superadmin.id
        assert Notification.objects.filter(
            user=booking.user,
            notification_type='booking_cancelled',
        ).exists()

    def test_company_admin_can_admin_cancel_only_in_own_company(
        self,
        api_client,
        company_admin_a,
        bookings_dataset,
    ):
        own_booking = bookings_dataset['booking_a_confirmed']
        foreign_booking = bookings_dataset['booking_b_confirmed']
        api_client.force_authenticate(user=company_admin_a)

        own_response = api_client.post(
            f'{RESERVATIONS_URL}{own_booking.id}/admin-cancel/',
            {'reason': 'Office closed'},
            format='json',
        )
        foreign_response = api_client.post(
            f'{RESERVATIONS_URL}{foreign_booking.id}/admin-cancel/',
            {'reason': 'Office closed'},
            format='json',
        )

        assert own_response.status_code == status.HTTP_200_OK
        assert foreign_response.status_code == status.HTTP_404_NOT_FOUND

    def test_employee_cannot_use_admin_cancel(self, api_client, employee_a, bookings_dataset):
        booking = bookings_dataset['booking_a_confirmed']
        api_client.force_authenticate(user=employee_a)

        response = api_client.post(
            f'{RESERVATIONS_URL}{booking.id}/admin-cancel/',
            {'reason': 'No rights'},
            format='json',
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_admin_cancel_requires_reason(self, api_client, superadmin, bookings_dataset):
        booking = bookings_dataset['booking_a_confirmed']
        api_client.force_authenticate(user=superadmin)

        response = api_client.post(
            f'{RESERVATIONS_URL}{booking.id}/admin-cancel/',
            {},
            format='json',
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
