"""
Integration tests for company settings endpoints.

GET  /api/v1/companies/<id>/settings/  — retrieve company settings
PATCH /api/v1/companies/<id>/settings/ — partial update company settings

Acceptance criteria covered:
  AC1  — GET: employee 200, company_admin 200, superadmin 200, guest 403, unauthenticated 401
  AC2  — PATCH: company_admin own company 200, company_admin other company 403, employee 403,
          superadmin any company 200
  AC3  — Validation: invalid hex color → 400
  AC4  — Validation: custom_labels > 30 items → 400
  AC5  — Validation: custom_task_categories > 50 items → 400
  AC6  — Validation: vacation_days_per_year < 0 → 400
  AC7  — Validation: working_hours start >= end → 400
  AC8  — working_hours start < end → 200
  AC9  — PATCH with only changed fields (partial update works)
  AC10 — CompanySettings created if not exists (get_or_create)
"""

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company, CompanySettings
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
    return Company.objects.create(name='Alpha Corp', plan='basic')


@pytest.fixture
def company_b(db):
    return Company.objects.create(name='Beta Ltd', plan='basic')


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
    client.force_authenticate(user=user)
    return client


def settings_url(company_id):
    return f'/api/v1/companies/{company_id}/settings/'


# ---------------------------------------------------------------------------
# AC1 — GET access control
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCompanySettingsGet:

    def test_employee_can_retrieve_own_company_settings(
        self, api_client, employee, company_a
    ):
        auth(api_client, employee)
        response = api_client.get(settings_url(company_a.id))
        assert response.status_code == status.HTTP_200_OK
        assert 'vacation_days_per_year' in response.data

    def test_company_admin_can_retrieve_own_company_settings(
        self, api_client, company_admin, company_a
    ):
        auth(api_client, company_admin)
        response = api_client.get(settings_url(company_a.id))
        assert response.status_code == status.HTTP_200_OK
        assert 'onboarding_enabled' in response.data

    def test_superadmin_can_retrieve_any_company_settings(
        self, api_client, superadmin, company_b
    ):
        auth(api_client, superadmin)
        response = api_client.get(settings_url(company_b.id))
        assert response.status_code == status.HTTP_200_OK

    def test_guest_cannot_retrieve_company_settings(
        self, api_client, guest, company_a
    ):
        auth(api_client, guest)
        response = api_client.get(settings_url(company_a.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_unauthenticated_cannot_retrieve_company_settings(
        self, api_client, company_a
    ):
        response = api_client.get(settings_url(company_a.id))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_response_contains_all_expected_fields(
        self, api_client, employee, company_a
    ):
        auth(api_client, employee)
        response = api_client.get(settings_url(company_a.id))
        assert response.status_code == status.HTTP_200_OK
        expected_fields = {
            'custom_task_categories', 'custom_labels',
            'vacation_days_per_year', 'onboarding_enabled',
            'brand_primary_color', 'working_hours',
        }
        assert expected_fields.issubset(set(response.data.keys()))

    def test_working_hours_is_object_with_default_values_when_not_overridden(
        self, api_client, employee, company_a
    ):
        # working_hours are now stored on Company (not CompanySettings).
        # Company always has non-null defaults (09:00 / 18:00), so the settings
        # endpoint must proxy these values — never return null.
        auth(api_client, employee)
        response = api_client.get(settings_url(company_a.id))
        assert response.status_code == status.HTTP_200_OK
        wh = response.data['working_hours']
        assert isinstance(wh, dict)
        assert 'start' in wh and 'end' in wh
        # Company defaults are 09:00 / 18:00 unless overridden
        assert wh['start'] == '09:00'
        assert wh['end'] == '18:00'

    def test_settings_auto_created_if_missing_on_get(
        self, api_client, superadmin, company_b
    ):
        # Delete any auto-created settings to test get_or_create path
        CompanySettings.objects.filter(company=company_b).delete()
        auth(api_client, superadmin)
        response = api_client.get(settings_url(company_b.id))
        assert response.status_code == status.HTTP_200_OK
        assert CompanySettings.objects.filter(company=company_b).exists()


# ---------------------------------------------------------------------------
# AC2 — PATCH access control
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCompanySettingsPatchAccess:

    def test_company_admin_can_patch_own_company_settings(
        self, api_client, company_admin, company_a
    ):
        auth(api_client, company_admin)
        payload = {'vacation_days_per_year': 30}
        response = api_client.patch(settings_url(company_a.id), payload, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['vacation_days_per_year'] == 30

    def test_company_admin_cannot_patch_other_company_settings(
        self, api_client, company_admin, company_b
    ):
        # company_admin belongs to company_a, company_b is a different company
        # get_object() will return 404 because company_b is not in their queryset
        auth(api_client, company_admin)
        payload = {'vacation_days_per_year': 99}
        response = api_client.patch(settings_url(company_b.id), payload, format='json')
        # company_b is not in company_admin's queryset → 404
        assert response.status_code in (
            status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND
        )

    def test_employee_cannot_patch_company_settings(
        self, api_client, employee, company_a
    ):
        auth(api_client, employee)
        payload = {'vacation_days_per_year': 99}
        response = api_client.patch(settings_url(company_a.id), payload, format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_superadmin_can_patch_any_company_settings(
        self, api_client, superadmin, company_b
    ):
        auth(api_client, superadmin)
        payload = {'onboarding_enabled': False}
        response = api_client.patch(settings_url(company_b.id), payload, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['onboarding_enabled'] is False

    def test_guest_cannot_patch_company_settings(
        self, api_client, guest, company_a
    ):
        auth(api_client, guest)
        payload = {'vacation_days_per_year': 99}
        response = api_client.patch(settings_url(company_a.id), payload, format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_unauthenticated_cannot_patch_company_settings(
        self, api_client, company_a
    ):
        payload = {'vacation_days_per_year': 99}
        response = api_client.patch(settings_url(company_a.id), payload, format='json')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


# ---------------------------------------------------------------------------
# AC3 — Validation: invalid hex color
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCompanySettingsColorValidation:

    def test_invalid_brand_primary_color_returns_400(
        self, api_client, company_admin, company_a
    ):
        auth(api_client, company_admin)
        payload = {'brand_primary_color': 'not-a-color'}
        response = api_client.patch(settings_url(company_a.id), payload, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_invalid_color_without_hash_returns_400(
        self, api_client, company_admin, company_a
    ):
        auth(api_client, company_admin)
        payload = {'brand_primary_color': 'FF0000'}  # missing #
        response = api_client.patch(settings_url(company_a.id), payload, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_valid_6char_hex_color_accepted(
        self, api_client, company_admin, company_a
    ):
        auth(api_client, company_admin)
        payload = {'brand_primary_color': '#FF5733'}
        response = api_client.patch(settings_url(company_a.id), payload, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['brand_primary_color'] == '#FF5733'

    def test_valid_3char_hex_color_accepted(
        self, api_client, company_admin, company_a
    ):
        auth(api_client, company_admin)
        payload = {'brand_primary_color': '#F53'}
        response = api_client.patch(settings_url(company_a.id), payload, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['brand_primary_color'] == '#F53'

    def test_valid_label_color_accepted(
        self, api_client, company_admin, company_a
    ):
        auth(api_client, company_admin)
        payload = {
            'custom_labels': [
                {'name': 'Urgent', 'color': '#FF0000'},
            ]
        }
        response = api_client.patch(settings_url(company_a.id), payload, format='json')
        assert response.status_code == status.HTTP_200_OK

    def test_invalid_label_color_returns_400(
        self, api_client, company_admin, company_a
    ):
        auth(api_client, company_admin)
        payload = {
            'custom_labels': [
                {'name': 'Urgent', 'color': 'red'},
            ]
        }
        response = api_client.patch(settings_url(company_a.id), payload, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# AC4 — Validation: custom_labels > 30 items
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCompanySettingsLabelsValidation:

    def test_custom_labels_exceeding_30_items_returns_400(
        self, api_client, company_admin, company_a
    ):
        auth(api_client, company_admin)
        labels = [{'name': f'Label {i}', 'color': '#AABBCC'} for i in range(31)]
        payload = {'custom_labels': labels}
        response = api_client.patch(settings_url(company_a.id), payload, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_custom_labels_exactly_30_items_accepted(
        self, api_client, company_admin, company_a
    ):
        auth(api_client, company_admin)
        labels = [{'name': f'Label {i}', 'color': '#AABBCC'} for i in range(30)]
        payload = {'custom_labels': labels}
        response = api_client.patch(settings_url(company_a.id), payload, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['custom_labels']) == 30

    def test_custom_labels_missing_name_returns_400(
        self, api_client, company_admin, company_a
    ):
        auth(api_client, company_admin)
        payload = {'custom_labels': [{'color': '#AABBCC'}]}
        response = api_client.patch(settings_url(company_a.id), payload, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_custom_labels_missing_color_returns_400(
        self, api_client, company_admin, company_a
    ):
        auth(api_client, company_admin)
        payload = {'custom_labels': [{'name': 'Urgent'}]}
        response = api_client.patch(settings_url(company_a.id), payload, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# AC5 — Validation: custom_task_categories > 50
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCompanySettingsCategoriesValidation:

    def test_custom_task_categories_exceeding_50_returns_400(
        self, api_client, company_admin, company_a
    ):
        auth(api_client, company_admin)
        categories = [f'Category {i}' for i in range(51)]
        payload = {'custom_task_categories': categories}
        response = api_client.patch(settings_url(company_a.id), payload, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_custom_task_categories_exactly_50_items_accepted(
        self, api_client, company_admin, company_a
    ):
        auth(api_client, company_admin)
        categories = [f'Category {i}' for i in range(50)]
        payload = {'custom_task_categories': categories}
        response = api_client.patch(settings_url(company_a.id), payload, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['custom_task_categories']) == 50


# ---------------------------------------------------------------------------
# AC6 — Validation: vacation_days_per_year < 0
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCompanySettingsVacationDaysValidation:

    def test_vacation_days_negative_returns_400(
        self, api_client, company_admin, company_a
    ):
        auth(api_client, company_admin)
        payload = {'vacation_days_per_year': -1}
        response = api_client.patch(settings_url(company_a.id), payload, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_vacation_days_zero_accepted(
        self, api_client, company_admin, company_a
    ):
        auth(api_client, company_admin)
        payload = {'vacation_days_per_year': 0}
        response = api_client.patch(settings_url(company_a.id), payload, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['vacation_days_per_year'] == 0

    def test_vacation_days_positive_accepted(
        self, api_client, company_admin, company_a
    ):
        auth(api_client, company_admin)
        payload = {'vacation_days_per_year': 28}
        response = api_client.patch(settings_url(company_a.id), payload, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['vacation_days_per_year'] == 28


# ---------------------------------------------------------------------------
# AC7 / AC8 — Validation: working_hours start/end
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCompanySettingsWorkingHoursValidation:

    def test_working_hours_start_equal_end_returns_400(
        self, api_client, company_admin, company_a
    ):
        auth(api_client, company_admin)
        payload = {'working_hours': {'start': '09:00', 'end': '09:00'}}
        response = api_client.patch(settings_url(company_a.id), payload, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_working_hours_start_after_end_returns_400(
        self, api_client, company_admin, company_a
    ):
        auth(api_client, company_admin)
        payload = {'working_hours': {'start': '18:00', 'end': '09:00'}}
        response = api_client.patch(settings_url(company_a.id), payload, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_working_hours_start_before_end_accepted(
        self, api_client, company_admin, company_a
    ):
        auth(api_client, company_admin)
        payload = {'working_hours': {'start': '09:00', 'end': '18:00'}}
        response = api_client.patch(settings_url(company_a.id), payload, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['working_hours'] == {'start': '09:00', 'end': '18:00'}

    def test_working_hours_missing_end_returns_400(
        self, api_client, company_admin, company_a
    ):
        auth(api_client, company_admin)
        payload = {'working_hours': {'start': '09:00'}}
        response = api_client.patch(settings_url(company_a.id), payload, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_working_hours_missing_start_returns_400(
        self, api_client, company_admin, company_a
    ):
        auth(api_client, company_admin)
        payload = {'working_hours': {'end': '18:00'}}
        response = api_client.patch(settings_url(company_a.id), payload, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_working_hours_persisted_in_db(
        self, api_client, company_admin, company_a
    ):
        auth(api_client, company_admin)
        payload = {'working_hours': {'start': '08:30', 'end': '17:30'}}
        response = api_client.patch(settings_url(company_a.id), payload, format='json')
        assert response.status_code == status.HTTP_200_OK

        # working_hours are now persisted on Company, not CompanySettings.
        company_a.refresh_from_db()
        assert company_a.working_hours_start is not None
        assert company_a.working_hours_start.strftime('%H:%M') == '08:30'
        assert company_a.working_hours_end.strftime('%H:%M') == '17:30'

    def test_superadmin_can_set_working_hours_for_any_company(
        self, api_client, superadmin, company_b
    ):
        auth(api_client, superadmin)
        payload = {'working_hours': {'start': '10:00', 'end': '19:00'}}
        response = api_client.patch(settings_url(company_b.id), payload, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['working_hours']['start'] == '10:00'
        assert response.data['working_hours']['end'] == '19:00'


# ---------------------------------------------------------------------------
# AC9 — Partial update: only changed fields
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCompanySettingsPartialUpdate:

    def test_patch_only_updates_provided_fields(
        self, api_client, company_admin, company_a
    ):
        # Pre-set some values
        settings_obj = CompanySettings.objects.get(company=company_a)
        settings_obj.vacation_days_per_year = 20
        settings_obj.onboarding_enabled = True
        settings_obj.save()

        auth(api_client, company_admin)
        # Only update vacation_days_per_year
        payload = {'vacation_days_per_year': 25}
        response = api_client.patch(settings_url(company_a.id), payload, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['vacation_days_per_year'] == 25
        # onboarding_enabled should remain unchanged
        assert response.data['onboarding_enabled'] is True

    def test_patch_multiple_fields_simultaneously(
        self, api_client, company_admin, company_a
    ):
        auth(api_client, company_admin)
        payload = {
            'vacation_days_per_year': 14,
            'onboarding_enabled': False,
            'brand_primary_color': '#123456',
            'custom_task_categories': ['Design', 'Dev', 'QA'],
        }
        response = api_client.patch(settings_url(company_a.id), payload, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['vacation_days_per_year'] == 14
        assert response.data['onboarding_enabled'] is False
        assert response.data['brand_primary_color'] == '#123456'
        assert response.data['custom_task_categories'] == ['Design', 'Dev', 'QA']

    def test_patch_custom_labels_updates_db(
        self, api_client, company_admin, company_a
    ):
        auth(api_client, company_admin)
        labels = [
            {'name': 'Bug', 'color': '#FF0000'},
            {'name': 'Feature', 'color': '#00FF00'},
        ]
        payload = {'custom_labels': labels}
        response = api_client.patch(settings_url(company_a.id), payload, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['custom_labels']) == 2

        settings_obj = CompanySettings.objects.get(company=company_a)
        assert len(settings_obj.custom_labels) == 2
        assert settings_obj.custom_labels[0]['name'] == 'Bug'


# ---------------------------------------------------------------------------
# AC10 — CompanySettings created if not exists (get_or_create)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCompanySettingsGetOrCreate:

    def test_settings_created_on_get_if_not_exist(
        self, api_client, superadmin, company_b
    ):
        CompanySettings.objects.filter(company=company_b).delete()
        assert not CompanySettings.objects.filter(company=company_b).exists()

        auth(api_client, superadmin)
        response = api_client.get(settings_url(company_b.id))
        assert response.status_code == status.HTTP_200_OK
        assert CompanySettings.objects.filter(company=company_b).exists()

    def test_settings_created_on_patch_if_not_exist(
        self, api_client, superadmin, company_b
    ):
        CompanySettings.objects.filter(company=company_b).delete()
        assert not CompanySettings.objects.filter(company=company_b).exists()

        auth(api_client, superadmin)
        payload = {'vacation_days_per_year': 15}
        response = api_client.patch(settings_url(company_b.id), payload, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert CompanySettings.objects.filter(company=company_b).exists()
        settings_obj = CompanySettings.objects.get(company=company_b)
        assert settings_obj.vacation_days_per_year == 15

    def test_settings_not_duplicated_on_repeated_get(
        self, api_client, employee, company_a
    ):
        auth(api_client, employee)
        api_client.get(settings_url(company_a.id))
        api_client.get(settings_url(company_a.id))
        assert CompanySettings.objects.filter(company=company_a).count() == 1

    def test_settings_not_duplicated_on_repeated_patch(
        self, api_client, company_admin, company_a
    ):
        auth(api_client, company_admin)
        api_client.patch(settings_url(company_a.id), {'vacation_days_per_year': 10}, format='json')
        api_client.patch(settings_url(company_a.id), {'vacation_days_per_year': 12}, format='json')
        assert CompanySettings.objects.filter(company=company_a).count() == 1
