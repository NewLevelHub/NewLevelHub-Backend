"""
Integration tests for FolderPermission ACL system (§4.4).

Coverage:
- Model DB constraint (user XOR role)
- FolderSerializer.is_restricted field
- _folder_access_level helper (open/closed/user-specific/role-based)
- FolderViewSet.get_queryset — employee sees open + explicitly-permitted restricted folders
- FolderViewSet.retrieve — child_folders filtered by ACL for employee
- FileViewSet.get_queryset — files in inaccessible restricted folders are hidden
- FileViewSet._resolve_folder — upload blocked when level < 'upload'
- FileViewSet._ensure_file_permission — folder ACL checked on view/download/full
- GET  /folders/{id}/permissions/  — folder_permissions action (GET branch)
- POST /folders/{id}/permissions/  — folder_permissions action (POST branch)
- PATCH/DELETE /folder-permissions/{id}/ — FolderPermissionViewSet
"""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.storage.models import File, Folder, FolderPermission
from apps.storage.views import _folder_access_level
from apps.users.models import User


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='Perm Co', plan='basic', storage_limit_gb=5)


@pytest.fixture
def another_company(db):
    return Company.objects.create(name='Other Co', plan='basic', storage_limit_gb=5)


@pytest.fixture
def admin(db, company):
    return User.objects.create_user(
        email='admin@perm.co', password='pass',
        first_name='Perm', last_name='Admin',
        role='company_admin', company=company,
    )


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='emp@perm.co', password='pass',
        first_name='Perm', last_name='Employee',
        role='employee', company=company,
    )


@pytest.fixture
def employee2(db, company):
    return User.objects.create_user(
        email='emp2@perm.co', password='pass',
        first_name='Perm', last_name='Employee2',
        role='employee', company=company,
    )


@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email='super@perm.co', password='pass',
        first_name='Super', last_name='Admin',
        role='superadmin', company=None,
    )


@pytest.fixture
def guest(db):
    return User.objects.create_user(
        email='guest@perm.co', password='pass',
        first_name='Guest', last_name='User',
        role='guest', company=None,
    )


@pytest.fixture
def company_folder(db, company, admin):
    return Folder.objects.create(
        name='Shared Folder', scope='company',
        owner=admin, company=company,
    )


@pytest.fixture
def personal_folder(db, employee):
    return Folder.objects.create(
        name='Personal', scope='personal',
        owner=employee, company=employee.company,
    )


def _upload(name='doc.txt', size=16):
    return SimpleUploadedFile(name, b'x' * size, content_type='text/plain')


def _make_file(folder, owner, name='file.txt'):
    return File.objects.create(
        name=name,
        file=_upload(name),
        file_size=16,
        content_type='text/plain',
        folder=folder,
        owner=owner,
        company=owner.company,
    )


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------

def _folder_permissions_url(folder_id):
    return f'/api/v1/storage/folders/{folder_id}/permissions/'


def _folder_permission_detail_url(perm_id):
    return f'/api/v1/storage/folder-permissions/{perm_id}/'


def _folders_url():
    return '/api/v1/storage/folders/'


def _folder_detail_url(folder_id):
    return f'/api/v1/storage/folders/{folder_id}/'


def _files_url():
    return '/api/v1/storage/files/'


def _file_download_url(file_id):
    return f'/api/v1/storage/files/{file_id}/download/'


def _file_detail_url(file_id):
    return f'/api/v1/storage/files/{file_id}/'


