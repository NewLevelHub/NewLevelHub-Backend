"""
Acceptance tests for priority booking (Standard / Premium plan override).

Priority tiers:
    3  premium plan or superadmin
    2  standard plan
    1  basic / free / no-company / guest

Override rule:
    A requester with higher priority can displace an existing confirmed booking when
    that booking's start_time > now + PRIORITY_OVERRIDE_HOURS (default 2 h).
    If the window has passed, or priorities are equal / lower → 409.
"""
from datetime import timedelta
from unittest.mock import patch, MagicMock

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.bookings.models import Booking, Resource
from apps.bookings.serializers import _booking_priority
from apps.companies.models import Company
from apps.users.models import User

RESERVATIONS_URL = '/api/v1/bookings/reservations/'

_NOTIFY_PATH = 'apps.bookings.serializers.create_notification'
_EMAIL_TASK_PATH = 'apps.notifications.tasks.send_notification_email'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _future(hours=4):
    """Return a timezone-aware datetime offset from now."""
    return timezone.now() + timedelta(hours=hours)


def _dt(hours_from_now):
    return timezone.now() + timedelta(hours=hours_from_now)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def basic_company(db):
    return Company.objects.create(name='Basic Co', plan='basic')


@pytest.fixture
def standard_company(db):
    return Company.objects.create(name='Standard Co', plan='standard')


@pytest.fixture
def premium_company(db):
    return Company.objects.create(name='Premium Co', plan='premium')


@pytest.fixture
def basic_user(db, basic_company):
    return User.objects.create_user(
        email='basic@priority.test',
        password='pass',
        first_name='Basic',
        last_name='User',
        role='company_admin',
        company=basic_company,
        is_email_verified=True,
    )


@pytest.fixture
def standard_user(db, standard_company):
    return User.objects.create_user(
        email='standard@priority.test',
        password='pass',
        first_name='Standard',
        last_name='User',
        role='company_admin',
        company=standard_company,
        is_email_verified=True,
    )


@pytest.fixture
def premium_user(db, premium_company):
    return User.objects.create_user(
        email='premium@priority.test',
        password='pass',
        first_name='Premium',
        last_name='User',
        role='company_admin',
        company=premium_company,
        is_email_verified=True,
    )


@pytest.fixture
def superadmin_user(db):
    return User.objects.create_user(
        email='superadmin@priority.test',
        password='pass',
        first_name='Super',
        last_name='Admin',
        role='superadmin',
        is_email_verified=True,
    )


@pytest.fixture
def shared_resource(db):
    return Resource.objects.create(
        name='Shared Desk',
        resource_type='desk',
        floor=1,
        capacity=1,
        is_active=True,
        advance_booking_days=30,
        min_duration_minutes=30,
        max_duration_minutes=480,
        available_from='07:00',
        available_until='23:00',
        available_days=[0, 1, 2, 3, 4, 5, 6],
    )


def _existing_booking(user, resource, start_offset_hours=4, duration_hours=2, priority=1):
    """Create a confirmed booking owned by user at the given offset."""
    start = _dt(start_offset_hours)
    end = start + timedelta(hours=duration_hours)
    return Booking.objects.create(
        resource=resource,
        user=user,
        company=getattr(user, 'company', None),
        start_time=start,
        end_time=end,
        status='confirmed',
        priority=priority,
    )


# ---------------------------------------------------------------------------
# AC1: standard vытесняет basic (слот в будущем > 2h) → 201, existing cancelled
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ac1_standard_displaces_basic_future_slot(api_client, standard_user, basic_user, shared_resource):
    existing = _existing_booking(basic_user, shared_resource, start_offset_hours=4, priority=1)
    start = existing.start_time
    end = existing.end_time

    api_client.force_authenticate(standard_user)
    payload = {
        'resource_id': shared_resource.id,
        'start_time': start.isoformat(),
        'end_time': end.isoformat(),
    }
    with patch(_NOTIFY_PATH), patch(_EMAIL_TASK_PATH) as mock_email:
        mock_email.delay = MagicMock()
        resp = api_client.post(RESERVATIONS_URL, payload, format='json')

    assert resp.status_code == status.HTTP_201_CREATED, resp.data

    existing.refresh_from_db()
    assert existing.status == 'cancelled'
    assert existing.cancel_reason == 'displaced_by_priority_booking'
    assert existing.cancelled_by_id == standard_user.id


