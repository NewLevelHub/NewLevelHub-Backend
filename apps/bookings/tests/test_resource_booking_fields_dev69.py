"""
Tests for DEV-69:
1. ResourceSerializer exposes advance_booking_days and min_cancel_minutes.
2. Admin can PATCH these fields on a resource.
3. BookingCreateSerializer uses resource.advance_booking_days (with type-default fallback).
"""
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.bookings.models import Resource
from apps.companies.models import Company
from apps.users.models import User

RESOURCES_URL = '/api/v1/bookings/resources/'
RESERVATIONS_URL = '/api/v1/bookings/reservations/'


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='DEV69 Co', plan='basic')


@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email='superadmin@dev69.test',
        password='pass',
        first_name='S',
        last_name='A',
        role='superadmin',
    )


@pytest.fixture
def company_admin(db, company):
    return User.objects.create_user(
        email='admin@dev69.test',
        password='pass',
        first_name='CA',
        last_name='Dev69',
        role='company_admin',
        company=company,
    )


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='emp@dev69.test',
        password='pass',
        first_name='E',
        last_name='M',
        role='employee',
        company=company,
        is_email_verified=True,
    )


def _make_resource(resource_type, **kwargs):
    defaults = {
        'name': f'DEV69 {resource_type}',
        'resource_type': resource_type,
        'available_days': list(range(7)),
        'available_from': '00:00',
        'available_until': '23:59',
    }
    defaults.update(kwargs)
    return Resource.objects.create(**defaults)


def _next_weekday(days_ahead=1):
    """Return a future datetime that is a weekday, at least days_ahead from now."""
    now = timezone.localtime()
    target = now + timedelta(days=days_ahead)
    while target.weekday() >= 5:
        target += timedelta(days=1)
    return target


