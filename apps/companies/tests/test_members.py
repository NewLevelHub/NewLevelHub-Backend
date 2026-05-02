"""
Integration tests for company members list and member activity endpoints.

Endpoints under test:
  GET /api/v1/companies/<id>/members/
  GET /api/v1/companies/<company_id>/members/<user_id>/activity/

Acceptance criteria:
  - Members list: fields, filters (role, is_active, search, ordering)
  - Activity: last_login, active/completed task counts, bookings last 30 days
  - company_admin can only see their own company (403 on another)
  - superadmin can see any company
  - Unauthenticated -> 401, guest -> 403, employee -> 403
"""

import pytest
from datetime import timedelta

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.bookings.models import Booking, Resource
from apps.companies.models import Company
from apps.crm.models import Board, Column, Task
from apps.users.models import User


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company_a(db):
    return Company.objects.create(name='Alpha Corp', plan='basic', max_employees=20)


@pytest.fixture
def company_b(db):
    return Company.objects.create(name='Beta Ltd', plan='basic', max_employees=20)


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
def admin_a(db, company_a):
    return User.objects.create_user(
        email='admin@alpha.com',
        password='pass',
        first_name='Alice',
        last_name='Admin',
        role='company_admin',
        company=company_a,
    )


@pytest.fixture
def admin_b(db, company_b):
    return User.objects.create_user(
        email='admin@beta.com',
        password='pass',
        first_name='Bob',
        last_name='Admin',
        role='company_admin',
        company=company_b,
    )


@pytest.fixture
def employee_a(db, company_a):
    return User.objects.create_user(
        email='emp@alpha.com',
        password='pass',
        first_name='Eve',
        last_name='Employee',
        role='employee',
        company=company_a,
    )


@pytest.fixture
def guest_user(db):
    return User.objects.create_user(
        email='guest@test.com',
        password='pass',
        first_name='Gary',
        last_name='Guest',
        role='guest',
    )


def auth(client, user):
    client.force_authenticate(user=user)


def members_url(company_id):
    return f'/api/v1/companies/{company_id}/members/'


def activity_url(company_id, user_id):
    return f'/api/v1/companies/{company_id}/members/{user_id}/activity/'


# ---------------------------------------------------------------------------
# Members list — access control
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestMembersAccessControl:

    def test_unauthenticated_returns_401(self, api_client, company_a):
        resp = api_client.get(members_url(company_a.pk))
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_guest_returns_403(self, api_client, company_a, guest_user):
        auth(api_client, guest_user)
        resp = api_client.get(members_url(company_a.pk))
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_employee_own_company_returns_200(self, api_client, company_a, employee_a):
        auth(api_client, employee_a)
        resp = api_client.get(members_url(company_a.pk))
        assert resp.status_code == status.HTTP_200_OK

    def test_company_admin_own_company_returns_200(self, api_client, company_a, admin_a):
        auth(api_client, admin_a)
        resp = api_client.get(members_url(company_a.pk))
        assert resp.status_code == status.HTTP_200_OK

    def test_company_admin_other_company_returns_403(self, api_client, company_b, admin_a):
        auth(api_client, admin_a)
        resp = api_client.get(members_url(company_b.pk))
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_superadmin_any_company_returns_200(self, api_client, company_b, superadmin):
        auth(api_client, superadmin)
        resp = api_client.get(members_url(company_b.pk))
        assert resp.status_code == status.HTTP_200_OK

    def test_nonexistent_company_returns_404(self, api_client, superadmin):
        auth(api_client, superadmin)
        resp = api_client.get(members_url(99999))
        assert resp.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# Members list — response shape and fields
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestMembersResponseShape:

    def test_response_contains_required_fields(self, api_client, company_a, admin_a):
        auth(api_client, admin_a)
        resp = api_client.get(members_url(company_a.pk))
        assert resp.status_code == status.HTTP_200_OK
        results = resp.data['results']
        # admin_a is a member of company_a
        assert len(results) >= 1
        member = results[0]
        for field in ('id', 'email', 'full_name', 'role', 'position',
                      'avatar', 'is_active', 'date_joined', 'last_login'):
            assert field in member, f'Missing field: {field}'

    def test_members_scoped_to_company(self, api_client, company_a, company_b,
                                       admin_a, admin_b, superadmin):
        auth(api_client, superadmin)
        resp_a = api_client.get(members_url(company_a.pk))
        resp_b = api_client.get(members_url(company_b.pk))
        ids_a = {m['id'] for m in resp_a.data['results']}
        ids_b = {m['id'] for m in resp_b.data['results']}
        assert admin_a.pk in ids_a
        assert admin_b.pk not in ids_a
        assert admin_b.pk in ids_b
        assert admin_a.pk not in ids_b

    def test_global_superadmin_with_company_fk_not_in_roster(
        self, api_client, company_a, admin_a, superadmin,
    ):
        """Platform superadmin must not appear in company employee lists."""
        superadmin.company = company_a
        superadmin.save(update_fields=['company'])
        auth(api_client, admin_a)
        resp = api_client.get(members_url(company_a.pk))
        assert resp.status_code == status.HTTP_200_OK
        ids = {m['id'] for m in resp.data['results']}
        assert superadmin.pk not in ids


