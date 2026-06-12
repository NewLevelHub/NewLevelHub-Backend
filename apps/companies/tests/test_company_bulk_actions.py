"""
Integration tests for CompanyViewSet bulk actions.

Endpoints under test:
  POST   /api/v1/companies/bulk-activate/
  POST   /api/v1/companies/bulk-deactivate/
  DELETE /api/v1/companies/bulk-delete/

Acceptance criteria:
  - Unauthenticated         → 401
  - company_admin           → 403  (superadmin only)
  - employee                → 403
  - guest                   → 403
  - superadmin, empty ids   → 400
  - superadmin, missing ids → 400
  - superadmin, nonexistent ids → 404
  - superadmin, valid ids   → 200 with expected count
  - bulk_activate: already-active companies return activated=0 (no error)
  - bulk_deactivate: already-inactive companies return deactivated=0 (no error)
  - bulk_delete: soft-deletes (sets is_deleted=True, deleted_at set)
"""

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.users.models import User


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

BULK_ACTIVATE_URL = '/api/v1/companies/bulk-activate/'
BULK_DEACTIVATE_URL = '/api/v1/companies/bulk-deactivate/'
BULK_DELETE_URL = '/api/v1/companies/bulk-delete/'


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email='superadmin@test.com',
        password='pass',
        first_name='Super',
        last_name='Admin',
        role='superadmin',
    )


@pytest.fixture
def company_a(db):
    return Company.objects.create(name='Alpha Corp', plan='basic', is_active=True)


@pytest.fixture
def company_b(db):
    return Company.objects.create(name='Beta Ltd', plan='basic', is_active=True)


@pytest.fixture
def company_inactive(db):
    return Company.objects.create(name='Inactive Co', plan='basic', is_active=False)


@pytest.fixture
def company_admin(db, company_a):
    return User.objects.create_user(
        email='admin@alpha.com',
        password='pass',
        first_name='Alice',
        last_name='Admin',
        role='company_admin',
        company=company_a,
    )


@pytest.fixture
def employee(db, company_a):
    return User.objects.create_user(
        email='employee@alpha.com',
        password='pass',
        first_name='Bob',
        last_name='Worker',
        role='employee',
        company=company_a,
    )


@pytest.fixture
def guest(db):
    return User.objects.create_user(
        email='guest@test.com',
        password='pass',
        first_name='Guest',
        last_name='User',
        role='guest',
    )


def _auth(client, user):
    """Force-authenticate the given client as user."""
    client.force_authenticate(user=user)


# ===========================================================================
# bulk-activate
# ===========================================================================

