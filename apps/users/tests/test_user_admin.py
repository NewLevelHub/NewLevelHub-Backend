"""
Tests for superadmin user management endpoints:
  GET /api/v1/auth/users/
  GET /api/v1/auth/users/<id>/
"""
import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.users.models import User
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken

LIST_URL = '/api/v1/auth/users/'
LOGIN_URL = '/api/v1/auth/login/'
ME_URL = '/api/v1/auth/me/'


def detail_url(pk):
    return f'/api/v1/auth/users/{pk}/'


def block_url(pk):
    return f'/api/v1/auth/users/{pk}/block/'


def unblock_url(pk):
    return f'/api/v1/auth/users/{pk}/unblock/'


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email='superadmin@example.com',
        password='Pass123!',
        first_name='Super',
        last_name='Admin',
        role='superadmin',
    )


@pytest.fixture
def company_admin(db, company):
    return User.objects.create_user(
        email='cadmin@example.com',
        password='Pass123!',
        first_name='Company',
        last_name='Admin',
        role='company_admin',
        company=company,
    )


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='employee@example.com',
        password='Pass123!',
        first_name='Emp',
        last_name='Loyee',
        role='employee',
        company=company,
    )


@pytest.fixture
def guest_user(db):
    return User.objects.create_user(
        email='guest@example.com',
        password='Pass123!',
        first_name='Guest',
        last_name='User',
        role='guest',
    )


@pytest.fixture
def company(db):
    from apps.companies.models import Company
    return Company.objects.create(name='Acme Corp')


@pytest.fixture
def other_company(db):
    from apps.companies.models import Company
    return Company.objects.create(name='Other Corp')


@pytest.fixture
def auth_client(api_client, superadmin):
    api_client.force_authenticate(user=superadmin)
    return api_client


# ── UserListView ───────────────────────────────────────────────────────


class TestUserListAuth:
    def test_unauthenticated_returns_401(self, api_client, db):
        response = api_client.get(LIST_URL)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_company_admin_returns_403(self, api_client, company_admin):
        api_client.force_authenticate(user=company_admin)
        response = api_client.get(LIST_URL)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_employee_returns_403(self, api_client, employee):
        api_client.force_authenticate(user=employee)
        response = api_client.get(LIST_URL)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_guest_returns_403(self, api_client, guest_user):
        api_client.force_authenticate(user=guest_user)
        response = api_client.get(LIST_URL)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_superadmin_returns_200(self, auth_client, superadmin):
        response = auth_client.get(LIST_URL)
        assert response.status_code == status.HTTP_200_OK


class TestUserListResponse:
    def test_returns_paginated_results(self, auth_client, superadmin):
        response = auth_client.get(LIST_URL)
        assert response.status_code == status.HTTP_200_OK
        data = response.data
        assert 'results' in data
        assert 'count' in data

    def test_response_fields(self, auth_client, superadmin):
        response = auth_client.get(LIST_URL)
        user_data = response.data['results'][0]
        expected_fields = {
            'id', 'email', 'first_name', 'last_name', 'full_name',
            'role', 'company', 'is_active', 'date_joined', 'last_login', 'avatar',
        }
        assert expected_fields.issubset(set(user_data.keys()))

    def test_superadmin_sees_all_users(self, auth_client, superadmin, company_admin, employee, guest_user):
        response = auth_client.get(LIST_URL)
        assert response.status_code == status.HTTP_200_OK
        returned_ids = {u['id'] for u in response.data['results']}
        assert superadmin.id in returned_ids
        assert company_admin.id in returned_ids
        assert employee.id in returned_ids
        assert guest_user.id in returned_ids


