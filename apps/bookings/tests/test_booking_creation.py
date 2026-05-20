from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.bookings.models import Booking, Resource
from apps.companies.models import Company
from apps.users.models import User

RESERVATIONS_URL = '/api/v1/bookings/reservations/'


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='Booking Co', plan='basic')


@pytest.fixture
def other_company(db):
    return Company.objects.create(name='Other Booking Co', plan='basic')


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='employee@booking.test',
        password='pass',
        first_name='Book',
        last_name='Er',
        role='employee',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def assigned_resource(db, company):
    return Resource.objects.create(
        name='Assigned Desk',
        resource_type='desk',
        assigned_company=company,
        available_days=[0, 1, 2, 3, 4],
    )


@pytest.fixture
def shared_resource(db):
    return Resource.objects.create(
        name='Shared Desk',
        resource_type='desk',
        assigned_company=None,
        available_days=[0, 1, 2, 3, 4],
    )


@pytest.fixture
def resource_other_company(db, other_company):
    return Resource.objects.create(
        name='Other Company Desk',
        resource_type='desk',
        assigned_company=other_company,
        available_days=[0, 1, 2, 3, 4],
    )


def _next_weekday_at(hour, minute=0):
    now = timezone.localtime()
    days_to_add = (0 - now.weekday()) % 7
    if days_to_add == 0:
        days_to_add = 7
    target = now + timedelta(days=days_to_add)
    return target.replace(hour=hour, minute=minute, second=0, microsecond=0)


@pytest.mark.django_db
class TestBookingCreate:
    def test_create_booking_success_for_shared_resource(self, api_client, employee, shared_resource):
        api_client.force_authenticate(user=employee)
        start_time = _next_weekday_at(10)
        end_time = start_time + timedelta(hours=1)

        response = api_client.post(
            RESERVATIONS_URL,
            {
                'resource_id': shared_resource.id,
                'start_time': start_time.isoformat(),
                'end_time': end_time.isoformat(),
                'description': 'Focus session',
            },
            format='json',
        )

        assert response.status_code == status.HTTP_201_CREATED
        body = response.json()
        assert body['resource'] == shared_resource.id
        assert body['user'] == employee.id
        assert body['status'] == 'confirmed'
        assert body['description'] == 'Focus session'

    def test_create_booking_success_for_assigned_company_resource(
        self, api_client, employee, assigned_resource
    ):
        api_client.force_authenticate(user=employee)
        start_time = _next_weekday_at(11)
        end_time = start_time + timedelta(hours=1)

        response = api_client.post(
            RESERVATIONS_URL,
            {
                'resource_id': assigned_resource.id,
                'start_time': start_time.isoformat(),
                'end_time': end_time.isoformat(),
            },
            format='json',
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()['resource'] == assigned_resource.id

    def test_create_booking_rejects_resource_assigned_to_other_company(
        self, api_client, employee, resource_other_company
    ):
        api_client.force_authenticate(user=employee)
        start_time = _next_weekday_at(12)
        end_time = start_time + timedelta(hours=1)

        response = api_client.post(
            RESERVATIONS_URL,
            {
                'resource_id': resource_other_company.id,
                'start_time': start_time.isoformat(),
                'end_time': end_time.isoformat(),
            },
            format='json',
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        body = response.json()
        assert 'resource_id' in body['error']['details']

    def test_create_booking_conflict_returns_409(self, api_client, employee, shared_resource, company):
        api_client.force_authenticate(user=employee)
        start_time = _next_weekday_at(13)
        Booking.objects.create(
            resource=shared_resource,
            user=employee,
            company=company,
            start_time=start_time,
            end_time=start_time + timedelta(hours=1),
            status='confirmed',
        )

        response = api_client.post(
            RESERVATIONS_URL,
            {
                'resource_id': shared_resource.id,
                'start_time': (start_time + timedelta(minutes=30)).isoformat(),
                'end_time': (start_time + timedelta(hours=2)).isoformat(),
            },
            format='json',
        )

        assert response.status_code == status.HTTP_409_CONFLICT
        assert 'error' in response.json()

    def test_create_booking_outside_availability_returns_400(
        self, api_client, employee, shared_resource
    ):
        api_client.force_authenticate(user=employee)
        start_time = _next_weekday_at(7)
        end_time = start_time + timedelta(hours=1)

        response = api_client.post(
            RESERVATIONS_URL,
            {
                'resource_id': shared_resource.id,
                'start_time': start_time.isoformat(),
                'end_time': end_time.isoformat(),
            },
            format='json',
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'error' in response.json()

    def test_create_booking_limit_active_reservations_returns_400(
        self, api_client, employee, shared_resource, company, settings
    ):
        settings.MAX_ACTIVE_BOOKINGS_PER_USER = 1
        api_client.force_authenticate(user=employee)
        first_start = timezone.now() + timedelta(hours=2)
        Booking.objects.create(
            resource=shared_resource,
            user=employee,
            company=company,
            start_time=first_start,
            end_time=first_start + timedelta(hours=1),
            status='confirmed',
        )

        second_start = first_start + timedelta(hours=3)
        response = api_client.post(
            RESERVATIONS_URL,
            {
                'resource_id': shared_resource.id,
                'start_time': second_start.isoformat(),
                'end_time': (second_start + timedelta(hours=1)).isoformat(),
            },
            format='json',
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'error' in response.json()
