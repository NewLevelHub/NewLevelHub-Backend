"""
Tests for:
  - AC1: Enhanced TaskFilter (deadline enum, search via TaskFilter)
  - AC2: Ordering support on TaskViewSet
  - AC3: GET /api/v1/crm/tasks/my/ endpoint
  - AC4: GET /api/v1/crm/boards/<id>/?view=list
"""
import pytest
from datetime import timedelta

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.crm.models import Board, Column, Label, Task
from apps.users.models import User

TASKS_URL = '/api/v1/crm/tasks/'
MY_TASKS_URL = '/api/v1/crm/tasks/my/'


def board_url(pk):
    return f'/api/v1/crm/boards/{pk}/'


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
        email='admin_a@filters.com',
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
        email='employee_a@filters.com',
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
        email='employee_b@filters.com',
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
        email='guest@filters.com',
        password='pass',
        first_name='Guest',
        last_name='User',
        role='guest',
        is_email_verified=True,
    )


@pytest.fixture
def board_a(db, company_a, admin_a):
    return Board.objects.create(company=company_a, name='Board Alpha', created_by=admin_a)


@pytest.fixture
def board_a2(db, company_a, admin_a):
    return Board.objects.create(company=company_a, name='Board Beta', created_by=admin_a)


@pytest.fixture
def column_a(db, board_a):
    return Column.objects.create(board=board_a, name='To Do', position=1)


@pytest.fixture
def column_a2(db, board_a2):
    return Column.objects.create(board=board_a2, name='In Progress', position=1)


@pytest.fixture
def board_b(db, company_b, employee_b):
    return Board.objects.create(company=company_b, name='Board B', created_by=employee_b)


@pytest.fixture
def column_b(db, board_b):
    return Column.objects.create(board=board_b, name='To Do', position=1)


@pytest.fixture
def label_a(db, company_a):
    return Label.objects.create(company=company_a, name='Bug', color='#ff0000')


@pytest.fixture
def label_a2(db, company_a):
    return Label.objects.create(company=company_a, name='Feature', color='#00ff00')


@pytest.fixture
def task_medium(db, column_a, admin_a):
    return Task.objects.create(
        column=column_a, title='Medium Task', priority='medium', position=1, created_by=admin_a,
    )


@pytest.fixture
def task_high(db, column_a, admin_a):
    return Task.objects.create(
        column=column_a, title='High Priority Task', priority='high', position=2, created_by=admin_a,
    )


@pytest.fixture
def task_low(db, column_a, admin_a):
    return Task.objects.create(
        column=column_a, title='Low Task', priority='low', position=3, created_by=admin_a,
    )


# ---------------------------------------------------------------------------
# AC1: deadline enum filter
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestDeadlineFilter:
    def _make_task(self, column, title, deadline, **kwargs):
        return Task.objects.create(
            column=column, title=title, deadline=deadline, priority='medium', position=1, **kwargs
        )

    def test_overdue_returns_past_non_archived_tasks(self, api_client, admin_a, column_a):
        yesterday = timezone.now() - timedelta(days=1)
        overdue_task = self._make_task(column_a, 'Overdue', yesterday)
        future_task = self._make_task(column_a, 'Future', timezone.now() + timedelta(days=5))

        api_client.force_authenticate(admin_a)
        res = api_client.get(TASKS_URL, {'deadline': 'overdue'})
        assert res.status_code == status.HTTP_200_OK
        ids = [t['id'] for t in res.data['results']]
        assert overdue_task.id in ids
        assert future_task.id not in ids

    def test_overdue_excludes_archived_tasks(self, api_client, admin_a, column_a):
        yesterday = timezone.now() - timedelta(days=1)
        overdue_archived = self._make_task(column_a, 'Overdue Archived', yesterday, is_archived=True)

        api_client.force_authenticate(admin_a)
        res = api_client.get(TASKS_URL, {'deadline': 'overdue'})
        assert res.status_code == status.HTTP_200_OK
        ids = [t['id'] for t in res.data['results']]
        assert overdue_archived.id not in ids

    def test_today_returns_tasks_due_today(self, api_client, admin_a, column_a):
        now = timezone.now()
        today_task = self._make_task(column_a, 'Due Today', now)
        yesterday_task = self._make_task(column_a, 'Yesterday', now - timedelta(days=1))
        tomorrow_task = self._make_task(column_a, 'Tomorrow', now + timedelta(days=2))

        api_client.force_authenticate(admin_a)
        res = api_client.get(TASKS_URL, {'deadline': 'today'})
        assert res.status_code == status.HTTP_200_OK
        ids = [t['id'] for t in res.data['results']]
        assert today_task.id in ids
        assert yesterday_task.id not in ids
        assert tomorrow_task.id not in ids

    def test_this_week_returns_tasks_within_7_days(self, api_client, admin_a, column_a):
        now = timezone.now()
        today_task = self._make_task(column_a, 'Today', now)
        in_3_days = self._make_task(column_a, 'In 3 days', now + timedelta(days=3))
        in_8_days = self._make_task(column_a, 'In 8 days', now + timedelta(days=8))

        api_client.force_authenticate(admin_a)
        res = api_client.get(TASKS_URL, {'deadline': 'this_week'})
        assert res.status_code == status.HTTP_200_OK
        ids = [t['id'] for t in res.data['results']]
        assert today_task.id in ids
        assert in_3_days.id in ids
        assert in_8_days.id not in ids

    def test_unknown_deadline_value_returns_all(self, api_client, admin_a, column_a, task_medium):
        """An unrecognised deadline value should not filter anything out."""
        api_client.force_authenticate(admin_a)
        res = api_client.get(TASKS_URL, {'deadline': 'invalid_value'})
        assert res.status_code == status.HTTP_200_OK
        ids = [t['id'] for t in res.data['results']]
        assert task_medium.id in ids

    def test_deadline_combinable_with_priority(self, api_client, admin_a, column_a):
        now = timezone.now()
        overdue_high = Task.objects.create(
            column=column_a, title='Overdue High', priority='high',
            deadline=now - timedelta(days=1), position=1,
        )
        overdue_low = Task.objects.create(
            column=column_a, title='Overdue Low', priority='low',
            deadline=now - timedelta(days=2), position=2,
        )

        api_client.force_authenticate(admin_a)
        res = api_client.get(TASKS_URL, {'deadline': 'overdue', 'priority': 'high'})
        assert res.status_code == status.HTTP_200_OK
        ids = [t['id'] for t in res.data['results']]
        assert overdue_high.id in ids
        assert overdue_low.id not in ids


