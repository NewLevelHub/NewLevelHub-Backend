"""
Acceptance tests for the OnboardingAssignment endpoints.

Coverage:
- POST /hr/onboarding/assignments/ — successful assign, creates progress rows
- POST /hr/onboarding/assignments/ — reassign, progress on overlapping steps preserved
- POST /hr/onboarding/assignments/ — foreign user_id or template_id → 403
- GET  /hr/onboarding/assignments/ — admin sees all members (with and without assignments)
- GET  /hr/onboarding/assignments/{user_id}/ — correct per-user progress
- GET  /hr/onboarding/my-assignment/  — employee sees their template + steps
- GET  /hr/onboarding/my-assignment/  — employee without assignment → {"assigned": false}
- Invite flow: accepting invite with a default template → OnboardingAssignment created
- onboarding_team_progress: returns per-employee template_name
"""
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company, Invitation
from apps.hr.models import (
    OnboardingAssignment, OnboardingStep, OnboardingTemplate, UserOnboardingProgress,
)
from apps.users.models import User

ASSIGNMENTS_URL = '/api/v1/hr/onboarding/assignments/'
MY_ASSIGNMENT_URL = '/api/v1/hr/onboarding/my-assignment/'
TEAM_PROGRESS_URL = '/api/v1/hr/onboarding/progress/team/'
REGISTER_INVITE_URL = '/api/v1/auth/register/invite/'


def assignment_detail_url(user_id):
    return f'/api/v1/hr/onboarding/assignments/{user_id}/'


def auth(client, user):
    client.force_authenticate(user=user)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='Assign Co', plan='basic')


@pytest.fixture
def other_company(db):
    return Company.objects.create(name='Other Co', plan='basic')


@pytest.fixture
def admin(db, company):
    return User.objects.create_user(
        email='admin@assign.test',
        password='pass',
        first_name='Admin',
        last_name='A',
        role='company_admin',
        company=company,
    )


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='emp@assign.test',
        password='pass',
        first_name='Emp',
        last_name='B',
        role='employee',
        company=company,
    )


@pytest.fixture
def employee2(db, company):
    return User.objects.create_user(
        email='emp2@assign.test',
        password='pass',
        first_name='Emp2',
        last_name='C',
        role='employee',
        company=company,
    )


@pytest.fixture
def other_admin(db, other_company):
    return User.objects.create_user(
        email='admin@other.test',
        password='pass',
        first_name='Other',
        last_name='Admin',
        role='company_admin',
        company=other_company,
    )


@pytest.fixture
def other_employee(db, other_company):
    return User.objects.create_user(
        email='emp@other.test',
        password='pass',
        first_name='Other',
        last_name='Emp',
        role='employee',
        company=other_company,
    )


def make_template(company, name='Template', is_default=False, is_active=True):
    return OnboardingTemplate.objects.create(
        company=company, title=name, is_active=is_active, is_default=is_default,
    )


def make_steps(template, count=3):
    steps = []
    for i in range(1, count + 1):
        steps.append(OnboardingStep.objects.create(
            template=template,
            title=f'Step {i}',
            position=i,
        ))
    return steps


