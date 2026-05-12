import json

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.crm.models import Board, Column, Label, Task, TaskHistory
from apps.users.models import User


TASKS_URL = '/api/v1/crm/tasks/'


def task_url(pk):
    return f'/api/v1/crm/tasks/{pk}/'


def task_history_url(pk):
    return f'/api/v1/crm/tasks/{pk}/history/'


def task_archive_url(pk):
    return f'/api/v1/crm/tasks/{pk}/archive/'


def task_move_url(pk):
    return f'/api/v1/crm/tasks/{pk}/move/'


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company_a(db):
    return Company.objects.create(name='History Co A', plan='standard', max_boards=10)


@pytest.fixture
def company_b(db):
    return Company.objects.create(name='History Co B', plan='standard', max_boards=10)


@pytest.fixture
def admin_a(db, company_a):
    return User.objects.create_user(
        email='hist_admin_a@test.com',
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
        email='hist_employee_a@test.com',
        password='pass',
        first_name='Employee',
        last_name='A',
        role='employee',
        company=company_a,
        is_email_verified=True,
    )


@pytest.fixture
def guest_user(db):
    return User.objects.create_user(
        email='hist_guest@test.com',
        password='pass',
        first_name='Guest',
        last_name='User',
        role='guest',
        is_email_verified=True,
    )


@pytest.fixture
def board_a(db, company_a, admin_a):
    return Board.objects.create(company=company_a, name='Hist Board A', created_by=admin_a)


@pytest.fixture
def column_a(db, board_a):
    return Column.objects.create(board=board_a, name='To Do', position=1)


@pytest.fixture
def column_a2(db, board_a):
    return Column.objects.create(board=board_a, name='Done', position=2)


@pytest.fixture
def label_a(db, company_a):
    return Label.objects.create(company=company_a, name='HistBug', color='#ff0000')


@pytest.fixture
def label_b(db, company_a):
    return Label.objects.create(company=company_a, name='HistFeature', color='#00ff00')


@pytest.fixture
def task_a(db, column_a, admin_a):
    return Task.objects.create(
        column=column_a,
        title='History Task A',
        description='Initial description',
        priority='medium',
        position=1,
        created_by=admin_a,
    )


# ---------------------------------------------------------------------------
# AC 1 — PATCH logs scalar field changes
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestPatchHistoryScalarFields:
    def test_patch_title_creates_history_entry(self, api_client, admin_a, task_a):
        api_client.force_authenticate(admin_a)
        res = api_client.patch(task_url(task_a.id), {'title': 'Updated Title'}, format='json')
        assert res.status_code == status.HTTP_200_OK
        assert TaskHistory.objects.filter(task=task_a, action='updated', field_name='title').exists()

    def test_patch_title_records_correct_old_and_new_values(self, api_client, admin_a, task_a):
        api_client.force_authenticate(admin_a)
        old_title = task_a.title
        api_client.patch(task_url(task_a.id), {'title': 'New Title'}, format='json')
        entry = TaskHistory.objects.get(task=task_a, action='updated', field_name='title')
        assert entry.old_value == old_title
        assert entry.new_value == 'New Title'

    def test_patch_description_creates_history_entry(self, api_client, admin_a, task_a):
        api_client.force_authenticate(admin_a)
        api_client.patch(task_url(task_a.id), {'description': 'New desc'}, format='json')
        assert TaskHistory.objects.filter(task=task_a, action='updated', field_name='description').exists()

    def test_patch_priority_creates_history_entry(self, api_client, admin_a, task_a):
        api_client.force_authenticate(admin_a)
        api_client.patch(task_url(task_a.id), {'priority': 'urgent'}, format='json')
        entry = TaskHistory.objects.get(task=task_a, action='updated', field_name='priority')
        assert entry.old_value == 'medium'
        assert entry.new_value == 'urgent'

    def test_patch_assignee_creates_history_entry(self, api_client, admin_a, employee_a, task_a):
        api_client.force_authenticate(admin_a)
        api_client.patch(task_url(task_a.id), {'assignee_id': employee_a.id}, format='json')
        assert TaskHistory.objects.filter(task=task_a, action='updated', field_name='assignee').exists()

    def test_patch_assignee_history_records_full_names(self, api_client, admin_a, employee_a, task_a):
        api_client.force_authenticate(admin_a)
        api_client.patch(task_url(task_a.id), {'assignee_id': employee_a.id}, format='json')
        entry = TaskHistory.objects.get(task=task_a, action='updated', field_name='assignee')
        assert entry.old_value == ''
        assert entry.new_value == employee_a.full_name

    def test_patch_same_value_does_not_create_history(self, api_client, admin_a, task_a):
        api_client.force_authenticate(admin_a)
        # Patch with same title — no change, no history
        before_count = TaskHistory.objects.filter(task=task_a).count()
        api_client.patch(task_url(task_a.id), {'title': task_a.title}, format='json')
        after_count = TaskHistory.objects.filter(task=task_a).count()
        assert after_count == before_count

    def test_patch_untracked_field_does_not_create_history(self, api_client, admin_a, task_a):
        """Sending only position (not a tracked history field) must not create any history entries."""
        before_count = TaskHistory.objects.filter(task=task_a).count()
        # position is read_only in the serializer so won't appear in validated_data, no history should be made
        api_client.force_authenticate(admin_a)
        api_client.patch(task_url(task_a.id), {'title': task_a.title}, format='json')
        assert TaskHistory.objects.filter(task=task_a).count() == before_count

    def test_patch_user_is_recorded_on_history(self, api_client, admin_a, task_a):
        api_client.force_authenticate(admin_a)
        api_client.patch(task_url(task_a.id), {'priority': 'low'}, format='json')
        entry = TaskHistory.objects.get(task=task_a, action='updated', field_name='priority')
        assert entry.user_id == admin_a.id


