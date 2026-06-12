from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.bookings.models import Booking, BookingCancellationAudit, Resource
from apps.companies.models import Company
from apps.users.models import User

AUDIT_URL = '/api/v1/bookings/cancellation-audit/'


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company_a(db):
    return Company.objects.create(name='Audit Company A', plan='basic')


@pytest.fixture
def company_b(db):
    return Company.objects.create(name='Audit Company B', plan='basic')


@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email='superadmin@audit.test',
        password='pass',
        first_name='Super',
        last_name='Admin',
        role='superadmin',
        is_email_verified=True,
    )


@pytest.fixture
def company_admin_a(db, company_a):
    return User.objects.create_user(
        email='company_admin_a@audit.test',
        password='pass',
        first_name='Admin',
        last_name='A',
        role='company_admin',
        company=company_a,
        is_email_verified=True,
    )


@pytest.fixture
def employee_a(db, company_a):
    return User.objects.create_user(
        email='employee_a@audit.test',
        password='pass',
        first_name='Employee',
        last_name='A',
        role='employee',
        company=company_a,
        is_email_verified=True,
    )


@pytest.fixture
def guest_user(db):
    return User.objects.create_user(
        email='guest@audit.test',
        password='pass',
        first_name='Guest',
        last_name='User',
        role='guest',
        is_email_verified=True,
    )


@pytest.fixture
def resource(db):
    return Resource.objects.create(
        name='Audit Desk',
        resource_type='desk',
        available_days=[0, 1, 2, 3, 4, 5, 6],
    )


@pytest.fixture
def audit_dataset(db, company_a, company_b, company_admin_a, resource):
    now = timezone.now()
    start = now + timedelta(days=2)

    canceller_b = User.objects.create_user(
        email='canceller_b@audit.test',
        password='pass',
        first_name='Canceller',
        last_name='B',
        role='company_admin',
        company=company_b,
        is_email_verified=True,
    )

    booking_a = Booking.objects.create(
        resource=resource,
        user=company_admin_a,
        company=company_a,
        start_time=start,
        end_time=start + timedelta(hours=1),
        status='cancelled',
    )
    audit_a = BookingCancellationAudit.objects.create(
        booking=booking_a,
        cancelled_by=company_admin_a,
        cancel_reason='Reason A',
        cancelled_at=now - timedelta(hours=2),
    )

    booking_b = Booking.objects.create(
        resource=resource,
        user=canceller_b,
        company=company_b,
        start_time=start + timedelta(days=1),
        end_time=start + timedelta(days=1, hours=1),
        status='cancelled',
    )
    audit_b = BookingCancellationAudit.objects.create(
        booking=booking_b,
        cancelled_by=canceller_b,
        cancel_reason='Reason B',
        cancelled_at=now - timedelta(hours=1),
    )

    return {
        'audit_a': audit_a,
        'audit_b': audit_b,
        'booking_a': booking_a,
        'booking_b': booking_b,
        'company_admin_a': company_admin_a,
        'canceller_b': canceller_b,
    }


def _results(response):
    data = response.json()
    if isinstance(data, dict) and 'results' in data:
        return data['results']
    return data


@pytest.mark.django_db
class TestBookingCancellationAuditList:

    def test_superadmin_sees_all_audits(self, api_client, superadmin, audit_dataset):
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(AUDIT_URL, {'page_size': 100})

        assert response.status_code == status.HTTP_200_OK
        ids = {item['id'] for item in _results(response)}
        assert audit_dataset['audit_a'].id in ids
        assert audit_dataset['audit_b'].id in ids

    def test_company_admin_sees_only_own_company_audits(
        self, api_client, company_admin_a, audit_dataset
    ):
        api_client.force_authenticate(user=company_admin_a)
        response = api_client.get(AUDIT_URL, {'page_size': 100})

        assert response.status_code == status.HTTP_200_OK
        ids = {item['id'] for item in _results(response)}
        assert audit_dataset['audit_a'].id in ids
        assert audit_dataset['audit_b'].id not in ids

    def test_employee_gets_403(self, api_client, employee_a):
        api_client.force_authenticate(user=employee_a)
        response = api_client.get(AUDIT_URL)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_guest_gets_403(self, api_client, guest_user):
        api_client.force_authenticate(user=guest_user)
        response = api_client.get(AUDIT_URL)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_unauthenticated_gets_401(self, api_client):
        response = api_client.get(AUDIT_URL)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_response_shape(self, api_client, company_admin_a, audit_dataset):
        api_client.force_authenticate(user=company_admin_a)
        response = api_client.get(AUDIT_URL, {'page_size': 100})

        assert response.status_code == status.HTTP_200_OK
        results = _results(response)
        assert len(results) >= 1
        item = next(r for r in results if r['id'] == audit_dataset['audit_a'].id)

        assert item['booking_id'] == audit_dataset['booking_a'].id
        assert item['cancel_reason'] == 'Reason A'
        assert 'cancelled_at' in item

        cancelled_by = item['cancelled_by']
        assert cancelled_by is not None
        assert cancelled_by['id'] == audit_dataset['company_admin_a'].id
        assert 'full_name' in cancelled_by

    def test_cancelled_by_null_when_user_deleted(self, api_client, superadmin, resource, db):
        booking = Booking.objects.create(
            resource=resource,
            user=superadmin,
            company=None,
            start_time=timezone.now() + timedelta(days=3),
            end_time=timezone.now() + timedelta(days=3, hours=1),
            status='cancelled',
        )
        audit = BookingCancellationAudit.objects.create(
            booking=booking,
            cancelled_by=None,
            cancel_reason='User removed',
            cancelled_at=timezone.now(),
        )

        api_client.force_authenticate(user=superadmin)
        response = api_client.get(AUDIT_URL, {'page_size': 100})

        assert response.status_code == status.HTTP_200_OK
        results = _results(response)
        matching = [r for r in results if r['id'] == audit.id]
        assert matching
        assert matching[0]['cancelled_by'] is None

    def test_filter_by_booking(self, api_client, superadmin, audit_dataset):
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(AUDIT_URL, {
            'booking': audit_dataset['booking_a'].id,
            'page_size': 100,
        })

        assert response.status_code == status.HTTP_200_OK
        ids = {item['id'] for item in _results(response)}
        assert audit_dataset['audit_a'].id in ids
        assert audit_dataset['audit_b'].id not in ids

    def test_filter_by_cancelled_by(self, api_client, superadmin, audit_dataset):
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(AUDIT_URL, {
            'cancelled_by': audit_dataset['canceller_b'].id,
            'page_size': 100,
        })

        assert response.status_code == status.HTTP_200_OK
        ids = {item['id'] for item in _results(response)}
        assert audit_dataset['audit_b'].id in ids
        assert audit_dataset['audit_a'].id not in ids

    def test_filter_by_cancelled_at_after(self, api_client, superadmin, audit_dataset):
        # audit_a: cancelled_at = now - 2h; audit_b: cancelled_at = now - 1h
        # filter: cancelled_at_after = now - 90min → only audit_b
        threshold = (timezone.now() - timedelta(minutes=90)).isoformat()
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(AUDIT_URL, {
            'cancelled_at_after': threshold,
            'page_size': 100,
        })

        assert response.status_code == status.HTTP_200_OK
        ids = {item['id'] for item in _results(response)}
        assert audit_dataset['audit_b'].id in ids
        assert audit_dataset['audit_a'].id not in ids

    def test_filter_by_cancelled_at_before(self, api_client, superadmin, audit_dataset):
        # audit_a: now - 2h; audit_b: now - 1h
        # filter: cancelled_at_before = now - 90min → only audit_a
        threshold = (timezone.now() - timedelta(minutes=90)).isoformat()
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(AUDIT_URL, {
            'cancelled_at_before': threshold,
            'page_size': 100,
        })

        assert response.status_code == status.HTTP_200_OK
        ids = {item['id'] for item in _results(response)}
        assert audit_dataset['audit_a'].id in ids
        assert audit_dataset['audit_b'].id not in ids

    def test_default_ordering_is_descending_cancelled_at(
        self, api_client, superadmin, audit_dataset
    ):
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(AUDIT_URL, {'page_size': 100})

        assert response.status_code == status.HTTP_200_OK
        results = _results(response)
        relevant = [r for r in results if r['id'] in (
            audit_dataset['audit_a'].id, audit_dataset['audit_b'].id
        )]
        assert len(relevant) == 2
        # audit_b (now-1h) должен быть перед audit_a (now-2h)
        relevant_ids = [r['id'] for r in relevant]
        assert relevant_ids.index(audit_dataset['audit_b'].id) < relevant_ids.index(audit_dataset['audit_a'].id)
