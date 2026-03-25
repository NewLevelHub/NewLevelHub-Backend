from types import SimpleNamespace

from django.test import SimpleTestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient


class LogoutValidationTests(SimpleTestCase):
    databases = {"default"}

    def test_logout_requires_refresh_token(self):
        path = reverse("logout")  # /api/v1/auth/logout/

        client = APIClient()
        client.force_authenticate(user=SimpleNamespace(is_authenticated=True))
        response = client.post(path, data={}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data, {"error": "Refresh token is required"})
