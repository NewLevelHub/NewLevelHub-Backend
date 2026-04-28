"""
Integration tests for notification preferences and Do-Not-Disturb.

Coverage:
  - GET  /api/v1/notifications/preferences/   — returns dict-by-type; auto-creates on first access
  - PATCH /api/v1/notifications/preferences/  — partial update; validates keys/values
  - POST /api/v1/notifications/do-not-disturb/ — enables/disables DND
  - create_notification suppression by DND and per-type in_app preference
  - Unauthenticated → 401 on preferences and DND endpoints
"""

import pytest
from datetime import timedelta
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.notifications.models import Notification, NotificationPreference
from apps.notifications.serializers import NOTIFICATION_TYPE_FIELD_MAP
from apps.notifications.utils import create_notification

PREFERENCES_URL = '/api/v1/notifications/preferences/'
DND_URL = '/api/v1/notifications/do-not-disturb/'

ALL_PREF_TYPES = list(NOTIFICATION_TYPE_FIELD_MAP.keys())


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

    def _make(name='Test Co'):
        return Company.objects.create(name=name)
    return _make


@pytest.fixture
def company(make_company):
    return make_company()


@pytest.fixture
def employee(make_user, company):
    return make_user('pref_employee@test.com', role='employee', company=company)


@pytest.fixture
def auth_client(api_client, employee):
    api_client.force_authenticate(user=employee)
    return api_client


