"""
Integration tests for bulk trash actions:
  POST /api/v1/storage/trash/restore/
  POST /api/v1/storage/trash/delete/

Verifies:
- Correct counts returned on success.
- Only accessible (owned / company) soft-deleted items are acted on.
- Items from other users / companies are silently skipped (not 404 / 403).
- Active (non-deleted) items are ignored.
- Validation: both lists empty → 400.
- Unauthenticated → 401.
- Guest can only act on their own personal deleted files/folders.
- Superadmin can act on any deleted item.
"""

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.storage.models import File, Folder
from apps.users.models import User


RESTORE_URL = '/api/v1/storage/trash/restore/'
DELETE_URL = '/api/v1/storage/trash/delete/'


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='Bulk Co', plan='basic', storage_limit_gb=5)


@pytest.fixture
def other_company(db):
    return Company.objects.create(name='Other Co', plan='basic', storage_limit_gb=5)


@pytest.fixture
def company_admin(db, company):
    return User.objects.create_user(
        email='admin@bulk.co',
        password='pass',
        first_name='Admin',
        last_name='Bulk',
        role='company_admin',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='employee@bulk.co',
        password='pass',
        first_name='Emp',
        last_name='Bulk',
        role='employee',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def other_admin(db, other_company):
    return User.objects.create_user(
        email='admin@other.co',
        password='pass',
        first_name='Other',
        last_name='Admin',
        role='company_admin',
        company=other_company,
        is_email_verified=True,
    )


@pytest.fixture
def guest_user(db):
    return User.objects.create_user(
        email='guest@bulk.co',
        password='pass',
        first_name='Guest',
        last_name='Bulk',
        role='guest',
        company=None,
        is_email_verified=True,
    )


@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email='super@bulk.co',
        password='pass',
        first_name='Super',
        last_name='Admin',
        role='superadmin',
        company=None,
        is_email_verified=True,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_deleted_file(owner, company=None, name='del.txt'):
    now = timezone.now()
    return File.all_objects.create(
        name=name,
        owner=owner,
        company=company,
        folder=None,
        file_size=100,
        content_type='text/plain',
        is_deleted=True,
        deleted_at=now,
    )


def _make_active_file(owner, company=None, name='active.txt'):
    return File.all_objects.create(
        name=name,
        owner=owner,
        company=company,
        folder=None,
        file_size=100,
        content_type='text/plain',
        is_deleted=False,
        deleted_at=None,
    )


def _make_deleted_folder(owner, company=None, scope='personal', name='del_folder'):
    now = timezone.now()
    return Folder.all_objects.create(
        name=name,
        scope=scope,
        owner=owner,
        company=company,
        is_deleted=True,
        deleted_at=now,
    )


def _make_active_folder(owner, company=None, scope='personal', name='active_folder'):
    return Folder.all_objects.create(
        name=name,
        scope=scope,
        owner=owner,
        company=company,
        is_deleted=False,
        deleted_at=None,
    )


# ===========================================================================
# BULK RESTORE — POST /api/v1/storage/trash/restore/
# ===========================================================================

class TestTrashBulkRestore:

    # --- validation ---

    @pytest.mark.django_db
    def test_unauthenticated_returns_401(self, api_client):
        response = api_client.post(RESTORE_URL, {'file_ids': [1]}, format='json')
        assert response.status_code == 401

    @pytest.mark.django_db
    def test_empty_both_lists_returns_400(self, api_client, company_admin):
        api_client.force_authenticate(user=company_admin)
        response = api_client.post(RESTORE_URL, {'file_ids': [], 'folder_ids': []}, format='json')
        assert response.status_code == 400

    @pytest.mark.django_db
    def test_missing_both_lists_returns_400(self, api_client, company_admin):
        api_client.force_authenticate(user=company_admin)
        response = api_client.post(RESTORE_URL, {}, format='json')
        assert response.status_code == 400

    # --- file restore ---

    @pytest.mark.django_db
    def test_restore_own_personal_file(self, api_client, company_admin):
        f = _make_deleted_file(company_admin, name='personal.txt')
        api_client.force_authenticate(user=company_admin)

        response = api_client.post(RESTORE_URL, {'file_ids': [f.id]}, format='json')

        assert response.status_code == 200
        assert response.data['restored_files'] == 1
        assert response.data['restored_folders'] == 0
        f.refresh_from_db()
        assert f.is_deleted is False
        assert f.deleted_at is None

    @pytest.mark.django_db
    def test_restore_company_file_by_admin(self, api_client, company_admin, company):
        f = _make_deleted_file(company_admin, company=company, name='company.txt')
        api_client.force_authenticate(user=company_admin)

        response = api_client.post(RESTORE_URL, {'file_ids': [f.id]}, format='json')

        assert response.status_code == 200
        assert response.data['restored_files'] == 1
        f.refresh_from_db()
        assert f.is_deleted is False

    @pytest.mark.django_db
    def test_restore_company_file_by_employee(self, api_client, employee, company):
        f = _make_deleted_file(employee, company=company, name='emp_company.txt')
        api_client.force_authenticate(user=employee)

        response = api_client.post(RESTORE_URL, {'file_ids': [f.id]}, format='json')

        assert response.status_code == 200
        assert response.data['restored_files'] == 1
        f.refresh_from_db()
        assert f.is_deleted is False

    @pytest.mark.django_db
    def test_restore_skips_file_from_other_company(
        self, api_client, company_admin, other_admin, other_company
    ):
        other_file = _make_deleted_file(other_admin, company=other_company, name='other.txt')
        api_client.force_authenticate(user=company_admin)

        response = api_client.post(RESTORE_URL, {'file_ids': [other_file.id]}, format='json')

        # silently skipped — count is 0, not 404/403
        assert response.status_code == 200
        assert response.data['restored_files'] == 0
        other_file.refresh_from_db()
        assert other_file.is_deleted is True  # untouched

    @pytest.mark.django_db
    def test_restore_skips_active_file(self, api_client, company_admin):
        active_file = _make_active_file(company_admin, name='active.txt')
        api_client.force_authenticate(user=company_admin)

        response = api_client.post(RESTORE_URL, {'file_ids': [active_file.id]}, format='json')

        assert response.status_code == 200
        assert response.data['restored_files'] == 0  # active file not in trash

    @pytest.mark.django_db
    def test_restore_multiple_files_partial_access(
        self, api_client, company_admin, company, other_admin, other_company
    ):
        own_file = _make_deleted_file(company_admin, company=company, name='own.txt')
        other_file = _make_deleted_file(other_admin, company=other_company, name='other.txt')
        api_client.force_authenticate(user=company_admin)

        response = api_client.post(
            RESTORE_URL, {'file_ids': [own_file.id, other_file.id]}, format='json'
        )

        assert response.status_code == 200
        assert response.data['restored_files'] == 1  # only own file restored
        own_file.refresh_from_db()
        assert own_file.is_deleted is False
        other_file.refresh_from_db()
        assert other_file.is_deleted is True  # untouched

    # --- folder restore ---

    @pytest.mark.django_db
    def test_restore_own_personal_folder(self, api_client, company_admin):
        folder = _make_deleted_folder(company_admin, scope='personal', name='pers_folder')
        api_client.force_authenticate(user=company_admin)

        response = api_client.post(RESTORE_URL, {'folder_ids': [folder.id]}, format='json')

        assert response.status_code == 200
        assert response.data['restored_files'] == 0
        assert response.data['restored_folders'] == 1
        folder.refresh_from_db()
        assert folder.is_deleted is False

    @pytest.mark.django_db
    def test_restore_company_folder(self, api_client, company_admin, company):
        folder = _make_deleted_folder(
            company_admin, company=company, scope='company', name='comp_folder'
        )
        api_client.force_authenticate(user=company_admin)

        response = api_client.post(RESTORE_URL, {'folder_ids': [folder.id]}, format='json')

        assert response.status_code == 200
        assert response.data['restored_folders'] == 1
        folder.refresh_from_db()
        assert folder.is_deleted is False

    @pytest.mark.django_db
    def test_restore_folder_also_restores_deleted_files_inside(
        self, api_client, company_admin, company
    ):
        folder = _make_deleted_folder(
            company_admin, company=company, scope='company', name='with_files'
        )
        now = timezone.now()
        file_inside = File.all_objects.create(
            name='inside.txt',
            owner=company_admin,
            company=company,
            folder=folder,
            file_size=50,
            content_type='text/plain',
            is_deleted=True,
            deleted_at=now,
        )
        api_client.force_authenticate(user=company_admin)

        response = api_client.post(RESTORE_URL, {'folder_ids': [folder.id]}, format='json')

        assert response.status_code == 200
        assert response.data['restored_folders'] == 1
        folder.refresh_from_db()
        assert folder.is_deleted is False
        file_inside.refresh_from_db()
        assert file_inside.is_deleted is False
        assert file_inside.deleted_at is None

    @pytest.mark.django_db
    def test_restore_skips_folder_from_other_company(
        self, api_client, company_admin, other_admin, other_company
    ):
        other_folder = _make_deleted_folder(
            other_admin, company=other_company, scope='company', name='other_folder'
        )
        api_client.force_authenticate(user=company_admin)

        response = api_client.post(RESTORE_URL, {'folder_ids': [other_folder.id]}, format='json')

        assert response.status_code == 200
        assert response.data['restored_folders'] == 0
        other_folder.refresh_from_db()
        assert other_folder.is_deleted is True  # untouched

    # --- mixed files + folders ---

    @pytest.mark.django_db
    def test_restore_files_and_folders_together(self, api_client, company_admin, company):
        f = _make_deleted_file(company_admin, company=company, name='mixed_f.txt')
        folder = _make_deleted_folder(
            company_admin, company=company, scope='company', name='mixed_folder'
        )
        api_client.force_authenticate(user=company_admin)

        response = api_client.post(
            RESTORE_URL,
            {'file_ids': [f.id], 'folder_ids': [folder.id]},
            format='json',
        )

        assert response.status_code == 200
        assert response.data['restored_files'] == 1
        assert response.data['restored_folders'] == 1

    # --- guest ---

    @pytest.mark.django_db
    def test_guest_restores_own_personal_file(self, api_client, guest_user):
        f = _make_deleted_file(guest_user, name='guest_del.txt')
        api_client.force_authenticate(user=guest_user)

        response = api_client.post(RESTORE_URL, {'file_ids': [f.id]}, format='json')

        assert response.status_code == 200
        assert response.data['restored_files'] == 1
        f.refresh_from_db()
        assert f.is_deleted is False

    @pytest.mark.django_db
    def test_guest_cannot_restore_other_users_file(
        self, api_client, guest_user, company_admin
    ):
        other_file = _make_deleted_file(company_admin, name='admin_del.txt')
        api_client.force_authenticate(user=guest_user)

        response = api_client.post(RESTORE_URL, {'file_ids': [other_file.id]}, format='json')

        assert response.status_code == 200
        assert response.data['restored_files'] == 0
        other_file.refresh_from_db()
        assert other_file.is_deleted is True

    # --- superadmin ---

    @pytest.mark.django_db
    def test_superadmin_can_restore_any_file(
        self, api_client, superadmin, other_admin, other_company
    ):
        f = _make_deleted_file(other_admin, company=other_company, name='any.txt')
        api_client.force_authenticate(user=superadmin)

        response = api_client.post(RESTORE_URL, {'file_ids': [f.id]}, format='json')

        assert response.status_code == 200
        assert response.data['restored_files'] == 1
        f.refresh_from_db()
        assert f.is_deleted is False


# ===========================================================================
# BULK PERMANENT DELETE — POST /api/v1/storage/trash/delete/
# ===========================================================================

class TestTrashBulkDelete:

    # --- validation ---

    @pytest.mark.django_db
    def test_unauthenticated_returns_401(self, api_client):
        response = api_client.post(DELETE_URL, {'file_ids': [1]}, format='json')
        assert response.status_code == 401

    @pytest.mark.django_db
    def test_empty_both_lists_returns_400(self, api_client, company_admin):
        api_client.force_authenticate(user=company_admin)
        response = api_client.post(DELETE_URL, {'file_ids': [], 'folder_ids': []}, format='json')
        assert response.status_code == 400

    @pytest.mark.django_db
    def test_missing_both_lists_returns_400(self, api_client, company_admin):
        api_client.force_authenticate(user=company_admin)
        response = api_client.post(DELETE_URL, {}, format='json')
        assert response.status_code == 400

    # --- file permanent delete ---

    @pytest.mark.django_db
    def test_permanently_deletes_own_personal_file(self, api_client, company_admin):
        f = _make_deleted_file(company_admin, name='del_personal.txt')
        file_id = f.id
        api_client.force_authenticate(user=company_admin)

        response = api_client.post(DELETE_URL, {'file_ids': [file_id]}, format='json')

        assert response.status_code == 200
        assert response.data['deleted_files'] == 1
        assert response.data['deleted_folders'] == 0
        assert not File.all_objects.filter(id=file_id).exists()

    @pytest.mark.django_db
    def test_permanently_deletes_company_file(self, api_client, company_admin, company):
        f = _make_deleted_file(company_admin, company=company, name='del_company.txt')
        file_id = f.id
        api_client.force_authenticate(user=company_admin)

        response = api_client.post(DELETE_URL, {'file_ids': [file_id]}, format='json')

        assert response.status_code == 200
        assert response.data['deleted_files'] == 1
        assert not File.all_objects.filter(id=file_id).exists()

    @pytest.mark.django_db
    def test_skips_file_from_other_company(
        self, api_client, company_admin, other_admin, other_company
    ):
        other_file = _make_deleted_file(other_admin, company=other_company, name='notouch.txt')
        file_id = other_file.id
        api_client.force_authenticate(user=company_admin)

        response = api_client.post(DELETE_URL, {'file_ids': [file_id]}, format='json')

        assert response.status_code == 200
        assert response.data['deleted_files'] == 0
        assert File.all_objects.filter(id=file_id).exists()  # untouched

    @pytest.mark.django_db
    def test_skips_active_file(self, api_client, company_admin):
        active_file = _make_active_file(company_admin, name='still_active.txt')
        api_client.force_authenticate(user=company_admin)

        response = api_client.post(DELETE_URL, {'file_ids': [active_file.id]}, format='json')

        assert response.status_code == 200
        assert response.data['deleted_files'] == 0
        assert File.all_objects.filter(id=active_file.id).exists()

    @pytest.mark.django_db
    def test_partial_access_only_deletes_own_files(
        self, api_client, company_admin, company, other_admin, other_company
    ):
        own_file = _make_deleted_file(company_admin, company=company, name='mine.txt')
        other_file = _make_deleted_file(other_admin, company=other_company, name='theirs.txt')
        api_client.force_authenticate(user=company_admin)

        response = api_client.post(
            DELETE_URL, {'file_ids': [own_file.id, other_file.id]}, format='json'
        )

        assert response.status_code == 200
        assert response.data['deleted_files'] == 1
        assert not File.all_objects.filter(id=own_file.id).exists()
        assert File.all_objects.filter(id=other_file.id).exists()

    # --- folder permanent delete ---

    @pytest.mark.django_db
    def test_permanently_deletes_own_personal_folder(self, api_client, company_admin):
        folder = _make_deleted_folder(company_admin, scope='personal', name='gone_folder')
        folder_id = folder.id
        api_client.force_authenticate(user=company_admin)

        response = api_client.post(DELETE_URL, {'folder_ids': [folder_id]}, format='json')

        assert response.status_code == 200
        assert response.data['deleted_folders'] == 1
        assert response.data['deleted_files'] == 0
        assert not Folder.all_objects.filter(id=folder_id).exists()

    @pytest.mark.django_db
    def test_skips_folder_from_other_company(
        self, api_client, company_admin, other_admin, other_company
    ):
        other_folder = _make_deleted_folder(
            other_admin, company=other_company, scope='company', name='other_gone'
        )
        folder_id = other_folder.id
        api_client.force_authenticate(user=company_admin)

        response = api_client.post(DELETE_URL, {'folder_ids': [folder_id]}, format='json')

        assert response.status_code == 200
        assert response.data['deleted_folders'] == 0
        assert Folder.all_objects.filter(id=folder_id).exists()  # untouched

    # --- mixed files + folders ---

    @pytest.mark.django_db
    def test_delete_files_and_folders_together(self, api_client, company_admin, company):
        f = _make_deleted_file(company_admin, company=company, name='del_both_f.txt')
        folder = _make_deleted_folder(
            company_admin, company=company, scope='company', name='del_both_folder'
        )
        file_id, folder_id = f.id, folder.id
        api_client.force_authenticate(user=company_admin)

        response = api_client.post(
            DELETE_URL,
            {'file_ids': [file_id], 'folder_ids': [folder_id]},
            format='json',
        )

        assert response.status_code == 200
        assert response.data['deleted_files'] == 1
        assert response.data['deleted_folders'] == 1
        assert not File.all_objects.filter(id=file_id).exists()
        assert not Folder.all_objects.filter(id=folder_id).exists()

    # --- guest ---

    @pytest.mark.django_db
    def test_guest_permanently_deletes_own_personal_file(self, api_client, guest_user):
        f = _make_deleted_file(guest_user, name='guest_gone.txt')
        file_id = f.id
        api_client.force_authenticate(user=guest_user)

        response = api_client.post(DELETE_URL, {'file_ids': [file_id]}, format='json')

        assert response.status_code == 200
        assert response.data['deleted_files'] == 1
        assert not File.all_objects.filter(id=file_id).exists()

    @pytest.mark.django_db
    def test_guest_cannot_delete_other_users_file(
        self, api_client, guest_user, company_admin
    ):
        other_file = _make_deleted_file(company_admin, name='admin_gone.txt')
        file_id = other_file.id
        api_client.force_authenticate(user=guest_user)

        response = api_client.post(DELETE_URL, {'file_ids': [file_id]}, format='json')

        assert response.status_code == 200
        assert response.data['deleted_files'] == 0
        assert File.all_objects.filter(id=file_id).exists()

    # --- superadmin ---

    @pytest.mark.django_db
    def test_superadmin_can_permanently_delete_any_file(
        self, api_client, superadmin, other_admin, other_company
    ):
        f = _make_deleted_file(other_admin, company=other_company, name='super_del.txt')
        file_id = f.id
        api_client.force_authenticate(user=superadmin)

        response = api_client.post(DELETE_URL, {'file_ids': [file_id]}, format='json')

        assert response.status_code == 200
        assert response.data['deleted_files'] == 1
        assert not File.all_objects.filter(id=file_id).exists()

    # --- employee: cannot act on company files they did not upload ---

    @pytest.mark.django_db
    def test_employee_cannot_restore_other_users_company_file(
        self, api_client, employee, company_admin, company
    ):
        """Employee must not be able to restore a deleted company file they didn't upload."""
        other_file = _make_deleted_file(company_admin, company=company, name='admin_comp.txt')
        api_client.force_authenticate(user=employee)

        response = api_client.post(RESTORE_URL, {'file_ids': [other_file.id]}, format='json')

        assert response.status_code == 200
        assert response.data['restored_files'] == 0
        other_file.refresh_from_db()
        assert other_file.is_deleted is True

    @pytest.mark.django_db
    def test_employee_cannot_delete_other_users_company_file(
        self, api_client, employee, company_admin, company
    ):
        """Employee must not be able to permanently delete a company file they didn't upload."""
        other_file = _make_deleted_file(company_admin, company=company, name='admin_perm.txt')
        file_id = other_file.id
        api_client.force_authenticate(user=employee)

        response = api_client.post(DELETE_URL, {'file_ids': [file_id]}, format='json')

        assert response.status_code == 200
        assert response.data['deleted_files'] == 0
        assert File.all_objects.filter(id=file_id).exists()

    @pytest.mark.django_db
    def test_employee_cannot_restore_other_users_company_folder(
        self, api_client, employee, company_admin, company
    ):
        """Employee must not be able to restore a deleted company folder they didn't create."""
        other_folder = _make_deleted_folder(
            company_admin, company=company, scope='company', name='admin_folder'
        )
        api_client.force_authenticate(user=employee)

        response = api_client.post(RESTORE_URL, {'folder_ids': [other_folder.id]}, format='json')

        assert response.status_code == 200
        assert response.data['restored_folders'] == 0
        other_folder.refresh_from_db()
        assert other_folder.is_deleted is True

    @pytest.mark.django_db
    def test_employee_can_restore_own_company_file(
        self, api_client, employee, company
    ):
        """Employee can restore a deleted company file they uploaded themselves."""
        own_file = _make_deleted_file(employee, company=company, name='own_comp_restore.txt')
        api_client.force_authenticate(user=employee)

        response = api_client.post(RESTORE_URL, {'file_ids': [own_file.id]}, format='json')

        assert response.status_code == 200
        assert response.data['restored_files'] == 1
        own_file.refresh_from_db()
        assert own_file.is_deleted is False

    @pytest.mark.django_db
    def test_company_admin_can_restore_any_company_file(
        self, api_client, company_admin, employee, company
    ):
        """company_admin can restore deleted company files regardless of who uploaded them."""
        employee_file = _make_deleted_file(employee, company=company, name='emp_restore.txt')
        api_client.force_authenticate(user=company_admin)

        response = api_client.post(RESTORE_URL, {'file_ids': [employee_file.id]}, format='json')

        assert response.status_code == 200
        assert response.data['restored_files'] == 1
        employee_file.refresh_from_db()
        assert employee_file.is_deleted is False
