"""
Integration tests for CompanyViewSet.

Acceptance criteria covered:
  AC1 — POST /api/v1/companies/  (superadmin only)
  AC2 — CompanySettings auto-created on company creation
  AC3 — GET list scoping: superadmin sees all, admin/employee see own, guest → 403
  AC4 — GET detail includes employee_count and storage_used
  AC5 — PATCH: superadmin all fields, company_admin restricted fields
  AC6 — Filters: plan, is_active, search by name
"""

import pytest
from decimal import Decimal
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company, CompanySettings
from apps.crm.models import Board
from apps.storage.models import File
from apps.users.models import User


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
    return Company.objects.create(name='Alpha Corp', plan='basic', max_employees=10)


@pytest.fixture
def company_b(db):
    return Company.objects.create(name='Beta Ltd', plan='premium', max_employees=50)


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


def auth(client, user):
    """Authenticate the APIClient as the given user."""
    client.force_authenticate(user=user)
    return client


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

COMPANIES_LIST_URL = '/api/v1/companies/'


def company_detail_url(pk):
    return f'/api/v1/companies/{pk}/'


# ---------------------------------------------------------------------------
# AC1 — POST: superadmin creates a company
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCompanyCreate:

    def test_superadmin_can_create_company(self, api_client, superadmin):
        auth(api_client, superadmin)
        payload = {
            'name': 'New Co',
            'plan': 'standard',
            'max_employees': 20,
            'storage_limit_gb': 10,
        }
        response = api_client.post(COMPANIES_LIST_URL, payload, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['name'] == 'New Co'

    def test_company_admin_cannot_create_company(self, api_client, company_admin):
        auth(api_client, company_admin)
        payload = {'name': 'Sneaky Co', 'plan': 'basic'}
        response = api_client.post(COMPANIES_LIST_URL, payload, format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_employee_cannot_create_company(self, api_client, employee):
        auth(api_client, employee)
        payload = {'name': 'Sneaky Co', 'plan': 'basic'}
        response = api_client.post(COMPANIES_LIST_URL, payload, format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_guest_cannot_create_company(self, api_client, guest):
        auth(api_client, guest)
        payload = {'name': 'Sneaky Co', 'plan': 'basic'}
        response = api_client.post(COMPANIES_LIST_URL, payload, format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_unauthenticated_cannot_create_company(self, api_client):
        payload = {'name': 'Anon Co', 'plan': 'basic'}
        response = api_client.post(COMPANIES_LIST_URL, payload, format='json')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_create_requires_name(self, api_client, superadmin):
        auth(api_client, superadmin)
        response = api_client.post(COMPANIES_LIST_URL, {'plan': 'basic'}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_uses_basic_defaults_when_limits_not_provided(self, api_client, superadmin):
        auth(api_client, superadmin)
        response = api_client.post(
            COMPANIES_LIST_URL,
            {'name': 'Basic Defaults Co', 'plan': 'basic'},
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['max_employees'] == 10
        assert response.data['max_boards'] == 1
        assert Decimal(str(response.data['storage_limit_gb'])) == Decimal('5')

    def test_create_uses_standard_defaults_when_limits_not_provided(self, api_client, superadmin):
        auth(api_client, superadmin)
        response = api_client.post(
            COMPANIES_LIST_URL,
            {'name': 'Standard Defaults Co', 'plan': 'standard'},
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['max_employees'] == 30
        assert response.data['max_boards'] == 5
        assert Decimal(str(response.data['storage_limit_gb'])) == Decimal('20')

    def test_create_uses_premium_defaults_when_limits_not_provided(self, api_client, superadmin):
        auth(api_client, superadmin)
        response = api_client.post(
            COMPANIES_LIST_URL,
            {'name': 'Premium Defaults Co', 'plan': 'premium'},
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['max_employees'] == 9999
        assert response.data['max_boards'] == 9999
        assert Decimal(str(response.data['storage_limit_gb'])) == Decimal('100')

    def test_create_keeps_explicit_limits(self, api_client, superadmin):
        auth(api_client, superadmin)
        response = api_client.post(
            COMPANIES_LIST_URL,
            {
                'name': 'Custom Limits Co',
                'plan': 'basic',
                'max_employees': 77,
                'max_boards': 9,
                'storage_limit_gb': 44,
            },
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['max_employees'] == 77
        assert response.data['max_boards'] == 9
        assert Decimal(str(response.data['storage_limit_gb'])) == Decimal('44')


# ---------------------------------------------------------------------------
# AC2 — CompanySettings auto-created via signal
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCompanySettingsAutoCreate:

    def test_settings_created_when_company_created_via_api(self, api_client, superadmin):
        auth(api_client, superadmin)
        response = api_client.post(
            COMPANIES_LIST_URL,
            {'name': 'Signal Co', 'plan': 'basic'},
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED
        company = Company.objects.get(id=response.data['id'])
        assert CompanySettings.objects.filter(company=company).exists()

    def test_settings_created_when_company_created_directly(self, db):
        company = Company.objects.create(name='Direct Co', plan='basic')
        assert CompanySettings.objects.filter(company=company).exists()

    def test_settings_not_duplicated_on_update(self, db, company_a):
        # Ensure signal guard (get_or_create) prevents duplicates on resave
        company_a.description = 'Updated'
        company_a.save()
        assert CompanySettings.objects.filter(company=company_a).count() == 1

    def test_model_create_applies_plan_defaults_for_non_basic_plan(self, db):
        company = Company.objects.create(name='ORM Premium Co', plan='premium')
        assert company.max_employees == 9999
        assert company.max_boards == 9999
        assert company.storage_limit_gb == 100


# ---------------------------------------------------------------------------
# AC3 — GET list scoping
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCompanyListScoping:

    def test_superadmin_sees_all_companies(self, api_client, superadmin, company_a, company_b):
        auth(api_client, superadmin)
        response = api_client.get(COMPANIES_LIST_URL)
        assert response.status_code == status.HTTP_200_OK
        ids = [c['id'] for c in response.data['results']]
        assert company_a.id in ids
        assert company_b.id in ids

    def test_company_admin_sees_only_own_company(self, api_client, company_admin, company_a, company_b):
        auth(api_client, company_admin)
        response = api_client.get(COMPANIES_LIST_URL)
        assert response.status_code == status.HTTP_200_OK
        ids = [c['id'] for c in response.data['results']]
        assert company_a.id in ids
        assert company_b.id not in ids

    def test_employee_sees_only_own_company(self, api_client, employee, company_a, company_b):
        auth(api_client, employee)
        response = api_client.get(COMPANIES_LIST_URL)
        assert response.status_code == status.HTTP_200_OK
        ids = [c['id'] for c in response.data['results']]
        assert company_a.id in ids
        assert company_b.id not in ids

    def test_guest_cannot_list_companies(self, api_client, guest):
        auth(api_client, guest)
        response = api_client.get(COMPANIES_LIST_URL)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_unauthenticated_cannot_list_companies(self, api_client):
        response = api_client.get(COMPANIES_LIST_URL)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_employee_without_company_returns_company_not_assigned(self, api_client):
        """
        In-memory user (never saved) avoids violating the DB CHECK constraint
        while still exercising the permission + exception handler contract.
        """
        user = User(
            email='no_company_yet@test.com',
            first_name='N',
            last_name='C',
            role='employee',
            company=None,
        )
        api_client.force_authenticate(user=user)
        response = api_client.get(COMPANIES_LIST_URL)
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data['success'] is False
        assert response.data['error']['details']['code'] == 'company_not_assigned'
        assert 'message' in response.data['error']['details']


# ---------------------------------------------------------------------------
# AC4 — GET detail: employee_count and storage_used present
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCompanyDetail:

    def test_superadmin_can_retrieve_any_company(self, api_client, superadmin, company_a):
        auth(api_client, superadmin)
        response = api_client.get(company_detail_url(company_a.id))
        assert response.status_code == status.HTTP_200_OK
        assert 'employee_count' in response.data
        assert 'storage_used' in response.data

    def test_company_admin_can_retrieve_own_company(self, api_client, company_admin, company_a):
        auth(api_client, company_admin)
        response = api_client.get(company_detail_url(company_a.id))
        assert response.status_code == status.HTTP_200_OK
        assert 'employee_count' in response.data
        assert 'storage_used' in response.data

    def test_company_admin_cannot_retrieve_other_company(
        self, api_client, company_admin, company_b
    ):
        auth(api_client, company_admin)
        response = api_client.get(company_detail_url(company_b.id))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_employee_count_reflects_active_members(
        self, api_client, superadmin, company_a, employee
    ):
        auth(api_client, superadmin)
        response = api_client.get(company_detail_url(company_a.id))
        assert response.status_code == status.HTTP_200_OK
        # At least the fixture employee should be counted
        assert response.data['employee_count'] >= 1

    def test_storage_used_is_zero_when_no_files(self, api_client, superadmin, company_a):
        auth(api_client, superadmin)
        response = api_client.get(company_detail_url(company_a.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data['storage_used'] == 0

    def test_guest_cannot_retrieve_company(self, api_client, guest, company_a):
        auth(api_client, guest)
        response = api_client.get(company_detail_url(company_a.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_unauthenticated_cannot_retrieve_company(self, api_client, company_a):
        response = api_client.get(company_detail_url(company_a.id))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


# ---------------------------------------------------------------------------
# AC5 — PATCH permission and field restriction
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCompanyUpdate:

    def test_superadmin_can_patch_any_field(self, api_client, superadmin, company_a):
        auth(api_client, superadmin)
        payload = {
            'name': 'Renamed',
            'plan': 'premium',
            'max_employees': 99,
            'max_boards': 13,
            'storage_limit_gb': 50,
        }
        response = api_client.patch(company_detail_url(company_a.id), payload, format='json')
        assert response.status_code == status.HTTP_200_OK
        company_a.refresh_from_db()
        assert company_a.name == 'Renamed'
        assert company_a.plan == 'premium'
        assert company_a.max_employees == 99
        assert company_a.max_boards == 13

    def test_company_admin_can_patch_allowed_fields(self, api_client, company_admin, company_a):
        auth(api_client, company_admin)
        payload = {'name': 'Admin Renamed', 'contact_email': 'new@alpha.com'}
        response = api_client.patch(company_detail_url(company_a.id), payload, format='json')
        assert response.status_code == status.HTTP_200_OK
        company_a.refresh_from_db()
        assert company_a.name == 'Admin Renamed'
        assert company_a.contact_email == 'new@alpha.com'

    def test_company_admin_cannot_change_plan(self, api_client, company_admin, company_a):
        """plan is not in CompanyAdminUpdateSerializer — it should be silently ignored."""
        auth(api_client, company_admin)
        original_plan = company_a.plan
        payload = {'plan': 'premium'}
        response = api_client.patch(company_detail_url(company_a.id), payload, format='json')
        # The request itself succeeds (200) but plan is unchanged
        assert response.status_code == status.HTTP_200_OK
        company_a.refresh_from_db()
        assert company_a.plan == original_plan

    def test_company_admin_cannot_patch_other_company(
        self, api_client, company_admin, company_b
    ):
        auth(api_client, company_admin)
        response = api_client.patch(
            company_detail_url(company_b.id), {'name': 'Hack'}, format='json'
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_employee_cannot_patch_company(self, api_client, employee, company_a):
        auth(api_client, employee)
        response = api_client.patch(
            company_detail_url(company_a.id), {'name': 'Hack'}, format='json'
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_guest_cannot_patch_company(self, api_client, guest, company_a):
        auth(api_client, guest)
        response = api_client.patch(
            company_detail_url(company_a.id), {'name': 'Hack'}, format='json'
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_unauthenticated_cannot_patch_company(self, api_client, company_a):
        response = api_client.patch(
            company_detail_url(company_a.id), {'name': 'Hack'}, format='json'
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_patch_updates_existing_company_without_creating_new_one(
        self, api_client, superadmin, company_a
    ):
        auth(api_client, superadmin)
        before_count = Company.objects.count()
        response = api_client.patch(
            company_detail_url(company_a.id),
            {'max_employees': 123, 'max_boards': 7, 'storage_limit_gb': 15},
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        after_count = Company.objects.count()
        company_a.refresh_from_db()

        assert before_count == after_count
        assert company_a.max_employees == 123
        assert company_a.max_boards == 7
        assert company_a.storage_limit_gb == 15


# ---------------------------------------------------------------------------
# AC5b — PATCH response must not include CompanySettings fields
# ---------------------------------------------------------------------------

SETTINGS_FIELDS = [
    'custom_task_categories',
    'custom_labels',
    'vacation_days_per_year',
    'onboarding_enabled',
    'brand_primary_color',
]


@pytest.mark.django_db
class TestCompanyPatchResponseShape:

    def test_superadmin_patch_response_has_no_settings_fields(
        self, api_client, superadmin, company_a
    ):
        auth(api_client, superadmin)
        response = api_client.patch(
            company_detail_url(company_a.id), {'name': 'SA Rename'}, format='json'
        )
        assert response.status_code == status.HTTP_200_OK
        for field in SETTINGS_FIELDS:
            assert field not in response.data, (
                f"CompanySettings field '{field}' must not appear in Company PATCH response"
            )

    def test_company_admin_patch_response_has_no_settings_fields(
        self, api_client, company_admin, company_a
    ):
        auth(api_client, company_admin)
        response = api_client.patch(
            company_detail_url(company_a.id), {'name': 'CA Rename'}, format='json'
        )
        assert response.status_code == status.HTTP_200_OK
        for field in SETTINGS_FIELDS:
            assert field not in response.data, (
                f"CompanySettings field '{field}' must not appear in Company PATCH response"
            )

    def test_superadmin_patch_response_includes_company_detail_fields(
        self, api_client, superadmin, company_a
    ):
        """After a successful PATCH the response should be the full CompanyDetailSerializer."""
        auth(api_client, superadmin)
        response = api_client.patch(
            company_detail_url(company_a.id), {'name': 'Detail Check'}, format='json'
        )
        assert response.status_code == status.HTTP_200_OK
        for field in ('employee_count', 'storage_used'):
            assert field in response.data, (
                f"Expected '{field}' in PATCH response but it was absent"
            )

    def test_superadmin_patch_updates_floor_and_office_number(
        self, api_client, superadmin, company_a
    ):
        """floor and office_number are superadmin-only writable fields."""
        auth(api_client, superadmin)
        payload = {'floor': '3', 'office_number': '301'}
        response = api_client.patch(company_detail_url(company_a.id), payload, format='json')
        assert response.status_code == status.HTTP_200_OK
        company_a.refresh_from_db()
        assert company_a.floor == '3'
        assert company_a.office_number == '301'

    def test_company_admin_cannot_update_floor_or_office_number(
        self, api_client, company_admin, company_a
    ):
        """floor/office_number are not in CompanyAdminUpdateSerializer — silently ignored."""
        auth(api_client, company_admin)
        original_floor = company_a.floor
        response = api_client.patch(
            company_detail_url(company_a.id), {'floor': '99'}, format='json'
        )
        assert response.status_code == status.HTTP_200_OK
        company_a.refresh_from_db()
        assert company_a.floor == original_floor


# ---------------------------------------------------------------------------
# AC6 — Filters: plan, is_active, search by name
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCompanyFilters:

    def test_filter_by_plan(self, api_client, superadmin, company_a, company_b):
        # company_a=basic, company_b=premium
        auth(api_client, superadmin)
        response = api_client.get(COMPANIES_LIST_URL, {'plan': 'premium'})
        assert response.status_code == status.HTTP_200_OK
        ids = [c['id'] for c in response.data['results']]
        assert company_b.id in ids
        assert company_a.id not in ids

    def test_filter_by_is_active_false(self, api_client, superadmin, company_a, company_b):
        company_a.is_active = False
        company_a.save()
        auth(api_client, superadmin)
        response = api_client.get(COMPANIES_LIST_URL, {'is_active': 'false'})
        assert response.status_code == status.HTTP_200_OK
        ids = [c['id'] for c in response.data['results']]
        assert company_a.id in ids
        assert company_b.id not in ids

    def test_search_by_name(self, api_client, superadmin, company_a, company_b):
        auth(api_client, superadmin)
        response = api_client.get(COMPANIES_LIST_URL, {'search': 'Alpha'})
        assert response.status_code == status.HTTP_200_OK
        ids = [c['id'] for c in response.data['results']]
        assert company_a.id in ids
        assert company_b.id not in ids

    def test_filter_name_case_insensitive(self, api_client, superadmin, company_a):
        auth(api_client, superadmin)
        response = api_client.get(COMPANIES_LIST_URL, {'name': 'alpha'})
        assert response.status_code == status.HTTP_200_OK
        ids = [c['id'] for c in response.data['results']]
        assert company_a.id in ids


@pytest.mark.django_db
class TestCompanyLimitsEndpoint:

    def test_superadmin_can_view_company_limits(self, api_client, superadmin, company_a, employee):
        auth(api_client, superadmin)
        owner = User.objects.create_user(
            email='owner@alpha.com',
            password='pass',
            first_name='Owner',
            last_name='One',
            role='employee',
            company=company_a,
        )
        Board.objects.create(company=company_a, name='Board 1', created_by=owner)
        File.objects.create(
            name='f1.txt',
            file='storage/2026/01/f1.txt',
            file_size=1024,
            owner=owner,
            company=company_a,
        )
        File.objects.create(
            name='f2.txt',
            file='storage/2026/01/f2.txt',
            file_size=1024,
            owner=owner,
            company=company_a,
        )

        response = api_client.get(f'/api/v1/companies/{company_a.id}/limits/')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['employees']['current'] >= 2
        assert response.data['employees']['max'] == company_a.max_employees
        assert response.data['boards']['current'] == 1
        assert response.data['boards']['max'] == company_a.max_boards
        # Two files of 1024 bytes each = 2048 bytes total.
        # used_bytes carries the exact count; used_gb may round to 0.0 for tiny files.
        assert response.data['storage']['used_bytes'] == 2048
        assert isinstance(response.data['storage']['used_gb'], float)
        assert response.data['storage']['limit_gb'] == company_a.storage_limit_gb

    def test_storage_used_bytes_includes_crm_direct_attachments(
        self, api_client, superadmin, company_a
    ):
        """Direct-upload CRM attachments (no storage_file link) count toward used_bytes."""
        from apps.crm.models import Board as CrmBoard, Column, Task, TaskAttachment
        auth(api_client, superadmin)
        owner = User.objects.create_user(
            email='crm_owner@alpha.com',
            password='pass',
            first_name='CRM',
            last_name='Owner',
            role='employee',
            company=company_a,
        )
        board = CrmBoard.objects.create(company=company_a, name='Test Board', created_by=owner)
        column = Column.objects.create(board=board, name='To Do', position=0)
        task = Task.objects.create(column=column, title='Task 1', created_by=owner)
        TaskAttachment.objects.create(
            task=task,
            file='task_attachments/1/sample.txt',
            filename='sample.txt',
            file_size=512000,
            mime_type='text/plain',
            uploaded_by=owner,
        )

        response = api_client.get(f'/api/v1/companies/{company_a.id}/limits/')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['storage']['used_bytes'] == 512000
        assert response.data['storage']['used_gb'] > 0.0

    def test_company_admin_can_view_own_company_limits(self, api_client, company_admin, company_a):
        auth(api_client, company_admin)
        response = api_client.get(f'/api/v1/companies/{company_a.id}/limits/')
        assert response.status_code == status.HTTP_200_OK
        assert set(response.data.keys()) == {'employees', 'boards', 'storage'}

    def test_company_admin_cannot_view_other_company_limits(
        self, api_client, company_admin, company_b
    ):
        auth(api_client, company_admin)
        response = api_client.get(f'/api/v1/companies/{company_b.id}/limits/')
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_guest_cannot_view_company_limits(self, api_client, guest, company_a):
        auth(api_client, guest)
        response = api_client.get(f'/api/v1/companies/{company_a.id}/limits/')
        assert response.status_code == status.HTTP_403_FORBIDDEN
