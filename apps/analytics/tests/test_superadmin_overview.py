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

    def test_extended_sections_exist_in_payload(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        r = api_client.get(SUPERADMIN_URL, {'period': '30d'})
        assert r.status_code == status.HTTP_200_OK
        body = r.json()
        assert 'resource_utilization' in body
        assert 'peak_hours' in body
        assert 'new_registrations' in body
        assert 'service_requests_by_type' in body
        assert 'top_resources' in body
        assert 'top_companies' in body
        assert 'low_utilization' in body

    def test_extended_analytics_ac_values(
        self,
        api_client,
        superadmin,
        company_a,
        company_b,
    ):
        base = _aware_local(2026, 5, 2, 9, 0, 0)

        emp_a = User.objects.create_user(
            email='extended-a@x.test',
            password='p',
            first_name='Ext',
            last_name='A',
            role='employee',
            company=company_a,
        )
        emp_b = User.objects.create_user(
            email='extended-b@x.test',
            password='p',
            first_name='Ext',
            last_name='B',
            role='employee',
            company=company_b,
        )

        User.objects.filter(pk=emp_a.pk).update(date_joined=base - timedelta(days=2))
        # 7d window is inclusive [today - 6d, today]; keep emp_b inside it (base - 10d falls outside).
        User.objects.filter(pk=emp_b.pk).update(date_joined=base - timedelta(days=5))

        desk_busy = Resource.objects.create(name='Desk Busy', resource_type='desk')
        desk_low = Resource.objects.create(name='Desk Low', resource_type='desk')
        room_hot = Resource.objects.create(name='Room Hot', resource_type='meeting_room')
        parking_hot = Resource.objects.create(name='Park Hot', resource_type='parking')
        capsule_hot = Resource.objects.create(name='Capsule Hot', resource_type='capsule')

        # 2026-05-01, hour 10, Friday (4) — two bookings for heatmap peak
        Booking.objects.create(
            resource=desk_busy,
            user=emp_a,
            company=company_a,
            start_time=_aware_local(2026, 5, 1, 10, 0, 0),
            end_time=_aware_local(2026, 5, 1, 11, 0, 0),
            status='confirmed',
        )
        Booking.objects.create(
            resource=room_hot,
            user=emp_a,
            company=company_a,
            start_time=_aware_local(2026, 5, 1, 10, 30, 0),
            end_time=_aware_local(2026, 5, 1, 11, 30, 0),
            status='confirmed',
        )

        # 2026-05-02, hour 9, Saturday (5)
        Booking.objects.create(
            resource=parking_hot,
            user=emp_b,
            company=company_b,
            start_time=_aware_local(2026, 5, 2, 9, 0, 0),
            end_time=_aware_local(2026, 5, 2, 10, 0, 0),
            status='confirmed',
        )
        Booking.objects.create(
            resource=capsule_hot,
            user=emp_b,
            company=company_b,
            start_time=_aware_local(2026, 5, 2, 9, 30, 0),
            end_time=_aware_local(2026, 5, 2, 10, 0, 0),
            status='confirmed',
        )

        # Additional booking to push top resources/companies
        Booking.objects.create(
            resource=desk_busy,
            user=emp_a,
            company=company_a,
            start_time=_aware_local(2026, 5, 2, 12, 0, 0),
            end_time=_aware_local(2026, 5, 2, 13, 0, 0),
            status='confirmed',
        )
        # Low-utilized resource (single booking in wide window)
        Booking.objects.create(
            resource=desk_low,
            user=emp_a,
            company=company_a,
            start_time=_aware_local(2026, 5, 2, 14, 0, 0),
            end_time=_aware_local(2026, 5, 2, 15, 0, 0),
            status='confirmed',
        )

        sr_clean_new = ServiceRequest.objects.create(
            company=company_a, request_type='cleaning', status='new',
        )
        sr_clean_done = ServiceRequest.objects.create(
            company=company_a, request_type='cleaning', status='completed',
        )
        sr_repair = ServiceRequest.objects.create(
            company=company_b, request_type='repair', status='accepted',
        )
        # Analytics filters by created_at inside period_*; default auto timestamps use wall-clock time.
        ts_in_period = base - timedelta(hours=1)
        ServiceRequest.objects.filter(pk__in=[sr_clean_new.pk, sr_clean_done.pk, sr_repair.pk]).update(
            created_at=ts_in_period,
        )

        api_client.force_authenticate(user=superadmin)
        with patch('django.utils.timezone.now', return_value=base):
            r = api_client.get(SUPERADMIN_URL, {'period': '7d'})
        assert r.status_code == status.HTTP_200_OK
        data = r.json()

        # resource_utilization rows and per-type counters
        util_by_date = {row['date']: row for row in data['resource_utilization']}
        assert util_by_date['2026-05-01']['desk_bookings'] == 1
        assert util_by_date['2026-05-01']['room_bookings'] == 1
        assert util_by_date['2026-05-02']['parking_bookings'] == 1
        assert util_by_date['2026-05-02']['capsule_bookings'] == 1

        # peak hours heatmap bins
        peak_lookup = {(row['day_of_week'], row['hour']): row['booking_count'] for row in data['peak_hours']}
        assert peak_lookup[(4, 10)] == 2
        assert peak_lookup[(5, 9)] == 2

        # registrations grouped by ISO week
        registrations = {row['week']: row['count'] for row in data['new_registrations']}
        assert sum(registrations.values()) >= 2

        # service requests grouped by request_type
        sr_by_type = {row['type']: row['count'] for row in data['service_requests_by_type']}
        assert sr_by_type['cleaning'] == 2
        assert sr_by_type['repair'] == 1

        # top tables are capped and include expected leaders
        assert len(data['top_resources']) <= 5
        assert len(data['top_companies']) <= 5
        assert data['top_resources'][0]['name'] == 'Desk Busy'
        assert data['top_resources'][0]['booking_count'] == 2
        assert data['top_companies'][0]['company_name'] == company_a.name
        assert data['top_companies'][0]['booking_count'] == 4

        # low-utilization table only contains rows under 20%
        assert all(item['utilization_percent'] < 20 for item in data['low_utilization'])
