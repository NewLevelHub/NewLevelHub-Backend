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
    return Company.objects.create(name='Invite Co', plan='basic')


@pytest.fixture
def other_company(db):
    return Company.objects.create(name='Other Co', plan='premium')


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
        first_name='Company',
        last_name='Admin',
        role='company_admin',
        company=company,
    )


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='employee@invite.co',
        password='pass',
        first_name='Normal',
        last_name='Employee',
        role='employee',
        company=company,
    )


def auth(client, user):
    client.force_authenticate(user=user)
    return client


def invitations_url(company_id):
    return f'/api/v1/companies/{company_id}/invitations/'


def invitation_revoke_url(company_id, invitation_id):
    return f'/api/v1/companies/{company_id}/invitations/{invitation_id}/revoke/'


def invitation_resend_url(company_id, invitation_id):
    return f'/api/v1/companies/{company_id}/invitations/{invitation_id}/resend/'


@pytest.mark.django_db
class TestInvitationCreate:
    @patch('apps.companies.serializers.send_invitation_email.delay')
    def test_create_invitation_sets_expiry_and_enqueues_email(
        self, mock_delay, api_client, company_admin, company
    ):
        auth(api_client, company_admin)
        started_at = timezone.now()
        response = api_client.post(
            invitations_url(company.id),
            {'email': 'new.user@example.com', 'role': 'employee'},
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED

        invitation = Invitation.objects.get(company=company, email='new.user@example.com')
        delta_seconds = (invitation.expires_at - started_at).total_seconds()
        assert 71.9 * 3600 <= delta_seconds <= 72.1 * 3600
        mock_delay.assert_called_once_with(invitation.id)

    def test_cannot_invite_registered_email(self, api_client, company_admin, company, employee):
        auth(api_client, company_admin)
        response = api_client.post(
            invitations_url(company.id),
            {'email': employee.email, 'role': 'employee'},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_cannot_invite_when_active_invite_exists(self, api_client, company_admin, company):
        Invitation.objects.create(
            company=company,
            email='taken@example.com',
            invited_by=company_admin,
            role='employee',
            expires_at=timezone.now() + timedelta(hours=72),
        )
        auth(api_client, company_admin)
        response = api_client.post(
            invitations_url(company.id),
            {'email': 'taken@example.com', 'role': 'employee'},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_company_admin_cannot_invite_company_admin(self, api_client, company_admin, company):
        auth(api_client, company_admin)
        response = api_client.post(
            invitations_url(company.id),
            {'email': 'new.admin@example.com', 'role': 'company_admin'},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch('apps.companies.serializers.send_invitation_email.delay')
    def test_superadmin_can_invite_company_admin(self, mock_delay, api_client, superadmin, company):
        auth(api_client, superadmin)
        response = api_client.post(
            invitations_url(company.id),
            {'email': 'new.admin@example.com', 'role': 'company_admin'},
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED
        invitation = Invitation.objects.get(company=company, email='new.admin@example.com')
        assert invitation.role == 'company_admin'
        mock_delay.assert_called_once_with(invitation.id)

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
            invitations_url(company.id),
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
            invitations_url(company.id),
            {'email': 'threshold@example.com', 'role': 'employee'},
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Notification.objects.filter(
            user=company_admin,
            notification_type='announcement_company',
            title='System limit warning: 80%',
            body='Your employee usage has reached 80% (4/5 employees). Please free up space or upgrade your plan.',
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
            invitations_url(company.id),
            {'email': 'threshold95@example.com', 'role': 'employee'},
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Notification.objects.filter(
            user=company_admin,
            notification_type='announcement_company',
            title='System limit warning: 95%',
            body='Your employee usage has reached 95% (19/20 employees). Please free up space or upgrade your plan.',
        ).exists()


@pytest.mark.django_db
class TestInvitationListAndActions:
    def test_list_filters_by_is_used_and_is_expired(self, api_client, company_admin, company):
        active_inv = Invitation.objects.create(
            company=company,
            email='active@example.com',
            invited_by=company_admin,
            role='employee',
            expires_at=timezone.now() + timedelta(hours=12),
        )
        Invitation.objects.create(
            company=company,
            email='expired@example.com',
            invited_by=company_admin,
            role='employee',
            expires_at=timezone.now() - timedelta(hours=1),
        )
        used_inv = Invitation.objects.create(
            company=company,
            email='used@example.com',
            invited_by=company_admin,
            role='employee',
            is_used=True,
            expires_at=timezone.now() + timedelta(hours=12),
        )
        auth(api_client, company_admin)

        used_response = api_client.get(invitations_url(company.id), {'is_used': 'true'})
        assert used_response.status_code == status.HTTP_200_OK
        used_ids = {row['id'] for row in used_response.data['results']}
        assert used_ids == {used_inv.id}

        expired_response = api_client.get(invitations_url(company.id), {'is_expired': 'true'})
        assert expired_response.status_code == status.HTTP_200_OK
        expired_ids = {row['id'] for row in expired_response.data['results']}
        assert active_inv.id not in expired_ids

    def test_revoke_marks_invitation_used(self, api_client, company_admin, company):
        invitation = Invitation.objects.create(
            company=company,
            email='revokable@example.com',
            invited_by=company_admin,
            role='employee',
            expires_at=timezone.now() + timedelta(hours=72),
        )
        auth(api_client, company_admin)
        response = api_client.post(invitation_revoke_url(company.id, invitation.id), format='json')
        assert response.status_code == status.HTTP_200_OK
        invitation.refresh_from_db()
        assert invitation.is_used is True

    @patch('apps.companies.views.send_invitation_email.delay')
    def test_resend_invalidates_old_and_creates_new(self, mock_delay, api_client, company_admin, company):
        invitation = Invitation.objects.create(
            company=company,
            email='resend@example.com',
            invited_by=company_admin,
            role='employee',
            expires_at=timezone.now() + timedelta(hours=72),
        )
        auth(api_client, company_admin)
        response = api_client.post(invitation_resend_url(company.id, invitation.id), format='json')
        assert response.status_code == status.HTTP_200_OK

        invitation.refresh_from_db()
        assert invitation.is_used is True

        new_invitation = Invitation.objects.exclude(id=invitation.id).get(email='resend@example.com')
        assert new_invitation.company_id == company.id
        assert new_invitation.is_used is False
        assert str(new_invitation.token) != str(invitation.token)
        mock_delay.assert_called_once_with(new_invitation.id)

    def test_company_admin_cannot_access_other_company_invitations(
        self, api_client, company_admin, other_company
    ):
        auth(api_client, company_admin)
        response = api_client.get(invitations_url(other_company.id))
        assert response.status_code == status.HTTP_404_NOT_FOUND
