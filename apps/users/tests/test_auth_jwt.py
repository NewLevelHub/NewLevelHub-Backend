from datetime import datetime, timedelta, timezone as dt_timezone

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from apps.users.models import User


LOGIN_URL = '/api/v1/auth/login/'
REFRESH_URL = '/api/v1/auth/token/refresh/'
LOGOUT_URL = '/api/v1/auth/logout/'
ME_URL = '/api/v1/auth/me/'


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email='jwt-user@example.com',
        password='StrongPass123!',
        first_name='JWT',
        last_name='User',
        is_email_verified=True,
    )


def _token_lifetime(token_str: str) -> timedelta:
    token = RefreshToken(token_str)
    expires_at = datetime.fromtimestamp(int(token['exp']), tz=dt_timezone.utc)
    return expires_at - timezone.now()


@pytest.mark.django_db
class TestAuthJWT:
    def test_login_without_remember_me_uses_default_refresh_ttl(self, api_client, user, settings):
        response = api_client.post(
            LOGIN_URL,
            {'email': user.email, 'password': 'StrongPass123!', 'remember_me': False},
            format='json',
        )

        assert response.status_code == status.HTTP_200_OK
        lifetime = _token_lifetime(response.data['tokens']['refresh'])
        expected = settings.REFRESH_TOKEN_LIFETIME
        assert expected - timedelta(minutes=1) <= lifetime <= expected + timedelta(minutes=1)

    def test_login_with_remember_me_uses_extended_refresh_ttl(self, api_client, user, settings):
        response = api_client.post(
            LOGIN_URL,
            {'email': user.email, 'password': 'StrongPass123!', 'remember_me': True},
            format='json',
        )

        assert response.status_code == status.HTTP_200_OK
        lifetime = _token_lifetime(response.data['tokens']['refresh'])
        expected = settings.REMEMBER_ME_LIFETIME
        assert expected - timedelta(minutes=1) <= lifetime <= expected + timedelta(minutes=1)

        jti = str(RefreshToken(response.data['tokens']['refresh'])['jti'])
        outstanding = OutstandingToken.objects.get(jti=jti)
        db_lifetime = outstanding.expires_at - timezone.now()
        assert expected - timedelta(minutes=1) <= db_lifetime <= expected + timedelta(minutes=1)

    def test_refresh_rotation_preserves_remember_me_lifetime(self, api_client, user, settings):
        login_response = api_client.post(
            LOGIN_URL,
            {'email': user.email, 'password': 'StrongPass123!', 'remember_me': True},
            format='json',
        )
        refresh = login_response.data['tokens']['refresh']

        refresh_response = api_client.post(REFRESH_URL, {'refresh': refresh}, format='json')

        assert refresh_response.status_code == status.HTTP_200_OK
        assert 'refresh' in refresh_response.data
        lifetime = _token_lifetime(refresh_response.data['refresh'])
        expected = settings.REMEMBER_ME_LIFETIME
        assert expected - timedelta(minutes=1) <= lifetime <= expected + timedelta(minutes=1)

        jti = str(RefreshToken(refresh_response.data['refresh'])['jti'])
        outstanding = OutstandingToken.objects.get(jti=jti)
        db_lifetime = outstanding.expires_at - timezone.now()
        assert expected - timedelta(minutes=1) <= db_lifetime <= expected + timedelta(minutes=1)

    def test_logout_blacklists_refresh_token(self, api_client, user):
        login_response = api_client.post(
            LOGIN_URL,
            {'email': user.email, 'password': 'StrongPass123!', 'remember_me': False},
            format='json',
        )
        refresh = login_response.data['tokens']['refresh']
        jti = str(RefreshToken(refresh)['jti'])

        logout_response = api_client.post(LOGOUT_URL, {'refresh': refresh}, format='json')

        assert logout_response.status_code == status.HTTP_200_OK
        assert BlacklistedToken.objects.filter(token__jti=jti).exists()

    def test_refresh_with_blacklisted_token_returns_401(self, api_client, user):
        login_response = api_client.post(
            LOGIN_URL,
            {'email': user.email, 'password': 'StrongPass123!', 'remember_me': False},
            format='json',
        )
        refresh = login_response.data['tokens']['refresh']

        logout_response = api_client.post(LOGOUT_URL, {'refresh': refresh}, format='json')
        assert logout_response.status_code == status.HTTP_200_OK

        refresh_response = api_client.post(REFRESH_URL, {'refresh': refresh}, format='json')
        assert refresh_response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_authenticated_request_updates_last_login(self, api_client, user):
        login_response = api_client.post(
            LOGIN_URL,
            {'email': user.email, 'password': 'StrongPass123!', 'remember_me': False},
            format='json',
        )
        access = login_response.data['tokens']['access']

        api_client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')
        response = api_client.get(ME_URL)

        assert response.status_code == status.HTTP_200_OK
        user.refresh_from_db()
        assert user.last_login is not None
