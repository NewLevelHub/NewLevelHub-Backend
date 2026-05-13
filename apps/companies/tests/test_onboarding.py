"""
TDD tests for company onboarding feature.

Acceptance criteria:
  AC1 — CompanySettings.onboarding_completed=False is auto-created when Company is created
  AC2 — GET /api/v1/companies/<id>/onboarding-status/ returns {completed, steps}
  AC3 — Steps are auto-detected from company data
  AC4 — POST /api/v1/companies/<id>/onboarding-status/skip/ sets onboarding_completed=True
  AC5 — GET /auth/me/ includes company.onboarding_completed
  AC6 — Only company_admin (own company) and superadmin can access onboarding endpoints
"""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company, CompanySettings, Invitation
from apps.crm.models import Board
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
def company(db):
    return Company.objects.create(name='Test Corp', plan='basic')


@pytest.fixture
def company_b(db):
    return Company.objects.create(name='Other Corp', plan='basic')


@pytest.fixture
def company_admin(db, company):
    return User.objects.create_user(
        email='admin@test.com',
        password='pass',
        first_name='Alice',
        last_name='Admin',
        role='company_admin',
        company=company,
    )


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='employee@test.com',
        password='pass',
        first_name='Bob',
        last_name='Worker',
        role='employee',
        company=company,
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


@pytest.fixture
def company_admin_b(db, company_b):
    return User.objects.create_user(
        email='admin@other.com',
        password='pass',
        first_name='Carol',
        last_name='Admin',
        role='company_admin',
        company=company_b,
    )


def auth(client, user):
    client.force_authenticate(user=user)
    return client


def onboarding_status_url(company_id):
    return f'/api/v1/companies/{company_id}/onboarding-status/'


def skip_url(company_id):
    return f'/api/v1/companies/{company_id}/onboarding-status/skip/'


REGISTER_INVITE_URL = '/api/v1/auth/register/invite/'
BOARDS_URL = '/api/v1/crm/boards/'


# ---------------------------------------------------------------------------
# TestOnboardingAutoCreate
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestOnboardingAutoCreate:

    def test_onboarding_settings_created_on_company_creation(self, db):
        """Creating a Company must auto-create CompanySettings with onboarding_completed=False."""
        company = Company.objects.create(name='Signal Co', plan='basic')
        settings = CompanySettings.objects.get(company=company)
        assert settings.onboarding_completed is False


