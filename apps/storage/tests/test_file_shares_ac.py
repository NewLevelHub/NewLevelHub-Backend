import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.notifications.models import Notification
from apps.storage.models import File, FileShare
from apps.users.models import User


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='Shares Co', plan='basic', storage_limit_gb=5)


@pytest.fixture
def another_company(db):
    return Company.objects.create(name='Other Shares Co', plan='basic', storage_limit_gb=5)


@pytest.fixture
def sharer(db, company):
    return User.objects.create_user(
        email='sharer@shares.co',
        password='pass',
        first_name='Share',
        last_name='Owner',
        role='employee',
        company=company,
    )


@pytest.fixture
def recipient(db, company):
    return User.objects.create_user(
        email='recipient@shares.co',
        password='pass',
        first_name='Share',
        last_name='Recipient',
        role='employee',
        company=company,
    )


@pytest.fixture
def foreign_user(db, another_company):
    return User.objects.create_user(
        email='foreign@shares.co',
        password='pass',
        first_name='Foreign',
        last_name='Recipient',
        role='employee',
        company=another_company,
    )


@pytest.fixture
def shared_file(sharer):
    return File.objects.create(
        name='shared-doc.txt',
        file=SimpleUploadedFile('shared-doc.txt', b'hello', content_type='text/plain'),
        file_size=5,
        content_type='text/plain',
        owner=sharer,
        company=sharer.company,
    )


def _shares_url():
    return '/api/v1/storage/shares/'


def _share_detail_url(share_id):
    return f'/api/v1/storage/shares/{share_id}/'


def _file_shares_url(file_id):
    return f'/api/v1/storage/files/{file_id}/shares/'


def _file_detail_url(file_id):
    return f'/api/v1/storage/files/{file_id}/'


def _file_download_url(file_id):
    return f'/api/v1/storage/files/{file_id}/download/'


