from django.test import SimpleTestCase
from django.urls import resolve, reverse
from rest_framework import status
from rest_framework.test import APIRequestFactory, APIClient
from unittest.mock import patch

from apps.core.views import build_info, health_check, ping, status_text


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


class PingEndpointTests(SimpleTestCase):
    def test_ping_returns_pong_payload(self):
        path = reverse('ping')
        self.assertEqual(path, '/api/v1/ping/')

        match = resolve(path)
        self.assertIs(match.func, ping)

        request = APIRequestFactory().get(path)
        response = ping(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {'message': 'pong'})


class StatusTextEndpointTests(SimpleTestCase):
    def test_status_text_returns_static_payload(self):
        path = reverse('status-text')
        self.assertEqual(path, '/api/v1/status-text/')

        match = resolve(path)
        self.assertIs(match.func, status_text)

        request = APIRequestFactory().get(path)
        response = status_text(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {'status_text': 'service is up'})


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


class ServerTimeEndpointTests(SimpleTestCase):
    def test_server_time_returns_utc_time_in_iso8601_format(self):
        path = reverse('server-time')
        self.assertEqual(path, '/api/v1/time/')

        client = APIClient()
        response = client.get(path)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('utc_time', response.data)
        self.assertIsInstance(response.data['utc_time'], str)
        self.assertTrue(response.data['utc_time'].endswith('Z'))
