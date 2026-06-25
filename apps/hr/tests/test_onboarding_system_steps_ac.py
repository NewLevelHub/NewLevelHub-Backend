import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.hr.constants import SYSTEM_STEPS
from apps.hr.models import OnboardingStep, OnboardingTemplate, UserOnboardingProgress
from apps.users.models import User


TEMPLATES_URL = '/api/v1/hr/onboarding/templates/'
TEAM_PROGRESS_URL = '/api/v1/hr/onboarding/progress/team/'


def steps_url(template_id):
    return f'/api/v1/hr/onboarding/templates/{template_id}/steps/'


def step_detail_url(template_id, step_id):
    return f'/api/v1/hr/onboarding/templates/{template_id}/steps/{step_id}/'


def team_member_progress_url(user_id):
    return f'/api/v1/hr/onboarding/progress/team/{user_id}/'


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='System Steps Co', plan='basic')


@pytest.fixture
def other_company(db):
    return Company.objects.create(name='Other Co', plan='basic')


@pytest.fixture
def company_admin(db, company):
    return User.objects.create_user(
        email='admin@sys步ep.test',
        password='pass',
        first_name='Админ',
        last_name='Тест',
        role='company_admin',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='emp@sysstep.test',
        password='pass',
        first_name='Иван',
        last_name='Петров',
        role='employee',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def other_company_admin(db, other_company):
    return User.objects.create_user(
        email='admin@other.test',
        password='pass',
        first_name='Чужой',
        last_name='Адмик',
        role='company_admin',
        company=other_company,
        is_email_verified=True,
    )


def create_template_via_api(api_client, custom_steps=None):
    payload = {
        'name': 'Test Template',
        'steps': custom_steps or [],
    }
    return api_client.post(TEMPLATES_URL, payload, format='json')


@pytest.mark.django_db
class TestSystemStepsAutoInjection:
    def test_create_template_injects_5_system_steps(self, api_client, company_admin):
        api_client.force_authenticate(user=company_admin)

        response = create_template_via_api(api_client)

        assert response.status_code == status.HTTP_201_CREATED
        system_steps = [s for s in response.data['steps'] if s['is_system']]
        assert len(system_steps) == len(SYSTEM_STEPS)

    def test_system_steps_have_correct_titles(self, api_client, company_admin):
        api_client.force_authenticate(user=company_admin)

        response = create_template_via_api(api_client)

        assert response.status_code == status.HTTP_201_CREATED
        system_titles = [s['title'] for s in response.data['steps'] if s['is_system']]
        expected_titles = [s['title'] for s in SYSTEM_STEPS]
        assert system_titles == expected_titles

    def test_system_steps_positions_1_to_5(self, api_client, company_admin):
        api_client.force_authenticate(user=company_admin)

        response = create_template_via_api(api_client)

        system_positions = sorted(s['order'] for s in response.data['steps'] if s['is_system'])
        assert system_positions == list(range(1, len(SYSTEM_STEPS) + 1))

    def test_custom_steps_follow_after_system_steps(self, api_client, company_admin):
        api_client.force_authenticate(user=company_admin)
        custom = [{'title': 'Инструктаж ИБ', 'description': '', 'url': '', 'order': 99}]

        response = create_template_via_api(api_client, custom_steps=custom)

        assert response.status_code == status.HTTP_201_CREATED
        custom_steps = [s for s in response.data['steps'] if not s['is_system']]
        assert len(custom_steps) == 1
        assert custom_steps[0]['title'] == 'Инструктаж ИБ'
        # Custom step position must be after all system steps
        assert custom_steps[0]['order'] > len(SYSTEM_STEPS)

    def test_patch_template_preserves_system_steps(self, api_client, company_admin, company):
        """PATCH с новым списком кастомных шагов не должен удалять системные."""
        api_client.force_authenticate(user=company_admin)
        create_resp = create_template_via_api(api_client)
        template_id = create_resp.data['id']

        patch_payload = {
            'steps': [{'title': 'Новый кастомный шаг', 'description': '', 'url': '', 'order': 10}]
        }
        response = api_client.patch(
            f'/api/v1/hr/onboarding/templates/{template_id}/', patch_payload, format='json'
        )

        assert response.status_code == status.HTTP_200_OK
        system_steps = [s for s in response.data['steps'] if s['is_system']]
        assert len(system_steps) == len(SYSTEM_STEPS)


@pytest.mark.django_db
class TestSystemStepsImmutability:
    """
    The guard checks template.is_system, not step.is_system.
    A step in a system template is immutable regardless of its own is_system flag.
    A step in a non-system (custom) template is always editable.
    """

    def _get_system_template_step(self, company):
        """Step belonging to a system template — must be immutable."""
        template = OnboardingTemplate.objects.create(
            company=company, title='System T', is_active=True, is_system=True
        )
        step = OnboardingStep.objects.create(
            template=template, title='System', position=1, is_system=True
        )
        return template, step

    def _get_custom_template_step(self, company):
        """Step belonging to a user-created (non-system) template — must be editable."""
        template = OnboardingTemplate.objects.create(
            company=company, title='Custom T', is_active=True, is_system=False
        )
        step = OnboardingStep.objects.create(
            template=template, title='Custom', position=6, is_system=False
        )
        return template, step

    def _get_custom_template_step_marked_system(self, company):
        """
        Step with is_system=True but its template is not a system template.
        Bug scenario: old data where step.is_system got set to True incorrectly.
        Guard must allow edits because template.is_system is False.
        """
        template = OnboardingTemplate.objects.create(
            company=company, title='Custom T2', is_active=True, is_system=False
        )
        step = OnboardingStep.objects.create(
            template=template, title='Wrongly flagged', position=7, is_system=True
        )
        return template, step

    # --- PATCH tests ---

    def test_patch_step_in_system_template_returns_400(self, api_client, company_admin, company):
        """Steps in a system template are immutable — PATCH must return 400."""
        api_client.force_authenticate(user=company_admin)
        template, step = self._get_system_template_step(company)

        response = api_client.patch(step_detail_url(template.pk, step.pk), {'title': 'Changed'}, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_patch_step_in_custom_template_succeeds(self, api_client, company_admin, company):
        """Steps in a custom (non-system) template must be editable."""
        api_client.force_authenticate(user=company_admin)
        template, step = self._get_custom_template_step(company)

        response = api_client.patch(
            step_detail_url(template.pk, step.pk), {'title': 'Updated'}, format='json'
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data['title'] == 'Updated'

    def test_patch_step_with_is_system_true_but_custom_template_succeeds(
        self, api_client, company_admin, company
    ):
        """
        Regression: step.is_system=True on a non-system template must NOT block edits.
        The guard checks template.is_system, not step.is_system.
        """
        api_client.force_authenticate(user=company_admin)
        template, step = self._get_custom_template_step_marked_system(company)

        response = api_client.patch(
            step_detail_url(template.pk, step.pk), {'title': 'Fixed'}, format='json'
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data['title'] == 'Fixed'

    # --- DELETE tests ---

    def test_delete_step_in_system_template_returns_400(self, api_client, company_admin, company):
        """Steps in a system template are immutable — DELETE must return 400."""
        api_client.force_authenticate(user=company_admin)
        template, step = self._get_system_template_step(company)

        response = api_client.delete(step_detail_url(template.pk, step.pk))

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_delete_step_in_custom_template_succeeds(self, api_client, company_admin, company):
        """Steps in a custom (non-system) template must be deletable."""
        api_client.force_authenticate(user=company_admin)
        template, step = self._get_custom_template_step(company)

        response = api_client.delete(step_detail_url(template.pk, step.pk))

        assert response.status_code == status.HTTP_204_NO_CONTENT

    def test_delete_step_with_is_system_true_but_custom_template_succeeds(
        self, api_client, company_admin, company
    ):
        """
        Regression: step.is_system=True on a non-system template must NOT block deletes.
        """
        api_client.force_authenticate(user=company_admin)
        template, step = self._get_custom_template_step_marked_system(company)

        response = api_client.delete(step_detail_url(template.pk, step.pk))

        assert response.status_code == status.HTTP_204_NO_CONTENT

    # --- POST (create) tests ---

    def test_post_step_to_system_template_returns_400(self, api_client, company_admin, company):
        """Cannot add new steps to a system template."""
        api_client.force_authenticate(user=company_admin)
        template = OnboardingTemplate.objects.create(
            company=company, title='System T3', is_active=True, is_system=True
        )

        response = api_client.post(
            steps_url(template.pk),
            {'title': 'New Step', 'description': '', 'url': '', 'order': 99},
            format='json',
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_post_step_to_custom_template_creates_with_is_system_false(
        self, api_client, company_admin, company
    ):
        """New steps added to a custom template must always have is_system=False."""
        api_client.force_authenticate(user=company_admin)
        template = OnboardingTemplate.objects.create(
            company=company, title='Custom T4', is_active=True, is_system=False
        )

        response = api_client.post(
            steps_url(template.pk),
            {'title': 'New Custom Step', 'description': '', 'url': '', 'order': 1},
            format='json',
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['is_system'] is False
        created_step = OnboardingStep.objects.get(pk=response.data['id'])
        assert created_step.is_system is False

    # --- Role / isolation tests ---

    def test_employee_cannot_modify_steps(self, api_client, employee, company):
        api_client.force_authenticate(user=employee)
        template, step = self._get_custom_template_step(company)

        response = api_client.patch(step_detail_url(template.pk, step.pk), {'title': 'X'}, format='json')

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_other_company_admin_cannot_access_steps(self, api_client, other_company_admin, company):
        api_client.force_authenticate(user=other_company_admin)
        template, step = self._get_custom_template_step(company)

        response = api_client.patch(step_detail_url(template.pk, step.pk), {'title': 'X'}, format='json')

        # Template belongs to another company → queryset returns empty → 404
        assert response.status_code in (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND)


@pytest.mark.django_db
class TestTeamProgressEnhanced:
    def test_team_progress_includes_user_profile_fields(self, api_client, company_admin, employee, company):
        api_client.force_authenticate(user=company_admin)

        response = api_client.get(TEAM_PROGRESS_URL)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) >= 1
        member_data = next(m for m in response.data if m['user'] == employee.id)
        assert member_data['first_name'] == employee.first_name
        assert member_data['last_name'] == employee.last_name
        assert 'avatar' in member_data
        assert member_data['role'] == employee.role

    def test_team_progress_counts_are_correct(self, api_client, company_admin, employee, company):
        from apps.hr.models import OnboardingAssignment
        template = OnboardingTemplate.objects.create(company=company, title='T', is_active=True)
        step1 = OnboardingStep.objects.create(template=template, title='S1', position=1)
        step2 = OnboardingStep.objects.create(template=template, title='S2', position=2)
        # Must have an assignment for the new architecture to count steps
        OnboardingAssignment.objects.create(user=employee, template=template, assigned_by=None)
        UserOnboardingProgress.objects.create(user=employee, step=step1, is_completed=True)
        UserOnboardingProgress.objects.create(user=employee, step=step2, is_completed=False)
        api_client.force_authenticate(user=company_admin)

        response = api_client.get(TEAM_PROGRESS_URL)

        member_data = next(m for m in response.data if m['user'] == employee.id)
        assert member_data['completed_steps'] == 1
        assert member_data['total_steps'] == 2


@pytest.mark.django_db
class TestTeamMemberProgressDrillDown:
    def test_admin_can_get_member_drill_down(self, api_client, company_admin, employee, company):
        template = OnboardingTemplate.objects.create(company=company, title='T', is_active=True)
        step1 = OnboardingStep.objects.create(template=template, title='Step 1', position=1, is_system=True)
        step2 = OnboardingStep.objects.create(template=template, title='Step 2', position=2, is_system=False)
        UserOnboardingProgress.objects.create(user=employee, step=step1, is_completed=True)
        UserOnboardingProgress.objects.create(user=employee, step=step2, is_completed=False)
        api_client.force_authenticate(user=company_admin)

        response = api_client.get(team_member_progress_url(employee.id))

        assert response.status_code == status.HTTP_200_OK
        assert response.data['user']['id'] == employee.id
        assert response.data['user']['first_name'] == employee.first_name
        assert response.data['completed_steps'] == 1
        assert response.data['total_steps'] == 2
        assert len(response.data['steps']) == 2
        completed_step = next(s for s in response.data['steps'] if s['id'] == step1.pk)
        assert completed_step['is_completed'] is True
        assert completed_step['is_system'] is True

    def test_drill_down_step_has_required_keys(self, api_client, company_admin, employee, company):
        template = OnboardingTemplate.objects.create(company=company, title='T', is_active=True)
        step = OnboardingStep.objects.create(template=template, title='S', position=1, is_system=True)
        UserOnboardingProgress.objects.create(user=employee, step=step, is_completed=False)
        api_client.force_authenticate(user=company_admin)

        response = api_client.get(team_member_progress_url(employee.id))

        assert response.status_code == status.HTTP_200_OK
        step_data = response.data['steps'][0]
        assert {'id', 'title', 'is_system', 'is_completed', 'completed_at'} <= set(step_data.keys())

    def test_employee_forbidden_from_drill_down(self, api_client, employee):
        api_client.force_authenticate(user=employee)

        response = api_client.get(team_member_progress_url(employee.id))

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_drill_down_returns_404_for_other_company_member(
        self, api_client, company_admin, other_company_admin, other_company
    ):
        other_member = User.objects.create_user(
            email='emp@other.test',
            password='pass',
            first_name='Other',
            last_name='Emp',
            role='employee',
            company=other_company,
        )
        api_client.force_authenticate(user=company_admin)

        response = api_client.get(team_member_progress_url(other_member.id))

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_drill_down_returns_empty_steps_when_no_progress(
        self, api_client, company_admin, employee
    ):
        api_client.force_authenticate(user=company_admin)

        response = api_client.get(team_member_progress_url(employee.id))

        assert response.status_code == status.HTTP_200_OK
        assert response.data['steps'] == []
        assert response.data['total_steps'] == 0
