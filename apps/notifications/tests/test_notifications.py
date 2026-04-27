"""
Integration tests for apps/notifications.

Coverage:
  - create_notification utility
  - GET  /api/v1/notifications/              — list (own only, pagination, filter)
  - GET  /api/v1/notifications/unread-count/ — count
  - POST /api/v1/notifications/{id}/read/    — mark single read
  - POST /api/v1/notifications/read-all/     — mark all read
  - DELETE /api/v1/notifications/{id}/       — delete
  - Unauthenticated → 401 on all endpoints
"""

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.notifications.models import Notification
from apps.notifications.utils import create_notification, NOTIFICATION_TYPES


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def make_user(db, django_user_model):
    """Factory for creating users with the custom User model."""
    def _make(email, role='employee', company=None):
        user = django_user_model.objects.create_user(
            email=email,
            password='testpass123',
            first_name='Test',
            last_name='User',
            role=role,
            company=company,
        )
        return user
    return _make


@pytest.fixture
def make_company(db):
    from apps.companies.models import Company

    def _make(name='Test Co'):
        return Company.objects.create(name=name)

    return _make


@pytest.fixture
def company(make_company):
    return make_company()


@pytest.fixture
def employee(make_user, company):
    return make_user('employee@test.com', role='employee', company=company)


@pytest.fixture
def other_user(make_user, company):
    return make_user('other@test.com', role='employee', company=company)


@pytest.fixture
def auth_client(api_client, employee):
    api_client.force_authenticate(user=employee)
    return api_client


@pytest.fixture
def notification(db, employee):
    return Notification.objects.create(
        user=employee,
        notification_type='booking_confirmed',
        title='Booking Confirmed',
        body='Your booking is ready.',
        url='',
    )


@pytest.fixture
def unread_notifications(db, employee):
    notifs = []
    for i in range(3):
        notifs.append(Notification.objects.create(
            user=employee,
            notification_type='task_assigned',
            title=f'Task {i}',
            body='You have a new task.',
            url='',
            is_read=False,
        ))
    return notifs


# ---------------------------------------------------------------------------
# Utility: create_notification
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCreateNotification:
    def test_creates_notification(self, employee):
        notif = create_notification(
            user=employee,
            notification_type='booking_confirmed',
            title='Test',
            message='Body text',
            link='https://example.com',
        )
        assert notif.pk is not None
        assert notif.user == employee
        assert notif.notification_type == 'booking_confirmed'
        assert notif.title == 'Test'
        assert notif.body == 'Body text'
        assert notif.url == 'https://example.com'
        assert notif.is_read is False

    def test_link_defaults_to_empty_string(self, employee):
        notif = create_notification(
            user=employee,
            notification_type='system',
            title='Hello',
            message='msg',
        )
        assert notif.url == ''

    def test_invalid_type_raises_value_error(self, employee):
        with pytest.raises(ValueError, match='Unknown notification_type'):
            create_notification(
                user=employee,
                notification_type='not_a_real_type',
                title='X',
                message='Y',
            )

    def test_all_spec_types_are_valid(self, employee):
        spec_types = {
            'booking_confirmed', 'booking_reminder', 'booking_cancelled',
            'task_assigned', 'task_moved', 'task_comment', 'task_deadline',
            'guest_validated', 'guest_pass_expiring', 'service_request_update',
            'announcement', 'invitation', 'leave_review', 'system',
        }
        for t in spec_types:
            assert t in NOTIFICATION_TYPES, f"'{t}' missing from NOTIFICATION_TYPES"