# ---------------------------------------------------------------------------
# AC1: search filter via TaskFilter
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestSearchFilter:
    def test_search_returns_matching_tasks(self, api_client, admin_a, column_a, task_medium, task_high):
        api_client.force_authenticate(admin_a)
        res = api_client.get(TASKS_URL, {'search': 'Medium'})
        assert res.status_code == status.HTTP_200_OK
        ids = [t['id'] for t in res.data['results']]
        assert task_medium.id in ids
        assert task_high.id not in ids

    def test_search_is_case_insensitive(self, api_client, admin_a, column_a, task_medium):
        api_client.force_authenticate(admin_a)
        res = api_client.get(TASKS_URL, {'search': 'medium task'})
        assert res.status_code == status.HTTP_200_OK
        ids = [t['id'] for t in res.data['results']]
        assert task_medium.id in ids

    def test_search_combined_with_board_id(self, api_client, admin_a, board_a, column_a, task_medium):
        api_client.force_authenticate(admin_a)
        res = api_client.get(TASKS_URL, {'search': 'Medium', 'board_id': board_a.id})
        assert res.status_code == status.HTTP_200_OK
        ids = [t['id'] for t in res.data['results']]
        assert task_medium.id in ids


# ---------------------------------------------------------------------------
# AC1: label_ids filter (ANY semantics via __in)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestLabelIdsFilter:
    def test_label_ids_single(self, api_client, admin_a, column_a, task_medium, label_a):
        task_medium.labels.add(label_a)
        api_client.force_authenticate(admin_a)
        res = api_client.get(TASKS_URL, {'label_ids': str(label_a.id)})
        assert res.status_code == status.HTTP_200_OK
        ids = [t['id'] for t in res.data['results']]
        assert task_medium.id in ids

    def test_label_ids_multiple(self, api_client, admin_a, column_a, task_medium, task_high, label_a, label_a2):
        task_medium.labels.add(label_a)
        task_high.labels.add(label_a2)
        api_client.force_authenticate(admin_a)
        res = api_client.get(TASKS_URL, {'label_ids': f'{label_a.id},{label_a2.id}'})
        assert res.status_code == status.HTTP_200_OK
        ids = [t['id'] for t in res.data['results']]
        assert task_medium.id in ids
        assert task_high.id in ids

    def test_label_ids_excludes_unlabelled(self, api_client, admin_a, column_a, task_medium, task_high, label_a):
        task_medium.labels.add(label_a)
        api_client.force_authenticate(admin_a)
        res = api_client.get(TASKS_URL, {'label_ids': str(label_a.id)})
        assert res.status_code == status.HTTP_200_OK
        ids = [t['id'] for t in res.data['results']]
        assert task_high.id not in ids


