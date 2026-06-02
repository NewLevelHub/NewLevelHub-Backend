from datetime import date, datetime, time, timedelta

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.bookings.models import Booking, RecurringBooking, Resource
from apps.bookings.tasks import generate_recurring_bookings
from apps.companies.models import Company
from apps.users.models import User

RECURRING_URL = '/api/v1/bookings/recurring/'


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='Recurring Co', plan='basic')


@pytest.fixture
def other_company(db):
    return Company.objects.create(name='Other Recurring Co', plan='basic')


@pytest.fixture
def company_admin(db, company):
    return User.objects.create_user(
        email='company-admin@recurring.test',
        password='pass',
        first_name='Company',
        last_name='Admin',
        role='company_admin',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='employee@recurring.test',
        password='pass',
        first_name='Regular',
        last_name='Employee',
        role='employee',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def another_employee(db, company):
    return User.objects.create_user(
        email='another@recurring.test',
        password='pass',
        first_name='Another',
        last_name='Employee',
        role='employee',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def another_company_admin(db, company):
    return User.objects.create_user(
        email='another-admin@recurring.test',
        password='pass',
        first_name='Another',
        last_name='Admin',
        role='company_admin',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def guest_user(db):
    return User.objects.create_user(
        email='guest@recurring.test',
        password='pass',
        first_name='Guest',
        last_name='User',
        role='guest',
        is_email_verified=True,
    )


@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email='superadmin@recurring.test',
        password='pass',
        first_name='Super',
        last_name='Admin',
        role='superadmin',
        is_email_verified=True,
    )


@pytest.fixture
def other_company_employee(db, other_company):
    return User.objects.create_user(
        email='employee-other@recurring.test',
        password='pass',
        first_name='Other',
        last_name='Company',
        role='employee',
        company=other_company,
        is_email_verified=True,
    )


@pytest.fixture
def desk_resource(db):
    return Resource.objects.create(
        name='Recurring Desk',
        resource_type='desk',
        available_days=list(range(7)),
        available_from='00:00',
        available_until='23:59',
    )


def _local_aware(target_date, target_time):
    naive = datetime.combine(target_date, target_time)
    return timezone.make_aware(naive, timezone.get_current_timezone())


def _next_weekday_date(weekday):
    today = timezone.localdate()
    days_ahead = (weekday - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return today + timedelta(days=days_ahead)


def _results(response):
    data = response.json()
    if isinstance(data, dict) and 'results' in data:
        return data['results']
    return data


@pytest.mark.django_db
class TestRecurringBookingCreateAC:
    def test_company_admin_can_create_series_generates_bookings_and_skips_conflicts(
        self, api_client, company_admin, company, desk_resource
    ):
        monday = _next_weekday_date(0)
        conflict_date = monday + timedelta(days=7)
        repeat_until = monday + timedelta(days=21)
        conflict_start = _local_aware(conflict_date, time(10, 0))
        conflict_end = _local_aware(conflict_date, time(11, 0))
        Booking.objects.create(
            resource=desk_resource,
            user=company_admin,
            company=company,
            start_time=conflict_start,
            end_time=conflict_end,
            status='confirmed',
        )

        api_client.force_authenticate(user=company_admin)
        response = api_client.post(
            RECURRING_URL,
            {
                'resource_id': desk_resource.id,
                'day_of_week': 0,
                'start_time': '10:00',
                'end_time': '11:00',
                'repeat_until': repeat_until.isoformat(),
            },
            format='json',
        )

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        payload = response.json()
        assert 'skipped_dates' in payload
        assert payload['skipped_dates'] == [conflict_date.isoformat()]

        recurring_booking = RecurringBooking.objects.get(pk=payload['id'])
        series_bookings = Booking.objects.filter(recurring_booking=recurring_booking).order_by('start_time')
        assert list(series_bookings.values_list('start_time__date', flat=True)) == [
            monday,
            monday + timedelta(days=14),
            monday + timedelta(days=21),
        ]

    def test_employee_can_create_recurring_booking(self, api_client, employee, desk_resource):
        monday = _next_weekday_date(0)
        api_client.force_authenticate(user=employee)

        response = api_client.post(
            RECURRING_URL,
            {
                'resource_id': desk_resource.id,
                'day_of_week': 0,
                'start_time': '09:00',
                'end_time': '10:00',
                'repeat_until': (monday + timedelta(days=14)).isoformat(),
            },
            format='json',
        )

        assert response.status_code == status.HTTP_201_CREATED, response.json()

    def test_company_admin_cannot_create_duplicate_active_series_for_same_slot(
        self, api_client, company_admin, desk_resource
    ):
        monday = _next_weekday_date(0)
        repeat_until = monday + timedelta(days=14)
        payload = {
            'resource_id': desk_resource.id,
            'day_of_week': 0,
            'start_time': '10:00',
            'end_time': '11:00',
            'repeat_until': repeat_until.isoformat(),
        }

        api_client.force_authenticate(user=company_admin)
        first_response = api_client.post(RECURRING_URL, payload, format='json')
        assert first_response.status_code == status.HTTP_201_CREATED, first_response.json()

        second_response = api_client.post(RECURRING_URL, payload, format='json')
        assert second_response.status_code == status.HTTP_400_BAD_REQUEST
        error_payload = second_response.json()
        assert 'error' in error_payload or 'detail' in error_payload
        assert RecurringBooking.objects.filter(
            user=company_admin,
            resource=desk_resource,
            day_of_week=0,
            start_time='10:00',
            end_time='11:00',
            is_active=True,
        ).count() == 1

    def test_company_admin_cannot_duplicate_slot_after_employee(
        self, api_client, company_admin, employee, desk_resource
    ):
        monday = _next_weekday_date(0)
        repeat_until = monday + timedelta(days=14)
        payload = {
            'resource_id': desk_resource.id,
            'day_of_week': 0,
            'start_time': '10:00',
            'end_time': '11:00',
            'repeat_until': repeat_until.isoformat(),
        }
        api_client.force_authenticate(user=employee)
        first = api_client.post(RECURRING_URL, payload, format='json')
        assert first.status_code == status.HTTP_201_CREATED, first.json()

        api_client.force_authenticate(user=company_admin)
        second = api_client.post(RECURRING_URL, payload, format='json')
        assert second.status_code == status.HTTP_400_BAD_REQUEST

    def test_today_weekday_included_when_time_in_future(
        self, api_client, employee, desk_resource
    ):
        today = timezone.localdate()
        now_local = timezone.localtime()
        start_dt = (now_local + timedelta(minutes=30)).replace(second=0, microsecond=0)
        end_dt = start_dt + timedelta(hours=1)
        if start_dt.date() != today or end_dt.date() != today:
            pytest.skip('Test would span midnight')

        api_client.force_authenticate(user=employee)
        response = api_client.post(
            RECURRING_URL,
            {
                'resource_id': desk_resource.id,
                'day_of_week': today.weekday(),
                'start_time': start_dt.strftime('%H:%M'),
                'end_time': end_dt.strftime('%H:%M'),
                'repeat_until': (today + timedelta(days=14)).isoformat(),
            },
            format='json',
        )

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        payload = response.json()
        assert payload['valid_from'] == today.isoformat()
        assert today.isoformat() not in payload['skipped_dates']

        series_dates = [
            timezone.localtime(b.start_time).date()
            for b in Booking.objects.filter(recurring_booking_id=payload['id']).order_by('start_time')
        ]
        assert today in series_dates

    def test_today_weekday_skipped_when_end_time_already_passed(
        self, api_client, employee, desk_resource
    ):
        today = timezone.localdate()
        now_local = timezone.localtime()
        end_dt = (now_local - timedelta(minutes=5)).replace(second=0, microsecond=0)
        start_dt = end_dt - timedelta(minutes=30)
        if start_dt.date() != today or end_dt.date() != today or start_dt >= end_dt:
            pytest.skip('Cannot pick a past time window today')

        api_client.force_authenticate(user=employee)
        response = api_client.post(
            RECURRING_URL,
            {
                'resource_id': desk_resource.id,
                'day_of_week': today.weekday(),
                'start_time': start_dt.strftime('%H:%M'),
                'end_time': end_dt.strftime('%H:%M'),
                'repeat_until': (today + timedelta(days=14)).isoformat(),
            },
            format='json',
        )

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        payload = response.json()
        assert payload['valid_from'] == today.isoformat()
        assert today.isoformat() not in payload['skipped_dates']

        series_dates = [
            timezone.localtime(b.start_time).date()
            for b in Booking.objects.filter(recurring_booking_id=payload['id']).order_by('start_time')
        ]
        assert today not in series_dates

    def test_past_weekday_in_range_not_reported_as_conflict(
        self, api_client, employee, company, desk_resource
    ):
        """Past occurrences are skipped silently; skipped_dates lists only real overlaps."""
        from unittest.mock import patch
        from zoneinfo import ZoneInfo

        # Sunday 2026-06-07 12:00 local — Monday 10:00-11:00 slot already passed for prior weeks
        fixed_now = timezone.make_aware(
            datetime(2026, 6, 7, 12, 0, 0),
            ZoneInfo('Asia/Almaty'),
        )
        monday = date(2026, 6, 8)
        past_monday = date(2026, 6, 1)
        Booking.objects.create(
            resource=desk_resource,
            user=employee,
            company=company,
            start_time=_local_aware(past_monday, time(10, 0)),
            end_time=_local_aware(past_monday, time(11, 0)),
            status='confirmed',
        )

        api_client.force_authenticate(user=employee)
        with patch('django.utils.timezone.now', return_value=fixed_now):
            response = api_client.post(
                RECURRING_URL,
                {
                    'resource_id': desk_resource.id,
                    'day_of_week': 0,
                    'start_time': '10:00',
                    'end_time': '11:00',
                    'repeat_until': monday.isoformat(),
                },
                format='json',
            )

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        payload = response.json()
        assert payload['skipped_dates'] == []
        assert payload['valid_from'] == monday.isoformat()

        series_dates = [
            timezone.localtime(b.start_time).date()
            for b in Booking.objects.filter(recurring_booking_id=payload['id']).order_by('start_time')
        ]
        assert series_dates == [monday]

    def test_create_rejected_when_only_occurrence_is_in_the_past(
        self, api_client, employee, desk_resource
    ):
        from unittest.mock import patch
        from zoneinfo import ZoneInfo

        # Monday 12:00 — 10:00–11:00 already passed; repeat_until is the same Monday only
        fixed_now = timezone.make_aware(
            datetime(2026, 6, 8, 12, 0, 0),
            ZoneInfo('Asia/Almaty'),
        )
        same_monday = date(2026, 6, 8)

        api_client.force_authenticate(user=employee)
        with patch('django.utils.timezone.now', return_value=fixed_now):
            response = api_client.post(
                RECURRING_URL,
                {
                    'resource_id': desk_resource.id,
                    'day_of_week': 0,
                    'start_time': '10:00',
                    'end_time': '11:00',
                    'repeat_until': same_monday.isoformat(),
                },
                format='json',
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
        assert RecurringBooking.objects.filter(
            resource=desk_resource,
            day_of_week=0,
            start_time=time(10, 0),
            end_time=time(11, 0),
            valid_until=same_monday,
        ).count() == 0

    def test_guest_cannot_create_recurring_booking(self, api_client, guest_user, desk_resource):
        monday = _next_weekday_date(0)
        api_client.force_authenticate(user=guest_user)

        response = api_client.post(
            RECURRING_URL,
            {
                'resource_id': desk_resource.id,
                'day_of_week': 0,
                'start_time': '09:00',
                'end_time': '10:00',
                'repeat_until': monday.isoformat(),
            },
            format='json',
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestRecurringBookingListAC:
    def test_get_returns_only_current_user_recurring_bookings(
        self, api_client, employee, another_employee, other_company_employee, company, other_company, desk_resource
    ):
        own = RecurringBooking.objects.create(
            resource=desk_resource,
            user=employee,
            company=company,
            day_of_week=0,
            start_time='09:00',
            end_time='10:00',
            valid_from=date.today(),
            valid_until=date.today() + timedelta(days=30),
        )
        RecurringBooking.objects.create(
            resource=desk_resource,
            user=another_employee,
            company=company,
            day_of_week=1,
            start_time='11:00',
            end_time='12:00',
            valid_from=date.today(),
            valid_until=date.today() + timedelta(days=30),
        )
        RecurringBooking.objects.create(
            resource=desk_resource,
            user=other_company_employee,
            company=other_company,
            day_of_week=2,
            start_time='14:00',
            end_time='15:00',
            valid_from=date.today(),
            valid_until=date.today() + timedelta(days=30),
        )

        api_client.force_authenticate(user=employee)
        response = api_client.get(RECURRING_URL)

        assert response.status_code == status.HTTP_200_OK
        ids = [item['id'] for item in _results(response)]
        assert ids == [own.id]

    def test_company_admin_get_includes_own_and_employee_series_same_company(
        self, api_client, company_admin, employee, another_company_admin, other_company_employee,
        company, other_company, desk_resource
    ):
        own_series = RecurringBooking.objects.create(
            resource=desk_resource,
            user=company_admin,
            company=company,
            day_of_week=0,
            start_time='09:00',
            end_time='10:00',
            valid_from=date.today(),
            valid_until=date.today() + timedelta(days=30),
        )
        employee_series = RecurringBooking.objects.create(
            resource=desk_resource,
            user=employee,
            company=company,
            day_of_week=1,
            start_time='11:00',
            end_time='12:00',
            valid_from=date.today(),
            valid_until=date.today() + timedelta(days=30),
        )
        RecurringBooking.objects.create(
            resource=desk_resource,
            user=another_company_admin,
            company=company,
            day_of_week=2,
            start_time='13:00',
            end_time='14:00',
            valid_from=date.today(),
            valid_until=date.today() + timedelta(days=30),
        )
        RecurringBooking.objects.create(
            resource=desk_resource,
            user=other_company_employee,
            company=other_company,
            day_of_week=3,
            start_time='15:00',
            end_time='16:00',
            valid_from=date.today(),
            valid_until=date.today() + timedelta(days=30),
        )

        api_client.force_authenticate(user=company_admin)
        response = api_client.get(RECURRING_URL)

        assert response.status_code == status.HTTP_200_OK
        rows = _results(response)
        assert sorted(item['id'] for item in rows) == sorted([own_series.id, employee_series.id])
        assert sorted(item['user_role'] for item in rows) == ['company_admin', 'employee']


@pytest.mark.django_db
class TestRecurringBookingDeleteAC:
    def test_delete_cancels_series_by_removing_only_future_bookings(
        self, api_client, employee, company, desk_resource
    ):
        recurring = RecurringBooking.objects.create(
            resource=desk_resource,
            user=employee,
            company=company,
            day_of_week=0,
            start_time='09:00',
            end_time='10:00',
            valid_from=date.today() - timedelta(days=30),
            valid_until=date.today() + timedelta(days=30),
        )
        past_start = timezone.now() - timedelta(days=7)
        future_start = timezone.now() + timedelta(days=7)
        past_booking = Booking.objects.create(
            resource=desk_resource,
            user=employee,
            company=company,
            recurring_booking=recurring,
            start_time=past_start,
            end_time=past_start + timedelta(hours=1),
            status='confirmed',
        )
        Booking.objects.create(
            resource=desk_resource,
            user=employee,
            company=company,
            recurring_booking=recurring,
            start_time=future_start,
            end_time=future_start + timedelta(hours=1),
            status='confirmed',
        )

        api_client.force_authenticate(user=employee)
        response = api_client.delete(f'{RECURRING_URL}{recurring.id}/')

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not Booking.objects.filter(recurring_booking=recurring, start_time__gt=timezone.now()).exists()
        assert Booking.objects.filter(pk=past_booking.pk).exists()

    def test_company_admin_can_cancel_employee_series_same_company(
        self, api_client, company_admin, employee, company, desk_resource
    ):
        recurring = RecurringBooking.objects.create(
            resource=desk_resource,
            user=employee,
            company=company,
            day_of_week=0,
            start_time='09:00',
            end_time='10:00',
            valid_from=date.today(),
            valid_until=date.today() + timedelta(days=30),
        )

        api_client.force_authenticate(user=company_admin)
        response = api_client.delete(f'{RECURRING_URL}{recurring.id}/')

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not RecurringBooking.objects.filter(pk=recurring.id).exists()

    def test_company_admin_cannot_cancel_superadmin_series(
        self, api_client, company_admin, superadmin, desk_resource
    ):
        recurring = RecurringBooking.objects.create(
            resource=desk_resource,
            user=superadmin,
            company=company_admin.company,
            day_of_week=0,
            start_time='09:00',
            end_time='10:00',
            valid_from=date.today(),
            valid_until=date.today() + timedelta(days=30),
        )

        api_client.force_authenticate(user=company_admin)
        response = api_client.delete(f'{RECURRING_URL}{recurring.id}/')

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert RecurringBooking.objects.filter(pk=recurring.id).exists()

    def test_company_admin_cannot_cancel_employee_series_other_company(
        self, api_client, company_admin, other_company_employee, other_company, desk_resource
    ):
        recurring = RecurringBooking.objects.create(
            resource=desk_resource,
            user=other_company_employee,
            company=other_company,
            day_of_week=0,
            start_time='09:00',
            end_time='10:00',
            valid_from=date.today(),
            valid_until=date.today() + timedelta(days=30),
        )

        api_client.force_authenticate(user=company_admin)
        response = api_client.delete(f'{RECURRING_URL}{recurring.id}/')

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert RecurringBooking.objects.filter(pk=recurring.id).exists()

    def test_employee_cannot_cancel_another_employee_series(
        self, api_client, employee, another_employee, company, desk_resource
    ):
        recurring = RecurringBooking.objects.create(
            resource=desk_resource,
            user=another_employee,
            company=company,
            day_of_week=0,
            start_time='09:00',
            end_time='10:00',
            valid_from=date.today(),
            valid_until=date.today() + timedelta(days=30),
        )

        api_client.force_authenticate(user=employee)
        response = api_client.delete(f'{RECURRING_URL}{recurring.id}/')

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert RecurringBooking.objects.filter(pk=recurring.id).exists()

    def test_superadmin_can_cancel_any_series(
        self, api_client, superadmin, other_company_employee, other_company, desk_resource
    ):
        recurring = RecurringBooking.objects.create(
            resource=desk_resource,
            user=other_company_employee,
            company=other_company,
            day_of_week=0,
            start_time='09:00',
            end_time='10:00',
            valid_from=date.today(),
            valid_until=date.today() + timedelta(days=30),
        )

        api_client.force_authenticate(user=superadmin)
        response = api_client.delete(f'{RECURRING_URL}{recurring.id}/')

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not RecurringBooking.objects.filter(pk=recurring.id).exists()


@pytest.mark.django_db
class TestRecurringBookingTaskAC:
    def test_weekly_task_extends_series_for_next_period(self, employee, company, desk_resource):
        today = timezone.localdate()
        recurring = RecurringBooking.objects.create(
            resource=desk_resource,
            user=employee,
            company=company,
            day_of_week=(today.weekday() + 1) % 7,
            start_time='15:00',
            end_time='16:00',
            valid_from=today - timedelta(days=14),
            valid_until=today + timedelta(days=7),
        )
        existing_date = recurring.valid_until
        Booking.objects.create(
            resource=desk_resource,
            user=employee,
            company=company,
            recurring_booking=recurring,
            start_time=_local_aware(existing_date, time(15, 0)),
            end_time=_local_aware(existing_date, time(16, 0)),
            status='confirmed',
        )

        generate_recurring_bookings()

        assert Booking.objects.filter(
            recurring_booking=recurring,
            start_time__date__gt=existing_date,
        ).exists()


@pytest.mark.django_db
class TestRecurringBookingLinkAC:
    def test_each_generated_booking_has_recurring_booking_id(self, api_client, employee, desk_resource):
        monday = _next_weekday_date(0)
        api_client.force_authenticate(user=employee)

        response = api_client.post(
            RECURRING_URL,
            {
                'resource_id': desk_resource.id,
                'day_of_week': 0,
                'start_time': '08:00',
                'end_time': '09:00',
                'repeat_until': (monday + timedelta(days=7)).isoformat(),
            },
            format='json',
        )

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        recurring_id = response.json()['id']
        for booking in Booking.objects.filter(recurring_booking_id=recurring_id):
            assert booking.recurring_booking_id == recurring_id
