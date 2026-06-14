"""Integration tests for bulk activate / bulk deactivate / bulk delete on ResourceViewSet."""

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.bookings.models import Resource
from apps.companies.models import Company
from apps.users.models import User


BULK_ACTIVATE_URL = '/api/v1/bookings/resources/bulk-activate/'
BULK_DEACTIVATE_URL = '/api/v1/bookings/resources/bulk-deactivate/'
BULK_DELETE_URL = '/api/v1/bookings/resources/bulk-delete/'


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='Tenant A', plan='basic')


@pytest.fixture
def other_company(db):
    return Company.objects.create(name='Tenant B', plan='basic')


@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email='super@bulk.test',
        password='pass',
        first_name='S',
        last_name='A',
        role='superadmin',
    )


@pytest.fixture
def company_admin(db, company):
    return User.objects.create_user(
        email='cadmin@bulk.test',
        password='pass',
        first_name='C',
        last_name='A',
        role='company_admin',
        company=company,
    )


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='emp@bulk.test',
        password='pass',
        first_name='E',
        last_name='M',
        role='employee',
        company=company,
    )


@pytest.fixture
def guest_user(db):
    return User.objects.create_user(
        email='guest@bulk.test',
        password='pass',
        first_name='G',
        last_name='U',
        role='guest',
    )


def make_resource(name='Desk', company=None, is_active=True):
    return Resource.objects.create(
        name=name,
        resource_type='desk',
        floor=1,
        is_active=is_active,
        assigned_company=company,
    )


# ---------------------------------------------------------------------------
# bulk_deactivate — permission tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBulkDeactivatePermissions:
    def test_unauthenticated_401(self, api_client):
        r = api_client.post(BULK_DEACTIVATE_URL, {'ids': [1]}, format='json')
        assert r.status_code == status.HTTP_401_UNAUTHORIZED

    def test_employee_403(self, api_client, employee):
        api_client.force_authenticate(user=employee)
        r = api_client.post(BULK_DEACTIVATE_URL, {'ids': [1]}, format='json')
        assert r.status_code == status.HTTP_403_FORBIDDEN

    def test_guest_403(self, api_client, guest_user):
        api_client.force_authenticate(user=guest_user)
        r = api_client.post(BULK_DEACTIVATE_URL, {'ids': [1]}, format='json')
        assert r.status_code == status.HTTP_403_FORBIDDEN

    def test_company_admin_allowed(self, api_client, company_admin, company):
        r1 = make_resource('DA1', company=company)
        api_client.force_authenticate(user=company_admin)
        r = api_client.post(BULK_DEACTIVATE_URL, {'ids': [r1.id]}, format='json')
        assert r.status_code == status.HTTP_200_OK

    def test_superadmin_allowed(self, api_client, superadmin):
        r1 = make_resource('DA2')
        api_client.force_authenticate(user=superadmin)
        r = api_client.post(BULK_DEACTIVATE_URL, {'ids': [r1.id]}, format='json')
        assert r.status_code == status.HTTP_200_OK


