"""
Tests for the superadmin impersonation endpoint:
  POST /api/v1/auth/users/<id>/impersonate/
"""
import pytest
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.users.models import User


def impersonate_url(pk):
    return f'/api/v1/auth/users/{pk}/impersonate/'


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    from apps.companies.models import Company
    return Company.objects.create(name='Acme Corp')


@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email='superadmin@example.com',
        password='Pass123!',
        first_name='Super',
        last_name='Admin',
        role='superadmin',
    )


@pytest.fixture
def other_superadmin(db):
    return User.objects.create_user(
        email='superadmin2@example.com',
        password='Pass123!',
        first_name='Super2',
        last_name='Admin2',
        role='superadmin',
    )


@pytest.fixture
def company_admin(db, company):
    return User.objects.create_user(
        email='cadmin@example.com',
        password='Pass123!',
        first_name='Company',
        last_name='Admin',
        role='company_admin',
        company=company,
    )


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='employee@example.com',
        password='Pass123!',
        first_name='Emp',
        last_name='Loyee',
        role='employee',
        company=company,
    )


@pytest.fixture
def guest_user(db):
    return User.objects.create_user(
        email='guest@example.com',
        password='Pass123!',
        first_name='Guest',
        last_name='User',
        role='guest',
    )


@pytest.fixture
def inactive_user(db, company):
    return User.objects.create_user(
        email='inactive@example.com',
        password='Pass123!',
        first_name='In',
        last_name='Active',
        role='employee',
        company=company,
        is_active=False,
    )


@pytest.fixture
def auth_client(api_client, superadmin):
    api_client.force_authenticate(user=superadmin)
    return api_client


# ── Auth ──────────────────────────────────────────────────────────────


class TestImpersonateAuth:
    def test_unauthenticated_returns_401(self, api_client, employee):
        response = api_client.post(impersonate_url(employee.id))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_company_admin_returns_403(self, api_client, company_admin, employee):
        api_client.force_authenticate(user=company_admin)
        response = api_client.post(impersonate_url(employee.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_employee_returns_403(self, api_client, employee, company_admin):
        api_client.force_authenticate(user=employee)
        response = api_client.post(impersonate_url(company_admin.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_guest_returns_403(self, api_client, guest_user, employee):
        api_client.force_authenticate(user=guest_user)
        response = api_client.post(impersonate_url(employee.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN


# ── Validation errors ─────────────────────────────────────────────────


class TestImpersonateValidation:
    def test_nonexistent_user_returns_404(self, auth_client):
        response = auth_client.post(impersonate_url(999999))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_impersonate_self_returns_400(self, auth_client, superadmin):
        response = auth_client.post(impersonate_url(superadmin.id))
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'yourself' in str(response.data).lower()

    def test_impersonate_other_superadmin_returns_400(self, auth_client, other_superadmin):
        response = auth_client.post(impersonate_url(other_superadmin.id))
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'superadmin' in str(response.data).lower()

    def test_impersonate_inactive_user_returns_400(self, auth_client, inactive_user):
        response = auth_client.post(impersonate_url(inactive_user.id))
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'inactive' in str(response.data).lower()


# ── Success ───────────────────────────────────────────────────────────


class TestImpersonateSuccess:
    def test_returns_tokens_and_user(self, auth_client, employee):
        response = auth_client.post(impersonate_url(employee.id))
        assert response.status_code == status.HTTP_200_OK
        assert 'access' in response.data
        assert 'refresh' in response.data
        assert 'user' in response.data
        assert response.data['user']['id'] == employee.id
        assert response.data['user']['email'] == employee.email

    def test_access_token_has_impersonated_by_claim(self, auth_client, employee, superadmin):
        response = auth_client.post(impersonate_url(employee.id))
        assert response.status_code == status.HTTP_200_OK
        access = AccessToken(response.data['access'])
        assert access['impersonated_by'] == superadmin.id

    def test_access_token_user_id_is_target(self, auth_client, employee, superadmin):
        response = auth_client.post(impersonate_url(employee.id))
        assert response.status_code == status.HTTP_200_OK
        access = AccessToken(response.data['access'])
        assert access['user_id'] == employee.id
        assert access['user_id'] != superadmin.id

    def test_can_impersonate_company_admin(self, auth_client, company_admin, superadmin):
        response = auth_client.post(impersonate_url(company_admin.id))
        assert response.status_code == status.HTTP_200_OK
        access = AccessToken(response.data['access'])
        assert access['user_id'] == company_admin.id
        assert access['impersonated_by'] == superadmin.id

    def test_can_impersonate_guest(self, auth_client, guest_user, superadmin):
        response = auth_client.post(impersonate_url(guest_user.id))
        assert response.status_code == status.HTTP_200_OK
        access = AccessToken(response.data['access'])
        assert access['user_id'] == guest_user.id
        assert access['impersonated_by'] == superadmin.id
