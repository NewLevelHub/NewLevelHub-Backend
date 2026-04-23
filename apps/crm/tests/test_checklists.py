import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.crm.models import Board, Column, Task, Checklist, ChecklistItem
from apps.users.models import User


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def task_checklists_url(task_id):
    return f'/api/v1/crm/tasks/{task_id}/checklists/'


def checklist_detail_url(checklist_id):
    return f'/api/v1/crm/checklists/{checklist_id}/'


def checklist_items_url(checklist_id):
    return f'/api/v1/crm/checklists/{checklist_id}/items/'


def item_detail_url(item_id):
    return f'/api/v1/crm/items/{item_id}/'


def task_detail_url(task_id):
    return f'/api/v1/crm/tasks/{task_id}/'


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company_a(db):
    return Company.objects.create(name='Company A', plan='standard', max_boards=10)


@pytest.fixture
def company_b(db):
    return Company.objects.create(name='Company B', plan='standard', max_boards=10)


@pytest.fixture
def admin_a(db, company_a):
    return User.objects.create_user(
        email='admin_a_cl@test.com',
        password='pass',
        first_name='Admin',
        last_name='A',
        role='company_admin',
        company=company_a,
        is_email_verified=True,
    )


@pytest.fixture
def employee_a(db, company_a):
    return User.objects.create_user(
        email='emp_a_cl@test.com',
        password='pass',
        first_name='Employee',
        last_name='A',
        role='employee',
        company=company_a,
        is_email_verified=True,
    )


@pytest.fixture
def employee_b(db, company_b):
    return User.objects.create_user(
        email='emp_b_cl@test.com',
        password='pass',
        first_name='Employee',
        last_name='B',
        role='employee',
        company=company_b,
        is_email_verified=True,
    )


@pytest.fixture
def guest_user(db):
    return User.objects.create_user(
        email='guest_cl@test.com',
        password='pass',
        first_name='Guest',
        last_name='User',
        role='guest',
        is_email_verified=True,
    )


@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email='super_cl@test.com',
        password='pass',
        first_name='Super',
        last_name='Admin',
        role='superadmin',
        is_email_verified=True,
    )


@pytest.fixture
def board_a(db, company_a, admin_a):
    return Board.objects.create(company=company_a, name='Board A', created_by=admin_a)


@pytest.fixture
def column_a(db, board_a):
    return Column.objects.create(board=board_a, name='To Do', position=1)


@pytest.fixture
def board_b(db, company_b, employee_b):
    return Board.objects.create(company=company_b, name='Board B', created_by=employee_b)


@pytest.fixture
def column_b(db, board_b):
    return Column.objects.create(board=board_b, name='To Do', position=1)


@pytest.fixture
def task_a(db, column_a, admin_a):
    return Task.objects.create(column=column_a, title='Task A', created_by=admin_a, position=1)


@pytest.fixture
def task_b(db, column_b, employee_b):
    return Task.objects.create(column=column_b, title='Task B', created_by=employee_b, position=1)


@pytest.fixture
def checklist_a(db, task_a):
    return Checklist.objects.create(task=task_a, title='DoD')


@pytest.fixture
def item_a(db, checklist_a):
    return ChecklistItem.objects.create(checklist=checklist_a, text='Write tests', position=1)