# ---------------------------------------------------------------------------
# AC2: basic cannot displace standard → 409
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ac2_basic_cannot_displace_standard(api_client, basic_user, standard_user, shared_resource):
    existing = _existing_booking(standard_user, shared_resource, start_offset_hours=4, priority=2)
    start = existing.start_time
    end = existing.end_time

    api_client.force_authenticate(basic_user)
    payload = {
        'resource_id': shared_resource.id,
        'start_time': start.isoformat(),
        'end_time': end.isoformat(),
    }
    with patch(_NOTIFY_PATH), patch(_EMAIL_TASK_PATH) as mock_email:
        mock_email.delay = MagicMock()
        resp = api_client.post(RESERVATIONS_URL, payload, format='json')

    assert resp.status_code == status.HTTP_409_CONFLICT, resp.data

    existing.refresh_from_db()
    assert existing.status == 'confirmed'


# ---------------------------------------------------------------------------
# AC3: equal priority (standard vs standard) → 409
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ac3_equal_priority_no_displace(api_client, standard_user, standard_company, shared_resource):
    other_standard_user = User.objects.create_user(
        email='standard2@priority.test',
        password='pass',
        first_name='Other',
        last_name='Standard',
        role='company_admin',
        company=standard_company,
        is_email_verified=True,
    )
    existing = _existing_booking(other_standard_user, shared_resource, start_offset_hours=4, priority=2)
    start = existing.start_time
    end = existing.end_time

    api_client.force_authenticate(standard_user)
    payload = {
        'resource_id': shared_resource.id,
        'start_time': start.isoformat(),
        'end_time': end.isoformat(),
    }
    with patch(_NOTIFY_PATH), patch(_EMAIL_TASK_PATH) as mock_email:
        mock_email.delay = MagicMock()
        resp = api_client.post(RESERVATIONS_URL, payload, format='json')

    assert resp.status_code == status.HTTP_409_CONFLICT, resp.data


# ---------------------------------------------------------------------------
# AC4: slot starts in < 2h → 409 even for premium
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ac4_slot_too_soon_no_displace(api_client, premium_user, basic_user, shared_resource):
    # Existing booking starts only 1 hour from now — inside override window
    existing = _existing_booking(basic_user, shared_resource, start_offset_hours=1, priority=1)
    start = existing.start_time
    end = existing.end_time

    api_client.force_authenticate(premium_user)
    payload = {
        'resource_id': shared_resource.id,
        'start_time': start.isoformat(),
        'end_time': end.isoformat(),
    }
    with patch(_NOTIFY_PATH), patch(_EMAIL_TASK_PATH) as mock_email:
        mock_email.delay = MagicMock()
        resp = api_client.post(RESERVATIONS_URL, payload, format='json')

    assert resp.status_code == status.HTTP_409_CONFLICT, resp.data

    existing.refresh_from_db()
    assert existing.status == 'confirmed'


# ---------------------------------------------------------------------------
# AC5: premium displaces basic → displaced booking status == 'cancelled'
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ac5_premium_displaces_basic_cancelled_status(api_client, premium_user, basic_user, shared_resource):
    existing = _existing_booking(basic_user, shared_resource, start_offset_hours=5, priority=1)
    start = existing.start_time
    end = existing.end_time

    api_client.force_authenticate(premium_user)
    payload = {
        'resource_id': shared_resource.id,
        'start_time': start.isoformat(),
        'end_time': end.isoformat(),
    }
    with patch(_NOTIFY_PATH), patch(_EMAIL_TASK_PATH) as mock_email:
        mock_email.delay = MagicMock()
        resp = api_client.post(RESERVATIONS_URL, payload, format='json')

    assert resp.status_code == status.HTTP_201_CREATED, resp.data

    existing.refresh_from_db()
    assert existing.status == 'cancelled'
    assert existing.cancel_reason == 'displaced_by_priority_booking'