@pytest.mark.django_db
class TestFileSharesAcceptanceCriteria:
    def test_post_create_share_accepts_frontend_payload_file_id_fields(
        self,
        api_client,
        sharer,
        recipient,
        shared_file,
    ):
        api_client.force_authenticate(user=sharer)

        response = api_client.post(
            _shares_url(),
            {
                'file_id': shared_file.id,
                'shared_with_user_id': recipient.id,
                'permission': 'download',
            },
            format='json',
        )

        assert response.status_code == status.HTTP_201_CREATED
        created = FileShare.objects.get(id=response.data['id'])
        assert created.file_id == shared_file.id
        assert created.shared_with_id == recipient.id
        assert response.data['file_id'] == shared_file.id
        assert response.data['shared_with_user_id'] == recipient.id

    def test_post_create_share_with_permission(self, api_client, sharer, recipient, shared_file):
        api_client.force_authenticate(user=sharer)

        response = api_client.post(
            _shares_url(),
            {
                'file': shared_file.id,
                'shared_with': recipient.id,
                'permission': 'download',
            },
            format='json',
        )

        assert response.status_code == status.HTTP_201_CREATED
        created = FileShare.objects.get(id=response.data['id'])
        assert created.file_id == shared_file.id
        assert created.shared_with_id == recipient.id
        assert created.permission == 'download'
        assert created.shared_by_id == sharer.id

    def test_post_create_share_to_another_company_user_returns_400(
        self,
        api_client,
        sharer,
        foreign_user,
        shared_file,
    ):
        api_client.force_authenticate(user=sharer)

        response = api_client.post(
            _shares_url(),
            {
                'file': shared_file.id,
                'shared_with': foreign_user.id,
                'permission': 'view',
            },
            format='json',
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not FileShare.objects.filter(file=shared_file, shared_with=foreign_user).exists()

    def test_get_shared_with_me_true_returns_only_my_shares(self, api_client, sharer, recipient, shared_file):
        another_user = User.objects.create_user(
            email='other@shares.co',
            password='pass',
            first_name='Other',
            last_name='User',
            role='employee',
            company=sharer.company,
        )
        FileShare.objects.create(file=shared_file, shared_by=sharer, shared_with=recipient, permission='view')
        FileShare.objects.create(file=shared_file, shared_by=sharer, shared_with=another_user, permission='view')
        api_client.force_authenticate(user=recipient)

        response = api_client.get(_shares_url(), {'shared_with_me': 'true'})

        assert response.status_code == status.HTTP_200_OK
        assert response.data['count'] == 1
        assert response.data['results'][0]['shared_with'] == recipient.id
        assert response.data['results'][0]['shared_by_name'] == sharer.full_name
        assert response.data['results'][0]['file_owner_name'] == sharer.full_name
        assert response.data['results'][0]['file_name'] == shared_file.name

    def test_get_file_shares_returns_who_file_is_shared_with(self, api_client, sharer, recipient, shared_file):
        FileShare.objects.create(file=shared_file, shared_by=sharer, shared_with=recipient, permission='full')
        api_client.force_authenticate(user=sharer)

        response = api_client.get(_file_shares_url(shared_file.id))

        assert response.status_code == status.HTTP_200_OK
        assert response.data['count'] == 1
        row = response.data['results'][0]
        assert row['file'] == shared_file.id
        assert row['shared_with'] == recipient.id
        assert row['permission'] == 'full'

    def test_patch_share_changes_permission(self, api_client, sharer, recipient, shared_file):
        share = FileShare.objects.create(file=shared_file, shared_by=sharer, shared_with=recipient, permission='view')
        api_client.force_authenticate(user=sharer)

        response = api_client.patch(_share_detail_url(share.id), {'permission': 'full'}, format='json')

        assert response.status_code == status.HTTP_200_OK
        share.refresh_from_db()
        assert share.permission == 'full'

    def test_delete_share_revokes_access(self, api_client, sharer, recipient, shared_file):
        share = FileShare.objects.create(file=shared_file, shared_by=sharer, shared_with=recipient, permission='download')
        api_client.force_authenticate(user=sharer)

        response = api_client.delete(_share_detail_url(share.id))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not FileShare.objects.filter(id=share.id).exists()

    def test_post_create_share_creates_notification_for_recipient(
        self,
        api_client,
        sharer,
        recipient,
        shared_file,
    ):
        api_client.force_authenticate(user=sharer)
        before = Notification.objects.filter(user=recipient).count()

        response = api_client.post(
            _shares_url(),
            {
                'file': shared_file.id,
                'shared_with': recipient.id,
                'permission': 'view',
            },
            format='json',
        )

        assert response.status_code == status.HTTP_201_CREATED
        after = Notification.objects.filter(user=recipient).count()
        assert after == before + 1

    def test_view_permission_allows_metadata_but_denies_download(self, api_client, sharer, recipient, shared_file):
        FileShare.objects.create(file=shared_file, shared_by=sharer, shared_with=recipient, permission='view')
        api_client.force_authenticate(user=recipient)

        metadata_response = api_client.get(_file_detail_url(shared_file.id))
        download_response = api_client.get(_file_download_url(shared_file.id))

        assert metadata_response.status_code == status.HTTP_200_OK
        assert download_response.status_code == status.HTTP_403_FORBIDDEN

    def test_download_permission_allows_file_download(self, api_client, sharer, recipient, shared_file):
        FileShare.objects.create(file=shared_file, shared_by=sharer, shared_with=recipient, permission='download')
        api_client.force_authenticate(user=recipient)

        response = api_client.get(_file_download_url(shared_file.id))

        assert response.status_code == status.HTTP_200_OK

    def test_full_permission_allows_rename_and_delete(self, api_client, sharer, recipient, shared_file):
        FileShare.objects.create(file=shared_file, shared_by=sharer, shared_with=recipient, permission='full')
        api_client.force_authenticate(user=recipient)

        rename_response = api_client.patch(_file_detail_url(shared_file.id), {'name': 'renamed.txt'}, format='json')
        delete_response = api_client.delete(_file_detail_url(shared_file.id))

        assert rename_response.status_code == status.HTTP_200_OK
        assert delete_response.status_code == status.HTTP_204_NO_CONTENT
