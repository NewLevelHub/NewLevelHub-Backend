"""Tests for nested company invitations API."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company, Invitation
from apps.notifications.models import Notification
from apps.users.models import User


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='Invite Co', plan='basic', max_employees=20)


@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email='super@test.com',
        password='pass',
        first_name='S',
        last_name='A',
        role='superadmin',
    )


@pytest.fixture
def company_admin(db, company):
    return User.objects.create_user(
        email='admin@invite.co',
        password='pass',
        first_name='Admin',
        last_name='User',
        role='company_admin',
        company=company,
    )


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='emp@invite.co',
        password='pass',
        first_name='Emp',
        last_name='User',
        role='employee',
        company=company,
    )


def _url(company_id):
    return f'/api/v1/companies/{company_id}/invitations/'


def _detail_url(company_id, inv_id):
    return f'/api/v1/companies/{company_id}/invitations/{inv_id}/'


def _revoke_url(company_id, inv_id):
    return f'/api/v1/companies/{company_id}/invitations/{inv_id}/revoke/'


def _resend_url(company_id, inv_id):
    return f'/api/v1/companies/{company_id}/invitations/{inv_id}/resend/'


@pytest.mark.django_db
class TestInvitationCreate:
    @patch('apps.companies.views.send_invitation_email.delay')
    def test_create_sets_expiry_72h(self, _mock_delay, api_client, company_admin, company):
        api_client.force_authenticate(user=company_admin)
        response = api_client.post(
            _url(company.id),
            {'email': 'newuser@example.com', 'role': 'employee'},
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED
        inv = Invitation.objects.get(email='newuser@example.com')
        now = timezone.now()
        assert inv.expires_at > now + timedelta(hours=71)
        assert inv.expires_at < now + timedelta(hours=73)

    @patch('apps.companies.views.send_invitation_email.delay')
    def test_registered_email_returns_400(self, _mock_delay, api_client, company_admin, company, employee):
        api_client.force_authenticate(user=company_admin)
        response = api_client.post(
            _url(company.id),
            {'email': employee.email, 'role': 'employee'},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch('apps.companies.views.send_invitation_email.delay')
    def test_active_invite_returns_400(self, _mock_delay, api_client, company_admin, company):
        api_client.force_authenticate(user=company_admin)
        Invitation.objects.create(
            company=company,
            email='pending@example.com',
            invited_by=company_admin,
            role='employee',
            expires_at=timezone.now() + timedelta(hours=48),
        )
        response = api_client.post(
            _url(company.id),
            {'email': 'pending@example.com', 'role': 'employee'},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_company_admin_cannot_invite_company_admin(self, api_client, company_admin, company):
        api_client.force_authenticate(user=company_admin)
        response = api_client.post(
            _url(company.id),
            {'email': 'ca@example.com', 'role': 'company_admin'},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch('apps.companies.views.send_invitation_email.delay')
    def test_superadmin_can_invite_company_admin(self, _mock_delay, api_client, superadmin, company):
        api_client.force_authenticate(user=superadmin)
        response = api_client.post(
            _url(company.id),
            {'email': 'ca2@example.com', 'role': 'company_admin'},
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED

    def test_employee_forbidden(self, api_client, employee, company):
        api_client.force_authenticate(user=employee)
        response = api_client.post(
            _url(company.id),
            {'email': 'x@example.com', 'role': 'employee'},
            format='json',
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_wrong_company_forbidden(self, api_client, company_admin, company):
        other = Company.objects.create(name='Other', plan='basic')
        api_client.force_authenticate(user=company_admin)
        response = api_client.post(
            _url(other.id),
            {'email': 'x@example.com', 'role': 'employee'},
            format='json',
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    @patch('apps.companies.views.send_invitation_email.delay')
    def test_employee_limit_reached_returns_400(self, _mock_delay, api_client, company_admin, company):
        company.max_employees = 2
        company.save(update_fields=['max_employees'])
        User.objects.create_user(
            email='employee-2@invite.co',
            password='pass',
            first_name='Emp2',
            last_name='User',
            role='employee',
            company=company,
        )

        api_client.force_authenticate(user=company_admin)
        response = api_client.post(
            _url(company.id),
            {'email': 'blocked@example.com', 'role': 'employee'},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data['detail'] == 'Employee limit reached'

    @patch('apps.companies.views.send_invitation_email.delay')
    def test_create_invitation_at_80_percent_creates_admin_notification(
        self, _mock_delay, api_client, company_admin, company
    ):
        company.max_employees = 5
        company.save(update_fields=['max_employees'])
        for idx in range(3):
            User.objects.create_user(
                email=f'emp-{idx}@invite.co',
                password='pass',
                first_name=f'Emp{idx}',
                last_name='User',
                role='employee',
                company=company,
            )

        api_client.force_authenticate(user=company_admin)
        response = api_client.post(
            _url(company.id),
            {'email': 'threshold@example.com', 'role': 'employee'},
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Notification.objects.filter(
            user=company_admin,
            notification_type='announcement_company',
            title='System limit warning',
            body='Employee usage reached 80% (4/5 employees).',
        ).exists()

    @patch('apps.companies.views.send_invitation_email.delay')
    def test_create_invitation_at_95_percent_creates_admin_notification(
        self, _mock_delay, api_client, company_admin, company
    ):
        company.max_employees = 20
        company.save(update_fields=['max_employees'])
        for idx in range(18):
            User.objects.create_user(
                email=f'emp95-{idx}@invite.co',
                password='pass',
                first_name=f'Emp95{idx}',
                last_name='User',
                role='employee',
                company=company,
            )

        api_client.force_authenticate(user=company_admin)
        response = api_client.post(
            _url(company.id),
            {'email': 'threshold95@example.com', 'role': 'employee'},
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Notification.objects.filter(
            user=company_admin,
            notification_type='announcement_company',
            title='System limit warning',
            body='Employee usage reached 95% (19/20 employees).',
        ).exists()


@pytest.mark.django_db
class TestInvitationList:
    def test_filter_is_used(self, api_client, company_admin, company):
        api_client.force_authenticate(user=company_admin)
        Invitation.objects.create(
            company=company,
            email='u1@example.com',
            invited_by=company_admin,
            role='employee',
            expires_at=timezone.now() + timedelta(hours=1),
            is_used=True,
        )
        Invitation.objects.create(
            company=company,
            email='u2@example.com',
            invited_by=company_admin,
            role='employee',
            expires_at=timezone.now() + timedelta(hours=1),
        )
        r = api_client.get(_url(company.id), {'is_used': 'true'})
        assert r.status_code == status.HTTP_200_OK
        emails = {row['email'] for row in r.data['results']}
        assert emails == {'u1@example.com'}

    def test_filter_is_expired(self, api_client, company_admin, company):
        api_client.force_authenticate(user=company_admin)
        Invitation.objects.create(
            company=company,
            email='old@example.com',
            invited_by=company_admin,
            role='employee',
            expires_at=timezone.now() - timedelta(hours=1),
        )
        Invitation.objects.create(
            company=company,
            email='new@example.com',
            invited_by=company_admin,
            role='employee',
            expires_at=timezone.now() + timedelta(hours=1),
        )
        r = api_client.get(_url(company.id), {'is_expired': 'true'})
        assert r.status_code == status.HTTP_200_OK
        emails = {row['email'] for row in r.data['results']}
        assert emails == {'old@example.com'}


@pytest.mark.django_db
class TestInvitationRevokeResend:
    @patch('apps.companies.views.send_invitation_email.delay')
    def test_resend_invalidates_old_token(self, mock_delay, api_client, company_admin, company):
        api_client.force_authenticate(user=company_admin)
        inv = Invitation.objects.create(
            company=company,
            email='r@example.com',
            invited_by=company_admin,
            role='employee',
            expires_at=timezone.now() + timedelta(hours=10),
        )
        old_token = inv.token
        response = api_client.post(_resend_url(company.id, inv.id))
        assert response.status_code == status.HTTP_200_OK
        inv.refresh_from_db()
        assert inv.is_used is True
        assert Invitation.objects.filter(company=company, email='r@example.com').count() == 2
        fresh = Invitation.objects.get(company=company, email='r@example.com', is_used=False)
        assert fresh.token != old_token
        assert mock_delay.called

    @patch('apps.companies.views.send_invitation_email.delay')
    def test_resend_used_fails(self, _mock_delay, api_client, company_admin, company):
        api_client.force_authenticate(user=company_admin)
        inv = Invitation.objects.create(
            company=company,
            email='used@example.com',
            invited_by=company_admin,
            role='employee',
            expires_at=timezone.now() + timedelta(hours=10),
            is_used=True,
        )
        response = api_client.post(_resend_url(company.id, inv.id))
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_revoke_marks_used(self, api_client, company_admin, company):
        api_client.force_authenticate(user=company_admin)
        inv = Invitation.objects.create(
            company=company,
            email='rev@example.com',
            invited_by=company_admin,
            role='employee',
            expires_at=timezone.now() + timedelta(hours=10),
        )
        response = api_client.post(_revoke_url(company.id, inv.id))
        assert response.status_code == status.HTTP_200_OK
        inv.refresh_from_db()
        assert inv.is_used is True
