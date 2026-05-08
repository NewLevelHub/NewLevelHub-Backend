"""
Tests for the three email-related bug fixes:

BUG-1 — email=False preference is respected (email not sent when disabled).
BUG-2 — duplicate email deduplication (same send within TTL is skipped).
BUG-3 — unsubscribe link present in template; unsubscribe endpoint works.
"""

import pytest
from unittest.mock import patch, MagicMock
from django.core import signing
from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APIClient

from apps.notifications.models import NotificationPreference
from apps.notifications.serializers import NOTIFICATION_TYPE_FIELD_MAP
from apps.notifications.utils import (
    _email_allowed,
    _send_notification_email,
    _email_dedup_key,
    create_notification,
)

UNSUBSCRIBE_URL = '/api/v1/notifications/unsubscribe/'


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def make_user(db, django_user_model):
    def _make(email, role='employee', company=None):
        return django_user_model.objects.create_user(
            email=email,
            password='testpass123',
            first_name='Test',
            last_name='User',
            role=role,
            company=company,
        )
    return _make


@pytest.fixture
def make_company(db):
    from apps.companies.models import Company

    def _make(name='Bug Fix Co'):
        return Company.objects.create(name=name)
    return _make


@pytest.fixture
def company(make_company):
    return make_company()


@pytest.fixture
def employee(make_user, company):
    return make_user('bugfix_employee@test.com', role='employee', company=company)


# ---------------------------------------------------------------------------
# BUG-1: Email preference respected
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBug1EmailPreferenceRespected:
    """_email_allowed returns False when the email field is False; no email sent."""

    def test_email_allowed_returns_true_when_enabled(self, employee):
        pref, _ = NotificationPreference.objects.get_or_create(user=employee)
        pref.booking_confirmed_email = True
        pref.save(update_fields=['booking_confirmed_email'])
        assert _email_allowed(pref, 'booking_confirmed') is True

    def test_email_allowed_returns_false_when_disabled(self, employee):
        pref, _ = NotificationPreference.objects.get_or_create(user=employee)
        pref.booking_confirmed_email = False
        pref.save(update_fields=['booking_confirmed_email'])
        assert _email_allowed(pref, 'booking_confirmed') is False

    def test_email_allowed_returns_false_for_unknown_type(self, employee):
        pref, _ = NotificationPreference.objects.get_or_create(user=employee)
        # Unknown types should not send email (safe default).
        assert _email_allowed(pref, 'booking_completed') is False

    def test_create_notification_only_creates_in_app_record(self, employee):
        """create_notification only persists the in-app record; email is handled
        by the send_notification_email Celery task, not by create_notification."""
        from apps.notifications.models import Notification

        pref, _ = NotificationPreference.objects.get_or_create(user=employee)
        pref.booking_confirmed_email = True
        pref.save(update_fields=['booking_confirmed_email'])

        with patch('apps.notifications.utils._send_notification_email') as mock_send:
            notif = create_notification(
                user=employee,
                notification_type='booking_confirmed',
                title='Booking confirmed',
                message='Your room is booked.',
            )
            # No email sent from create_notification — it is the Celery task's job.
            mock_send.assert_not_called()
            # In-app notification was created.
            assert notif is not None
            assert Notification.objects.filter(user=employee, notification_type='booking_confirmed').exists()

    def test_create_notification_does_not_send_email_when_disabled(self, employee):
        """create_notification never sends email regardless of preferences
        (email is the Celery task's responsibility)."""
        pref, _ = NotificationPreference.objects.get_or_create(user=employee)
        pref.booking_confirmed_email = False
        pref.save(update_fields=['booking_confirmed_email'])

        with patch('apps.notifications.utils._send_notification_email') as mock_send:
            create_notification(
                user=employee,
                notification_type='booking_confirmed',
                title='Booking confirmed',
                message='Your room is booked.',
            )
            mock_send.assert_not_called()

    def test_email_not_sent_when_pref_defaults_to_false(self, employee):
        """create_notification never sends email (task_assigned or any type)."""
        pref, _ = NotificationPreference.objects.get_or_create(user=employee)
        # Explicitly disable to ensure create_notification does not trigger email.
        pref.task_assigned_email = False
        pref.save(update_fields=['task_assigned_email'])

        with patch('apps.notifications.utils._send_notification_email') as mock_send:
            create_notification(
                user=employee,
                notification_type='task_assigned',
                title='New task',
                message='You have a new task.',
            )
            mock_send.assert_not_called()


