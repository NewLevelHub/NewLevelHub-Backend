from unittest.mock import patch

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient


@pytest.mark.django_db
def test_health_check_returns_200_when_dependencies_are_up():
    client = APIClient()
    response = client.get(reverse('health-check'))

    assert response.status_code == status.HTTP_200_OK
    assert response.data['status'] == 'healthy'
    assert response.data['database'] == 'connected'
    assert response.data['redis'] == 'connected'


@pytest.mark.django_db
def test_health_check_returns_503_when_database_is_down():
    client = APIClient()

    with patch('apps.core.views.connection.ensure_connection', side_effect=Exception('db down')):
        response = client.get(reverse('health-check'))

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.data['status'] == 'unhealthy'
    assert response.data['database'] == 'unavailable'


@pytest.mark.django_db
def test_health_check_returns_503_when_redis_is_down():
    client = APIClient()

    with patch('apps.core.views.redis.from_url', side_effect=Exception('redis down')):
        response = client.get(reverse('health-check'))

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.data['status'] == 'unhealthy'
    assert response.data['redis'] == 'unavailable'


def test_ping_returns_pong():
    client = APIClient()
    response = client.get(reverse('ping'))

    assert response.status_code == status.HTTP_200_OK
    assert response.data['message'] == 'pong'
