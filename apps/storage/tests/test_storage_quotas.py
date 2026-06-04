"""
Tests for Storage Quotas feature:
  - GET /api/v1/storage/usage/ (AC1: new response shape)
  - Storage limit check on file upload (AC2)
  - Celery cleanup_deleted_files task (AC4)
"""

import pytest
from datetime import timedelta
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.storage.models import File
from apps.storage.tasks import cleanup_deleted_files
from apps.users.models import User

USAGE_URL = '/api/v1/storage/usage/'
FILES_URL = '/api/v1/storage/files/'


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='Quota Co', plan='basic', storage_limit_gb=1)


@pytest.fixture
def company_admin(db, company):
    return User.objects.create_user(
        email='admin@quota.co',
        password='pass',
        first_name='Admin',
        last_name='Quota',
        role='company_admin',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='emp@quota.co',
        password='pass',
        first_name='Emp',
        last_name='Quota',
        role='employee',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def guest_user(db):
    return User.objects.create_user(
        email='guest@quota.co',
        password='pass',
        first_name='Guest',
        last_name='User',
        role='guest',
    )


def _make_file(size=1024):
    return SimpleUploadedFile('test.txt', b'x' * size, content_type='text/plain')


def _create_db_file(owner, company=None, size=1024, is_deleted=False, deleted_at=None):
    """Create a File record directly in the DB (no disk I/O needed for size checks)."""
    f = File.objects.create(
        name='test.txt',
        file=_make_file(size),
        file_size=size,
        content_type='text/plain',
        owner=owner,
        company=company,
    )
    if is_deleted:
        f.is_deleted = True
        f.deleted_at = deleted_at or timezone.now()
        f.save(update_fields=['is_deleted', 'deleted_at'])
    return f


# ---------------------------------------------------------------------------
# AC1 — GET /api/v1/storage/usage/ response shape
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestStorageUsageEndpoint:
    def test_unauthenticated_returns_401(self, api_client):
        response = api_client.get(USAGE_URL)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_guest_can_access_storage_usage(self, api_client, guest_user):
        # Guests may check their own storage usage.
        api_client.force_authenticate(user=guest_user)
        response = api_client.get(USAGE_URL)
        assert response.status_code == status.HTTP_200_OK

    def test_response_shape_for_company_member(self, api_client, company_admin, company):
        api_client.force_authenticate(user=company_admin)
        response = api_client.get(USAGE_URL)

        assert response.status_code == status.HTTP_200_OK
        data = response.data

        # Top-level keys
        assert 'personal' in data
        assert 'company' in data

        # personal sub-fields
        personal = data['personal']
        assert 'used_bytes' in personal
        assert 'file_count' in personal

        # company sub-fields
        company_data = data['company']
        assert 'used_bytes' in company_data
        assert 'limit_bytes' in company_data
        assert 'file_count' in company_data

    def test_limit_bytes_matches_company_storage_limit_gb(self, api_client, company_admin, company):
        company.storage_limit_gb = 20
        company.save(update_fields=['storage_limit_gb'])

        api_client.force_authenticate(user=company_admin)
        response = api_client.get(USAGE_URL)

        assert response.status_code == status.HTTP_200_OK
        expected_limit = 20 * 1024 * 1024 * 1024
        assert response.data['company']['limit_bytes'] == expected_limit

    def test_personal_used_bytes_counts_only_own_files(self, api_client, company_admin, employee, company):
        # Personal file owned by admin (company=None → true personal)
        _create_db_file(owner=company_admin, company=None, size=500)
        # Personal file owned by employee — should NOT appear in admin's personal stats
        _create_db_file(owner=employee, company=None, size=200)

        api_client.force_authenticate(user=company_admin)
        response = api_client.get(USAGE_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.data['personal']['used_bytes'] == 500
        assert response.data['personal']['file_count'] == 1

    def test_company_used_bytes_aggregates_all_company_files(self, api_client, company_admin, employee, company):
        _create_db_file(owner=company_admin, company=company, size=300)
        _create_db_file(owner=employee, company=company, size=700)

        api_client.force_authenticate(user=company_admin)
        response = api_client.get(USAGE_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.data['company']['used_bytes'] == 1000
        assert response.data['company']['file_count'] == 2

    def test_soft_deleted_files_excluded_from_counts(self, api_client, company_admin, company):
        # Use company=None so the active file also shows in personal stats
        _create_db_file(owner=company_admin, company=None, size=100)
        _create_db_file(owner=company_admin, company=None, size=200, is_deleted=True)

        api_client.force_authenticate(user=company_admin)
        response = api_client.get(USAGE_URL)

        assert response.status_code == status.HTTP_200_OK
        # Soft-deleted file should not count
        assert response.data['personal']['used_bytes'] == 100
        assert response.data['personal']['file_count'] == 1
        assert response.data['company']['used_bytes'] == 100
        assert response.data['company']['file_count'] == 1

    def test_empty_usage_returns_zeros(self, api_client, company_admin, company):
        api_client.force_authenticate(user=company_admin)
        response = api_client.get(USAGE_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.data['personal']['used_bytes'] == 0
        assert response.data['personal']['file_count'] == 0
        assert response.data['company']['used_bytes'] == 0
        assert response.data['company']['file_count'] == 0

    def test_employee_can_access_usage(self, api_client, employee, company):
        api_client.force_authenticate(user=employee)
        response = api_client.get(USAGE_URL)
        assert response.status_code == status.HTTP_200_OK

    def test_company_used_bytes_includes_personal_files_of_employees(
        self, api_client, company_admin, employee, company
    ):
        """Personal files of company employees must be counted in company total."""
        _create_db_file(owner=company_admin, company=company, size=300)   # company-scoped
        _create_db_file(owner=employee, company=None, size=400)            # personal by employee

        api_client.force_authenticate(user=company_admin)
        response = api_client.get(USAGE_URL)

        assert response.status_code == status.HTTP_200_OK
        # Both files count toward company total
        assert response.data['company']['used_bytes'] == 700
        assert response.data['company']['file_count'] == 2

    def test_personal_used_bytes_excludes_company_scoped_files(
        self, api_client, company_admin, company
    ):
        """Company-scoped files should not appear in personal.used_bytes."""
        _create_db_file(owner=company_admin, company=company, size=1000)   # company-scoped
        _create_db_file(owner=company_admin, company=None, size=250)        # true personal

        api_client.force_authenticate(user=company_admin)
        response = api_client.get(USAGE_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.data['personal']['used_bytes'] == 250
        assert response.data['personal']['file_count'] == 1


# ---------------------------------------------------------------------------
# AC2 — Storage limit check on file upload
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestStorageQuotaOnUpload:
    def test_upload_blocked_when_limit_exceeded(self, api_client, company_admin, company):
        # Fill the 1 GB limit
        _create_db_file(
            owner=company_admin,
            company=company,
            size=1 * 1024 * 1024 * 1024,
        )

        api_client.force_authenticate(user=company_admin)
        response = api_client.post(
            FILES_URL,
            {'name': 'blocked.txt', 'file': _make_file(10)},
            format='multipart',
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_upload_allowed_when_below_limit(self, api_client, company_admin, company):
        # Only 100 bytes used against a 1 GB limit
        _create_db_file(owner=company_admin, company=company, size=100)

        api_client.force_authenticate(user=company_admin)
        response = api_client.post(
            FILES_URL,
            {'name': 'ok.txt', 'file': _make_file(100)},
            format='multipart',
        )

        assert response.status_code == status.HTTP_201_CREATED

    def test_personal_upload_blocked_when_company_quota_filled_by_personal_files(
        self, api_client, employee, company
    ):
        """Personal files of employees consume company quota — upload must be blocked."""
        # Fill quota with employee's personal file
        _create_db_file(owner=employee, company=None, size=1 * 1024 * 1024 * 1024)

        api_client.force_authenticate(user=employee)
        response = api_client.post(
            FILES_URL,
            {'name': 'blocked.txt', 'file': _make_file(10)},
            format='multipart',
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# AC4 — Celery task: cleanup_deleted_files
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCleanupDeletedFilesTask:
    def test_deletes_files_older_than_30_days(self, company_admin, company):
        old_cutoff = timezone.now() - timedelta(days=31)
        file_obj = _create_db_file(
            owner=company_admin,
            company=company,
            is_deleted=True,
            deleted_at=old_cutoff,
        )
        file_id = file_obj.id

        with patch.object(type(file_obj.file), 'delete', return_value=None):
            count = cleanup_deleted_files()

        assert count >= 1
        assert not File.all_objects.filter(id=file_id).exists()

    def test_does_not_delete_recently_soft_deleted_files(self, company_admin, company):
        recent_file = _create_db_file(
            owner=company_admin,
            company=company,
            is_deleted=True,
            deleted_at=timezone.now() - timedelta(days=5),
        )

        count = cleanup_deleted_files()

        assert count == 0
        assert File.all_objects.filter(id=recent_file.id).exists()

    def test_does_not_delete_active_files(self, company_admin, company):
        active_file = _create_db_file(owner=company_admin, company=company)

        count = cleanup_deleted_files()

        assert count == 0
        assert File.objects.filter(id=active_file.id).exists()

    def test_returns_count_of_deleted_files(self, company_admin, company):
        old_time = timezone.now() - timedelta(days=35)
        file_objs = [
            _create_db_file(
                owner=company_admin,
                company=company,
                is_deleted=True,
                deleted_at=old_time,
            )
            for _ in range(3)
        ]

        # Patch FieldFile.delete on the class so disk deletion is skipped
        with patch.object(type(file_objs[0].file), 'delete', return_value=None):
            count = cleanup_deleted_files()

        assert count == 3
