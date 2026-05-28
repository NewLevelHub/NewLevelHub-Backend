"""AC tests for assigned-reviewer flow on leave requests (DEV-63 follow-up).

Covers:
  - company_admin must select another active company_admin as the reviewer.
  - Author cannot be selected as their own reviewer.
  - Employees as reviewer / outside-company users are rejected.
  - Lone company_admin (no peer) cannot submit a leave request at all.
  - Author cannot approve their own request (even non-company_admin authors).
  - Only the explicitly assigned reviewer can approve when one is set.
  - When an assigned_reviewer is present, only that admin gets a notification.
"""
from datetime import date

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.hr.models import LeaveRequest
from apps.notifications.models import Notification
from apps.users.models import User


LEAVES_URL = '/api/v1/hr/leaves/'


def _review_url(leave_id):
    return f'/api/v1/hr/leaves/{leave_id}/review/'


def _auth(client, user):
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='Reviewer AC Co', plan='basic')


@pytest.fixture
def admin_a(db, company):
    return User.objects.create_user(
        email='admin-a@reviewer-ac.test', password='pass',
        first_name='Admin', last_name='A',
        role='company_admin', company=company,
    )


@pytest.fixture
def admin_b(db, company):
    return User.objects.create_user(
        email='admin-b@reviewer-ac.test', password='pass',
        first_name='Admin', last_name='B',
        role='company_admin', company=company,
    )


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='employee@reviewer-ac.test', password='pass',
        first_name='Reg', last_name='Employee',
        role='employee', company=company,
    )


@pytest.fixture
def other_company(db):
    return Company.objects.create(name='Other Co', plan='basic')


@pytest.fixture
def outsider_admin(db, other_company):
    return User.objects.create_user(
        email='outsider@reviewer-ac.test', password='pass',
        first_name='Out', last_name='Sider',
        role='company_admin', company=other_company,
    )


def _details(response):
    """Extract validation-error details regardless of the standard/wrapped shape."""
    body = response.json()
    if isinstance(body, dict) and 'error' in body and isinstance(body['error'], dict):
        return body['error'].get('details', {}) or {}
    return body


def _payload(assigned_reviewer=None, leave_type='remote'):
    """Use leave_type=remote so we don't fight LeaveBalance defaults in tests."""
    body = {
        'leave_type': leave_type,
        'start_date': str(date(2027, 6, 1)),
        'end_date': str(date(2027, 6, 1)),
        'comment': 'Reviewer AC',
    }
    if assigned_reviewer is not None:
        body['assigned_reviewer'] = assigned_reviewer
    return body


