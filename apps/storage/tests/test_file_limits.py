from django.core.files.uploadedfile import SimpleUploadedFile
import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.notifications.models import Notification
from apps.storage.models import File
from apps.users.models import User


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='Storage Co', plan='basic', storage_limit_gb=1)


@pytest.fixture
def company_admin(db, company):
    return User.objects.create_user(
        email='admin@storage.co',
        password='pass',
        first_name='Admin',
        last_name='Storage',
        role='company_admin',
        company=company,
    )


def _url():
    return '/api/v1/storage/files/'


def _upload(size):
    return SimpleUploadedFile('test.txt', b'a' * size, content_type='text/plain')


@pytest.mark.django_db
class TestFileLimit:
    def test_upload_file_storage_limit_reached_returns_400(self, api_client, company_admin, company):
        File.objects.create(
            name='existing.txt',
            file=_upload(10),
            file_size=1 * 1024 * 1024 * 1024,
            content_type='text/plain',
            owner=company_admin,
            company=company,
        )

        api_client.force_authenticate(user=company_admin)
        response = api_client.post(
            _url(),
            {'name': 'blocked.txt', 'file': _upload(10)},
            format='multipart',
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data['detail'] == 'Storage limit reached'

    def test_upload_file_at_80_percent_creates_admin_notification(self, api_client, company_admin, company):
        File.objects.create(
            name='existing.txt',
            file=_upload(10),
            file_size=int(0.79 * 1024 * 1024 * 1024),
            content_type='text/plain',
            owner=company_admin,
            company=company,
        )

        api_client.force_authenticate(user=company_admin)
        response = api_client.post(
            _url(),
            {'name': 'new.txt', 'file': _upload(20 * 1024 * 1024)},
            format='multipart',
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert Notification.objects.filter(
            user=company_admin,
            notification_type='announcement_company',
            title='System limit warning',
            body='Storage usage reached 80% (0.81/1 GB).',
        ).exists()

    def test_upload_file_at_95_percent_creates_admin_notification(self, api_client, company_admin, company):
        File.objects.create(
            name='existing95.txt',
            file=_upload(10),
            file_size=int(0.94 * 1024 * 1024 * 1024),
            content_type='text/plain',
            owner=company_admin,
            company=company,
        )

        api_client.force_authenticate(user=company_admin)
        response = api_client.post(
            _url(),
            {'name': 'new95.txt', 'file': _upload(20 * 1024 * 1024)},
            format='multipart',
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert Notification.objects.filter(
            user=company_admin,
            notification_type='announcement_company',
            title='System limit warning',
            body='Storage usage reached 95% (0.96/1 GB).',
        ).exists()