# ---------------------------------------------------------------------------
# AC6: premium displaces basic → notification sent to displaced owner
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ac6_premium_displaces_basic_notification_sent(api_client, premium_user, basic_user, shared_resource):
    existing = _existing_booking(basic_user, shared_resource, start_offset_hours=5, priority=1)
    start = existing.start_time
    end = existing.end_time

    api_client.force_authenticate(premium_user)
    payload = {
        'resource_id': shared_resource.id,
        'start_time': start.isoformat(),
        'end_time': end.isoformat(),
    }
    with patch(_NOTIFY_PATH) as mock_notify, patch(_EMAIL_TASK_PATH) as mock_email:
        mock_email.delay = MagicMock()
        resp = api_client.post(RESERVATIONS_URL, payload, format='json')

    assert resp.status_code == status.HTTP_201_CREATED, resp.data

    # create_notification should have been called at least once for the displaced user
    notify_calls = [call for call in mock_notify.call_args_list if call[1].get('user') == basic_user]
    assert len(notify_calls) >= 1
    displaced_call = notify_calls[0]
    assert displaced_call[1]['notification_type'] == 'booking_cancelled'


# ---------------------------------------------------------------------------
# AC7: new booking has correct priority field
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ac7_new_booking_has_correct_priority_standard(api_client, standard_user, shared_resource):
    start = _dt(4)
    end = start + timedelta(hours=2)

    api_client.force_authenticate(standard_user)
    payload = {
        'resource_id': shared_resource.id,
        'start_time': start.isoformat(),
        'end_time': end.isoformat(),
    }
    with patch(_NOTIFY_PATH), patch(_EMAIL_TASK_PATH) as mock_email:
        mock_email.delay = MagicMock()
        resp = api_client.post(RESERVATIONS_URL, payload, format='json')

    assert resp.status_code == status.HTTP_201_CREATED, resp.data
    booking = Booking.objects.get(pk=resp.data['id'])
    assert booking.priority == 2


@pytest.mark.django_db
def test_ac7_new_booking_has_correct_priority_premium(api_client, premium_user, shared_resource):
    start = _dt(4)
    end = start + timedelta(hours=2)

    api_client.force_authenticate(premium_user)
    payload = {
        'resource_id': shared_resource.id,
        'start_time': start.isoformat(),
        'end_time': end.isoformat(),
    }
    with patch(_NOTIFY_PATH), patch(_EMAIL_TASK_PATH) as mock_email:
        mock_email.delay = MagicMock()
        resp = api_client.post(RESERVATIONS_URL, payload, format='json')

    assert resp.status_code == status.HTTP_201_CREATED, resp.data
    booking = Booking.objects.get(pk=resp.data['id'])
    assert booking.priority == 3


# ---------------------------------------------------------------------------
# Unit tests for _booking_priority helper
# ---------------------------------------------------------------------------

def test_booking_priority_superadmin():
    user = MagicMock()
    user.role = 'superadmin'
    assert _booking_priority(user) == 3


def test_booking_priority_premium():
    company = MagicMock()
    company.plan = 'premium'
    user = MagicMock()
    user.role = 'company_admin'
    user.company = company
    assert _booking_priority(user) == 3


def test_booking_priority_standard():
    company = MagicMock()
    company.plan = 'standard'
    user = MagicMock()
    user.role = 'company_admin'
    user.company = company
    assert _booking_priority(user) == 2


def test_booking_priority_basic():
    company = MagicMock()
    company.plan = 'basic'
    user = MagicMock()
    user.role = 'company_admin'
    user.company = company
    assert _booking_priority(user) == 1


def test_booking_priority_no_company():
    user = MagicMock()
    user.role = 'guest'
    user.company = None
    assert _booking_priority(user) == 1