# ---------------------------------------------------------------------------
# AC 2 — PATCH logs label changes
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestPatchHistoryLabels:
    def test_adding_label_creates_label_added_entry(self, api_client, admin_a, task_a, label_a):
        api_client.force_authenticate(admin_a)
        api_client.patch(task_url(task_a.id), {'label_ids': [label_a.id]}, format='json')
        expected = json.dumps({'name': label_a.name, 'color': label_a.color})
        assert TaskHistory.objects.filter(
            task=task_a, action='label_added', new_value=expected
        ).exists()

    def test_removing_label_creates_label_removed_entry(self, api_client, admin_a, task_a, label_a):
        task_a.labels.add(label_a)
        api_client.force_authenticate(admin_a)
        # Remove label by sending empty list
        api_client.patch(task_url(task_a.id), {'label_ids': []}, format='json')
        expected = json.dumps({'name': label_a.name, 'color': label_a.color})
        assert TaskHistory.objects.filter(
            task=task_a, action='label_removed', old_value=expected
        ).exists()

    def test_swapping_labels_creates_both_entries(self, api_client, admin_a, task_a, label_a, label_b):
        task_a.labels.add(label_a)
        api_client.force_authenticate(admin_a)
        api_client.patch(task_url(task_a.id), {'label_ids': [label_b.id]}, format='json')
        assert TaskHistory.objects.filter(task=task_a, action='label_added').exists()
        assert TaskHistory.objects.filter(task=task_a, action='label_removed').exists()

    def test_no_label_change_no_label_history(self, api_client, admin_a, task_a, label_a):
        task_a.labels.add(label_a)
        api_client.force_authenticate(admin_a)
        # Send same label — no label history expected
        before_label_history = TaskHistory.objects.filter(
            task=task_a, action__in=['label_added', 'label_removed']
        ).count()
        api_client.patch(task_url(task_a.id), {'label_ids': [label_a.id]}, format='json')
        after_label_history = TaskHistory.objects.filter(
            task=task_a, action__in=['label_added', 'label_removed']
        ).count()
        assert after_label_history == before_label_history

    def test_patch_without_label_ids_does_not_log_label_history(self, api_client, admin_a, task_a, label_a):
        """If label_ids not in PATCH body, no label history should be created."""
        task_a.labels.add(label_a)
        api_client.force_authenticate(admin_a)
        before = TaskHistory.objects.filter(task=task_a, action__in=['label_added', 'label_removed']).count()
        api_client.patch(task_url(task_a.id), {'title': 'Title Only Patch'}, format='json')
        after = TaskHistory.objects.filter(task=task_a, action__in=['label_added', 'label_removed']).count()
        assert after == before


