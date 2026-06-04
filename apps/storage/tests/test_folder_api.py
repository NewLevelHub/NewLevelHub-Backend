from django.core.files.uploadedfile import SimpleUploadedFile
import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.storage.models import File, Folder
from apps.users.models import User


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='Folder Co', plan='basic', storage_limit_gb=5)


@pytest.fixture
def another_company(db):
    return Company.objects.create(name='Other Co', plan='basic', storage_limit_gb=5)


@pytest.fixture
def company_admin(db, company):
    return User.objects.create_user(
        email='admin@folder.co',
        password='pass',
        first_name='Folder',
        last_name='Admin',
        role='company_admin',
        company=company,
    )


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='employee@folder.co',
        password='pass',
        first_name='Folder',
        last_name='Employee',
        role='employee',
        company=company,
    )


@pytest.fixture
def guest(db, company):
    return User.objects.create_user(
        email='guest@folder.co',
        password='pass',
        first_name='Folder',
        last_name='Guest',
        role='guest',
        company=company,
    )


@pytest.fixture
def foreign_employee(db, another_company):
    return User.objects.create_user(
        email='employee@other.co',
        password='pass',
        first_name='Other',
        last_name='Employee',
        role='employee',
        company=another_company,
    )


def _folders_url():
    return '/api/v1/storage/folders/'


def _folder_detail_url(folder_id):
    return f'/api/v1/storage/folders/{folder_id}/'


def _upload(name='doc.txt', size=16):
    return SimpleUploadedFile(name, b'a' * size, content_type='text/plain')


