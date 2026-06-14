"""
Acceptance tests for POST /api/v1/bookings/reservations/bulk-cancel/

AC1:  employee cancels their own 2 bookings → 200, cancelled=2, skipped=0
AC2:  employee tries to cancel another user's bookings → cancelled=0, skipped=N (no error)
AC3:  company_admin cancels other employees' bookings in their company → 200, cancelled=N
AC4:  already-cancelled booking → lands in skipped_ids
AC5:  booking whose start_time <= now → lands in skipped_ids
AC6:  booking_ids empty [] → 400
AC7:  booking_ids > 50 elements → 400
AC8:  bookings from another company → company isolation prevents access, lands in skipped_ids
AC9:  transaction.atomic — if create_notification raises, cancellations are not persisted
AC10: create_notification is called once per successfully cancelled booking
"""
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.bookings.models import Booking, BookingCancellationAudit, Resource
from apps.companies.models import Company
from apps.users.models import User

BULK_CANCEL_URL = '/api/v1/bookings/reservations/bulk-cancel/'


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company_a(db):
    return Company.objects.create(name='BulkCancel Company A', plan='basic')


@pytest.fixture
def company_b(db):
    return Company.objects.create(name='BulkCancel Company B', plan='basic')


@pytest.fixture
def resource(db):
    return Resource.objects.create(
        name='Bulk Cancel Desk',
        resource_type='desk',
        available_days=[0, 1, 2, 3, 4, 5, 6],
    )


@pytest.fixture
def employee_a(db, company_a):
    return User.objects.create_user(
        email='bulk_employee_a@test.test',
        password='pass',
        first_name='Emp',
        last_name='A',
        role='employee',
        company=company_a,
        is_email_verified=True,
    )


@pytest.fixture
def employee_a2(db, company_a):
    return User.objects.create_user(
        email='bulk_employee_a2@test.test',
        password='pass',
        first_name='Emp',
        last_name='A2',
        role='employee',
        company=company_a,
        is_email_verified=True,
    )


@pytest.fixture
def company_admin_a(db, company_a):
    return User.objects.create_user(
        email='bulk_admin_a@test.test',
        password='pass',
        first_name='Admin',
        last_name='A',
        role='company_admin',
        company=company_a,
        is_email_verified=True,
    )


@pytest.fixture
def employee_b(db, company_b):
    return User.objects.create_user(
        email='bulk_employee_b@test.test',
        password='pass',
        first_name='Emp',
        last_name='B',
        role='employee',
        company=company_b,
        is_email_verified=True,
    )


@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email='bulk_superadmin@test.test',
        password='pass',
        first_name='Super',
        last_name='Admin',
        role='superadmin',
        is_email_verified=True,
    )


def _future_booking(resource, user, company, offset_days=2, offset_hours=0):
    """Create a confirmed booking starting in the future."""
    base = timezone.now().replace(minute=0, second=0, microsecond=0) + timedelta(days=offset_days, hours=offset_hours)
    return Booking.objects.create(
        resource=resource,
        user=user,
        company=company,
        start_time=base,
        end_time=base + timedelta(hours=1),
        status='confirmed',
    )


def _past_booking(resource, user, company):
    """Create a confirmed booking that has already started (start_time in the past)."""
    base = timezone.now() - timedelta(hours=2)
    return Booking.objects.create(
        resource=resource,
        user=user,
        company=company,
        start_time=base,
        end_time=base + timedelta(hours=1),
        status='confirmed',
    )


# ---------------------------------------------------------------------------
# AC1: employee cancels their own 2 bookings → 200, cancelled=2, skipped=0
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ac1_employee_cancels_own_bookings(api_client, employee_a, company_a, resource):
    b1 = _future_booking(resource, employee_a, company_a, offset_days=2)
    b2 = _future_booking(resource, employee_a, company_a, offset_days=3)

    api_client.force_authenticate(user=employee_a)
    response = api_client.post(BULK_CANCEL_URL, {'booking_ids': [b1.pk, b2.pk]}, format='json')

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data['cancelled'] == 2
    assert data['skipped'] == 0
    assert data['skipped_ids'] == []

    b1.refresh_from_db()
    b2.refresh_from_db()
    assert b1.status == 'cancelled'
    assert b2.status == 'cancelled'

    assert BookingCancellationAudit.objects.filter(booking__in=[b1, b2]).count() == 2


# ---------------------------------------------------------------------------
# AC2: employee tries to cancel another user's bookings → skipped, no error
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ac2_employee_cannot_cancel_others_bookings(api_client, employee_a, employee_a2, company_a, resource):
    b1 = _future_booking(resource, employee_a2, company_a, offset_days=2)
    b2 = _future_booking(resource, employee_a2, company_a, offset_days=3)

    api_client.force_authenticate(user=employee_a)
    response = api_client.post(BULK_CANCEL_URL, {'booking_ids': [b1.pk, b2.pk]}, format='json')

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data['cancelled'] == 0
    assert data['skipped'] == 2
    assert set(data['skipped_ids']) == {b1.pk, b2.pk}

    b1.refresh_from_db()
    b2.refresh_from_db()
    assert b1.status == 'confirmed'
    assert b2.status == 'confirmed'


