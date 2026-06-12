"""
Integration tests for brand theming feature on Company and CompanySettings.

Acceptance criteria:
  AC1  — GET detail response includes logo field (null when unset)
  AC2  — GET list response includes logo field (null when unset)
  AC3  — PATCH company with logo upload (multipart) → 200, logo URL returned
  AC4  — PATCH company settings with valid 6-digit brand_primary_color → 200
  AC5  — PATCH company settings with 3-digit hex (#RGB) → 400
  AC6  — PATCH company settings with invalid color → 400
  AC7  — PATCH company settings with null brand_primary_color → 200 (cleared)
  AC8  — PATCH company settings with blank brand_primary_color → 200 (cleared)
  AC9  — company_admin on premium plan can patch brand_primary_color on own company
  AC10 — employee cannot patch brand_primary_color on company settings
  AC11 — logo field uses use_url=True (absolute or relative URL, not a bare filename)
  AC12 — CompanyViewSet accepts multipart/form-data via parser_classes
  AC13 — company_admin on non-premium plan gets 403 when patching brand_primary_color
  AC14 — company_admin on non-premium plan gets 403 when uploading logo
  AC15 — company_admin on premium plan can upload logo
  AC16 — superadmin can always patch brand_primary_color regardless of plan
  AC17 — superadmin can always upload logo regardless of plan
"""
import io

import pytest
from PIL import Image
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company, CompanySettings
from apps.users.models import User


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_png_file(name='test_logo.png', size=(10, 10)):
    """Create an in-memory PNG file suitable for DRF multipart upload."""
    buf = io.BytesIO()
    img = Image.new('RGB', size, color=(255, 0, 0))
    img.save(buf, format='PNG')
    buf.seek(0)
    buf.name = name
    return buf


def auth(client, user):
    client.force_authenticate(user=user)
    return client


def detail_url(pk):
    return f'/api/v1/companies/{pk}/'


def settings_url(pk):
    return f'/api/v1/companies/{pk}/settings/'


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email='sa_brand@test.com', password='pass',
        first_name='Super', last_name='Admin', role='superadmin',
    )


@pytest.fixture
def company(db):
    """Basic plan company — cannot use branding features."""
    return Company.objects.create(name='Brand Corp', plan='basic')


@pytest.fixture
def premium_company(db):
    """Premium plan company — full branding access."""
    return Company.objects.create(name='Premium Corp', plan='premium')


@pytest.fixture
def company_admin(db, company):
    return User.objects.create_user(
        email='ca_brand@test.com', password='pass',
        first_name='Carol', last_name='Admin',
        role='company_admin', company=company,
    )


@pytest.fixture
def premium_company_admin(db, premium_company):
    return User.objects.create_user(
        email='ca_premium@test.com', password='pass',
        first_name='Pete', last_name='Premium',
        role='company_admin', company=premium_company,
    )


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='emp_brand@test.com', password='pass',
        first_name='Bob', last_name='Worker',
        role='employee', company=company,
    )


# ---------------------------------------------------------------------------
# AC1 / AC2 — logo field present in GET responses
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestLogoFieldPresent:

    def test_detail_response_includes_logo_field_null_when_unset(
        self, api_client, superadmin, company
    ):
        auth(api_client, superadmin)
        response = api_client.get(detail_url(company.id))
        assert response.status_code == status.HTTP_200_OK
        assert 'logo' in response.data
        assert response.data['logo'] is None

    def test_list_response_includes_logo_field_null_when_unset(
        self, api_client, superadmin, company
    ):
        auth(api_client, superadmin)
        response = api_client.get('/api/v1/companies/')
        assert response.status_code == status.HTTP_200_OK
        result = next(c for c in response.data['results'] if c['id'] == company.id)
        assert 'logo' in result
        assert result['logo'] is None


