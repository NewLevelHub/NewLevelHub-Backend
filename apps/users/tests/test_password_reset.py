"""
Tests for password reset flow:
  POST /api/v1/auth/password/reset/
  POST /api/v1/auth/password/reset/confirm/
"""
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.users.models import PasswordResetToken, User


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email=f'test-{uuid.uuid4().hex[:8]}@example.com',
        password='OldPass123!',
        first_name='Test',
        last_name='User',
    )


@pytest.fixture
def inactive_user(db):
    return User.objects.create_user(
        email=f'inactive-{uuid.uuid4().hex[:8]}@example.com',
        password='OldPass123!',
        first_name='Inactive',
        last_name='User',
        is_active=False,
    )


@pytest.fixture
def reset_token(user):
    return PasswordResetToken.objects.create(
        user=user,
        expires_at=timezone.now() + timedelta(hours=1),
    )


@pytest.fixture
def expired_token(user):
    return PasswordResetToken.objects.create(
        user=user,
        expires_at=timezone.now() - timedelta(seconds=1),
    )


@pytest.fixture
def used_token(user):
    return PasswordResetToken.objects.create(
        user=user,
        expires_at=timezone.now() + timedelta(hours=1),
        is_used=True,
    )


REQUEST_URL = '/api/v1/auth/password/reset/'
CONFIRM_URL = '/api/v1/auth/password/reset/confirm/'


# ── password_reset_request ──────────────────────────────────────────────────

@pytest.mark.django_db
class TestPasswordResetRequest:

    def setup_method(self):
        cache.clear()

    @patch('apps.users.tasks.send_password_reset_email')
    def test_existing_email_returns_200_and_queues_task(self, mock_task, api_client, user):
        response = api_client.post(REQUEST_URL, {'email': user.email}, format='json')

        assert response.status_code == status.HTTP_200_OK
        assert 'detail' in response.data
        mock_task.delay.assert_called_once()

    @patch('apps.users.tasks.send_password_reset_email')
    def test_task_called_with_correct_token_id(self, mock_task, api_client, user):
        api_client.post(REQUEST_URL, {'email': user.email}, format='json')

        created_token = PasswordResetToken.objects.get(user=user)
        mock_task.delay.assert_called_once_with(created_token.id)

    @patch('apps.users.tasks.send_password_reset_email')
    def test_nonexistent_email_returns_200_no_task(self, mock_task, api_client, db):
        response = api_client.post(REQUEST_URL, {'email': 'nobody@example.com'}, format='json')

        assert response.status_code == status.HTTP_200_OK
        mock_task.delay.assert_not_called()

    @patch('apps.users.tasks.send_password_reset_email')
    def test_inactive_user_returns_200_no_task_no_token(self, mock_task, api_client, inactive_user):
        response = api_client.post(REQUEST_URL, {'email': inactive_user.email}, format='json')

        assert response.status_code == status.HTTP_200_OK
        mock_task.delay.assert_not_called()
        assert not PasswordResetToken.objects.filter(user=inactive_user).exists()

    @patch('apps.users.tasks.send_password_reset_email')
    def test_creates_reset_token_with_1h_expiry(self, mock_task, api_client, user):
        before = timezone.now()
        api_client.post(REQUEST_URL, {'email': user.email}, format='json')
        after = timezone.now()

        token = PasswordResetToken.objects.filter(user=user).latest('created_at')
        assert before + timedelta(minutes=59) < token.expires_at <= after + timedelta(hours=1, seconds=5)

    def test_invalid_email_format_returns_400(self, api_client, db):
        response = api_client.post(REQUEST_URL, {'email': 'not-an-email'}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch('apps.users.tasks.send_password_reset_email')
    def test_rate_limit_429_after_5_requests(self, mock_task, api_client, user):
        cache.clear()
        for _ in range(5):
            api_client.post(REQUEST_URL, {'email': user.email}, format='json')

        response = api_client.post(REQUEST_URL, {'email': user.email}, format='json')
        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS


# ── password_reset_confirm ──────────────────────────────────────────────────

@pytest.mark.django_db
class TestPasswordResetConfirm:

    def test_valid_token_resets_password(self, api_client, user, reset_token):
        response = api_client.post(CONFIRM_URL, {
            'token': str(reset_token.token),
            'new_password': 'NewPass456!',
        }, format='json')

        assert response.status_code == status.HTTP_200_OK
        user.refresh_from_db()
        assert user.check_password('NewPass456!')

    def test_valid_token_marks_as_used(self, api_client, user, reset_token):
        api_client.post(CONFIRM_URL, {
            'token': str(reset_token.token),
            'new_password': 'NewPass456!',
        }, format='json')

        reset_token.refresh_from_db()
        assert reset_token.is_used is True

    def test_token_cannot_be_used_twice(self, api_client, user, reset_token):
        payload = {'token': str(reset_token.token), 'new_password': 'NewPass456!'}
        first = api_client.post(CONFIRM_URL, payload, format='json')
        second = api_client.post(CONFIRM_URL, payload, format='json')

        assert first.status_code == status.HTTP_200_OK
        assert second.status_code == status.HTTP_400_BAD_REQUEST
        assert second.data['error']['code'] == 'TOKEN_ALREADY_USED'

    def test_expired_token_returns_400(self, api_client, expired_token):
        response = api_client.post(CONFIRM_URL, {
            'token': str(expired_token.token),
            'new_password': 'NewPass456!',
        }, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data['error']['code'] == 'TOKEN_EXPIRED'

    def test_used_token_returns_400(self, api_client, used_token):
        response = api_client.post(CONFIRM_URL, {
            'token': str(used_token.token),
            'new_password': 'NewPass456!',
        }, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data['error']['code'] == 'TOKEN_ALREADY_USED'

    def test_nonexistent_token_returns_400(self, api_client, db):
        response = api_client.post(CONFIRM_URL, {
            'token': str(uuid.uuid4()),
            'new_password': 'NewPass456!',
        }, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data['error']['code'] == 'TOKEN_INVALID'

    def test_inactive_user_token_returns_400(self, api_client, inactive_user):
        token = PasswordResetToken.objects.create(
            user=inactive_user,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        response = api_client.post(CONFIRM_URL, {
            'token': str(token.token),
            'new_password': 'NewPass456!',
        }, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data['error']['code'] == 'TOKEN_INVALID'

    def test_short_password_returns_400(self, api_client, reset_token):
        response = api_client.post(CONFIRM_URL, {
            'token': str(reset_token.token),
            'new_password': 'short',
        }, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_numeric_only_password_fails_django_validator(self, api_client, reset_token):
        response = api_client.post(CONFIRM_URL, {
            'token': str(reset_token.token),
            'new_password': '12345678',
        }, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_refresh_tokens_blacklisted_after_reset(self, api_client, user, reset_token):
        from rest_framework_simplejwt.tokens import RefreshToken as JWTRefreshToken
        from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken

        refresh = JWTRefreshToken.for_user(user)
        outstanding_jti = str(refresh['jti'])

        api_client.post(CONFIRM_URL, {
            'token': str(reset_token.token),
            'new_password': 'NewPass456!',
        }, format='json')

        assert BlacklistedToken.objects.filter(token__jti=outstanding_jti).exists()

    def test_user_with_no_outstanding_tokens_reset_succeeds(self, api_client, user, reset_token):
        """Reset should succeed even when the user has no outstanding JWT tokens."""
        response = api_client.post(CONFIRM_URL, {
            'token': str(reset_token.token),
            'new_password': 'NewPass456!',
        }, format='json')

        assert response.status_code == status.HTTP_200_OK