# ---------------------------------------------------------------------------
# Members list — filters
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestMembersFilters:

    def test_filter_by_role(self, api_client, company_a, admin_a, employee_a):
        auth(api_client, admin_a)
        resp = api_client.get(members_url(company_a.pk), {'role': 'employee'})
        assert resp.status_code == status.HTTP_200_OK
        ids = {m['id'] for m in resp.data['results']}
        assert employee_a.pk in ids
        assert admin_a.pk not in ids

    def test_filter_by_is_active_false(self, api_client, company_a, admin_a, employee_a):
        employee_a.is_active = False
        employee_a.save()
        auth(api_client, admin_a)
        resp = api_client.get(members_url(company_a.pk), {'is_active': 'false'})
        assert resp.status_code == status.HTTP_200_OK
        ids = {m['id'] for m in resp.data['results']}
        assert employee_a.pk in ids
        assert admin_a.pk not in ids

    def test_filter_search_by_email(self, api_client, company_a, admin_a, employee_a):
        auth(api_client, admin_a)
        resp = api_client.get(members_url(company_a.pk), {'search': 'emp@alpha'})
        assert resp.status_code == status.HTTP_200_OK
        ids = {m['id'] for m in resp.data['results']}
        assert employee_a.pk in ids
        assert admin_a.pk not in ids

    def test_filter_search_by_first_name(self, api_client, company_a, admin_a, employee_a):
        auth(api_client, admin_a)
        resp = api_client.get(members_url(company_a.pk), {'search': 'Eve'})
        assert resp.status_code == status.HTTP_200_OK
        ids = {m['id'] for m in resp.data['results']}
        assert employee_a.pk in ids

    def test_ordering_by_date_joined(self, api_client, company_a, admin_a, employee_a):
        auth(api_client, admin_a)
        resp = api_client.get(members_url(company_a.pk), {'ordering': 'date_joined'})
        assert resp.status_code == status.HTTP_200_OK
        results = resp.data['results']
        dates = [r['date_joined'] for r in results]
        assert dates == sorted(dates)

    def test_ordering_by_date_joined_desc(self, api_client, company_a, admin_a, employee_a):
        auth(api_client, admin_a)
        resp = api_client.get(members_url(company_a.pk), {'ordering': '-date_joined'})
        assert resp.status_code == status.HTTP_200_OK
        results = resp.data['results']
        dates = [r['date_joined'] for r in results]
        assert dates == sorted(dates, reverse=True)


