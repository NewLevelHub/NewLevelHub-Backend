from django.test import SimpleTestCase
from django.urls import resolve, reverse
from rest_framework import status
from rest_framework.test import APIRequestFactory
from unittest.mock import patch

from apps.core.views import build_info, health_check


class BuildInfoEndpointTests(SimpleTestCase):
    def test_build_info_returns_static_payload(self):
        path = reverse('build-info')
        self.assertEqual(path, '/api/v1/build-info/')

        match = resolve(path)
        self.assertIs(match.func, build_info)

        request = APIRequestFactory().get(path)
        response = build_info(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data,
            {
                'service': 'newlevelhub-backend',
                'version': '1.0.0',
                'status': 'ok',
            },
        )


class HealthCheckEndpointTests(SimpleTestCase):
    def test_health_check_url_resolves_to_health_check_view(self):
        path = reverse('health-check')
        self.assertEqual(path, '/api/v1/health/')

        match = resolve(path)
        self.assertIs(match.func, health_check)

    @patch('apps.core.views.connection.ensure_connection')
    def test_health_check_returns_healthy_when_database_connection_is_ok(self, mocked_ensure_connection):
        path = reverse('health-check')
        request = APIRequestFactory().get(path)

        response = health_check(request)

        mocked_ensure_connection.assert_called_once_with()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data,
            {
                'status': 'healthy',
                'database': 'connected',
                'message': 'New Level Hub Backend is running',
            },
        )

    @patch('apps.core.views.connection.ensure_connection', side_effect=Exception('db down'))
    def test_health_check_returns_unhealthy_when_database_connection_fails(self, mocked_ensure_connection):
        path = reverse('health-check')
        request = APIRequestFactory().get(path)

        response = health_check(request)

        mocked_ensure_connection.assert_called_once_with()
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data['status'], 'unhealthy')
        self.assertEqual(response.data['database'], 'error: db down')
        self.assertEqual(response.data['message'], 'New Level Hub Backend is running')