# ---------------------------------------------------------------------------
# AC3: company_admin cancels other employees' bookings in their company
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ac3_company_admin_cancels_others_bookings(
    api_client, company_admin_a, employee_a, employee_a2, company_a, resource
):
    b1 = _future_booking(resource, employee_a, company_a, offset_days=2)
    b2 = _future_booking(resource, employee_a2, company_a, offset_days=3)

    api_client.force_authenticate(user=company_admin_a)
    response = api_client.post(
        BULK_CANCEL_URL, {'booking_ids': [b1.pk, b2.pk], 'reason': 'Admin override'}, format='json'
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data['cancelled'] == 2
    assert data['skipped'] == 0

    b1.refresh_from_db()
    b2.refresh_from_db()
    assert b1.status == 'cancelled'
    assert b2.status == 'cancelled'
    assert b1.cancel_reason == 'Admin override'


# ---------------------------------------------------------------------------
# AC4: already-cancelled booking → skipped_ids
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ac4_already_cancelled_booking_is_skipped(api_client, employee_a, company_a, resource):
    b1 = _future_booking(resource, employee_a, company_a, offset_days=2)
    b1.status = 'cancelled'
    b1.save()

    api_client.force_authenticate(user=employee_a)
    response = api_client.post(BULK_CANCEL_URL, {'booking_ids': [b1.pk]}, format='json')

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data['cancelled'] == 0
    assert data['skipped'] == 1
    assert b1.pk in data['skipped_ids']


# ---------------------------------------------------------------------------
# AC5: booking that has already started → skipped_ids
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ac5_already_started_booking_is_skipped(api_client, employee_a, company_a, resource):
    b1 = _past_booking(resource, employee_a, company_a)

    api_client.force_authenticate(user=employee_a)
    response = api_client.post(BULK_CANCEL_URL, {'booking_ids': [b1.pk]}, format='json')

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data['cancelled'] == 0
    assert data['skipped'] == 1
    assert b1.pk in data['skipped_ids']

    b1.refresh_from_db()
    assert b1.status == 'confirmed'


# ---------------------------------------------------------------------------
# AC6: booking_ids empty [] → 400
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ac6_empty_booking_ids_returns_400(api_client, employee_a):
    api_client.force_authenticate(user=employee_a)
    response = api_client.post(BULK_CANCEL_URL, {'booking_ids': []}, format='json')

    assert response.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# AC7: booking_ids > 50 elements → 400
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ac7_too_many_booking_ids_returns_400(api_client, employee_a):
    api_client.force_authenticate(user=employee_a)
    response = api_client.post(BULK_CANCEL_URL, {'booking_ids': list(range(1, 52))}, format='json')

    assert response.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# AC8: bookings from another company → company isolation, skipped
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ac8_other_company_bookings_are_inaccessible(api_client, employee_a, employee_b, company_b, resource):
    # booking belongs to company_b, employee_a is in company_a
    b_other = _future_booking(resource, employee_b, company_b, offset_days=2)

    api_client.force_authenticate(user=employee_a)
    response = api_client.post(BULK_CANCEL_URL, {'booking_ids': [b_other.pk]}, format='json')

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data['cancelled'] == 0
    assert data['skipped'] == 1
    assert b_other.pk in data['skipped_ids']

    b_other.refresh_from_db()
    assert b_other.status == 'confirmed'


# ---------------------------------------------------------------------------
# AC9: if create_notification raises, the whole transaction is rolled back
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ac9_notification_failure_rolls_back_cancellations(api_client, employee_a, company_a, resource):
    b1 = _future_booking(resource, employee_a, company_a, offset_days=2)

    api_client.force_authenticate(user=employee_a)

    with patch(
        'apps.bookings.views.create_notification',
        side_effect=Exception('notification service down'),
    ):
        try:
            api_client.post(BULK_CANCEL_URL, {'booking_ids': [b1.pk]}, format='json')
        except Exception:
            pass

    b1.refresh_from_db()
    # The booking must remain confirmed because the transaction was rolled back.
    assert b1.status == 'confirmed'
    assert BookingCancellationAudit.objects.filter(booking=b1).count() == 0


# ---------------------------------------------------------------------------
# AC10: create_notification is called for each cancelled booking
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ac10_notification_sent_per_cancelled_booking(api_client, employee_a, company_a, resource):
    b1 = _future_booking(resource, employee_a, company_a, offset_days=2)
    b2 = _future_booking(resource, employee_a, company_a, offset_days=3)

    api_client.force_authenticate(user=employee_a)

    with patch('apps.bookings.views.create_notification') as mock_notify:
        response = api_client.post(BULK_CANCEL_URL, {'booking_ids': [b1.pk, b2.pk]}, format='json')

    assert response.status_code == status.HTTP_200_OK
    assert mock_notify.call_count == 2

    notified_users = {c.kwargs.get('user') or c.args[0] for c in mock_notify.call_args_list}
    assert employee_a in notified_users


# ---------------------------------------------------------------------------
# Additional: unauthenticated → 401
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_unauthenticated_returns_401(api_client, employee_a, company_a, resource):
    b1 = _future_booking(resource, employee_a, company_a, offset_days=2)
    response = api_client.post(BULK_CANCEL_URL, {'booking_ids': [b1.pk]}, format='json')
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