# ---------------------------------------------------------------------------
# Model constraint
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestFolderPermissionModelConstraint:
    def test_user_only_is_valid(self, company_folder, employee, admin):
        perm = FolderPermission.objects.create(
            folder=company_folder, user=employee, permission='view', granted_by=admin,
        )
        assert perm.pk is not None

    def test_role_only_is_valid(self, company_folder, admin):
        perm = FolderPermission.objects.create(
            folder=company_folder, role='employee', permission='upload', granted_by=admin,
        )
        assert perm.pk is not None

    def test_both_user_and_role_violates_constraint(self, company_folder, employee, admin):
        with pytest.raises(IntegrityError):
            FolderPermission.objects.create(
                folder=company_folder, user=employee, role='employee',
                permission='view', granted_by=admin,
            )

    def test_neither_user_nor_role_violates_constraint(self, company_folder, admin):
        with pytest.raises(IntegrityError):
            FolderPermission.objects.create(
                folder=company_folder, user=None, role=None,
                permission='view', granted_by=admin,
            )


# ---------------------------------------------------------------------------
# _folder_access_level helper
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestFolderAccessLevel:
    def test_superadmin_always_gets_full(self, superadmin, company_folder):
        assert _folder_access_level(superadmin, company_folder) == 'full'

    def test_personal_folder_owner_gets_full(self, employee, personal_folder):
        assert _folder_access_level(employee, personal_folder) == 'full'

    def test_personal_folder_non_owner_gets_none(self, employee2, personal_folder):
        assert _folder_access_level(employee2, personal_folder) is None

    def test_company_admin_gets_full_on_company_folder(self, admin, company_folder):
        assert _folder_access_level(admin, company_folder) == 'full'

    def test_open_folder_no_perms_employee_gets_upload(self, employee, company_folder):
        # No FolderPermission rows → open folder → view + upload, but not full
        assert _folder_access_level(employee, company_folder) == 'upload'

    def test_restricted_folder_with_user_perm(self, employee, company_folder, admin):
        FolderPermission.objects.create(
            folder=company_folder, user=employee, permission='upload', granted_by=admin,
        )
        assert _folder_access_level(employee, company_folder) == 'upload'

    def test_restricted_folder_with_role_perm(self, employee, company_folder, admin):
        FolderPermission.objects.create(
            folder=company_folder, role='employee', permission='view', granted_by=admin,
        )
        assert _folder_access_level(employee, company_folder) == 'view'

    def test_user_perm_overrides_role_perm(self, employee, company_folder, admin):
        FolderPermission.objects.create(
            folder=company_folder, role='employee', permission='view', granted_by=admin,
        )
        FolderPermission.objects.create(
            folder=company_folder, user=employee, permission='full', granted_by=admin,
        )
        assert _folder_access_level(employee, company_folder) == 'full'

    def test_restricted_folder_no_matching_perm_returns_none(self, employee, employee2, company_folder, admin):
        # Only employee2 has access — employee should get None
        FolderPermission.objects.create(
            folder=company_folder, user=employee2, permission='view', granted_by=admin,
        )
        assert _folder_access_level(employee, company_folder) is None

    def test_wrong_company_employee_gets_none(self, another_company, company_folder):
        other_emp = User.objects.create_user(
            email='other@other.co', password='pass',
            first_name='O', last_name='E',
            role='employee', company=another_company,
        )
        assert _folder_access_level(other_emp, company_folder) is None


