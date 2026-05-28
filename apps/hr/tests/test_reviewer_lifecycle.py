"""Reproduce visibility/deletion bugs around assigned_reviewer lifecycle."""
from datetime import date

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.hr.models import LeaveRequest
from apps.users.models import User

LEAVES_URL = '/api/v1/hr/leaves/'


def _auth(client, user):
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='Lifecycle Co', plan='basic')


@pytest.fixture
def admin_a(db, company):
    return User.objects.create_user(
        email='a@lifecycle.test', password='pass', first_name='A', last_name='Admin',
        role='company_admin', company=company,
    )


@pytest.fixture
def admin_b(db, company):
    return User.objects.create_user(
        email='b@lifecycle.test', password='pass', first_name='B', last_name='Admin',
        role='company_admin', company=company,
    )


def _make_pending(author, reviewer):
    return LeaveRequest.objects.create(
        user=author, company=author.company,
        leave_type='remote', status='pending',
        start_date=date(2027, 6, 1), end_date=date(2027, 6, 1),
        assigned_reviewer=reviewer,
    )


@pytest.mark.django_db
class TestReviewerLifecycle:
    def test_leave_visible_after_reviewer_deactivated(self, api_client, admin_a, admin_b):
        leave = _make_pending(author=admin_a, reviewer=admin_b)
        admin_b.is_active = False
        admin_b.save(update_fields=['is_active'])

        _auth(api_client, admin_a)
        response = api_client.get(LEAVES_URL)

        assert response.status_code == status.HTTP_200_OK
        ids = [row['id'] for row in response.json()['results']]
        assert leave.id in ids, 'Leave should remain visible to the author after reviewer is deactivated'

    def test_leave_survives_reviewer_removal(self, api_client, admin_a, admin_b, company):
        leave = _make_pending(author=admin_a, reviewer=admin_b)

        # Simulate what /companies/<id>/members/<uid>/remove/ does on the user.
        admin_b.company = None
        admin_b.is_active = False
        admin_b.role = 'guest'
        admin_b.save(update_fields=['company', 'is_active', 'role'])

        assert LeaveRequest.objects.filter(pk=leave.pk).exists(), 'Leave must not be deleted when reviewer is removed'

        _auth(api_client, admin_a)
        response = api_client.get(LEAVES_URL)
        assert response.status_code == status.HTTP_200_OK
        ids = [row['id'] for row in response.json()['results']]
        assert leave.id in ids

    def test_leave_deleted_when_reviewer_user_deleted(self, admin_a, admin_b):
        """Sanity check: SET_NULL on assigned_reviewer should keep leave alive even if user row is deleted."""
        leave = _make_pending(author=admin_a, reviewer=admin_b)
        admin_b.delete()

        leave.refresh_from_db()
        assert leave.assigned_reviewer_id is None
        assert LeaveRequest.objects.filter(pk=leave.pk).exists()


@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email='su@lifecycle.test', password='pass', first_name='Super', last_name='Admin',
        role='superadmin',
    )


@pytest.mark.django_db
class TestReviewerEndpointSideEffects:
    """Hitting the real /deactivate/ and /remove/ endpoints clears the dangling FK."""

    def test_deactivate_clears_reviewer_when_other_admin_can_step_in(
        self, api_client, admin_a, admin_b, company, superadmin,
    ):
        # admin_c is another active admin who can review admin_a's leave once admin_b is gone.
        admin_c = User.objects.create_user(
            email='c@lifecycle.test', password='pass', first_name='C', last_name='Admin',
            role='company_admin', company=company,
        )
        leave = _make_pending(author=admin_a, reviewer=admin_b)
        _auth(api_client, superadmin)

        response = api_client.post(f'/api/v1/companies/{company.id}/members/{admin_b.id}/deactivate/')

        assert response.status_code == status.HTTP_200_OK
        leave.refresh_from_db()
        assert leave.assigned_reviewer_id is None
        assert leave.status == 'pending'  # admin_c can still review it
        assert admin_c.is_active

    def test_deactivate_cancels_pending_when_no_other_reviewer_left(
        self, api_client, admin_a, admin_b, company, superadmin,
    ):
        # Only admins are admin_a (the author) and admin_b (the reviewer being deactivated),
        # so the leave would dangle forever — must be auto-cancelled.
        leave = _make_pending(author=admin_a, reviewer=admin_b)
        _auth(api_client, superadmin)

        response = api_client.post(f'/api/v1/companies/{company.id}/members/{admin_b.id}/deactivate/')

        assert response.status_code == status.HTTP_200_OK
        leave.refresh_from_db()
        assert leave.assigned_reviewer_id is None
        assert leave.status == 'cancelled'

    def test_deactivate_keeps_assigned_reviewer_on_decided_leaves(
        self, api_client, admin_a, admin_b, company, superadmin,
    ):
        leave = _make_pending(author=admin_a, reviewer=admin_b)
        leave.status = 'approved'
        leave.save(update_fields=['status'])
        _auth(api_client, superadmin)

        api_client.post(f'/api/v1/companies/{company.id}/members/{admin_b.id}/deactivate/')

        leave.refresh_from_db()
        assert leave.assigned_reviewer_id == admin_b.id  # historical link preserved

    def test_remove_clears_assigned_reviewer_and_keeps_leave(
        self, api_client, admin_a, admin_b, company, superadmin,
    ):
        # Add a third admin so the leave isn't auto-cancelled — exercises the
        # "stand-in available" branch on remove.
        User.objects.create_user(
            email='c@lifecycle.test', password='pass', first_name='C', last_name='Admin',
            role='company_admin', company=company,
        )
        leave = _make_pending(author=admin_a, reviewer=admin_b)
        _auth(api_client, superadmin)

        response = api_client.delete(f'/api/v1/companies/{company.id}/members/{admin_b.id}/')

        assert response.status_code == status.HTTP_200_OK, response.content
        leave.refresh_from_db()
        assert leave.assigned_reviewer_id is None
        assert leave.status == 'pending'
        assert LeaveRequest.objects.filter(pk=leave.pk).exists()