# ---------------------------------------------------------------------------
# AC3 — PATCH with logo upload returns URL (superadmin always passes)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestLogoUpload:

    def test_superadmin_can_upload_logo_via_multipart(
        self, api_client, superadmin, company, settings
    ):
        # Use local file storage (no S3) in tests
        settings.DEFAULT_FILE_STORAGE = 'django.core.files.storage.FileSystemStorage'
        settings.USE_S3 = False

        auth(api_client, superadmin)
        logo_file = make_png_file()
        response = api_client.patch(
            detail_url(company.id),
            {'logo': logo_file},
            format='multipart',
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data['logo'] is not None


# ---------------------------------------------------------------------------
# AC4 — valid 6-digit brand_primary_color accepted (premium company_admin)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBrandPrimaryColorValid:

    def test_valid_6digit_hex_lowercase_accepted(
        self, api_client, premium_company_admin, premium_company
    ):
        auth(api_client, premium_company_admin)
        response = api_client.patch(
            settings_url(premium_company.id),
            {'brand_primary_color': '#1a2b3c'},
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data['brand_primary_color'] == '#1a2b3c'

    def test_valid_6digit_hex_uppercase_accepted(
        self, api_client, premium_company_admin, premium_company
    ):
        auth(api_client, premium_company_admin)
        response = api_client.patch(
            settings_url(premium_company.id),
            {'brand_primary_color': '#FF5733'},
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data['brand_primary_color'] == '#FF5733'

    def test_valid_6digit_hex_mixed_case_accepted(
        self, api_client, premium_company_admin, premium_company
    ):
        auth(api_client, premium_company_admin)
        response = api_client.patch(
            settings_url(premium_company.id),
            {'brand_primary_color': '#aAbBcC'},
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data['brand_primary_color'] == '#aAbBcC'


# ---------------------------------------------------------------------------
# AC5 — 3-digit hex rejected
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBrandPrimaryColor3DigitRejected:

    def test_3digit_hex_returns_400(self, api_client, premium_company_admin, premium_company):
        auth(api_client, premium_company_admin)
        response = api_client.patch(
            settings_url(premium_company.id),
            {'brand_primary_color': '#F53'},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_3digit_hex_lowercase_returns_400(self, api_client, premium_company_admin, premium_company):
        auth(api_client, premium_company_admin)
        response = api_client.patch(
            settings_url(premium_company.id),
            {'brand_primary_color': '#abc'},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# AC6 — invalid color formats rejected
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBrandPrimaryColorInvalid:

    def test_plain_string_returns_400(self, api_client, premium_company_admin, premium_company):
        auth(api_client, premium_company_admin)
        response = api_client.patch(
            settings_url(premium_company.id),
            {'brand_primary_color': 'red'},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_hex_without_hash_returns_400(self, api_client, premium_company_admin, premium_company):
        auth(api_client, premium_company_admin)
        response = api_client.patch(
            settings_url(premium_company.id),
            {'brand_primary_color': 'FF5733'},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_7digit_hex_returns_400(self, api_client, premium_company_admin, premium_company):
        auth(api_client, premium_company_admin)
        response = api_client.patch(
            settings_url(premium_company.id),
            {'brand_primary_color': '#FF57331'},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_rgb_notation_returns_400(self, api_client, premium_company_admin, premium_company):
        auth(api_client, premium_company_admin)
        response = api_client.patch(
            settings_url(premium_company.id),
            {'brand_primary_color': 'rgb(255,0,0)'},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# AC7 / AC8 — null and blank brand_primary_color accepted (field cleared)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBrandPrimaryColorClear:

    def test_null_brand_primary_color_accepted(
        self, api_client, premium_company_admin, premium_company
    ):
        # Pre-set a color
        settings_obj, _ = CompanySettings.objects.get_or_create(company=premium_company)
        settings_obj.brand_primary_color = '#123456'
        settings_obj.save()

        auth(api_client, premium_company_admin)
        response = api_client.patch(
            settings_url(premium_company.id),
            {'brand_primary_color': None},
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data['brand_primary_color'] is None

    def test_blank_brand_primary_color_accepted(
        self, api_client, premium_company_admin, premium_company
    ):
        auth(api_client, premium_company_admin)
        response = api_client.patch(
            settings_url(premium_company.id),
            {'brand_primary_color': ''},
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data['brand_primary_color'] == ''


# ---------------------------------------------------------------------------
# AC9 — premium company_admin can patch brand_primary_color
# AC16 — superadmin can always patch brand_primary_color regardless of plan
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBrandPrimaryColorAccess:

    def test_premium_company_admin_can_patch_brand_color(
        self, api_client, premium_company_admin, premium_company
    ):
        auth(api_client, premium_company_admin)
        response = api_client.patch(
            settings_url(premium_company.id),
            {'brand_primary_color': '#ABCDEF'},
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data['brand_primary_color'] == '#ABCDEF'

        settings_obj = CompanySettings.objects.get(company=premium_company)
        assert settings_obj.brand_primary_color == '#ABCDEF'

    def test_superadmin_can_patch_brand_color_on_basic_company(
        self, api_client, superadmin, company
    ):
        """Superadmin bypasses the premium gate even on a basic plan company."""
        auth(api_client, superadmin)
        response = api_client.patch(
            settings_url(company.id),
            {'brand_primary_color': '#000000'},
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data['brand_primary_color'] == '#000000'

    def test_superadmin_can_patch_brand_color_on_premium_company(
        self, api_client, superadmin, premium_company
    ):
        auth(api_client, superadmin)
        response = api_client.patch(
            settings_url(premium_company.id),
            {'brand_primary_color': '#FFFFFF'},
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data['brand_primary_color'] == '#FFFFFF'


# ---------------------------------------------------------------------------
# AC10 — employee cannot patch brand_primary_color on company settings
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBrandPrimaryColorEmployeeForbidden:

    def test_employee_cannot_patch_brand_color(self, api_client, employee, company):
        auth(api_client, employee)
        response = api_client.patch(
            settings_url(company.id),
            {'brand_primary_color': '#FF0000'},
            format='json',
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN


# ---------------------------------------------------------------------------
# AC11 — logo URL is a string (URL), not a bare filename
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestLogoUrlFormat:

    def test_logo_is_none_when_not_set(self, api_client, superadmin, company):
        auth(api_client, superadmin)
        response = api_client.get(detail_url(company.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data['logo'] is None

    def test_logo_field_is_url_string_after_upload(
        self, api_client, superadmin, company, settings
    ):
        settings.DEFAULT_FILE_STORAGE = 'django.core.files.storage.FileSystemStorage'
        settings.USE_S3 = False

        auth(api_client, superadmin)
        logo_file = make_png_file('logo_url_test.png')
        response = api_client.patch(
            detail_url(company.id),
            {'logo': logo_file},
            format='multipart',
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        logo_value = response.data['logo']
        assert logo_value is not None
        # Must be a URL string (absolute http/https or root-relative /media/...), not a bare filename
        assert isinstance(logo_value, str)
        assert '/' in logo_value, f"Expected URL with slash, got: {logo_value!r}"


# ---------------------------------------------------------------------------
# AC12 — CompanyViewSet parser_classes includes MultiPartParser
# ---------------------------------------------------------------------------

def test_company_viewset_has_multipart_parser():
    """Unit test: verify parser_classes is set explicitly on CompanyViewSet."""
    from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
    from apps.companies.views import CompanyViewSet

    parser_classes = CompanyViewSet.parser_classes
    assert MultiPartParser in parser_classes, "MultiPartParser must be in CompanyViewSet.parser_classes"
    assert FormParser in parser_classes, "FormParser must be in CompanyViewSet.parser_classes"
    assert JSONParser in parser_classes, "JSONParser must be in CompanyViewSet.parser_classes"


# ---------------------------------------------------------------------------
# AC13 — non-premium company_admin gets 403 when patching brand_primary_color
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBrandPrimaryColorPremiumGating:

    def test_basic_plan_company_admin_gets_403_on_brand_color(
        self, api_client, company_admin, company
    ):
        """company_admin on basic plan must get 403 when setting brand_primary_color."""
        assert company.plan == 'basic'
        auth(api_client, company_admin)
        response = api_client.patch(
            settings_url(company.id),
            {'brand_primary_color': '#FF0000'},
            format='json',
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_standard_plan_company_admin_gets_403_on_brand_color(
        self, api_client, db
    ):
        """company_admin on standard plan also does not get branding access."""
        standard_company = Company.objects.create(name='Standard Co', plan='standard')
        standard_admin = User.objects.create_user(
            email='std_admin@test.com', password='pass',
            first_name='Stan', last_name='Admin',
            role='company_admin', company=standard_company,
        )
        client = APIClient()
        auth(client, standard_admin)
        response = client.patch(
            settings_url(standard_company.id),
            {'brand_primary_color': '#00FF00'},
            format='json',
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_settings_without_branding_field_still_works_on_basic_plan(
        self, api_client, company_admin, company
    ):
        """Patching non-branding settings fields must still work on basic plan."""
        assert company.plan == 'basic'
        auth(api_client, company_admin)
        response = api_client.patch(
            settings_url(company.id),
            {'vacation_days_per_year': 20},
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK


# ---------------------------------------------------------------------------
# AC14 — non-premium company_admin gets 403 when uploading logo
# AC15 — premium company_admin can upload logo
# AC17 — superadmin can always upload logo regardless of plan
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestLogoPremiumGating:

    def test_basic_plan_company_admin_gets_403_on_logo_upload(
        self, api_client, company_admin, company, settings
    ):
        """company_admin on basic plan must get 403 when uploading logo."""
        settings.DEFAULT_FILE_STORAGE = 'django.core.files.storage.FileSystemStorage'
        settings.USE_S3 = False
        assert company.plan == 'basic'
        auth(api_client, company_admin)
        logo_file = make_png_file('blocked_logo.png')
        response = api_client.patch(
            detail_url(company.id),
            {'logo': logo_file},
            format='multipart',
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_premium_company_admin_can_upload_logo(
        self, api_client, premium_company_admin, premium_company, settings
    ):
        """company_admin on premium plan can upload a logo."""
        settings.DEFAULT_FILE_STORAGE = 'django.core.files.storage.FileSystemStorage'
        settings.USE_S3 = False
        assert premium_company.plan == 'premium'
        auth(api_client, premium_company_admin)
        logo_file = make_png_file('premium_logo.png')
        response = api_client.patch(
            detail_url(premium_company.id),
            {'logo': logo_file},
            format='multipart',
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data['logo'] is not None

    def test_superadmin_can_upload_logo_on_basic_plan_company(
        self, api_client, superadmin, company, settings
    ):
        """Superadmin bypasses the premium gate and can upload logo on any company."""
        settings.DEFAULT_FILE_STORAGE = 'django.core.files.storage.FileSystemStorage'
        settings.USE_S3 = False
        assert company.plan == 'basic'
        auth(api_client, superadmin)
        logo_file = make_png_file('sa_logo.png')
        response = api_client.patch(
            detail_url(company.id),
            {'logo': logo_file},
            format='multipart',
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data['logo'] is not None