# ---------------------------------------------------------------------------
# Activity endpoint — access control
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestActivityAccessControl:

    def test_unauthenticated_returns_401(self, api_client, company_a, employee_a):
        resp = api_client.get(activity_url(company_a.pk, employee_a.pk))
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_guest_returns_403(self, api_client, company_a, employee_a, guest_user):
        auth(api_client, guest_user)
        resp = api_client.get(activity_url(company_a.pk, employee_a.pk))
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_employee_returns_403(self, api_client, company_a, employee_a):
        auth(api_client, employee_a)
        resp = api_client.get(activity_url(company_a.pk, employee_a.pk))
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_company_admin_own_company_returns_200(self, api_client, company_a,
                                                   admin_a, employee_a):
        auth(api_client, admin_a)
        resp = api_client.get(activity_url(company_a.pk, employee_a.pk))
        assert resp.status_code == status.HTTP_200_OK

    def test_company_admin_other_company_returns_403(self, api_client, company_b,
                                                     employee_a, admin_a):
        # admin_a belongs to company_a, tries to access company_b member
        auth(api_client, admin_a)
        resp = api_client.get(activity_url(company_b.pk, employee_a.pk))
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_superadmin_any_company_returns_200(self, api_client, company_a,
                                                employee_a, superadmin):
        auth(api_client, superadmin)
        resp = api_client.get(activity_url(company_a.pk, employee_a.pk))
        assert resp.status_code == status.HTTP_200_OK

    def test_user_from_wrong_company_returns_404(self, api_client, company_b,
                                                 employee_a, superadmin):
        # employee_a belongs to company_a, not company_b
        auth(api_client, superadmin)
        resp = api_client.get(activity_url(company_b.pk, employee_a.pk))
        assert resp.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# Activity endpoint — response shape and counts
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestActivityResponseShape:

    def test_response_fields(self, api_client, company_a, admin_a, employee_a):
        auth(api_client, admin_a)
        resp = api_client.get(activity_url(company_a.pk, employee_a.pk))
        assert resp.status_code == status.HTTP_200_OK
        for field in ('last_login', 'active_tasks_count',
                      'completed_tasks_count', 'bookings_last_30_days'):
            assert field in resp.data, f'Missing field: {field}'

    def test_task_counts(self, api_client, company_a, admin_a, employee_a):
        board = Board.objects.create(
            company=company_a,
            name='Test Board',
            created_by=admin_a,
        )
        col = Column.objects.create(board=board, name='In Progress', position=0)

        # 2 active tasks
        Task.objects.create(column=col, title='Task 1', assignee=employee_a,
                            created_by=admin_a, is_archived=False)
        Task.objects.create(column=col, title='Task 2', assignee=employee_a,
                            created_by=admin_a, is_archived=False)
        # 1 completed (archived) task
        Task.objects.create(column=col, title='Task Done', assignee=employee_a,
                            created_by=admin_a, is_archived=True)

        auth(api_client, admin_a)
        resp = api_client.get(activity_url(company_a.pk, employee_a.pk))
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['active_tasks_count'] == 2
        assert resp.data['completed_tasks_count'] == 1

    def test_bookings_last_30_days(self, api_client, company_a, admin_a, employee_a):
        resource = Resource.objects.create(
            name='Desk 1',
            resource_type='desk',
        )
        now = timezone.now()
        # 2 bookings in last 30 days
        for i in range(2):
            Booking.objects.create(
                resource=resource,
                user=employee_a,
                company=company_a,
                start_time=now - timedelta(days=i + 1),
                end_time=now - timedelta(days=i + 1) + timedelta(hours=1),
                status='confirmed',
            )
        # 1 booking older than 30 days (should not count)
        Booking.objects.create(
            resource=resource,
            user=employee_a,
            company=company_a,
            start_time=now - timedelta(days=40),
            end_time=now - timedelta(days=40) + timedelta(hours=1),
            status='confirmed',
        )

        auth(api_client, admin_a)
        resp = api_client.get(activity_url(company_a.pk, employee_a.pk))
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['bookings_last_30_days'] == 2

    def test_tasks_from_other_company_not_counted(self, api_client, company_a,
                                                  company_b, admin_a, employee_a,
                                                  admin_b):
        # Task assigned to employee_a but on company_b's board
        board_b = Board.objects.create(
            company=company_b,
            name='Board B',
            created_by=admin_b,
        )
        col_b = Column.objects.create(board=board_b, name='Todo', position=0)
        Task.objects.create(column=col_b, title='Other Co Task',
                            assignee=employee_a, created_by=admin_b, is_archived=False)

        auth(api_client, admin_a)
        resp = api_client.get(activity_url(company_a.pk, employee_a.pk))
        assert resp.status_code == status.HTTP_200_OK
        # Task is on company_b's board, should not appear in company_a counts
        assert resp.data['active_tasks_count'] == 0
