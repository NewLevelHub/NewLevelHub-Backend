from django.test import SimpleTestCase
from django.urls import resolve, reverse
from rest_framework import status
from rest_framework.test import APIRequestFactory

from apps.core.views import build_info


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
