from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.error_codes import SESSION_ABSOLUTE_TIMEOUT
from apps.users.jwt import issue_refresh_token
from apps.users.models import User
from apps.users.session import (
    SESSION_CREATED_AT_CLAIM,
    is_absolute_session_marked_expired,
)


LOGIN_URL = '/api/v1/auth/login/'
REFRESH_URL = '/api/v1/auth/token/refresh/'
ME_URL = '/api/v1/auth/me/'


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email='absolute-user@example.com',
        password='StrongPass123!',
        first_name='Absolute',
        last_name='User',
        is_email_verified=True,
    )


def _login(api_client, user):
    response = api_client.post(
        LOGIN_URL,
        {'email': user.email, 'password': 'StrongPass123!', 'remember_me': False},
        format='json',
    )
    assert response.status_code == status.HTTP_200_OK
    return response.data['tokens']


def _tokens_with_session_start(user, session_start, remember_me=False):
    refresh = issue_refresh_token(user, remember_me=remember_me)
    timestamp = int(session_start.timestamp())
    refresh[SESSION_CREATED_AT_CLAIM] = timestamp
    access = refresh.access_token
    access[SESSION_CREATED_AT_CLAIM] = timestamp
    return {'access': str(access), 'refresh': str(refresh)}


@pytest.mark.django_db
class TestAbsoluteSessionTimeout:
    def test_active_user_session_remains_valid(self, api_client, user, settings):
        settings.ABSOLUTE_SESSION_TIMEOUT = timedelta(minutes=15)
        tokens = _login(api_client, user)

        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        response = api_client.get(ME_URL)

        assert response.status_code == status.HTTP_200_OK

    def test_absolute_timeout_rejects_authenticated_request(self, api_client, user, settings):
        settings.ABSOLUTE_SESSION_TIMEOUT = timedelta(minutes=15)
        session_start = timezone.now() - timedelta(minutes=16)
        tokens = _tokens_with_session_start(user, session_start)

        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        response = api_client.get(ME_URL)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.data['success'] is False
        assert response.data['error']['code'] == SESSION_ABSOLUTE_TIMEOUT
        assert is_absolute_session_marked_expired(user.pk)

    def test_refresh_token_rejected_after_absolute_timeout(self, api_client, user, settings):
        settings.ABSOLUTE_SESSION_TIMEOUT = timedelta(minutes=15)
        session_start = timezone.now() - timedelta(minutes=16)
        tokens = _tokens_with_session_start(user, session_start)
        refresh = tokens['refresh']

        response = api_client.post(REFRESH_URL, {'refresh': refresh}, format='json')

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.data['success'] is False
        assert response.data['error']['code'] == SESSION_ABSOLUTE_TIMEOUT
        assert is_absolute_session_marked_expired(user.pk)

        refresh_again = api_client.post(REFRESH_URL, {'refresh': refresh}, format='json')
        assert refresh_again.status_code == status.HTTP_401_UNAUTHORIZED
        assert refresh_again.data['error']['code'] == SESSION_ABSOLUTE_TIMEOUT

    def test_refresh_preserves_session_created_at(self, api_client, user, settings):
        settings.ABSOLUTE_SESSION_TIMEOUT = timedelta(minutes=15)
        session_start = timezone.now() - timedelta(minutes=10)
        tokens = _tokens_with_session_start(user, session_start)

        response = api_client.post(REFRESH_URL, {'refresh': tokens['refresh']}, format='json')

        assert response.status_code == status.HTTP_200_OK
        rotated_refresh = RefreshToken(response.data['refresh'])
        assert int(rotated_refresh[SESSION_CREATED_AT_CLAIM]) == int(session_start.timestamp())

    def test_login_stamps_session_created_at_on_tokens(self, api_client, user):
        before = int(timezone.now().timestamp())
        tokens = _login(api_client, user)
        after = int(timezone.now().timestamp())

        refresh = RefreshToken(tokens['refresh'])
        session_created_at = int(refresh[SESSION_CREATED_AT_CLAIM])

        assert before <= session_created_at <= after

    def test_absolute_timeout_disabled_when_zero(self, api_client, user, settings):
        settings.ABSOLUTE_SESSION_TIMEOUT = timedelta(minutes=0)
        session_start = timezone.now() - timedelta(days=30)
        tokens = _tokens_with_session_start(user, session_start)

        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        response = api_client.get(ME_URL)

        assert response.status_code == status.HTTP_200_OK