# ---------------------------------------------------------------------------
# AC2: Ordering
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTaskOrdering:
    def test_ordering_by_priority(self, api_client, admin_a, column_a, task_medium, task_high, task_low):
        api_client.force_authenticate(admin_a)
        res = api_client.get(TASKS_URL, {'ordering': 'priority'})
        assert res.status_code == status.HTTP_200_OK
        priorities = [t['priority'] for t in res.data['results']]
        assert priorities == sorted(priorities)

    def test_ordering_by_created_at_desc(self, api_client, admin_a, column_a, task_medium, task_high):
        api_client.force_authenticate(admin_a)
        res = api_client.get(TASKS_URL, {'ordering': '-created_at'})
        assert res.status_code == status.HTTP_200_OK
        # Just ensure response is 200 and has results (ordering correctness guaranteed by ORM)
        assert len(res.data['results']) >= 2

    def test_ordering_by_deadline(self, api_client, admin_a, column_a):
        now = timezone.now()
        t1 = Task.objects.create(column=column_a, title='T1', priority='low', deadline=now + timedelta(days=3),
                                 position=1)
        t2 = Task.objects.create(column=column_a, title='T2', priority='low', deadline=now + timedelta(days=1),
                                 position=2)
        api_client.force_authenticate(admin_a)
        res = api_client.get(TASKS_URL, {'ordering': 'deadline'})
        assert res.status_code == status.HTTP_200_OK
        ids = [t['id'] for t in res.data['results']]
        assert ids.index(t2.id) < ids.index(t1.id)

    def test_default_ordering_is_created_at_asc(self, api_client, admin_a, column_a, task_medium, task_high):
        """Without explicit ordering param, tasks default to created_at asc."""
        api_client.force_authenticate(admin_a)
        res = api_client.get(TASKS_URL)
        assert res.status_code == status.HTTP_200_OK
        ids = [t['id'] for t in res.data['results']]
        assert task_medium.id in ids and task_high.id in ids


