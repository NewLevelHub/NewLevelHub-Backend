"""
Integration tests for company lifecycle endpoints:

  POST   /api/v1/companies/<id>/deactivate/   — superadmin only
  POST   /api/v1/companies/<id>/activate/     — superadmin only
  DELETE /api/v1/companies/<id>/              — superadmin only, requires ?confirm=true

Coverage:
  - deactivate: company.is_active → False, all members is_active → False,
    confirmed bookings status → 'cancelled'
  - deactivate: non-superadmin → 403
  - activate: company.is_active → True, all members is_active → True
  - activate: bookings are NOT restored
  - activate: non-superadmin → 403
  - delete with ?confirm=true → 204, company row gone
  - delete without ?confirm=true → 400 with error envelope
  - delete: non-superadmin → 403
  - delete: unauthenticated → 401
"""

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def company(db):
    from apps.companies.models import Company
    return Company.objects.create(name='Lifecycle Corp', is_active=True)


@pytest.fixture
def superadmin(db):
    from apps.users.models import User
    return User.objects.create_user(
        email='superadmin@test.com',
        password='testpass123',
        first_name='Super',
        last_name='Admin',
        role='superadmin',
    )


@pytest.fixture
def company_admin(db, company):
    from apps.users.models import User
    return User.objects.create_user(
        email='cadmin@test.com',
        password='testpass123',
        first_name='Company',
        last_name='Admin',
        role='company_admin',
        company=company,
    )


@pytest.fixture
def employee(db, company):
    from apps.users.models import User
    return User.objects.create_user(
        email='employee@test.com',
        password='testpass123',
        first_name='Jane',
        last_name='Doe',
        role='employee',
        company=company,
    )


@pytest.fixture
def guest_user(db):
    from apps.users.models import User
    return User.objects.create_user(
        email='guest@test.com',
        password='testpass123',
        first_name='Guest',
        last_name='User',
        role='guest',
    )


@pytest.fixture
def resource(db):
    from apps.bookings.models import Resource
    return Resource.objects.create(
        name='Meeting Room A',
        resource_type='meeting_room',
        available_days=[0, 1, 2, 3, 4],
    )


@pytest.fixture
def confirmed_booking(db, company, employee, resource):
    from apps.bookings.models import Booking
    now = timezone.now()
    return Booking.objects.create(
        resource=resource,
        user=employee,
        company=company,
        start_time=now,
        end_time=now,
        status='confirmed',
    )


@pytest.fixture
def cancelled_booking(db, company, employee, resource):
    from apps.bookings.models import Booking
    now = timezone.now()
    return Booking.objects.create(
        resource=resource,
        user=employee,
        company=company,
        start_time=now,
        end_time=now,
        status='cancelled',
    )


def _auth(client, user):
    """Force-authenticate the client as the given user."""
    client.force_authenticate(user=user)
    return client


def _deactivate_url(company_id):
    return f'/api/v1/companies/{company_id}/deactivate/'


def _activate_url(company_id):
    return f'/api/v1/companies/{company_id}/activate/'


def _delete_url(company_id, confirm=None):
    url = f'/api/v1/companies/{company_id}/'
    if confirm is not None:
        url += f'?confirm={confirm}'
    return url


# ---------------------------------------------------------------------------
# Deactivate
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestDeactivate:

    def test_superadmin_deactivates_company(self, client, superadmin, company):
        response = _auth(client, superadmin).post(_deactivate_url(company.pk))

        assert response.status_code == status.HTTP_200_OK
        company.refresh_from_db()
        assert company.is_active is False

    def test_deactivate_sets_members_inactive(
        self, client, superadmin, company, company_admin, employee
    ):
        from apps.users.models import User

        _auth(client, superadmin).post(_deactivate_url(company.pk))

        for user in User.objects.filter(company=company):
            assert user.is_active is False, f'{user.email} should be inactive'

    def test_deactivate_cancels_confirmed_bookings(
        self, client, superadmin, company, confirmed_booking
    ):
        _auth(client, superadmin).post(_deactivate_url(company.pk))

        confirmed_booking.refresh_from_db()
        assert confirmed_booking.status == 'cancelled'

    def test_deactivate_does_not_touch_already_cancelled_bookings(
        self, client, superadmin, company, cancelled_booking
    ):
        _auth(client, superadmin).post(_deactivate_url(company.pk))

        cancelled_booking.refresh_from_db()
        assert cancelled_booking.status == 'cancelled'

    def test_deactivate_returns_company_data(self, client, superadmin, company):
        response = _auth(client, superadmin).post(_deactivate_url(company.pk))

        assert response.status_code == status.HTTP_200_OK
        assert response.data['id'] == company.pk
        assert response.data['is_active'] is False

    def test_company_admin_cannot_deactivate(self, client, company_admin, company):
        response = _auth(client, company_admin).post(_deactivate_url(company.pk))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_employee_cannot_deactivate(self, client, employee, company):
        response = _auth(client, employee).post(_deactivate_url(company.pk))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_guest_cannot_deactivate(self, client, guest_user, company):
        response = _auth(client, guest_user).post(_deactivate_url(company.pk))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_unauthenticated_cannot_deactivate(self, client, company):
        response = client.post(_deactivate_url(company.pk))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


