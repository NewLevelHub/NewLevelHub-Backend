import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.crm.models import Board, Column, Label, Task, TaskHistory
from apps.notifications.models import Notification
from apps.users.models import User

TASKS_URL = '/api/v1/crm/tasks/'


def task_url(pk):
    return f'/api/v1/crm/tasks/{pk}/'


def task_move_url(pk):
    return f'/api/v1/crm/tasks/{pk}/move/'


def task_history_url(pk):
    return f'/api/v1/crm/tasks/{pk}/history/'


def task_archive_url(pk):
    return f'/api/v1/crm/tasks/{pk}/archive/'


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
        email='admin_a@test.com',
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
        email='employee_a@test.com',
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
        email='employee_b@test.com',
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
        email='guest@test.com',
        password='pass',
        first_name='Guest',
        last_name='User',
        role='guest',
        is_email_verified=True,
    )


@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email='super@test.com',
        password='pass',
        first_name='Super',
        last_name='Admin',
        role='superadmin',
        is_email_verified=True,
    )


@pytest.fixture
def board_a(db, company_a, admin_a):
    board = Board.objects.create(company=company_a, name='Board A', created_by=admin_a)
    return board


@pytest.fixture
def column_a(db, board_a):
    return Column.objects.create(board=board_a, name='To Do', position=1)


@pytest.fixture
def column_a2(db, board_a):
    return Column.objects.create(board=board_a, name='Done', position=2)


@pytest.fixture
def board_b(db, company_b, employee_b):
    board = Board.objects.create(company=company_b, name='Board B', created_by=employee_b)
    return board


@pytest.fixture
def column_b(db, board_b):
    return Column.objects.create(board=board_b, name='To Do', position=1)


@pytest.fixture
def label_a(db, company_a):
    return Label.objects.create(company=company_a, name='Bug', color='#ff0000')


@pytest.fixture
def task_a(db, column_a, admin_a):
    return Task.objects.create(
        column=column_a,
        title='Task A',
        description='Desc',
        priority='medium',
        position=1,
        created_by=admin_a,
    )


# ---------------------------------------------------------------------------
# POST /api/v1/crm/tasks/ — Create
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTaskCreate:
    def test_unauthenticated_returns_401(self, api_client, column_a, board_a):
        res = api_client.post(TASKS_URL, {
            'board_id': board_a.id, 'column_id': column_a.id,
            'title': 'X', 'priority': 'low',
        }, format='json')
        assert res.status_code == status.HTTP_401_UNAUTHORIZED

    def test_guest_returns_403(self, api_client, guest_user, column_a, board_a):
        api_client.force_authenticate(guest_user)
        res = api_client.post(TASKS_URL, {
            'board_id': board_a.id, 'column_id': column_a.id,
            'title': 'X', 'priority': 'low',
        }, format='json')
        assert res.status_code == status.HTTP_403_FORBIDDEN

    def test_employee_can_create_task(self, api_client, employee_a, column_a, board_a):
        api_client.force_authenticate(employee_a)
        res = api_client.post(TASKS_URL, {
            'board_id': board_a.id,
            'column_id': column_a.id,
            'title': 'New Task',
            'priority': 'high',
        }, format='json')
        assert res.status_code == status.HTTP_201_CREATED
        assert res.data['title'] == 'New Task'
        assert res.data['priority'] == 'high'

    def test_admin_can_create_task_with_assignee(self, api_client, admin_a, employee_a, column_a, board_a):
        api_client.force_authenticate(admin_a)
        res = api_client.post(TASKS_URL, {
            'board_id': board_a.id,
            'column_id': column_a.id,
            'title': 'Assigned Task',
            'priority': 'medium',
            'assignee_id': employee_a.id,
        }, format='json')
        assert res.status_code == status.HTTP_201_CREATED

    def test_create_with_labels(self, api_client, admin_a, column_a, board_a, label_a):
        api_client.force_authenticate(admin_a)
        res = api_client.post(TASKS_URL, {
            'board_id': board_a.id,
            'column_id': column_a.id,
            'title': 'Labelled',
            'priority': 'low',
            'label_ids': [label_a.id],
        }, format='json')
        assert res.status_code == status.HTTP_201_CREATED
        assert label_a.id in res.data['labels']

    def test_invalid_board_returns_400(self, api_client, admin_a, column_a):
        api_client.force_authenticate(admin_a)
        res = api_client.post(TASKS_URL, {
            'board_id': 99999,
            'column_id': column_a.id,
            'title': 'X',
            'priority': 'low',
        }, format='json')
        assert res.status_code == status.HTTP_400_BAD_REQUEST

    def test_cross_company_board_returns_400(self, api_client, admin_a, column_b, board_b):
        api_client.force_authenticate(admin_a)
        res = api_client.post(TASKS_URL, {
            'board_id': board_b.id,
            'column_id': column_b.id,
            'title': 'X',
            'priority': 'low',
        }, format='json')
        assert res.status_code == status.HTTP_400_BAD_REQUEST

    def test_cross_company_assignee_returns_400(self, api_client, admin_a, column_a, board_a, employee_b):
        api_client.force_authenticate(admin_a)
        res = api_client.post(TASKS_URL, {
            'board_id': board_a.id,
            'column_id': column_a.id,
            'title': 'X',
            'priority': 'low',
            'assignee_id': employee_b.id,
        }, format='json')
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        assert 'assignee_id' in str(res.data)

    def test_column_not_on_board_returns_400(self, api_client, admin_a, board_a, column_b):
        api_client.force_authenticate(admin_a)
        res = api_client.post(TASKS_URL, {
            'board_id': board_a.id,
            'column_id': column_b.id,
            'title': 'X',
            'priority': 'low',
        }, format='json')
        assert res.status_code == status.HTTP_400_BAD_REQUEST

    def test_created_by_auto_set(self, api_client, admin_a, column_a, board_a):
        api_client.force_authenticate(admin_a)
        res = api_client.post(TASKS_URL, {
            'board_id': board_a.id,
            'column_id': column_a.id,
            'title': 'Auto Created By',
            'priority': 'low',
        }, format='json')
        assert res.status_code == status.HTTP_201_CREATED
        task = Task.objects.get(pk=res.data['id'])
        assert task.created_by == admin_a


# ---------------------------------------------------------------------------
# GET /api/v1/crm/tasks/ — List with filters
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTaskList:
    def test_unauthenticated_returns_401(self, api_client):
        res = api_client.get(TASKS_URL)
        assert res.status_code == status.HTTP_401_UNAUTHORIZED

    def test_employee_sees_only_own_company_tasks(
        self, api_client, employee_a, task_a, column_b
    ):
        # Task in company B
        Task.objects.create(column=column_b, title='Other Co Task', priority='low', position=1)
        api_client.force_authenticate(employee_a)
        res = api_client.get(TASKS_URL)
        assert res.status_code == status.HTTP_200_OK
        ids = [t['id'] for t in res.data['results']]
        assert task_a.id in ids
        assert all(
            Task.objects.get(pk=tid).column.board.company_id == employee_a.company_id
            for tid in ids
        )

    def test_filter_by_board_id(self, api_client, admin_a, board_a, column_a, task_a):
        api_client.force_authenticate(admin_a)
        res = api_client.get(TASKS_URL, {'board_id': board_a.id})
        assert res.status_code == status.HTTP_200_OK
        assert all(
            Task.objects.get(pk=t['id']).column.board_id == board_a.id
            for t in res.data['results']
        )

    def test_filter_by_column_id(self, api_client, admin_a, column_a, column_a2, task_a):
        task2 = Task.objects.create(column=column_a2, title='Task 2', priority='low', position=1)
        api_client.force_authenticate(admin_a)
        res = api_client.get(TASKS_URL, {'column_id': column_a.id})
        assert res.status_code == status.HTTP_200_OK
        ids = [t['id'] for t in res.data['results']]
        assert task_a.id in ids
        assert task2.id not in ids

    def test_filter_by_priority(self, api_client, admin_a, column_a, task_a):
        Task.objects.create(column=column_a, title='Low Pri', priority='low', position=2)
        api_client.force_authenticate(admin_a)
        res = api_client.get(TASKS_URL, {'priority': 'medium'})
        assert res.status_code == status.HTTP_200_OK
        assert all(t['priority'] == 'medium' for t in res.data['results'])

    def test_search_by_title(self, api_client, admin_a, column_a, task_a):
        Task.objects.create(column=column_a, title='Unrelated', priority='low', position=2)
        api_client.force_authenticate(admin_a)
        res = api_client.get(TASKS_URL, {'search': 'Task A'})
        assert res.status_code == status.HTTP_200_OK
        ids = [t['id'] for t in res.data['results']]
        assert task_a.id in ids

    def test_filter_by_assignee_id(self, api_client, admin_a, employee_a, column_a, task_a):
        task_a.assignee = employee_a
        task_a.save()
        unassigned = Task.objects.create(column=column_a, title='Unassigned', priority='low', position=2)
        api_client.force_authenticate(admin_a)
        res = api_client.get(TASKS_URL, {'assignee_id': employee_a.id})
        assert res.status_code == status.HTTP_200_OK
        ids = [t['id'] for t in res.data['results']]
        assert task_a.id in ids
        assert unassigned.id not in ids

    def test_filter_by_label_ids(self, api_client, admin_a, column_a, task_a, label_a):
        task_a.labels.add(label_a)
        unlabelled = Task.objects.create(column=column_a, title='No Label', priority='low', position=2)
        api_client.force_authenticate(admin_a)
        res = api_client.get(TASKS_URL, {'label_ids': str(label_a.id)})
        assert res.status_code == status.HTTP_200_OK
        ids = [t['id'] for t in res.data['results']]
        assert task_a.id in ids
        assert unlabelled.id not in ids

    def test_superadmin_sees_all_companies(
        self, api_client, superadmin, task_a, column_b
    ):
        task_b = Task.objects.create(column=column_b, title='Other Co', priority='low', position=1)
        api_client.force_authenticate(superadmin)
        res = api_client.get(TASKS_URL)
        assert res.status_code == status.HTTP_200_OK
        ids = [t['id'] for t in res.data['results']]
        assert task_a.id in ids
        assert task_b.id in ids