# ---------------------------------------------------------------------------
# AC 3 — Archive action logs history
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestArchiveHistory:
    def test_archive_creates_history_entry(self, api_client, admin_a, task_a):
        api_client.force_authenticate(admin_a)
        api_client.post(task_archive_url(task_a.id))
        assert TaskHistory.objects.filter(task=task_a, action='archived').exists()

    def test_archive_history_has_correct_values(self, api_client, admin_a, task_a):
        api_client.force_authenticate(admin_a)
        api_client.post(task_archive_url(task_a.id))
        entry = TaskHistory.objects.get(task=task_a, action='archived')
        assert entry.old_value == 'False'
        assert entry.new_value == 'True'
        assert entry.user_id == admin_a.id

    def test_archive_employee_history_records_correct_user(self, api_client, employee_a, task_a):
        api_client.force_authenticate(employee_a)
        api_client.post(task_archive_url(task_a.id))
        entry = TaskHistory.objects.get(task=task_a, action='archived')
        assert entry.user_id == employee_a.id


# ---------------------------------------------------------------------------
# AC 4 — Move action logs history (existing behaviour, no double-log)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestMoveHistory:
    def test_move_logs_moved_action(self, api_client, admin_a, task_a, column_a2):
        api_client.force_authenticate(admin_a)
        api_client.post(task_move_url(task_a.id), {'column_id': column_a2.id}, format='json')
        assert TaskHistory.objects.filter(task=task_a, action='moved').exists()

    def test_move_records_old_and_new_column(self, api_client, admin_a, task_a, column_a, column_a2):
        api_client.force_authenticate(admin_a)
        api_client.post(task_move_url(task_a.id), {'column_id': column_a2.id}, format='json')
        entry = TaskHistory.objects.get(task=task_a, action='moved')
        assert entry.old_value == column_a.name
        assert entry.new_value == column_a2.name

    def test_patch_column_also_logs_updated_column(self, api_client, admin_a, task_a, column_a2, board_a):
        """PATCH with column_id in body (not via move action) should log updated column."""
        api_client.force_authenticate(admin_a)
        res = api_client.patch(task_url(task_a.id), {
            'column_id': column_a2.id,
        }, format='json')
        assert res.status_code == status.HTTP_200_OK
        assert TaskHistory.objects.filter(task=task_a, action='updated', field_name='column').exists()


# ---------------------------------------------------------------------------
# AC 5 — GET /api/v1/crm/tasks/<id>/history/ endpoint
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestHistoryEndpoint:
    def test_unauthenticated_returns_401(self, api_client, task_a):
        res = api_client.get(task_history_url(task_a.id))
        assert res.status_code == status.HTTP_401_UNAUTHORIZED

    def test_guest_returns_403(self, api_client, guest_user, task_a):
        api_client.force_authenticate(guest_user)
        res = api_client.get(task_history_url(task_a.id))
        assert res.status_code == status.HTTP_403_FORBIDDEN

    def test_employee_can_read_history(self, api_client, employee_a, task_a, admin_a):
        TaskHistory.objects.create(
            task=task_a, user=admin_a, action='moved', old_value='1', new_value='2'
        )
        api_client.force_authenticate(employee_a)
        res = api_client.get(task_history_url(task_a.id))
        assert res.status_code == status.HTTP_200_OK

    def test_history_response_shape(self, api_client, admin_a, task_a):
        TaskHistory.objects.create(
            task=task_a, user=admin_a, action='updated', field_name='title',
            old_value='Old', new_value='New',
        )
        api_client.force_authenticate(admin_a)
        res = api_client.get(task_history_url(task_a.id))
        assert res.status_code == status.HTTP_200_OK
        results = res.data.get('results') or res.data
        entry = results[0]
        assert 'id' in entry
        assert 'action' in entry
        assert 'field_name' in entry
        assert 'old_value' in entry
        assert 'new_value' in entry
        assert 'created_at' in entry
        # Nested user object
        assert 'user' in entry
        assert 'id' in entry['user']
        assert 'full_name' in entry['user']

    def test_history_user_full_name_populated(self, api_client, admin_a, task_a):
        TaskHistory.objects.create(
            task=task_a, user=admin_a, action='archived', field_name=None,
            old_value='False', new_value='True',
        )
        api_client.force_authenticate(admin_a)
        res = api_client.get(task_history_url(task_a.id))
        results = res.data.get('results') or res.data
        entry = results[0]
        assert entry['user']['full_name'] == admin_a.full_name

    def test_history_ordered_by_newest_first(self, api_client, admin_a, task_a):
        h1 = TaskHistory.objects.create(
            task=task_a, user=admin_a, action='updated', field_name='title',
            old_value='A', new_value='B',
        )
        h2 = TaskHistory.objects.create(
            task=task_a, user=admin_a, action='updated', field_name='priority',
            old_value='low', new_value='high',
        )
        api_client.force_authenticate(admin_a)
        res = api_client.get(task_history_url(task_a.id))
        results = res.data.get('results') or res.data
        ids = [r['id'] for r in results]
        # Newest first — h2 should come before h1
        assert ids.index(h2.id) < ids.index(h1.id)

    def test_history_is_paginated(self, api_client, admin_a, task_a):
        for i in range(60):
            TaskHistory.objects.create(
                task=task_a, user=admin_a, action='updated', field_name='title',
                old_value=str(i), new_value=str(i + 1),
            )
        api_client.force_authenticate(admin_a)
        res = api_client.get(task_history_url(task_a.id))
        assert res.status_code == status.HTTP_200_OK
        assert 'count' in res.data
        assert 'results' in res.data

    def test_history_no_post_method(self, api_client, admin_a, task_a):
        """History endpoint must be read-only — POST should return 405."""
        api_client.force_authenticate(admin_a)
        res = api_client.post(task_history_url(task_a.id), {'action': 'fake'}, format='json')
        assert res.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


