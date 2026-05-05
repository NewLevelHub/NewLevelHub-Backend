"""
Integration tests for notification preferences and Do-Not-Disturb.

Coverage:
  - GET  /api/v1/notifications/preferences/   — returns dict-by-type; auto-creates on first access
  - PATCH /api/v1/notifications/preferences/  — partial update; validates keys/values
  - POST /api/v1/notifications/do-not-disturb/ — enables/disables DND
  - create_notification suppression by DND and per-type in_app preference
  - Unauthenticated → 401 on preferences and DND endpoints
  - Role-based filtering: each role sees only its allowed types
"""

import pytest
from datetime import timedelta
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.notifications.models import Notification, NotificationPreference
from apps.notifications.serializers import get_allowed_types_for_role
from apps.notifications.utils import create_notification

PREFERENCES_URL = '/api/v1/notifications/preferences/'
DND_URL = '/api/v1/notifications/do-not-disturb/'

# Types visible to an employee (subset of NOTIFICATION_TYPE_FIELD_MAP).
EMPLOYEE_PREF_TYPES = get_allowed_types_for_role('employee')
# Types visible to a company_admin.
COMPANY_ADMIN_PREF_TYPES = get_allowed_types_for_role('company_admin')


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
def company_admin(make_user, company):
    return make_user('pref_admin@test.com', role='company_admin', company=company)


@pytest.fixture
def guest_user(make_user, company):
    return make_user('pref_guest@test.com', role='guest', company=company)


@pytest.fixture
def auth_client(api_client, employee):
    api_client.force_authenticate(user=employee)
    return api_client


@pytest.fixture
def admin_client(api_client, company_admin):
    api_client.force_authenticate(user=company_admin)
    return api_client


@pytest.fixture
def guest_client(api_client, guest_user):
    api_client.force_authenticate(user=guest_user)
    return api_client


