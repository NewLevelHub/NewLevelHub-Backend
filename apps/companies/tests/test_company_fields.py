"""
Integration tests for the `categories` field and the computed `company_admin` field on Company.

Acceptance criteria:
  AC1 — PATCH with valid categories list → 200, field persists in DB and response
  AC2 — PATCH with invalid categories (not a list) → 400
  AC3 — PATCH with categories containing non-string items → 400
  AC4 — PATCH with more than 10 categories → 400
  AC5 — PATCH with a category exceeding 50 characters → 400
  AC6 — GET list includes categories and company_admin (computed)
  AC7 — GET detail includes categories and company_admin (computed)
  AC8 — company_admin can PATCH categories on own company
  AC9 — company_admin field contains the full object of the active company_admin user
  AC10 — company_admin is null when the company has no active company_admin
"""

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.services.models import Floor
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
        email='sa_fields@test.com',
        password='pass',
        first_name='Super',
        last_name='Admin',
        role='superadmin',
    )


@pytest.fixture
def company(db):
    return Company.objects.create(name='Fields Corp', plan='basic')


@pytest.fixture
def company_admin(db, company):
    return User.objects.create_user(
        email='ca_fields@test.com',
        password='pass',
        first_name='Carol',
        last_name='Admin',
        role='company_admin',
        company=company,
    )


def auth(client, user):
    client.force_authenticate(user=user)
    return client


def detail_url(pk):
    return f'/api/v1/companies/{pk}/'


