"""Tests for nested company invitations API."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company, Invitation
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
    return Company.objects.create(name='Invite Co', plan='basic', max_employees=20)


@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email='superadmin@test.com',
        password='pass',
        first_name='Super',
        last_name='Admin',
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
        auth(api_client, company_admin)
        response = api_client.post(
            invitations_url(company.id),
            {'email': 'new.admin@example.com', 'role': 'company_admin'},
        api_client.force_authenticate(user=company_admin)
        response = api_client.post(
            _url(company.id),
            {'email': 'ca@example.com', 'role': 'company_admin'},
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
