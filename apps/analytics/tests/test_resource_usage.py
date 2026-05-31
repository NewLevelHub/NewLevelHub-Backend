"""Integration tests for GET /api/v1/analytics/resources/ (per-resource usage)."""

from datetime import datetime
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.bookings.models import Booking, Resource
from apps.companies.models import Company
from apps.users.models import User

RESOURCES_URL = '/api/v1/analytics/resources/'


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company_a(db):
    return Company.objects.create(name='Co A', plan='basic')


@pytest.fixture
def company_b(db):
    return Company.objects.create(name='Co B', plan='basic')


@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email='sa-res@analytics.test',
        password='pass',
        first_name='S',
        last_name='A',
        role='superadmin',
    )


@pytest.fixture
def employee_a(db, company_a):
    return User.objects.create_user(
        email='emp-a@res.test',
        password='pass',
        first_name='E',
        last_name='A',
        role='employee',
        company=company_a,
    )


@pytest.fixture
def employee_b(db, company_b):
    return User.objects.create_user(
        email='emp-b@res.test',
        password='pass',
        first_name='E',
        last_name='B',
        role='employee',
        company=company_b,
    )


def _aware_local(*args):
    return timezone.make_aware(datetime(*args), timezone.get_current_timezone())


@pytest.mark.django_db
class TestResourceUsage:
    def test_non_superadmin_403(self, api_client, employee_a):
        api_client.force_authenticate(user=employee_a)
        r = api_client.get(RESOURCES_URL)
        assert r.status_code == status.HTTP_403_FORBIDDEN

    def test_unauthenticated_401(self, api_client):
        r = api_client.get(RESOURCES_URL)
        assert r.status_code == status.HTTP_401_UNAUTHORIZED

    def test_response_shape(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        r = api_client.get(RESOURCES_URL, {'period': '30d'})
        assert r.status_code == status.HTTP_200_OK
        body = r.json()
        assert body['period'] == '30d'
        assert 'date_from' in body and 'date_to' in body
        assert isinstance(body['results'], list)

    def test_aggregates_per_resource(self, api_client, superadmin, company_a, employee_a):
        base = _aware_local(2026, 5, 2, 9, 0, 0)
        desk1 = Resource.objects.create(name='Desk 1', resource_type='desk', floor=1)
        desk2 = Resource.objects.create(name='Desk 2', resource_type='desk', floor=2)
        # desk1: two bookings (60 + 30 min); desk2: one booking (45 min)
        Booking.objects.create(
            resource=desk1, user=employee_a, company=company_a,
            start_time=_aware_local(2026, 5, 1, 10, 0, 0),
            end_time=_aware_local(2026, 5, 1, 11, 0, 0),
            status='confirmed',
        )
        Booking.objects.create(
            resource=desk1, user=employee_a, company=company_a,
            start_time=_aware_local(2026, 5, 2, 12, 0, 0),
            end_time=_aware_local(2026, 5, 2, 12, 30, 0),
            status='confirmed',
        )
        Booking.objects.create(
            resource=desk2, user=employee_a, company=company_a,
            start_time=_aware_local(2026, 5, 2, 13, 0, 0),
            end_time=_aware_local(2026, 5, 2, 13, 45, 0),
            status='confirmed',
        )

        api_client.force_authenticate(user=superadmin)
        with patch('django.utils.timezone.now', return_value=base):
            r = api_client.get(RESOURCES_URL, {'period': '7d'})
        assert r.status_code == status.HTTP_200_OK
        by_id = {row['resource_id']: row for row in r.json()['results']}

        assert by_id[desk1.id]['resource_name'] == 'Desk 1'
        assert by_id[desk1.id]['resource_type'] == 'desk'
        assert by_id[desk1.id]['floor'] == 1
        assert by_id[desk1.id]['total_bookings'] == 2
        assert by_id[desk1.id]['total_booked_minutes'] == 90.0
        assert by_id[desk1.id]['avg_duration_minutes'] == 45.0

        assert by_id[desk2.id]['total_bookings'] == 1
        assert by_id[desk2.id]['total_booked_minutes'] == 45.0
        assert by_id[desk2.id]['avg_duration_minutes'] == 45.0

    def test_filter_by_resource_id(self, api_client, superadmin, company_a, employee_a):
        base = _aware_local(2026, 5, 2, 9, 0, 0)
        desk1 = Resource.objects.create(name='Desk A', resource_type='desk', floor=1)
        desk2 = Resource.objects.create(name='Desk B', resource_type='desk', floor=1)
        Booking.objects.create(
            resource=desk1, user=employee_a, company=company_a,
            start_time=_aware_local(2026, 5, 1, 10, 0, 0),
            end_time=_aware_local(2026, 5, 1, 11, 0, 0),
            status='confirmed',
        )
        Booking.objects.create(
            resource=desk2, user=employee_a, company=company_a,
            start_time=_aware_local(2026, 5, 1, 12, 0, 0),
            end_time=_aware_local(2026, 5, 1, 13, 0, 0),
            status='confirmed',
        )

        api_client.force_authenticate(user=superadmin)
        with patch('django.utils.timezone.now', return_value=base):
            r = api_client.get(RESOURCES_URL, {'period': '7d', 'resource_id': desk1.id})
        assert r.status_code == status.HTTP_200_OK
        results = r.json()['results']
        assert len(results) == 1
        assert results[0]['resource_id'] == desk1.id

    def test_filter_by_floor(self, api_client, superadmin, company_a, employee_a):
        base = _aware_local(2026, 5, 2, 9, 0, 0)
        floor1 = Resource.objects.create(name='F1', resource_type='desk', floor=1)
        floor3 = Resource.objects.create(name='F3', resource_type='desk', floor=3)
        Booking.objects.create(
            resource=floor1, user=employee_a, company=company_a,
            start_time=_aware_local(2026, 5, 1, 10, 0, 0),
            end_time=_aware_local(2026, 5, 1, 11, 0, 0),
            status='confirmed',
        )
        Booking.objects.create(
            resource=floor3, user=employee_a, company=company_a,
            start_time=_aware_local(2026, 5, 1, 10, 0, 0),
            end_time=_aware_local(2026, 5, 1, 11, 0, 0),
            status='confirmed',
        )

        api_client.force_authenticate(user=superadmin)
        with patch('django.utils.timezone.now', return_value=base):
            r = api_client.get(RESOURCES_URL, {'period': '7d', 'floor': 3})
        assert r.status_code == status.HTTP_200_OK
        results = r.json()['results']
        assert len(results) == 1
        assert results[0]['resource_id'] == floor3.id
        assert results[0]['floor'] == 3

    def test_filter_by_company(
        self, api_client, superadmin, company_a, company_b, employee_a, employee_b,
    ):
        base = _aware_local(2026, 5, 2, 9, 0, 0)
        shared = Resource.objects.create(name='Shared', resource_type='desk', floor=1)
        Booking.objects.create(
            resource=shared, user=employee_a, company=company_a,
            start_time=_aware_local(2026, 5, 1, 10, 0, 0),
            end_time=_aware_local(2026, 5, 1, 11, 0, 0),
            status='confirmed',
        )
        Booking.objects.create(
            resource=shared, user=employee_b, company=company_b,
            start_time=_aware_local(2026, 5, 1, 14, 0, 0),
            end_time=_aware_local(2026, 5, 1, 15, 0, 0),
            status='confirmed',
        )

        api_client.force_authenticate(user=superadmin)
        with patch('django.utils.timezone.now', return_value=base):
            r = api_client.get(RESOURCES_URL, {'period': '7d', 'company_id': company_a.id})
        results = r.json()['results']
        assert len(results) == 1
        assert results[0]['resource_id'] == shared.id
        assert results[0]['total_bookings'] == 1

    def test_custom_period(self, api_client, superadmin, company_a, employee_a):
        in_window = Resource.objects.create(name='InWin', resource_type='desk', floor=1)
        out_window = Resource.objects.create(name='OutWin', resource_type='desk', floor=1)
        Booking.objects.create(
            resource=in_window, user=employee_a, company=company_a,
            start_time=_aware_local(2026, 4, 15, 10, 0, 0),
            end_time=_aware_local(2026, 4, 15, 11, 0, 0),
            status='confirmed',
        )
        Booking.objects.create(
            resource=out_window, user=employee_a, company=company_a,
            start_time=_aware_local(2026, 3, 1, 10, 0, 0),
            end_time=_aware_local(2026, 3, 1, 11, 0, 0),
            status='confirmed',
        )

        api_client.force_authenticate(user=superadmin)
        r = api_client.get(
            RESOURCES_URL,
            {'period': 'custom', 'date_from': '2026-04-01', 'date_to': '2026-04-30'},
        )
        assert r.status_code == status.HTTP_200_OK
        ids = {row['resource_id'] for row in r.json()['results']}
        assert in_window.id in ids
        assert out_window.id not in ids

    def test_excludes_cancelled(self, api_client, superadmin, company_a, employee_a):
        base = _aware_local(2026, 5, 2, 9, 0, 0)
        desk = Resource.objects.create(name='Desk X', resource_type='desk', floor=1)
        Booking.objects.create(
            resource=desk, user=employee_a, company=company_a,
            start_time=_aware_local(2026, 5, 1, 10, 0, 0),
            end_time=_aware_local(2026, 5, 1, 11, 0, 0),
            status='confirmed',
        )
        Booking.objects.create(
            resource=desk, user=employee_a, company=company_a,
            start_time=_aware_local(2026, 5, 1, 14, 0, 0),
            end_time=_aware_local(2026, 5, 1, 15, 0, 0),
            status='cancelled',
        )

        api_client.force_authenticate(user=superadmin)
        with patch('django.utils.timezone.now', return_value=base):
            r = api_client.get(RESOURCES_URL, {'period': '7d', 'resource_id': desk.id})
        assert r.json()['results'][0]['total_bookings'] == 1

    def test_invalid_period_400(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        r = api_client.get(RESOURCES_URL, {'period': '1y'})
        assert r.status_code == status.HTTP_400_BAD_REQUEST

    def test_company_not_found_404(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        r = api_client.get(RESOURCES_URL, {'period': '7d', 'company_id': 999999999})
        assert r.status_code == status.HTTP_404_NOT_FOUND

    def test_resource_not_found_404(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        r = api_client.get(RESOURCES_URL, {'period': '7d', 'resource_id': 999999999})
        assert r.status_code == status.HTTP_404_NOT_FOUND

    def test_invalid_integer_400(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        r = api_client.get(RESOURCES_URL, {'period': '7d', 'floor': 'abc'})
        assert r.status_code == status.HTTP_400_BAD_REQUEST
