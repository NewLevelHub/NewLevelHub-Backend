import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.crm.models import Board, Column, Task, Comment
from apps.notifications.models import Notification
from apps.users.models import User


def comments_url(task_pk):
    return f'/api/v1/crm/tasks/{task_pk}/comments/'


def comment_detail_url(task_pk, comment_pk):
    return f'/api/v1/crm/tasks/{task_pk}/comments/{comment_pk}/'


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
def employee_a2(db, company_a):
    return User.objects.create_user(
        email='employee_a2@test.com',
        password='pass',
        first_name='Employee',
        last_name='A2',
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
    return Task.objects.create(
        column=column_a,
        title='Task A',
        description='Desc',
        priority='medium',
        position=1,
        created_by=admin_a,
    )


@pytest.fixture
def task_b(db, column_b, employee_b):
    return Task.objects.create(
        column=column_b,
        title='Task B',
        priority='low',
        position=1,
        created_by=employee_b,
    )


@pytest.fixture
def comment_a(db, task_a, employee_a):
    return Comment.objects.create(task=task_a, author=employee_a, text='First comment')


# ---------------------------------------------------------------------------
# GET /api/v1/crm/tasks/<task_pk>/comments/  — List
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCommentList:
    def test_unauthenticated_returns_401(self, api_client, task_a):
        res = api_client.get(comments_url(task_a.id))
        assert res.status_code == status.HTTP_401_UNAUTHORIZED

    def test_guest_returns_403(self, api_client, guest_user, task_a):
        api_client.force_authenticate(guest_user)
        res = api_client.get(comments_url(task_a.id))
        assert res.status_code == status.HTTP_403_FORBIDDEN

    def test_employee_can_list_comments(self, api_client, employee_a, task_a, comment_a):
        api_client.force_authenticate(employee_a)
        res = api_client.get(comments_url(task_a.id))
        assert res.status_code == status.HTTP_200_OK
        ids = [c['id'] for c in res.data['results']]
        assert comment_a.id in ids

    def test_comment_response_shape(self, api_client, employee_a, task_a, comment_a):
        api_client.force_authenticate(employee_a)
        res = api_client.get(comments_url(task_a.id))
        assert res.status_code == status.HTTP_200_OK
        item = next(c for c in res.data['results'] if c['id'] == comment_a.id)
        assert 'id' in item
        assert 'text' in item
        assert 'created_at' in item
        assert 'author' in item
        assert 'id' in item['author']
        assert 'full_name' in item['author']
        assert 'avatar' in item['author']

    def test_comments_ordered_by_created_at_asc(self, api_client, admin_a, task_a):
        c1 = Comment.objects.create(task=task_a, author=admin_a, text='First')
        c2 = Comment.objects.create(task=task_a, author=admin_a, text='Second')
        api_client.force_authenticate(admin_a)
        res = api_client.get(comments_url(task_a.id))
        assert res.status_code == status.HTTP_200_OK
        ids = [c['id'] for c in res.data['results']]
        assert ids.index(c1.id) < ids.index(c2.id)

    def test_cross_company_task_returns_403(self, api_client, employee_a, task_b):
        api_client.force_authenticate(employee_a)
        res = api_client.get(comments_url(task_b.id))
        assert res.status_code == status.HTTP_403_FORBIDDEN

    def test_superadmin_can_list_any_task_comments(self, api_client, superadmin, task_b, employee_b):
        Comment.objects.create(task=task_b, author=employee_b, text='Cross-company comment')
        api_client.force_authenticate(superadmin)
        res = api_client.get(comments_url(task_b.id))
        assert res.status_code == status.HTTP_200_OK


# ---------------------------------------------------------------------------
# POST /api/v1/crm/tasks/<task_pk>/comments/  — Create
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCommentCreate:
    def test_unauthenticated_returns_401(self, api_client, task_a):
        res = api_client.post(comments_url(task_a.id), {'text': 'hi'}, format='json')
        assert res.status_code == status.HTTP_401_UNAUTHORIZED

    def test_guest_returns_403(self, api_client, guest_user, task_a):
        api_client.force_authenticate(guest_user)
        res = api_client.post(comments_url(task_a.id), {'text': 'hi'}, format='json')
        assert res.status_code == status.HTTP_403_FORBIDDEN

    def test_employee_can_create_comment(self, api_client, employee_a, task_a):
        api_client.force_authenticate(employee_a)
        res = api_client.post(comments_url(task_a.id), {'text': 'LGTM'}, format='json')
        assert res.status_code == status.HTTP_201_CREATED
        assert res.data['text'] == 'LGTM'
        assert res.data['author']['id'] == employee_a.id

    def test_author_auto_set_to_request_user(self, api_client, employee_a, task_a):
        api_client.force_authenticate(employee_a)
        res = api_client.post(comments_url(task_a.id), {'text': 'Check'}, format='json')
        assert res.status_code == status.HTTP_201_CREATED
        comment = Comment.objects.get(pk=res.data['id'])
        assert comment.author == employee_a

    def test_task_auto_set_from_url(self, api_client, employee_a, task_a):
        api_client.force_authenticate(employee_a)
        res = api_client.post(comments_url(task_a.id), {'text': 'Check task FK'}, format='json')
        assert res.status_code == status.HTTP_201_CREATED
        comment = Comment.objects.get(pk=res.data['id'])
        assert comment.task_id == task_a.id

    def test_cross_company_task_returns_403(self, api_client, employee_a, task_b):
        api_client.force_authenticate(employee_a)
        res = api_client.post(comments_url(task_b.id), {'text': 'hacked'}, format='json')
        assert res.status_code == status.HTTP_403_FORBIDDEN

    def test_missing_text_returns_400(self, api_client, employee_a, task_a):
        api_client.force_authenticate(employee_a)
        res = api_client.post(comments_url(task_a.id), {}, format='json')
        assert res.status_code == status.HTTP_400_BAD_REQUEST

    def test_nonexistent_task_returns_404(self, api_client, employee_a):
        api_client.force_authenticate(employee_a)
        res = api_client.post(comments_url(99999), {'text': 'hello'}, format='json')
        assert res.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# POST /crm/tasks/<task_pk>/comments/ — Notification side effects (AC 5)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCommentNotifications:
    def test_assignee_notified_on_comment(self, api_client, task_a, employee_a, employee_a2):
        task_a.assignee = employee_a2
        task_a.save()
        api_client.force_authenticate(employee_a)
        api_client.post(comments_url(task_a.id), {'text': 'Hey assignee'}, format='json')
        notif = Notification.objects.filter(
            user=employee_a2,
            notification_type='task_comment',
        ).first()
        assert notif is not None
        assert notif.title == 'Новый комментарий к задаче'
        assert notif.body == task_a.title
        assert notif.url == f'/crm/tasks/{task_a.pk}/'

    def test_creator_notified_on_comment(self, api_client, task_a, employee_a, admin_a):
        # task_a.created_by == admin_a; commenter == employee_a
        api_client.force_authenticate(employee_a)
        api_client.post(comments_url(task_a.id), {'text': 'Hey creator'}, format='json')
        assert Notification.objects.filter(
            user=admin_a,
            notification_type='task_comment',
        ).exists()

    def test_author_not_notified_even_if_assignee(self, api_client, task_a, employee_a):
        # employee_a comments on a task where they are also the assignee
        task_a.assignee = employee_a
        task_a.save()
        api_client.force_authenticate(employee_a)
        api_client.post(comments_url(task_a.id), {'text': 'Self comment'}, format='json')
        assert not Notification.objects.filter(
            user=employee_a,
            notification_type='task_comment',
        ).exists()

    def test_author_not_notified_even_if_creator(self, api_client, task_a, admin_a):
        # admin_a is task creator; they comment on their own task
        api_client.force_authenticate(admin_a)
        api_client.post(comments_url(task_a.id), {'text': 'My own task comment'}, format='json')
        assert not Notification.objects.filter(
            user=admin_a,
            notification_type='task_comment',
        ).exists()

    def test_no_notification_when_no_assignee(self, api_client, task_a, employee_a):
        task_a.assignee = None
        task_a.save()
        initial_count = Notification.objects.filter(notification_type='task_comment').count()
        api_client.force_authenticate(employee_a)
        api_client.post(comments_url(task_a.id), {'text': 'No assignee here'}, format='json')
        # Only admin_a (creator) should be notified
        final_count = Notification.objects.filter(notification_type='task_comment').count()
        assert final_count == initial_count + 1

    def test_single_notification_when_creator_is_assignee(self, api_client, task_a, employee_a, admin_a):
        # admin_a is both creator and assignee; employee_a comments — only 1 notification expected
        task_a.assignee = admin_a
        task_a.save()
        api_client.force_authenticate(employee_a)
        api_client.post(comments_url(task_a.id), {'text': 'Double role'}, format='json')
        count = Notification.objects.filter(user=admin_a, notification_type='task_comment').count()
        assert count == 1


# ---------------------------------------------------------------------------
# PATCH /api/v1/crm/tasks/<task_pk>/comments/<id>/  — Partial update (AC 3)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCommentUpdate:
    def test_unauthenticated_returns_401(self, api_client, task_a, comment_a):
        res = api_client.patch(
            comment_detail_url(task_a.id, comment_a.id), {'text': 'edit'}, format='json'
        )
        assert res.status_code == status.HTTP_401_UNAUTHORIZED

    def test_author_can_edit_own_comment(self, api_client, employee_a, task_a, comment_a):
        api_client.force_authenticate(employee_a)
        res = api_client.patch(
            comment_detail_url(task_a.id, comment_a.id), {'text': 'Updated text'}, format='json'
        )
        assert res.status_code == status.HTTP_200_OK
        assert res.data['text'] == 'Updated text'

    def test_other_employee_cannot_edit_comment(self, api_client, employee_a2, task_a, comment_a):
        api_client.force_authenticate(employee_a2)
        res = api_client.patch(
            comment_detail_url(task_a.id, comment_a.id), {'text': 'Hacked'}, format='json'
        )
        assert res.status_code == status.HTTP_403_FORBIDDEN

    def test_company_admin_cannot_edit_others_comment(self, api_client, admin_a, task_a, comment_a):
        # AC#3: PATCH is author-only; company_admin must be blocked
        api_client.force_authenticate(admin_a)
        res = api_client.patch(
            comment_detail_url(task_a.id, comment_a.id), {'text': 'Admin edit'}, format='json'
        )
        assert res.status_code == status.HTTP_403_FORBIDDEN

    def test_superadmin_cannot_edit_others_comment(self, api_client, superadmin, task_a, comment_a):
        # AC#3: PATCH is strictly author-only — superadmin must also be blocked
        api_client.force_authenticate(superadmin)
        res = api_client.patch(
            comment_detail_url(task_a.id, comment_a.id), {'text': 'Superadmin edit'}, format='json'
        )
        assert res.status_code == status.HTTP_403_FORBIDDEN

    def test_guest_returns_403(self, api_client, guest_user, task_a, comment_a):
        api_client.force_authenticate(guest_user)
        res = api_client.patch(
            comment_detail_url(task_a.id, comment_a.id), {'text': 'nope'}, format='json'
        )
        assert res.status_code == status.HTTP_403_FORBIDDEN


# ---------------------------------------------------------------------------
# DELETE /api/v1/crm/tasks/<task_pk>/comments/<id>/  — Destroy (AC 4)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCommentDelete:
    def test_unauthenticated_returns_401(self, api_client, task_a, comment_a):
        res = api_client.delete(comment_detail_url(task_a.id, comment_a.id))
        assert res.status_code == status.HTTP_401_UNAUTHORIZED

    def test_author_can_delete_own_comment(self, api_client, employee_a, task_a, comment_a):
        api_client.force_authenticate(employee_a)
        res = api_client.delete(comment_detail_url(task_a.id, comment_a.id))
        assert res.status_code == status.HTTP_204_NO_CONTENT
        assert not Comment.objects.filter(pk=comment_a.id).exists()

    def test_other_employee_cannot_delete_comment(self, api_client, employee_a2, task_a, comment_a):
        api_client.force_authenticate(employee_a2)
        res = api_client.delete(comment_detail_url(task_a.id, comment_a.id))
        assert res.status_code == status.HTTP_403_FORBIDDEN

    def test_company_admin_can_delete_any_comment(self, api_client, admin_a, task_a, comment_a):
        api_client.force_authenticate(admin_a)
        res = api_client.delete(comment_detail_url(task_a.id, comment_a.id))
        assert res.status_code == status.HTTP_204_NO_CONTENT

    def test_superadmin_can_delete_any_comment(self, api_client, superadmin, task_a, comment_a):
        api_client.force_authenticate(superadmin)
        res = api_client.delete(comment_detail_url(task_a.id, comment_a.id))
        assert res.status_code == status.HTTP_204_NO_CONTENT

    def test_guest_returns_403(self, api_client, guest_user, task_a, comment_a):
        api_client.force_authenticate(guest_user)
        res = api_client.delete(comment_detail_url(task_a.id, comment_a.id))
        assert res.status_code == status.HTTP_403_FORBIDDEN

    def test_cross_company_admin_cannot_delete(self, api_client, employee_b, task_a, comment_a):
        # employee_b belongs to company_b and should not have access to company_a's task comments
        api_client.force_authenticate(employee_b)
        res = api_client.delete(comment_detail_url(task_a.id, comment_a.id))
        assert res.status_code == status.HTTP_403_FORBIDDEN


# ---------------------------------------------------------------------------
# AC 6 — comments_count in task detail
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCommentsCount:
    def test_task_detail_comments_count_zero_initially(self, api_client, employee_a, task_a):
        api_client.force_authenticate(employee_a)
        res = api_client.get(f'/api/v1/crm/tasks/{task_a.id}/')
        assert res.status_code == status.HTTP_200_OK
        assert res.data['comments_count'] == 0

    def test_task_detail_comments_count_increments(self, api_client, employee_a, task_a):
        Comment.objects.create(task=task_a, author=employee_a, text='First')
        Comment.objects.create(task=task_a, author=employee_a, text='Second')
        api_client.force_authenticate(employee_a)
        res = api_client.get(f'/api/v1/crm/tasks/{task_a.id}/')
        assert res.status_code == status.HTTP_200_OK
        assert res.data['comments_count'] == 2


# ---------------------------------------------------------------------------
# DEV-87 AC#3 — PATCH author-only, DELETE author or company_admin
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCommentPermissions:
    def test_company_admin_cannot_edit_others_comment(self, api_client, admin_a, task_a, comment_a):
        """PATCH: company_admin must NOT be able to edit another user's comment."""
        api_client.force_authenticate(admin_a)
        res = api_client.patch(
            comment_detail_url(task_a.id, comment_a.id), {'text': 'Admin forced edit'}, format='json'
        )
        assert res.status_code == status.HTTP_403_FORBIDDEN

    def test_author_can_edit_own_comment(self, api_client, employee_a, task_a, comment_a):
        """PATCH: the comment author must be able to edit their own comment."""
        api_client.force_authenticate(employee_a)
        res = api_client.patch(
            comment_detail_url(task_a.id, comment_a.id), {'text': 'Self edit'}, format='json'
        )
        assert res.status_code == status.HTTP_200_OK
        assert res.data['text'] == 'Self edit'

    def test_company_admin_can_delete_others_comment(self, api_client, admin_a, task_a, comment_a):
        """DELETE: company_admin must be able to delete another user's comment."""
        api_client.force_authenticate(admin_a)
        res = api_client.delete(comment_detail_url(task_a.id, comment_a.id))
        assert res.status_code == status.HTTP_204_NO_CONTENT
        assert not Comment.objects.filter(pk=comment_a.id).exists()

    def test_author_can_delete_own_comment(self, api_client, employee_a, task_a, comment_a):
        """DELETE: the comment author must be able to delete their own comment."""
        api_client.force_authenticate(employee_a)
        res = api_client.delete(comment_detail_url(task_a.id, comment_a.id))
        assert res.status_code == status.HTTP_204_NO_CONTENT
        assert not Comment.objects.filter(pk=comment_a.id).exists()

    def test_superadmin_cannot_edit_others_comment(self, api_client, superadmin, task_a, comment_a):
        """PATCH: superadmin must NOT be able to edit another user's comment (AC#3: author-only)."""
        api_client.force_authenticate(superadmin)
        res = api_client.patch(
            comment_detail_url(task_a.id, comment_a.id), {'text': 'hacked'}, format='json'
        )
        assert res.status_code == status.HTTP_403_FORBIDDEN