# ---------------------------------------------------------------------------
# Unauthenticated → 401
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestUnauthenticated:
    LIST_URL = '/api/v1/notifications/'

    def test_list_requires_auth(self, api_client):
        resp = api_client.get(self.LIST_URL)
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_unread_count_requires_auth(self, api_client):
        resp = api_client.get('/api/v1/notifications/unread-count/')
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_mark_read_requires_auth(self, api_client, notification):
        resp = api_client.post(f'/api/v1/notifications/{notification.pk}/read/')
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_read_all_requires_auth(self, api_client):
        resp = api_client.post('/api/v1/notifications/read-all/')
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_delete_requires_auth(self, api_client, notification):
        resp = api_client.delete(f'/api/v1/notifications/{notification.pk}/')
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# ---------------------------------------------------------------------------
# List endpoint
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestNotificationList:
    URL = '/api/v1/notifications/'

    def test_returns_own_notifications(self, auth_client, notification):
        resp = auth_client.get(self.URL)
        assert resp.status_code == status.HTTP_200_OK
        ids = [n['id'] for n in resp.data['results']]
        assert notification.pk in ids

    def test_does_not_return_other_users_notifications(self, auth_client, other_user):
        other_notif = Notification.objects.create(
            user=other_user,
            notification_type='system',
            title='Other',
            body='',
            url='',
        )
        resp = auth_client.get(self.URL)
        ids = [n['id'] for n in resp.data['results']]
        assert other_notif.pk not in ids

    def test_response_shape(self, auth_client, notification):
        resp = auth_client.get(self.URL)
        assert resp.status_code == status.HTTP_200_OK
        item = resp.data['results'][0]
        assert 'id' in item
        assert 'type' in item
        assert 'title' in item
        assert 'message' in item
        assert 'link' in item
        assert 'is_read' in item
        assert 'created_at' in item
        # model fields should NOT leak through directly
        assert 'notification_type' not in item
        assert 'body' not in item
        assert 'url' not in item

    def test_filter_by_is_read(self, auth_client, employee):
        Notification.objects.create(
            user=employee, notification_type='system',
            title='Unread', body='', url='', is_read=False,
        )
        Notification.objects.create(
            user=employee, notification_type='system',
            title='Read', body='', url='', is_read=True,
        )
        resp = auth_client.get(self.URL, {'is_read': 'false'})
        assert all(not n['is_read'] for n in resp.data['results'])

        resp = auth_client.get(self.URL, {'is_read': 'true'})
        assert all(n['is_read'] for n in resp.data['results'])

    def test_filter_by_notification_type(self, auth_client, employee):
        Notification.objects.create(
            user=employee, notification_type='system',
            title='System', body='', url='',
        )
        Notification.objects.create(
            user=employee, notification_type='announcement',
            title='Announce', body='', url='',
        )
        resp = auth_client.get(self.URL, {'notification_type': 'system'})
        types = [n['type'] for n in resp.data['results']]
        assert all(t == 'system' for t in types)


# ---------------------------------------------------------------------------
# Unread count
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestUnreadCount:
    URL = '/api/v1/notifications/unread-count/'

    def test_returns_correct_count(self, auth_client, unread_notifications):
        resp = auth_client.get(self.URL)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['count'] == 3

    def test_count_zero_when_no_unread(self, auth_client, employee):
        Notification.objects.create(
            user=employee, notification_type='system',
            title='Done', body='', url='', is_read=True,
        )
        resp = auth_client.get(self.URL)
        assert resp.data['count'] == 0

    def test_does_not_count_other_users(self, auth_client, other_user):
        Notification.objects.create(
            user=other_user, notification_type='system',
            title='Other', body='', url='', is_read=False,
        )
        resp = auth_client.get(self.URL)
        assert resp.data['count'] == 0


# ---------------------------------------------------------------------------
# Mark single as read
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestMarkRead:
    def test_marks_as_read(self, auth_client, notification):
        assert not notification.is_read
        resp = auth_client.post(f'/api/v1/notifications/{notification.pk}/read/')
        assert resp.status_code == status.HTTP_200_OK
        notification.refresh_from_db()
        assert notification.is_read is True

    def test_response_contains_notification(self, auth_client, notification):
        resp = auth_client.post(f'/api/v1/notifications/{notification.pk}/read/')
        assert resp.data['id'] == notification.pk
        assert resp.data['is_read'] is True

    def test_cannot_mark_other_users_notification(self, auth_client, other_user):
        other_notif = Notification.objects.create(
            user=other_user, notification_type='system',
            title='Other', body='', url='',
        )
        resp = auth_client.post(f'/api/v1/notifications/{other_notif.pk}/read/')
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_idempotent_when_already_read(self, auth_client, employee):
        notif = Notification.objects.create(
            user=employee, notification_type='system',
            title='Already Read', body='', url='', is_read=True,
        )
        resp = auth_client.post(f'/api/v1/notifications/{notif.pk}/read/')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['is_read'] is True


# ---------------------------------------------------------------------------
# Mark all as read
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestMarkAllRead:
    URL = '/api/v1/notifications/read-all/'

    def test_marks_all_read(self, auth_client, unread_notifications, employee):
        resp = auth_client.post(self.URL)
        assert resp.status_code == status.HTTP_200_OK
        assert Notification.objects.filter(user=employee, is_read=False).count() == 0

    def test_does_not_affect_other_users(self, auth_client, other_user):
        other_notif = Notification.objects.create(
            user=other_user, notification_type='system',
            title='Other', body='', url='', is_read=False,
        )
        auth_client.post(self.URL)
        other_notif.refresh_from_db()
        assert other_notif.is_read is False


# ---------------------------------------------------------------------------
# Delete notification
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestDeleteNotification:
    def test_deletes_own_notification(self, auth_client, notification):
        resp = auth_client.delete(f'/api/v1/notifications/{notification.pk}/')
        assert resp.status_code == status.HTTP_204_NO_CONTENT
        assert not Notification.objects.filter(pk=notification.pk).exists()

    def test_cannot_delete_other_users_notification(self, auth_client, other_user):
        other_notif = Notification.objects.create(
            user=other_user, notification_type='system',
            title='Other', body='', url='',
        )
        resp = auth_client.delete(f'/api/v1/notifications/{other_notif.pk}/')
        assert resp.status_code == status.HTTP_404_NOT_FOUND
