"""Integration tests for bookings Resource CRUD (DEV-63)."""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.bookings.models import Booking, Resource
from apps.companies.models import Company
from apps.notifications.models import Notification
from apps.users.models import User


RESOURCES_URL = '/api/v1/bookings/resources/'


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='Tenant Co', plan='basic')


@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email='super@resources.test',
        password='pass',
        first_name='S',
        last_name='A',
        role='superadmin',
    )


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='emp@resources.test',
        password='pass',
        first_name='E',
        last_name='M',
        role='employee',
        company=company,
    )


@pytest.fixture
def guest_user(db):
    return User.objects.create_user(
        email='guest@resources.test',
        password='pass',
        first_name='G',
        last_name='U',
        role='guest',
    )


@pytest.mark.django_db
class TestResourceListAuth:
    def test_anonymous_list_401(self, api_client):
        r = api_client.get(RESOURCES_URL)
        assert r.status_code == status.HTTP_401_UNAUTHORIZED

    def test_guest_list_200(self, api_client, guest_user):
        api_client.force_authenticate(user=guest_user)
        r = api_client.get(RESOURCES_URL)
        assert r.status_code == status.HTTP_200_OK

    def test_employee_list_200(self, api_client, employee):
        api_client.force_authenticate(user=employee)
        r = api_client.get(RESOURCES_URL)
        assert r.status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestResourceCreate:
    def test_non_superadmin_create_403(self, api_client, employee):
        api_client.force_authenticate(user=employee)
        r = api_client.post(
            RESOURCES_URL,
            {
                'type': 'desk',
                'name': 'D1',
                'floor': 2,
            },
            format='json',
        )
        assert r.status_code == status.HTTP_403_FORBIDDEN

    def test_superadmin_create_meeting_room(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        r = api_client.post(
            RESOURCES_URL,
            {
                'type': 'meeting_room',
                'name': 'MR A',
                'floor': 3,
                'zone': 'North',
                'description': 'Big room',
                'capacity': 12,
                'equipment': {'projector': True, 'tv': False, 'whiteboard': True},
                'min_duration_minutes': 60,
                'max_duration_minutes': 240,
            },
            format='json',
        )
        assert r.status_code == status.HTTP_201_CREATED
        body = r.json()
        assert body['type'] == 'meeting_room'
        assert body['capacity'] == 12
        assert body['equipment']['projector'] is True
        assert body['equipment']['tv'] is False
        rid = body['id']
        resource = Resource.objects.get(pk=rid)
        assert resource.has_projector is True
        assert resource.has_tv is False

    def test_meeting_room_capacity_required(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        r = api_client.post(
            RESOURCES_URL,
            {
                'type': 'meeting_room',
                'name': 'MR B',
                'floor': 1,
            },
            format='json',
        )
        assert r.status_code == status.HTTP_400_BAD_REQUEST

    def test_superadmin_create_desk(self, api_client, superadmin, company):
        api_client.force_authenticate(user=superadmin)
        r = api_client.post(
            RESOURCES_URL,
            {
                'type': 'desk',
                'name': 'Hot 1',
                'floor': 5,
                'has_monitor': True,
                'has_dock': True,
                'has_power_outlet': True,
                'is_hot_desk': True,
                'assigned_company': company.id,
            },
            format='json',
        )
        assert r.status_code == status.HTTP_201_CREATED
        body = r.json()
        assert body['type'] == 'desk'
        assert body['assigned_company'] == company.id

    def test_superadmin_create_parking(self, api_client, superadmin, company):
        api_client.force_authenticate(user=superadmin)
        r = api_client.post(
            RESOURCES_URL,
            {
                'type': 'parking',
                'name': 'P-01',
                'floor': -1,
                'parking_type': 'vip',
                'assigned_company': company.id,
            },
            format='json',
        )
        assert r.status_code == status.HTTP_201_CREATED
        body = r.json()
        assert body['parking_type'] == 'vip'
        assert Resource.objects.get(pk=body['id']).is_vip is True

    def test_parking_type_required_on_create(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        r = api_client.post(
            RESOURCES_URL,
            {
                'type': 'parking',
                'name': 'P-02',
                'floor': -1,
            },
            format='json',
        )
        assert r.status_code == status.HTTP_400_BAD_REQUEST

    def test_superadmin_create_capsule(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        r = api_client.post(
            RESOURCES_URL,
            {
                'type': 'capsule',
                'name': 'Cap 1',
                'floor': 6,
                'capsule_zone': 'quiet',
            },
            format='json',
        )
        assert r.status_code == status.HTTP_201_CREATED
        assert r.json()['capsule_zone'] == 'quiet'


@pytest.mark.django_db
class TestResourceDeactivateAndDelete:
    def test_patch_deactivate_cancels_future_bookings_and_notifies(
        self, api_client, superadmin, employee, company
    ):
        api_client.force_authenticate(user=superadmin)
        create = api_client.post(
            RESOURCES_URL,
            {
                'type': 'desk',
                'name': 'Desk X',
                'floor': 1,
            },
            format='json',
        )
        rid = create.json()['id']
        resource = Resource.objects.get(pk=rid)
        start = timezone.now() + timedelta(days=1)
        end = start + timedelta(hours=1)
        booking = Booking.objects.create(
            resource=resource,
            user=employee,
            company=company,
            start_time=start,
            end_time=end,
            status='confirmed',
        )
        api_client.force_authenticate(user=superadmin)
        patch = api_client.patch(
            f'{RESOURCES_URL}{rid}/',
            {'is_active': False},
            format='json',
        )
        assert patch.status_code == status.HTTP_200_OK
        booking.refresh_from_db()
        assert booking.status == 'cancelled'
        assert Notification.objects.filter(
            user=employee,
            notification_type='booking_cancelled',
        ).exists()

    def test_delete_blocked_when_future_bookings(self, api_client, superadmin, employee, company):
        api_client.force_authenticate(user=superadmin)
        create = api_client.post(
            RESOURCES_URL,
            {'type': 'desk', 'name': 'Desk Y', 'floor': 1},
            format='json',
        )
        rid = create.json()['id']
        resource = Resource.objects.get(pk=rid)
        Booking.objects.create(
            resource=resource,
            user=employee,
            company=company,
            start_time=timezone.now() + timedelta(days=2),
            end_time=timezone.now() + timedelta(days=2, hours=1),
            status='confirmed',
        )
        api_client.force_authenticate(user=superadmin)
        r = api_client.delete(f'{RESOURCES_URL}{rid}/')
        assert r.status_code == status.HTTP_400_BAD_REQUEST
        assert Resource.objects.filter(pk=rid).exists()

    def test_delete_ok_without_future_bookings(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        create = api_client.post(
            RESOURCES_URL,
            {'type': 'desk', 'name': 'Desk Z', 'floor': 1},
            format='json',
        )
        rid = create.json()['id']
        r = api_client.delete(f'{RESOURCES_URL}{rid}/')
        assert r.status_code == status.HTTP_204_NO_CONTENT
        assert not Resource.objects.filter(pk=rid).exists()


@pytest.mark.django_db
class TestResourceVisibility:
    def test_inactive_hidden_from_non_superadmin(self, api_client, superadmin, employee):
        api_client.force_authenticate(user=superadmin)
        create = api_client.post(
            RESOURCES_URL,
            {'type': 'desk', 'name': 'Hidden', 'floor': 1, 'is_active': False},
            format='json',
        )
        rid = create.json()['id']
        api_client.force_authenticate(user=employee)
        r = api_client.get(f'{RESOURCES_URL}{rid}/')
        assert r.status_code == status.HTTP_404_NOT_FOUND
        api_client.force_authenticate(user=superadmin)
        r2 = api_client.get(f'{RESOURCES_URL}{rid}/')
        assert r2.status_code == status.HTTP_200_OK