# ---------------------------------------------------------------------------
# POST /hr/onboarding/assignments/ — successful assign
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAssignmentCreate:
    def test_assign_creates_assignment_and_progress_rows(self, api_client, admin, employee, company):
        template = make_template(company)
        steps = make_steps(template)
        auth(api_client, admin)

        response = api_client.post(ASSIGNMENTS_URL, {
            'user_id': employee.id,
            'template_id': template.id,
            'note': 'Welcome!',
        }, format='json')

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['template_id'] == template.id
        assert response.data['total_steps'] == len(steps)
        assert response.data['completed_steps'] == 0

        assert OnboardingAssignment.objects.filter(user=employee, template=template).exists()
        progress_count = UserOnboardingProgress.objects.filter(user=employee, step__template=template).count()
        assert progress_count == len(steps)

    def test_employee_cannot_assign(self, api_client, employee, company):
        template = make_template(company)
        auth(api_client, employee)

        response = api_client.post(ASSIGNMENTS_URL, {
            'user_id': employee.id,
            'template_id': template.id,
        }, format='json')

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_assign_with_foreign_user_id_returns_403(
        self, api_client, admin, other_employee,
    ):
        """Admin of company A cannot assign to a user from company B."""
        other_template = make_template(other_employee.company)
        auth(api_client, admin)

        response = api_client.post(ASSIGNMENTS_URL, {
            'user_id': other_employee.id,
            'template_id': other_template.id,
        }, format='json')

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_assign_with_foreign_template_returns_403(
        self, api_client, admin, employee, other_company,
    ):
        """Admin of company A cannot assign a template from company B to their own employee."""
        foreign_template = make_template(other_company, name='Foreign')
        auth(api_client, admin)

        response = api_client.post(ASSIGNMENTS_URL, {
            'user_id': employee.id,
            'template_id': foreign_template.id,
        }, format='json')

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_reassign_preserves_progress_on_shared_steps(
        self, api_client, admin, employee, company,
    ):
        """
        When an employee is reassigned to a new template, progress rows for steps that
        exist in both templates must be preserved (not overwritten via get_or_create).
        """
        old_template = make_template(company, name='Old')
        shared_step = OnboardingStep.objects.create(
            template=old_template, title='Shared step', position=1,
        )
        new_template = make_template(company, name='New')
        # The new template includes the same step object (unusual but possible in tests)
        # More realistic: we just confirm that progress on old_template steps remains untouched
        # and new template gets fresh rows.
        new_steps = make_steps(new_template)

        # First assignment → create initial progress
        OnboardingAssignment.objects.create(
            user=employee, template=old_template, assigned_by=admin,
        )
        UserOnboardingProgress.objects.create(
            user=employee, step=shared_step, is_completed=True,
        )

        auth(api_client, admin)
        response = api_client.post(ASSIGNMENTS_URL, {
            'user_id': employee.id,
            'template_id': new_template.id,
        }, format='json')

        assert response.status_code == status.HTTP_201_CREATED
        # The assignment now points to new_template
        employee_assignment = OnboardingAssignment.objects.get(user=employee)
        assert employee_assignment.template_id == new_template.id

        # Progress rows for old template's shared step should still exist (not deleted)
        assert UserOnboardingProgress.objects.filter(user=employee, step=shared_step).exists()
        # Progress rows for new template steps created
        for step in new_steps:
            assert UserOnboardingProgress.objects.filter(user=employee, step=step).exists()

    def test_reassign_does_not_overwrite_completed_progress(
        self, api_client, admin, employee, company,
    ):
        """
        Re-assigning the same template a second time should not reset completed steps.
        """
        template = make_template(company)
        steps = make_steps(template, count=2)

        # First assign
        api_client.force_authenticate(user=admin)
        api_client.post(ASSIGNMENTS_URL, {
            'user_id': employee.id,
            'template_id': template.id,
        }, format='json')

        # Employee completes step 0
        UserOnboardingProgress.objects.filter(user=employee, step=steps[0]).update(
            is_completed=True,
        )

        # Reassign same template
        response = api_client.post(ASSIGNMENTS_URL, {
            'user_id': employee.id,
            'template_id': template.id,
        }, format='json')

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['completed_steps'] == 1


# ---------------------------------------------------------------------------
# GET /hr/onboarding/assignments/
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAssignmentList:
    def test_admin_sees_all_members_including_unassigned(
        self, api_client, admin, employee, employee2, company,
    ):
        """All members appear in the list; unassigned members have template_id=null."""
        template = make_template(company)
        make_steps(template)
        OnboardingAssignment.objects.create(
            user=employee, template=template, assigned_by=admin,
        )
        # employee2 has no assignment

        auth(api_client, admin)
        response = api_client.get(ASSIGNMENTS_URL)

        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        user_ids = {item['user_id'] for item in data}
        # Both employees and the admin should appear (admin is a member too)
        assert employee.id in user_ids
        assert employee2.id in user_ids

        emp2_item = next(i for i in data if i['user_id'] == employee2.id)
        assert emp2_item['template_id'] is None
        assert emp2_item['completed_steps'] == 0
        assert emp2_item['total_steps'] == 0

    def test_employee_cannot_access_assignments_list(self, api_client, employee):
        auth(api_client, employee)
        response = api_client.get(ASSIGNMENTS_URL)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_unauthenticated_returns_401(self, api_client):
        response = api_client.get(ASSIGNMENTS_URL)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_assigned_member_shows_correct_progress(
        self, api_client, admin, employee, company,
    ):
        template = make_template(company)
        steps = make_steps(template, count=3)

        OnboardingAssignment.objects.create(
            user=employee, template=template, assigned_by=admin,
        )
        # Create progress rows: 2 completed, 1 not
        UserOnboardingProgress.objects.create(user=employee, step=steps[0], is_completed=True)
        UserOnboardingProgress.objects.create(user=employee, step=steps[1], is_completed=True)
        UserOnboardingProgress.objects.create(user=employee, step=steps[2], is_completed=False)

        auth(api_client, admin)
        response = api_client.get(ASSIGNMENTS_URL)

        emp_item = next(i for i in response.json() if i['user_id'] == employee.id)
        assert emp_item['template_id'] == template.id
        assert emp_item['template_name'] == template.title
        assert emp_item['completed_steps'] == 2
        assert emp_item['total_steps'] == 3
        assert emp_item['assigned_at'] is not None


