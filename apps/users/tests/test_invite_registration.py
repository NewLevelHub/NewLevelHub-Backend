from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company, Invitation
from apps.users.models import User

REGISTER_INVITE_URL = '/api/v1/auth/register/invite/'


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='Invite Co', plan='basic', max_employees=10)


@pytest.fixture
def inviter(db, company):
    return User.objects.create_user(
        email='admin@invite.co',
        password='StrongPass123!',
        first_name='Admin',
        last_name='User',
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
    def test_get_invite_returns_payload(self, api_client, invitation):
        response = api_client.get(REGISTER_INVITE_URL, {'token': str(invitation.token)})

        assert response.status_code == status.HTTP_200_OK
        assert response.data['company_name'] == invitation.company.name
        assert response.data['email'] == invitation.email
        assert response.data['role'] == invitation.role

    def test_get_invite_invalid_or_expired_returns_400(self, api_client, invitation):
        response_missing = api_client.get(REGISTER_INVITE_URL)
        assert response_missing.status_code == status.HTTP_400_BAD_REQUEST

        invitation.expires_at = timezone.now() - timedelta(minutes=1)
        invitation.save(update_fields=['expires_at', 'updated_at'])
        response_expired = api_client.get(REGISTER_INVITE_URL, {'token': str(invitation.token)})
        assert response_expired.status_code == status.HTTP_400_BAD_REQUEST

    def test_get_invite_fails_if_email_already_active_member(self, api_client, invitation):
        User.objects.create_user(
            email=invitation.email,
            password='StrongPass123!',
            first_name='Existing',
            last_name='User',
            company=invitation.company,
            role='employee',
        )
        response = api_client.get(REGISTER_INVITE_URL, {'token': str(invitation.token)})
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data['error'] is True
        assert 'email' in response.data['detail']

    @patch('apps.users.views.send_verification_email.delay')
    def test_post_register_by_invite_creates_user_marks_invite_used_and_returns_tokens(
        self, mock_send_email, api_client, invitation
    ):
        payload = {
            'token': str(invitation.token),
            'first_name': 'New',
            'last_name': 'Employee',
            'password': 'StrongPass123!',
            'phone': '+77001112233',
        }
        response = api_client.post(REGISTER_INVITE_URL, payload, format='json')

        assert response.status_code == status.HTTP_201_CREATED
        assert 'tokens' in response.data
        assert response.data['user']['email'] == invitation.email
        assert response.data['user']['company']['id'] == invitation.company_id
        assert response.data['user']['role'] == invitation.role

        user = User.objects.get(email=invitation.email)
        assert user.company_id == invitation.company_id
        assert user.role == invitation.role
        assert user.is_email_verified is False

        invitation.refresh_from_db()
        assert invitation.is_used is True
        assert invitation.used_at is not None
        mock_send_email.assert_called_once()

    @patch('apps.users.views.send_verification_email.delay')
    def test_post_register_by_invite_rejoins_removed_user(
        self, mock_send_email, api_client, invitation
    ):
        User.objects.create_user(
            email=invitation.email,
            password='OldPass123!',
            first_name='Was',
            last_name='Member',
            role='guest',
            company=None,
            is_active=False,
        )
        response = api_client.post(
            REGISTER_INVITE_URL,
            {
                'token': str(invitation.token),
                'first_name': 'Back',
                'last_name': 'Again',
                'password': 'StrongPass123!',
            },
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['user']['company']['id'] == invitation.company_id
        user = User.objects.get(email=invitation.email)
        assert user.company_id == invitation.company_id
        assert user.is_active is True
        assert user.is_email_verified is False
        assert user.check_password('StrongPass123!')
        invitation.refresh_from_db()
        assert invitation.is_used is True
        mock_send_email.assert_called_once()

    def test_post_register_by_invite_used_token_returns_400(self, api_client, invitation):
        invitation.is_used = True
        invitation.used_at = timezone.now()
        invitation.save(update_fields=['is_used', 'used_at', 'updated_at'])

        response = api_client.post(
            REGISTER_INVITE_URL,
            {
                'token': str(invitation.token),
                'first_name': 'Used',
                'last_name': 'Token',
                'password': 'StrongPass123!',
            },
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_post_register_by_invite_fails_if_email_already_registered(self, api_client, invitation):
        User.objects.create_user(
            email=invitation.email,
            password='StrongPass123!',
            first_name='Existing',
            last_name='User',
            company=invitation.company,
            role='employee',
        )
        response = api_client.post(
            REGISTER_INVITE_URL,
            {
                'token': str(invitation.token),
                'first_name': 'New',
                'last_name': 'Employee',
                'password': 'StrongPass123!',
            },
            format='json',
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data['error'] is True
        assert 'email' in response.data['detail']

    def test_post_register_by_invite_fails_if_email_normalized_match(self, api_client, company, inviter):
        invitation = Invitation.objects.create(
            company=company,
            email='new.user@EXAMPLE.COM',
            invited_by=inviter,
            role='employee',
            expires_at=timezone.now() + timedelta(hours=72),
        )
        User.objects.create_user(
            email='new.user@example.com',
            password='StrongPass123!',
            first_name='Existing',
            last_name='User',
        )
        response = api_client.post(
            REGISTER_INVITE_URL,
            {
                'token': str(invitation.token),
                'first_name': 'New',
                'last_name': 'Employee',
                'password': 'StrongPass123!',
            },
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data['error'] is True
        assert 'email' in response.data['detail']

    def test_post_register_by_invite_fails_if_employee_limit_reached(self, api_client, invitation):
        company = invitation.company
        company.max_employees = 1
        company.save(update_fields=['max_employees', 'updated_at'])
        User.objects.create_user(
            email='existing@invite.co',
            password='StrongPass123!',
            first_name='Existing',
            last_name='Employee',
            role='employee',
            company=company,
        )

        response = api_client.post(
            REGISTER_INVITE_URL,
            {
                'token': str(invitation.token),
                'first_name': 'Limit',
                'last_name': 'Reached',
                'password': 'StrongPass123!',
            },
            format='json',
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'Employee limit reached' in str(response.data)