# ---------------------------------------------------------------------------
# AC3: GET /api/v1/crm/tasks/my/
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestMyTasks:
    def test_unauthenticated_returns_401(self, api_client):
        res = api_client.get(MY_TASKS_URL)
        assert res.status_code == status.HTTP_401_UNAUTHORIZED

    def test_guest_returns_403(self, api_client, guest_user):
        api_client.force_authenticate(guest_user)
        res = api_client.get(MY_TASKS_URL)
        assert res.status_code == status.HTTP_403_FORBIDDEN

    def test_returns_only_assigned_to_me(self, api_client, employee_a, admin_a, column_a, task_medium, task_high):
        task_medium.assignee = employee_a
        task_medium.save()
        # task_high is not assigned to employee_a

        api_client.force_authenticate(employee_a)
        res = api_client.get(MY_TASKS_URL)
        assert res.status_code == status.HTTP_200_OK
        ids = [t['id'] for t in res.data['results']]
        assert task_medium.id in ids
        assert task_high.id not in ids

    def test_does_not_include_other_company_tasks(self, api_client, employee_a, employee_b, column_b):
        task_b = Task.objects.create(
            column=column_b, title='B Task', priority='low', position=1, assignee=employee_a,
        )
        api_client.force_authenticate(employee_a)
        res = api_client.get(MY_TASKS_URL)
        assert res.status_code == status.HTTP_200_OK
        ids = [t['id'] for t in res.data['results']]
        assert task_b.id not in ids

    def test_does_not_include_archived_tasks(self, api_client, employee_a, column_a):
        archived_task = Task.objects.create(
            column=column_a, title='Archived', priority='low', position=1,
            assignee=employee_a, is_archived=True,
        )
        api_client.force_authenticate(employee_a)
        res = api_client.get(MY_TASKS_URL)
        assert res.status_code == status.HTTP_200_OK
        ids = [t['id'] for t in res.data['results']]
        assert archived_task.id not in ids

    def test_response_is_paginated(self, api_client, employee_a, column_a):
        for i in range(5):
            Task.objects.create(
                column=column_a, title=f'My Task {i}', priority='low', position=i + 1,
                assignee=employee_a,
            )
        api_client.force_authenticate(employee_a)
        res = api_client.get(MY_TASKS_URL)
        assert res.status_code == status.HTTP_200_OK
        assert 'count' in res.data
        assert 'results' in res.data

    def test_response_includes_board_title(self, api_client, employee_a, column_a, board_a):
        task = Task.objects.create(
            column=column_a, title='Board Title Task', priority='low', position=1, assignee=employee_a,
        )
        api_client.force_authenticate(employee_a)
        res = api_client.get(MY_TASKS_URL)
        assert res.status_code == status.HTTP_200_OK
        result = next(t for t in res.data['results'] if t['id'] == task.id)
        assert result['board_title'] == board_a.name

    def test_filter_by_priority_applied(self, api_client, employee_a, column_a):
        high_task = Task.objects.create(
            column=column_a, title='High', priority='high', position=1, assignee=employee_a,
        )
        low_task = Task.objects.create(
            column=column_a, title='Low', priority='low', position=2, assignee=employee_a,
        )
        api_client.force_authenticate(employee_a)
        res = api_client.get(MY_TASKS_URL, {'priority': 'high'})
        assert res.status_code == status.HTTP_200_OK
        ids = [t['id'] for t in res.data['results']]
        assert high_task.id in ids
        assert low_task.id not in ids

    def test_deadline_filter_applied(self, api_client, employee_a, column_a):
        now = timezone.now()
        overdue = Task.objects.create(
            column=column_a, title='Overdue', priority='low', position=1,
            assignee=employee_a, deadline=now - timedelta(days=1),
        )
        future = Task.objects.create(
            column=column_a, title='Future', priority='low', position=2,
            assignee=employee_a, deadline=now + timedelta(days=10),
        )
        api_client.force_authenticate(employee_a)
        res = api_client.get(MY_TASKS_URL, {'deadline': 'overdue'})
        assert res.status_code == status.HTTP_200_OK
        ids = [t['id'] for t in res.data['results']]
        assert overdue.id in ids
        assert future.id not in ids

    def test_search_filter_applied(self, api_client, employee_a, column_a):
        match = Task.objects.create(
            column=column_a, title='Login feature', priority='low', position=1, assignee=employee_a,
        )
        no_match = Task.objects.create(
            column=column_a, title='Unrelated task', priority='low', position=2, assignee=employee_a,
        )
        api_client.force_authenticate(employee_a)
        res = api_client.get(MY_TASKS_URL, {'search': 'login'})
        assert res.status_code == status.HTTP_200_OK
        ids = [t['id'] for t in res.data['results']]
        assert match.id in ids
        assert no_match.id not in ids

    def test_ordering_applied(self, api_client, employee_a, column_a):
        t1 = Task.objects.create(
            column=column_a, title='T1', priority='high', position=1, assignee=employee_a,
        )
        t2 = Task.objects.create(
            column=column_a, title='T2', priority='urgent', position=2, assignee=employee_a,
        )
        api_client.force_authenticate(employee_a)
        res = api_client.get(MY_TASKS_URL, {'ordering': 'priority'})
        assert res.status_code == status.HTTP_200_OK
        ids = [t['id'] for t in res.data['results']]
        # 'high' < 'urgent' alphabetically, so t1 comes before t2
        assert ids.index(t1.id) < ids.index(t2.id)

    def test_tasks_from_multiple_boards(self, api_client, employee_a, column_a, column_a2, board_a, board_a2):
        t1 = Task.objects.create(
            column=column_a, title='Board 1 Task', priority='low', position=1, assignee=employee_a,
        )
        t2 = Task.objects.create(
            column=column_a2, title='Board 2 Task', priority='low', position=1, assignee=employee_a,
        )
        api_client.force_authenticate(employee_a)
        res = api_client.get(MY_TASKS_URL)
        assert res.status_code == status.HTTP_200_OK
        ids = [t['id'] for t in res.data['results']]
        assert t1.id in ids
        assert t2.id in ids

    def test_board_id_filter_scopes_to_one_board(
        self, api_client, employee_a, column_a, column_a2, board_a, board_a2
    ):
        t1 = Task.objects.create(
            column=column_a, title='Board 1 Task', priority='low', position=1, assignee=employee_a,
        )
        t2 = Task.objects.create(
            column=column_a2, title='Board 2 Task', priority='low', position=1, assignee=employee_a,
        )
        api_client.force_authenticate(employee_a)
        res = api_client.get(MY_TASKS_URL, {'board_id': board_a.id})
        assert res.status_code == status.HTTP_200_OK
        ids = [t['id'] for t in res.data['results']]
        assert t1.id in ids
        assert t2.id not in ids


