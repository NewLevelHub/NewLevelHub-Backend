from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.storage.models import File, Folder, FileShare
from apps.users.models import User


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='Files Co', plan='basic', storage_limit_gb=5)


@pytest.fixture
def company_member(db, company):
    return User.objects.create_user(
        email='member@files.co',
        password='pass',
        first_name='File',
        last_name='Member',
        role='employee',
        company=company,
    )


@pytest.fixture
def company_admin(db, company):
    return User.objects.create_user(
        email='admin@files.co',
        password='pass',
        first_name='Company',
        last_name='Admin',
        role='company_admin',
        company=company,
    )


@pytest.fixture
def guest_user(db):
    return User.objects.create_user(
        email='guest@files.co',
        password='pass',
        first_name='Guest',
        last_name='User',
        role='guest',
        company=None,
    )


def _files_url():
    return '/api/v1/storage/files/'


def _file_detail_url(file_id):
    return f'/api/v1/storage/files/{file_id}/'


def _file_download_url(file_id):
    return f'/api/v1/storage/files/{file_id}/download/'


def _file_move_url(file_id):
    return f'/api/v1/storage/files/{file_id}/move/'


def _upload(name='doc.txt', size=16, content_type='text/plain'):
    return SimpleUploadedFile(name, b'a' * size, content_type=content_type)