# ---------------------------------------------------------------------------
# AC1 — PATCH valid categories
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCategoriesValid:

    def test_superadmin_patch_categories(self, api_client, superadmin, company):
        auth(api_client, superadmin)
        payload = {'categories': ['Tech', 'B2B', 'SaaS']}
        response = api_client.patch(detail_url(company.id), payload, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['categories'] == ['Tech', 'B2B', 'SaaS']
        company.refresh_from_db()
        assert company.categories == ['Tech', 'B2B', 'SaaS']

    def test_patch_categories_empty_list(self, api_client, superadmin, company):
        auth(api_client, superadmin)
        response = api_client.patch(detail_url(company.id), {'categories': []}, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['categories'] == []

    def test_patch_categories_exactly_10_items(self, api_client, superadmin, company):
        auth(api_client, superadmin)
        cats = [f'Cat{i}' for i in range(10)]
        response = api_client.patch(detail_url(company.id), {'categories': cats}, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['categories'] == cats

    def test_patch_categories_item_max_length(self, api_client, superadmin, company):
        auth(api_client, superadmin)
        long_cat = 'A' * 50
        response = api_client.patch(
            detail_url(company.id), {'categories': [long_cat]}, format='json'
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data['categories'] == [long_cat]


# ---------------------------------------------------------------------------
# AC2 — categories not a list → 400
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCategoriesInvalid:

    def test_categories_as_string_is_invalid(self, api_client, superadmin, company):
        auth(api_client, superadmin)
        response = api_client.patch(
            detail_url(company.id), {'categories': 'Tech'}, format='json'
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_categories_as_dict_is_invalid(self, api_client, superadmin, company):
        auth(api_client, superadmin)
        response = api_client.patch(
            detail_url(company.id), {'categories': {'key': 'value'}}, format='json'
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    # AC3 — items not strings
    def test_categories_with_integer_items(self, api_client, superadmin, company):
        auth(api_client, superadmin)
        response = api_client.patch(
            detail_url(company.id), {'categories': [1, 2, 3]}, format='json'
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_categories_with_empty_string_item(self, api_client, superadmin, company):
        auth(api_client, superadmin)
        response = api_client.patch(
            detail_url(company.id), {'categories': ['Tech', '']}, format='json'
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_categories_with_whitespace_only_item(self, api_client, superadmin, company):
        auth(api_client, superadmin)
        response = api_client.patch(
            detail_url(company.id), {'categories': ['Tech', '   ']}, format='json'
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    # AC4 — more than 10 categories
    def test_categories_more_than_10(self, api_client, superadmin, company):
        auth(api_client, superadmin)
        cats = [f'Cat{i}' for i in range(11)]
        response = api_client.patch(detail_url(company.id), {'categories': cats}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    # AC5 — item exceeds 50 chars
    def test_categories_item_exceeds_50_chars(self, api_client, superadmin, company):
        auth(api_client, superadmin)
        long_cat = 'A' * 51
        response = api_client.patch(
            detail_url(company.id), {'categories': [long_cat]}, format='json'
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# AC6 — GET list includes both fields (company_admin is computed)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestFieldsInListResponse:

    def test_list_includes_categories_and_company_admin(
        self, api_client, superadmin, company, company_admin
    ):
        company.categories = ['Tech']
        company.save()

        auth(api_client, superadmin)
        response = api_client.get('/api/v1/companies/')
        assert response.status_code == status.HTTP_200_OK
        result = next(c for c in response.data['results'] if c['id'] == company.id)
        assert 'categories' in result
        assert 'company_admin' in result
        assert result['categories'] == ['Tech']
        # company_admin fixture creates Carol Admin as company_admin for this company
        admin_data = result['company_admin']
        assert admin_data is not None
        assert admin_data['email'] == 'ca_fields@test.com'
        assert admin_data['full_name'] == 'Carol Admin'
        assert admin_data['first_name'] == 'Carol'
        assert admin_data['last_name'] == 'Admin'

    def test_list_company_admin_null_when_no_admin(self, api_client, superadmin, company):
        # company has no company_admin member
        auth(api_client, superadmin)
        response = api_client.get('/api/v1/companies/')
        assert response.status_code == status.HTTP_200_OK
        result = next(c for c in response.data['results'] if c['id'] == company.id)
        assert result['company_admin'] is None


# ---------------------------------------------------------------------------
# AC7 — GET detail includes both fields (company_admin is computed)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestFieldsInDetailResponse:

    def test_detail_company_admin_returns_admin_object(
        self, api_client, superadmin, company, company_admin
    ):
        auth(api_client, superadmin)
        response = api_client.get(detail_url(company.id))
        assert response.status_code == status.HTTP_200_OK
        admin_data = response.data['company_admin']
        assert admin_data is not None
        assert admin_data['email'] == 'ca_fields@test.com'
        assert admin_data['full_name'] == 'Carol Admin'
        assert admin_data['first_name'] == 'Carol'
        assert admin_data['last_name'] == 'Admin'
        assert admin_data['position'] == ''
        assert admin_data['avatar'] is None

    def test_detail_company_admin_null_when_no_admin(self, api_client, superadmin, company):
        auth(api_client, superadmin)
        response = api_client.get(detail_url(company.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data['company_admin'] is None

    def test_detail_includes_categories(self, api_client, superadmin, company):
        company.categories = ['B2B', 'SaaS']
        company.save()

        auth(api_client, superadmin)
        response = api_client.get(detail_url(company.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data['categories'] == ['B2B', 'SaaS']

    def test_detail_categories_default_is_empty_list(self, api_client, superadmin, company):
        auth(api_client, superadmin)
        response = api_client.get(detail_url(company.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data['categories'] == []

    def test_detail_company_admin_object_shape(
        self, api_client, superadmin, company, company_admin
    ):
        """company_admin object must have exactly the expected keys."""
        auth(api_client, superadmin)
        response = api_client.get(detail_url(company.id))
        assert response.status_code == status.HTTP_200_OK
        admin_data = response.data['company_admin']
        assert set(admin_data.keys()) == {'id', 'email', 'first_name', 'last_name', 'full_name', 'avatar', 'position'}
        assert admin_data['id'] == company_admin.id


# ---------------------------------------------------------------------------
# AC8 — company_admin can PATCH categories on their own company
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCompanyAdminCanPatchCategories:

    def test_company_admin_patch_categories(self, api_client, company_admin, company):
        auth(api_client, company_admin)
        payload = {'categories': ['Coworking', 'Startup']}
        response = api_client.patch(detail_url(company.id), payload, format='json')
        assert response.status_code == status.HTTP_200_OK
        company.refresh_from_db()
        assert company.categories == ['Coworking', 'Startup']

    def test_company_admin_invalid_categories_returns_400(
        self, api_client, company_admin, company
    ):
        auth(api_client, company_admin)
        response = api_client.patch(
            detail_url(company.id), {'categories': [42, 'valid']}, format='json'
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# AC9 — company_admin field is not writable (PATCH company_admin is silently ignored)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCompanyAdminIsReadOnly:

    def test_patch_company_admin_is_ignored(self, api_client, superadmin, company):
        """Sending company_admin in a PATCH payload must not raise an error (it is just ignored)."""
        auth(api_client, superadmin)
        response = api_client.patch(
            detail_url(company.id), {'company_admin': {'email': 'fake@test.com'}}, format='json'
        )
        assert response.status_code == status.HTTP_200_OK
        # No admin exists → computed field returns null
        assert response.data['company_admin'] is None

    def test_company_admin_reflects_actual_admin_not_submitted_value(
        self, api_client, superadmin, company, company_admin
    ):
        auth(api_client, superadmin)
        response = api_client.patch(
            detail_url(company.id), {'company_admin': {'email': 'fake@test.com'}}, format='json'
        )
        assert response.status_code == status.HTTP_200_OK
        admin_data = response.data['company_admin']
        assert admin_data is not None
        assert admin_data['email'] == 'ca_fields@test.com'
        assert admin_data['full_name'] == 'Carol Admin'


# ---------------------------------------------------------------------------
# AC10 — multipart/form-data: categories encoded as JSON string
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCategoriesMultipartEncoding:
    """
    When the request is multipart/form-data (e.g. logo upload + categories together),
    DRF's JSONField does NOT auto-decode string values from QueryDict.  The serializer
    must JSON-decode the string itself before validation so that the frontend can send:

        categories=["B2B","SaaS"]   (JSON-encoded, URL-encoded in the form body)

    A bare unquoted string like ``categories=B2B`` (not valid JSON array) must still
    return 400 with the ``company.categories_must_be_list`` error code.
    """

    def test_patch_categories_as_json_string_in_multipart(self, api_client, superadmin, company):
        """categories sent as JSON-encoded string via multipart → 200, list persisted."""
        auth(api_client, superadmin)
        # format='multipart' makes APIClient send as multipart/form-data.
        # The value is a JSON-encoded string, exactly as a browser/fetch FormData would send it.
        response = api_client.patch(
            detail_url(company.id),
            {'name': company.name, 'categories': '["B2B","SaaS"]'},
            format='multipart',
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data['categories'] == ['B2B', 'SaaS']
        company.refresh_from_db()
        assert company.categories == ['B2B', 'SaaS']

    def test_post_categories_as_json_string_in_multipart(self, api_client, superadmin):
        """categories sent as JSON-encoded string on POST via multipart → 201."""
        auth(api_client, superadmin)
        response = api_client.post(
            '/api/v1/companies/',
            {'name': 'Multipart Co', 'plan': 'basic', 'categories': '["Tech","SaaS"]'},
            format='multipart',
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert response.data['categories'] == ['Tech', 'SaaS']

    def test_patch_categories_empty_json_array_string_in_multipart(
        self, api_client, superadmin, company
    ):
        """categories=[] (empty JSON array string) via multipart → 200, stored as []."""
        auth(api_client, superadmin)
        response = api_client.patch(
            detail_url(company.id),
            {'name': company.name, 'categories': '[]'},
            format='multipart',
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data['categories'] == []

    def test_patch_categories_bare_string_in_multipart_returns_400(
        self, api_client, superadmin, company
    ):
        """A bare unquoted string (categories=B2B, not a JSON array) must return 400."""
        auth(api_client, superadmin)
        response = api_client.patch(
            detail_url(company.id),
            {'name': company.name, 'categories': 'B2B'},
            format='multipart',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# Floor FK tests
# AC-F1 — POST company with floor_id → 201, floor_id/floor_number/floor_name in response
# AC-F2 — PATCH company with floor_id → 200, FK set, read fields present
# AC-F3 — PATCH company with floor_id=null → 200, floor_fk cleared
# AC-F4 — PATCH company with invalid floor_id → 400
# AC-F5 — read response always includes floor_id/floor_number/floor_name (null when unset)
# AC-F6 — company_admin can PATCH floor_id on own company
# ---------------------------------------------------------------------------

@pytest.fixture
def floor(db, company):
    return Floor.objects.create(number=3, name='Third Floor', company=company)


@pytest.mark.django_db
class TestFloorFkField:

    def test_post_company_with_floor_id(self, api_client, superadmin, floor):
        auth(api_client, superadmin)
        response = api_client.post(
            '/api/v1/companies/',
            {'name': 'Floor Test Co', 'plan': 'basic', 'floor_id': floor.id},
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert response.data['floor_id'] == floor.id
        assert response.data['floor_number'] == 3
        assert response.data['floor_name'] == 'Third Floor'

    def test_patch_company_sets_floor_fk(self, api_client, superadmin, company, floor):
        auth(api_client, superadmin)
        response = api_client.patch(
            detail_url(company.id),
            {'floor_id': floor.id},
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data['floor_id'] == floor.id
        assert response.data['floor_number'] == 3
        assert response.data['floor_name'] == 'Third Floor'
        company.refresh_from_db()
        assert company.floor_fk_id == floor.id

    def test_patch_company_clears_floor_fk_with_null(self, api_client, superadmin, company, floor):
        company.floor_fk = floor
        company.save(update_fields=['floor_fk'])

        auth(api_client, superadmin)
        response = api_client.patch(
            detail_url(company.id),
            {'floor_id': None},
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data['floor_id'] is None
        assert response.data['floor_number'] is None
        assert response.data['floor_name'] is None
        company.refresh_from_db()
        assert company.floor_fk_id is None

    def test_patch_company_with_invalid_floor_id_returns_400(
        self, api_client, superadmin, company
    ):
        auth(api_client, superadmin)
        response = api_client.patch(
            detail_url(company.id),
            {'floor_id': 999999},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_detail_response_has_floor_fields_null_when_unset(
        self, api_client, superadmin, company
    ):
        auth(api_client, superadmin)
        response = api_client.get(detail_url(company.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data['floor_id'] is None
        assert response.data['floor_number'] is None
        assert response.data['floor_name'] is None

    def test_list_response_includes_floor_fields(
        self, api_client, superadmin, company, floor
    ):
        company.floor_fk = floor
        company.save(update_fields=['floor_fk'])

        auth(api_client, superadmin)
        response = api_client.get('/api/v1/companies/')
        assert response.status_code == status.HTTP_200_OK
        result = next(c for c in response.data['results'] if c['id'] == company.id)
        assert result['floor_id'] == floor.id
        assert result['floor_number'] == 3
        assert result['floor_name'] == 'Third Floor'

    def test_company_admin_can_patch_floor_id(
        self, api_client, company_admin, company, floor
    ):
        auth(api_client, company_admin)
        response = api_client.patch(
            detail_url(company.id),
            {'floor_id': floor.id},
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data['floor_id'] == floor.id
        company.refresh_from_db()
        assert company.floor_fk_id == floor.id