class TestUserListFilters:
    def test_filter_by_role(self, auth_client, superadmin, employee):
        response = auth_client.get(LIST_URL, {'role': 'employee'})
        assert response.status_code == status.HTTP_200_OK
        roles = {u['role'] for u in response.data['results']}
        assert roles == {'employee'}

    def test_filter_by_is_active_false(self, auth_client, superadmin):
        inactive = User.objects.create_user(
            email='inactive@example.com',
            password='Pass123!',
            first_name='Inactive',
            last_name='User',
            is_active=False,
        )
        response = auth_client.get(LIST_URL, {'is_active': 'false'})
        assert response.status_code == status.HTTP_200_OK
        returned_ids = {u['id'] for u in response.data['results']}
        assert inactive.id in returned_ids
        assert superadmin.id not in returned_ids

    def test_filter_by_company_id(self, auth_client, superadmin, company_admin, employee, company):
        response = auth_client.get(LIST_URL, {'company_id': company.id})
        assert response.status_code == status.HTTP_200_OK
        returned_ids = {u['id'] for u in response.data['results']}
        assert company_admin.id in returned_ids
        assert employee.id in returned_ids
        assert superadmin.id not in returned_ids


class TestUserListSearch:
    def test_search_by_email(self, auth_client, superadmin, employee):
        response = auth_client.get(LIST_URL, {'search': 'employee@'})
        assert response.status_code == status.HTTP_200_OK
        returned_ids = {u['id'] for u in response.data['results']}
        assert employee.id in returned_ids
        assert superadmin.id not in returned_ids

    def test_search_by_first_name(self, auth_client, superadmin, employee):
        response = auth_client.get(LIST_URL, {'search': 'Emp'})
        assert response.status_code == status.HTTP_200_OK
        returned_ids = {u['id'] for u in response.data['results']}
        assert employee.id in returned_ids

    def test_search_by_last_name(self, auth_client, superadmin, employee):
        response = auth_client.get(LIST_URL, {'search': 'Loyee'})
        assert response.status_code == status.HTTP_200_OK
        returned_ids = {u['id'] for u in response.data['results']}
        assert employee.id in returned_ids


class TestUserListOrdering:
    def test_ordering_by_date_joined_asc(self, auth_client, superadmin, employee):
        response = auth_client.get(LIST_URL, {'ordering': 'date_joined'})
        assert response.status_code == status.HTTP_200_OK
        results = response.data['results']
        dates = [r['date_joined'] for r in results]
        assert dates == sorted(dates)

    def test_ordering_by_date_joined_desc(self, auth_client, superadmin, employee):
        response = auth_client.get(LIST_URL, {'ordering': '-date_joined'})
        assert response.status_code == status.HTTP_200_OK
        results = response.data['results']
        dates = [r['date_joined'] for r in results]
        assert dates == sorted(dates, reverse=True)

    def test_ordering_by_last_login(self, auth_client, superadmin, employee):
        response = auth_client.get(LIST_URL, {'ordering': 'last_login'})
        assert response.status_code == status.HTTP_200_OK
        # Just verify it doesn't error — last_login can be null
        assert response.data['count'] >= 1


# ── UserDetailView ─────────────────────────────────────────────────────


