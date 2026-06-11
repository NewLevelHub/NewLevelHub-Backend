import uuid
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.access.models import AccessLog
from apps.bookings.models import Booking, Resource
from apps.companies.models import Company
from apps.users.models import User

RESERVATIONS_URL = '/api/v1/bookings/reservations/'
VALIDATE_QR_URL = '/api/v1/bookings/reservations/validate-qr/'


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='Booking QR Co', plan='basic')


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='booking-qr-employee@test.local',
        password='pass',
        first_name='Ivan',
        last_name='Petrov',
        role='employee',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def reception(db):
    return User.objects.create_user(
        email='booking-qr-reception@test.local',
        password='pass',
        first_name='Desk',
        last_name='Reception',
        role='reception',
        is_email_verified=True,
    )


@pytest.fixture
def capsule_resource(db):
    return Resource.objects.create(
        name='Capsule #3',
        resource_type='capsule',
        capsule_zone='quiet',
        min_duration_minutes=60,
        max_duration_minutes=480,
        available_days=[0, 1, 2, 3, 4, 5, 6],
        available_from='08:00',
        available_until='22:00',
    )


@pytest.fixture
def desk_resource(db):
    return Resource.objects.create(
        name='Desk 1',
        resource_type='desk',
        available_days=[0, 1, 2, 3, 4, 5, 6],
        available_from='08:00',
        available_until='22:00',
    )


def _capsule_slot(hours_from_now=2, duration_hours=2):
    start = timezone.now() + timedelta(hours=hours_from_now)
    end = start + timedelta(hours=duration_hours)
    return start, end


@pytest.mark.django_db
class TestCapsuleBookingQrGeneration:
    def test_capsule_booking_create_generates_qr(self, api_client, employee, capsule_resource):
        api_client.force_authenticate(user=employee)
        start, end = _capsule_slot()

        response = api_client.post(
            RESERVATIONS_URL,
            {
                'resource_id': capsule_resource.id,
                'start_time': start.isoformat(),
                'end_time': end.isoformat(),
            },
            format='json',
        )

        assert response.status_code == status.HTTP_201_CREATED
        body = response.json()
        assert body['resource_type'] == 'capsule'
        assert body['qr_code']
        assert body['qr_image']

        booking = Booking.objects.get(pk=body['id'])
        assert booking.qr_code is not None
        assert booking.qr_image.name

    def test_desk_booking_has_no_qr(self, api_client, employee, desk_resource):
        api_client.force_authenticate(user=employee)
        start = timezone.now() + timedelta(days=1)
        start = start.replace(hour=10, minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=1)

        response = api_client.post(
            RESERVATIONS_URL,
            {
                'resource_id': desk_resource.id,
                'start_time': start.isoformat(),
                'end_time': end.isoformat(),
            },
            format='json',
        )

        assert response.status_code == status.HTTP_201_CREATED
        body = response.json()
        assert body.get('qr_code') in (None, '')
        assert body.get('qr_image') in (None, '')


@pytest.mark.django_db
class TestBookingQrValidate:
    def _make_capsule_booking(self, user, capsule_resource, *, start_offset_hours=0, duration_hours=2, status_code='confirmed'):
        start = timezone.now() + timedelta(hours=start_offset_hours)
        end = start + timedelta(hours=duration_hours)
        booking = Booking.objects.create(
            resource=capsule_resource,
            user=user,
            company=user.company,
            start_time=start,
            end_time=end,
            status=status_code,
            qr_code=uuid.uuid4(),
        )
        from apps.bookings.qr_image import generate_booking_qr_image
        generate_booking_qr_image(booking)
        return booking

    def test_reception_can_validate_active_booking(self, api_client, employee, reception, capsule_resource):
        booking = self._make_capsule_booking(employee, capsule_resource, start_offset_hours=-0.1)
        api_client.force_authenticate(user=reception)

        response = api_client.post(
            VALIDATE_QR_URL,
            {'qr_code': str(booking.qr_code)},
            format='json',
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data['valid'] is True
        assert data['user_name'] == employee.full_name
        assert data['resource_name'] == capsule_resource.name
        assert data['capsule_zone'] == 'quiet'

        booking.refresh_from_db()
        assert booking.checked_in_at is not None
        assert AccessLog.objects.filter(booking=booking, method='qr').exists()

    def test_employee_cannot_validate_booking_qr(self, api_client, employee, capsule_resource):
        booking = self._make_capsule_booking(employee, capsule_resource, start_offset_hours=-0.1)
        api_client.force_authenticate(user=employee)

        response = api_client.post(
            VALIDATE_QR_URL,
            {'qr_code': str(booking.qr_code)},
            format='json',
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_validate_cancelled_booking_returns_cancelled(self, api_client, reception, employee, capsule_resource):
        booking = self._make_capsule_booking(
            employee, capsule_resource, start_offset_hours=-0.1, status_code='cancelled',
        )
        api_client.force_authenticate(user=reception)

        response = api_client.post(
            VALIDATE_QR_URL,
            {'qr_code': str(booking.qr_code)},
            format='json',
        )

        assert response.json() == {'valid': False, 'reason': 'cancelled'}

    def test_validate_before_start_returns_not_yet_active(self, api_client, reception, employee, capsule_resource):
        booking = self._make_capsule_booking(employee, capsule_resource, start_offset_hours=2)
        api_client.force_authenticate(user=reception)

        response = api_client.post(
            VALIDATE_QR_URL,
            {'qr_code': str(booking.qr_code)},
            format='json',
        )

        data = response.json()
        assert data['valid'] is False
        assert data['reason'] == 'not_yet_active'
        assert data['available_from']

    def test_validate_after_end_returns_expired(self, api_client, reception, employee, capsule_resource):
        booking = self._make_capsule_booking(employee, capsule_resource, start_offset_hours=-4, duration_hours=1)
        api_client.force_authenticate(user=reception)

        response = api_client.post(
            VALIDATE_QR_URL,
            {'qr_code': str(booking.qr_code)},
            format='json',
        )

        assert response.json() == {'valid': False, 'reason': 'expired'}

    def test_validate_unknown_qr_returns_not_found(self, api_client, reception):
        api_client.force_authenticate(user=reception)

        response = api_client.post(
            VALIDATE_QR_URL,
            {'qr_code': '64fdbf4f-465e-40e6-8ef4-3f3c96d34ac6'},
            format='json',
        )

        assert response.json() == {'valid': False, 'reason': 'not_found'}
