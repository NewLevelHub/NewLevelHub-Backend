from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company, Invitation
from apps.users.models import EmailVerificationToken, User


INVITE_REGISTER_URL = '/api/v1/auth/register/invite/'


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='Invite Target Co', plan='basic', max_employees=10)


@pytest.fixture
def inviter(db, company):
    return User.objects.create_user(
        email='inviter@example.com',
        password='StrongPass123!',
        first_name='Invite',
        last_name='Owner',
        role='company_admin',
        company=company,
    )


@pytest.fixture
def invitation(db, company, inviter):
    return Invitation.objects.create(
        company=company,
        email='new.employee@example.com',
        invited_by=inviter,
        role='employee',
        expires_at=timezone.now() + timedelta(hours=72),
    )


@pytest.mark.django_db
class TestInviteRegistration:
    def test_get_returns_invite_data(self, api_client, invitation):
        response = api_client.get(INVITE_REGISTER_URL, {'token': str(invitation.token)})

        assert response.status_code == status.HTTP_200_OK
        assert response.data['company_name'] == invitation.company.name
        assert response.data['email'] == invitation.email
        assert response.data['role'] == invitation.role

    def test_get_returns_400_for_invalid_token(self, api_client):
        response = api_client.get(INVITE_REGISTER_URL, {'token': '7af494e5-f7aa-4a9d-b6f4-34af5718d6aa'})
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch('apps.users.views.send_verification_email.delay')
    def test_post_registers_user_and_marks_invitation_used(self, mock_send_email, api_client, invitation):
        response = api_client.post(
            INVITE_REGISTER_URL,
            {
                'token': str(invitation.token),
                'first_name': 'New',
                'last_name': 'Employee',
                'password': 'StrongPass123!',
                'phone': '+77001234567',
            },
            format='json',
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert 'tokens' in response.data
        user = User.objects.get(email='new.employee@example.com')
        assert user.company_id == invitation.company_id
        assert user.role == invitation.role
        assert user.first_name == 'New'
        assert user.phone == '+77001234567'

        invitation.refresh_from_db()
        assert invitation.is_used is True
        assert invitation.used_at is not None

        verification_token = EmailVerificationToken.objects.get(user=user)
        mock_send_email.assert_called_once_with(user.id, str(verification_token.token))

    def test_post_returns_400_for_expired_invitation(self, api_client, invitation):
        invitation.expires_at = timezone.now() - timedelta(minutes=1)
        invitation.save(update_fields=['expires_at'])

        response = api_client.post(
            INVITE_REGISTER_URL,
            {
                'token': str(invitation.token),
                'first_name': 'New',
                'last_name': 'Employee',
                'password': 'StrongPass123!',
            },
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_post_returns_400_for_already_used_invitation(self, api_client, invitation):
        invitation.is_used = True
        invitation.used_at = timezone.now()
        invitation.save(update_fields=['is_used', 'used_at'])

        response = api_client.post(
            INVITE_REGISTER_URL,
            {
                'token': str(invitation.token),
                'first_name': 'New',
                'last_name': 'Employee',
                'password': 'StrongPass123!',
            },
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_post_returns_400_when_email_already_registered(self, api_client, invitation):
        User.objects.create_user(
            email=invitation.email,
            password='StrongPass123!',
            first_name='Existing',
            last_name='User',
        )
        response = api_client.post(
            INVITE_REGISTER_URL,
            {
                'token': str(invitation.token),
                'first_name': 'New',
                'last_name': 'Employee',
                'password': 'StrongPass123!',
            },
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_post_returns_400_when_employee_limit_reached(self, api_client, company, inviter):
        company.max_employees = 1
        company.save(update_fields=['max_employees'])
        User.objects.create_user(
            email='current.employee@example.com',
            password='StrongPass123!',
            first_name='Current',
            last_name='Employee',
            role='employee',
            company=company,
        )
        invitation = Invitation.objects.create(
            company=company,
            email='limit.user@example.com',
            invited_by=inviter,
            role='employee',
            expires_at=timezone.now() + timedelta(hours=72),
        )

        response = api_client.post(
            INVITE_REGISTER_URL,
            {
                'token': str(invitation.token),
                'first_name': 'Limit',
                'last_name': 'User',
                'password': 'StrongPass123!',
            },
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data['detail'] == 'Employee limit reached'
