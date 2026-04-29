from django.core.files.uploadedfile import SimpleUploadedFile
import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.limits import notify_company_admins_limit_thresholds
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
            title='System limit warning: 80%',
        ).exists()

    def test_upload_file_at_80_percent_notification_body_is_not_empty(
        self, api_client, company_admin, company
    ):
        File.objects.create(
            name='existing.txt',
            file=_upload(10),
            file_size=int(0.79 * 1024 * 1024 * 1024),
            content_type='text/plain',
            owner=company_admin,
            company=company,
        )

        api_client.force_authenticate(user=company_admin)
        api_client.post(
            _url(),
            {'name': 'new.txt', 'file': _upload(20 * 1024 * 1024)},
            format='multipart',
        )

        notification = Notification.objects.filter(
            user=company_admin,
            notification_type='announcement_company',
            title='System limit warning: 80%',
        ).first()
        assert notification is not None
        assert notification.body != ''
        assert '80%' in notification.body
        assert 'storage' in notification.body.lower()

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
            title='System limit warning: 95%',
        ).exists()


@pytest.mark.django_db
class TestStorageNotificationIdempotency:
    """Verify that repeated threshold checks do not create duplicate notifications."""

    def test_calling_notify_twice_at_same_threshold_creates_only_one_notification(
        self, company_admin, company
    ):
        # First call: 80% threshold
        notify_company_admins_limit_thresholds(
            company=company,
            metric='storage',
            current_value=0.82,
            limit_value=1,
        )
        count_after_first = Notification.objects.filter(
            user=company_admin,
            notification_type='announcement_company',
            title='System limit warning: 80%',
        ).count()
        assert count_after_first == 1

        # Second call at a slightly different usage but still within same 80% threshold
        notify_company_admins_limit_thresholds(
            company=company,
            metric='storage',
            current_value=0.85,
            limit_value=1,
        )
        count_after_second = Notification.objects.filter(
            user=company_admin,
            notification_type='announcement_company',
            title='System limit warning: 80%',
        ).count()
        assert count_after_second == 1, (
            'A second notification must not be created when the same threshold is crossed again.'
        )

    def test_calling_notify_after_read_creates_new_notification(
        self, company_admin, company
    ):
        # First call creates notification
        notify_company_admins_limit_thresholds(
            company=company,
            metric='storage',
            current_value=0.82,
            limit_value=1,
        )
        # Admin reads it
        Notification.objects.filter(
            user=company_admin,
            notification_type='announcement_company',
            title='System limit warning: 80%',
        ).update(is_read=True)

        # Second call at same threshold should create a fresh notification
        # because the previous one was already read/dismissed
        notify_company_admins_limit_thresholds(
            company=company,
            metric='storage',
            current_value=0.87,
            limit_value=1,
        )
        count = Notification.objects.filter(
            user=company_admin,
            notification_type='announcement_company',
            title='System limit warning: 80%',
        ).count()
        assert count == 2, (
            'A new notification should be created once the previous one has been read.'
        )

    def test_80_and_95_percent_thresholds_create_separate_notifications(
        self, company_admin, company
    ):
        notify_company_admins_limit_thresholds(
            company=company,
            metric='storage',
            current_value=0.96,
            limit_value=1,
        )
        assert Notification.objects.filter(
            user=company_admin,
            notification_type='announcement_company',
            title='System limit warning: 80%',
        ).count() == 1
        assert Notification.objects.filter(
            user=company_admin,
            notification_type='announcement_company',
            title='System limit warning: 95%',
        ).count() == 1