# ---------------------------------------------------------------------------
# Activate
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestActivate:

    def _deactivate(self, client, superadmin, company):
        """Helper: deactivate before testing activation."""
        _auth(client, superadmin).post(_deactivate_url(company.pk))
        client.force_authenticate(user=superadmin)

    def test_superadmin_activates_company(self, client, superadmin, company):
        self._deactivate(client, superadmin, company)

        response = _auth(client, superadmin).post(_activate_url(company.pk))

        assert response.status_code == status.HTTP_200_OK
        company.refresh_from_db()
        assert company.is_active is True

    def test_activate_sets_members_active(
        self, client, superadmin, company, company_admin, employee
    ):
        from apps.users.models import User

        self._deactivate(client, superadmin, company)
        _auth(client, superadmin).post(_activate_url(company.pk))

        for user in User.objects.filter(company=company):
            assert user.is_active is True, f'{user.email} should be active'

    def test_activate_does_not_restore_bookings(
        self, client, superadmin, company, confirmed_booking
    ):
        """Bookings cancelled during deactivation must stay cancelled after activate."""
        self._deactivate(client, superadmin, company)
        _auth(client, superadmin).post(_activate_url(company.pk))

        # Re-fetch directly from DB to confirm status was not rolled back
        confirmed_booking.refresh_from_db()
        assert confirmed_booking.status == 'cancelled'

    def test_activate_returns_company_data(self, client, superadmin, company):
        self._deactivate(client, superadmin, company)
        response = _auth(client, superadmin).post(_activate_url(company.pk))

        assert response.status_code == status.HTTP_200_OK
        assert response.data['id'] == company.pk
        assert response.data['is_active'] is True

    def test_company_admin_cannot_activate(self, client, company_admin, company):
        response = _auth(client, company_admin).post(_activate_url(company.pk))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_employee_cannot_activate(self, client, employee, company):
        response = _auth(client, employee).post(_activate_url(company.pk))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_guest_cannot_activate(self, client, guest_user, company):
        response = _auth(client, guest_user).post(_activate_url(company.pk))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_unauthenticated_cannot_activate(self, client, company):
        response = client.post(_activate_url(company.pk))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


# ---------------------------------------------------------------------------
# Delete (destroy)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestDelete:

    def test_superadmin_can_delete_with_confirm(self, client, superadmin, company):
        pk = company.pk
        response = _auth(client, superadmin).delete(_delete_url(pk, confirm='true'))

        assert response.status_code == status.HTTP_204_NO_CONTENT

        from apps.companies.models import Company
        assert not Company.objects.filter(pk=pk).exists()

    def test_delete_without_confirm_returns_400(self, client, superadmin, company):
        response = _auth(client, superadmin).delete(_delete_url(company.pk))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        # Custom exception handler wraps into error envelope
        assert response.data['success'] is False
        assert 'confirm' in str(response.data['error']['details']).lower()

    def test_delete_with_wrong_confirm_value_returns_400(
        self, client, superadmin, company
    ):
        response = _auth(client, superadmin).delete(
            _delete_url(company.pk, confirm='yes')
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_company_admin_cannot_delete(self, client, company_admin, company):
        response = _auth(client, company_admin).delete(
            _delete_url(company.pk, confirm='true')
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_employee_cannot_delete(self, client, employee, company):
        response = _auth(client, employee).delete(
            _delete_url(company.pk, confirm='true')
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_guest_cannot_delete(self, client, guest_user, company):
        response = _auth(client, guest_user).delete(
            _delete_url(company.pk, confirm='true')
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_unauthenticated_cannot_delete(self, client, company):
        response = client.delete(_delete_url(company.pk, confirm='true'))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