# ---------------------------------------------------------------------------
# 1. ResourceSerializer fields
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestResourceSerializerFields:
    """advance_booking_days and min_cancel_minutes appear in retrieve response."""

    def test_retrieve_includes_advance_booking_days(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        create_resp = api_client.post(
            RESOURCES_URL,
            {'type': 'desk', 'name': 'Field check desk', 'floor': 1},
            format='json',
        )
        assert create_resp.status_code == status.HTTP_201_CREATED
        rid = create_resp.json()['id']
        r = api_client.get(f'{RESOURCES_URL}{rid}/')
        assert r.status_code == status.HTTP_200_OK
        body = r.json()
        assert 'advance_booking_days' in body
        assert 'min_cancel_minutes' in body

    def test_retrieve_returns_correct_defaults(self, api_client, superadmin):
        """Default values from the model: advance_booking_days=14, min_cancel_minutes=30."""
        api_client.force_authenticate(user=superadmin)
        create_resp = api_client.post(
            RESOURCES_URL,
            {'type': 'desk', 'name': 'Defaults desk', 'floor': 1},
            format='json',
        )
        rid = create_resp.json()['id']
        r = api_client.get(f'{RESOURCES_URL}{rid}/')
        body = r.json()
        assert body['advance_booking_days'] == 14
        assert body['min_cancel_minutes'] == 30

    def test_create_with_custom_advance_and_cancel_fields(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        r = api_client.post(
            RESOURCES_URL,
            {
                'type': 'desk',
                'name': 'Custom advance desk',
                'floor': 2,
                'advance_booking_days': 30,
                'min_cancel_minutes': 60,
            },
            format='json',
        )
        assert r.status_code == status.HTTP_201_CREATED
        body = r.json()
        assert body['advance_booking_days'] == 30
        assert body['min_cancel_minutes'] == 60
        resource = Resource.objects.get(pk=body['id'])
        assert resource.advance_booking_days == 30
        assert resource.min_cancel_minutes == 60


# ---------------------------------------------------------------------------
# 2. Admin PATCH of advance_booking_days and min_cancel_minutes
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestResourcePatchAdvanceAndCancelFields:
    """superadmin can update advance_booking_days and min_cancel_minutes via PATCH."""

    def test_superadmin_can_patch_advance_booking_days(self, api_client, superadmin):
        resource = _make_resource('desk', advance_booking_days=14)
        api_client.force_authenticate(user=superadmin)
        r = api_client.patch(
            f'{RESOURCES_URL}{resource.id}/',
            {'advance_booking_days': 21},
            format='json',
        )
        assert r.status_code == status.HTTP_200_OK
        assert r.json()['advance_booking_days'] == 21
        resource.refresh_from_db()
        assert resource.advance_booking_days == 21

    def test_superadmin_can_patch_min_cancel_minutes(self, api_client, superadmin):
        resource = _make_resource('desk', min_cancel_minutes=30)
        api_client.force_authenticate(user=superadmin)
        r = api_client.patch(
            f'{RESOURCES_URL}{resource.id}/',
            {'min_cancel_minutes': 120},
            format='json',
        )
        assert r.status_code == status.HTTP_200_OK
        assert r.json()['min_cancel_minutes'] == 120
        resource.refresh_from_db()
        assert resource.min_cancel_minutes == 120

    def test_superadmin_can_patch_both_fields_together(self, api_client, superadmin):
        resource = _make_resource('parking', advance_booking_days=7, min_cancel_minutes=30)
        api_client.force_authenticate(user=superadmin)
        r = api_client.patch(
            f'{RESOURCES_URL}{resource.id}/',
            {'advance_booking_days': 3, 'min_cancel_minutes': 60},
            format='json',
        )
        assert r.status_code == status.HTTP_200_OK
        body = r.json()
        assert body['advance_booking_days'] == 3
        assert body['min_cancel_minutes'] == 60

    def test_employee_cannot_patch_resource(self, api_client, employee):
        """Employees get 403 on any resource PATCH (only superadmin allowed)."""
        resource = _make_resource('desk')
        api_client.force_authenticate(user=employee)
        r = api_client.patch(
            f'{RESOURCES_URL}{resource.id}/',
            {'advance_booking_days': 7},
            format='json',
        )
        assert r.status_code == status.HTTP_403_FORBIDDEN


# ---------------------------------------------------------------------------
# 3. BookingCreateSerializer uses resource.advance_booking_days
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBookingAdvanceBookingDaysFromResource:
    """Booking validation reads advance_booking_days from the resource, not hardcoded."""

    # --- Desk ---

    def test_desk_booking_respects_custom_advance_days(self, api_client, employee):
        """Desk with advance_booking_days=3: booking 4 calendar days ahead must fail."""
        resource = _make_resource('desk', advance_booking_days=3)
        api_client.force_authenticate(user=employee)
        now = timezone.localtime()
        # 4 calendar days ahead — beyond resource's custom 3-day window
        start = (now + timedelta(days=4)).replace(hour=10, minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=1)
        resp = api_client.post(RESERVATIONS_URL, {
            'resource_id': resource.id,
            'start_time': start.isoformat(),
            'end_time': end.isoformat(),
        }, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert '3 days' in str(resp.json()).lower() or '3' in str(resp.json())

    def test_desk_booking_within_custom_advance_days_succeeds(self, api_client, employee):
        """Desk with advance_booking_days=3: booking 1 calendar day ahead must succeed."""
        resource = _make_resource('desk', advance_booking_days=3)
        api_client.force_authenticate(user=employee)
        # Use 1 calendar day ahead — always within the 3-day window regardless of weekday.
        now = timezone.localtime()
        start = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=1)
        resp = api_client.post(RESERVATIONS_URL, {
            'resource_id': resource.id,
            'start_time': start.isoformat(),
            'end_time': end.isoformat(),
        }, format='json')
        assert resp.status_code == status.HTTP_201_CREATED

    def test_desk_booking_with_advance_days_14_allows_10_days_ahead(self, api_client, employee):
        """Desk with advance_booking_days=14: booking 10 calendar days ahead must succeed."""
        resource = _make_resource('desk', advance_booking_days=14)
        api_client.force_authenticate(user=employee)
        now = timezone.localtime()
        start = (now + timedelta(days=10)).replace(hour=10, minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=1)
        resp = api_client.post(RESERVATIONS_URL, {
            'resource_id': resource.id,
            'start_time': start.isoformat(),
            'end_time': end.isoformat(),
        }, format='json')
        assert resp.status_code == status.HTTP_201_CREATED

    def test_desk_booking_custom_large_advance_days_allows_far_future(self, api_client, employee):
        """Desk with advance_booking_days=60: booking 30 calendar days ahead must succeed."""
        resource = _make_resource('desk', advance_booking_days=60)
        api_client.force_authenticate(user=employee)
        now = timezone.localtime()
        start = (now + timedelta(days=30)).replace(hour=10, minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=1)
        resp = api_client.post(RESERVATIONS_URL, {
            'resource_id': resource.id,
            'start_time': start.isoformat(),
            'end_time': end.isoformat(),
        }, format='json')
        assert resp.status_code == status.HTTP_201_CREATED

    # --- Parking ---

    def test_parking_booking_respects_custom_advance_days(self, api_client, employee):
        """Parking with advance_booking_days=3: booking 5 calendar days ahead must fail."""
        resource = _make_resource('parking', advance_booking_days=3)
        api_client.force_authenticate(user=employee)
        now = timezone.localtime()
        # 5 calendar days ahead — beyond resource's custom 3-day window
        start = (now + timedelta(days=5)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = (start + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        resp = api_client.post(RESERVATIONS_URL, {
            'resource_id': resource.id,
            'start_time': start.isoformat(),
            'end_time': end.isoformat(),
        }, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert '3 days' in str(resp.json()).lower() or '3' in str(resp.json())

    def test_parking_booking_within_custom_advance_days_succeeds(self, api_client, employee):
        """Parking with advance_booking_days=3: booking 1 calendar day ahead must succeed."""
        resource = _make_resource('parking', advance_booking_days=3)
        api_client.force_authenticate(user=employee)
        # Use 1 calendar day ahead — always within the 3-day window.
        now = timezone.localtime()
        start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = (start + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        resp = api_client.post(RESERVATIONS_URL, {
            'resource_id': resource.id,
            'start_time': start.isoformat(),
            'end_time': end.isoformat(),
        }, format='json')
        assert resp.status_code == status.HTTP_201_CREATED

    def test_parking_uses_advance_booking_days_7_allows_5_days_ahead(self, api_client, employee):
        """Parking with advance_booking_days=7: booking 5 calendar days ahead passes."""
        resource = _make_resource('parking', advance_booking_days=7)
        api_client.force_authenticate(user=employee)
        now = timezone.localtime()
        start = (now + timedelta(days=5)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = (start + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        resp = api_client.post(RESERVATIONS_URL, {
            'resource_id': resource.id,
            'start_time': start.isoformat(),
            'end_time': end.isoformat(),
        }, format='json')
        assert resp.status_code == status.HTTP_201_CREATED
