"""
Tests for member deactivation, activation, and removal endpoints.

  POST /api/v1/companies/<id>/members/<user_id>/deactivate/
  POST /api/v1/companies/<id>/members/<user_id>/activate/
  DELETE /api/v1/companies/<id>/members/<user_id>/[?reassign_to=<id>]
"""

import pytest
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from apps.companies.models import Company
from apps.crm.models import Board, Column, Task
from apps.users.models import User


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def deactivate_url(company_id, user_id):
    return f'/api/v1/companies/{company_id}/members/{user_id}/deactivate/'


def activate_url(company_id, user_id):
    return f'/api/v1/companies/{company_id}/members/{user_id}/activate/'


def remove_url(company_id, user_id, reassign_to=None):
    url = f'/api/v1/companies/{company_id}/members/{user_id}/'
    if reassign_to is not None:
        url += f'?reassign_to={reassign_to}'
    return url


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='Test Corp', plan='basic')


@pytest.fixture
def other_company(db):
    return Company.objects.create(name='Other Corp', plan='basic')


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
def company_admin(db, company):
    return User.objects.create_user(
        email='admin@test.com',
        password='pass',
        first_name='Company',
        last_name='Admin',
        role='company_admin',
        company=company,
    )


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='employee@test.com',
        password='pass',
        first_name='Regular',
        last_name='Employee',
        role='employee',
        company=company,
    )


@pytest.fixture
def other_company_admin(db, other_company):
    return User.objects.create_user(
        email='otheradmin@test.com',
        password='pass',
        first_name='Other',
        last_name='Admin',
        role='company_admin',
        company=other_company,
    )


@pytest.fixture
def other_employee(db, other_company):
    return User.objects.create_user(
        email='other_emp@test.com',
        password='pass',
        first_name='Other',
        last_name='Employee',
        role='employee',
        company=other_company,
    )


def _issue_refresh_token(user):
    """Issue a real refresh token so it appears in OutstandingToken."""
    refresh = RefreshToken.for_user(user)
    return refresh


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def auth(client, user):
    client.force_authenticate(user=user)
    return client


