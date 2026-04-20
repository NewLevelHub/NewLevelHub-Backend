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

    def test_delete_folder_recursive(self, api_client, employee):
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
        nested = Folder.objects.create(
            name='Nested',
            scope='personal',
            owner=employee,
            company=employee.company,
            parent=child,
        )
        file_obj = File.objects.create(
            name='doc.txt',
            file=_upload('doc.txt'),
            file_size=16,
            content_type='text/plain',
            folder=child,
            owner=employee,
            company=employee.company,
        )
        api_client.force_authenticate(user=employee)

        response = api_client.delete(_folder_detail_url(root.id))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not Folder.objects.filter(id__in=[root.id, child.id, nested.id]).exists()
        assert not File.objects.filter(id=file_obj.id).exists()

    def test_permissions_unauthenticated_get_is_401(self, api_client):
        response = api_client.get(_folders_url())
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_permissions_guest_get_is_403(self, api_client, guest):
        api_client.force_authenticate(user=guest)
        response = api_client.get(_folders_url())
        assert response.status_code == status.HTTP_403_FORBIDDEN