# ---------------------------------------------------------------------------
# GET /api/v1/crm/tasks/<id>/ — Retrieve (detail)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTaskRetrieve:
    def test_unauthenticated_returns_401(self, api_client, task_a):
        res = api_client.get(task_url(task_a.id))
        assert res.status_code == status.HTTP_401_UNAUTHORIZED

    def test_employee_can_retrieve_own_company_task(self, api_client, employee_a, task_a):
        api_client.force_authenticate(employee_a)
        res = api_client.get(task_url(task_a.id))
        assert res.status_code == status.HTTP_200_OK
        assert res.data['id'] == task_a.id

    def test_detail_includes_checklists_and_counts(self, api_client, employee_a, task_a):
        api_client.force_authenticate(employee_a)
        res = api_client.get(task_url(task_a.id))
        assert res.status_code == status.HTTP_200_OK
        assert 'checklists' in res.data
        assert 'comments_count' in res.data
        assert 'attachments_count' in res.data
        assert 'history' in res.data

    def test_detail_history_is_list(self, api_client, employee_a, task_a):
        TaskHistory.objects.create(
            task=task_a, user=employee_a, action='moved',
            old_value='1', new_value='2',
        )
        api_client.force_authenticate(employee_a)
        res = api_client.get(task_url(task_a.id))
        assert res.status_code == status.HTTP_200_OK
        assert isinstance(res.data['history'], list)
        assert len(res.data['history']) >= 1

    def test_detail_history_max_10_entries(self, api_client, employee_a, task_a):
        for i in range(15):
            TaskHistory.objects.create(
                task=task_a, user=employee_a, action='updated',
                old_value=str(i), new_value=str(i + 1),
            )
        api_client.force_authenticate(employee_a)
        res = api_client.get(task_url(task_a.id))
        assert res.status_code == status.HTTP_200_OK
        assert len(res.data['history']) <= 10

    def test_cross_company_task_returns_404(self, api_client, employee_a, column_b):
        task_b = Task.objects.create(column=column_b, title='Other', priority='low', position=1)
        api_client.force_authenticate(employee_a)
        res = api_client.get(task_url(task_b.id))
        assert res.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# PATCH /api/v1/crm/tasks/<id>/ — Update
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTaskUpdate:
    def test_unauthenticated_returns_401(self, api_client, task_a):
        res = api_client.patch(task_url(task_a.id), {'title': 'X'}, format='json')
        assert res.status_code == status.HTTP_401_UNAUTHORIZED

    def test_employee_can_update_title(self, api_client, employee_a, task_a):
        api_client.force_authenticate(employee_a)
        res = api_client.patch(task_url(task_a.id), {'title': 'Updated Title'}, format='json')
        assert res.status_code == status.HTTP_200_OK
        assert res.data['title'] == 'Updated Title'

    def test_update_assignee_sends_notification(self, api_client, admin_a, employee_a, task_a):
        api_client.force_authenticate(admin_a)
        assert Notification.objects.filter(user=employee_a, notification_type='task_assigned').count() == 0
        res = api_client.patch(task_url(task_a.id), {'assignee_id': employee_a.id}, format='json')
        assert res.status_code == status.HTTP_200_OK
        assert Notification.objects.filter(user=employee_a, notification_type='task_assigned').count() == 1

    def test_update_assignee_same_no_duplicate_notification(self, api_client, admin_a, employee_a, task_a):
        # Assign first time
        task_a.assignee = employee_a
        task_a.save()
        api_client.force_authenticate(admin_a)
        before = Notification.objects.filter(user=employee_a, notification_type='task_assigned').count()
        # Update with same assignee — no new notification
        api_client.patch(task_url(task_a.id), {'assignee_id': employee_a.id}, format='json')
        after = Notification.objects.filter(user=employee_a, notification_type='task_assigned').count()
        assert after == before

    def test_cross_company_assignee_returns_400(self, api_client, admin_a, task_a, employee_b):
        api_client.force_authenticate(admin_a)
        res = api_client.patch(task_url(task_a.id), {'assignee_id': employee_b.id}, format='json')
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        assert 'assignee_id' in str(res.data)

    def test_update_labels(self, api_client, admin_a, task_a, label_a):
        api_client.force_authenticate(admin_a)
        res = api_client.patch(task_url(task_a.id), {'label_ids': [label_a.id]}, format='json')
        assert res.status_code == status.HTTP_200_OK
        assert label_a.id in res.data['labels']

    def test_update_priority(self, api_client, admin_a, task_a):
        api_client.force_authenticate(admin_a)
        res = api_client.patch(task_url(task_a.id), {'priority': 'urgent'}, format='json')
        assert res.status_code == status.HTTP_200_OK
        assert res.data['priority'] == 'urgent'

    def test_cross_company_task_returns_404(self, api_client, employee_a, column_b):
        task_b = Task.objects.create(column=column_b, title='Other', priority='low', position=1)
        api_client.force_authenticate(employee_a)
        res = api_client.patch(task_url(task_b.id), {'title': 'Hack'}, format='json')
        assert res.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# DELETE /api/v1/crm/tasks/<id>/ — Destroy (soft delete)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTaskDelete:
    def test_unauthenticated_returns_401(self, api_client, task_a):
        res = api_client.delete(task_url(task_a.id))
        assert res.status_code == status.HTTP_401_UNAUTHORIZED

    def test_employee_can_delete_task(self, api_client, employee_a, task_a):
        api_client.force_authenticate(employee_a)
        res = api_client.delete(task_url(task_a.id))
        assert res.status_code == status.HTTP_204_NO_CONTENT

    def test_deleted_task_is_soft_deleted(self, api_client, admin_a, task_a):
        api_client.force_authenticate(admin_a)
        api_client.delete(task_url(task_a.id))
        task_a.refresh_from_db()
        assert task_a.is_deleted is True

    def test_soft_deleted_task_not_in_list(self, api_client, admin_a, task_a):
        api_client.force_authenticate(admin_a)
        api_client.delete(task_url(task_a.id))
        res = api_client.get(TASKS_URL)
        ids = [t['id'] for t in res.data['results']]
        assert task_a.id not in ids

    def test_cross_company_task_returns_404(self, api_client, employee_a, column_b):
        task_b = Task.objects.create(column=column_b, title='Other', priority='low', position=1)
        api_client.force_authenticate(employee_a)
        res = api_client.delete(task_url(task_b.id))
        assert res.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# POST /api/v1/crm/tasks/<id>/move/ — Move action
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTaskMove:
    def test_move_task_to_another_column(self, api_client, admin_a, task_a, column_a2):
        api_client.force_authenticate(admin_a)
        res = api_client.post(task_move_url(task_a.id), {
            'column_id': column_a2.id, 'order': 1
        }, format='json')
        assert res.status_code == status.HTTP_200_OK
        task_a.refresh_from_db()
        assert task_a.column_id == column_a2.id
        assert task_a.position == 1

    def test_move_records_history(self, api_client, admin_a, task_a, column_a2):
        api_client.force_authenticate(admin_a)
        api_client.post(task_move_url(task_a.id), {
            'column_id': column_a2.id, 'order': 1
        }, format='json')
        assert TaskHistory.objects.filter(task=task_a, action='moved').exists()

    def test_move_without_order_appends_to_end(self, api_client, admin_a, task_a, column_a2):
        """When order is omitted, task is placed at the end and positions are normalised."""
        Task.objects.create(
            column=column_a2, title='Existing', priority='low', position=5, created_by=admin_a,
        )
        api_client.force_authenticate(admin_a)
        res = api_client.post(task_move_url(task_a.id), {'column_id': column_a2.id}, format='json')
        assert res.status_code == status.HTTP_200_OK
        task_a.refresh_from_db()
        assert task_a.column_id == column_a2.id
        # After normalisation: existing task → 1, moved task → 2 (appended to end).
        positions = list(
            Task.objects.filter(column=column_a2).order_by('position').values_list('position', flat=True)
        )
        assert positions == [1, 2]
        assert task_a.position == 2

    def test_move_unauthenticated_returns_401(self, api_client, task_a, column_a2):
        res = api_client.post(task_move_url(task_a.id), {'column_id': column_a2.id}, format='json')
        assert res.status_code == status.HTTP_401_UNAUTHORIZED

    def test_move_guest_returns_403(self, api_client, guest_user, task_a, column_a2):
        api_client.force_authenticate(guest_user)
        res = api_client.post(task_move_url(task_a.id), {'column_id': column_a2.id}, format='json')
        assert res.status_code == status.HTTP_403_FORBIDDEN

    def test_move_to_cross_company_column_returns_400(
        self, api_client, admin_a, task_a, column_b
    ):
        api_client.force_authenticate(admin_a)
        res = api_client.post(task_move_url(task_a.id), {'column_id': column_b.id}, format='json')
        assert res.status_code == status.HTTP_400_BAD_REQUEST

    def test_move_wip_limit_exceeded_returns_400(self, api_client, admin_a, task_a, column_a2):
        """Moving to a column at WIP capacity returns 400."""
        column_a2.wip_limit = 1
        column_a2.save()
        # Fill the column to capacity with a different task
        Task.objects.create(
            column=column_a2, title='Blocking', priority='low', position=1, created_by=admin_a,
        )
        api_client.force_authenticate(admin_a)
        res = api_client.post(task_move_url(task_a.id), {'column_id': column_a2.id}, format='json')
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        assert 'WIP limit reached' in str(res.data)

    def test_move_wip_limit_not_exceeded_returns_200(self, api_client, admin_a, task_a, column_a2):
        """Moving to a column under WIP capacity succeeds."""
        column_a2.wip_limit = 2
        column_a2.save()
        # One task already in target column — still room for one more
        Task.objects.create(
            column=column_a2, title='Existing', priority='low', position=1, created_by=admin_a,
        )
        api_client.force_authenticate(admin_a)
        res = api_client.post(task_move_url(task_a.id), {'column_id': column_a2.id}, format='json')
        assert res.status_code == status.HTTP_200_OK

    def test_move_wip_limit_zero_means_no_limit(self, api_client, admin_a, task_a, column_a2):
        """wip_limit=0 is treated as unlimited — any number of tasks allowed."""
        column_a2.wip_limit = 0
        column_a2.save()
        for i in range(5):
            Task.objects.create(
                column=column_a2, title=f'Task {i}', priority='low', position=i, created_by=admin_a,
            )
        api_client.force_authenticate(admin_a)
        res = api_client.post(task_move_url(task_a.id), {'column_id': column_a2.id}, format='json')
        assert res.status_code == status.HTTP_200_OK

    def test_move_task_not_counted_in_wip_of_target(self, api_client, admin_a, task_a, column_a2):
        """The task being moved should not count toward the WIP of the target column."""
        # Move task_a into column_a2 first, then move it back and forth
        task_a.column = column_a2
        task_a.save()
        column_a2.wip_limit = 1
        column_a2.save()
        # task_a is already in column_a2, wip_limit is 1 — moving it within same column is ok
        api_client.force_authenticate(admin_a)
        res = api_client.post(task_move_url(task_a.id), {'column_id': column_a2.id, 'order': 1}, format='json')
        assert res.status_code == status.HTTP_200_OK


# ---------------------------------------------------------------------------
# POST /api/v1/crm/tasks/<id>/archive/ — Archive action
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTaskArchive:
    def test_archive_task_returns_200(self, api_client, admin_a, task_a):
        api_client.force_authenticate(admin_a)
        res = api_client.post(task_archive_url(task_a.id))
        assert res.status_code == status.HTTP_200_OK
        assert res.data['detail'] == 'Task archived'

    def test_archive_sets_is_deleted(self, api_client, admin_a, task_a):
        api_client.force_authenticate(admin_a)
        api_client.post(task_archive_url(task_a.id))
        task_a.refresh_from_db()
        assert task_a.is_deleted is True

    def test_archive_task_not_in_list(self, api_client, admin_a, task_a):
        """Archived (soft-deleted) tasks must not appear in the task list."""
        api_client.force_authenticate(admin_a)
        api_client.post(task_archive_url(task_a.id))
        res = api_client.get(TASKS_URL)
        ids = [t['id'] for t in res.data['results']]
        assert task_a.id not in ids

    def test_archive_unauthenticated_returns_401(self, api_client, task_a):
        res = api_client.post(task_archive_url(task_a.id))
        assert res.status_code == status.HTTP_401_UNAUTHORIZED

    def test_archive_guest_returns_403(self, api_client, guest_user, task_a):
        api_client.force_authenticate(guest_user)
        res = api_client.post(task_archive_url(task_a.id))
        assert res.status_code == status.HTTP_403_FORBIDDEN

    def test_archive_employee_can_archive(self, api_client, employee_a, task_a):
        api_client.force_authenticate(employee_a)
        res = api_client.post(task_archive_url(task_a.id))
        assert res.status_code == status.HTTP_200_OK

    def test_archive_cross_company_task_returns_404(self, api_client, admin_a, column_b):
        task_b = Task.objects.create(column=column_b, title='Other', priority='low', position=1)
        api_client.force_authenticate(admin_a)
        res = api_client.post(task_archive_url(task_b.id))
        assert res.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# Position normalization
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTaskPositionNormalization:
    """Positions must always be sequential (1, 2, 3, …) with no gaps."""

    def test_create_sets_sequential_position(self, api_client, admin_a, column_a, board_a):
        """Tasks created one after another receive sequential positions."""
        api_client.force_authenticate(admin_a)
        ids = []
        for i in range(3):
            res = api_client.post(TASKS_URL, {
                'board_id': board_a.id,
                'column_id': column_a.id,
                'title': f'Task {i}',
                'priority': 'low',
            }, format='json')
            assert res.status_code == status.HTTP_201_CREATED
            ids.append(res.data['id'])

        positions = list(
            Task.objects.filter(column=column_a).order_by('position').values_list('position', flat=True)
        )
        assert positions == list(range(1, len(positions) + 1)), f"Gaps found: {positions}"

    def test_delete_normalizes_remaining_positions(self, api_client, admin_a, column_a, board_a):
        """After deleting a task the remaining tasks must have no gaps."""
        # Create tasks with artificial gaps to simulate pre-existing gap state.
        Task.objects.create(column=column_a, title='T1', priority='low', position=1, created_by=admin_a)
        t2 = Task.objects.create(column=column_a, title='T2', priority='low', position=5, created_by=admin_a)
        Task.objects.create(column=column_a, title='T3', priority='low', position=12, created_by=admin_a)

        api_client.force_authenticate(admin_a)
        api_client.delete(task_url(t2.id))

        # t2 is soft-deleted; only t1 and t3 remain active.
        positions = list(
            Task.objects.filter(column=column_a).order_by('position').values_list('position', flat=True)
        )
        assert positions == [1, 2], f"Expected [1, 2], got {positions}"

    def test_archive_normalizes_remaining_positions(self, api_client, admin_a, column_a, board_a):
        """After archiving a task the remaining tasks must have no gaps."""
        t1 = Task.objects.create(column=column_a, title='T1', priority='low', position=1, created_by=admin_a)
        Task.objects.create(column=column_a, title='T2', priority='low', position=5, created_by=admin_a)
        Task.objects.create(column=column_a, title='T3', priority='low', position=12, created_by=admin_a)

        api_client.force_authenticate(admin_a)
        api_client.post(task_archive_url(t1.id))

        # t1 is soft-deleted; t2 and t3 remain and must be renumbered.
        positions = list(
            Task.objects.filter(column=column_a).order_by('position').values_list('position', flat=True)
        )
        assert positions == [1, 2], f"Expected [1, 2], got {positions}"

    def test_move_normalizes_source_and_target_columns(self, api_client, admin_a, column_a, column_a2, board_a):
        """Moving a task re-normalizes positions in both the source and the target column."""
        # Source column: tasks with gaps.
        t1 = Task.objects.create(column=column_a, title='T1', priority='low', position=1, created_by=admin_a)
        Task.objects.create(column=column_a, title='T2', priority='low', position=10, created_by=admin_a)
        # Target column: task with a high position.
        Task.objects.create(column=column_a2, title='T3', priority='low', position=99, created_by=admin_a)

        api_client.force_authenticate(admin_a)
        res = api_client.post(task_move_url(t1.id), {'column_id': column_a2.id}, format='json')
        assert res.status_code == status.HTTP_200_OK

        # Source column (column_a) now has only t2 — must be at position 1.
        src_positions = list(
            Task.objects.filter(column=column_a).order_by('position').values_list('position', flat=True)
        )
        assert src_positions == [1], f"Source column gaps: {src_positions}"

        # Target column (column_a2) now has t3 and t1 — must be sequential.
        tgt_positions = list(
            Task.objects.filter(column=column_a2).order_by('position').values_list('position', flat=True)
        )
        assert tgt_positions == list(range(1, len(tgt_positions) + 1)), f"Target column gaps: {tgt_positions}"

    def test_move_without_order_appends_sequentially(self, api_client, admin_a, column_a, column_a2, board_a):
        """Moving without an explicit order places the task at the next sequential position."""
        t1 = Task.objects.create(column=column_a, title='T1', priority='low', position=1, created_by=admin_a)
        # Target column already has two tasks with a gap.
        Task.objects.create(column=column_a2, title='E1', priority='low', position=1, created_by=admin_a)
        Task.objects.create(column=column_a2, title='E2', priority='low', position=7, created_by=admin_a)

        api_client.force_authenticate(admin_a)
        res = api_client.post(task_move_url(t1.id), {'column_id': column_a2.id}, format='json')
        assert res.status_code == status.HTTP_200_OK

        tgt_positions = list(
            Task.objects.filter(column=column_a2).order_by('position').values_list('position', flat=True)
        )
        assert tgt_positions == [1, 2, 3], f"Expected [1, 2, 3], got {tgt_positions}"