@pytest.mark.django_db
class TestFolderApiByAcceptanceCriteria:
    def test_post_create_personal_root_folder(self, api_client, employee):
        api_client.force_authenticate(user=employee)

        response = api_client.post(
            _folders_url(),
            {'name': 'Personal Root', 'is_company_shared': False},
            format='json',
        )

        assert response.status_code == status.HTTP_201_CREATED
        created = Folder.objects.get(id=response.data['id'])
        assert created.name == 'Personal Root'
        assert created.parent_id is None
        assert created.scope == 'personal'
        assert created.owner_id == employee.id
        assert created.company_id == employee.company_id

    def test_post_create_company_child_folder(self, api_client, employee):
        parent = Folder.objects.create(
            name='Company Root',
            scope='company',
            owner=employee,
            company=employee.company,
            parent=None,
        )
        api_client.force_authenticate(user=employee)

        response = api_client.post(
            _folders_url(),
            {'name': 'Company Child', 'parent_id': parent.id, 'is_company_shared': True},
            format='json',
        )

        assert response.status_code == status.HTTP_201_CREATED
        created = Folder.objects.get(id=response.data['id'])
        assert created.parent_id == parent.id
        assert created.scope == 'company'

    def test_get_personal_root_folders_by_scope_and_parent(self, api_client, employee):
        personal_root = Folder.objects.create(
            name='Personal Root',
            scope='personal',
            owner=employee,
            company=employee.company,
            parent=None,
        )
        Folder.objects.create(
            name='Personal Child',
            scope='personal',
            owner=employee,
            company=employee.company,
            parent=personal_root,
        )
        Folder.objects.create(
            name='Company Root',
            scope='company',
            owner=employee,
            company=employee.company,
            parent=None,
        )

        api_client.force_authenticate(user=employee)
        response = api_client.get(_folders_url(), {'parent_id': 'null', 'scope': 'personal'})

        assert response.status_code == status.HTTP_200_OK
        names = {row['name'] for row in response.data['results']}
        assert names == {'Personal Root'}

    def test_get_company_root_folders_by_scope_and_parent(self, api_client, employee, foreign_employee):
        Folder.objects.create(
            name='Company Root',
            scope='company',
            owner=employee,
            company=employee.company,
            parent=None,
        )
        Folder.objects.create(
            name='Foreign Company Root',
            scope='company',
            owner=foreign_employee,
            company=foreign_employee.company,
            parent=None,
        )
        Folder.objects.create(
            name='Personal Root',
            scope='personal',
            owner=employee,
            company=employee.company,
            parent=None,
        )

        api_client.force_authenticate(user=employee)
        response = api_client.get(_folders_url(), {'parent_id': 'null', 'scope': 'company'})

        assert response.status_code == status.HTTP_200_OK
        names = {row['name'] for row in response.data['results']}
        assert names == {'Company Root'}

    def test_get_folder_detail_contains_nested_folders_and_files(self, api_client, employee):
        root = Folder.objects.create(
            name='Root',
            scope='personal',
            owner=employee,
            company=employee.company,
            parent=None,
        )
        child = Folder.objects.create(
            name='Child',
            scope='personal',
            owner=employee,
            company=employee.company,
            parent=root,
        )
        file_obj = File.objects.create(
            name='readme.txt',
            file=_upload('readme.txt'),
            file_size=16,
            content_type='text/plain',
            folder=root,
            owner=employee,
            company=employee.company,
        )

        api_client.force_authenticate(user=employee)
        response = api_client.get(_folder_detail_url(root.id))

        assert response.status_code == status.HTTP_200_OK
        nested_folder_ids = {row['id'] for row in response.data['folders']}
        nested_file_ids = {row['id'] for row in response.data['files']}
        assert child.id in nested_folder_ids
        assert file_obj.id in nested_file_ids

    def test_patch_rename_folder(self, api_client, employee):
        folder = Folder.objects.create(
            name='Old Name',
            scope='personal',
            owner=employee,
            company=employee.company,
            parent=None,
        )
        api_client.force_authenticate(user=employee)

        response = api_client.patch(_folder_detail_url(folder.id), {'name': 'New Name'}, format='json')

        assert response.status_code == status.HTTP_200_OK
        folder.refresh_from_db()
        assert folder.name == 'New Name'

    def test_delete_folder_soft_deletes_folder(self, api_client, employee):
        folder = Folder.objects.create(
            name='To Delete',
            scope='personal',
            owner=employee,
            company=employee.company,
            parent=None,
        )
        api_client.force_authenticate(user=employee)

        response = api_client.delete(_folder_detail_url(folder.id))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        # Folder must not appear via the default manager (SoftDeleteManager).
        assert not Folder.objects.filter(id=folder.id).exists()
        # Folder must still exist in the DB (soft-deleted, not hard-deleted).
        folder.refresh_from_db()
        assert folder.is_deleted is True
        assert folder.deleted_at is not None

    def test_delete_folder_soft_deletes_nested_files(self, api_client, employee):
        folder = Folder.objects.create(
            name='Parent',
            scope='personal',
            owner=employee,
            company=employee.company,
            parent=None,
        )
        file_obj = File.objects.create(
            name='doc.txt',
            file=_upload('doc.txt'),
            file_size=16,
            content_type='text/plain',
            folder=folder,
            owner=employee,
            company=employee.company,
        )
        api_client.force_authenticate(user=employee)

        response = api_client.delete(_folder_detail_url(folder.id))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        # File must not appear via the default manager.
        assert not File.objects.filter(id=file_obj.id).exists()
        # File must still be in the DB with is_deleted=True.
        file_obj.refresh_from_db()
        assert file_obj.is_deleted is True

    def test_delete_folder_soft_deletes_nested_subfolders(self, api_client, employee):
        root = Folder.objects.create(
            name='Root',
            scope='personal',
            owner=employee,
            company=employee.company,
            parent=None,
        )
        child = Folder.objects.create(
            name='Child',
            scope='personal',
            owner=employee,
            company=employee.company,
            parent=root,
        )
        api_client.force_authenticate(user=employee)

        response = api_client.delete(_folder_detail_url(root.id))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        root.refresh_from_db()
        child.refresh_from_db()
        assert root.is_deleted is True
        assert child.is_deleted is True

    def test_delete_folder_recursive_deep(self, api_client, employee):
        """3-level nesting: root -> child -> grandchild, each level with a file."""
        root = Folder.objects.create(
            name='Root',
            scope='personal',
            owner=employee,
            company=employee.company,
            parent=None,
        )
        child = Folder.objects.create(
            name='Child',
            scope='personal',
            owner=employee,
            company=employee.company,
            parent=root,
        )
        grandchild = Folder.objects.create(
            name='Grandchild',
            scope='personal',
            owner=employee,
            company=employee.company,
            parent=child,
        )
        f1 = File.objects.create(
            name='f1.txt', file=_upload('f1.txt'), file_size=16,
            content_type='text/plain', folder=root, owner=employee, company=employee.company,
        )
        f2 = File.objects.create(
            name='f2.txt', file=_upload('f2.txt'), file_size=16,
            content_type='text/plain', folder=child, owner=employee, company=employee.company,
        )
        f3 = File.objects.create(
            name='f3.txt', file=_upload('f3.txt'), file_size=16,
            content_type='text/plain', folder=grandchild, owner=employee, company=employee.company,
        )
        api_client.force_authenticate(user=employee)

        response = api_client.delete(_folder_detail_url(root.id))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        for obj in [root, child, grandchild]:
            obj.refresh_from_db()
            assert obj.is_deleted is True
        for file_obj in [f1, f2, f3]:
            file_obj.refresh_from_db()
            assert file_obj.is_deleted is True

    def test_deleted_folder_not_in_list(self, api_client, employee):
        visible = Folder.objects.create(
            name='Visible',
            scope='personal',
            owner=employee,
            company=employee.company,
            parent=None,
        )
        to_delete = Folder.objects.create(
            name='Gone',
            scope='personal',
            owner=employee,
            company=employee.company,
            parent=None,
        )
        api_client.force_authenticate(user=employee)

        api_client.delete(_folder_detail_url(to_delete.id))

        response = api_client.get(_folders_url())
        assert response.status_code == status.HTTP_200_OK
        ids = {row['id'] for row in response.data['results']}
        assert visible.id in ids
        assert to_delete.id not in ids

    def test_permissions_unauthenticated_get_is_401(self, api_client):
        response = api_client.get(_folders_url())
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_permissions_guest_get_is_200(self, api_client, guest):
        # Guests may list their own personal folders.
        api_client.force_authenticate(user=guest)
        response = api_client.get(_folders_url())
        assert response.status_code == status.HTTP_200_OK
