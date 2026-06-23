"""
Integration tests for DELETE /api/v1/auth/me/delete/ — soft-delete own account.

Coverage:
  - Unauthenticated request returns 401
  - Authenticated user (employee) gets 204
  - After deletion: is_deleted=True, is_active=False, deleted_at is set
  - After deletion: user can no longer login (LoginSerializer rejects inactive user)
  - Outstanding refresh tokens are blacklisted on deletion
  - Guest can also delete their own account (endpoint is IsAuthenticated, not IsCompanyMember)
  - Superadmin can also delete their own account
  - Second DELETE call on already-deleted account still returns 204 (idempotent DB write)
"""

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken


URL = '/api/v1/auth/me/delete/'
LOGIN_URL = '/api/v1/auth/login/'


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def company(db):
    from apps.companies.models import Company
    return Company.objects.create(name='Acme Corp')


@pytest.fixture
def employee(db, company):
    from apps.users.models import User
    return User.objects.create_user(
        email='employee@test.com',
        password='SecurePass1!',
        first_name='Alice',
        last_name='Smith',
        role='employee',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def guest_user(db):
    from apps.users.models import User
    return User.objects.create_user(
        email='guest@test.com',
        password='SecurePass1!',
        first_name='Bob',
        last_name='Guest',
        role='guest',
        is_email_verified=True,
    )


@pytest.fixture
def superadmin(db):
    from apps.users.models import User
    return User.objects.create_user(
        email='super@test.com',
        password='SecurePass1!',
        first_name='Super',
        last_name='Admin',
        role='superadmin',
        is_email_verified=True,
    )


# ── Auth gate ─────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_unauthenticated_returns_401(client):
    response = client.delete(URL)
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


# ── Happy path ────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_employee_delete_returns_204(client, employee):
    client.force_authenticate(user=employee)
    response = client.delete(URL)
    assert response.status_code == status.HTTP_204_NO_CONTENT


@pytest.mark.django_db
def test_delete_marks_is_deleted_and_is_active_false(client, employee):
    client.force_authenticate(user=employee)
    client.delete(URL)

    employee.refresh_from_db()
    assert employee.is_deleted is True
    assert employee.is_active is False


@pytest.mark.django_db
def test_delete_sets_deleted_at(client, employee):
    before = timezone.now()
    client.force_authenticate(user=employee)
    client.delete(URL)

    employee.refresh_from_db()
    assert employee.deleted_at is not None
    assert employee.deleted_at >= before


@pytest.mark.django_db
def test_guest_can_delete_own_account(client, guest_user):
    client.force_authenticate(user=guest_user)
    response = client.delete(URL)
    assert response.status_code == status.HTTP_204_NO_CONTENT
    guest_user.refresh_from_db()
    assert guest_user.is_deleted is True
    assert guest_user.is_active is False


@pytest.mark.django_db
def test_superadmin_can_delete_own_account(client, superadmin):
    client.force_authenticate(user=superadmin)
    response = client.delete(URL)
    assert response.status_code == status.HTTP_204_NO_CONTENT
    superadmin.refresh_from_db()
    assert superadmin.is_deleted is True
    assert superadmin.is_active is False


# ── Token blacklisting ────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_outstanding_refresh_tokens_blacklisted_on_delete(client, employee):
    # Issue a real refresh token so OutstandingToken is populated.
    refresh = RefreshToken.for_user(employee)
    jti = refresh['jti']

    client.force_authenticate(user=employee)
    client.delete(URL)

    # The token's outstanding entry should now have a corresponding BlacklistedToken row.
    outstanding = OutstandingToken.objects.get(jti=jti)
    assert BlacklistedToken.objects.filter(token=outstanding).exists()


# ── Login blocked after deletion ──────────────────────────────────────────────

@pytest.mark.django_db
def test_deleted_user_cannot_login(client, employee):
    client.force_authenticate(user=employee)
    client.delete(URL)

    # Attempt to log in with correct credentials
    response = client.post(LOGIN_URL, {
        'email': 'employee@test.com',
        'password': 'SecurePass1!',
    }, format='json')
    # is_active=False so django.contrib.auth.authenticate returns None → 400
    assert response.status_code in (
        status.HTTP_400_BAD_REQUEST,
        status.HTTP_403_FORBIDDEN,
    )


# ── Idempotency ───────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_second_delete_is_idempotent(client, employee):
    """Calling delete twice should not raise an error; the user stays deleted."""
    client.force_authenticate(user=employee)
    client.delete(URL)

    # Re-authenticate with force (the user is inactive but we can still force)
    client.force_authenticate(user=employee)
    response = client.delete(URL)

    # Still 204 — we just save the same flags again, no uniqueness issues
    assert response.status_code == status.HTTP_204_NO_CONTENT
    employee.refresh_from_db()
    assert employee.is_deleted is True
