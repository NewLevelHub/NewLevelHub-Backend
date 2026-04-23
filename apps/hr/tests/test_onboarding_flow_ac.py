from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company, Invitation
from apps.hr.models import OnboardingStep, OnboardingTemplate, UserOnboardingProgress
from apps.users.models import User


TEMPLATES_URL = '/api/v1/hr/onboarding/templates/'
PROGRESS_URL = '/api/v1/hr/onboarding/progress/'
REGISTER_INVITE_URL = '/api/v1/auth/register/invite/'


def template_detail_url(template_id):
    return f'/api/v1/hr/onboarding/templates/{template_id}/'


def complete_step_url(step_id):
    return f'/api/v1/hr/onboarding/progress/steps/{step_id}/complete/'


def team_progress_url():
    return '/api/v1/hr/onboarding/progress/team/'


def auth(client, user):
    client.force_authenticate(user=user)


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='Onboarding Co', plan='basic')


@pytest.fixture
def other_company(db):
    return Company.objects.create(name='Other Onboarding Co', plan='basic')


@pytest.fixture
def company_admin(db, company):
    return User.objects.create_user(
        email='admin@onboarding.test',
        password='pass',
        first_name='Admin',
        last_name='One',
        role='company_admin',
        company=company,
    )


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='employee@onboarding.test',
        password='pass',
        first_name='Employee',
        last_name='One',
        role='employee',
        company=company,
    )


@pytest.fixture
def second_employee(db, company):
    return User.objects.create_user(
        email='employee2@onboarding.test',
        password='pass',
        first_name='Employee',
        last_name='Two',
        role='employee',
        company=company,
    )


@pytest.fixture
def other_company_admin(db, other_company):
    return User.objects.create_user(
        email='admin@other-onboarding.test',
        password='pass',
        first_name='Admin',
        last_name='Two',
        role='company_admin',
        company=other_company,
    )


def create_template_with_steps(*, company, name='Default employee onboarding'):
    template = OnboardingTemplate.objects.create(company=company, title=name, is_active=True)
    step_one = OnboardingStep.objects.create(
        template=template,
        title='Sign HR docs',
        description='Read and sign required HR documents',
        position=1,
    )
    step_two = OnboardingStep.objects.create(
        template=template,
        title='Setup workspace',
        description='Configure laptop and internal tools',
        position=2,
    )
    return template, [step_one, step_two]