class TestBulkActivate:

    def test_unauthenticated_returns_401(self, api_client, company_a):
        resp = api_client.post(BULK_ACTIVATE_URL, {'ids': [company_a.pk]}, format='json')
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_company_admin_returns_403(self, api_client, company_admin, company_a):
        _auth(api_client, company_admin)
        resp = api_client.post(BULK_ACTIVATE_URL, {'ids': [company_a.pk]}, format='json')
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_employee_returns_403(self, api_client, employee, company_a):
        _auth(api_client, employee)
        resp = api_client.post(BULK_ACTIVATE_URL, {'ids': [company_a.pk]}, format='json')
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_guest_returns_403(self, api_client, guest, company_a):
        _auth(api_client, guest)
        resp = api_client.post(BULK_ACTIVATE_URL, {'ids': [company_a.pk]}, format='json')
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_empty_ids_returns_400(self, api_client, superadmin):
        _auth(api_client, superadmin)
        resp = api_client.post(BULK_ACTIVATE_URL, {'ids': []}, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_missing_ids_returns_400(self, api_client, superadmin):
        _auth(api_client, superadmin)
        resp = api_client.post(BULK_ACTIVATE_URL, {}, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_nonexistent_ids_returns_404(self, api_client, superadmin):
        _auth(api_client, superadmin)
        resp = api_client.post(BULK_ACTIVATE_URL, {'ids': [999999, 999998]}, format='json')
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_superadmin_activates_inactive_company(self, api_client, superadmin, company_inactive):
        _auth(api_client, superadmin)
        resp = api_client.post(BULK_ACTIVATE_URL, {'ids': [company_inactive.pk]}, format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['activated'] == 1
        company_inactive.refresh_from_db()
        assert company_inactive.is_active is True

    def test_superadmin_activates_multiple(self, api_client, superadmin, company_inactive, company_b):
        # Make company_b also inactive first
        company_b.is_active = False
        company_b.save(update_fields=['is_active'])

        _auth(api_client, superadmin)
        resp = api_client.post(
            BULK_ACTIVATE_URL,
            {'ids': [company_inactive.pk, company_b.pk]},
            format='json',
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['activated'] == 2

    def test_already_active_company_returns_zero_count(self, api_client, superadmin, company_a):
        """Activating an already-active company returns activated=0 (not an error)."""
        assert company_a.is_active is True
        _auth(api_client, superadmin)
        resp = api_client.post(BULK_ACTIVATE_URL, {'ids': [company_a.pk]}, format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['activated'] == 0


# ===========================================================================
# bulk-deactivate
# ===========================================================================

class TestBulkDeactivate:

    def test_unauthenticated_returns_401(self, api_client, company_a):
        resp = api_client.post(BULK_DEACTIVATE_URL, {'ids': [company_a.pk]}, format='json')
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_company_admin_returns_403(self, api_client, company_admin, company_a):
        _auth(api_client, company_admin)
        resp = api_client.post(BULK_DEACTIVATE_URL, {'ids': [company_a.pk]}, format='json')
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_employee_returns_403(self, api_client, employee, company_a):
        _auth(api_client, employee)
        resp = api_client.post(BULK_DEACTIVATE_URL, {'ids': [company_a.pk]}, format='json')
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_guest_returns_403(self, api_client, guest, company_a):
        _auth(api_client, guest)
        resp = api_client.post(BULK_DEACTIVATE_URL, {'ids': [company_a.pk]}, format='json')
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_empty_ids_returns_400(self, api_client, superadmin):
        _auth(api_client, superadmin)
        resp = api_client.post(BULK_DEACTIVATE_URL, {'ids': []}, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_missing_ids_returns_400(self, api_client, superadmin):
        _auth(api_client, superadmin)
        resp = api_client.post(BULK_DEACTIVATE_URL, {}, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_nonexistent_ids_returns_404(self, api_client, superadmin):
        _auth(api_client, superadmin)
        resp = api_client.post(BULK_DEACTIVATE_URL, {'ids': [999999, 999998]}, format='json')
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_superadmin_deactivates_active_company(self, api_client, superadmin, company_a):
        _auth(api_client, superadmin)
        resp = api_client.post(BULK_DEACTIVATE_URL, {'ids': [company_a.pk]}, format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['deactivated'] == 1
        company_a.refresh_from_db()
        assert company_a.is_active is False

    def test_superadmin_deactivates_multiple(self, api_client, superadmin, company_a, company_b):
        _auth(api_client, superadmin)
        resp = api_client.post(
            BULK_DEACTIVATE_URL,
            {'ids': [company_a.pk, company_b.pk]},
            format='json',
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['deactivated'] == 2
        company_a.refresh_from_db()
        company_b.refresh_from_db()
        assert company_a.is_active is False
        assert company_b.is_active is False

    def test_already_inactive_company_returns_zero_count(self, api_client, superadmin, company_inactive):
        """Deactivating an already-inactive company returns deactivated=0 (not an error)."""
        assert company_inactive.is_active is False
        _auth(api_client, superadmin)
        resp = api_client.post(BULK_DEACTIVATE_URL, {'ids': [company_inactive.pk]}, format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['deactivated'] == 0


# ===========================================================================
# bulk-delete
# ===========================================================================

class TestBulkDelete:

    def test_unauthenticated_returns_401(self, api_client, company_a):
        resp = api_client.delete(BULK_DELETE_URL, {'ids': [company_a.pk]}, format='json')
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_company_admin_returns_403(self, api_client, company_admin, company_a):
        _auth(api_client, company_admin)
        resp = api_client.delete(BULK_DELETE_URL, {'ids': [company_a.pk]}, format='json')
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_employee_returns_403(self, api_client, employee, company_a):
        _auth(api_client, employee)
        resp = api_client.delete(BULK_DELETE_URL, {'ids': [company_a.pk]}, format='json')
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_guest_returns_403(self, api_client, guest, company_a):
        _auth(api_client, guest)
        resp = api_client.delete(BULK_DELETE_URL, {'ids': [company_a.pk]}, format='json')
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_empty_ids_returns_400(self, api_client, superadmin):
        _auth(api_client, superadmin)
        resp = api_client.delete(BULK_DELETE_URL, {'ids': []}, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_missing_ids_returns_400(self, api_client, superadmin):
        _auth(api_client, superadmin)
        resp = api_client.delete(BULK_DELETE_URL, {}, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_nonexistent_ids_returns_404(self, api_client, superadmin):
        _auth(api_client, superadmin)
        resp = api_client.delete(BULK_DELETE_URL, {'ids': [999999, 999998]}, format='json')
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_superadmin_soft_deletes_company(self, api_client, superadmin, company_a):
        pk = company_a.pk
        _auth(api_client, superadmin)
        resp = api_client.delete(BULK_DELETE_URL, {'ids': [pk]}, format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['deleted'] == 1

        # Company should be soft-deleted (is_deleted=True, deleted_at set)
        from apps.companies.models import Company as CompanyModel
        company = CompanyModel.all_objects.get(pk=pk)
        assert company.is_deleted is True
        assert company.deleted_at is not None

        # Company should NOT appear in the default manager
        assert not CompanyModel.objects.filter(pk=pk).exists()

    def test_superadmin_soft_deletes_multiple(self, api_client, superadmin, company_a, company_b):
        _auth(api_client, superadmin)
        resp = api_client.delete(
            BULK_DELETE_URL,
            {'ids': [company_a.pk, company_b.pk]},
            format='json',
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['deleted'] == 2

        from apps.companies.models import Company as CompanyModel
        assert not CompanyModel.objects.filter(pk__in=[company_a.pk, company_b.pk]).exists()
        assert CompanyModel.all_objects.filter(
            pk__in=[company_a.pk, company_b.pk], is_deleted=True,
        ).count() == 2

    def test_bulk_delete_already_soft_deleted_returns_404(self, api_client, superadmin):
        """A company that is already soft-deleted is invisible to the default manager,
        so trying to delete it again returns 404."""
        company = Company.objects.create(name='Ghost Co', plan='basic')
        company.soft_delete()

        _auth(api_client, superadmin)
        resp = api_client.delete(BULK_DELETE_URL, {'ids': [company.pk]}, format='json')
        assert resp.status_code == status.HTTP_404_NOT_FOUND
