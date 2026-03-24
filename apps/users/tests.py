from types import SimpleNamespace

from django.test import SimpleTestCase
from django.urls import resolve, reverse
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.users.models import User
from apps.users.views import list_user_roles, logout_user


class LogoutValidationTests(SimpleTestCase):
    def test_logout_requires_refresh_token(self):
        path = reverse("logout")
        self.assertEqual(path, "/api/v1/auth/logout/")

        match = resolve(path)
        self.assertIs(match.func, logout_user)

        request = APIRequestFactory().post(path, data={}, format="json")
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
        response = logout_user(request)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data, {"error": "Refresh token is required"})


class UserRolesEndpointTests(SimpleTestCase):
    def test_roles_endpoint_returns_expected_structure(self):
        path = reverse("user-roles")
        self.assertEqual(path, "/api/v1/auth/roles/")

        match = resolve(path)
        self.assertIs(match.func, list_user_roles)

        request = APIRequestFactory().get(path)
        response = list_user_roles(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        self.assertGreater(len(response.data), 0)

        first_role = response.data[0]
        self.assertIsInstance(first_role, dict)
        self.assertEqual(set(first_role.keys()), {"code", "label", "description"})

    def test_roles_endpoint_contains_required_roles(self):
        path = reverse("user-roles")

        request = APIRequestFactory().get(path)
        response = list_user_roles(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        role_codes = {item["code"] for item in response.data}
        self.assertTrue({"admin", "tenant", "employee"}.issubset(role_codes))

    def test_roles_endpoint_matches_user_role_choices(self):
        path = reverse("user-roles")

        request = APIRequestFactory().get(path)
        response = list_user_roles(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, User.get_role_definitions())