# ---------------------------------------------------------------------------
# bulk_deactivate — validation
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBulkDeactivateValidation:
    def test_empty_ids_400(self, api_client, company_admin):
        api_client.force_authenticate(user=company_admin)
        r = api_client.post(BULK_DEACTIVATE_URL, {'ids': []}, format='json')
        assert r.status_code == status.HTTP_400_BAD_REQUEST

    def test_missing_ids_400(self, api_client, company_admin):
        api_client.force_authenticate(user=company_admin)
        r = api_client.post(BULK_DEACTIVATE_URL, {}, format='json')
        assert r.status_code == status.HTTP_400_BAD_REQUEST

    def test_nonexistent_ids_404(self, api_client, company_admin):
        api_client.force_authenticate(user=company_admin)
        r = api_client.post(BULK_DEACTIVATE_URL, {'ids': [999999]}, format='json')
        assert r.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# bulk_deactivate — business logic
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBulkDeactivateLogic:
    def test_superadmin_deactivates_any_resource(self, api_client, superadmin):
        r1 = make_resource('SA1')
        r2 = make_resource('SA2')
        api_client.force_authenticate(user=superadmin)
        resp = api_client.post(
            BULK_DEACTIVATE_URL, {'ids': [r1.id, r2.id]}, format='json'
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()['deactivated'] == 2
        r1.refresh_from_db()
        r2.refresh_from_db()
        assert r1.is_active is False
        assert r2.is_active is False

    def test_company_admin_deactivates_own_company_resource(
        self, api_client, company_admin, company
    ):
        r1 = make_resource('CA_Own', company=company)
        api_client.force_authenticate(user=company_admin)
        resp = api_client.post(BULK_DEACTIVATE_URL, {'ids': [r1.id]}, format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()['deactivated'] == 1
        r1.refresh_from_db()
        assert r1.is_active is False

    def test_company_admin_cannot_deactivate_other_company_resource(
        self, api_client, company_admin, other_company
    ):
        r_other = make_resource('Other', company=other_company)
        api_client.force_authenticate(user=company_admin)
        resp = api_client.post(BULK_DEACTIVATE_URL, {'ids': [r_other.id]}, format='json')
        # Scoped queryset excludes this resource → 404
        assert resp.status_code == status.HTTP_404_NOT_FOUND
        r_other.refresh_from_db()
        assert r_other.is_active is True  # unchanged

    def test_company_admin_cannot_deactivate_unassigned_resource(
        self, api_client, company_admin
    ):
        r_shared = make_resource('Shared', company=None)
        api_client.force_authenticate(user=company_admin)
        resp = api_client.post(BULK_DEACTIVATE_URL, {'ids': [r_shared.id]}, format='json')
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_already_inactive_counted_zero(self, api_client, superadmin):
        r1 = make_resource('AlreadyOff', is_active=False)
        api_client.force_authenticate(user=superadmin)
        resp = api_client.post(BULK_DEACTIVATE_URL, {'ids': [r1.id]}, format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()['deactivated'] == 0

    def test_partial_match_only_active_updated(self, api_client, superadmin):
        r_active = make_resource('Active', is_active=True)
        r_inactive = make_resource('Inactive', is_active=False)
        api_client.force_authenticate(user=superadmin)
        resp = api_client.post(
            BULK_DEACTIVATE_URL,
            {'ids': [r_active.id, r_inactive.id]},
            format='json',
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()['deactivated'] == 1
        r_active.refresh_from_db()
        assert r_active.is_active is False


# ---------------------------------------------------------------------------
# bulk_delete — permission tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBulkDeletePermissions:
    def test_unauthenticated_401(self, api_client):
        r = api_client.delete(BULK_DELETE_URL, {'ids': [1]}, format='json')
        assert r.status_code == status.HTTP_401_UNAUTHORIZED

    def test_employee_403(self, api_client, employee):
        api_client.force_authenticate(user=employee)
        r = api_client.delete(BULK_DELETE_URL, {'ids': [1]}, format='json')
        assert r.status_code == status.HTTP_403_FORBIDDEN

    def test_guest_403(self, api_client, guest_user):
        api_client.force_authenticate(user=guest_user)
        r = api_client.delete(BULK_DELETE_URL, {'ids': [1]}, format='json')
        assert r.status_code == status.HTTP_403_FORBIDDEN

    def test_company_admin_allowed(self, api_client, company_admin, company):
        r1 = make_resource('DelCA', company=company)
        api_client.force_authenticate(user=company_admin)
        r = api_client.delete(BULK_DELETE_URL, {'ids': [r1.id]}, format='json')
        assert r.status_code == status.HTTP_200_OK

    def test_superadmin_allowed(self, api_client, superadmin):
        r1 = make_resource('DelSA')
        api_client.force_authenticate(user=superadmin)
        r = api_client.delete(BULK_DELETE_URL, {'ids': [r1.id]}, format='json')
        assert r.status_code == status.HTTP_200_OK


# ---------------------------------------------------------------------------
# bulk_delete — validation
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBulkDeleteValidation:
    def test_empty_ids_400(self, api_client, company_admin):
        api_client.force_authenticate(user=company_admin)
        r = api_client.delete(BULK_DELETE_URL, {'ids': []}, format='json')
        assert r.status_code == status.HTTP_400_BAD_REQUEST

    def test_missing_ids_400(self, api_client, company_admin):
        api_client.force_authenticate(user=company_admin)
        r = api_client.delete(BULK_DELETE_URL, {}, format='json')
        assert r.status_code == status.HTTP_400_BAD_REQUEST

    def test_nonexistent_ids_404(self, api_client, company_admin):
        api_client.force_authenticate(user=company_admin)
        r = api_client.delete(BULK_DELETE_URL, {'ids': [999999]}, format='json')
        assert r.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# bulk_delete — business logic
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBulkDeleteLogic:
    def test_superadmin_deletes_any_resources(self, api_client, superadmin):
        r1 = make_resource('Del1')
        r2 = make_resource('Del2')
        ids = [r1.id, r2.id]
        api_client.force_authenticate(user=superadmin)
        resp = api_client.delete(BULK_DELETE_URL, {'ids': ids}, format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()['deleted'] == 2
        assert not Resource.objects.filter(pk__in=ids).exists()

    def test_company_admin_deletes_own_company_resource(
        self, api_client, company_admin, company
    ):
        r1 = make_resource('OwnDel', company=company)
        api_client.force_authenticate(user=company_admin)
        resp = api_client.delete(BULK_DELETE_URL, {'ids': [r1.id]}, format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()['deleted'] == 1
        assert not Resource.objects.filter(pk=r1.id).exists()

    def test_company_admin_cannot_delete_other_company_resource(
        self, api_client, company_admin, other_company
    ):
        r_other = make_resource('OtherDel', company=other_company)
        api_client.force_authenticate(user=company_admin)
        resp = api_client.delete(BULK_DELETE_URL, {'ids': [r_other.id]}, format='json')
        assert resp.status_code == status.HTTP_404_NOT_FOUND
        assert Resource.objects.filter(pk=r_other.id).exists()  # not deleted

    def test_company_admin_cannot_delete_unassigned_resource(
        self, api_client, company_admin
    ):
        r_shared = make_resource('SharedDel', company=None)
        api_client.force_authenticate(user=company_admin)
        resp = api_client.delete(BULK_DELETE_URL, {'ids': [r_shared.id]}, format='json')
        assert resp.status_code == status.HTTP_404_NOT_FOUND
        assert Resource.objects.filter(pk=r_shared.id).exists()

    def test_partial_ids_deletes_only_scoped(self, api_client, superadmin):
        r_keep = make_resource('Keep')
        r_del = make_resource('Delete')
        # Only delete one
        api_client.force_authenticate(user=superadmin)
        resp = api_client.delete(
            BULK_DELETE_URL, {'ids': [r_del.id]}, format='json'
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()['deleted'] == 1
        assert Resource.objects.filter(pk=r_keep.id).exists()
        assert not Resource.objects.filter(pk=r_del.id).exists()


# ---------------------------------------------------------------------------
# bulk_activate — permission tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBulkActivatePermissions:
    def test_unauthenticated_401(self, api_client):
        r = api_client.post(BULK_ACTIVATE_URL, {'ids': [1]}, format='json')
        assert r.status_code == status.HTTP_401_UNAUTHORIZED

    def test_employee_403(self, api_client, employee):
        api_client.force_authenticate(user=employee)
        r = api_client.post(BULK_ACTIVATE_URL, {'ids': [1]}, format='json')
        assert r.status_code == status.HTTP_403_FORBIDDEN

    def test_guest_403(self, api_client, guest_user):
        api_client.force_authenticate(user=guest_user)
        r = api_client.post(BULK_ACTIVATE_URL, {'ids': [1]}, format='json')
        assert r.status_code == status.HTTP_403_FORBIDDEN

    def test_company_admin_allowed(self, api_client, company_admin, company):
        r1 = make_resource('ActCA', company=company, is_active=False)
        api_client.force_authenticate(user=company_admin)
        r = api_client.post(BULK_ACTIVATE_URL, {'ids': [r1.id]}, format='json')
        assert r.status_code == status.HTTP_200_OK

    def test_superadmin_allowed(self, api_client, superadmin):
        r1 = make_resource('ActSA', is_active=False)
        api_client.force_authenticate(user=superadmin)
        r = api_client.post(BULK_ACTIVATE_URL, {'ids': [r1.id]}, format='json')
        assert r.status_code == status.HTTP_200_OK


# ---------------------------------------------------------------------------
# bulk_activate — validation
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBulkActivateValidation:
    def test_empty_ids_400(self, api_client, company_admin):
        api_client.force_authenticate(user=company_admin)
        r = api_client.post(BULK_ACTIVATE_URL, {'ids': []}, format='json')
        assert r.status_code == status.HTTP_400_BAD_REQUEST

    def test_missing_ids_400(self, api_client, company_admin):
        api_client.force_authenticate(user=company_admin)
        r = api_client.post(BULK_ACTIVATE_URL, {}, format='json')
        assert r.status_code == status.HTTP_400_BAD_REQUEST

    def test_nonexistent_ids_404(self, api_client, company_admin):
        api_client.force_authenticate(user=company_admin)
        r = api_client.post(BULK_ACTIVATE_URL, {'ids': [999999]}, format='json')
        assert r.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# bulk_activate — business logic
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBulkActivateLogic:
    def test_superadmin_activates_any_resource(self, api_client, superadmin):
        r1 = make_resource('ActSA1', is_active=False)
        r2 = make_resource('ActSA2', is_active=False)
        api_client.force_authenticate(user=superadmin)
        resp = api_client.post(
            BULK_ACTIVATE_URL, {'ids': [r1.id, r2.id]}, format='json'
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()['activated'] == 2
        r1.refresh_from_db()
        r2.refresh_from_db()
        assert r1.is_active is True
        assert r2.is_active is True

    def test_company_admin_activates_own_company_resource(
        self, api_client, company_admin, company
    ):
        r1 = make_resource('CA_OwnAct', company=company, is_active=False)
        api_client.force_authenticate(user=company_admin)
        resp = api_client.post(BULK_ACTIVATE_URL, {'ids': [r1.id]}, format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()['activated'] == 1
        r1.refresh_from_db()
        assert r1.is_active is True

    def test_cross_company_isolation_returns_404(
        self, api_client, company_admin, other_company
    ):
        r_other = make_resource('OtherAct', company=other_company, is_active=False)
        api_client.force_authenticate(user=company_admin)
        resp = api_client.post(BULK_ACTIVATE_URL, {'ids': [r_other.id]}, format='json')
        # Scoped queryset excludes this resource → 404
        assert resp.status_code == status.HTTP_404_NOT_FOUND
        r_other.refresh_from_db()
        assert r_other.is_active is False  # unchanged

    def test_company_admin_cannot_activate_unassigned_resource(
        self, api_client, company_admin
    ):
        r_shared = make_resource('SharedAct', company=None, is_active=False)
        api_client.force_authenticate(user=company_admin)
        resp = api_client.post(BULK_ACTIVATE_URL, {'ids': [r_shared.id]}, format='json')
        assert resp.status_code == status.HTTP_404_NOT_FOUND
        r_shared.refresh_from_db()
        assert r_shared.is_active is False  # unchanged

    def test_already_active_counted_zero(self, api_client, superadmin):
        r1 = make_resource('AlreadyOn', is_active=True)
        api_client.force_authenticate(user=superadmin)
        resp = api_client.post(BULK_ACTIVATE_URL, {'ids': [r1.id]}, format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()['activated'] == 0

    def test_partial_match_only_inactive_updated(self, api_client, superadmin):
        r_inactive = make_resource('Inactive2', is_active=False)
        r_active = make_resource('Active2', is_active=True)
        api_client.force_authenticate(user=superadmin)
        resp = api_client.post(
            BULK_ACTIVATE_URL,
            {'ids': [r_inactive.id, r_active.id]},
            format='json',
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()['activated'] == 1
        r_inactive.refresh_from_db()
        assert r_inactive.is_active is True