# ---------------------------------------------------------------------------
# AC1: GET /api/v1/notifications/preferences/
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestGetPreferences:
    def test_returns_200(self, auth_client):
        resp = auth_client.get(PREFERENCES_URL)
        assert resp.status_code == status.HTTP_200_OK

    def test_employee_sees_only_allowed_types(self, auth_client):
        resp = auth_client.get(PREFERENCES_URL)
        data = resp.data
        skip_keys = {'dnd_enabled', 'dnd_until'}
        returned_types = {k for k in data if k not in skip_keys}
        assert returned_types == EMPLOYEE_PREF_TYPES

    def test_each_type_has_in_app_and_email(self, auth_client):
        resp = auth_client.get(PREFERENCES_URL)
        skip_keys = {'dnd_enabled', 'dnd_until'}
        for ntype, prefs in resp.data.items():
            if ntype in skip_keys:
                continue
            assert 'in_app' in prefs, f"'{ntype}' missing 'in_app'"
            assert 'email' in prefs, f"'{ntype}' missing 'email'"
            assert isinstance(prefs['in_app'], bool), f"'{ntype}.in_app' must be bool"
            assert isinstance(prefs['email'], bool), f"'{ntype}.email' must be bool"

    def test_dnd_fields_present_in_response(self, auth_client):
        resp = auth_client.get(PREFERENCES_URL)
        assert 'dnd_enabled' in resp.data
        assert 'dnd_until' in resp.data
        assert isinstance(resp.data['dnd_enabled'], bool)

    def test_dnd_enabled_defaults_to_false(self, auth_client):
        resp = auth_client.get(PREFERENCES_URL)
        assert resp.data['dnd_enabled'] is False
        assert resp.data['dnd_until'] is None

    def test_dnd_fields_reflect_active_dnd(self, auth_client, employee):
        until = '2099-06-01T12:00:00Z'
        auth_client.post(DND_URL, {'enabled': True, 'until': until}, format='json')
        resp = auth_client.get(PREFERENCES_URL)
        assert resp.data['dnd_enabled'] is True
        assert resp.data['dnd_until'] is not None

    def test_auto_creates_preferences_on_first_access(self, auth_client, employee):
        assert not NotificationPreference.objects.filter(user=employee).exists()
        resp = auth_client.get(PREFERENCES_URL)
        assert resp.status_code == status.HTTP_200_OK
        assert NotificationPreference.objects.filter(user=employee).exists()

    def test_defaults_are_true_for_in_app(self, auth_client):
        resp = auth_client.get(PREFERENCES_URL)
        skip_keys = {'dnd_enabled', 'dnd_until'}
        for ntype, prefs in resp.data.items():
            if ntype in skip_keys:
                continue
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

    def test_response_has_employee_types(self, auth_client):
        payload = {'system': {'email': False}}
        resp = auth_client.patch(PREFERENCES_URL, payload, format='json')
        skip_keys = {'dnd_enabled', 'dnd_until'}
        returned_types = {k for k in resp.data if k not in skip_keys}
        assert returned_types == EMPLOYEE_PREF_TYPES

    def test_unauthenticated_returns_401(self, api_client):
        resp = api_client.patch(PREFERENCES_URL, {}, format='json')
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_patch_with_dnd_enabled_returns_400(self, auth_client):
        resp = auth_client.patch(PREFERENCES_URL, {'dnd_enabled': True}, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_patch_with_dnd_until_returns_400(self, auth_client):
        resp = auth_client.patch(PREFERENCES_URL, {'dnd_until': '2099-01-01T00:00:00Z'}, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_patch_does_not_change_dnd_fields(self, auth_client, employee):
        auth_client.post(DND_URL, {'enabled': True}, format='json')
        auth_client.patch(PREFERENCES_URL, {'system': {'email': False}}, format='json')
        resp = auth_client.get(PREFERENCES_URL)
        assert resp.data['dnd_enabled'] is True

    def test_patch_response_includes_dnd_fields(self, auth_client):
        resp = auth_client.patch(PREFERENCES_URL, {'system': {'email': False}}, format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert 'dnd_enabled' in resp.data
        assert 'dnd_until' in resp.data

    def test_only_changed_type_is_affected(self, auth_client):
        skip_keys = {'dnd_enabled', 'dnd_until'}

        resp = auth_client.get(PREFERENCES_URL)
        original = {k: dict(v) for k, v in resp.data.items() if k not in skip_keys}

        auth_client.patch(PREFERENCES_URL, {'system': {'in_app': False}}, format='json')

        resp2 = auth_client.get(PREFERENCES_URL)
        for ntype, prefs in resp2.data.items():
            if ntype in skip_keys:
                continue
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
        auth_client.post(DND_URL, {'enabled': True}, format='json')
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

    def test_past_dnd_until_returns_400(self, auth_client):
        past = (timezone.now() - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        resp = auth_client.post(DND_URL, {'enabled': True, 'until': past}, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_past_dnd_until_error_field_name(self, auth_client):
        past = (timezone.now() - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        resp = auth_client.post(DND_URL, {'enabled': True, 'until': past}, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert 'dnd_until' in str(resp.data)

    def test_future_dnd_until_is_accepted(self, auth_client):
        future = (timezone.now() + timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%SZ')
        resp = auth_client.post(DND_URL, {'enabled': True, 'until': future}, format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['dnd_enabled'] is True
        assert resp.data['dnd_until'] is not None

    def test_disable_dnd_with_past_until_is_accepted(self, auth_client, employee):
        past = (timezone.now() - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        resp = auth_client.post(DND_URL, {'enabled': False, 'until': past}, format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['dnd_enabled'] is False
        pref = NotificationPreference.objects.get(user=employee)
        assert pref.dnd_until is None

    def test_disable_dnd_with_future_until_clears_until(self, auth_client, employee):
        future = (timezone.now() + timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%SZ')
        auth_client.post(DND_URL, {'enabled': True, 'until': future}, format='json')
        resp = auth_client.post(DND_URL, {'enabled': False, 'until': future}, format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['dnd_enabled'] is False
        pref = NotificationPreference.objects.get(user=employee)
        assert pref.dnd_until is None


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
        assert NotificationPreference.objects.filter(user=employee).exists()

    def test_per_type_in_app_disabled_suppresses_only_that_type(self, employee):
        """Each booking type now has its own independent toggle."""
        NotificationPreference.objects.create(
            user=employee,
            booking_confirmed_in_app=False,
            booking_reminder_in_app=False,
            booking_cancelled_in_app=False,
        )
        for ntype in ('booking_confirmed', 'booking_reminder', 'booking_cancelled'):
            Notification.objects.filter(user=employee).delete()
            result = create_notification(user=employee, notification_type=ntype, title='T', message='M')
            assert result is None, f"'{ntype}' should be suppressed when its own in_app field is False"

    def test_disabling_one_booking_type_does_not_affect_others(self, employee):
        """Toggling booking_reminder does not affect booking_confirmed or booking_cancelled."""
        NotificationPreference.objects.create(
            user=employee,
            booking_reminder_in_app=False,
            booking_confirmed_in_app=True,
            booking_cancelled_in_app=True,
        )
        result_reminder = create_notification(
            user=employee, notification_type='booking_reminder', title='T', message='M',
        )
        assert result_reminder is None, 'booking_reminder should be suppressed'

        Notification.objects.filter(user=employee).delete()
        result_confirmed = create_notification(
            user=employee, notification_type='booking_confirmed', title='T', message='M',
        )
        assert result_confirmed is not None, 'booking_confirmed should not be affected by booking_reminder toggle'


# ---------------------------------------------------------------------------
# AC5: Role-based filtering on GET /preferences/
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestRoleBasedFiltering:
    def test_employee_cannot_see_company_admin_types(self, auth_client):
        # These types are for company_admin only, not employee.
        admin_only = {'guest_validated', 'leave_review', 'invitation'}
        resp = auth_client.get(PREFERENCES_URL)
        assert resp.status_code == status.HTTP_200_OK
        for ntype in admin_only:
            assert ntype not in resp.data, f"Employee should not see '{ntype}'"

    def test_company_admin_can_see_all_relevant_types(self, admin_client):
        resp = admin_client.get(PREFERENCES_URL)
        assert resp.status_code == status.HTTP_200_OK
        for ntype in ('leave_review', 'guest_validated', 'invitation'):
            assert ntype in resp.data, f"company_admin should see '{ntype}'"

    def test_guest_sees_only_guest_types(self, guest_client):
        resp = guest_client.get(PREFERENCES_URL)
        assert resp.status_code == status.HTTP_200_OK
        skip_keys = {'dnd_enabled', 'dnd_until'}
        returned_types = {k for k in resp.data if k not in skip_keys}
        expected = get_allowed_types_for_role('guest')
        assert returned_types == expected

    def test_guest_does_not_see_employee_only_types(self, guest_client):
        employee_only = {'booking_confirmed', 'task_assigned', 'leave_approved'}
        resp = guest_client.get(PREFERENCES_URL)
        for ntype in employee_only:
            assert ntype not in resp.data, f"Guest should not see '{ntype}'"

    def test_company_admin_sees_correct_type_count(self, admin_client):
        resp = admin_client.get(PREFERENCES_URL)
        skip_keys = {'dnd_enabled', 'dnd_until'}
        returned_types = {k for k in resp.data if k not in skip_keys}
        assert returned_types == COMPANY_ADMIN_PREF_TYPES


# ---------------------------------------------------------------------------
# AC6: Role-based validation on PATCH /preferences/
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestRoleBasedPatchValidation:
    def test_employee_patch_restricted_type_is_ignored(self, auth_client):
        # guest_validated is not in the employee allowed list — it should be silently skipped.
        payload = {'guest_validated': {'in_app': True}}
        resp = auth_client.patch(PREFERENCES_URL, payload, format='json')
        assert resp.status_code == status.HTTP_200_OK
        # The response is filtered by role, so guest_validated must not appear.
        assert 'guest_validated' not in resp.data

    def test_employee_cannot_patch_leave_review(self, auth_client):
        payload = {'leave_review': {'in_app': True}}
        resp = auth_client.patch(PREFERENCES_URL, payload, format='json')
        assert resp.status_code == status.HTTP_200_OK

    def test_employee_cannot_patch_invitation(self, auth_client):
        payload = {'invitation': {'in_app': False}}
        resp = auth_client.patch(PREFERENCES_URL, payload, format='json')
        assert resp.status_code == status.HTTP_200_OK

    def test_company_admin_can_patch_allowed_type(self, admin_client):
        payload = {'leave_review': {'in_app': True}}
        resp = admin_client.patch(PREFERENCES_URL, payload, format='json')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['leave_review']['in_app'] is True

    def test_company_admin_can_patch_guest_validated(self, admin_client):
        payload = {'guest_validated': {'email': False}}
        resp = admin_client.patch(PREFERENCES_URL, payload, format='json')
        assert resp.status_code == status.HTTP_200_OK

    def test_guest_cannot_patch_booking_type(self, guest_client):
        # booking_confirmed is not in the guest allowed list — it should be silently skipped.
        payload = {'booking_confirmed': {'in_app': True}}
        resp = guest_client.patch(PREFERENCES_URL, payload, format='json')
        assert resp.status_code == status.HTTP_200_OK

    def test_guest_can_patch_allowed_type(self, guest_client):
        payload = {'system': {'email': False}}
        resp = guest_client.patch(PREFERENCES_URL, payload, format='json')
        assert resp.status_code == status.HTTP_200_OK
