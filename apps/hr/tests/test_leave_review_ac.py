from datetime import date

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.hr.models import LeaveBalance, LeaveRequest
from apps.notifications.models import Notification
from apps.users.models import User


def _auth(client, user):
    client.force_authenticate(user=user)
    return client


def _review_url(leave_id):
    return f'/api/v1/hr/leaves/{leave_id}/review/'


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='Review AC Co', plan='basic')


@pytest.fixture
def company_admin(db, company):
    return User.objects.create_user(
        email='review-admin@ac.test',
        password='pass',
        first_name='Review',
        last_name='Admin',
        role='company_admin',
        company=company,
    )


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='review-employee@ac.test',
        password='pass',
        first_name='Review',
        last_name='Employee',
        role='employee',
        company=company,
    )


@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email='review-superadmin@ac.test',
        password='pass',
        first_name='Review',
        last_name='Superadmin',
        role='superadmin',
    )


@pytest.fixture
def company_two(db):
    return Company.objects.create(name='Review AC Co 2', plan='basic')


@pytest.fixture
def outsider_admin(db, company_two):
    return User.objects.create_user(
        email='review-outsider-admin@ac.test',
        password='pass',
        first_name='Outsider',
        last_name='Admin',
        role='company_admin',
        company=company_two,
    )


def _create_pending_vacation(employee):
    return LeaveRequest.objects.create(
        user=employee,
        company=employee.company,
        leave_type='vacation',
        status='pending',
        start_date=date(2026, 7, 10),
        end_date=date(2026, 7, 12),
        comment='Need short break',
    )


@pytest.mark.django_db
class TestLeaveReviewAcceptanceCriteria:
    def test_review_requires_authentication(self, api_client, employee):
        leave = _create_pending_vacation(employee)

        response = api_client.post(_review_url(leave.id), {'status': 'approved'}, format='json')

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_review_allowed_only_for_company_admin(self, api_client, employee):
        leave = _create_pending_vacation(employee)
        _auth(api_client, employee)

        response = api_client.post(_review_url(leave.id), {'status': 'approved'}, format='json')

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_superadmin_cannot_review_leave(self, api_client, superadmin, employee):
        leave = _create_pending_vacation(employee)
        _auth(api_client, superadmin)

        response = api_client.post(_review_url(leave.id), {'status': 'approved'}, format='json')

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_review_non_existing_leave_returns_404(self, api_client, company_admin):
        _auth(api_client, company_admin)
        response = api_client.post(_review_url(999999), {'status': 'approved'}, format='json')
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_cross_company_review_is_forbidden_and_leave_not_changed(
        self, api_client, outsider_admin, employee
    ):
        leave = _create_pending_vacation(employee)
        _auth(api_client, outsider_admin)

        response = api_client.post(_review_url(leave.id), {'status': 'approved'}, format='json')

        assert response.status_code == status.HTTP_404_NOT_FOUND
        leave.refresh_from_db()
        assert leave.status == 'pending'

    def test_approve_vacation_returns_400_when_balance_insufficient(self, api_client, company_admin, employee):
        leave = _create_pending_vacation(employee)
        LeaveBalance.objects.create(user=employee, year=2026, total_days=2, used_days=0)

        _auth(api_client, company_admin)
        response = api_client.post(_review_url(leave.id), {'status': 'approved'}, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        leave.refresh_from_db()
        assert leave.status == 'pending'

    def test_approve_vacation_spends_days_from_leave_balance(self, api_client, company_admin, employee):
        leave = _create_pending_vacation(employee)
        balance = LeaveBalance.objects.create(user=employee, year=2026, total_days=10, used_days=1)

        _auth(api_client, company_admin)
        response = api_client.post(_review_url(leave.id), {'status': 'approved'}, format='json')

        assert response.status_code == status.HTTP_200_OK
        balance.refresh_from_db()
        leave.refresh_from_db()
        assert leave.status == 'approved'
        assert balance.used_days == 4

    def test_review_approved_creates_employee_notification_with_comment(
        self, api_client, company_admin, employee
    ):
        leave = _create_pending_vacation(employee)
        LeaveBalance.objects.create(user=employee, year=2026, total_days=10, used_days=0)

        _auth(api_client, company_admin)
        response = api_client.post(
            _review_url(leave.id),
            {'status': 'approved', 'review_comment': 'Approved, enjoy your vacation'},
            format='json',
        )

        assert response.status_code == status.HTTP_200_OK
        notification = Notification.objects.filter(user=employee).order_by('-created_at').first()
        assert notification is not None
        assert notification.notification_type == 'leave_approved'
        assert 'одобр' in notification.title.lower()
        assert 'Approved, enjoy your vacation' in notification.body

    def test_review_rejected_creates_employee_notification_with_comment(
        self, api_client, company_admin, employee
    ):
        leave = _create_pending_vacation(employee)

        _auth(api_client, company_admin)
        response = api_client.post(
            _review_url(leave.id),
            {'status': 'rejected', 'review_comment': 'Please resubmit with proper dates'},
            format='json',
        )

        assert response.status_code == status.HTTP_200_OK
        notification = Notification.objects.filter(user=employee).order_by('-created_at').first()
        assert notification is not None
        assert notification.notification_type == 'leave_rejected'
        assert 'отклон' in notification.title.lower()
        assert 'Please resubmit with proper dates' in notification.body

    def test_approved_leave_is_visible_as_leave_event_in_team_calendar(
        self, api_client, company_admin, employee
    ):
        leave = _create_pending_vacation(employee)
        LeaveBalance.objects.create(user=employee, year=2026, total_days=20, used_days=0)

        _auth(api_client, company_admin)
        approve_response = api_client.post(_review_url(leave.id), {'status': 'approved'}, format='json')
        assert approve_response.status_code == status.HTTP_200_OK

        calendar_response = api_client.get('/api/v1/calendar/events/')

        assert calendar_response.status_code == status.HTTP_200_OK
        assert isinstance(calendar_response.data, list)
        leave_events = [event for event in calendar_response.data if event.get('event_type') == 'leave']
        assert any(event.get('source_id') == leave.id for event in leave_events)