# ---------------------------------------------------------------------------
# GET /hr/onboarding/assignments/{user_id}/
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAssignmentDetail:
    def test_returns_assignment_details_for_assigned_user(
        self, api_client, admin, employee, company,
    ):
        template = make_template(company)
        steps = make_steps(template, count=2)
        OnboardingAssignment.objects.create(user=employee, template=template, assigned_by=admin)
        UserOnboardingProgress.objects.create(user=employee, step=steps[0], is_completed=True)
        UserOnboardingProgress.objects.create(user=employee, step=steps[1], is_completed=False)

        auth(api_client, admin)
        response = api_client.get(assignment_detail_url(employee.id))

        assert response.status_code == status.HTTP_200_OK
        assert response.data['template_id'] == template.id
        assert response.data['completed_steps'] == 1
        assert response.data['total_steps'] == 2

    def test_returns_null_template_for_unassigned_user(
        self, api_client, admin, employee,
    ):
        auth(api_client, admin)
        response = api_client.get(assignment_detail_url(employee.id))

        assert response.status_code == status.HTTP_200_OK
        assert response.data['template_id'] is None
        assert response.data['completed_steps'] == 0

    def test_admin_of_other_company_cannot_view_detail(
        self, api_client, other_admin, employee,
    ):
        auth(api_client, other_admin)
        response = api_client.get(assignment_detail_url(employee.id))

        # user not in other_admin's company → 403
        assert response.status_code == status.HTTP_403_FORBIDDEN


# ---------------------------------------------------------------------------
# GET /hr/onboarding/my-assignment/
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestMyAssignment:
    def test_employee_with_assignment_sees_template_and_steps(
        self, api_client, employee, admin, company,
    ):
        template = make_template(company, name='My template')
        steps = make_steps(template, count=2)
        OnboardingAssignment.objects.create(
            user=employee, template=template, assigned_by=admin,
        )
        UserOnboardingProgress.objects.create(user=employee, step=steps[0], is_completed=True)
        UserOnboardingProgress.objects.create(user=employee, step=steps[1], is_completed=False)

        auth(api_client, employee)
        response = api_client.get(MY_ASSIGNMENT_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.data['assigned'] is True
        assert response.data['template']['id'] == template.id
        assert response.data['template']['name'] == template.title
        assert response.data['completed_steps'] == 1
        assert response.data['total_steps'] == 2
        assert len(response.data['steps']) == 2

        step_fields = set(response.data['steps'][0].keys())
        assert {'id', 'title', 'is_system', 'is_completed', 'completed_at'} <= step_fields

    def test_employee_without_assignment_returns_assigned_false(
        self, api_client, employee,
    ):
        auth(api_client, employee)
        response = api_client.get(MY_ASSIGNMENT_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.data == {'assigned': False}

    def test_unauthenticated_returns_401(self, api_client):
        response = api_client.get(MY_ASSIGNMENT_URL)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_admin_can_also_see_their_own_assignment(
        self, api_client, admin, company,
    ):
        template = make_template(company)
        make_steps(template, count=1)
        OnboardingAssignment.objects.create(user=admin, template=template, assigned_by=None)

        auth(api_client, admin)
        response = api_client.get(MY_ASSIGNMENT_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.data['assigned'] is True


# ---------------------------------------------------------------------------
# Invite flow — accepting invite with default template creates assignment
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestInviteFlowCreatesAssignment:
    @patch('apps.users.views.send_verification_email.delay')
    def test_invite_with_default_template_creates_assignment(
        self, _mock_email, api_client, admin, company,
    ):
        template = make_template(company, name='Default', is_default=True)
        steps = make_steps(template, count=2)

        invitation = Invitation.objects.create(
            company=company,
            email='newuser@assign.test',
            invited_by=admin,
            role='employee',
            expires_at=timezone.now() + timedelta(hours=72),
        )
        payload = {
            'token': str(invitation.token),
            'first_name': 'New',
            'last_name': 'User',
            'password': 'StrongPass123!',
            'phone': '+77001112233',
        }

        response = api_client.post(REGISTER_INVITE_URL, payload, format='json')

        assert response.status_code == status.HTTP_201_CREATED
        new_user = User.objects.get(email='newuser@assign.test')

        assignment = OnboardingAssignment.objects.filter(user=new_user).first()
        assert assignment is not None, 'OnboardingAssignment should be created'
        assert assignment.template_id == template.id

        progress_count = UserOnboardingProgress.objects.filter(user=new_user).count()
        assert progress_count == len(steps)

    @patch('apps.users.views.send_verification_email.delay')
    def test_invite_without_default_template_no_assignment(
        self, _mock_email, api_client, admin, company,
    ):
        # No template at all
        invitation = Invitation.objects.create(
            company=company,
            email='newuser2@assign.test',
            invited_by=admin,
            role='employee',
            expires_at=timezone.now() + timedelta(hours=72),
        )
        payload = {
            'token': str(invitation.token),
            'first_name': 'New2',
            'last_name': 'User2',
            'password': 'StrongPass123!',
            'phone': '+77001112244',
        }

        response = api_client.post(REGISTER_INVITE_URL, payload, format='json')

        assert response.status_code == status.HTTP_201_CREATED
        new_user = User.objects.get(email='newuser2@assign.test')
        assert not OnboardingAssignment.objects.filter(user=new_user).exists()


# ---------------------------------------------------------------------------
# onboarding_team_progress returns per-employee template info
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTeamProgressWithAssignments:
    def test_team_progress_shows_per_employee_template(
        self, api_client, admin, employee, employee2, company,
    ):
        template_a = make_template(company, name='Template A')
        template_b = make_template(company, name='Template B')
        steps_a = make_steps(template_a, count=2)
        steps_b = make_steps(template_b, count=3)

        OnboardingAssignment.objects.create(user=employee, template=template_a, assigned_by=admin)
        OnboardingAssignment.objects.create(user=employee2, template=template_b, assigned_by=admin)

        # employee has 1/2 completed
        UserOnboardingProgress.objects.create(user=employee, step=steps_a[0], is_completed=True)
        UserOnboardingProgress.objects.create(user=employee, step=steps_a[1], is_completed=False)

        # employee2 has all 3 progress rows (none completed)
        for step in steps_b:
            UserOnboardingProgress.objects.create(user=employee2, step=step, is_completed=False)

        auth(api_client, admin)
        response = api_client.get(TEAM_PROGRESS_URL)

        assert response.status_code == status.HTTP_200_OK
        items = {i['user']: i for i in response.json()}

        emp_item = items[employee.id]
        assert emp_item['template_name'] == 'Template A'
        assert emp_item['completed_steps'] == 1
        assert emp_item['total_steps'] == 2

        emp2_item = items[employee2.id]
        assert emp2_item['template_name'] == 'Template B'
        assert emp2_item['total_steps'] == 3

    def test_team_progress_member_without_assignment_shows_null_template(
        self, api_client, admin, employee, company,
    ):
        # No assignment for employee
        auth(api_client, admin)
        response = api_client.get(TEAM_PROGRESS_URL)

        assert response.status_code == status.HTTP_200_OK
        items = {i['user']: i for i in response.json()}

        emp_item = items.get(employee.id)
        assert emp_item is not None
        assert emp_item['template_id'] is None
        assert emp_item['template_name'] is None
        assert emp_item['completed_steps'] == 0
        assert emp_item['total_steps'] == 0
