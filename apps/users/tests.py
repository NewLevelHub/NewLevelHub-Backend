from types import SimpleNamespace

from django.test import SimpleTestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIRequestFactory

from apps.users.views import logout_user


class LogoutValidationTests(SimpleTestCase):
    def test_logout_requires_refresh_token(self):
        path = reverse("logout")  # /api/v1/auth/logout/

        request = APIRequestFactory().post(path, data={}, format="json")
        request.user = SimpleNamespace(is_authenticated=True)  # bypass IsAuthenticated

        response = logout_user(request)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data, {"error": "Refresh token is required"})

