import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.hr.models import OnboardingStep, OnboardingTemplate, UserOnboardingProgress
from apps.users.models import User


TEMPLATES_URL = '/api/v1/hr/onboarding/templates/'
PROGRESS_URL = '/api/v1/hr/onboarding/progress/'
REGISTER_INVITE_URL = '/api/v1/auth/register/invite/'


def template_detail_url(template_id):
    return f'/api/v1/hr/onboarding/templates/{template_id}/'


def complete_step_url(step_id):
    return f'/api/v1/hr/onboarding/progress/steps/{step_id}/complete/'


def set_default_url(template_id):
    return f'/api/v1/hr/onboarding/templates/{template_id}/set-default/'


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
        # 6 system steps are auto-injected + 2 custom = 8 total
        assert len(response.data['steps']) == 8
        # First 6 are system steps
        assert all(s['is_system'] for s in response.data['steps'][:6])
        # Custom steps follow after position 6
        assert not response.data['steps'][6]['is_system']

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
class TestSetDefaultTemplateAC:
    def test_company_admin_can_set_template_as_default(self, api_client, company_admin, company):
        template, _ = create_template_with_steps(company=company)
        auth(api_client, company_admin)

        response = api_client.post(set_default_url(template.pk))

        assert response.status_code == status.HTTP_200_OK
        template.refresh_from_db()
        assert template.is_default is True

    def test_setting_new_default_unsets_previous_default(self, api_client, company_admin, company):
        first_template, _ = create_template_with_steps(company=company, name='First')
        second_template, _ = create_template_with_steps(company=company, name='Second')
        first_template.set_as_default()
        auth(api_client, company_admin)

        api_client.post(set_default_url(second_template.pk))

        first_template.refresh_from_db()
        second_template.refresh_from_db()
        assert first_template.is_default is False
        assert second_template.is_default is True

    def test_only_one_default_per_company_after_multiple_sets(self, api_client, company_admin, company):
        t1, _ = create_template_with_steps(company=company, name='T1')
        t2, _ = create_template_with_steps(company=company, name='T2')
        t3, _ = create_template_with_steps(company=company, name='T3')
        auth(api_client, company_admin)

        api_client.post(set_default_url(t1.pk))
        api_client.post(set_default_url(t2.pk))
        api_client.post(set_default_url(t3.pk))

        defaults = OnboardingTemplate.objects.filter(company=company, is_default=True)
        assert defaults.count() == 1
        assert defaults.first().pk == t3.pk

    def test_employee_forbidden_from_set_default(self, api_client, employee, company):
        template, _ = create_template_with_steps(company=company)
        auth(api_client, employee)

        response = api_client.post(set_default_url(template.pk))

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_admin_of_other_company_cannot_set_default(self, api_client, other_company_admin, company):
        template, _ = create_template_with_steps(company=company)
        auth(api_client, other_company_admin)

        response = api_client.post(set_default_url(template.pk))

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_initialize_onboarding_uses_default_template(self, company, employee):
        from apps.hr.tasks import initialize_user_onboarding_progress
        _other, _ = create_template_with_steps(company=company, name='Non-default')
        default_template, default_steps = create_template_with_steps(company=company, name='Default')
        default_template.set_as_default()

        count = initialize_user_onboarding_progress(employee)

        assert count == len(default_steps)
        assigned_step_ids = set(
            UserOnboardingProgress.objects.filter(user=employee).values_list('step_id', flat=True)
        )
        assert assigned_step_ids == {s.pk for s in default_steps}


@pytest.mark.django_db
class TestMyOnboardingProgressAC:
    def test_get_progress_returns_completed_and_steps_shape(self, api_client, employee, company):
        from apps.hr.models import OnboardingAssignment
        template, steps = create_template_with_steps(company=company)
        OnboardingAssignment.objects.create(user=employee, template=template, assigned_by=None)
        UserOnboardingProgress.objects.create(user=employee, step=steps[0], is_completed=False)
        UserOnboardingProgress.objects.create(user=employee, step=steps[1], is_completed=True)
        auth(api_client, employee)

        response = api_client.get(PROGRESS_URL)

        assert response.status_code == status.HTTP_200_OK
        # 'assigned' is now part of the response shape
        assert 'assigned' in response.data
        assert response.data['assigned'] is True
        assert isinstance(response.data['completed'], bool)
        assert isinstance(response.data['steps'], list)
        assert set(response.data['steps'][0].keys()) == {'id', 'title', 'is_completed', 'url'}

    def test_progress_shows_only_assigned_template_steps(self, api_client, employee, company):
        """Progress endpoint shows only steps from the assigned template (via OnboardingAssignment)."""
        from apps.hr.models import OnboardingAssignment
        assigned_template, assigned_steps = create_template_with_steps(company=company, name='Assigned')
        other_template, other_steps = create_template_with_steps(company=company, name='Other')

        # Only the assigned_template is wired to the employee via assignment
        OnboardingAssignment.objects.create(user=employee, template=assigned_template, assigned_by=None)

        # Progress rows for both templates
        UserOnboardingProgress.objects.create(user=employee, step=assigned_steps[0], is_completed=False)
        UserOnboardingProgress.objects.create(user=employee, step=assigned_steps[1], is_completed=False)
        # Stale rows from the other template
        UserOnboardingProgress.objects.create(user=employee, step=other_steps[0], is_completed=True)
        UserOnboardingProgress.objects.create(user=employee, step=other_steps[1], is_completed=True)
        auth(api_client, employee)

        response = api_client.get(PROGRESS_URL)

        assert response.status_code == status.HTTP_200_OK
        returned_step_ids = {s['id'] for s in response.data['steps']}
        assert returned_step_ids == {assigned_steps[0].pk, assigned_steps[1].pk}
        assert len(response.data['steps']) == 2


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
        from apps.hr.models import OnboardingAssignment
        template, steps = create_template_with_steps(company=company)
        OnboardingAssignment.objects.create(user=employee, template=template, assigned_by=None)
        OnboardingAssignment.objects.create(user=second_employee, template=template, assigned_by=None)
        UserOnboardingProgress.objects.create(user=employee, step=steps[0], is_completed=True)
        UserOnboardingProgress.objects.create(user=employee, step=steps[1], is_completed=False)
        UserOnboardingProgress.objects.create(user=second_employee, step=steps[0], is_completed=True)
        UserOnboardingProgress.objects.create(user=second_employee, step=steps[1], is_completed=True)
        auth(api_client, company_admin)

        response = api_client.get(team_progress_url())

        assert response.status_code == status.HTTP_200_OK
        assert isinstance(response.data, list)
        # New response shape includes template_id and template_name per employee
        expected_keys = {
            'user', 'first_name', 'last_name', 'avatar', 'role',
            'completed_steps', 'total_steps', 'template_id', 'template_name',
        }
        assert set(response.data[0].keys()) == expected_keys

    def test_employee_forbidden_for_team_progress(self, api_client, employee):
        auth(api_client, employee)
        response = api_client.get(team_progress_url())
        assert response.status_code == status.HTTP_403_FORBIDDEN
