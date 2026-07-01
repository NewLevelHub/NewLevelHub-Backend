"""
Integration tests for GET /api/v1/storage/trash/

Authority rules verified here:
- superadmin    → sees all deleted items.
- company_admin → sees their own personal deleted items + any deleted item in their company.
- employee      → sees only deleted items they personally own (owner=user), including
                  both personal (company=NULL) and company-scoped (company=user.company)
                  files they uploaded themselves. Company files uploaded by other
                  employees are NOT visible.
- guest         → sees only their own personal deleted files.
"""

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.storage.models import File, Folder
from apps.users.models import User


TRASH_URL = '/api/v1/storage/trash/'


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='Trash Co', plan='basic', storage_limit_gb=5)


@pytest.fixture
def company_admin(db, company):
    return User.objects.create_user(
        email='admin@trash.co',
        password='pass',
        first_name='Admin',
        last_name='Trash',
        role='company_admin',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='employee@trash.co',
        password='pass',
        first_name='Emp',
        last_name='Trash',
        role='employee',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def guest_user(db):
    return User.objects.create_user(
        email='guest@trash.co',
        password='pass',
        first_name='Guest',
        last_name='Trash',
        role='guest',
        company=None,
        is_email_verified=True,
    )


def _make_deleted_personal_file(owner, name='personal_deleted.txt'):
    """Create a soft-deleted personal file (no company, no folder)."""
    now = timezone.now()
    f = File.all_objects.create(
        name=name,
        owner=owner,
        company=None,
        folder=None,
        file_size=100,
        content_type='text/plain',
        is_deleted=True,
        deleted_at=now,
    )
    return f


def _make_deleted_company_file(owner, company, name='company_deleted.txt'):
    """Create a soft-deleted company-shared file."""
    now = timezone.now()
    folder = Folder.all_objects.create(
        name='Company Folder',
        scope='company',
        owner=owner,
        company=company,
        is_deleted=False,
    )
    f = File.all_objects.create(
        name=name,
        owner=owner,
        company=company,
        folder=folder,
        file_size=200,
        content_type='text/plain',
        is_deleted=True,
        deleted_at=now,
    )
    return f


# ---------------------------------------------------------------------------
# No scope param — should return both personal AND company deleted items
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_trash_no_scope_returns_personal_and_company_files(api_client, company_admin, company):
    """Bug regression: no scope param must return both personal and company deleted files."""
    personal_file = _make_deleted_personal_file(company_admin, name='my_personal.txt')
    company_file = _make_deleted_company_file(company_admin, company, name='shared_company.txt')

    api_client.force_authenticate(user=company_admin)
    response = api_client.get(TRASH_URL)

    assert response.status_code == 200
    result_ids = {item['id'] for item in response.data['results'] if item['item_type'] == 'file'}
    assert personal_file.id in result_ids, 'Personal deleted file must appear in trash'
    assert company_file.id in result_ids, 'Company-shared deleted file must appear in trash'


@pytest.mark.django_db
def test_trash_employee_sees_own_personal_and_own_company_files(api_client, employee, company):
    """Employee sees their own personal deleted files and company files they uploaded."""
    personal_file = _make_deleted_personal_file(employee, name='emp_personal.txt')
    # Company file owned by the employee themselves.
    own_company_file = _make_deleted_company_file(employee, company, name='emp_own_company.txt')

    api_client.force_authenticate(user=employee)
    response = api_client.get(TRASH_URL)

    assert response.status_code == 200
    result_ids = {item['id'] for item in response.data['results'] if item['item_type'] == 'file'}
    assert personal_file.id in result_ids, 'Own personal deleted file must appear in trash'
    assert own_company_file.id in result_ids, 'Own company-scope deleted file must appear in trash'


@pytest.mark.django_db
def test_trash_employee_cannot_see_other_users_company_files(
    api_client, employee, company_admin, company
):
    """Employee must NOT see company deleted files uploaded by another user."""
    # File uploaded by company_admin, not the employee.
    other_users_company_file = _make_deleted_company_file(
        company_admin, company, name='admin_company.txt'
    )

    api_client.force_authenticate(user=employee)
    response = api_client.get(TRASH_URL)

    assert response.status_code == 200
    result_ids = {item['id'] for item in response.data['results'] if item['item_type'] == 'file'}
    assert other_users_company_file.id not in result_ids, (
        'Employee must not see company files they did not upload'
    )


# ---------------------------------------------------------------------------
# scope=personal — should return only personal deleted items
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_trash_scope_personal_excludes_company_files(api_client, company_admin, company):
    personal_file = _make_deleted_personal_file(company_admin, name='pers.txt')
    company_file = _make_deleted_company_file(company_admin, company, name='comp.txt')

    api_client.force_authenticate(user=company_admin)
    response = api_client.get(TRASH_URL, {'scope': 'personal'})

    assert response.status_code == 200
    result_ids = {item['id'] for item in response.data['results'] if item['item_type'] == 'file'}
    assert personal_file.id in result_ids
    assert company_file.id not in result_ids, 'Company file must not appear when scope=personal'


# ---------------------------------------------------------------------------
# scope=company — should return only company-shared deleted items
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_trash_scope_company_excludes_personal_files(api_client, company_admin, company):
    personal_file = _make_deleted_personal_file(company_admin, name='pers2.txt')
    company_file = _make_deleted_company_file(company_admin, company, name='comp2.txt')

    api_client.force_authenticate(user=company_admin)
    response = api_client.get(TRASH_URL, {'scope': 'company'})

    assert response.status_code == 200
    result_ids = {item['id'] for item in response.data['results'] if item['item_type'] == 'file'}
    assert personal_file.id not in result_ids, 'Personal file must not appear when scope=company'
    assert company_file.id in result_ids


# ---------------------------------------------------------------------------
# Guest user — only own personal deleted files, never company files
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_trash_guest_only_sees_own_personal_files(api_client, guest_user, company_admin, company):
    guest_file = _make_deleted_personal_file(guest_user, name='guest_personal.txt')
    company_file = _make_deleted_company_file(company_admin, company, name='other_company.txt')

    api_client.force_authenticate(user=guest_user)
    response = api_client.get(TRASH_URL)

    assert response.status_code == 200
    result_ids = {item['id'] for item in response.data['results'] if item['item_type'] == 'file'}
    assert guest_file.id in result_ids
    assert company_file.id not in result_ids, 'Guest must never see company-shared files in trash'


# ---------------------------------------------------------------------------
# Unauthenticated → 401
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_trash_unauthenticated_returns_401(api_client):
    response = api_client.get(TRASH_URL)
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Active (non-deleted) files must NOT appear in trash
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_trash_active_files_not_included(api_client, company_admin, company):
    """Active files must not appear in the trash list even though the user owns them."""
    active_file = File.all_objects.create(
        name='active.txt',
        owner=company_admin,
        company=None,
        folder=None,
        file_size=50,
        content_type='text/plain',
        is_deleted=False,
        deleted_at=None,
    )

    api_client.force_authenticate(user=company_admin)
    response = api_client.get(TRASH_URL)

    assert response.status_code == 200
    result_ids = {item['id'] for item in response.data['results'] if item['item_type'] == 'file'}
    assert active_file.id not in result_ids


# ---------------------------------------------------------------------------
# scope=company for employee — only their own uploaded company items
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_trash_scope_company_employee_only_sees_own_files(
    api_client, employee, company_admin, company
):
    """With scope=company, employee sees only deleted company files they uploaded."""
    own_company_file = _make_deleted_company_file(employee, company, name='own_comp.txt')
    other_company_file = _make_deleted_company_file(company_admin, company, name='other_comp.txt')

    api_client.force_authenticate(user=employee)
    response = api_client.get(TRASH_URL, {'scope': 'company'})

    assert response.status_code == 200
    result_ids = {item['id'] for item in response.data['results'] if item['item_type'] == 'file'}
    assert own_company_file.id in result_ids
    assert other_company_file.id not in result_ids, (
        'Employee must not see other users company files even with scope=company'
    )


# ---------------------------------------------------------------------------
# company_admin sees all company deleted files (including other users')
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_trash_company_admin_sees_all_company_deleted_files(
    api_client, company_admin, employee, company
):
    """company_admin must see deleted company files regardless of who uploaded them."""
    own_file = _make_deleted_company_file(company_admin, company, name='admin_own.txt')
    employee_file = _make_deleted_company_file(employee, company, name='emp_uploaded.txt')

    api_client.force_authenticate(user=company_admin)
    response = api_client.get(TRASH_URL)

    assert response.status_code == 200
    result_ids = {item['id'] for item in response.data['results'] if item['item_type'] == 'file'}
    assert own_file.id in result_ids
    assert employee_file.id in result_ids, 'company_admin must see all company deleted files'


# ---------------------------------------------------------------------------
# Response shape follows pagination contract
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_trash_response_has_pagination_shape(api_client, company_admin):
    api_client.force_authenticate(user=company_admin)
    response = api_client.get(TRASH_URL)

    assert response.status_code == 200
    assert 'count' in response.data
    assert 'next' in response.data
    assert 'previous' in response.data
    assert 'results' in response.data