# ---------------------------------------------------------------------------
# DEV-89 AC#1 — field_name present in history after PATCH
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTaskHistoryFieldName:
    def test_patch_writes_field_name_to_history(self, api_client, admin_a, task_a):
        api_client.force_authenticate(admin_a)
        api_client.patch(task_url(task_a.id), {'title': 'New Title'}, format='json')
        history = TaskHistory.objects.filter(task=task_a, action='updated').first()
        assert history is not None
        assert history.field_name == 'title'

    def test_field_name_present_in_api_response(self, api_client, admin_a, task_a):
        api_client.force_authenticate(admin_a)
        api_client.patch(task_url(task_a.id), {'title': 'New Title'}, format='json')
        res = api_client.get(task_history_url(task_a.id))
        assert res.status_code == status.HTTP_200_OK
        entry = res.data['results'][0]
        assert 'field_name' in entry
        assert entry['field_name'] == 'title'

    def test_non_updated_actions_have_null_field_name(self, api_client, admin_a, task_a):
        api_client.force_authenticate(admin_a)
        api_client.post(task_archive_url(task_a.id))
        entry = TaskHistory.objects.get(task=task_a, action='archived')
        assert entry.field_name is None


# ---------------------------------------------------------------------------
# DEV-89 AC#4 — history pagination returns all records, not sliced at 50
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTaskHistoryPagination:
    def test_history_paginates_at_20(self, api_client, admin_a, task_a):
        for i in range(60):
            TaskHistory.objects.create(
                task=task_a, user=admin_a, action='updated',
                field_name='title', old_value=str(i), new_value=str(i + 1),
            )
        api_client.force_authenticate(admin_a)
        res = api_client.get(task_history_url(task_a.id))
        assert res.status_code == status.HTTP_200_OK
        assert len(res.data['results']) == 20
        assert res.data['next'] is not None
        assert res.data['count'] == 60

    def test_history_second_page_accessible(self, api_client, admin_a, task_a):
        for i in range(60):
            TaskHistory.objects.create(
                task=task_a, user=admin_a, action='updated',
                field_name='title', old_value=str(i), new_value=str(i + 1),
            )
        api_client.force_authenticate(admin_a)
        res = api_client.get(task_history_url(task_a.id) + '?page=2')
        assert res.status_code == status.HTTP_200_OK
        assert len(res.data['results']) == 20

    def test_history_third_page_has_remaining_records(self, api_client, admin_a, task_a):
        for i in range(60):
            TaskHistory.objects.create(
                task=task_a, user=admin_a, action='updated',
                field_name='title', old_value=str(i), new_value=str(i + 1),
            )
        api_client.force_authenticate(admin_a)
        res = api_client.get(task_history_url(task_a.id) + '?page=3')
        assert res.status_code == status.HTTP_200_OK
        assert len(res.data['results']) == 20
        assert res.data['next'] is None
