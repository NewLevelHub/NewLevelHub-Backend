"""
Tests for meeting room participants feature.

- participant_ids accepted on booking create for meeting rooms
- BookingParticipant records created
- Participants receive Notification
- Non-meeting-room resources ignore participant_ids or reject them
"""
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.bookings.models import BookingParticipant, Resource
from apps.companies.models import Company
from apps.notifications.models import Notification
from apps.users.models import User

RESERVATIONS_URL = '/api/v1/bookings/reservations/'


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='Participant Co', plan='basic')


@pytest.fixture
def employee(company):
    return User.objects.create_user(
        email='organizer@participant.test',
        password='pass',
        first_name='Org',
        last_name='Anizer',
        role='employee',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def colleague_1(company):
    return User.objects.create_user(
        email='colleague1@participant.test',
        password='pass',
        first_name='Col',
        last_name='One',
        role='employee',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def colleague_2(company):
    return User.objects.create_user(
        email='colleague2@participant.test',
        password='pass',
        first_name='Col',
        last_name='Two',
        role='employee',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def meeting_room(db):
    return Resource.objects.create(
        name='Conference A',
        resource_type='meeting_room',
        capacity=10,
        available_days=[0, 1, 2, 3, 4],
        available_from='00:00',
        available_until='23:59',
    )


@pytest.fixture
def desk(db):
    return Resource.objects.create(
        name='Desk 1',
        resource_type='desk',
        available_days=[0, 1, 2, 3, 4],
        available_from='00:00',
        available_until='23:59',
    )


def _next_weekday(days_ahead=1):
    now = timezone.localtime()
    target = now + timedelta(days=days_ahead)
    while target.weekday() >= 5:
        target += timedelta(days=1)
    return target


@pytest.mark.django_db
class TestBookingParticipants:
    """Participants on meeting room bookings."""

    def test_create_meeting_with_participants(
        self, api_client, employee, colleague_1, colleague_2, meeting_room
    ):
        api_client.force_authenticate(user=employee)
        day = _next_weekday(1)
        start = day.replace(hour=10, minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=1)

        resp = api_client.post(RESERVATIONS_URL, {
            'resource_id': meeting_room.id,
            'start_time': start.isoformat(),
            'end_time': end.isoformat(),
            'participant_ids': [colleague_1.id, colleague_2.id],
        }, format='json')

        assert resp.status_code == status.HTTP_201_CREATED
        booking_id = resp.json()['id']

        # BookingParticipant records created
        assert BookingParticipant.objects.filter(booking_id=booking_id).count() == 2
        participant_user_ids = set(
            BookingParticipant.objects.filter(booking_id=booking_id)
            .values_list('user_id', flat=True)
        )
        assert participant_user_ids == {colleague_1.id, colleague_2.id}

    def test_participants_receive_notifications(
        self, api_client, employee, colleague_1, colleague_2, meeting_room
    ):
        api_client.force_authenticate(user=employee)
        day = _next_weekday(1)
        start = day.replace(hour=11, minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=1)

        resp = api_client.post(RESERVATIONS_URL, {
            'resource_id': meeting_room.id,
            'start_time': start.isoformat(),
            'end_time': end.isoformat(),
            'participant_ids': [colleague_1.id, colleague_2.id],
        }, format='json')

        assert resp.status_code == status.HTTP_201_CREATED

        # Each participant gets a notification
        notifs_1 = Notification.objects.filter(
            user=colleague_1,
            notification_type='booking_confirmed',
        )
        assert notifs_1.exists()

        notifs_2 = Notification.objects.filter(
            user=colleague_2,
            notification_type='booking_confirmed',
        )
        assert notifs_2.exists()

    def test_participants_listed_in_booking_response(
        self, api_client, employee, colleague_1, meeting_room
    ):
        api_client.force_authenticate(user=employee)
        day = _next_weekday(1)
        start = day.replace(hour=12, minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=1)

        resp = api_client.post(RESERVATIONS_URL, {
            'resource_id': meeting_room.id,
            'start_time': start.isoformat(),
            'end_time': end.isoformat(),
            'participant_ids': [colleague_1.id],
        }, format='json')

        assert resp.status_code == status.HTTP_201_CREATED
        body = resp.json()
        assert 'participants' in body
        assert colleague_1.email in body['participants']

    def test_booking_without_participants_succeeds(
        self, api_client, employee, meeting_room
    ):
        api_client.force_authenticate(user=employee)
        day = _next_weekday(1)
        start = day.replace(hour=13, minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=1)

        resp = api_client.post(RESERVATIONS_URL, {
            'resource_id': meeting_room.id,
            'start_time': start.isoformat(),
            'end_time': end.isoformat(),
        }, format='json')

        assert resp.status_code == status.HTTP_201_CREATED
        assert BookingParticipant.objects.filter(
            booking_id=resp.json()['id']
        ).count() == 0

    def test_desk_booking_ignores_participant_ids(
        self, api_client, employee, colleague_1, desk
    ):
        """For non-meeting-room resources, participant_ids are accepted but ignored."""
        api_client.force_authenticate(user=employee)
        day = _next_weekday(1)
        start = day.replace(hour=10, minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=1)

        resp = api_client.post(RESERVATIONS_URL, {
            'resource_id': desk.id,
            'start_time': start.isoformat(),
            'end_time': end.isoformat(),
            'participant_ids': [colleague_1.id],
        }, format='json')

        # Should succeed but no participants created
        assert resp.status_code == status.HTTP_201_CREATED
        booking_id = resp.json()['id']
        assert BookingParticipant.objects.filter(booking_id=booking_id).count() == 0

    def test_unauthenticated_returns_401(self, api_client):
        resp = api_client.post(RESERVATIONS_URL, {}, format='json')
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED
