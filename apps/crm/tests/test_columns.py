"""
Integration tests for CRM Column management endpoints.

Covers:
- POST   /api/v1/crm/boards/{board_pk}/columns/
- PATCH  /api/v1/crm/boards/{board_pk}/columns/{id}/
- POST   /api/v1/crm/boards/{board_pk}/columns/reorder/
- DELETE /api/v1/crm/boards/{board_pk}/columns/{id}/?move_to=<id>
"""
import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.crm.models import Board, Column, Task
from apps.users.models import User


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='Test Co', plan='basic')


@pytest.fixture
def other_company(db):
    return Company.objects.create(name='Other Co', plan='basic')


@pytest.fixture
def admin(db, company):
    return User.objects.create_user(
        email='admin@test.co',
        password='pass',
        first_name='Admin',
        last_name='User',
        role='company_admin',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='employee@test.co',
        password='pass',
        first_name='Emp',
        last_name='User',
        role='employee',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def guest(db, company):
    return User.objects.create_user(
        email='guest@test.co',
        password='pass',
        first_name='Guest',
        last_name='User',
        role='guest',
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
def board(db, company, admin):
    b = Board.objects.create(company=company, name='Test Board', created_by=admin)
    # Create three default columns
    Column.objects.create(board=b, name='To Do', position=1)
    Column.objects.create(board=b, name='In Progress', position=2)
    Column.objects.create(board=b, name='Done', position=3)
    return b


@pytest.fixture
def other_board(db, other_company, other_admin):
    b = Board.objects.create(company=other_company, name='Other Board', created_by=other_admin)
    Column.objects.create(board=b, name='Col A', position=1)
    return b


def _columns_url(board_pk):
    return f'/api/v1/crm/boards/{board_pk}/columns/'


def _column_detail_url(board_pk, col_pk):
    return f'/api/v1/crm/boards/{board_pk}/columns/{col_pk}/'


def _reorder_url(board_pk):
    return f'/api/v1/crm/boards/{board_pk}/columns/reorder/'


# ---------------------------------------------------------------------------
# Auth / permission baseline
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestColumnAuth:
    def test_unauthenticated_list_returns_401(self, api_client, board):
        response = api_client.get(_columns_url(board.pk))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_unauthenticated_create_returns_401(self, api_client, board):
        response = api_client.post(_columns_url(board.pk), {'name': 'New'}, format='json')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_guest_cannot_create_column(self, api_client, board, guest):
        api_client.force_authenticate(user=guest)
        response = api_client.post(_columns_url(board.pk), {'name': 'New'}, format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_guest_cannot_delete_column(self, api_client, board, guest):
        col = board.columns.first()
        api_client.force_authenticate(user=guest)
        response = api_client.delete(_column_detail_url(board.pk, col.pk) + '?move_to=999')
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_employee_from_other_company_gets_404_on_list(self, api_client, board, other_admin):
        api_client.force_authenticate(user=other_admin)
        response = api_client.get(_columns_url(board.pk))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_employee_cannot_delete_column(self, api_client, board, employee):
        col = Column.objects.filter(board=board).first()
        other_col = Column.objects.filter(board=board).exclude(pk=col.pk).first()
        api_client.force_authenticate(user=employee)
        response = api_client.delete(
            _column_detail_url(board.pk, col.pk) + f'?move_to={other_col.pk}'
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN


# ---------------------------------------------------------------------------
# POST /columns/ — create
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestColumnCreate:
    def test_create_column_happy_path(self, api_client, board, admin):
        api_client.force_authenticate(user=admin)
        response = api_client.post(_columns_url(board.pk), {'name': 'Backlog'}, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        data = response.data
        assert data['name'] == 'Backlog'
        assert data['position'] == 4  # max(1,2,3) + 1
        assert data['board'] == board.pk
        assert 'created_at' in data

    def test_create_column_order_auto_assigned(self, api_client, board, employee):
        api_client.force_authenticate(user=employee)
        r1 = api_client.post(_columns_url(board.pk), {'name': 'Col4'}, format='json')
        r2 = api_client.post(_columns_url(board.pk), {'name': 'Col5'}, format='json')
        assert r1.status_code == status.HTTP_201_CREATED
        assert r2.status_code == status.HTTP_201_CREATED
        assert r1.data['position'] == 4
        assert r2.data['position'] == 5

    def test_create_column_with_wip_limit(self, api_client, board, admin):
        api_client.force_authenticate(user=admin)
        response = api_client.post(_columns_url(board.pk), {'name': 'WIP', 'wip_limit': 5}, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['wip_limit'] == 5

    def test_create_column_wip_limit_optional(self, api_client, board, admin):
        api_client.force_authenticate(user=admin)
        response = api_client.post(_columns_url(board.pk), {'name': 'No Limit'}, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['wip_limit'] == 0  # default

    def test_create_column_board_not_found_returns_404(self, api_client, admin):
        api_client.force_authenticate(user=admin)
        response = api_client.post(_columns_url(99999), {'name': 'Ghost'}, format='json')
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_create_column_other_company_board_returns_403(self, api_client, board, other_admin):
        api_client.force_authenticate(user=other_admin)
        response = api_client.post(_columns_url(board.pk), {'name': 'Intruder'}, format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_create_column_missing_name_returns_400(self, api_client, board, admin):
        api_client.force_authenticate(user=admin)
        response = api_client.post(_columns_url(board.pk), {}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_employee_can_create_column(self, api_client, board, employee):
        api_client.force_authenticate(user=employee)
        response = api_client.post(_columns_url(board.pk), {'name': 'By Employee'}, format='json')
        assert response.status_code == status.HTTP_201_CREATED


# ---------------------------------------------------------------------------
# PATCH /columns/{id}/ — partial_update
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestColumnUpdate:
    def test_rename_column(self, api_client, board, admin):
        col = Column.objects.get(board=board, name='To Do')
        api_client.force_authenticate(user=admin)
        response = api_client.patch(
            _column_detail_url(board.pk, col.pk),
            {'name': 'Renamed'},
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data['name'] == 'Renamed'

    def test_update_wip_limit(self, api_client, board, admin):
        col = Column.objects.get(board=board, name='In Progress')
        api_client.force_authenticate(user=admin)
        response = api_client.patch(
            _column_detail_url(board.pk, col.pk),
            {'wip_limit': 10},
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        col.refresh_from_db()
        assert col.wip_limit == 10

    def test_change_position_shifts_others(self, api_client, board, admin):
        # Columns: To Do=1, In Progress=2, Done=3
        # Move "Done" (pos=3) to position 1 → expects: Done=1, To Do=2, In Progress=3
        col_done = Column.objects.get(board=board, name='Done')
        api_client.force_authenticate(user=admin)
        response = api_client.patch(
            _column_detail_url(board.pk, col_done.pk),
            {'position': 1},
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data['position'] == 1

        col_todo = Column.objects.get(board=board, name='To Do')
        col_inprogress = Column.objects.get(board=board, name='In Progress')
        assert col_todo.position == 2
        assert col_inprogress.position == 3

    def test_move_column_down_shifts_others(self, api_client, board, admin):
        # Columns: To Do=1, In Progress=2, Done=3
        # Move "To Do" (pos=1) to position 3 → expects: In Progress=1, Done=2, To Do=3
        col_todo = Column.objects.get(board=board, name='To Do')
        api_client.force_authenticate(user=admin)
        response = api_client.patch(
            _column_detail_url(board.pk, col_todo.pk),
            {'position': 3},
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data['position'] == 3

        col_inprogress = Column.objects.get(board=board, name='In Progress')
        col_done = Column.objects.get(board=board, name='Done')
        assert col_inprogress.position == 1
        assert col_done.position == 2

    def test_update_column_wrong_board_returns_403(self, api_client, board, other_admin):
        col = Column.objects.filter(board=board).first()
        api_client.force_authenticate(user=other_admin)
        response = api_client.patch(
            _column_detail_url(board.pk, col.pk),
            {'name': 'Hacked'},
            format='json',
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_employee_can_update_column(self, api_client, board, employee):
        col = Column.objects.filter(board=board).first()
        api_client.force_authenticate(user=employee)
        response = api_client.patch(
            _column_detail_url(board.pk, col.pk),
            {'name': 'By Emp'},
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK


# ---------------------------------------------------------------------------
# POST /columns/reorder/ — reorder
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestColumnReorder:
    def test_reorder_happy_path(self, api_client, board, admin):
        cols = list(Column.objects.filter(board=board).order_by('position'))
        # Reverse the order
        reversed_ids = [c.id for c in reversed(cols)]
        api_client.force_authenticate(user=admin)
        response = api_client.post(_reorder_url(board.pk), {'column_ids': reversed_ids}, format='json')
        assert response.status_code == status.HTTP_200_OK
        result_ids = [c['id'] for c in response.data]
        assert result_ids == reversed_ids

        # Verify DB positions
        for new_pos, col_id in enumerate(reversed_ids, start=1):
            col = Column.objects.get(pk=col_id)
            assert col.position == new_pos

    def test_reorder_missing_column_id_returns_400(self, api_client, board, admin):
        cols = list(Column.objects.filter(board=board).order_by('position'))
        # Provide only first two of three
        partial_ids = [cols[0].id, cols[1].id]
        api_client.force_authenticate(user=admin)
        response = api_client.post(_reorder_url(board.pk), {'column_ids': partial_ids}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_reorder_extra_id_returns_400(self, api_client, board, admin):
        cols = list(Column.objects.filter(board=board).order_by('position'))
        all_ids = [c.id for c in cols] + [99999]
        api_client.force_authenticate(user=admin)
        response = api_client.post(_reorder_url(board.pk), {'column_ids': all_ids}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_reorder_wrong_board_column_id_returns_400(self, api_client, board, other_board, admin):
        cols = list(Column.objects.filter(board=board).order_by('position'))
        # Replace one of the column IDs with one from another board
        other_col = Column.objects.filter(board=other_board).first()
        ids = [cols[0].id, cols[1].id, other_col.id]  # wrong: one from another board
        api_client.force_authenticate(user=admin)
        response = api_client.post(_reorder_url(board.pk), {'column_ids': ids}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_reorder_empty_list_returns_400(self, api_client, board, admin):
        api_client.force_authenticate(user=admin)
        response = api_client.post(_reorder_url(board.pk), {'column_ids': []}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_reorder_unauthenticated_returns_401(self, api_client, board):
        response = api_client.post(_reorder_url(board.pk), {'column_ids': [1]}, format='json')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_employee_can_reorder(self, api_client, board, employee):
        cols = list(Column.objects.filter(board=board).order_by('position'))
        reversed_ids = [c.id for c in reversed(cols)]
        api_client.force_authenticate(user=employee)
        response = api_client.post(_reorder_url(board.pk), {'column_ids': reversed_ids}, format='json')
        assert response.status_code == status.HTTP_200_OK


# ---------------------------------------------------------------------------
# DELETE /columns/{id}/?move_to=<id> — destroy
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestColumnDelete:
    def test_delete_happy_path_moves_tasks(self, api_client, board, admin):
        col_todo = Column.objects.get(board=board, name='To Do')
        col_inprogress = Column.objects.get(board=board, name='In Progress')
        col_done = Column.objects.get(board=board, name='Done')

        # Create tasks in To Do
        task1 = Task.objects.create(column=col_todo, title='Task 1', created_by=admin, position=1)
        task2 = Task.objects.create(column=col_todo, title='Task 2', created_by=admin, position=2)

        api_client.force_authenticate(user=admin)
        url = _column_detail_url(board.pk, col_todo.pk) + f'?move_to={col_inprogress.pk}'
        response = api_client.delete(url)
        assert response.status_code == status.HTTP_204_NO_CONTENT

        # Tasks were moved
        task1.refresh_from_db()
        task2.refresh_from_db()
        assert task1.column_id == col_inprogress.pk
        assert task2.column_id == col_inprogress.pk

        # Column is gone
        assert not Column.objects.filter(pk=col_todo.pk).exists()

        # Remaining columns re-normalized: In Progress→1, Done→2
        col_inprogress.refresh_from_db()
        col_done.refresh_from_db()
        assert col_inprogress.position == 1
        assert col_done.position == 2

    def test_delete_without_move_to_returns_400(self, api_client, board, admin):
        col = Column.objects.filter(board=board).first()
        api_client.force_authenticate(user=admin)
        response = api_client.delete(_column_detail_url(board.pk, col.pk))
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_delete_last_column_returns_400(self, api_client, company, admin):
        # Board with a single column
        single_board = Board.objects.create(company=company, name='Single', created_by=admin)
        only_col = Column.objects.create(board=single_board, name='Only', position=1)
        api_client.force_authenticate(user=admin)
        url = _column_detail_url(single_board.pk, only_col.pk) + f'?move_to={only_col.pk}'
        response = api_client.delete(url)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_delete_move_to_from_different_board_returns_400(self, api_client, board, other_board, admin):
        col = Column.objects.filter(board=board).first()
        other_col = Column.objects.filter(board=other_board).first()
        api_client.force_authenticate(user=admin)
        url = _column_detail_url(board.pk, col.pk) + f'?move_to={other_col.pk}'
        response = api_client.delete(url)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_delete_move_to_nonexistent_returns_400(self, api_client, board, admin):
        col = Column.objects.filter(board=board).first()
        api_client.force_authenticate(user=admin)
        url = _column_detail_url(board.pk, col.pk) + '?move_to=99999'
        response = api_client.delete(url)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_employee_cannot_delete_column_returns_403(self, api_client, board, employee):
        col = Column.objects.filter(board=board).first()
        other_col = Column.objects.filter(board=board).exclude(pk=col.pk).first()
        api_client.force_authenticate(user=employee)
        url = _column_detail_url(board.pk, col.pk) + f'?move_to={other_col.pk}'
        response = api_client.delete(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_unauthenticated_delete_returns_401(self, api_client, board):
        col = Column.objects.filter(board=board).first()
        other_col = Column.objects.filter(board=board).exclude(pk=col.pk).first()
        url = _column_detail_url(board.pk, col.pk) + f'?move_to={other_col.pk}'
        response = api_client.delete(url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_delete_column_from_wrong_company_board_returns_403(self, api_client, board, other_admin):
        col = Column.objects.filter(board=board).first()
        other_col = Column.objects.filter(board=board).exclude(pk=col.pk).first()
        api_client.force_authenticate(user=other_admin)
        url = _column_detail_url(board.pk, col.pk) + f'?move_to={other_col.pk}'
        response = api_client.delete(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN
