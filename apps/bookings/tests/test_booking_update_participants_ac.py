from datetime import timedelta

import pytest
from django.apps import apps
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.bookings.models import Booking, BookingParticipant, Resource
from apps.companies.models import Company
from apps.notifications.models import Notification
from apps.users.models import User

RESERVATIONS_URL = '/api/v1/bookings/reservations/'


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='Booking Update AC Co', plan='basic')


@pytest.fixture
def organizer(db, company):
    return User.objects.create_user(
        email='organizer@booking-update-ac.test',
        password='pass',
        first_name='Booking',
        last_name='Organizer',
        role='employee',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def company_admin(db, company):
    return User.objects.create_user(
        email='company-admin@booking-update-ac.test',
        password='pass',
        first_name='Company',
        last_name='Admin',
        role='company_admin',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def other_company(db):
    return Company.objects.create(name='Other Booking Update AC Co', plan='basic')


@pytest.fixture
def other_company_employee(db, other_company):
    return User.objects.create_user(
        email='employee-other-company@booking-update-ac.test',
        password='pass',
        first_name='Other',
        last_name='CompanyEmployee',
        role='employee',
        company=other_company,
        is_email_verified=True,
    )


@pytest.fixture
def participant_one(db, company):
    return User.objects.create_user(
        email='participant-one@booking-update-ac.test',
        password='pass',
        first_name='Participant',
        last_name='One',
        role='employee',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def participant_two(db, company):
    return User.objects.create_user(
        email='participant-two@booking-update-ac.test',
        password='pass',
        first_name='Participant',
        last_name='Two',
        role='employee',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def meeting_room_resource(db):
    return Resource.objects.create(
        name='AC Update Meeting Room',
        resource_type='meeting_room',
        min_cancel_minutes=90,
        available_days=[0, 1, 2, 3, 4, 5, 6],
        available_from='07:00',
        available_until='23:00',
        capacity=8,
    )


@pytest.fixture
def desk_resource(db):
    return Resource.objects.create(
        name='AC Update Desk',
        resource_type='desk',
        min_cancel_minutes=30,
        available_days=[0, 1, 2, 3, 4, 5, 6],
        available_from='07:00',
        available_until='23:00',
    )


def _create_booking(*, user, company, resource, start_delta_minutes, duration_minutes=60):
    start = timezone.now() + timedelta(minutes=start_delta_minutes)
    end = start + timedelta(minutes=duration_minutes)
    return Booking.objects.create(
        resource=resource,
        user=user,
        company=company,
        start_time=start,
        end_time=end,
        status='confirmed',
    )


def _next_day_slot(*, days_ahead=1, hour=10, duration_minutes=60):
    local_now = timezone.localtime()
    local_start = (local_now + timedelta(days=days_ahead)).replace(
        hour=hour,
        minute=0,
        second=0,
        microsecond=0,
    )
    start = timezone.make_aware(local_start.replace(tzinfo=None))
    end = start + timedelta(minutes=duration_minutes)
    return start, end


@pytest.mark.django_db
class TestBookingTimeUpdateConflictControl:
    def test_patch_booking_time_without_conflict_updates_successfully(
        self, api_client, organizer, company, meeting_room_resource
    ):
        booking = _create_booking(
            user=organizer,
            company=company,
            resource=meeting_room_resource,
            start_delta_minutes=240,
        )
        api_client.force_authenticate(user=organizer)

        new_start, new_end = _next_day_slot(days_ahead=2, hour=10, duration_minutes=90)
        response = api_client.patch(
            f'{RESERVATIONS_URL}{booking.id}/',
            {
                'start_time': new_start.isoformat(),
                'end_time': new_end.isoformat(),
            },
            format='json',
        )

        assert response.status_code == status.HTTP_200_OK
        booking.refresh_from_db()
        assert booking.start_time == new_start
        assert booking.end_time == new_end

    def test_patch_booking_time_with_overlap_returns_409(
        self, api_client, organizer, company, meeting_room_resource
    ):
        booking = _create_booking(
            user=organizer,
            company=company,
            resource=meeting_room_resource,
            start_delta_minutes=240,
            duration_minutes=60,
        )
        conflict_start, conflict_end = _next_day_slot(days_ahead=2, hour=11, duration_minutes=30)
        Booking.objects.create(
            user=organizer,
            company=company,
            resource=meeting_room_resource,
            start_time=conflict_start - timedelta(minutes=10),
            end_time=conflict_end + timedelta(minutes=10),
            status='confirmed',
        )
        api_client.force_authenticate(user=organizer)

        response = api_client.patch(
            f'{RESERVATIONS_URL}{booking.id}/',
            {
                'start_time': conflict_start.isoformat(),
                'end_time': conflict_end.isoformat(),
            },
            format='json',
        )

        assert response.status_code == status.HTTP_409_CONFLICT
        booking.refresh_from_db()
        assert booking.end_time - booking.start_time == timedelta(minutes=60)


@pytest.mark.django_db
class TestBookingParticipantsManagement:
    def test_add_participants_for_meeting_room_creates_rows_and_notifications(
        self,
        api_client,
        company_admin,
        company,
        meeting_room_resource,
        participant_one,
        participant_two,
    ):
        booking = _create_booking(
            user=company_admin,
            company=company,
            resource=meeting_room_resource,
            start_delta_minutes=300,
        )
        api_client.force_authenticate(user=company_admin)
        response = api_client.post(
            f'{RESERVATIONS_URL}{booking.id}/participants/',
            {'user_ids': [participant_one.id, participant_two.id]},
            format='json',
        )

        assert response.status_code == status.HTTP_200_OK
        participant_ids = set(
            BookingParticipant.objects.filter(booking=booking).values_list('user_id', flat=True)
        )
        assert participant_ids == {participant_one.id, participant_two.id}

        notification_user_ids = set(
            Notification.objects.filter(
                user_id__in=[participant_one.id, participant_two.id],
                notification_type='booking_confirmed',
            ).values_list('user_id', flat=True)
        )
        assert notification_user_ids == {participant_one.id, participant_two.id}

    def test_add_participants_for_non_meeting_room_returns_400(
        self, api_client, company_admin, company, desk_resource, participant_one
    ):
        booking = _create_booking(
            user=company_admin,
            company=company,
            resource=desk_resource,
            start_delta_minutes=300,
        )
        api_client.force_authenticate(user=company_admin)
        response = api_client.post(
            f'{RESERVATIONS_URL}{booking.id}/participants/',
            {'user_ids': [participant_one.id]},
            format='json',
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert BookingParticipant.objects.filter(booking=booking).count() == 0

    def test_delete_participant_removes_relation(
        self, api_client, company_admin, company, meeting_room_resource, participant_one
    ):
        booking = _create_booking(
            user=company_admin,
            company=company,
            resource=meeting_room_resource,
            start_delta_minutes=300,
        )
        BookingParticipant.objects.create(booking=booking, user=participant_one)
        api_client.force_authenticate(user=company_admin)
        response = api_client.delete(
            f'{RESERVATIONS_URL}{booking.id}/participants/{participant_one.id}/'
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not BookingParticipant.objects.filter(booking=booking, user=participant_one).exists()

    def test_employee_owner_cannot_add_participants(
        self, api_client, organizer, company, meeting_room_resource, participant_one
    ):
        booking = _create_booking(
            user=organizer,
            company=company,
            resource=meeting_room_resource,
            start_delta_minutes=300,
        )
        api_client.force_authenticate(user=organizer)
        response = api_client.post(
            f'{RESERVATIONS_URL}{booking.id}/participants/',
            {'user_ids': [participant_one.id]},
            format='json',
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert BookingParticipant.objects.filter(booking=booking).count() == 0


@pytest.mark.django_db
class TestBookingChangesAudit:
    def test_booking_update_and_participants_changes_are_written_to_audit(
        self, api_client, company_admin, company, meeting_room_resource, participant_one
    ):
        booking = _create_booking(
            user=company_admin,
            company=company,
            resource=meeting_room_resource,
            start_delta_minutes=300,
        )
        api_client.force_authenticate(user=company_admin)

        patch_start, patch_end = _next_day_slot(days_ahead=2, hour=12, duration_minutes=60)
        patch_response = api_client.patch(
            f'{RESERVATIONS_URL}{booking.id}/',
            {
                'start_time': patch_start.isoformat(),
                'end_time': patch_end.isoformat(),
            },
            format='json',
        )
        assert patch_response.status_code == status.HTTP_200_OK

        add_response = api_client.post(
            f'{RESERVATIONS_URL}{booking.id}/participants/',
            {'user_ids': [participant_one.id]},
            format='json',
        )
        assert add_response.status_code == status.HTTP_200_OK

        remove_response = api_client.delete(
            f'{RESERVATIONS_URL}{booking.id}/participants/{participant_one.id}/'
        )
        assert remove_response.status_code == status.HTTP_204_NO_CONTENT

        audit_model = apps.get_model('bookings', 'BookingChangeAudit')
        actions = list(
            audit_model.objects.filter(booking_id=booking.id).values_list('action', flat=True)
        )
        assert 'time_updated' in actions
        assert 'participants_added' in actions
        assert 'participant_removed' in actions


@pytest.mark.django_db
class TestBookingUpdateIsolation:
    def test_patch_booking_from_other_company_returns_404(
        self, api_client, organizer, other_company_employee, company, meeting_room_resource
    ):
        booking = _create_booking(
            user=organizer,
            company=company,
            resource=meeting_room_resource,
            start_delta_minutes=360,
        )
        new_start, new_end = _next_day_slot(days_ahead=2, hour=15, duration_minutes=60)

        api_client.force_authenticate(user=other_company_employee)
        response = api_client.patch(
            f'{RESERVATIONS_URL}{booking.id}/',
            {
                'start_time': new_start.isoformat(),
                'end_time': new_end.isoformat(),
            },
            format='json',
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
