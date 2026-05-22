"""S3 storage tests using moto (no real network calls)."""
import os
import threading
import boto3
import pytest
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import override_settings
from django.utils.functional import empty as _lazy_empty
from moto import mock_aws
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.storage.models import File
from apps.users.models import User


@pytest.fixture
def s3_settings():
    with mock_aws():
        conn = boto3.client('s3', region_name='eu-central-1')
        conn.create_bucket(
            Bucket='test-bucket',
            CreateBucketConfiguration={'LocationConstraint': 'eu-central-1'},
        )
        with override_settings(
            USE_S3=True,
            AWS_STORAGE_BUCKET_NAME='test-bucket',
            AWS_S3_REGION_NAME='eu-central-1',
            AWS_ACCESS_KEY_ID='testing',
            AWS_SECRET_ACCESS_KEY='testing',
            AWS_S3_ENDPOINT_URL=None,
            AWS_S3_PRESIGNED_URL_EXPIRY=900,
            # Mirrors production config: overwrite=False so exists() actually
            # queries S3 instead of short-circuiting to False.
            AWS_S3_FILE_OVERWRITE=False,
            STORAGES={
                'default': {
                    'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage',
                },
                'staticfiles': {
                    'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
                },
            },
        ):
            # S3Storage caches boto3 connections in a class-level threading.local().
            # Between tests the mock_aws context resets but the cached connection
            # from a previous test still points to the old (now dead) moto backend.
            # Reset it so every test opens a fresh connection inside the active mock.
            from storages.backends.s3 import S3Storage
            S3Storage._connections = threading.local()
            default_storage._wrapped = _lazy_empty
            yield


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='S3 Co', plan='basic', storage_limit_gb=5)


@pytest.fixture
def company_member(db, company):
    return User.objects.create_user(
        email='s3member@test.co',
        password='pass',
        first_name='S3',
        last_name='Member',
        role='employee',
        company=company,
    )


@pytest.mark.django_db
class TestPresignedDownload:
    def test_download_returns_json_with_presigned_url(self, s3_settings, api_client, company_member):
        uploaded = SimpleUploadedFile('doc.txt', b'hello s3', content_type='text/plain')
        file_obj = File.objects.create(
            name='doc.txt',
            file=uploaded,
            file_size=uploaded.size,
            content_type='text/plain',
            owner=company_member,
            company=company_member.company,
        )
        api_client.force_authenticate(user=company_member)

        response = api_client.get(f'/api/v1/storage/files/{file_obj.id}/download/')

        assert response.status_code == status.HTTP_200_OK
        assert 'url' in response.data
        assert 'expires_in' in response.data
        assert response.data['expires_in'] == 900
        assert 'test-bucket' in response.data['url'] or 'amazonaws.com' in response.data['url']

    def test_download_forbidden_without_permission(self, s3_settings, api_client, company_member, company):
        from apps.storage.models import Folder

        other = User.objects.create_user(
            email='other@test.co',
            password='pass',
            first_name='Other',
            last_name='User',
            role='employee',
            company=company,
        )
        personal_folder = Folder.objects.create(
            name='Private',
            scope='personal',
            owner=other,
            company=None,
        )
        uploaded = SimpleUploadedFile('private.txt', b'secret', content_type='text/plain')
        file_obj = File.objects.create(
            name='private.txt',
            file=uploaded,
            file_size=uploaded.size,
            content_type='text/plain',
            owner=other,
            company=None,
            folder=personal_folder,
        )
        api_client.force_authenticate(user=company_member)

        response = api_client.get(f'/api/v1/storage/files/{file_obj.id}/download/')

        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestMigrateFilesToS3:
    def test_dry_run_logs_missing_local_file(self, s3_settings, company_member, company, capsys):
        file_obj = File.objects.create(
            name='ghost.txt',
            file='storage/2024/01/ghost.txt',
            file_size=1,
            content_type='text/plain',
            owner=company_member,
            company=company,
        )
        assert not os.path.exists(
            os.path.join(settings.MEDIA_ROOT, file_obj.file.name)
        )

        call_command('migrate_files_to_s3', '--dry-run')

        captured = capsys.readouterr()
        assert 'missing on local disk' in captured.err
        file_obj.refresh_from_db()
        assert file_obj.file.name == 'storage/2024/01/ghost.txt'

    def test_migrate_copies_local_file_to_s3(self, s3_settings, company_member, company, tmp_path):
        legacy_dir = tmp_path / 'media'
        legacy_dir.mkdir()
        legacy_rel = 'storage/2024/01/local.txt'
        (legacy_dir / 'storage' / '2024' / '01').mkdir(parents=True)
        (legacy_dir / 'storage' / '2024' / '01' / 'local.txt').write_bytes(b'migrate me')

        file_obj = File.objects.create(
            name='local.txt',
            file=legacy_rel,
            file_size=10,
            content_type='text/plain',
            owner=company_member,
            company=company,
        )

        with override_settings(MEDIA_ROOT=legacy_dir):
            call_command('migrate_files_to_s3')

        file_obj.refresh_from_db()
        assert file_obj.file.name.startswith('companies/')

        # Verify the object landed in the moto-intercepted bucket via a fresh
        # boto3 client.  We cannot rely on default_storage.exists() here because
        # S3Boto3Storage caches its internal boto3 resource/client in a
        # thread-local (S3Storage._connections) that may have been initialised
        # before the mock_aws context took full effect, leading to a stale
        # connection that bypasses moto.  A boto3 client created directly inside
        # the active mock_aws context is guaranteed to be intercepted by moto.
        s3 = boto3.client(
            's3',
            region_name='eu-central-1',
            aws_access_key_id='testing',
            aws_secret_access_key='testing',
        )
        keys = [
            obj['Key']
            for obj in s3.list_objects_v2(Bucket='test-bucket').get('Contents', [])
        ]
        assert file_obj.file.name in keys, (
            f"Expected {file_obj.file.name!r} in moto bucket, found: {keys}"
        )


@pytest.mark.django_db
class TestCleanupDeleteRetry:
    def test_delete_retries_before_failure(self, company_member, company):
        from botocore.exceptions import ClientError
        from unittest.mock import MagicMock, patch

        from apps.storage.tasks import _delete_fieldfile_with_retry

        mock_field = MagicMock()
        mock_field.name = 'companies/1/files/2024/01/x.txt'
        mock_field.delete.side_effect = ClientError(
            {'Error': {'Code': '500', 'Message': 'Error'}},
            'DeleteObject',
        )

        with patch('apps.storage.tasks.time.sleep'):
            with pytest.raises(ClientError):
                _delete_fieldfile_with_retry(mock_field, max_attempts=3)

        assert mock_field.delete.call_count == 3