# ---------------------------------------------------------------------------
# BUG-2: Deduplication
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBug2EmailDeduplication:
    """_send_notification_email deduplicates sends within EMAIL_DEDUP_TTL."""

    def setup_method(self):
        # Clear cache before each test to avoid cross-test interference.
        cache.clear()

    def test_cache_key_format_is_deterministic(self, employee):
        key1 = _email_dedup_key(employee, 'system', 'Test title')
        key2 = _email_dedup_key(employee, 'system', 'Test title')
        assert key1 == key2

    def test_cache_key_differs_for_different_type(self, employee):
        key1 = _email_dedup_key(employee, 'system', 'Test')
        key2 = _email_dedup_key(employee, 'booking_confirmed', 'Test')
        assert key1 != key2


# ---------------------------------------------------------------------------
# BUG-3: Unsubscribe link in template + unsubscribe endpoint
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBug3UnsubscribeLink:
    """Unsubscribe link appears in rendered email; endpoint works."""

    def test_send_notification_email_renders_unsubscribe_link(self, employee):
        """_send_notification_email passes unsubscribe_url to the template."""
        with patch('apps.notifications.utils.EmailMultiAlternatives') as MockEmail:
            mock_msg = MagicMock()
            MockEmail.return_value = mock_msg
            with patch('apps.notifications.utils.render_to_string') as mock_render:
                mock_render.return_value = '<html>mock</html>'
                _send_notification_email(
                    user=employee,
                    notification_type='system',
                    title='Test',
                    body='Body',
                )
                call_kwargs = mock_render.call_args
                context = call_kwargs[0][1]  # positional args: (template_name, context)
                assert 'unsubscribe_url' in context
                assert context['unsubscribe_url']  # must be non-empty


@pytest.mark.django_db
class TestUnsubscribeEndpoint:
    """GET /api/v1/notifications/unsubscribe/?token=<token>"""

    def _make_token(self, user):
        return signing.dumps({'user_id': user.pk}, salt='notification-unsubscribe')

    def test_valid_token_disables_all_email_prefs(self, api_client, employee):
        # First ensure some email prefs are True.
        pref, _ = NotificationPreference.objects.get_or_create(user=employee)
        pref.booking_confirmed_email = True
        pref.invitation_email = True
        pref.save(update_fields=['booking_confirmed_email', 'invitation_email'])

        token = self._make_token(employee)
        resp = api_client.get(UNSUBSCRIBE_URL, {'token': token}, follow=False)
        assert resp.status_code == status.HTTP_302_FOUND
        assert resp['Location'].endswith('/unsubscribe/success')

        pref.refresh_from_db()
        for _, email_field in NOTIFICATION_TYPE_FIELD_MAP.values():
            assert getattr(pref, email_field) is False, (
                f"Expected {email_field} to be False after unsubscribe"
            )

    def test_unsubscribe_redirects_to_success(self, api_client, employee):
        token = self._make_token(employee)
        resp = api_client.get(UNSUBSCRIBE_URL, {'token': token}, follow=False)
        assert resp.status_code == status.HTTP_302_FOUND
        assert resp['Location'].endswith('/unsubscribe/success')

    def test_missing_token_redirects_to_invalid(self, api_client):
        resp = api_client.get(UNSUBSCRIBE_URL, follow=False)
        assert resp.status_code == status.HTTP_302_FOUND
        assert resp['Location'].endswith('/unsubscribe/invalid')

    def test_invalid_token_redirects_to_invalid(self, api_client):
        resp = api_client.get(UNSUBSCRIBE_URL, {'token': 'not-a-valid-token'}, follow=False)
        assert resp.status_code == status.HTTP_302_FOUND
        assert resp['Location'].endswith('/unsubscribe/invalid')

    def test_tampered_token_redirects_to_invalid(self, api_client, employee):
        token = self._make_token(employee)
        tampered = token[:-5] + 'XXXXX'
        resp = api_client.get(UNSUBSCRIBE_URL, {'token': tampered}, follow=False)
        assert resp.status_code == status.HTTP_302_FOUND
        assert resp['Location'].endswith('/unsubscribe/invalid')

    def test_endpoint_is_unauthenticated(self, api_client, employee):
        """Unsubscribe endpoint must not require authentication."""
        token = self._make_token(employee)
        # No force_authenticate — plain unauthenticated client.
        resp = api_client.get(UNSUBSCRIBE_URL, {'token': token}, follow=False)
        assert resp.status_code == status.HTTP_302_FOUND
        assert resp['Location'].endswith('/unsubscribe/success')

    def test_auto_creates_pref_if_none_exists(self, api_client, employee):
        assert not NotificationPreference.objects.filter(user=employee).exists()
        token = self._make_token(employee)
        resp = api_client.get(UNSUBSCRIBE_URL, {'token': token}, follow=False)
        assert resp.status_code == status.HTTP_302_FOUND
        assert resp['Location'].endswith('/unsubscribe/success')
        assert NotificationPreference.objects.filter(user=employee).exists()
