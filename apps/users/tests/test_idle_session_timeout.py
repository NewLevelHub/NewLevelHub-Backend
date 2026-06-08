from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient
from apps.core.error_codes import SESSION_IDLE_TIMEOUT
from apps.users.models import User
from apps.users.session import is_idle_session_marked_expired


LOGIN_URL = '/api/v1/auth/login/'
REFRESH_URL = '/api/v1/auth/token/refresh/'
ME_URL = '/api/v1/auth/me/'


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email='idle-user@example.com',
        password='StrongPass123!',
        first_name='Idle',
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


@pytest.mark.django_db
class TestIdleSessionTimeout:
    def test_active_user_session_remains_valid(self, api_client, user, settings):
        settings.IDLE_SESSION_TIMEOUT = timedelta(minutes=5)
        tokens = _login(api_client, user)

        user.last_login = timezone.now() - timedelta(minutes=2)
        user.save(update_fields=['last_login'])

        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        response = api_client.get(ME_URL)

        assert response.status_code == status.HTTP_200_OK
        user.refresh_from_db()
        assert user.last_login is not None
        assert timezone.now() - user.last_login < timedelta(minutes=1)

    def test_inactive_user_receives_session_idle_timeout_error(self, api_client, user, settings):
        settings.IDLE_SESSION_TIMEOUT = timedelta(minutes=5)
        tokens = _login(api_client, user)

        user.last_login = timezone.now() - timedelta(minutes=6)
        user.save(update_fields=['last_login'])

        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        response = api_client.get(ME_URL)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.data['success'] is False
        assert response.data['error']['code'] == SESSION_IDLE_TIMEOUT

    def test_refresh_token_rejected_after_idle_timeout(self, api_client, user, settings):
        settings.IDLE_SESSION_TIMEOUT = timedelta(minutes=5)
        tokens = _login(api_client, user)
        refresh = tokens['refresh']

        user.last_login = timezone.now() - timedelta(minutes=6)
        user.save(update_fields=['last_login'])

        response = api_client.post(REFRESH_URL, {'refresh': refresh}, format='json')

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.data['success'] is False
        assert response.data['error']['code'] == SESSION_IDLE_TIMEOUT
        assert is_idle_session_marked_expired(user.pk)

        refresh_again = api_client.post(REFRESH_URL, {'refresh': refresh}, format='json')
        assert refresh_again.status_code == status.HTTP_401_UNAUTHORIZED
        assert refresh_again.data['error']['code'] == SESSION_IDLE_TIMEOUT

    def test_idle_timeout_disabled_when_zero(self, api_client, user, settings):
        settings.IDLE_SESSION_TIMEOUT = timedelta(minutes=0)
        tokens = _login(api_client, user)

        user.last_login = timezone.now() - timedelta(days=30)
        user.save(update_fields=['last_login'])

        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        response = api_client.get(ME_URL)

        assert response.status_code == status.HTTP_200_OK
