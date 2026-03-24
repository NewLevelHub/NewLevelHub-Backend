from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

User = get_user_model()


class CurrentUserPermissionsTests(TestCase):
    def test_me_permissions_requires_authentication(self):
        path = reverse("current-user-permissions")
        response = APIClient().get(path)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_me_permissions_returns_role_and_capabilities_for_authenticated_user(self):
        path = reverse("current-user-permissions")
        user = User.objects.create_user(email="admin@example.com", password="pass12345", role="admin")

        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get(path)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["role"], "admin")
        self.assertEqual(
            response.data["capabilities"],
            {
                "can_manage_users": True,
                "can_view_crm": True,
                "can_manage_booking": True,
                "can_access_iot": True,
            },
        )


class LogoutValidationTests(SimpleTestCase):
    databases = {'default'}

    def test_logout_requires_refresh_token(self):
        path = reverse("logout")  # /api/v1/auth/logout/

        client = APIClient()
        client.force_authenticate(user=SimpleNamespace(is_authenticated=True))
        response = client.post(path, data={}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data, {"error": "Refresh token is required"})
