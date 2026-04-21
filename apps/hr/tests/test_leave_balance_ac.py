from datetime import date

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.hr.models import LeaveBalance, LeaveRequest
from apps.users.models import User


def _auth(client, user):
    client.force_authenticate(user=user)
    return client


def _my_balance_url():
    return '/api/v1/hr/leaves/balance/'


def _set_balance_url():
    return '/api/v1/hr/leaves/balance/set/'


def _team_balance_url():
    return '/api/v1/hr/leaves/balance/team/'


def _review_url(leave_id):
    return f'/api/v1/hr/leaves/{leave_id}/review/'


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='ACME', plan='basic')


@pytest.fixture
def company_admin(db, company):
    return User.objects.create_user(
        email='admin@acme.com',
        password='pass',
        first_name='Alice',
        last_name='Admin',
        role='company_admin',
        company=company,
    )


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='employee@acme.com',
        password='pass',
        first_name='Bob',
        last_name='Employee',
        role='employee',
        company=company,
    )


@pytest.fixture
def employee_two(db, company):
    return User.objects.create_user(
        email='employee2@acme.com',
        password='pass',
        first_name='Jane',
        last_name='Employee',
        role='employee',
        company=company,
    )


@pytest.fixture
def outsider_admin(db):
    other_company = Company.objects.create(name='Other', plan='basic')
    return User.objects.create_user(
        email='admin@other.com',
        password='pass',
        first_name='Out',
        last_name='Sider',
        role='company_admin',
        company=other_company,
    )


@pytest.mark.django_db
class TestLeaveBalanceAcceptanceCriteria:
    def test_get_my_balance_returns_year_totals_and_remaining(self, api_client, employee, company):
        company.settings.vacation_days_per_year = 28
        company.settings.save(update_fields=['vacation_days_per_year'])

        _auth(api_client, employee)
        response = api_client.get(_my_balance_url(), {'year': 2026})

        assert response.status_code == status.HTTP_200_OK
        assert response.data['year'] == 2026
        assert response.data['total_days'] == 28
        assert response.data['used_days'] == 0
        assert response.data['remaining_days'] == 28

    def test_company_admin_can_set_user_balance_for_year(self, api_client, company_admin, employee):
        _auth(api_client, company_admin)
        response = api_client.post(
            _set_balance_url(),
            {'user_id': employee.id, 'year': 2026, 'total_days': 35},
            format='json',
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data['user_id'] == employee.id
        assert response.data['year'] == 2026
        assert response.data['total_days'] == 35

    def test_non_admin_cannot_set_user_balance(self, api_client, employee):
        _auth(api_client, employee)
        response = api_client.post(
            _set_balance_url(),
            {'user_id': employee.id, 'year': 2026, 'total_days': 35},
            format='json',
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_company_admin_can_view_team_balances(self, api_client, company_admin, employee, employee_two):
        LeaveBalance.objects.create(user=employee, year=2026, total_days=25, used_days=5)
        LeaveBalance.objects.create(user=employee_two, year=2026, total_days=30, used_days=2)

        _auth(api_client, company_admin)
        response = api_client.get(_team_balance_url(), {'year': 2026})

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2
        assert {item['user_id'] for item in response.data} == {employee.id, employee_two.id}

    def test_approved_vacation_deducts_used_days(self, api_client, company_admin, employee):
        balance = LeaveBalance.objects.create(user=employee, year=2026, total_days=28, used_days=0)
        leave = LeaveRequest.objects.create(
            user=employee,
            company=employee.company,
            leave_type='vacation',
            status='pending',
            start_date=date(2026, 5, 5),
            end_date=date(2026, 5, 7),
        )

        _auth(api_client, company_admin)
        response = api_client.post(_review_url(leave.id), {'status': 'approved'}, format='json')

        assert response.status_code == status.HTTP_200_OK
        balance.refresh_from_db()
        assert balance.used_days == 3

    def test_cancel_approved_leave_returns_days_to_balance(self, api_client, company_admin, employee):
        balance = LeaveBalance.objects.create(user=employee, year=2026, total_days=28, used_days=0)
        leave = LeaveRequest.objects.create(
            user=employee,
            company=employee.company,
            leave_type='vacation',
            status='pending',
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 2),
        )

        _auth(api_client, company_admin)
        approve_resp = api_client.post(_review_url(leave.id), {'status': 'approved'}, format='json')
        assert approve_resp.status_code == status.HTTP_200_OK
        balance.refresh_from_db()
        assert balance.used_days == 2

        cancel_resp = api_client.post(_review_url(leave.id), {'status': 'rejected'}, format='json')
        assert cancel_resp.status_code == status.HTTP_200_OK
        balance.refresh_from_db()
        assert balance.used_days == 0

    @pytest.mark.parametrize('leave_type', ['sick_leave', 'remote'])
    def test_sick_leave_and_remote_do_not_spend_vacation_days(
        self, api_client, company_admin, employee, leave_type
    ):
        balance = LeaveBalance.objects.create(user=employee, year=2026, total_days=28, used_days=4)
        leave = LeaveRequest.objects.create(
            user=employee,
            company=employee.company,
            leave_type=leave_type,
            status='pending',
            start_date=date(2026, 6, 10),
            end_date=date(2026, 6, 12),
        )

        _auth(api_client, company_admin)
        response = api_client.post(_review_url(leave.id), {'status': 'approved'}, format='json')

        assert response.status_code == status.HTTP_200_OK
        balance.refresh_from_db()
        assert balance.used_days == 4

    def test_company_admin_cannot_view_other_company_team_balances(
        self, api_client, outsider_admin, employee
    ):
        LeaveBalance.objects.create(user=employee, year=2026, total_days=20, used_days=1)
        _auth(api_client, outsider_admin)
        response = api_client.get(_team_balance_url(), {'year': 2026})

        assert response.status_code == status.HTTP_200_OK
        assert response.data == []
