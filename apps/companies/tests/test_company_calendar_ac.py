from datetime import datetime, time, timedelta

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.access.models import GuestPass
from apps.bookings.models import Booking, Resource
from apps.companies.models import Company
from apps.crm.models import Board, Column, Task
from apps.hr.models import LeaveRequest
from apps.users.models import User


def _iso_date(value):
    return value.isoformat()


def _items(response):
    data = response.json()
    if isinstance(data, dict) and 'results' in data:
        return data['results']
    return data


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='Calendar AC Co', plan='basic')


@pytest.fixture
def company_other(db):
    return Company.objects.create(name='Calendar Other Co', plan='basic')


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='calendar.employee@test.local',
        password='pass',
        first_name='Ivan',
        last_name='Employee',
        role='employee',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def colleague(db, company):
    return User.objects.create_user(
        email='calendar.colleague@test.local',
        password='pass',
        first_name='Olga',
        last_name='Colleague',
        role='employee',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def outsider(db, company_other):
    return User.objects.create_user(
        email='calendar.outsider@test.local',
        password='pass',
        first_name='Other',
        last_name='Company',
        role='employee',
        company=company_other,
        is_email_verified=True,
    )


@pytest.fixture
def admin(db, company):
    return User.objects.create_user(
        email='calendar.admin@test.local',
        password='pass',
        first_name='Ayan',
        last_name='Admin',
        role='company_admin',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def desk(db):
    return Resource.objects.create(
        name='Desk C-12',
        resource_type='desk',
        available_days=[0, 1, 2, 3, 4, 5, 6],
        available_from='08:00',
        available_until='22:00',
    )


def _calendar_url(company_id):
    return f'/api/v1/companies/{company_id}/calendar/'


def _calendar_busy_url(company_id):
    return f'/api/v1/companies/{company_id}/calendar/busy/'


def _dt_for(day, hh, mm=0):
    return timezone.make_aware(datetime.combine(day, time(hour=hh, minute=mm)))


@pytest.mark.django_db
class TestCompanyCalendarAggregateAC:
    def test_returns_all_event_types_with_required_shape(
        self, api_client, company, employee, colleague, admin, desk
    ):
        target_day = timezone.localdate() + timedelta(days=3)
        date_from = target_day - timedelta(days=1)
        date_to = target_day + timedelta(days=1)

        booking = Booking.objects.create(
            resource=desk,
            user=employee,
            company=company,
            start_time=_dt_for(target_day, 9),
            end_time=_dt_for(target_day, 10),
            status='confirmed',
        )
        leave = LeaveRequest.objects.create(
            user=colleague,
            company=company,
            leave_type='vacation',
            status='approved',
            start_date=target_day,
            end_date=target_day,
        )
        board = Board.objects.create(company=company, name='Sales', created_by=admin)
        column = Column.objects.create(board=board, name='Todo', position=1)
        task = Task.objects.create(
            column=column,
            title='Call enterprise lead',
            created_by=admin,
            assignee=colleague,
            deadline=_dt_for(target_day, 16),
            priority='high',
            position=1,
        )
        guest = GuestPass.objects.create(
            created_by=employee,
            company=company,
            guest_name='John Visitor',
            guest_email='john.visitor@test.local',
            valid_from=_dt_for(target_day, 11),
            valid_until=_dt_for(target_day, 12),
        )

        api_client.force_authenticate(user=employee)
        response = api_client.get(
            _calendar_url(company.id),
            {'date_from': _iso_date(date_from), 'date_to': _iso_date(date_to)},
        )

        assert response.status_code == status.HTTP_200_OK
        events = _items(response)
        event_types = {event['type'] for event in events}
        assert {'booking', 'leave', 'task_deadline', 'guest_visit'}.issubset(event_types)

        by_type = {event['type']: event for event in events}
        assert by_type['booking']['title'] == booking.resource.name
        assert by_type['task_deadline']['title'] == task.title
        assert 'vacation' in by_type['leave']['title'].lower()
        assert by_type['guest_visit']['title'] == guest.guest_name

        for event in events:
            assert set(event.keys()) == {'type', 'title', 'start', 'end', 'user'}
            assert set(event['user'].keys()) == {'id', 'full_name'}

    def test_filters_user_id_event_type_and_my(
        self, api_client, company, employee, colleague, admin, desk
    ):
        target_day = timezone.localdate() + timedelta(days=4)
        Booking.objects.create(
            resource=desk,
            user=employee,
            company=company,
            start_time=_dt_for(target_day, 9),
            end_time=_dt_for(target_day, 10),
            status='confirmed',
        )
        Booking.objects.create(
            resource=desk,
            user=colleague,
            company=company,
            start_time=_dt_for(target_day, 10),
            end_time=_dt_for(target_day, 11),
            status='confirmed',
        )
        board = Board.objects.create(company=company, name='Ops', created_by=admin)
        column = Column.objects.create(board=board, name='Todo', position=1)
        Task.objects.create(
            column=column,
            title='Task for employee',
            created_by=admin,
            assignee=employee,
            deadline=_dt_for(target_day, 18),
            priority='medium',
            position=1,
        )

        api_client.force_authenticate(user=employee)

        user_filtered = api_client.get(
            _calendar_url(company.id),
            {
                'date_from': _iso_date(target_day),
                'date_to': _iso_date(target_day),
                'user_id': colleague.id,
            },
        )
        assert user_filtered.status_code == status.HTTP_200_OK
        assert {item['user']['id'] for item in _items(user_filtered)} == {colleague.id}

        type_filtered = api_client.get(
            _calendar_url(company.id),
            {
                'date_from': _iso_date(target_day),
                'date_to': _iso_date(target_day),
                'event_type': 'task_deadline',
            },
        )
        assert type_filtered.status_code == status.HTTP_200_OK
        assert {item['type'] for item in _items(type_filtered)} == {'task_deadline'}

        my_filtered = api_client.get(
            _calendar_url(company.id),
            {
                'date_from': _iso_date(target_day),
                'date_to': _iso_date(target_day),
                'my': 'true',
            },
        )
        assert my_filtered.status_code == status.HTTP_200_OK
        assert {item['user']['id'] for item in _items(my_filtered)} == {employee.id}

    def test_scopes_events_by_company_only(
        self, api_client, company, company_other, employee, outsider, desk
    ):
        target_day = timezone.localdate() + timedelta(days=2)
        Booking.objects.create(
            resource=desk,
            user=employee,
            company=company,
            start_time=_dt_for(target_day, 13),
            end_time=_dt_for(target_day, 14),
            status='confirmed',
        )
        Booking.objects.create(
            resource=desk,
            user=outsider,
            company=company_other,
            start_time=_dt_for(target_day, 14),
            end_time=_dt_for(target_day, 15),
            status='confirmed',
        )

        api_client.force_authenticate(user=employee)
        response = api_client.get(
            _calendar_url(company.id),
            {'date_from': _iso_date(target_day), 'date_to': _iso_date(target_day)},
        )

        assert response.status_code == status.HTTP_200_OK
        users_in_response = {item['user']['id'] for item in _items(response)}
        assert outsider.id not in users_in_response
        assert employee.id in users_in_response


@pytest.mark.django_db
class TestCompanyCalendarBusyAC:
    def test_busy_returns_slots_for_user_day(
        self, api_client, company, employee, desk
    ):
        target_day = timezone.localdate() + timedelta(days=5)
        Booking.objects.create(
            resource=desk,
            user=employee,
            company=company,
            start_time=_dt_for(target_day, 9),
            end_time=_dt_for(target_day, 10, 30),
            status='confirmed',
        )
        LeaveRequest.objects.create(
            user=employee,
            company=company,
            leave_type='day_off',
            status='approved',
            start_date=target_day,
            end_date=target_day,
        )

        api_client.force_authenticate(user=employee)
        response = api_client.get(
            _calendar_busy_url(company.id),
            {'user_id': employee.id, 'date': _iso_date(target_day)},
        )

        assert response.status_code == status.HTTP_200_OK
        slots = _items(response)
        assert len(slots) >= 2
        for slot in slots:
            assert 'start' in slot
            assert 'end' in slot
