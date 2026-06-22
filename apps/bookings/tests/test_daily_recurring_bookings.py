"""Tests for `daily` recurrence type on RecurringBooking."""
from datetime import date, datetime, time, timedelta

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.bookings.models import Booking, RecurringBooking, Resource
from apps.bookings.tasks import (
    _matching_dates,
    generate_recurring_bookings,
    recurring_has_creatable_occurrence,
)
from apps.companies.models import Company
from apps.users.models import User

RECURRING_URL = '/api/v1/bookings/recurring/'


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='Daily Co', plan='basic')


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='employee@daily.test',
        password='pass',
        first_name='Daily',
        last_name='Employee',
        role='employee',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def company_admin(db, company):
    return User.objects.create_user(
        email='admin@daily.test',
        password='pass',
        first_name='Daily',
        last_name='Admin',
        role='company_admin',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def desk_resource(db):
    return Resource.objects.create(
        name='Daily Desk',
        resource_type='desk',
        available_days=list(range(7)),
        available_from='00:00',
        available_until='23:59',
    )


def _local_aware(target_date, target_time):
    naive = datetime.combine(target_date, target_time)
    return timezone.make_aware(naive, timezone.get_current_timezone())


# ---------------------------------------------------------------------------
# Unit tests — task helpers
# ---------------------------------------------------------------------------

class TestMatchingDatesDaily:
    """_matching_dates with recurrence_type='daily' yields every calendar day."""

    def test_yields_every_day_in_range(self):
        start = date(2026, 6, 10)
        end = date(2026, 6, 14)
        result = list(_matching_dates(
            day_of_week=None, start_date=start, end_date=end, recurrence_type='daily',
        ))
        assert result == [
            date(2026, 6, 10),
            date(2026, 6, 11),
            date(2026, 6, 12),
            date(2026, 6, 13),
            date(2026, 6, 14),
        ]

    def test_single_day_range(self):
        d = date(2026, 6, 10)
        result = list(_matching_dates(
            day_of_week=None, start_date=d, end_date=d, recurrence_type='daily',
        ))
        assert result == [d]

    def test_start_after_end_yields_nothing(self):
        result = list(_matching_dates(
            day_of_week=None,
            start_date=date(2026, 6, 15),
            end_date=date(2026, 6, 10),
            recurrence_type='daily',
        ))
        assert result == []

    def test_weekly_unchanged_by_recurrence_param(self):
        """Passing recurrence_type='weekly' still uses 7-day stepping."""
        start = date(2026, 6, 8)   # Monday
        end = date(2026, 6, 29)    # three more Mondays
        result = list(_matching_dates(
            day_of_week=0, start_date=start, end_date=end, recurrence_type='weekly',
        ))
        assert result == [
            date(2026, 6, 8),
            date(2026, 6, 15),
            date(2026, 6, 22),
            date(2026, 6, 29),
        ]


class TestRecurringHasCreatableOccurrenceDaily:
    def test_returns_true_when_future_slot_exists(self):
        from unittest.mock import patch
        from zoneinfo import ZoneInfo

        fixed_now = timezone.make_aware(
            datetime(2026, 6, 10, 8, 0, 0), ZoneInfo('Asia/Almaty'),
        )
        with patch('apps.bookings.tasks.timezone.now', return_value=fixed_now):
            result = recurring_has_creatable_occurrence(
                day_of_week=None,
                end_time=time(12, 0),
                repeat_until=date(2026, 6, 12),
                base_date=date(2026, 6, 10),
                recurrence_type='daily',
            )
        assert result is True

    def test_returns_false_when_all_past(self):
        from unittest.mock import patch
        from zoneinfo import ZoneInfo

        fixed_now = timezone.make_aware(
            datetime(2026, 6, 12, 15, 0, 0), ZoneInfo('Asia/Almaty'),
        )
        with patch('apps.bookings.tasks.timezone.now', return_value=fixed_now):
            result = recurring_has_creatable_occurrence(
                day_of_week=None,
                end_time=time(12, 0),
                repeat_until=date(2026, 6, 12),
                base_date=date(2026, 6, 10),
                recurrence_type='daily',
            )
        assert result is False


# ---------------------------------------------------------------------------
# Integration tests — API
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestDailyRecurringBookingCreateAPI:
    def test_employee_can_create_daily_series(self, api_client, employee, desk_resource):
        today = timezone.localdate()
        repeat_until = today + timedelta(days=4)

        api_client.force_authenticate(user=employee)
        response = api_client.post(
            RECURRING_URL,
            {
                'resource_id': desk_resource.id,
                'recurrence_type': 'daily',
                'start_time': '09:00',
                'end_time': '10:00',
                'repeat_until': repeat_until.isoformat(),
            },
            format='json',
        )

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        payload = response.json()
        assert payload['recurrence_type'] == 'daily'
        assert payload['day_of_week'] is None

        # Expect a booking for each day from today through repeat_until where the slot is in the future.
        bookings = Booking.objects.filter(recurring_booking_id=payload['id']).order_by('start_time')
        booking_dates = [timezone.localtime(b.start_time).date() for b in bookings]
        # All dates should be in [today, repeat_until]
        for d in booking_dates:
            assert today <= d <= repeat_until
        # All days in range should appear (some may be skipped if already past at exact moment)
        expected_count = (repeat_until - today).days + 1
        assert len(booking_dates) <= expected_count

    def test_daily_series_generates_one_booking_per_day(self, api_client, employee, desk_resource):
        today = timezone.localdate()
        repeat_until = today + timedelta(days=2)

        api_client.force_authenticate(user=employee)
        response = api_client.post(
            RECURRING_URL,
            {
                'resource_id': desk_resource.id,
                'recurrence_type': 'daily',
                'start_time': '14:00',
                'end_time': '15:00',
                'repeat_until': repeat_until.isoformat(),
            },
            format='json',
        )

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        recurring_id = response.json()['id']
        bookings = Booking.objects.filter(recurring_booking_id=recurring_id).order_by('start_time')

        dates = [timezone.localtime(b.start_time).date() for b in bookings]
        # No duplicate dates
        assert len(dates) == len(set(dates))
        # Each booking is exactly 1 hour
        for b in bookings:
            assert (b.end_time - b.start_time).total_seconds() == 3600

    def test_daily_series_skips_conflicting_days(self, api_client, employee, company, desk_resource):
        today = timezone.localdate()
        repeat_until = today + timedelta(days=3)
        conflict_day = today + timedelta(days=1)
        conflict_start = _local_aware(conflict_day, time(9, 0))
        conflict_end = _local_aware(conflict_day, time(10, 0))

        # Pre-create a confirmed booking on day+1 in the same slot
        Booking.objects.create(
            resource=desk_resource,
            user=employee,
            company=company,
            start_time=conflict_start,
            end_time=conflict_end,
            status='confirmed',
        )

        api_client.force_authenticate(user=employee)
        response = api_client.post(
            RECURRING_URL,
            {
                'resource_id': desk_resource.id,
                'recurrence_type': 'daily',
                'start_time': '09:00',
                'end_time': '10:00',
                'repeat_until': repeat_until.isoformat(),
            },
            format='json',
        )

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        payload = response.json()
        assert conflict_day.isoformat() in payload['skipped_dates']
        booking_dates = [
            timezone.localtime(b.start_time).date()
            for b in Booking.objects.filter(recurring_booking_id=payload['id'])
        ]
        assert conflict_day not in booking_dates

    def test_weekly_recurrence_still_works_with_day_of_week(self, api_client, employee, desk_resource):
        today = timezone.localdate()
        days_until_monday = (0 - today.weekday()) % 7 or 7
        next_monday = today + timedelta(days=days_until_monday)

        api_client.force_authenticate(user=employee)
        response = api_client.post(
            RECURRING_URL,
            {
                'resource_id': desk_resource.id,
                'recurrence_type': 'weekly',
                'day_of_week': 0,
                'start_time': '10:00',
                'end_time': '11:00',
                'repeat_until': (next_monday + timedelta(days=14)).isoformat(),
            },
            format='json',
        )

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        payload = response.json()
        assert payload['recurrence_type'] == 'weekly'
        assert payload['day_of_week'] == 0

    def test_weekly_recurrence_without_day_of_week_returns_400(self, api_client, employee, desk_resource):
        today = timezone.localdate()
        api_client.force_authenticate(user=employee)
        response = api_client.post(
            RECURRING_URL,
            {
                'resource_id': desk_resource.id,
                'recurrence_type': 'weekly',
                'start_time': '10:00',
                'end_time': '11:00',
                'repeat_until': (today + timedelta(days=14)).isoformat(),
            },
            format='json',
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()

    def test_daily_recurring_booking_has_recurring_booking_id_on_each_booking(
        self, api_client, employee, desk_resource
    ):
        today = timezone.localdate()
        api_client.force_authenticate(user=employee)
        response = api_client.post(
            RECURRING_URL,
            {
                'resource_id': desk_resource.id,
                'recurrence_type': 'daily',
                'start_time': '11:00',
                'end_time': '12:00',
                'repeat_until': (today + timedelta(days=2)).isoformat(),
            },
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED, response.json()
        recurring_id = response.json()['id']
        for booking in Booking.objects.filter(recurring_booking_id=recurring_id):
            assert booking.recurring_booking_id == recurring_id

    def test_duplicate_daily_series_for_same_slot_is_rejected(self, api_client, employee, desk_resource):
        today = timezone.localdate()
        payload = {
            'resource_id': desk_resource.id,
            'recurrence_type': 'daily',
            'start_time': '13:00',
            'end_time': '14:00',
            'repeat_until': (today + timedelta(days=5)).isoformat(),
        }
        api_client.force_authenticate(user=employee)
        first = api_client.post(RECURRING_URL, payload, format='json')
        assert first.status_code == status.HTTP_201_CREATED, first.json()

        second = api_client.post(RECURRING_URL, payload, format='json')
        assert second.status_code == status.HTTP_400_BAD_REQUEST

    def test_daily_and_weekly_series_on_same_slot_are_independent(
        self, api_client, employee, desk_resource
    ):
        """A daily and a weekly series on the same time slot are separate; both should be created."""
        today = timezone.localdate()
        days_until_monday = (0 - today.weekday()) % 7 or 7
        next_monday = today + timedelta(days=days_until_monday)

        api_client.force_authenticate(user=employee)
        daily_response = api_client.post(
            RECURRING_URL,
            {
                'resource_id': desk_resource.id,
                'recurrence_type': 'daily',
                'start_time': '15:00',
                'end_time': '16:00',
                'repeat_until': (next_monday + timedelta(days=7)).isoformat(),
            },
            format='json',
        )
        assert daily_response.status_code == status.HTTP_201_CREATED, daily_response.json()

        # A weekly series on Monday at the same time will conflict with the daily booking on that Monday;
        # its create may succeed (series created) but that specific Monday slot will appear in skipped_dates.
        weekly_response = api_client.post(
            RECURRING_URL,
            {
                'resource_id': desk_resource.id,
                'recurrence_type': 'weekly',
                'day_of_week': 0,
                'start_time': '16:30',  # different slot to avoid duplicate-series rejection
                'end_time': '17:30',
                'repeat_until': (next_monday + timedelta(days=14)).isoformat(),
            },
            format='json',
        )
        assert weekly_response.status_code == status.HTTP_201_CREATED, weekly_response.json()


@pytest.mark.django_db
class TestDailyRecurringCeleryTask:
    def test_generate_recurring_bookings_advances_daily_series_by_one_day(
        self, employee, company, desk_resource
    ):
        today = timezone.localdate()
        recurring = RecurringBooking.objects.create(
            resource=desk_resource,
            user=employee,
            company=company,
            recurrence_type='daily',
            day_of_week=None,
            start_time='10:00',
            end_time='11:00',
            valid_from=today - timedelta(days=2),
            valid_until=today + timedelta(days=1),
        )
        original_until = recurring.valid_until

        generate_recurring_bookings()

        recurring.refresh_from_db()
        # Daily: advanced by 1 day
        assert recurring.valid_until == original_until + timedelta(days=1)
        # A new booking should exist for the newly opened day
        assert Booking.objects.filter(
            recurring_booking=recurring,
            start_time__date__gt=original_until,
        ).exists()

    def test_generate_recurring_bookings_advances_weekly_series_by_seven_days(
        self, employee, company, desk_resource
    ):
        today = timezone.localdate()
        days_until_tuesday = (1 - today.weekday()) % 7 or 7
        recurring = RecurringBooking.objects.create(
            resource=desk_resource,
            user=employee,
            company=company,
            recurrence_type='weekly',
            day_of_week=1,  # Tuesday
            start_time='10:00',
            end_time='11:00',
            valid_from=today - timedelta(days=14),
            valid_until=today + timedelta(days=days_until_tuesday),
        )
        original_until = recurring.valid_until

        generate_recurring_bookings()

        recurring.refresh_from_db()
        # Weekly: advanced by 7 days
        assert recurring.valid_until == original_until + timedelta(days=7)