class TestUserDetailAuth:
    def test_unauthenticated_returns_401(self, api_client, superadmin):
        response = api_client.get(detail_url(superadmin.id))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_company_admin_returns_403(self, api_client, company_admin, superadmin):
        api_client.force_authenticate(user=company_admin)
        response = api_client.get(detail_url(superadmin.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_employee_returns_403(self, api_client, employee, superadmin):
        api_client.force_authenticate(user=employee)
        response = api_client.get(detail_url(superadmin.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_guest_returns_403(self, api_client, guest_user, superadmin):
        api_client.force_authenticate(user=guest_user)
        response = api_client.get(detail_url(superadmin.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_superadmin_returns_200(self, auth_client, employee):
        response = auth_client.get(detail_url(employee.id))
        assert response.status_code == status.HTTP_200_OK

    def test_nonexistent_user_returns_404(self, auth_client):
        response = auth_client.get(detail_url(999999))
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestUserDetailResponse:
    def test_response_contains_required_fields(self, auth_client, employee):
        response = auth_client.get(detail_url(employee.id))
        assert response.status_code == status.HTTP_200_OK
        data = response.data
        expected_fields = {
            'id', 'email', 'first_name', 'last_name', 'full_name',
            'phone', 'position', 'avatar', 'role', 'company',
            'is_active', 'is_email_verified', 'date_joined', 'last_login',
            'bookings_count', 'tasks_count',
        }
        assert expected_fields.issubset(set(data.keys()))

    def test_bookings_count_is_zero_by_default(self, auth_client, employee):
        response = auth_client.get(detail_url(employee.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data['bookings_count'] == 0

    def test_tasks_count_is_zero_by_default(self, auth_client, employee):
        response = auth_client.get(detail_url(employee.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data['tasks_count'] == 0

    def test_bookings_count_reflects_actual_bookings(self, auth_client, employee, company):
        from apps.bookings.models import Booking, Resource
        resource = Resource.objects.create(
            name='Desk 1',
            resource_type='desk',
            available_days=[0, 1, 2, 3, 4],
        )
        Booking.objects.create(
            resource=resource,
            user=employee,
            company=company,
            start_time='2025-01-01 09:00:00+00:00',
            end_time='2025-01-01 10:00:00+00:00',
        )
        Booking.objects.create(
            resource=resource,
            user=employee,
            company=company,
            start_time='2025-01-02 09:00:00+00:00',
            end_time='2025-01-02 10:00:00+00:00',
        )
        response = auth_client.get(detail_url(employee.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data['bookings_count'] == 2

    def test_tasks_count_reflects_assigned_tasks(self, auth_client, employee, company):
        from apps.crm.models import Board, Column, Task
        board = Board.objects.create(name='Board', company=company)
        column = Column.objects.create(board=board, name='Todo')
        Task.objects.create(column=column, title='Task 1', assignee=employee)
        Task.objects.create(column=column, title='Task 2', assignee=employee)
        response = auth_client.get(detail_url(employee.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data['tasks_count'] == 2


class TestUserBlockUnblock:
    def test_superadmin_can_block_user_and_invalidate_sessions(self, auth_client, employee):
        login_client = APIClient()
        login_response = login_client.post(
            LOGIN_URL,
            {'email': employee.email, 'password': 'Pass123!'},
            format='json',
        )
        assert login_response.status_code == status.HTTP_200_OK
        refresh = login_response.data['tokens']['refresh']
        access = login_response.data['tokens']['access']

        response = auth_client.post(block_url(employee.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data['detail'] == 'User blocked'

        employee.refresh_from_db()
        assert employee.is_active is False
        assert BlacklistedToken.objects.filter(token__user=employee).exists()

        login_again = login_client.post(
            LOGIN_URL,
            {'email': employee.email, 'password': 'Pass123!'},
            format='json',
        )
        assert login_again.status_code == status.HTTP_403_FORBIDDEN
        assert login_again.data['detail']['detail'] == 'Account is blocked'

        token_client = APIClient()
        token_client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')
        me_response = token_client.get(ME_URL)
        assert me_response.status_code == status.HTTP_401_UNAUTHORIZED

        refresh_response = APIClient().post(
            '/api/v1/auth/token/refresh/',
            {'refresh': refresh},
            format='json',
        )
        assert refresh_response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_superadmin_can_unblock_user(self, auth_client, employee):
        employee.is_active = False
        employee.save(update_fields=['is_active'])

        response = auth_client.post(unblock_url(employee.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data['detail'] == 'User unblocked'

        employee.refresh_from_db()
        assert employee.is_active is True

    def test_only_superadmin_can_block_or_unblock(self, api_client, company_admin, employee):
        api_client.force_authenticate(user=company_admin)

        block_response = api_client.post(block_url(employee.id))
        assert block_response.status_code == status.HTTP_403_FORBIDDEN

        unblock_response = api_client.post(unblock_url(employee.id))
        assert unblock_response.status_code == status.HTTP_403_FORBIDDEN

    def test_unauthenticated_cannot_block_or_unblock(self, api_client, employee):
        block_response = api_client.post(block_url(employee.id))
        assert block_response.status_code == status.HTTP_401_UNAUTHORIZED

        unblock_response = api_client.post(unblock_url(employee.id))
        assert unblock_response.status_code == status.HTTP_401_UNAUTHORIZED