# ---------------------------------------------------------------------------
# AC1: GET /api/v1/notifications/preferences/
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestGetPreferences:
    def test_returns_200(self, auth_client):
        resp = auth_client.get(PREFERENCES_URL)
        assert resp.status_code == status.HTTP_200_OK

    def test_all_14_types_present(self, auth_client):
        resp = auth_client.get(PREFERENCES_URL)
        data = resp.data
        for ntype in ALL_PREF_TYPES:
            assert ntype in data, f"Missing notification type '{ntype}' in response"

    def test_each_type_has_in_app_and_email(self, auth_client):
        resp = auth_client.get(PREFERENCES_URL)
        for ntype, prefs in resp.data.items():
            assert 'in_app' in prefs, f"'{ntype}' missing 'in_app'"
            assert 'email' in prefs, f"'{ntype}' missing 'email'"
            assert isinstance(prefs['in_app'], bool), f"'{ntype}.in_app' must be bool"
            assert isinstance(prefs['email'], bool), f"'{ntype}.email' must be bool"

    def test_auto_creates_preferences_on_first_access(self, auth_client, employee):
        assert not NotificationPreference.objects.filter(user=employee).exists()
        resp = auth_client.get(PREFERENCES_URL)
        assert resp.status_code == status.HTTP_200_OK
        assert NotificationPreference.objects.filter(user=employee).exists()

    def test_defaults_are_true_for_in_app(self, auth_client):
        resp = auth_client.get(PREFERENCES_URL)
        # By default, all in_app values should be True.
        for ntype, prefs in resp.data.items():
            assert prefs['in_app'] is True, f"'{ntype}.in_app' should default to True"

    def test_unauthenticated_returns_401(self, api_client):
        resp = api_client.get(PREFERENCES_URL)
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# ---------------------------------------------------------------------------
# AC2: PATCH /api/v1/notifications/preferences/
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestPatchPreferences:
    def test_partial_update_single_type(self, auth_client):
        payload = {'booking_reminder': {'email': False}}
        resp = auth_client.patch(PREFERENCES_URL, payload, format='json')
        assert resp.status_code == status.HTTP_200_OK
        # The booking group email should be updated.
        assert resp.data['booking_reminder']['email'] is False

    def test_partial_update_multiple_types(self, auth_client):
        payload = {
            'task_assigned': {'in_app': False, 'email': False},
            'system': {'email': False},
        }
        resp = auth_client.patch(PREFERENCES_URL, payload, format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['task_assigned']['in_app'] is False
        assert resp.data['task_assigned']['email'] is False
        assert resp.data['system']['email'] is False

    def test_update_persists_to_db(self, auth_client, employee):
        payload = {'announcement': {'in_app': False}}
        auth_client.patch(PREFERENCES_URL, payload, format='json')
        pref = NotificationPreference.objects.get(user=employee)
        assert pref.announcement_in_app is False

    def test_upserts_if_no_preference_exists(self, auth_client, employee):
        assert not NotificationPreference.objects.filter(user=employee).exists()
        payload = {'system': {'in_app': False}}
        resp = auth_client.patch(PREFERENCES_URL, payload, format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert NotificationPreference.objects.filter(user=employee).exists()

    def test_invalid_notification_type_returns_400(self, auth_client):
        payload = {'not_a_real_type': {'in_app': False}}
        resp = auth_client.patch(PREFERENCES_URL, payload, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_invalid_key_in_type_dict_returns_400(self, auth_client):
        payload = {'system': {'push': True}}
        resp = auth_client.patch(PREFERENCES_URL, payload, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_non_boolean_value_returns_400(self, auth_client):
        payload = {'system': {'in_app': 'yes'}}
        resp = auth_client.patch(PREFERENCES_URL, payload, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_response_has_all_14_types(self, auth_client):
        payload = {'system': {'email': False}}
        resp = auth_client.patch(PREFERENCES_URL, payload, format='json')
        for ntype in ALL_PREF_TYPES:
            assert ntype in resp.data

    def test_unauthenticated_returns_401(self, api_client):
        resp = api_client.patch(PREFERENCES_URL, {}, format='json')
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_only_changed_type_is_affected(self, auth_client):
        # First, get defaults.
        resp = auth_client.get(PREFERENCES_URL)
        original = {k: dict(v) for k, v in resp.data.items()}

        # Patch only system.
        auth_client.patch(PREFERENCES_URL, {'system': {'in_app': False}}, format='json')

        resp2 = auth_client.get(PREFERENCES_URL)
        for ntype, prefs in resp2.data.items():
            if ntype == 'system':
                assert prefs['in_app'] is False
            else:
                assert prefs['in_app'] == original[ntype]['in_app']


# ---------------------------------------------------------------------------
# AC3: POST /api/v1/notifications/do-not-disturb/
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestDoNotDisturb:
    def test_enable_dnd_without_until(self, auth_client, employee):
        payload = {'enabled': True}
        resp = auth_client.post(DND_URL, payload, format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['dnd_enabled'] is True
        assert resp.data['dnd_until'] is None

        pref = NotificationPreference.objects.get(user=employee)
        assert pref.dnd_enabled is True
        assert pref.dnd_until is None

    def test_enable_dnd_with_until(self, auth_client, employee):
        until = '2099-01-01T00:00:00Z'
        payload = {'enabled': True, 'until': until}
        resp = auth_client.post(DND_URL, payload, format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['dnd_enabled'] is True
        assert resp.data['dnd_until'] is not None

        pref = NotificationPreference.objects.get(user=employee)
        assert pref.dnd_enabled is True
        assert pref.dnd_until is not None

    def test_disable_dnd(self, auth_client, employee):
        # First enable.
        auth_client.post(DND_URL, {'enabled': True}, format='json')
        # Then disable.
        resp = auth_client.post(DND_URL, {'enabled': False}, format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['dnd_enabled'] is False

    def test_auto_creates_preference_if_none(self, auth_client, employee):
        assert not NotificationPreference.objects.filter(user=employee).exists()
        resp = auth_client.post(DND_URL, {'enabled': True}, format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert NotificationPreference.objects.filter(user=employee).exists()

    def test_unauthenticated_returns_401(self, api_client):
        resp = api_client.post(DND_URL, {'enabled': True}, format='json')
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_missing_enabled_returns_400(self, auth_client):
        resp = auth_client.post(DND_URL, {}, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_invalid_until_returns_400(self, auth_client):
        resp = auth_client.post(DND_URL, {'enabled': True, 'until': 'not-a-date'}, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_response_shape(self, auth_client):
        resp = auth_client.post(DND_URL, {'enabled': True}, format='json')
        assert 'dnd_enabled' in resp.data
        assert 'dnd_until' in resp.data


# ---------------------------------------------------------------------------
# AC4: create_notification suppression
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCreateNotificationSuppression:
    def test_dnd_active_indefinite_suppresses_notification(self, employee):
        NotificationPreference.objects.create(user=employee, dnd_enabled=True, dnd_until=None)
        result = create_notification(
            user=employee,
            notification_type='system',
            title='Test',
            message='Body',
        )
        assert result is None
        assert Notification.objects.filter(user=employee).count() == 0

    def test_dnd_active_with_future_until_suppresses_notification(self, employee):
        future = timezone.now() + timedelta(hours=1)
        NotificationPreference.objects.create(user=employee, dnd_enabled=True, dnd_until=future)
        result = create_notification(
            user=employee,
            notification_type='booking_confirmed',
            title='Test',
            message='Body',
        )
        assert result is None

    def test_dnd_active_with_past_until_allows_notification(self, employee):
        past = timezone.now() - timedelta(hours=1)
        NotificationPreference.objects.create(user=employee, dnd_enabled=True, dnd_until=past)
        result = create_notification(
            user=employee,
            notification_type='booking_confirmed',
            title='Test',
            message='Body',
        )
        assert result is not None
        assert result.pk is not None

    def test_dnd_disabled_allows_notification(self, employee):
        NotificationPreference.objects.create(user=employee, dnd_enabled=False)
        result = create_notification(
            user=employee,
            notification_type='system',
            title='Test',
            message='Body',
        )
        assert result is not None

    def test_in_app_disabled_suppresses_notification(self, employee):
        # Disable in_app for system.
        NotificationPreference.objects.create(user=employee, system_in_app=False)
        result = create_notification(
            user=employee,
            notification_type='system',
            title='Test',
            message='Body',
        )
        assert result is None
        assert Notification.objects.filter(user=employee).count() == 0

    def test_in_app_enabled_creates_notification(self, employee):
        NotificationPreference.objects.create(user=employee, system_in_app=True)
        result = create_notification(
            user=employee,
            notification_type='system',
            title='Test',
            message='Body',
        )
        assert result is not None
        assert Notification.objects.filter(user=employee, pk=result.pk).exists()

    def test_no_preference_record_creates_notification(self, employee):
        """When no preference exists, create_notification should auto-create pref with defaults and allow."""
        assert not NotificationPreference.objects.filter(user=employee).exists()
        result = create_notification(
            user=employee,
            notification_type='announcement',
            title='Hello',
            message='World',
        )
        assert result is not None
        # Pref record should have been created.
        assert NotificationPreference.objects.filter(user=employee).exists()

    def test_booking_in_app_disabled_suppresses_all_booking_types(self, employee):
        """booking_* types share the booking_in_app field."""
        NotificationPreference.objects.create(user=employee, booking_in_app=False)
        for ntype in ('booking_confirmed', 'booking_reminder', 'booking_cancelled'):
            Notification.objects.filter(user=employee).delete()
            result = create_notification(user=employee, notification_type=ntype, title='T', message='M')
            assert result is None, f"'{ntype}' should be suppressed when booking_in_app=False"
