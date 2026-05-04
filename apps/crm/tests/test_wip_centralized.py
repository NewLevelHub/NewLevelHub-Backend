import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.crm.models import Board, Column, Task
from apps.users.models import User

TASKS_URL = '/api/v1/crm/tasks/'


def task_url(pk):
    return f'/api/v1/crm/tasks/{pk}/'


def task_move_url(pk):
    return f'/api/v1/crm/tasks/{pk}/move/'


def column_url(board_pk, col_pk):
    return f'/api/v1/crm/boards/{board_pk}/columns/{col_pk}/'


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='WIP Co', plan='standard', max_boards=10)


@pytest.fixture
def admin(db, company):
    return User.objects.create_user(
        email='admin@wip.test',
        password='pass',
        first_name='Admin',
        last_name='WIP',
        role='company_admin',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def board(db, company, admin):
    return Board.objects.create(company=company, name='WIP Board', created_by=admin)


@pytest.fixture
def col_no_limit(db, board):
    return Column.objects.create(board=board, name='No Limit', position=1, wip_limit=0)


@pytest.fixture
def col_limit_1(db, board):
    return Column.objects.create(board=board, name='Limit 1', position=2, wip_limit=1)


@pytest.fixture
def col_limit_2(db, board):
    return Column.objects.create(board=board, name='Limit 2', position=3, wip_limit=2)


def _make_task(column, admin, title='Task', is_archived=False):
    return Task.objects.create(
        column=column,
        title=title,
        priority='medium',
        position=1,
        created_by=admin,
        is_archived=is_archived,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestWipLimitCentralized:

    def test_create_task_wip_exceeded_returns_wip_limit_exceeded_code(
        self, api_client, admin, board, col_limit_1
    ):
        _make_task(col_limit_1, admin, title='Existing')
        api_client.force_authenticate(admin)
        res = api_client.post(TASKS_URL, {
            'board_id': board.id,
            'column_id': col_limit_1.id,
            'title': 'Over limit',
            'priority': 'low',
        }, format='json')
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        assert 'wip_limit_exceeded' in res.data['detail']

    def test_create_task_wip_not_exceeded_succeeds(
        self, api_client, admin, board, col_limit_2
    ):
        _make_task(col_limit_2, admin, title='First')
        api_client.force_authenticate(admin)
        res = api_client.post(TASKS_URL, {
            'board_id': board.id,
            'column_id': col_limit_2.id,
            'title': 'Second',
            'priority': 'low',
        }, format='json')
        assert res.status_code == status.HTTP_201_CREATED

    def test_move_task_wip_exceeded_returns_wip_limit_exceeded_code(
        self, api_client, admin, board, col_no_limit, col_limit_1
    ):
        source_task = _make_task(col_no_limit, admin, title='Moving task')
        _make_task(col_limit_1, admin, title='Already there')
        api_client.force_authenticate(admin)
        res = api_client.post(task_move_url(source_task.pk), {
            'column_id': col_limit_1.id,
        }, format='json')
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        assert 'wip_limit_exceeded' in res.data['detail']

    def test_move_task_wip_not_exceeded_succeeds(
        self, api_client, admin, board, col_no_limit, col_limit_2
    ):
        source_task = _make_task(col_no_limit, admin, title='Moving task')
        _make_task(col_limit_2, admin, title='Already there')
        api_client.force_authenticate(admin)
        res = api_client.post(task_move_url(source_task.pk), {
            'column_id': col_limit_2.id,
        }, format='json')
        assert res.status_code == status.HTTP_200_OK

    def test_unarchive_task_wip_exceeded_returns_wip_limit_exceeded_code(
        self, api_client, admin, board, col_limit_1
    ):
        _make_task(col_limit_1, admin, title='Active')
        archived_task = _make_task(col_limit_1, admin, title='Archived', is_archived=True)
        api_client.force_authenticate(admin)
        res = api_client.patch(task_url(archived_task.pk), {
            'board_id': board.id,
            'is_archived': False,
        }, format='json')
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        assert 'wip_limit_exceeded' in res.data['detail']

    def test_unarchive_task_wip_not_exceeded_succeeds(
        self, api_client, admin, board, col_limit_2
    ):
        _make_task(col_limit_2, admin, title='Active')
        archived_task = _make_task(col_limit_2, admin, title='Archived', is_archived=True)
        api_client.force_authenticate(admin)
        res = api_client.patch(task_url(archived_task.pk), {
            'board_id': board.id,
            'is_archived': False,
        }, format='json')
        assert res.status_code == status.HTTP_200_OK

    def test_column_delete_wip_exceeded_returns_wip_limit_exceeded_code(
        self, api_client, admin, board, col_no_limit, col_limit_2
    ):
        for i in range(3):
            _make_task(col_no_limit, admin, title=f'Source task {i}')
        delete_url = column_url(board.pk, col_no_limit.pk) + f'?move_to={col_limit_2.pk}'
        api_client.force_authenticate(admin)
        res = api_client.delete(delete_url)
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        assert 'wip_limit_exceeded' in res.data['detail']

    def test_wip_limit_zero_always_allows_all_operations(
        self, api_client, admin, board, col_no_limit
    ):
        for i in range(100):
            _make_task(col_no_limit, admin, title=f'Task {i}')
        api_client.force_authenticate(admin)
        res = api_client.post(TASKS_URL, {
            'board_id': board.id,
            'column_id': col_no_limit.id,
            'title': 'One more',
            'priority': 'low',
        }, format='json')
        assert res.status_code == status.HTTP_201_CREATED