# ---------------------------------------------------------------------------
# AC 1 — POST /crm/tasks/<task_id>/checklists/
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCreateChecklist:
    def test_create_checklist_as_admin(self, api_client, admin_a, task_a):
        api_client.force_authenticate(user=admin_a)
        resp = api_client.post(task_checklists_url(task_a.pk), {'title': 'Definition of Done'})
        assert resp.status_code == status.HTTP_201_CREATED
        data = resp.json()
        assert data['title'] == 'Definition of Done'
        assert data['items'] == []
        assert data['checklist_progress'] == {'total': 0, 'completed': 0}

    def test_create_checklist_as_employee(self, api_client, employee_a, task_a):
        api_client.force_authenticate(user=employee_a)
        resp = api_client.post(task_checklists_url(task_a.pk), {'title': 'My List'})
        assert resp.status_code == status.HTTP_201_CREATED

    def test_create_checklist_unauthenticated(self, api_client, task_a):
        resp = api_client.post(task_checklists_url(task_a.pk), {'title': 'List'})
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_create_checklist_guest_forbidden(self, api_client, guest_user, task_a):
        api_client.force_authenticate(user=guest_user)
        resp = api_client.post(task_checklists_url(task_a.pk), {'title': 'List'})
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_create_checklist_wrong_company_forbidden(self, api_client, employee_b, task_a):
        """employee_b belongs to company_b; task_a belongs to company_a."""
        api_client.force_authenticate(user=employee_b)
        resp = api_client.post(task_checklists_url(task_a.pk), {'title': 'List'})
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_create_checklist_superadmin_any_company(self, api_client, superadmin, task_a):
        api_client.force_authenticate(user=superadmin)
        resp = api_client.post(task_checklists_url(task_a.pk), {'title': 'SA List'})
        assert resp.status_code == status.HTTP_201_CREATED

    def test_create_checklist_task_not_found(self, api_client, admin_a):
        api_client.force_authenticate(user=admin_a)
        resp = api_client.post(task_checklists_url(9999), {'title': 'List'})
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_create_checklist_missing_title(self, api_client, admin_a, task_a):
        api_client.force_authenticate(user=admin_a)
        resp = api_client.post(task_checklists_url(task_a.pk), {})
        assert resp.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# AC 1 — GET /crm/tasks/<task_id>/checklists/
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestListChecklists:
    def test_list_checklists(self, api_client, admin_a, task_a, checklist_a, item_a):
        api_client.force_authenticate(user=admin_a)
        resp = api_client.get(task_checklists_url(task_a.pk))
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert len(data) == 1
        assert data[0]['id'] == checklist_a.pk
        assert len(data[0]['items']) == 1
        assert data[0]['checklist_progress'] == {'total': 1, 'completed': 0}

    def test_list_checklists_wrong_company(self, api_client, employee_b, task_a):
        api_client.force_authenticate(user=employee_b)
        resp = api_client.get(task_checklists_url(task_a.pk))
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_list_checklists_unauthenticated(self, api_client, task_a):
        resp = api_client.get(task_checklists_url(task_a.pk))
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# ---------------------------------------------------------------------------
# AC 2 — POST /crm/checklists/<id>/items/
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCreateItem:
    def test_create_item_auto_order(self, api_client, admin_a, checklist_a, item_a):
        """Second item should get order=2 automatically."""
        api_client.force_authenticate(user=admin_a)
        resp = api_client.post(checklist_items_url(checklist_a.pk), {'text': 'Deploy'})
        assert resp.status_code == status.HTTP_201_CREATED
        data = resp.json()
        assert data['text'] == 'Deploy'
        assert data['order'] == 2
        assert data['is_completed'] is False

    def test_create_first_item_order_is_one(self, api_client, admin_a, checklist_a):
        """First item in an empty checklist should get order=1."""
        api_client.force_authenticate(user=admin_a)
        resp = api_client.post(checklist_items_url(checklist_a.pk), {'text': 'First'})
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.json()['order'] == 1

    def test_create_item_unauthenticated(self, api_client, checklist_a):
        resp = api_client.post(checklist_items_url(checklist_a.pk), {'text': 'X'})
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_create_item_guest_forbidden(self, api_client, guest_user, checklist_a):
        api_client.force_authenticate(user=guest_user)
        resp = api_client.post(checklist_items_url(checklist_a.pk), {'text': 'X'})
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_create_item_wrong_company_forbidden(self, api_client, employee_b, checklist_a):
        api_client.force_authenticate(user=employee_b)
        resp = api_client.post(checklist_items_url(checklist_a.pk), {'text': 'X'})
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_create_item_checklist_not_found(self, api_client, admin_a):
        api_client.force_authenticate(user=admin_a)
        resp = api_client.post(checklist_items_url(9999), {'text': 'X'})
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_create_item_missing_text(self, api_client, admin_a, checklist_a):
        api_client.force_authenticate(user=admin_a)
        resp = api_client.post(checklist_items_url(checklist_a.pk), {})
        assert resp.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# AC 3 — PATCH /crm/items/<id>/
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestUpdateItem:
    def test_patch_text(self, api_client, admin_a, item_a):
        api_client.force_authenticate(user=admin_a)
        resp = api_client.patch(item_detail_url(item_a.pk), {'text': 'Updated text'})
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()['text'] == 'Updated text'

    def test_patch_is_completed(self, api_client, admin_a, item_a):
        api_client.force_authenticate(user=admin_a)
        resp = api_client.patch(item_detail_url(item_a.pk), {'is_completed': True})
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()['is_completed'] is True

    def test_patch_order(self, api_client, admin_a, item_a):
        api_client.force_authenticate(user=admin_a)
        resp = api_client.patch(item_detail_url(item_a.pk), {'order': 3})
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()['order'] == 3

    def test_patch_order_below_one_rejected(self, api_client, admin_a, item_a):
        api_client.force_authenticate(user=admin_a)
        resp = api_client.patch(item_detail_url(item_a.pk), {'order': 0})
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_patch_all_fields(self, api_client, admin_a, item_a):
        api_client.force_authenticate(user=admin_a)
        resp = api_client.patch(item_detail_url(item_a.pk), {
            'text': 'All updated', 'is_completed': True, 'order': 2,
        })
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data['text'] == 'All updated'
        assert data['is_completed'] is True
        assert data['order'] == 2

    def test_patch_unauthenticated(self, api_client, item_a):
        resp = api_client.patch(item_detail_url(item_a.pk), {'text': 'X'})
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_patch_guest_forbidden(self, api_client, guest_user, item_a):
        api_client.force_authenticate(user=guest_user)
        resp = api_client.patch(item_detail_url(item_a.pk), {'text': 'X'})
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_patch_wrong_company_forbidden(self, api_client, employee_b, item_a):
        api_client.force_authenticate(user=employee_b)
        resp = api_client.patch(item_detail_url(item_a.pk), {'text': 'X'})
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_patch_item_not_found(self, api_client, admin_a):
        api_client.force_authenticate(user=admin_a)
        resp = api_client.patch(item_detail_url(9999), {'text': 'X'})
        assert resp.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# AC 4 — DELETE
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestDeleteChecklist:
    def test_delete_checklist_cascades_items(self, api_client, admin_a, checklist_a, item_a):
        api_client.force_authenticate(user=admin_a)
        checklist_id = checklist_a.pk
        resp = api_client.delete(checklist_detail_url(checklist_id))
        assert resp.status_code == status.HTTP_204_NO_CONTENT
        assert not Checklist.objects.filter(pk=checklist_id).exists()
        assert not ChecklistItem.objects.filter(checklist_id=checklist_id).exists()

    def test_delete_checklist_unauthenticated(self, api_client, checklist_a):
        resp = api_client.delete(checklist_detail_url(checklist_a.pk))
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_delete_checklist_guest_forbidden(self, api_client, guest_user, checklist_a):
        api_client.force_authenticate(user=guest_user)
        resp = api_client.delete(checklist_detail_url(checklist_a.pk))
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_delete_checklist_wrong_company_forbidden(self, api_client, employee_b, checklist_a):
        api_client.force_authenticate(user=employee_b)
        resp = api_client.delete(checklist_detail_url(checklist_a.pk))
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_delete_checklist_not_found(self, api_client, admin_a):
        api_client.force_authenticate(user=admin_a)
        resp = api_client.delete(checklist_detail_url(9999))
        assert resp.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestDeleteItem:
    def test_delete_item_and_renormalise(self, api_client, admin_a, checklist_a):
        """Delete item 1 of 3; remaining items should be renormalised to 1, 2."""
        item1 = ChecklistItem.objects.create(checklist=checklist_a, text='One', position=1)
        item2 = ChecklistItem.objects.create(checklist=checklist_a, text='Two', position=2)
        item3 = ChecklistItem.objects.create(checklist=checklist_a, text='Three', position=3)

        api_client.force_authenticate(user=admin_a)
        resp = api_client.delete(item_detail_url(item1.pk))
        assert resp.status_code == status.HTTP_204_NO_CONTENT
        assert not ChecklistItem.objects.filter(pk=item1.pk).exists()

        positions = list(ChecklistItem.objects.filter(checklist=checklist_a).order_by('position').values_list(
            'position', flat=True
        ))
        assert positions == [1, 2]

        # item2 and item3 still exist
        assert ChecklistItem.objects.filter(pk=item2.pk).exists()
        assert ChecklistItem.objects.filter(pk=item3.pk).exists()

    def test_delete_item_unauthenticated(self, api_client, item_a):
        resp = api_client.delete(item_detail_url(item_a.pk))
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_delete_item_guest_forbidden(self, api_client, guest_user, item_a):
        api_client.force_authenticate(user=guest_user)
        resp = api_client.delete(item_detail_url(item_a.pk))
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_delete_item_wrong_company_forbidden(self, api_client, employee_b, item_a):
        api_client.force_authenticate(user=employee_b)
        resp = api_client.delete(item_detail_url(item_a.pk))
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_delete_item_not_found(self, api_client, admin_a):
        api_client.force_authenticate(user=admin_a)
        resp = api_client.delete(item_detail_url(9999))
        assert resp.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# AC 5 — GET /crm/tasks/<id>/ includes checklists
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTaskDetailIncludesChecklists:
    def test_task_detail_has_checklists_field(self, api_client, admin_a, task_a, checklist_a, item_a):
        api_client.force_authenticate(user=admin_a)
        resp = api_client.get(task_detail_url(task_a.pk))
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert 'checklists' in data
        assert len(data['checklists']) == 1
        cl = data['checklists'][0]
        assert cl['id'] == checklist_a.pk
        assert cl['title'] == 'DoD'
        assert len(cl['items']) == 1
        assert cl['items'][0]['text'] == 'Write tests'
        assert cl['items'][0]['is_completed'] is False
        assert cl['items'][0]['order'] == 1
        assert cl['checklist_progress'] == {'total': 1, 'completed': 0}

    def test_task_detail_progress_reflects_completed_items(self, api_client, admin_a, task_a, checklist_a):
        ChecklistItem.objects.create(checklist=checklist_a, text='Done item', position=1, is_done=True)
        ChecklistItem.objects.create(checklist=checklist_a, text='Pending item', position=2, is_done=False)

        api_client.force_authenticate(user=admin_a)
        resp = api_client.get(task_detail_url(task_a.pk))
        assert resp.status_code == status.HTTP_200_OK
        cl = resp.json()['checklists'][0]
        assert cl['checklist_progress'] == {'total': 2, 'completed': 1}
