"""Integration tests for GET /api/v1/analytics/superadmin/ overview (superadmin dashboard)."""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.access.models import AccessLog, GuestPass
from apps.bookings.models import Booking, Resource
from apps.companies.models import Company
from apps.services.models import ServiceRequest
from apps.users.models import User

SUPERADMIN_URL = '/api/v1/analytics/superadmin/'


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
        email='sa@analytics.test',
        password='pass',
        first_name='S',
        last_name='A',
        role='superadmin',
    )


@pytest.fixture
def employee(db, company_a):
    return User.objects.create_user(
        email='emp@analytics.test',
        password='pass',
        first_name='E',
        last_name='M',
        role='employee',
        company=company_a,
    )


def _aware_local(*args):
    return timezone.make_aware(datetime(*args), timezone.get_current_timezone())


@pytest.mark.django_db
class TestSuperadminOverview:
    def test_response_shape_200(self, api_client, superadmin, company_a):
        api_client.force_authenticate(user=superadmin)
        r = api_client.get(SUPERADMIN_URL, {'period': '30d'})
        assert r.status_code == status.HTTP_200_OK
        body = r.json()
        assert 'period' in body and 'date_from' in body and 'date_to' in body
        assert body['period'] == '30d'
        ov = body['overview']
        for k in (
            'total_companies',
            'active_companies',
            'total_users',
            'active_users_7d',
            'bookings_today',
            'guests_today',
            'open_service_requests',
        ):
            assert k in ov
            assert isinstance(ov[k], int)

    def test_non_superadmin_403(self, api_client, employee):
        api_client.force_authenticate(user=employee)
        r = api_client.get(SUPERADMIN_URL)
        assert r.status_code == status.HTTP_403_FORBIDDEN

    def test_invalid_period_400(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        r = api_client.get(SUPERADMIN_URL, {'period': '1y'})
        assert r.status_code == status.HTTP_400_BAD_REQUEST
        data = r.json()
        assert data.get('error') is True
        assert 'detail' in data

    def test_custom_without_dates_400(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        r = api_client.get(SUPERADMIN_URL, {'period': 'custom'})
        assert r.status_code == status.HTTP_400_BAD_REQUEST

    def test_company_filter_narrows_counts(self, api_client, superadmin, company_a, company_b):
        u1 = User.objects.create_user(
            email='a1@x.test', password='p', first_name='A', last_name='1', role='employee', company=company_a,
        )
        User.objects.create_user(
            email='a2@x.test', password='p', first_name='A', last_name='2', role='employee', company=company_a,
        )
        User.objects.create_user(
            email='b1@x.test', password='p', first_name='B', last_name='1', role='employee', company=company_b,
        )
        u1.last_login = timezone.now()
        u1.save(update_fields=['last_login'])

        api_client.force_authenticate(user=superadmin)
        r_all = api_client.get(SUPERADMIN_URL, {'period': '7d'})
        assert r_all.json()['overview']['total_users'] >= 3
        r_a = api_client.get(
            SUPERADMIN_URL,
            {'period': '7d', 'company_id': company_a.id},
        )
        assert r_a.json()['overview']['total_users'] == 2
        assert r_a.json()['overview']['total_companies'] == 1
        assert r_a.json()['overview']['active_companies'] in (0, 1)

    def test_resource_type_filters_bookings_today(
        self, api_client, superadmin, company_a, employee,
    ):
        fixed = _aware_local(2026, 5, 2, 12, 0, 0)
        day_start = _aware_local(2026, 5, 2, 0, 0, 0)

        desk = Resource.objects.create(name='D1', resource_type='desk')
        room = Resource.objects.create(name='M1', resource_type='meeting_room')

        with patch('django.utils.timezone.now', return_value=fixed):
            Booking.objects.create(
                resource=desk,
                user=employee,
                company=company_a,
                start_time=day_start + timedelta(hours=2),
                end_time=day_start + timedelta(hours=3),
                status='confirmed',
            )
            Booking.objects.create(
                resource=room,
                user=employee,
                company=company_a,
                start_time=day_start + timedelta(hours=4),
                end_time=day_start + timedelta(hours=5),
                status='confirmed',
            )

        api_client.force_authenticate(user=superadmin)
        with patch('django.utils.timezone.now', return_value=fixed):
            r0 = api_client.get(SUPERADMIN_URL, {'period': '7d'})
            r_desk = api_client.get(
                SUPERADMIN_URL,
                {'period': '7d', 'resource_type': 'desk'},
            )
            r_room = api_client.get(
                SUPERADMIN_URL,
                {'period': '7d', 'resource_type': 'meeting_room'},
            )

        assert r0.json()['overview']['bookings_today'] == 2
        assert r_desk.json()['overview']['bookings_today'] == 1
        assert r_room.json()['overview']['bookings_today'] == 1

    def test_company_not_found_404(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        r = api_client.get(SUPERADMIN_URL, {'period': '7d', 'company_id': 999999999})
        assert r.status_code == status.HTTP_404_NOT_FOUND

    def test_guests_today_access_log(self, api_client, superadmin, company_a, employee):
        fixed = _aware_local(2026, 5, 2, 12, 0, 0)
        gp = GuestPass.objects.create(
            created_by=employee,
            company=company_a,
            guest_name='G',
            guest_email='g@test.com',
            valid_from=fixed - timedelta(days=1),
            valid_until=fixed + timedelta(days=1),
        )
        log = AccessLog.objects.create(
            guest_pass=gp,
            is_entry=True,
            method='qr',
        )
        AccessLog.objects.filter(pk=log.pk).update(created_at=fixed)

        api_client.force_authenticate(user=superadmin)
        with patch('django.utils.timezone.now', return_value=fixed):
            r = api_client.get(SUPERADMIN_URL, {'period': '7d', 'company_id': company_a.id})
        assert r.json()['overview']['guests_today'] >= 1

    def test_open_service_requests_excludes_completed(self, api_client, superadmin, company_a):
        ServiceRequest.objects.create(
            company=company_a,
            request_type='general',
            status='new',
        )
        ServiceRequest.objects.create(
            company=company_a,
            request_type='general',
            status='completed',
        )

        api_client.force_authenticate(user=superadmin)
        r = api_client.get(SUPERADMIN_URL, {'period': '7d', 'company_id': company_a.id})
        assert r.json()['overview']['open_service_requests'] == 1