@pytest.mark.django_db
class TestAssignedReviewerCreate:
    def test_company_admin_without_reviewer_returns_400(self, api_client, admin_a, admin_b):
        _auth(api_client, admin_a)

        response = api_client.post(LEAVES_URL, _payload(), format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'assigned_reviewer' in _details(response)

    def test_company_admin_cannot_pick_self_as_reviewer(self, api_client, admin_a, admin_b):
        _auth(api_client, admin_a)

        response = api_client.post(LEAVES_URL, _payload(assigned_reviewer=admin_a.id), format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'assigned_reviewer' in _details(response)

    def test_company_admin_cannot_pick_employee_as_reviewer(self, api_client, admin_a, admin_b, employee):
        _auth(api_client, admin_a)

        response = api_client.post(LEAVES_URL, _payload(assigned_reviewer=employee.id), format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'assigned_reviewer' in _details(response)

    def test_company_admin_cannot_pick_admin_from_other_company(
        self, api_client, admin_a, admin_b, outsider_admin,
    ):
        _auth(api_client, admin_a)

        response = api_client.post(
            LEAVES_URL, _payload(assigned_reviewer=outsider_admin.id), format='json',
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'assigned_reviewer' in _details(response)

    def test_company_admin_cannot_pick_inactive_admin_as_reviewer(
        self, api_client, admin_a, admin_b,
    ):
        admin_b.is_active = False
        admin_b.save(update_fields=['is_active'])
        _auth(api_client, admin_a)

        response = api_client.post(
            LEAVES_URL, _payload(assigned_reviewer=admin_b.id), format='json',
        )

        # Inactive admin acts like "no peer exists" — we surface no-other-admin first.
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        details = _details(response)
        assert 'assigned_reviewer' in details or 'non_field_errors' in details

    def test_company_admin_with_valid_peer_succeeds(self, api_client, admin_a, admin_b):
        _auth(api_client, admin_a)

        response = api_client.post(
            LEAVES_URL, _payload(assigned_reviewer=admin_b.id), format='json',
        )

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        data = response.json()
        assert data['assigned_reviewer'] == admin_b.id
        assert data['assigned_reviewer_name']

    def test_lone_company_admin_cannot_submit(self, api_client, admin_a):
        # admin_b is intentionally not in the fixtures here — only admin_a exists.
        _auth(api_client, admin_a)

        response = api_client.post(LEAVES_URL, _payload(), format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        details = _details(response)
        assert 'non_field_errors' in details or 'assigned_reviewer' in details

    def test_employee_can_submit_without_reviewer(self, api_client, admin_a, admin_b, employee):
        _auth(api_client, employee)

        response = api_client.post(LEAVES_URL, _payload(), format='json')

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        assert response.json()['assigned_reviewer'] is None


@pytest.mark.django_db
class TestAssignedReviewerReview:
    def _create_with_reviewer(self, author, reviewer):
        return LeaveRequest.objects.create(
            user=author, company=author.company,
            leave_type='remote', status='pending',
            start_date=date(2027, 6, 1), end_date=date(2027, 6, 1),
            assigned_reviewer=reviewer,
        )

    def test_author_cannot_approve_own_request(self, api_client, admin_a, admin_b):
        leave = self._create_with_reviewer(author=admin_a, reviewer=admin_b)
        _auth(api_client, admin_a)

        response = api_client.post(_review_url(leave.id), {'status': 'approved'}, format='json')

        assert response.status_code == status.HTTP_403_FORBIDDEN
        leave.refresh_from_db()
        assert leave.status == 'pending'

    def test_non_assigned_admin_cannot_review(self, api_client, admin_a, admin_b, company):
        admin_c = User.objects.create_user(
            email='admin-c@reviewer-ac.test', password='pass',
            first_name='Admin', last_name='C',
            role='company_admin', company=company,
        )
        leave = self._create_with_reviewer(author=admin_a, reviewer=admin_b)
        _auth(api_client, admin_c)

        response = api_client.post(_review_url(leave.id), {'status': 'approved'}, format='json')

        assert response.status_code == status.HTTP_403_FORBIDDEN
        leave.refresh_from_db()
        assert leave.status == 'pending'

    def test_assigned_admin_can_approve(self, api_client, admin_a, admin_b):
        leave = self._create_with_reviewer(author=admin_a, reviewer=admin_b)
        _auth(api_client, admin_b)

        response = api_client.post(_review_url(leave.id), {'status': 'approved'}, format='json')

        assert response.status_code == status.HTTP_200_OK, response.json()
        leave.refresh_from_db()
        assert leave.status == 'approved'
        assert leave.reviewed_by_id == admin_b.id


@pytest.mark.django_db
class TestAssignedReviewerNotifications:
    def test_only_assigned_admin_is_notified_on_create(self, api_client, admin_a, admin_b, company):
        admin_c = User.objects.create_user(
            email='admin-c@reviewer-ac.test', password='pass',
            first_name='Admin', last_name='C',
            role='company_admin', company=company,
        )
        Notification.objects.all().delete()
        _auth(api_client, admin_a)

        response = api_client.post(
            LEAVES_URL, _payload(assigned_reviewer=admin_b.id), format='json',
        )

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        recipients = set(Notification.objects.values_list('user_id', flat=True))
        assert admin_b.id in recipients
        assert admin_c.id not in recipients
        assert admin_a.id not in recipients