# ---------------------------------------------------------------------------
# FolderSerializer.is_restricted
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestFolderSerializerIsRestricted:
    def test_open_folder_is_restricted_false(self, api_client, employee, company_folder):
        api_client.force_authenticate(user=employee)
        response = api_client.get(_folder_detail_url(company_folder.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data['is_restricted'] is False

    def test_closed_folder_is_restricted_true(self, api_client, employee, company_folder, admin):
        FolderPermission.objects.create(
            folder=company_folder, user=employee, permission='view', granted_by=admin,
        )
        api_client.force_authenticate(user=admin)
        response = api_client.get(_folder_detail_url(company_folder.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data['is_restricted'] is True

    def test_is_restricted_present_in_folder_list(self, api_client, employee, company_folder):
        api_client.force_authenticate(user=employee)
        response = api_client.get(_folders_url())
        assert response.status_code == status.HTTP_200_OK
        assert any(r['id'] == company_folder.id for r in response.data['results'])
        folder_data = next(r for r in response.data['results'] if r['id'] == company_folder.id)
        assert 'is_restricted' in folder_data


# ---------------------------------------------------------------------------
# FolderViewSet.get_queryset — employee visibility
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestFolderQuerysetACL:
    def test_employee_sees_open_company_folder(self, api_client, employee, company_folder):
        api_client.force_authenticate(user=employee)
        response = api_client.get(_folders_url(), {'scope': 'company'})
        ids = {r['id'] for r in response.data['results']}
        assert company_folder.id in ids

    def test_employee_cannot_see_restricted_folder_without_perm(
        self, api_client, employee, employee2, company_folder, admin,
    ):
        # Restrict folder — only employee2 has access
        FolderPermission.objects.create(
            folder=company_folder, user=employee2, permission='view', granted_by=admin,
        )
        api_client.force_authenticate(user=employee)
        response = api_client.get(_folders_url(), {'scope': 'company'})
        ids = {r['id'] for r in response.data['results']}
        assert company_folder.id not in ids

    def test_employee_sees_restricted_folder_when_explicitly_permitted(
        self, api_client, employee, company_folder, admin,
    ):
        # Restrict folder and grant access to employee
        FolderPermission.objects.create(
            folder=company_folder, user=employee, permission='view', granted_by=admin,
        )
        api_client.force_authenticate(user=employee)
        response = api_client.get(_folders_url(), {'scope': 'company'})
        ids = {r['id'] for r in response.data['results']}
        assert company_folder.id in ids

    def test_employee_sees_restricted_folder_by_role_perm(
        self, api_client, employee, company_folder, admin,
    ):
        FolderPermission.objects.create(
            folder=company_folder, role='employee', permission='upload', granted_by=admin,
        )
        api_client.force_authenticate(user=employee)
        response = api_client.get(_folders_url(), {'scope': 'company'})
        ids = {r['id'] for r in response.data['results']}
        assert company_folder.id in ids

    def test_company_admin_sees_all_company_folders_including_restricted(
        self, api_client, admin, employee, company_folder, employee2,
    ):
        # Restrict folder — admin should still see it
        FolderPermission.objects.create(
            folder=company_folder, user=employee2, permission='view', granted_by=admin,
        )
        api_client.force_authenticate(user=admin)
        response = api_client.get(_folders_url(), {'scope': 'company'})
        ids = {r['id'] for r in response.data['results']}
        assert company_folder.id in ids


# ---------------------------------------------------------------------------
# FolderViewSet.retrieve — child folders filtered by ACL
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestFolderRetrieveChildACL:
    def test_employee_sees_open_child_folder(self, api_client, employee, company_folder, admin):
        child = Folder.objects.create(
            name='Open Child', scope='company',
            owner=admin, company=admin.company, parent=company_folder,
        )
        api_client.force_authenticate(user=employee)
        response = api_client.get(_folder_detail_url(company_folder.id))
        child_ids = {f['id'] for f in response.data['folders']}
        assert child.id in child_ids

    def test_employee_cannot_see_restricted_child_without_perm(
        self, api_client, employee, employee2, company_folder, admin,
    ):
        child = Folder.objects.create(
            name='Restricted Child', scope='company',
            owner=admin, company=admin.company, parent=company_folder,
        )
        # Grant only employee2 access to child
        FolderPermission.objects.create(
            folder=child, user=employee2, permission='view', granted_by=admin,
        )
        api_client.force_authenticate(user=employee)
        response = api_client.get(_folder_detail_url(company_folder.id))
        child_ids = {f['id'] for f in response.data['folders']}
        assert child.id not in child_ids

    def test_employee_sees_restricted_child_when_permitted(
        self, api_client, employee, company_folder, admin,
    ):
        child = Folder.objects.create(
            name='My Restricted Child', scope='company',
            owner=admin, company=admin.company, parent=company_folder,
        )
        FolderPermission.objects.create(
            folder=child, user=employee, permission='view', granted_by=admin,
        )
        api_client.force_authenticate(user=employee)
        response = api_client.get(_folder_detail_url(company_folder.id))
        child_ids = {f['id'] for f in response.data['folders']}
        assert child.id in child_ids

    def test_child_folders_have_is_restricted_field(self, api_client, admin, company_folder):
        Folder.objects.create(
            name='Child A', scope='company',
            owner=admin, company=admin.company, parent=company_folder,
        )
        api_client.force_authenticate(user=admin)
        response = api_client.get(_folder_detail_url(company_folder.id))
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['folders']) > 0
        assert 'is_restricted' in response.data['folders'][0]


# ---------------------------------------------------------------------------
# FileViewSet.get_queryset — files hidden in inaccessible restricted folders
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestFileQuerysetACL:
    def test_employee_sees_files_in_open_folder(self, api_client, employee, company_folder, admin):
        f = _make_file(company_folder, admin, 'open-file.txt')
        api_client.force_authenticate(user=employee)
        response = api_client.get(_files_url())
        ids = {r['id'] for r in response.data['results']}
        assert f.id in ids

    def test_employee_cannot_see_files_in_restricted_inaccessible_folder(
        self, api_client, employee, employee2, company_folder, admin,
    ):
        # Restrict folder — only employee2 has access
        FolderPermission.objects.create(
            folder=company_folder, user=employee2, permission='view', granted_by=admin,
        )
        f = _make_file(company_folder, admin, 'secret.txt')
        api_client.force_authenticate(user=employee)
        response = api_client.get(_files_url())
        ids = {r['id'] for r in response.data['results']}
        assert f.id not in ids

    def test_employee_sees_files_in_restricted_folder_when_permitted(
        self, api_client, employee, company_folder, admin,
    ):
        FolderPermission.objects.create(
            folder=company_folder, user=employee, permission='view', granted_by=admin,
        )
        f = _make_file(company_folder, admin, 'visible.txt')
        api_client.force_authenticate(user=employee)
        response = api_client.get(_files_url())
        ids = {r['id'] for r in response.data['results']}
        assert f.id in ids

    def test_company_admin_sees_all_files_regardless_of_restrictions(
        self, api_client, admin, employee, company_folder,
    ):
        FolderPermission.objects.create(
            folder=company_folder, user=employee, permission='view', granted_by=admin,
        )
        f = _make_file(company_folder, admin, 'admin-visible.txt')
        api_client.force_authenticate(user=admin)
        response = api_client.get(_files_url())
        ids = {r['id'] for r in response.data['results']}
        assert f.id in ids


# ---------------------------------------------------------------------------
# FileViewSet._ensure_file_permission — folder ACL gates on retrieve/download/patch
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestFilePermissionViaFolderACL:
    def test_employee_with_view_perm_can_retrieve_file(
        self, api_client, employee, company_folder, admin,
    ):
        FolderPermission.objects.create(
            folder=company_folder, user=employee, permission='view', granted_by=admin,
        )
        f = _make_file(company_folder, admin, 'viewable.txt')
        api_client.force_authenticate(user=employee)
        response = api_client.get(_file_detail_url(f.id))
        assert response.status_code == status.HTTP_200_OK

    def test_employee_with_no_perm_cannot_retrieve_file_in_restricted_folder(
        self, api_client, employee, employee2, company_folder, admin,
    ):
        # Restrict folder to employee2 only
        FolderPermission.objects.create(
            folder=company_folder, user=employee2, permission='view', granted_by=admin,
        )
        f = _make_file(company_folder, admin, 'blocked.txt')
        api_client.force_authenticate(user=employee)
        response = api_client.get(_file_detail_url(f.id))
        # File is absent from employee's queryset (restricted folder) → 404, not 403,
        # so we don't leak the existence of the file.
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_employee_with_view_perm_cannot_patch_file(
        self, api_client, employee, company_folder, admin,
    ):
        FolderPermission.objects.create(
            folder=company_folder, user=employee, permission='view', granted_by=admin,
        )
        f = _make_file(company_folder, admin, 'readonly.txt')
        api_client.force_authenticate(user=employee)
        response = api_client.patch(_file_detail_url(f.id), {'name': 'hacked.txt'}, format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_employee_with_full_perm_can_patch_file(
        self, api_client, employee, company_folder, admin,
    ):
        FolderPermission.objects.create(
            folder=company_folder, user=employee, permission='full', granted_by=admin,
        )
        f = _make_file(company_folder, admin, 'editable.txt')
        api_client.force_authenticate(user=employee)
        response = api_client.patch(_file_detail_url(f.id), {'name': 'renamed.txt'}, format='json')
        assert response.status_code == status.HTTP_200_OK

    def test_employee_with_view_perm_can_download_file(
        self, api_client, employee, company_folder, admin,
    ):
        FolderPermission.objects.create(
            folder=company_folder, user=employee, permission='view', granted_by=admin,
        )
        f = _make_file(company_folder, admin, 'downloadable.txt')
        api_client.force_authenticate(user=employee)
        response = api_client.get(_file_download_url(f.id))
        assert response.status_code == status.HTTP_200_OK


# ---------------------------------------------------------------------------
# FileViewSet._resolve_folder — upload permission check
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestFileUploadFolderACL:
    def test_employee_with_upload_perm_can_upload_to_restricted_folder(
        self, api_client, employee, company_folder, admin,
    ):
        FolderPermission.objects.create(
            folder=company_folder, user=employee, permission='upload', granted_by=admin,
        )
        api_client.force_authenticate(user=employee)
        response = api_client.post(
            _files_url(),
            {'name': 'upload.txt', 'file': _upload('upload.txt'), 'folder_id': company_folder.id},
            format='multipart',
        )
        assert response.status_code == status.HTTP_201_CREATED

    def test_employee_with_view_only_perm_cannot_upload_to_restricted_folder(
        self, api_client, employee, company_folder, admin,
    ):
        FolderPermission.objects.create(
            folder=company_folder, user=employee, permission='view', granted_by=admin,
        )
        api_client.force_authenticate(user=employee)
        response = api_client.post(
            _files_url(),
            {'name': 'blocked.txt', 'file': _upload('blocked.txt'), 'folder_id': company_folder.id},
            format='multipart',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_employee_with_no_perm_cannot_upload_to_restricted_folder(
        self, api_client, employee, employee2, company_folder, admin,
    ):
        FolderPermission.objects.create(
            folder=company_folder, user=employee2, permission='full', granted_by=admin,
        )
        api_client.force_authenticate(user=employee)
        response = api_client.post(
            _files_url(),
            {'name': 'nope.txt', 'file': _upload('nope.txt'), 'folder_id': company_folder.id},
            format='multipart',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_company_admin_can_always_upload_to_any_folder(
        self, api_client, admin, company_folder, employee,
    ):
        # Folder is restricted to employee only — admin ignores ACL
        FolderPermission.objects.create(
            folder=company_folder, user=employee, permission='view', granted_by=admin,
        )
        api_client.force_authenticate(user=admin)
        response = api_client.post(
            _files_url(),
            {'name': 'admin-upload.txt', 'file': _upload('admin-upload.txt'), 'folder_id': company_folder.id},
            format='multipart',
        )
        assert response.status_code == status.HTTP_201_CREATED

    def test_employee_can_upload_to_open_folder(self, api_client, employee, company_folder):
        # No permissions → open folder → upload allowed
        api_client.force_authenticate(user=employee)
        response = api_client.post(
            _files_url(),
            {'name': 'open-upload.txt', 'file': _upload('open-upload.txt'), 'folder_id': company_folder.id},
            format='multipart',
        )
        assert response.status_code == status.HTTP_201_CREATED


# ---------------------------------------------------------------------------
# GET /folders/{id}/permissions/ — folder_permissions action (GET branch)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestListPermissionsAction:
    def test_admin_can_list_permissions(self, api_client, admin, company_folder, employee):
        FolderPermission.objects.create(
            folder=company_folder, user=employee, permission='view', granted_by=admin,
        )
        api_client.force_authenticate(user=admin)
        response = api_client.get(_folder_permissions_url(company_folder.id))
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]['permission'] == 'view'

    def test_response_contains_expected_fields(self, api_client, admin, company_folder, employee):
        FolderPermission.objects.create(
            folder=company_folder, user=employee, permission='upload', granted_by=admin,
        )
        api_client.force_authenticate(user=admin)
        response = api_client.get(_folder_permissions_url(company_folder.id))
        assert response.status_code == status.HTTP_200_OK
        item = response.data[0]
        for field in ('id', 'folder', 'user', 'role', 'permission', 'granted_by', 'created_at'):
            assert field in item

    def test_employee_cannot_list_permissions(self, api_client, employee, company_folder):
        api_client.force_authenticate(user=employee)
        response = api_client.get(_folder_permissions_url(company_folder.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_unauthenticated_cannot_list_permissions(self, api_client, company_folder):
        response = api_client.get(_folder_permissions_url(company_folder.id))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_superadmin_can_list_permissions(self, api_client, superadmin, company_folder, admin, employee):
        FolderPermission.objects.create(
            folder=company_folder, user=employee, permission='full', granted_by=admin,
        )
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(_folder_permissions_url(company_folder.id))
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

    def test_empty_list_when_no_permissions(self, api_client, admin, company_folder):
        api_client.force_authenticate(user=admin)
        response = api_client.get(_folder_permissions_url(company_folder.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data == []


# ---------------------------------------------------------------------------
# POST /folders/{id}/permissions/ — folder_permissions action (POST branch)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAddPermissionAction:
    def test_admin_can_grant_user_permission(self, api_client, admin, company_folder, employee):
        api_client.force_authenticate(user=admin)
        response = api_client.post(
            _folder_permissions_url(company_folder.id),
            {'user': employee.id, 'permission': 'upload'},
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert FolderPermission.objects.filter(folder=company_folder, user=employee).exists()

    def test_admin_can_grant_role_permission(self, api_client, admin, company_folder):
        api_client.force_authenticate(user=admin)
        response = api_client.post(
            _folder_permissions_url(company_folder.id),
            {'role': 'employee', 'permission': 'view'},
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert FolderPermission.objects.filter(folder=company_folder, role='employee').exists()

    def test_granted_by_is_set_to_request_user(self, api_client, admin, company_folder, employee):
        api_client.force_authenticate(user=admin)
        api_client.post(
            _folder_permissions_url(company_folder.id),
            {'user': employee.id, 'permission': 'view'},
            format='json',
        )
        perm = FolderPermission.objects.get(folder=company_folder, user=employee)
        assert perm.granted_by_id == admin.id

    def test_cannot_set_both_user_and_role(self, api_client, admin, company_folder, employee):
        api_client.force_authenticate(user=admin)
        response = api_client.post(
            _folder_permissions_url(company_folder.id),
            {'user': employee.id, 'role': 'employee', 'permission': 'view'},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_cannot_set_neither_user_nor_role(self, api_client, admin, company_folder):
        api_client.force_authenticate(user=admin)
        response = api_client.post(
            _folder_permissions_url(company_folder.id),
            {'permission': 'view'},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_cannot_grant_permission_to_user_from_different_company(
        self, api_client, admin, company_folder, another_company,
    ):
        outsider = User.objects.create_user(
            email='outsider@other.co', password='pass',
            first_name='Out', last_name='Sider',
            role='employee', company=another_company,
        )
        api_client.force_authenticate(user=admin)
        response = api_client.post(
            _folder_permissions_url(company_folder.id),
            {'user': outsider.id, 'permission': 'view'},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_employee_cannot_add_permission(self, api_client, employee, company_folder, employee2):
        api_client.force_authenticate(user=employee)
        response = api_client.post(
            _folder_permissions_url(company_folder.id),
            {'user': employee2.id, 'permission': 'view'},
            format='json',
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_unauthenticated_cannot_add_permission(self, api_client, company_folder, employee):
        response = api_client.post(
            _folder_permissions_url(company_folder.id),
            {'user': employee.id, 'permission': 'view'},
            format='json',
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


# ---------------------------------------------------------------------------
# PATCH /folder-permissions/{id}/ — update permission level
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestFolderPermissionViewSetPatch:
    def test_admin_can_update_permission_level(self, api_client, admin, company_folder, employee):
        perm = FolderPermission.objects.create(
            folder=company_folder, user=employee, permission='view', granted_by=admin,
        )
        api_client.force_authenticate(user=admin)
        response = api_client.patch(
            _folder_permission_detail_url(perm.id),
            {'permission': 'full'},
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        perm.refresh_from_db()
        assert perm.permission == 'full'

    def test_employee_cannot_patch_permission(self, api_client, employee, admin, company_folder):
        perm = FolderPermission.objects.create(
            folder=company_folder, user=employee, permission='view', granted_by=admin,
        )
        api_client.force_authenticate(user=employee)
        response = api_client.patch(
            _folder_permission_detail_url(perm.id),
            {'permission': 'full'},
            format='json',
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_admin_cannot_patch_permission_of_other_company(
        self, api_client, admin, another_company,
    ):
        other_admin = User.objects.create_user(
            email='otheradmin@other.co', password='pass',
            first_name='Other', last_name='Admin',
            role='company_admin', company=another_company,
        )
        other_folder = Folder.objects.create(
            name='Other Folder', scope='company',
            owner=other_admin, company=another_company,
        )
        other_emp = User.objects.create_user(
            email='otheremp@other.co', password='pass',
            first_name='Other', last_name='Emp',
            role='employee', company=another_company,
        )
        perm = FolderPermission.objects.create(
            folder=other_folder, user=other_emp, permission='view', granted_by=other_admin,
        )
        api_client.force_authenticate(user=admin)
        response = api_client.patch(
            _folder_permission_detail_url(perm.id),
            {'permission': 'full'},
            format='json',
        )
        # Returns 404 because the permission is scoped to own company only
        assert response.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# DELETE /folder-permissions/{id}/ — revoke permission
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestFolderPermissionViewSetDelete:
    def test_admin_can_delete_permission(self, api_client, admin, company_folder, employee):
        perm = FolderPermission.objects.create(
            folder=company_folder, user=employee, permission='view', granted_by=admin,
        )
        api_client.force_authenticate(user=admin)
        response = api_client.delete(_folder_permission_detail_url(perm.id))
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not FolderPermission.objects.filter(id=perm.id).exists()

    def test_deleting_last_permission_makes_folder_open(
        self, api_client, admin, employee, employee2, company_folder,
    ):
        perm = FolderPermission.objects.create(
            folder=company_folder, user=employee2, permission='view', granted_by=admin,
        )
        api_client.force_authenticate(user=admin)
        api_client.delete(_folder_permission_detail_url(perm.id))

        # Now folder is open — employee should see it
        api_client.force_authenticate(user=employee)
        response = api_client.get(_folders_url(), {'scope': 'company'})
        ids = {r['id'] for r in response.data['results']}
        assert company_folder.id in ids

    def test_employee_cannot_delete_permission(self, api_client, employee, admin, company_folder):
        perm = FolderPermission.objects.create(
            folder=company_folder, user=employee, permission='view', granted_by=admin,
        )
        api_client.force_authenticate(user=employee)
        response = api_client.delete(_folder_permission_detail_url(perm.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_unauthenticated_cannot_delete_permission(self, api_client, admin, company_folder, employee):
        perm = FolderPermission.objects.create(
            folder=company_folder, user=employee, permission='view', granted_by=admin,
        )
        response = api_client.delete(_folder_permission_detail_url(perm.id))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
