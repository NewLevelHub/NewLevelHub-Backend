"""Integration tests for floor occupancy_pct field in the Floor list API."""

import pytest
from django.utils import timezone
from datetime import timedelta
from rest_framework import status
from rest_framework.test import APIClient

from apps.bookings.models import Booking, Resource
from apps.companies.models import Company
from apps.services.models import Floor
from apps.users.models import User


FLOORS_URL = '/api/v1/services/floors/'


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='Occupancy Co', plan='basic')


@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email='super@occupancy.test',
        password='pass',
        first_name='Super',
        last_name='Admin',
        role='superadmin',
    )


@pytest.fixture
def floor_obj(db, company):
    return Floor.objects.create(number=50, name='Occupancy Floor', company=company)


def _make_resource(floor, name='Desk'):
    return Resource.objects.create(
        name=name,
        resource_type='desk',
        floor=floor.number,
        floor_fk=floor,
        is_active=True,
    )


def _make_active_booking(resource, user, company):
    now = timezone.now()
    return Booking.objects.create(
        resource=resource,
        user=user,
        company=company,
        start_time=now - timedelta(minutes=30),
        end_time=now + timedelta(minutes=30),
        status='confirmed',
    )


@pytest.mark.django_db
class TestFloorOccupancy:
    def test_two_resources_one_booked_returns_50_pct(self, api_client, superadmin, floor_obj, company):
        """Floor with 2 resources, 1 booked now → occupancy_pct == 50."""
        r1 = _make_resource(floor_obj, 'Desk 1')
        _make_resource(floor_obj, 'Desk 2')
        _make_active_booking(r1, superadmin, company)

        api_client.force_authenticate(user=superadmin)
        response = api_client.get(FLOORS_URL)

        assert response.status_code == status.HTTP_200_OK
        results = response.json()['results']
        floor_data = next(f for f in results if f['id'] == floor_obj.id)
        assert floor_data['occupancy_pct'] == 50

    def test_two_resources_none_booked_returns_0_pct(self, api_client, superadmin, floor_obj):
        """Floor with 2 resources, 0 booked now → occupancy_pct == 0."""
        _make_resource(floor_obj, 'Desk A')
        _make_resource(floor_obj, 'Desk B')

        api_client.force_authenticate(user=superadmin)
        response = api_client.get(FLOORS_URL)

        assert response.status_code == status.HTTP_200_OK
        results = response.json()['results']
        floor_data = next(f for f in results if f['id'] == floor_obj.id)
        assert floor_data['occupancy_pct'] == 0

    def test_floor_with_no_resources_returns_0_pct(self, api_client, superadmin, floor_obj):
        """Floor with no resources at all → occupancy_pct == 0."""
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(FLOORS_URL)

        assert response.status_code == status.HTTP_200_OK
        results = response.json()['results']
        floor_data = next(f for f in results if f['id'] == floor_obj.id)
        assert floor_data['occupancy_pct'] == 0

    def test_cancelled_booking_does_not_count_as_occupied(self, api_client, superadmin, floor_obj, company):
        """A cancelled booking must not affect occupancy_pct."""
        r1 = _make_resource(floor_obj, 'Desk X')
        now = timezone.now()
        Booking.objects.create(
            resource=r1,
            user=superadmin,
            company=company,
            start_time=now - timedelta(minutes=30),
            end_time=now + timedelta(minutes=30),
            status='cancelled',
        )

        api_client.force_authenticate(user=superadmin)
        response = api_client.get(FLOORS_URL)

        assert response.status_code == status.HTTP_200_OK
        results = response.json()['results']
        floor_data = next(f for f in results if f['id'] == floor_obj.id)
        assert floor_data['occupancy_pct'] == 0

    def test_inactive_resource_not_counted(self, api_client, superadmin, floor_obj, company):
        """An inactive resource (is_active=False) must not inflate total or occupied counts."""
        active_resource = _make_resource(floor_obj, 'Active Desk')
        inactive_resource = Resource.objects.create(
            name='Inactive Desk',
            resource_type='desk',
            floor=floor_obj.number,
            floor_fk=floor_obj,
            is_active=False,
        )
        # Book only the inactive one — occupancy should still be 0 (it doesn't count)
        _make_active_booking(inactive_resource, superadmin, company)
        # Book the active one too → 1/1 = 100%
        _make_active_booking(active_resource, superadmin, company)

        api_client.force_authenticate(user=superadmin)
        response = api_client.get(FLOORS_URL)

        assert response.status_code == status.HTTP_200_OK
        results = response.json()['results']
        floor_data = next(f for f in results if f['id'] == floor_obj.id)
        # Only 1 active resource, booked → 100%
        assert floor_data['occupancy_pct'] == 100
