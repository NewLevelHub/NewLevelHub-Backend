"""
AC tests for the participant picker endpoint.

GET /api/v1/bookings/members/?q=<search>

Access rules:
  - unauthenticated          → 401
  - guest                    → 403  (guests cannot SEARCH, but CAN appear in results)
  - company member no company → 403 (company_not_assigned)
  - company_admin / employee  → 200

Functional rules (platform-wide):
  - returns ALL active platform users excluding requester and superadmins
  - includes guests in results (guests can be invited as participants)
  - includes users from OTHER companies
  - ?q filters by first_name, last_name, email (case-insensitive)
  - inactive users excluded
  - max 20 results
"""
import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.users.models import User

MEMBERS_URL = '/api/v1/bookings/members/'


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='Picker Co', plan='basic')


@pytest.fixture
def other_company(db):
    return Company.objects.create(name='Other Co', plan='basic')


@pytest.fixture
def admin(company):
    return User.objects.create_user(
        email='admin@picker.test',
        password='pass',
        first_name='Admin',
        last_name='User',
        role='company_admin',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def employee(company):
    return User.objects.create_user(
        email='employee@picker.test',
        password='pass',
        first_name='Alice',
        last_name='Smith',
        role='employee',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def employee2(company):
    return User.objects.create_user(
        email='bob@picker.test',
        password='pass',
        first_name='Bob',
        last_name='Jones',
        role='employee',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def guest(db):
    return User.objects.create_user(
        email='guest@picker.test',
        password='pass',
        first_name='Guest',
        last_name='User',
        role='guest',
        is_email_verified=True,
    )


@pytest.fixture
def other_employee(other_company):
    return User.objects.create_user(
        email='other@picker.test',
        password='pass',
        first_name='Other',
        last_name='Person',
        role='employee',
        company=other_company,
        is_email_verified=True,
    )


@pytest.mark.django_db
class TestParticipantPickerAccess:

    def test_unauthenticated_returns_401(self, api_client):
        resp = api_client.get(MEMBERS_URL)
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_guest_returns_403(self, api_client, guest):
        api_client.force_authenticate(user=guest)
        resp = api_client.get(MEMBERS_URL)
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_employee_returns_200(self, api_client, employee, employee2):
        api_client.force_authenticate(user=employee)
        resp = api_client.get(MEMBERS_URL)
        assert resp.status_code == status.HTTP_200_OK

    def test_admin_returns_200(self, api_client, admin, employee):
        api_client.force_authenticate(user=admin)
        resp = api_client.get(MEMBERS_URL)
        assert resp.status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestParticipantPickerResults:

    def test_excludes_requester(self, api_client, employee, employee2):
        api_client.force_authenticate(user=employee)
        resp = api_client.get(MEMBERS_URL)
        assert resp.status_code == status.HTTP_200_OK
        ids = [u['id'] for u in resp.json()]
        assert employee.id not in ids

    def test_includes_same_company_members(self, api_client, employee, employee2, admin):
        api_client.force_authenticate(user=employee)
        resp = api_client.get(MEMBERS_URL)
        ids = [u['id'] for u in resp.json()]
        assert employee2.id in ids
        assert admin.id in ids

    def test_includes_other_company_members(self, api_client, employee, other_employee):
        """Platform-wide: users from other companies must appear in results."""
        api_client.force_authenticate(user=employee)
        resp = api_client.get(MEMBERS_URL)
        ids = [u['id'] for u in resp.json()]
        assert other_employee.id in ids

    def test_includes_guests_in_results(self, api_client, employee, guest):
        """Guests can be invited as meeting room participants."""
        api_client.force_authenticate(user=employee)
        resp = api_client.get(MEMBERS_URL)
        ids = [u['id'] for u in resp.json()]
        assert guest.id in ids

    def test_response_shape(self, api_client, employee, employee2):
        api_client.force_authenticate(user=employee)
        resp = api_client.get(MEMBERS_URL)
        assert resp.status_code == status.HTTP_200_OK
        item = resp.json()[0]
        for field in ('id', 'email', 'full_name', 'avatar', 'position'):
            assert field in item, f'Missing field: {field}'

    def test_search_by_first_name(self, api_client, employee, employee2):
        api_client.force_authenticate(user=employee)
        resp = api_client.get(MEMBERS_URL, {'q': 'bob'})
        ids = [u['id'] for u in resp.json()]
        assert employee2.id in ids
        assert employee.id not in ids

    def test_search_by_email(self, api_client, employee, employee2):
        api_client.force_authenticate(user=employee)
        resp = api_client.get(MEMBERS_URL, {'q': 'bob@picker'})
        ids = [u['id'] for u in resp.json()]
        assert employee2.id in ids

    def test_search_finds_other_company_user(self, api_client, employee, other_employee):
        """Search should return users from other companies by name."""
        api_client.force_authenticate(user=employee)
        resp = api_client.get(MEMBERS_URL, {'q': 'Other'})
        ids = [u['id'] for u in resp.json()]
        assert other_employee.id in ids

    def test_search_no_match_returns_empty(self, api_client, employee, employee2):
        api_client.force_authenticate(user=employee)
        resp = api_client.get(MEMBERS_URL, {'q': 'zzznomatch'})
        assert resp.json() == []

    def test_no_query_returns_all_platform_members(self, api_client, admin, employee, employee2, other_employee):
        api_client.force_authenticate(user=admin)
        resp = api_client.get(MEMBERS_URL)
        ids = {u['id'] for u in resp.json()}
        assert employee.id in ids
        assert employee2.id in ids
        assert other_employee.id in ids

    def test_inactive_user_excluded(self, api_client, employee, company):
        inactive = User.objects.create_user(
            email='inactive@picker.test',
            password='pass',
            first_name='Inactive',
            last_name='User',
            role='employee',
            company=company,
            is_active=False,
            is_email_verified=True,
        )
        api_client.force_authenticate(user=employee)
        resp = api_client.get(MEMBERS_URL)
        ids = [u['id'] for u in resp.json()]
        assert inactive.id not in ids
