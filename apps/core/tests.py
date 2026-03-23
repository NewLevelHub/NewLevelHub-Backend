from django.test import SimpleTestCase
from django.urls import reverse
from rest_framework import status


class BuildInfoEndpointTests(SimpleTestCase):
    def test_build_info_returns_static_payload(self):
        response = self.client.get(reverse('build-info'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.json(),
            {
                'service': 'newlevelhub-backend',
                'version': '1.0.0',
                'status': 'ok',
            },
        )
