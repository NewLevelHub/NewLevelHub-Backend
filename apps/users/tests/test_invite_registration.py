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
        is_email_verified=True,
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
        assert response.data['success'] is False
        assert 'email' in response.data['error']['details']

    @patch('apps.users.views.send_verification_email.delay')
    def test_post_register_by_invite_creates_user_marks_invite_used_and_sends_verification(
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
        assert 'tokens' not in response.data
        assert 'detail' in response.data

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
        assert 'tokens' not in response.data
        user = User.objects.get(email=invitation.email)
        assert user.company_id == invitation.company_id
        assert user.is_active is True
        assert user.is_email_verified is False
        assert user.check_password('StrongPass123!')
        invitation.refresh_from_db()
        assert invitation.is_used is True
        mock_send_email.assert_called_once()

    def test_post_register_by_invite_used_token_returns_400(self, api_client, invitation):
        invitation.status = Invitation.STATUS_ACCEPTED
        invitation.used_at = timezone.now()
        invitation.save(update_fields=['status', 'used_at', 'updated_at'])

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
        assert response.data['success'] is False
        assert 'email' in response.data['error']['details']

    def test_post_register_by_invite_fails_if_active_employee_email_normalized_match(
        self, api_client, company, inviter
    ):
        """Normalized-email collision with an active non-guest blocks the invite."""
        invitation = Invitation.objects.create(
            company=company,
            email='new.user@EXAMPLE.COM',
            invited_by=inviter,
            role='employee',
            expires_at=timezone.now() + timedelta(hours=72),
        )
        other_company = Company.objects.create(name='Other Co', plan='basic')
        User.objects.create_user(
            email='new.user@example.com',
            password='StrongPass123!',
            first_name='Existing',
            last_name='User',
            role='employee',
            company=other_company,
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
        assert response.data['success'] is False
        assert 'email' in response.data['error']['details']

    def test_get_invite_succeeds_and_signals_guest_upgrade(self, api_client, invitation):
        User.objects.create_user(
            email=invitation.email,
            password='StrongPass123!',
            first_name='Guest',
            last_name='User',
            role='guest',
            company=None,
            is_active=True,
        )
        response = api_client.get(REGISTER_INVITE_URL, {'token': str(invitation.token)})
        assert response.status_code == status.HTTP_200_OK
        assert response.data['is_guest_upgrade'] is True

    @patch('apps.users.views.send_verification_email.delay')
    def test_post_register_by_invite_upgrades_guest_account(
        self, mock_send_email, api_client, invitation
    ):
        User.objects.create_user(
            email=invitation.email,
            password='OldPass123!',
            first_name='Was',
            last_name='Guest',
            role='guest',
            company=None,
            is_active=True,
        )
        response = api_client.post(
            REGISTER_INVITE_URL,
            {
                'token': str(invitation.token),
                'first_name': 'Now',
                'last_name': 'Employee',
                'password': 'StrongPass123!',
            },
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED
        user = User.objects.get(email=invitation.email)
        assert user.company_id == invitation.company_id
        assert user.role == invitation.role
        assert user.is_active is True
        assert user.is_email_verified is False
        assert user.check_password('StrongPass123!')
        invitation.refresh_from_db()
        assert invitation.is_used is True
        mock_send_email.assert_called_once()

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
        msg = response.data['error']['details']['non_field_errors'][0]
        assert msg == 'Достигнут лимит сотрудников для вашего тарифа.'

    @patch('apps.users.views.send_verification_email.delay')
    @patch('apps.users.tasks.notify_new_employee.delay')
    @patch('apps.users.serializers.transaction.on_commit', side_effect=lambda fn: fn())
    def test_post_register_by_invite_notifies_company_admins(
        self, _mock_on_commit, mock_notify, mock_send_email, api_client, invitation, inviter
    ):
        """Successful invite registration triggers new_employee notification for admins."""
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

        assert response.status_code == status.HTTP_201_CREATED
        new_user = User.objects.get(email=invitation.email)
        mock_notify.assert_called_once_with(new_user.pk)

    @patch('apps.users.views.send_verification_email.delay')
    @patch('apps.users.tasks.notify_new_employee.delay')
    @patch('apps.users.serializers.transaction.on_commit', side_effect=lambda fn: fn())
    def test_notify_new_employee_task_creates_notifications_for_admins(
        self, _mock_on_commit, _mock_notify_delay, mock_send_email, db, company, inviter
    ):
        """notify_new_employee task creates in-app notifications for all company admins."""
        from apps.notifications.models import Notification
        from apps.users.tasks import notify_new_employee

        second_admin = User.objects.create_user(
            email='admin2@invite.co',
            password='StrongPass123!',
            first_name='Admin',
            last_name='Two',
            role='company_admin',
            company=company,
        )
        new_user = User.objects.create_user(
            email='newbie@invite.co',
            password='StrongPass123!',
            first_name='New',
            last_name='Hire',
            role='employee',
            company=company,
        )

        notify_new_employee(new_user.pk)

        admin_ids = {inviter.pk, second_admin.pk}
        notified_ids = set(
            Notification.objects.filter(
                notification_type='new_employee',
                user_id__in=admin_ids,
            ).values_list('user_id', flat=True)
        )
        assert notified_ids == admin_ids

    @patch('apps.notifications.tasks.send_notification_email.delay')
    def test_notify_new_employee_task_sends_email_to_admins(
        self, mock_send_email, db, company, inviter
    ):
        """notify_new_employee task sends email notification to all company admins."""
        from apps.users.tasks import notify_new_employee

        second_admin = User.objects.create_user(
            email='admin2@invite.co',
            password='StrongPass123!',
            first_name='Admin',
            last_name='Two',
            role='company_admin',
            company=company,
            is_email_verified=True,
        )
        unverified_admin = User.objects.create_user(
            email='admin3@invite.co',
            password='StrongPass123!',
            first_name='Admin',
            last_name='Three',
            role='company_admin',
            company=company,
            is_email_verified=False,
        )
        new_user = User.objects.create_user(
            email='newbie@invite.co',
            password='StrongPass123!',
            first_name='New',
            last_name='Hire',
            role='employee',
            company=company,
        )

        notify_new_employee(new_user.pk)

        # Only verified admins receive email; unverified admin is skipped.
        assert mock_send_email.call_count == 2
        called_admin_ids = {call.args[0] for call in mock_send_email.call_args_list}
        assert called_admin_ids == {inviter.pk, second_admin.pk}
        assert unverified_admin.pk not in called_admin_ids

        for call in mock_send_email.call_args_list:
            assert call.args[1] == 'new_employee'
            ctx = call.args[2]
            assert ctx['employee_name'] == new_user.full_name
            assert ctx['employee_email'] == new_user.email