@pytest.mark.django_db
class TestFileApiAcceptanceCriteria:
    def test_post_upload_with_folder_id_assigns_folder(self, api_client, company_member):
        target_folder = Folder.objects.create(
            name='Target',
            scope='personal',
            owner=company_member,
            company=company_member.company,
            parent=None,
        )
        api_client.force_authenticate(user=company_member)

        response = api_client.post(
            _files_url(),
            {
                'name': 'contract.pdf',
                'file': _upload('contract.pdf', size=128, content_type='application/pdf'),
                'folder_id': target_folder.id,
            },
            format='multipart',
        )

        assert response.status_code == status.HTTP_201_CREATED
        created = File.objects.get(id=response.data['id'])
        assert created.folder_id == target_folder.id

    def test_post_upload_larger_than_100mb_returns_400(self, api_client, company_member):
        oversized = _upload(
            'oversized.bin',
            size=100 * 1024 * 1024 + 1,
            content_type='application/octet-stream',
        )
        api_client.force_authenticate(user=company_member)

        response = api_client.post(
            _files_url(),
            {'name': 'oversized.bin', 'file': oversized},
            format='multipart',
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_post_upload_without_file_returns_400(self, api_client, company_member):
        api_client.force_authenticate(user=company_member)

        response = api_client.post(
            _files_url(),
            {'name': 'missing-file.txt'},
            format='multipart',
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_get_file_metadata_contains_ac_fields(self, api_client, company_member):
        file_obj = File.objects.create(
            name='report.pdf',
            file=_upload('report.pdf', size=64, content_type='application/pdf'),
            file_size=64,
            content_type='application/pdf',
            owner=company_member,
            company=company_member.company,
        )
        api_client.force_authenticate(user=company_member)

        response = api_client.get(_file_detail_url(file_obj.id))

        assert response.status_code == status.HTTP_200_OK
        assert 'name' in response.data
        assert 'size' in response.data
        assert 'mime_type' in response.data
        assert 'download_url' in response.data
        assert 'uploaded_by' in response.data
        assert 'created_at' in response.data

    def test_get_download_returns_attachment_disposition(self, api_client, company_member):
        file_obj = File.objects.create(
            name='manual.txt',
            file=_upload('manual.txt', size=32),
            file_size=32,
            content_type='text/plain',
            owner=company_member,
            company=company_member.company,
        )
        api_client.force_authenticate(user=company_member)

        response = api_client.get(_file_download_url(file_obj.id))

        assert response.status_code == status.HTTP_200_OK
        assert 'attachment' in response.headers.get('Content-Disposition', '')

    def test_patch_rename_file(self, api_client, company_member):
        file_obj = File.objects.create(
            name='old.txt',
            file=_upload('old.txt', size=16),
            file_size=16,
            content_type='text/plain',
            owner=company_member,
            company=company_member.company,
        )
        api_client.force_authenticate(user=company_member)

        response = api_client.patch(_file_detail_url(file_obj.id), {'name': 'new.txt'}, format='json')

        assert response.status_code == status.HTTP_200_OK
        file_obj.refresh_from_db()
        assert file_obj.name == 'new.txt'

    def test_post_move_file_to_another_folder(self, api_client, company_member):
        source_folder = Folder.objects.create(
            name='Source',
            scope='personal',
            owner=company_member,
            company=company_member.company,
            parent=None,
        )
        target_folder = Folder.objects.create(
            name='Target',
            scope='personal',
            owner=company_member,
            company=company_member.company,
            parent=None,
        )
        file_obj = File.objects.create(
            name='move-me.txt',
            file=_upload('move-me.txt', size=8),
            file_size=8,
            content_type='text/plain',
            owner=company_member,
            company=company_member.company,
            folder=source_folder,
        )
        api_client.force_authenticate(user=company_member)

        response = api_client.post(_file_move_url(file_obj.id), {'folder_id': target_folder.id}, format='json')

        assert response.status_code == status.HTTP_200_OK
        file_obj.refresh_from_db()
        assert file_obj.folder_id == target_folder.id

    def test_delete_soft_deletes_file(self, api_client, company_member):
        file_obj = File.objects.create(
            name='to-delete.txt',
            file=_upload('to-delete.txt', size=8),
            file_size=8,
            content_type='text/plain',
            owner=company_member,
            company=company_member.company,
        )
        api_client.force_authenticate(user=company_member)

        response = api_client.delete(_file_detail_url(file_obj.id))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        deleted = File.all_objects.get(id=file_obj.id)
        assert deleted.is_deleted is True
        assert deleted.deleted_at is not None

    def test_search_by_name(self, api_client, company_member):
        File.objects.create(
            name='quarterly-report.pdf',
            file=_upload('quarterly-report.pdf', size=10),
            file_size=10,
            content_type='application/pdf',
            owner=company_member,
            company=company_member.company,
        )
        File.objects.create(
            name='vacation-photo.jpg',
            file=_upload('vacation-photo.jpg', size=10, content_type='image/jpeg'),
            file_size=10,
            content_type='image/jpeg',
            owner=company_member,
            company=company_member.company,
        )
        api_client.force_authenticate(user=company_member)

        response = api_client.get(_files_url(), {'search': 'report'})

        assert response.status_code == status.HTTP_200_OK
        names = [item['name'] for item in response.data['results']]
        assert names == ['quarterly-report.pdf']

    def test_sort_by_name(self, api_client, company_member):
        File.objects.create(
            name='zeta.txt',
            file=_upload('zeta.txt', size=5),
            file_size=5,
            content_type='text/plain',
            owner=company_member,
            company=company_member.company,
        )
        File.objects.create(
            name='alpha.txt',
            file=_upload('alpha.txt', size=10),
            file_size=10,
            content_type='text/plain',
            owner=company_member,
            company=company_member.company,
        )
        api_client.force_authenticate(user=company_member)

        response = api_client.get(_files_url(), {'ordering': 'name'})

        assert response.status_code == status.HTTP_200_OK
        names = [item['name'] for item in response.data['results']]
        assert names == ['alpha.txt', 'zeta.txt']

    def test_sort_by_created_at_desc(self, api_client, company_member):
        older = File.objects.create(
            name='older.txt',
            file=_upload('older.txt', size=7),
            file_size=7,
            content_type='text/plain',
            owner=company_member,
            company=company_member.company,
        )
        newer = File.objects.create(
            name='newer.txt',
            file=_upload('newer.txt', size=9),
            file_size=9,
            content_type='text/plain',
            owner=company_member,
            company=company_member.company,
        )
        File.all_objects.filter(id=older.id).update(created_at=timezone.now() - timedelta(days=1))
        File.all_objects.filter(id=newer.id).update(created_at=timezone.now())
        api_client.force_authenticate(user=company_member)

        response = api_client.get(_files_url(), {'ordering': '-created_at'})

        assert response.status_code == status.HTTP_200_OK
        names = [item['name'] for item in response.data['results']]
        assert names == ['newer.txt', 'older.txt']

    def test_sort_by_size(self, api_client, company_member):
        File.objects.create(
            name='big.txt',
            file=_upload('big.txt', size=30),
            file_size=30,
            content_type='text/plain',
            owner=company_member,
            company=company_member.company,
        )
        File.objects.create(
            name='small.txt',
            file=_upload('small.txt', size=3),
            file_size=3,
            content_type='text/plain',
            owner=company_member,
            company=company_member.company,
        )
        api_client.force_authenticate(user=company_member)

        response = api_client.get(_files_url(), {'ordering': 'size'})

        assert response.status_code == status.HTTP_200_OK
        names = [item['name'] for item in response.data['results']]
        assert names == ['small.txt', 'big.txt']

    def test_celery_cleanup_removes_soft_deleted_files_after_30_days(self):
        try:
            from apps.storage import tasks as storage_tasks
        except Exception as exc:  # pragma: no cover - explicit AC failure message
            pytest.fail(f'Expected apps.storage.tasks module with cleanup task: {exc}')

        cleanup_deleted_files = getattr(storage_tasks, 'cleanup_deleted_files', None)
        assert cleanup_deleted_files is not None

        company = Company.objects.create(name='Cleanup Co', plan='basic', storage_limit_gb=5)
        owner = User.objects.create_user(
            email='cleanup@files.co',
            password='pass',
            first_name='Clean',
            last_name='Up',
            role='employee',
            company=company,
        )
        old_deleted = File.all_objects.create(
            name='old-deleted.txt',
            file=_upload('old-deleted.txt', size=1),
            file_size=1,
            content_type='text/plain',
            owner=owner,
            company=company,
            is_deleted=True,
            deleted_at=timezone.now() - timedelta(days=31),
        )
        recent_deleted = File.all_objects.create(
            name='recent-deleted.txt',
            file=_upload('recent-deleted.txt', size=1),
            file_size=1,
            content_type='text/plain',
            owner=owner,
            company=company,
            is_deleted=True,
            deleted_at=timezone.now() - timedelta(days=5),
        )

        cleanup_deleted_files()

        assert not File.all_objects.filter(id=old_deleted.id).exists()
        assert File.all_objects.filter(id=recent_deleted.id).exists()

    def test_guest_cannot_access_storage_files_list(self, api_client, guest_user):
        api_client.force_authenticate(user=guest_user)

        response = api_client.get(_files_url())

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_company_admin_does_not_see_other_users_personal_files(self, api_client, company_member, company_admin):
        File.objects.create(
            name='private-notes.txt',
            file=_upload('private-notes.txt', size=12),
            file_size=12,
            content_type='text/plain',
            owner=company_member,
            company=company_member.company,
            folder=Folder.objects.create(
                name='Private',
                scope='personal',
                owner=company_member,
                company=company_member.company,
            ),
        )
        api_client.force_authenticate(user=company_admin)

        response = api_client.get(_files_url(), {'search': 'private'})

        assert response.status_code == status.HTTP_200_OK
        assert response.data['count'] == 0
        assert response.data['results'] == []

    def test_company_member_sees_company_files_but_not_other_personal_files(
        self,
        api_client,
        company_member,
        company_admin,
    ):
        File.objects.create(
            name='team-doc.txt',
            file=_upload('team-doc.txt', size=12),
            file_size=12,
            content_type='text/plain',
            owner=company_admin,
            company=company_member.company,
            folder=Folder.objects.create(
                name='Team',
                scope='company',
                owner=company_admin,
                company=company_member.company,
            ),
        )
        File.objects.create(
            name='admin-private.txt',
            file=_upload('admin-private.txt', size=12),
            file_size=12,
            content_type='text/plain',
            owner=company_admin,
            company=company_member.company,
            folder=Folder.objects.create(
                name='Admin private',
                scope='personal',
                owner=company_admin,
                company=company_member.company,
            ),
        )
        api_client.force_authenticate(user=company_member)

        response = api_client.get(_files_url())

        assert response.status_code == status.HTTP_200_OK
        names = {item['name'] for item in response.data['results']}
        assert 'team-doc.txt' in names
        assert 'admin-private.txt' not in names

    def test_shared_personal_file_is_visible_to_recipient_in_search(self, api_client, company_member, company_admin):
        personal_file = File.objects.create(
            name='payroll-private.txt',
            file=_upload('payroll-private.txt', size=12),
            file_size=12,
            content_type='text/plain',
            owner=company_admin,
            company=company_member.company,
            folder=Folder.objects.create(
                name='Admin private',
                scope='personal',
                owner=company_admin,
                company=company_member.company,
            ),
        )
        FileShare.objects.create(
            file=personal_file,
            shared_by=company_admin,
            shared_with=company_member,
            permission='view',
        )
        api_client.force_authenticate(user=company_member)

        response = api_client.get(_files_url(), {'search': 'payroll'})

        assert response.status_code == status.HTTP_200_OK
        names = [item['name'] for item in response.data['results']]
        assert names == ['payroll-private.txt']

    def test_company_member_can_download_company_file_without_explicit_share(
        self,
        api_client,
        company_member,
        company_admin,
    ):
        company_file = File.objects.create(
            name='team-guide.txt',
            file=_upload('team-guide.txt', size=24),
            file_size=24,
            content_type='text/plain',
            owner=company_admin,
            company=company_member.company,
            folder=Folder.objects.create(
                name='Company docs',
                scope='company',
                owner=company_admin,
                company=company_member.company,
            ),
        )
        api_client.force_authenticate(user=company_member)

        response = api_client.get(_file_download_url(company_file.id))

        assert response.status_code == status.HTTP_200_OK
        assert 'attachment' in response.headers.get('Content-Disposition', '')

    def test_company_member_cannot_modify_company_file_of_another_user(
        self,
        api_client,
        company_member,
        company_admin,
    ):
        company_file = File.objects.create(
            name='team-rules.txt',
            file=_upload('team-rules.txt', size=24),
            file_size=24,
            content_type='text/plain',
            owner=company_admin,
            company=company_member.company,
            folder=Folder.objects.create(
                name='Company docs',
                scope='company',
                owner=company_admin,
                company=company_member.company,
            ),
        )
        api_client.force_authenticate(user=company_member)

        response = api_client.patch(
            _file_detail_url(company_file.id),
            {'name': 'hacked-name.txt'},
            format='json',
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