# ---------------------------------------------------------------------------
# TestOnboardingStatusGet
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestOnboardingStatusGet:

    def test_company_admin_gets_onboarding_status(self, api_client, company_admin, company):
        auth(api_client, company_admin)
        response = api_client.get(onboarding_status_url(company.id))
        assert response.status_code == status.HTTP_200_OK

    def test_superadmin_gets_onboarding_status(self, api_client, superadmin, company):
        auth(api_client, superadmin)
        response = api_client.get(onboarding_status_url(company.id))
        assert response.status_code == status.HTTP_200_OK

    def test_employee_cannot_get_onboarding_status(self, api_client, employee, company):
        auth(api_client, employee)
        response = api_client.get(onboarding_status_url(company.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_unauthenticated_cannot_get_onboarding_status(self, api_client, company):
        response = api_client.get(onboarding_status_url(company.id))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_company_admin_cannot_get_other_company_onboarding(
        self, api_client, company_admin_b, company
    ):
        """company_admin of company_b cannot access company_a's onboarding status."""
        auth(api_client, company_admin_b)
        response = api_client.get(onboarding_status_url(company.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_response_has_correct_structure(self, api_client, company_admin, company):
        auth(api_client, company_admin)
        response = api_client.get(onboarding_status_url(company.id))
        assert response.status_code == status.HTTP_200_OK
        data = response.data
        assert 'completed' in data
        assert isinstance(data['completed'], bool)
        assert 'steps' in data
        assert isinstance(data['steps'], list)
        assert len(data['steps']) == 4

    def test_steps_have_correct_keys(self, api_client, company_admin, company):
        auth(api_client, company_admin)
        response = api_client.get(onboarding_status_url(company.id))
        for step in response.data['steps']:
            assert 'key' in step
            assert 'title' in step
            assert 'completed' in step
            assert isinstance(step['completed'], bool)

    def test_step_keys_are_correct(self, api_client, company_admin, company):
        auth(api_client, company_admin)
        response = api_client.get(onboarding_status_url(company.id))
        keys = [s['key'] for s in response.data['steps']]
        assert set(keys) == {
            'upload_logo',
            'fill_description',
            'create_first_board',
            'invite_first_employee',
        }


# ---------------------------------------------------------------------------
# TestOnboardingStepAutoDetection
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestOnboardingStepAutoDetection:

    def _get_step(self, response, key):
        return next(s for s in response.data['steps'] if s['key'] == key)

    def test_upload_logo_step_not_completed_when_no_logo(
        self, api_client, company_admin, company
    ):
        company.logo = None
        company.save()
        auth(api_client, company_admin)
        response = api_client.get(onboarding_status_url(company.id))
        step = self._get_step(response, 'upload_logo')
        assert step['completed'] is False

    def test_upload_logo_step_completed_when_logo_set(
        self, api_client, company_admin, company
    ):
        # Set a non-empty logo path directly (avoid actual file upload)
        Company.objects.filter(pk=company.pk).update(logo='company_logos/test.png')
        company.refresh_from_db()
        auth(api_client, company_admin)
        response = api_client.get(onboarding_status_url(company.id))
        step = self._get_step(response, 'upload_logo')
        assert step['completed'] is True

    def test_fill_description_step_not_completed_when_empty(
        self, api_client, company_admin, company
    ):
        company.description = ''
        company.save()
        auth(api_client, company_admin)
        response = api_client.get(onboarding_status_url(company.id))
        step = self._get_step(response, 'fill_description')
        assert step['completed'] is False

    def test_fill_description_step_completed_when_description_set(
        self, api_client, company_admin, company
    ):
        company.description = 'We are a great company.'
        company.save()
        auth(api_client, company_admin)
        response = api_client.get(onboarding_status_url(company.id))
        step = self._get_step(response, 'fill_description')
        assert step['completed'] is True

    def test_create_first_board_step_not_completed_when_no_boards(
        self, api_client, company_admin, company, superadmin
    ):
        auth(api_client, company_admin)
        response = api_client.get(onboarding_status_url(company.id))
        step = self._get_step(response, 'create_first_board')
        assert step['completed'] is False

    def test_create_first_board_step_completed_when_board_exists(
        self, api_client, company_admin, company, superadmin
    ):
        Board.objects.create(company=company, name='First Board', created_by=superadmin)
        auth(api_client, company_admin)
        response = api_client.get(onboarding_status_url(company.id))
        step = self._get_step(response, 'create_first_board')
        assert step['completed'] is True

    def test_invite_first_employee_step_not_completed_when_no_employees(
        self, api_client, company_admin, company
    ):
        # The only member is company_admin — no employees
        auth(api_client, company_admin)
        response = api_client.get(onboarding_status_url(company.id))
        step = self._get_step(response, 'invite_first_employee')
        assert step['completed'] is False

    def test_invite_first_employee_step_completed_when_employee_exists(
        self, api_client, company_admin, company, employee
    ):
        # 'employee' fixture creates a user with role='employee' in the same company
        auth(api_client, company_admin)
        response = api_client.get(onboarding_status_url(company.id))
        step = self._get_step(response, 'invite_first_employee')
        assert step['completed'] is True


@pytest.mark.django_db
class TestInvitedCompanyAdminBoardOnboarding:
    """Invited admins must create boards without separate email verification (CRM gate)."""

    def test_invited_company_admin_post_board_marks_create_first_board(
        self, api_client, company, company_admin
    ):
        invitation = Invitation.objects.create(
            company=company,
            email='invited.admin@onboarding-board.test',
            invited_by=company_admin,
            role='company_admin',
            expires_at=timezone.now() + timedelta(hours=72),
        )
        reg = api_client.post(
            REGISTER_INVITE_URL,
            {
                'token': str(invitation.token),
                'first_name': 'Invited',
                'last_name': 'Admin',
                'password': 'StrongPass123!',
            },
            format='json',
        )
        assert reg.status_code == status.HTTP_201_CREATED
        new_user = User.objects.get(email='invited.admin@onboarding-board.test')
        assert new_user.is_email_verified is False

        auth(api_client, new_user)
        board_resp = api_client.post(BOARDS_URL, {'name': 'Onboarding Board'}, format='json')
        assert board_resp.status_code == status.HTTP_201_CREATED

        status_resp = api_client.get(onboarding_status_url(company.id))
        assert status_resp.status_code == status.HTTP_200_OK
        step = next(s for s in status_resp.data['steps'] if s['key'] == 'create_first_board')
        assert step['completed'] is True


# ---------------------------------------------------------------------------
# TestOnboardingSkip
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestOnboardingSkip:

    def test_company_admin_can_skip_onboarding(self, api_client, company_admin, company):
        auth(api_client, company_admin)
        response = api_client.post(skip_url(company.id))
        assert response.status_code == status.HTTP_200_OK

    def test_superadmin_can_skip_onboarding(self, api_client, superadmin, company):
        auth(api_client, superadmin)
        response = api_client.post(skip_url(company.id))
        assert response.status_code == status.HTTP_200_OK

    def test_employee_cannot_skip_onboarding(self, api_client, employee, company):
        auth(api_client, employee)
        response = api_client.post(skip_url(company.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_unauthenticated_cannot_skip(self, api_client, company):
        response = api_client.post(skip_url(company.id))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_skip_response_has_completed_true(self, api_client, company_admin, company):
        auth(api_client, company_admin)
        response = api_client.post(skip_url(company.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data.get('completed') is True

    def test_skip_sets_onboarding_completed_in_db(self, api_client, company_admin, company):
        auth(api_client, company_admin)
        api_client.post(skip_url(company.id))
        company.settings.refresh_from_db()
        assert company.settings.onboarding_completed is True

    def test_skip_is_idempotent(self, api_client, company_admin, company):
        auth(api_client, company_admin)
        api_client.post(skip_url(company.id))
        response = api_client.post(skip_url(company.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data.get('completed') is True

    def test_company_admin_cannot_skip_other_company_onboarding(
        self, api_client, company_admin_b, company
    ):
        auth(api_client, company_admin_b)
        response = api_client.post(skip_url(company.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN


# ---------------------------------------------------------------------------
# TestMeEndpointOnboarding
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestMeEndpointOnboarding:

    ME_URL = '/api/v1/auth/me/'

    def test_me_includes_company_onboarding_completed(self, api_client, company_admin, company):
        auth(api_client, company_admin)
        response = api_client.get(self.ME_URL)
        assert response.status_code == status.HTTP_200_OK
        assert 'company' in response.data
        assert response.data['company'] is not None
        assert 'onboarding_completed' in response.data['company']

    def test_me_onboarding_completed_false_by_default(self, api_client, company_admin, company):
        auth(api_client, company_admin)
        response = api_client.get(self.ME_URL)
        assert response.data['company']['onboarding_completed'] is False

    def test_me_onboarding_completed_true_after_skip(
        self, api_client, company_admin, company
    ):
        # Skip onboarding first
        auth(api_client, company_admin)
        api_client.post(skip_url(company.id))

        # Now check /me/
        response = api_client.get(self.ME_URL)
        assert response.data['company']['onboarding_completed'] is True