@pytest.mark.django_db
class TestOnboardingTemplatesAC:
    def test_company_admin_can_create_template_with_name_and_steps(self, api_client, company_admin):
        auth(api_client, company_admin)
        payload = {
            'name': 'Backend onboarding',
            'steps': [
                {'title': 'Create account', 'description': 'Set up SSO account', 'order': 1},
                {'title': 'Read policies', 'description': 'Read security handbook', 'order': 2},
            ],
        }

        response = api_client.post(TEMPLATES_URL, payload, format='json')

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['name'] == 'Backend onboarding'
        assert len(response.data['steps']) == 2
        assert response.data['steps'][0]['order'] == 1

    def test_company_admin_can_get_templates(self, api_client, company_admin, company):
        create_template_with_steps(company=company)
        auth(api_client, company_admin)

        response = api_client.get(TEMPLATES_URL)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]['steps'][0]['title'] == 'Sign HR docs'

    def test_company_admin_can_patch_template(self, api_client, company_admin, company):
        template, _ = create_template_with_steps(company=company)
        auth(api_client, company_admin)
        payload = {
            'name': 'Updated onboarding',
            'steps': [
                {'title': 'Updated step', 'description': 'Updated description', 'order': 1},
            ],
        }

        response = api_client.patch(template_detail_url(template.pk), payload, format='json')

        assert response.status_code == status.HTTP_200_OK
        assert response.data['name'] == 'Updated onboarding'
        assert len(response.data['steps']) == 1
        assert response.data['steps'][0]['title'] == 'Updated step'

    def test_employee_forbidden_for_template_create(self, api_client, employee):
        auth(api_client, employee)
        response = api_client.post(TEMPLATES_URL, {'name': 'No access', 'steps': []}, format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestInviteRegistrationCreatesOnboardingProgressAC:
    @patch('apps.users.views.send_verification_email.delay')
    def test_register_by_invite_auto_creates_user_progress(
        self,
        _mock_send_email,
        api_client,
        company,
        company_admin,
    ):
        _template, steps = create_template_with_steps(company=company)
        invitation = Invitation.objects.create(
            company=company,
            email='new.employee@onboarding.test',
            invited_by=company_admin,
            role='employee',
            expires_at=timezone.now() + timedelta(hours=72),
        )
        payload = {
            'token': str(invitation.token),
            'first_name': 'New',
            'last_name': 'Employee',
            'password': 'StrongPass123!',
            'phone': '+77000000000',
        }

        response = api_client.post(REGISTER_INVITE_URL, payload, format='json')

        assert response.status_code == status.HTTP_201_CREATED
        created_user = User.objects.get(email='new.employee@onboarding.test')
        progress_qs = UserOnboardingProgress.objects.filter(user=created_user).order_by('step__position')
        assert progress_qs.count() == len(steps)
        assert list(progress_qs.values_list('is_completed', flat=True)) == [False, False]


@pytest.mark.django_db
class TestMyOnboardingProgressAC:
    def test_get_progress_returns_completed_and_steps_shape(self, api_client, employee, company):
        _template, steps = create_template_with_steps(company=company)
        UserOnboardingProgress.objects.create(user=employee, step=steps[0], is_completed=False)
        UserOnboardingProgress.objects.create(user=employee, step=steps[1], is_completed=True)
        auth(api_client, employee)

        response = api_client.get(PROGRESS_URL)

        assert response.status_code == status.HTTP_200_OK
        assert set(response.data.keys()) == {'completed', 'steps'}
        assert isinstance(response.data['completed'], bool)
        assert isinstance(response.data['steps'], list)
        assert set(response.data['steps'][0].keys()) == {'id', 'title', 'is_completed'}


@pytest.mark.django_db
class TestCompleteOnboardingStepAC:
    def test_complete_step_marks_item_as_completed(self, api_client, employee, company):
        _template, steps = create_template_with_steps(company=company)
        UserOnboardingProgress.objects.create(user=employee, step=steps[0], is_completed=False)
        UserOnboardingProgress.objects.create(user=employee, step=steps[1], is_completed=False)
        auth(api_client, employee)

        response = api_client.post(complete_step_url(steps[0].pk))

        assert response.status_code == status.HTTP_200_OK
        step_progress = UserOnboardingProgress.objects.get(user=employee, step=steps[0])
        assert step_progress.is_completed is True
        assert step_progress.completed_at is not None

    def test_all_steps_completed_switches_progress_completed_to_true(self, api_client, employee, company):
        _template, steps = create_template_with_steps(company=company)
        UserOnboardingProgress.objects.create(user=employee, step=steps[0], is_completed=False)
        UserOnboardingProgress.objects.create(user=employee, step=steps[1], is_completed=False)
        auth(api_client, employee)

        first_complete = api_client.post(complete_step_url(steps[0].pk))
        second_complete = api_client.post(complete_step_url(steps[1].pk))
        progress_response = api_client.get(PROGRESS_URL)

        assert first_complete.status_code == status.HTTP_200_OK
        assert second_complete.status_code == status.HTTP_200_OK
        assert progress_response.status_code == status.HTTP_200_OK
        assert progress_response.data['completed'] is True


@pytest.mark.django_db
class TestTeamOnboardingProgressAC:
    def test_company_admin_gets_team_progress(self, api_client, company_admin, employee, second_employee, company):
        _template, steps = create_template_with_steps(company=company)
        UserOnboardingProgress.objects.create(user=employee, step=steps[0], is_completed=True)
        UserOnboardingProgress.objects.create(user=employee, step=steps[1], is_completed=False)
        UserOnboardingProgress.objects.create(user=second_employee, step=steps[0], is_completed=True)
        UserOnboardingProgress.objects.create(user=second_employee, step=steps[1], is_completed=True)
        auth(api_client, company_admin)

        response = api_client.get(team_progress_url())

        assert response.status_code == status.HTTP_200_OK
        assert isinstance(response.data, list)
        assert set(response.data[0].keys()) == {'user', 'completed_steps', 'total_steps'}

    def test_employee_forbidden_for_team_progress(self, api_client, employee):
        auth(api_client, employee)
        response = api_client.get(team_progress_url())
        assert response.status_code == status.HTTP_403_FORBIDDEN
