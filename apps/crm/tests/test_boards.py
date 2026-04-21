import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.crm.models import Board, Column
from apps.users.models import User

BOARDS_URL = '/api/v1/crm/boards/'


def board_url(pk):
    return f'/api/v1/crm/boards/{pk}/'


def archive_url(pk):
    return f'/api/v1/crm/boards/{pk}/archive/'


def unarchive_url(pk):
    return f'/api/v1/crm/boards/{pk}/unarchive/'


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company_a(db):
    return Company.objects.create(name='Company A', plan='standard', max_boards=5)


@pytest.fixture
def company_b(db):
    return Company.objects.create(name='Company B', plan='standard', max_boards=5)


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
def admin_b(db, company_b):
    return User.objects.create_user(
        email='admin_b@test.com',
        password='pass',
        first_name='Admin',
        last_name='B',
        role='company_admin',
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
        company=None,
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
    return Board.objects.create(company=company_a, name='Board A', created_by=admin_a)


@pytest.fixture
def archived_board_a(db, company_a, admin_a):
    return Board.objects.create(
        company=company_a, name='Archived Board', created_by=admin_a, is_archived=True,
    )


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBoardAuthentication:
    def test_unauthenticated_list_returns_401(self, api_client):
        response = api_client.get(BOARDS_URL)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_unauthenticated_create_returns_401(self, api_client):
        response = api_client.post(BOARDS_URL, {'name': 'X'}, format='json')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_guest_list_returns_403(self, api_client, guest_user):
        api_client.force_authenticate(user=guest_user)
        response = api_client.get(BOARDS_URL)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_guest_create_returns_403(self, api_client, guest_user):
        api_client.force_authenticate(user=guest_user)
        response = api_client.post(BOARDS_URL, {'name': 'X'}, format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBoardCreate:
    def test_employee_creates_board_returns_201(self, api_client, employee_a, company_a):
        api_client.force_authenticate(user=employee_a)
        response = api_client.post(BOARDS_URL, {'name': 'My Board'}, format='json')

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['name'] == 'My Board'
        assert response.data['company'] == company_a.id
        assert Board.objects.filter(name='My Board', company=company_a).exists()

    def test_create_board_creates_3_default_columns(self, api_client, employee_a):
        api_client.force_authenticate(user=employee_a)
        response = api_client.post(BOARDS_URL, {'name': 'With Columns'}, format='json')

        assert response.status_code == status.HTTP_201_CREATED
        board = Board.objects.get(id=response.data['id'])
        columns = Column.objects.filter(board=board).order_by('position')

        assert columns.count() == 3
        assert list(columns.values_list('name', flat=True)) == ['К выполнению', 'В работе', 'Готово']
        assert list(columns.values_list('position', flat=True)) == [0, 1, 2]

    def test_create_board_with_description(self, api_client, admin_a):
        api_client.force_authenticate(user=admin_a)
        response = api_client.post(
            BOARDS_URL, {'name': 'Described', 'description': 'Some desc'}, format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['description'] == 'Some desc'

    def test_6th_board_returns_400_with_hardcoded_limit(self, api_client, admin_a, company_a):
        """Company with max_boards=5: creating a 6th board must fail with 400."""
        for i in range(5):
            Board.objects.create(company=company_a, name=f'Board {i}', created_by=admin_a)

        api_client.force_authenticate(user=admin_a)
        response = api_client.post(BOARDS_URL, {'name': 'Over Limit'}, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'detail' in response.data

    def test_archived_boards_dont_count_toward_limit(self, api_client, admin_a, company_a):
        """Archived boards must not block new board creation."""
        company_a.max_boards = 2
        company_a.save(update_fields=['max_boards'])

        Board.objects.create(company=company_a, name='Active', created_by=admin_a)
        Board.objects.create(company=company_a, name='Archived', created_by=admin_a, is_archived=True)

        api_client.force_authenticate(user=admin_a)
        response = api_client.post(BOARDS_URL, {'name': 'New Board'}, format='json')

        # Active count is 1, limit is 2 — should succeed
        assert response.status_code == status.HTTP_201_CREATED

    def test_create_requires_name(self, api_client, employee_a):
        api_client.force_authenticate(user=employee_a)
        response = api_client.post(BOARDS_URL, {}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# List / filtering
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBoardList:
    def test_list_excludes_archived_by_default(self, api_client, admin_a, board_a, archived_board_a):
        api_client.force_authenticate(user=admin_a)
        response = api_client.get(BOARDS_URL)

        assert response.status_code == status.HTTP_200_OK
        ids = [b['id'] for b in response.data['results']]
        assert board_a.id in ids
        assert archived_board_a.id not in ids

    def test_list_includes_archived_with_param(self, api_client, admin_a, board_a, archived_board_a):
        api_client.force_authenticate(user=admin_a)
        response = api_client.get(BOARDS_URL, {'include_archived': 'true'})

        assert response.status_code == status.HTTP_200_OK
        ids = [b['id'] for b in response.data['results']]
        assert board_a.id in ids
        assert archived_board_a.id in ids

    def test_employee_only_sees_own_company_boards(
        self, api_client, employee_a, admin_b, company_a, company_b,
    ):
        board_a_obj = Board.objects.create(company=company_a, name='A Board', created_by=employee_a)
        board_b_obj = Board.objects.create(company=company_b, name='B Board', created_by=admin_b)

        api_client.force_authenticate(user=employee_a)
        response = api_client.get(BOARDS_URL)

        ids = [b['id'] for b in response.data['results']]
        assert board_a_obj.id in ids
        assert board_b_obj.id not in ids

    def test_superadmin_sees_all_companies_boards(
        self, api_client, superadmin, admin_a, admin_b, company_a, company_b,
    ):
        board_a_obj = Board.objects.create(company=company_a, name='A Board', created_by=admin_a)
        board_b_obj = Board.objects.create(company=company_b, name='B Board', created_by=admin_b)

        api_client.force_authenticate(user=superadmin)
        response = api_client.get(BOARDS_URL)

        ids = [b['id'] for b in response.data['results']]
        assert board_a_obj.id in ids
        assert board_b_obj.id in ids

    def test_superadmin_can_filter_by_company_id(
        self, api_client, superadmin, admin_a, admin_b, company_a, company_b,
    ):
        # Create multiple boards for company_a so the test cannot pass by accident
        # if the filter were applied on board `id` rather than board `company_id`.
        board_a1 = Board.objects.create(company=company_a, name='A Board 1', created_by=admin_a)
        board_a2 = Board.objects.create(company=company_a, name='A Board 2', created_by=admin_a)
        board_b_obj = Board.objects.create(company=company_b, name='B Board', created_by=admin_b)

        api_client.force_authenticate(user=superadmin)
        response = api_client.get(BOARDS_URL, {'company_id': company_a.id})

        assert response.status_code == status.HTTP_200_OK
        ids = [b['id'] for b in response.data['results']]
        # Both company_a boards must appear
        assert board_a1.id in ids
        assert board_a2.id in ids
        # company_b board must be excluded
        assert board_b_obj.id not in ids
        # Total count must match exactly the number of company_a boards
        assert response.data['count'] == 2

    def test_list_response_contains_company_field(self, api_client, admin_a, board_a, company_a):
        api_client.force_authenticate(user=admin_a)
        response = api_client.get(BOARDS_URL)

        assert response.status_code == status.HTTP_200_OK
        result = next(b for b in response.data['results'] if b['id'] == board_a.id)
        assert result['company'] == company_a.id


# ---------------------------------------------------------------------------
# Retrieve / Update / Delete
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBoardRetrieveUpdateDelete:
    def test_retrieve_board(self, api_client, admin_a, board_a):
        api_client.force_authenticate(user=admin_a)
        response = api_client.get(board_url(board_a.id))

        assert response.status_code == status.HTTP_200_OK
        assert response.data['id'] == board_a.id
        assert 'columns' in response.data

    def test_retrieve_other_company_board_returns_404(self, api_client, employee_a, admin_b, company_b):
        board_b = Board.objects.create(company=company_b, name='B Board', created_by=admin_b)
        api_client.force_authenticate(user=employee_a)
        response = api_client.get(board_url(board_b.id))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_partial_update_board(self, api_client, admin_a, board_a):
        api_client.force_authenticate(user=admin_a)
        response = api_client.patch(board_url(board_a.id), {'name': 'Updated'}, format='json')

        assert response.status_code == status.HTTP_200_OK
        board_a.refresh_from_db()
        assert board_a.name == 'Updated'

    def test_delete_board(self, api_client, admin_a, board_a):
        api_client.force_authenticate(user=admin_a)
        response = api_client.delete(board_url(board_a.id))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not Board.objects.filter(id=board_a.id).exists()


# ---------------------------------------------------------------------------
# Archive action
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBoardArchive:
    def test_employee_can_archive_board(self, api_client, employee_a, board_a):
        api_client.force_authenticate(user=employee_a)
        response = api_client.post(archive_url(board_a.id))

        assert response.status_code == status.HTTP_200_OK
        board_a.refresh_from_db()
        assert board_a.is_archived is True

    def test_admin_can_archive_board(self, api_client, admin_a, board_a):
        api_client.force_authenticate(user=admin_a)
        response = api_client.post(archive_url(board_a.id))

        assert response.status_code == status.HTTP_200_OK
        board_a.refresh_from_db()
        assert board_a.is_archived is True

    def test_archive_response_contains_board_data(self, api_client, admin_a, board_a):
        api_client.force_authenticate(user=admin_a)
        response = api_client.post(archive_url(board_a.id))

        assert response.status_code == status.HTTP_200_OK
        assert response.data['id'] == board_a.id
        assert response.data['is_archived'] is True

    def test_superadmin_can_archive_any_board(self, api_client, superadmin, admin_b, company_b):
        board_b = Board.objects.create(company=company_b, name='B Board', created_by=admin_b)
        api_client.force_authenticate(user=superadmin)
        response = api_client.post(archive_url(board_b.id))

        assert response.status_code == status.HTTP_200_OK
        board_b.refresh_from_db()
        assert board_b.is_archived is True

    def test_archive_unauthenticated_returns_401(self, api_client, board_a):
        response = api_client.post(archive_url(board_a.id))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_archive_other_company_board_returns_404_for_employee(
        self, api_client, employee_a, admin_b, company_b,
    ):
        board_b = Board.objects.create(company=company_b, name='B Board', created_by=admin_b)
        api_client.force_authenticate(user=employee_a)
        response = api_client.post(archive_url(board_b.id))
        # 403 from permission check before object lookup
        assert response.status_code in (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND)

    def test_archive_idempotent(self, api_client, admin_a, board_a):
        """Archiving an already-archived board must return 200, not 404."""
        api_client.force_authenticate(user=admin_a)

        response_first = api_client.post(archive_url(board_a.id))
        assert response_first.status_code == status.HTTP_200_OK
        assert response_first.data['is_archived'] is True

        response_second = api_client.post(archive_url(board_a.id))
        assert response_second.status_code == status.HTTP_200_OK
        assert response_second.data['is_archived'] is True

    def test_archive_idempotent_superadmin(self, api_client, superadmin, admin_b, company_b):
        """Superadmin archiving an already-archived board in any company must return 200."""
        board_b = Board.objects.create(company=company_b, name='B Board', created_by=admin_b)
        api_client.force_authenticate(user=superadmin)

        response_first = api_client.post(archive_url(board_b.id))
        assert response_first.status_code == status.HTTP_200_OK

        response_second = api_client.post(archive_url(board_b.id))
        assert response_second.status_code == status.HTTP_200_OK
        assert response_second.data['is_archived'] is True


# ---------------------------------------------------------------------------
# Unarchive action
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBoardUnarchive:
    def test_unarchive_happy_path(self, api_client, admin_a, company_a, archived_board_a):
        """Admin archives a board and then unarchives it — board must be active again."""
        api_client.force_authenticate(user=admin_a)
        response = api_client.post(unarchive_url(archived_board_a.id))

        assert response.status_code == status.HTTP_200_OK
        assert response.data['id'] == archived_board_a.id
        assert response.data['is_archived'] is False
        archived_board_a.refresh_from_db()
        assert archived_board_a.is_archived is False

    def test_unarchive_limit_exceeded(self, api_client, admin_a, company_a):
        """max_boards=2, 2 active + 1 archived → unarchiving the archived one returns 400."""
        company_a.max_boards = 2
        company_a.save(update_fields=['max_boards'])

        Board.objects.create(company=company_a, name='Active 1', created_by=admin_a)
        Board.objects.create(company=company_a, name='Active 2', created_by=admin_a)
        archived = Board.objects.create(
            company=company_a, name='Archived', created_by=admin_a, is_archived=True,
        )

        api_client.force_authenticate(user=admin_a)
        response = api_client.post(unarchive_url(archived.id))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'detail' in response.data
        assert 'лимит' in response.data['detail']
        archived.refresh_from_db()
        assert archived.is_archived is True

    def test_unarchive_already_active_is_idempotent(self, api_client, admin_a, board_a):
        """Unarchiving a board that is already active returns 200 without error."""
        api_client.force_authenticate(user=admin_a)
        response = api_client.post(unarchive_url(board_a.id))

        assert response.status_code == status.HTTP_200_OK
        assert response.data['id'] == board_a.id
        assert response.data['is_archived'] is False

    def test_employee_can_unarchive_board(self, api_client, employee_a, archived_board_a):
        """Employees are allowed to unarchive boards."""
        api_client.force_authenticate(user=employee_a)
        response = api_client.post(unarchive_url(archived_board_a.id))

        assert response.status_code == status.HTTP_200_OK
        archived_board_a.refresh_from_db()
        assert archived_board_a.is_archived is False

    def test_unarchive_superadmin_any_company(self, api_client, superadmin, admin_b, company_b):
        """Superadmin can unarchive a board belonging to any company."""
        board_b = Board.objects.create(
            company=company_b, name='Archived B', created_by=admin_b, is_archived=True,
        )
        api_client.force_authenticate(user=superadmin)
        response = api_client.post(unarchive_url(board_b.id))

        assert response.status_code == status.HTTP_200_OK
        assert response.data['is_archived'] is False
        board_b.refresh_from_db()
        assert board_b.is_archived is False

    def test_unarchive_unauthenticated_returns_401(self, api_client, archived_board_a):
        response = api_client.post(unarchive_url(archived_board_a.id))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_unarchive_other_company_board_returns_404(
        self, api_client, admin_a, admin_b, company_b,
    ):
        """An admin cannot unarchive a board from a different company."""
        board_b = Board.objects.create(
            company=company_b, name='Archived B', created_by=admin_b, is_archived=True,
        )
        api_client.force_authenticate(user=admin_a)
        response = api_client.post(unarchive_url(board_b.id))

        assert response.status_code == status.HTTP_404_NOT_FOUND