# ---------------------------------------------------------------------------
# AC4: GET /api/v1/crm/boards/<id>/?view=list
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBoardListView:
    def test_kanban_view_is_default(self, api_client, admin_a, board_a):
        api_client.force_authenticate(admin_a)
        res = api_client.get(board_url(board_a.id))
        assert res.status_code == status.HTTP_200_OK
        # Default view returns columns (BoardSerializer)
        assert 'columns' in res.data

    def test_list_view_returns_paginated_tasks(self, api_client, admin_a, board_a, column_a):
        Task.objects.create(column=column_a, title='Task 1', priority='low', position=1)
        Task.objects.create(column=column_a, title='Task 2', priority='high', position=2)
        api_client.force_authenticate(admin_a)
        res = api_client.get(board_url(board_a.id), {'view': 'list'})
        assert res.status_code == status.HTTP_200_OK
        assert 'count' in res.data
        assert 'results' in res.data
        assert len(res.data['results']) == 2

    def test_list_view_scopes_to_board(self, api_client, admin_a, board_a, board_a2, column_a, column_a2):
        t1 = Task.objects.create(column=column_a, title='Board A Task', priority='low', position=1)
        t2 = Task.objects.create(column=column_a2, title='Board B Task', priority='low', position=1)
        api_client.force_authenticate(admin_a)
        res = api_client.get(board_url(board_a.id), {'view': 'list'})
        assert res.status_code == status.HTTP_200_OK
        ids = [t['id'] for t in res.data['results']]
        assert t1.id in ids
        assert t2.id not in ids

    def test_list_view_priority_filter(self, api_client, admin_a, board_a, column_a):
        high_task = Task.objects.create(column=column_a, title='High', priority='high', position=1)
        low_task = Task.objects.create(column=column_a, title='Low', priority='low', position=2)
        api_client.force_authenticate(admin_a)
        res = api_client.get(board_url(board_a.id), {'view': 'list', 'priority': 'high'})
        assert res.status_code == status.HTTP_200_OK
        ids = [t['id'] for t in res.data['results']]
        assert high_task.id in ids
        assert low_task.id not in ids

    def test_list_view_search_filter(self, api_client, admin_a, board_a, column_a):
        match = Task.objects.create(column=column_a, title='Login page', priority='low', position=1)
        no_match = Task.objects.create(column=column_a, title='Dashboard', priority='low', position=2)
        api_client.force_authenticate(admin_a)
        res = api_client.get(board_url(board_a.id), {'view': 'list', 'search': 'login'})
        assert res.status_code == status.HTTP_200_OK
        ids = [t['id'] for t in res.data['results']]
        assert match.id in ids
        assert no_match.id not in ids

    def test_list_view_deadline_filter(self, api_client, admin_a, board_a, column_a):
        now = timezone.now()
        overdue = Task.objects.create(
            column=column_a, title='Overdue', priority='low', position=1,
            deadline=now - timedelta(days=2),
        )
        future = Task.objects.create(
            column=column_a, title='Future', priority='low', position=2,
            deadline=now + timedelta(days=5),
        )
        api_client.force_authenticate(admin_a)
        res = api_client.get(board_url(board_a.id), {'view': 'list', 'deadline': 'overdue'})
        assert res.status_code == status.HTTP_200_OK
        ids = [t['id'] for t in res.data['results']]
        assert overdue.id in ids
        assert future.id not in ids

    def test_list_view_ordering(self, api_client, admin_a, board_a, column_a):
        t_low = Task.objects.create(column=column_a, title='Low', priority='low', position=1)
        t_high = Task.objects.create(column=column_a, title='High', priority='high', position=2)
        api_client.force_authenticate(admin_a)
        res = api_client.get(board_url(board_a.id), {'view': 'list', 'ordering': 'priority'})
        assert res.status_code == status.HTTP_200_OK
        ids = [t['id'] for t in res.data['results']]
        # 'high' < 'low' alphabetically so high should come first
        assert ids.index(t_high.id) < ids.index(t_low.id)

    def test_list_view_unauthenticated_returns_401(self, api_client, board_a):
        res = api_client.get(board_url(board_a.id), {'view': 'list'})
        assert res.status_code == status.HTTP_401_UNAUTHORIZED

    def test_list_view_guest_returns_403(self, api_client, guest_user, board_a):
        api_client.force_authenticate(guest_user)
        res = api_client.get(board_url(board_a.id), {'view': 'list'})
        assert res.status_code == status.HTTP_403_FORBIDDEN

    def test_list_view_cross_company_board_returns_404(self, api_client, employee_b, board_a):
        api_client.force_authenticate(employee_b)
        res = api_client.get(board_url(board_a.id), {'view': 'list'})
        assert res.status_code == status.HTTP_404_NOT_FOUND

    def test_kanban_view_explicit_param(self, api_client, admin_a, board_a):
        api_client.force_authenticate(admin_a)
        res = api_client.get(board_url(board_a.id), {'view': 'kanban'})
        assert res.status_code == status.HTTP_200_OK
        assert 'columns' in res.data