# ---------------------------------------------------------------------------
# Deactivate member tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestDeactivateMember:

    def test_company_admin_can_deactivate_employee(self, api_client, company_admin, employee, company):
        auth(api_client, company_admin)
        resp = api_client.post(deactivate_url(company.id, employee.id), format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['detail'] == 'User deactivated successfully'
        employee.refresh_from_db()
        assert employee.is_active is False

    def test_superadmin_can_deactivate_employee(self, api_client, superadmin, employee, company):
        auth(api_client, superadmin)
        resp = api_client.post(deactivate_url(company.id, employee.id), format='json')
        assert resp.status_code == status.HTTP_200_OK
        employee.refresh_from_db()
        assert employee.is_active is False

    def test_cannot_deactivate_yourself(self, api_client, company_admin, company):
        auth(api_client, company_admin)
        resp = api_client.post(deactivate_url(company.id, company_admin.id), format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert resp.data['detail'] == 'Cannot deactivate yourself'

    def test_cannot_deactivate_member_of_different_company(
        self, api_client, company_admin, company, other_employee
    ):
        auth(api_client, company_admin)
        # other_employee belongs to a different company → 404
        resp = api_client.post(deactivate_url(company.id, other_employee.id), format='json')
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_unauthenticated_gets_401(self, api_client, employee, company):
        resp = api_client.post(deactivate_url(company.id, employee.id), format='json')
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_employee_role_gets_403(self, api_client, employee, company):
        # employee cannot deactivate anyone — IsCompanyAdmin blocks them
        other = User.objects.create_user(
            email='emp2@test.com',
            password='pass',
            first_name='Emp2',
            last_name='User',
            role='employee',
            company=company,
        )
        auth(api_client, employee)
        resp = api_client.post(deactivate_url(company.id, other.id), format='json')
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_deactivate_blacklists_outstanding_tokens(self, api_client, company_admin, employee, company):
        # Issue a real token to create an OutstandingToken row.
        _issue_refresh_token(employee)
        assert OutstandingToken.objects.filter(user=employee).exists()

        auth(api_client, company_admin)
        resp = api_client.post(deactivate_url(company.id, employee.id), format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert BlacklistedToken.objects.filter(token__user=employee).exists()


# ---------------------------------------------------------------------------
# Activate member tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestActivateMember:

    def test_company_admin_can_activate_inactive_employee(
        self, api_client, company_admin, employee, company
    ):
        employee.is_active = False
        employee.save(update_fields=['is_active'])

        auth(api_client, company_admin)
        resp = api_client.post(activate_url(company.id, employee.id), format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['detail'] == 'User activated successfully'
        employee.refresh_from_db()
        assert employee.is_active is True

    def test_superadmin_can_activate_employee(
        self, api_client, superadmin, employee, company
    ):
        employee.is_active = False
        employee.save(update_fields=['is_active'])

        auth(api_client, superadmin)
        resp = api_client.post(activate_url(company.id, employee.id), format='json')
        assert resp.status_code == status.HTTP_200_OK
        employee.refresh_from_db()
        assert employee.is_active is True

    def test_cannot_activate_member_of_different_company(
        self, api_client, company_admin, company, other_employee
    ):
        auth(api_client, company_admin)
        resp = api_client.post(activate_url(company.id, other_employee.id), format='json')
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_unauthenticated_gets_401(self, api_client, employee, company):
        resp = api_client.post(activate_url(company.id, employee.id), format='json')
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_employee_role_gets_403(self, api_client, employee, company):
        other = User.objects.create_user(
            email='emp3@test.com',
            password='pass',
            first_name='Emp3',
            last_name='User',
            role='employee',
            company=company,
            is_active=False,
        )
        auth(api_client, employee)
        resp = api_client.post(activate_url(company.id, other.id), format='json')
        assert resp.status_code == status.HTTP_403_FORBIDDEN


# ---------------------------------------------------------------------------
# Remove member tests
# ---------------------------------------------------------------------------

@pytest.fixture
def crm_board(db, company, company_admin):
    board = Board.objects.create(company=company, name='Test Board', created_by=company_admin)
    column = Column.objects.create(board=board, name='To Do', position=0)
    return board, column


@pytest.mark.django_db
class TestRemoveMember:

    def test_company_admin_can_remove_employee(
        self, api_client, company_admin, employee, company
    ):
        auth(api_client, company_admin)
        resp = api_client.delete(remove_url(company.id, employee.id), format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['detail'] == 'User removed from company'
        employee.refresh_from_db()
        assert employee.company is None
        assert employee.is_active is False
        assert employee.role == 'guest'

    def test_remove_blacklists_outstanding_tokens(
        self, api_client, company_admin, employee, company
    ):
        _issue_refresh_token(employee)
        assert OutstandingToken.objects.filter(user=employee).exists()

        auth(api_client, company_admin)
        resp = api_client.delete(remove_url(company.id, employee.id), format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert BlacklistedToken.objects.filter(token__user=employee).exists()

    def test_cannot_remove_yourself(self, api_client, company_admin, company):
        auth(api_client, company_admin)
        resp = api_client.delete(remove_url(company.id, company_admin.id), format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert resp.data['detail'] == 'Cannot remove yourself'

    def test_company_admin_cannot_remove_another_company_admin(
        self, api_client, company, company_admin
    ):
        target_admin = User.objects.create_user(
            email='admin2@test.com',
            password='pass',
            first_name='Admin2',
            last_name='User',
            role='company_admin',
            company=company,
        )
        auth(api_client, company_admin)
        resp = api_client.delete(remove_url(company.id, target_admin.id), format='json')
        assert resp.status_code == status.HTTP_403_FORBIDDEN
        assert resp.data['detail'] == 'Only superadmin can remove a company admin'

    def test_superadmin_can_remove_company_admin(
        self, api_client, superadmin, company, company_admin
    ):
        auth(api_client, superadmin)
        resp = api_client.delete(remove_url(company.id, company_admin.id), format='json')
        assert resp.status_code == status.HTTP_200_OK
        company_admin.refresh_from_db()
        assert company_admin.company is None
        assert company_admin.role == 'guest'

    def test_remove_with_reassign_to_reassigns_tasks(
        self, api_client, company_admin, employee, company, crm_board
    ):
        _, column = crm_board
        reassignee = User.objects.create_user(
            email='reassignee@test.com',
            password='pass',
            first_name='Re',
            last_name='Assignee',
            role='employee',
            company=company,
        )
        task1 = Task.objects.create(column=column, title='Task 1', assignee=employee, created_by=employee)
        task2 = Task.objects.create(column=column, title='Task 2', assignee=employee, created_by=employee)
        # A task belonging to the employee but in another company board should NOT be reassigned.
        other_co = Company.objects.create(name='Unrelated Corp', plan='basic')
        other_board = Board.objects.create(company=other_co, name='Other Board', created_by=employee)
        other_column = Column.objects.create(board=other_board, name='Col', position=0)
        unrelated_task = Task.objects.create(
            column=other_column, title='Unrelated', assignee=employee, created_by=employee
        )

        auth(api_client, company_admin)
        resp = api_client.delete(
            remove_url(company.id, employee.id, reassign_to=reassignee.id),
            format='json',
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['tasks_reassigned'] == 2

        task1.refresh_from_db()
        task2.refresh_from_db()
        assert task1.assignee_id == reassignee.id
        assert task2.assignee_id == reassignee.id

        # Unrelated task must remain unchanged.
        unrelated_task.refresh_from_db()
        assert unrelated_task.assignee_id == employee.id

    def test_remove_without_reassign_to_unassigns_tasks(
        self, api_client, company_admin, employee, company, crm_board
    ):
        _, column = crm_board
        task = Task.objects.create(column=column, title='Orphan Task', assignee=employee, created_by=employee)

        auth(api_client, company_admin)
        resp = api_client.delete(remove_url(company.id, employee.id), format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['tasks_reassigned'] == 1

        task.refresh_from_db()
        assert task.assignee is None

    def test_reassign_to_inactive_user_returns_400(
        self, api_client, company_admin, employee, company
    ):
        inactive_user = User.objects.create_user(
            email='inactive@test.com',
            password='pass',
            first_name='Inactive',
            last_name='User',
            role='employee',
            company=company,
            is_active=False,
        )
        auth(api_client, company_admin)
        resp = api_client.delete(
            remove_url(company.id, employee.id, reassign_to=inactive_user.id),
            format='json',
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert 'reassign_to' in resp.data['detail']

    def test_reassign_to_user_from_another_company_returns_400(
        self, api_client, company_admin, employee, company, other_employee
    ):
        auth(api_client, company_admin)
        resp = api_client.delete(
            remove_url(company.id, employee.id, reassign_to=other_employee.id),
            format='json',
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert 'reassign_to' in resp.data['detail']

    def test_cannot_remove_member_of_different_company(
        self, api_client, company_admin, company, other_employee
    ):
        auth(api_client, company_admin)
        resp = api_client.delete(remove_url(company.id, other_employee.id), format='json')
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_unauthenticated_gets_401(self, api_client, employee, company):
        resp = api_client.delete(remove_url(company.id, employee.id), format='json')
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED
