"""
Acceptance-criteria tests for DEV-119 (DEV-131?): Role-based dashboard API.

AC:
  GET /api/v1/dashboard/ — данные зависят от role текущего пользователя

  Superadmin:    total_companies, total_users, bookings_today,
                 recent_events (10), quick_actions
  Company admin: employee_count, active_tasks, bookings_today,
                 announcement_feed (5), pending_approvals {leaves, guest_passes}
  Employee:      my_tasks_today, my_bookings_today,
                 announcement_feed (5), unread_notifications_count
  Guest:         my_bookings_today,
                 quick_booking {available_desks, available_rooms},
                 bc_announcements (5)

  Ответ содержит: role, user {id, full_name, avatar}
"""
from datetime import timedelta, time as dt_time, datetime

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.access.models import GuestPass
from apps.bookings.models import Booking, Resource
from apps.companies.models import Company
from apps.crm.models import Board, Column, Task
from apps.hr.models import LeaveRequest
from apps.notifications.models import Notification
from apps.services.models import Announcement
from apps.users.models import User

DASHBOARD_URL = '/api/v1/dashboard/'


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    from decimal import Decimal
    return Company.objects.create(name='Dashboard Test Co', storage_limit_gb=Decimal('10.000'))


@pytest.fixture
def other_company(db):
    from decimal import Decimal
    return Company.objects.create(name='Other Co', storage_limit_gb=Decimal('5.000'))


@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email='dashboard-superadmin@test.local',
        password='pass',
        first_name='Super',
        last_name='Admin',
        role='superadmin',
        is_email_verified=True,
    )


@pytest.fixture
def company_admin(db, company):
    return User.objects.create_user(
        email='dashboard-company-admin@test.local',
        password='pass',
        first_name='Company',
        last_name='Boss',
        role='company_admin',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='dashboard-employee@test.local',
        password='pass',
        first_name='John',
        last_name='Worker',
        role='employee',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def guest_user(db):
    return User.objects.create_user(
        email='dashboard-guest@test.local',
        password='pass',
        first_name='Jane',
        last_name='Guest',
        role='guest',
        is_email_verified=True,
    )


@pytest.fixture
def desk_resource(db):
    return Resource.objects.create(name='Desk A1', resource_type='desk')


@pytest.fixture
def room_resource(db):
    return Resource.objects.create(name='Room 101', resource_type='meeting_room')


def _booking_today(user, resource, company=None, status='confirmed'):
    today = timezone.localdate()
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(today, dt_time(10, 0)), tz)
    end = timezone.make_aware(datetime.combine(today, dt_time(11, 0)), tz)
    return Booking.objects.create(
        user=user,
        resource=resource,
        company=company,
        start_time=start,
        end_time=end,
        status=status,
    )


def _booking_past(user, resource, company=None):
    """Booking from 3 days ago — should NOT be counted as today."""
    past = timezone.now() - timedelta(days=3)
    return Booking.objects.create(
        user=user,
        resource=resource,
        company=company,
        start_time=past,
        end_time=past + timedelta(hours=1),
        status='confirmed',
    )


# ─────────────────────────────────────────────────────────────────────────────
# Auth gate
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_unauthenticated_returns_401(api_client):
    response = api_client.get(DASHBOARD_URL)
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


# ─────────────────────────────────────────────────────────────────────────────
# Common: role + user object in every response
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestDashboardUserInfo:
    def test_superadmin_response_contains_role_and_user(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        resp = api_client.get(DASHBOARD_URL)
        assert resp.status_code == status.HTTP_200_OK
        data = resp.data
        assert data['role'] == 'superadmin'
        assert 'user' in data
        user_obj = data['user']
        assert user_obj['id'] == superadmin.id
        assert user_obj['full_name'] == superadmin.full_name
        assert 'avatar' in user_obj

    def test_company_admin_response_contains_role_and_user(self, api_client, company_admin):
        api_client.force_authenticate(user=company_admin)
        resp = api_client.get(DASHBOARD_URL)
        assert resp.status_code == status.HTTP_200_OK
        data = resp.data
        assert data['role'] == 'company_admin'
        assert data['user']['id'] == company_admin.id
        assert data['user']['full_name'] == company_admin.full_name

    def test_employee_response_contains_role_and_user(self, api_client, employee):
        api_client.force_authenticate(user=employee)
        resp = api_client.get(DASHBOARD_URL)
        assert resp.status_code == status.HTTP_200_OK
        data = resp.data
        assert data['role'] == 'employee'
        assert data['user']['id'] == employee.id
        assert data['user']['full_name'] == employee.full_name

    def test_guest_response_contains_role_and_user(self, api_client, guest_user):
        api_client.force_authenticate(user=guest_user)
        resp = api_client.get(DASHBOARD_URL)
        assert resp.status_code == status.HTTP_200_OK
        data = resp.data
        assert data['role'] == 'guest'
        assert data['user']['id'] == guest_user.id
        assert data['user']['full_name'] == guest_user.full_name


# ─────────────────────────────────────────────────────────────────────────────
# Superadmin widgets
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestSuperadminDashboard:
    REQUIRED_KEYS = {'role', 'user', 'total_companies', 'total_users', 'bookings_today',
                     'recent_events', 'quick_actions'}

    def test_superadmin_has_correct_widget_keys(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        resp = api_client.get(DASHBOARD_URL)
        assert resp.status_code == status.HTTP_200_OK
        assert self.REQUIRED_KEYS.issubset(set(resp.data.keys()))

    def test_total_companies_count(self, api_client, superadmin, company, other_company):
        api_client.force_authenticate(user=superadmin)
        resp = api_client.get(DASHBOARD_URL)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['total_companies'] == Company.objects.count()

    def test_total_users_count(self, api_client, superadmin, company_admin, employee, guest_user):
        api_client.force_authenticate(user=superadmin)
        resp = api_client.get(DASHBOARD_URL)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['total_users'] == User.objects.filter(is_active=True).count()

    def test_bookings_today_counts_only_todays_confirmed(
        self, api_client, superadmin, desk_resource, employee, company,
    ):
        _booking_today(employee, desk_resource, company=company)
        _booking_past(employee, desk_resource, company=company)

        api_client.force_authenticate(user=superadmin)
        resp = api_client.get(DASHBOARD_URL)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['bookings_today'] >= 1

    def test_recent_events_is_list_max_10(self, api_client, superadmin, desk_resource, employee, company):
        for _ in range(12):
            _booking_today(employee, desk_resource, company=company)

        api_client.force_authenticate(user=superadmin)
        resp = api_client.get(DASHBOARD_URL)
        assert resp.status_code == status.HTTP_200_OK
        assert isinstance(resp.data['recent_events'], list)
        assert len(resp.data['recent_events']) <= 10

    def test_quick_actions_is_list(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        resp = api_client.get(DASHBOARD_URL)
        assert isinstance(resp.data['quick_actions'], list)

    def test_no_company_admin_keys_in_superadmin_response(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        resp = api_client.get(DASHBOARD_URL)
        assert 'employee_count' not in resp.data
        assert 'pending_approvals' not in resp.data


# ─────────────────────────────────────────────────────────────────────────────
# Company admin widgets
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestCompanyAdminDashboard:
    REQUIRED_KEYS = {'role', 'user', 'employee_count', 'active_tasks', 'bookings_today',
                     'announcement_feed', 'pending_approvals'}

    def test_company_admin_has_correct_widget_keys(self, api_client, company_admin):
        api_client.force_authenticate(user=company_admin)
        resp = api_client.get(DASHBOARD_URL)
        assert resp.status_code == status.HTTP_200_OK
        assert self.REQUIRED_KEYS.issubset(set(resp.data.keys()))

    def test_pending_approvals_has_leaves_and_guest_passes(self, api_client, company_admin):
        api_client.force_authenticate(user=company_admin)
        resp = api_client.get(DASHBOARD_URL)
        assert resp.status_code == status.HTTP_200_OK
        pa = resp.data['pending_approvals']
        assert 'leaves' in pa
        assert 'guest_passes' in pa

    def test_employee_count(self, api_client, company_admin, employee, company):
        api_client.force_authenticate(user=company_admin)
        resp = api_client.get(DASHBOARD_URL)
        assert resp.status_code == status.HTTP_200_OK
        expected = User.objects.filter(company=company, is_active=True).count()
        assert resp.data['employee_count'] == expected

    def test_active_tasks_count(self, api_client, company_admin, employee, company):
        board = Board.objects.create(company=company, name='Board', created_by=company_admin)
        col = Column.objects.create(board=board, name='Todo', position=1)
        Task.objects.create(column=col, title='T1', assignee=employee, created_by=company_admin)
        Task.objects.create(column=col, title='T2', assignee=employee, created_by=company_admin)
        Task.objects.create(
            column=col, title='Archived', assignee=employee,
            created_by=company_admin, is_archived=True,
        )

        api_client.force_authenticate(user=company_admin)
        resp = api_client.get(DASHBOARD_URL)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['active_tasks'] >= 2

    def test_bookings_today_counts_company_bookings(
        self, api_client, company_admin, desk_resource, employee, company,
    ):
        _booking_today(employee, desk_resource, company=company)
        _booking_past(employee, desk_resource, company=company)

        api_client.force_authenticate(user=company_admin)
        resp = api_client.get(DASHBOARD_URL)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['bookings_today'] >= 1

    def test_announcement_feed_max_5(self, api_client, company_admin, company):
        for i in range(7):
            Announcement.objects.create(
                title=f'Company Ann {i}',
                body='body',
                company=company,
                author=company_admin,
            )

        api_client.force_authenticate(user=company_admin)
        resp = api_client.get(DASHBOARD_URL)
        assert resp.status_code == status.HTTP_200_OK
        feed = resp.data['announcement_feed']
        assert isinstance(feed, list)
        assert len(feed) <= 5

    def test_pending_approvals_leaves_count(self, api_client, company_admin, employee, company):
        from datetime import date
        LeaveRequest.objects.create(
            user=employee,
            company=company,
            leave_type='vacation',
            status='pending',
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 10),
        )
        LeaveRequest.objects.create(
            user=employee,
            company=company,
            leave_type='day_off',
            status='approved',
            start_date=date(2026, 6, 5),
            end_date=date(2026, 6, 5),
        )

        api_client.force_authenticate(user=company_admin)
        resp = api_client.get(DASHBOARD_URL)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['pending_approvals']['leaves'] == 1

    def test_pending_approvals_guest_passes_count(self, api_client, company_admin, company):
        now = timezone.now()
        GuestPass.objects.create(
            created_by=company_admin,
            company=company,
            guest_name='Guest A',
            guest_email='ga@test.local',
            valid_from=now - timedelta(hours=1),
            valid_until=now + timedelta(days=1),
            status='active',
        )
        GuestPass.objects.create(
            created_by=company_admin,
            company=company,
            guest_name='Guest B',
            guest_email='gb@test.local',
            valid_from=now - timedelta(hours=1),
            valid_until=now + timedelta(days=1),
            status='revoked',
        )

        api_client.force_authenticate(user=company_admin)
        resp = api_client.get(DASHBOARD_URL)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['pending_approvals']['guest_passes'] == 1

    def test_data_isolated_from_other_company(
        self, api_client, company_admin, company, other_company, desk_resource,
    ):
        """Company admin should not see other companies' data."""
        other_admin = User.objects.create_user(
            email='other-admin-dashboard@test.local',
            password='pass',
            first_name='Other',
            last_name='Admin',
            role='company_admin',
            company=other_company,
            is_email_verified=True,
        )
        _booking_today(other_admin, desk_resource, company=other_company)
        LeaveRequest.objects.create(
            user=other_admin,
            company=other_company,
            leave_type='vacation',
            status='pending',
            start_date=timezone.localdate(),
            end_date=timezone.localdate() + timedelta(days=5),
        )

        api_client.force_authenticate(user=company_admin)
        resp = api_client.get(DASHBOARD_URL)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['bookings_today'] == 0
        assert resp.data['pending_approvals']['leaves'] == 0


# ─────────────────────────────────────────────────────────────────────────────
# Employee widgets
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestEmployeeDashboard:
    REQUIRED_KEYS = {'role', 'user', 'my_tasks_today', 'my_bookings_today',
                     'announcement_feed', 'unread_notifications_count'}

    def test_employee_has_correct_widget_keys(self, api_client, employee):
        api_client.force_authenticate(user=employee)
        resp = api_client.get(DASHBOARD_URL)
        assert resp.status_code == status.HTTP_200_OK
        assert self.REQUIRED_KEYS.issubset(set(resp.data.keys()))

    def test_my_bookings_today_counts_own_confirmed_bookings(
        self, api_client, employee, desk_resource, company,
    ):
        _booking_today(employee, desk_resource, company=company)
        _booking_today(employee, desk_resource, company=company)
        _booking_past(employee, desk_resource, company=company)

        api_client.force_authenticate(user=employee)
        resp = api_client.get(DASHBOARD_URL)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['my_bookings_today'] == 2

    def test_my_tasks_today_counts_tasks_with_deadline_today(
        self, api_client, employee, company, company_admin,
    ):
        today = timezone.localdate()
        tz = timezone.get_current_timezone()
        deadline_today = timezone.make_aware(
            datetime.combine(today, dt_time(17, 0)), tz
        )
        deadline_tomorrow = timezone.make_aware(
            datetime.combine(today + timedelta(days=1), dt_time(17, 0)), tz
        )

        board = Board.objects.create(company=company, name='Emp Board', created_by=company_admin)
        col = Column.objects.create(board=board, name='Todo', position=1)
        Task.objects.create(
            column=col, title='Due Today', assignee=employee,
            created_by=company_admin, deadline=deadline_today,
        )
        Task.objects.create(
            column=col, title='Due Tomorrow', assignee=employee,
            created_by=company_admin, deadline=deadline_tomorrow,
        )
        Task.objects.create(
            column=col, title='No Deadline', assignee=employee,
            created_by=company_admin,
        )

        api_client.force_authenticate(user=employee)
        resp = api_client.get(DASHBOARD_URL)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['my_tasks_today'] == 1

    def test_announcement_feed_max_5(self, api_client, employee, company, company_admin):
        for i in range(7):
            Announcement.objects.create(
                title=f'Ann {i}',
                body='body',
                company=company,
                author=company_admin,
            )

        api_client.force_authenticate(user=employee)
        resp = api_client.get(DASHBOARD_URL)
        assert resp.status_code == status.HTTP_200_OK
        feed = resp.data['announcement_feed']
        assert isinstance(feed, list)
        assert len(feed) <= 5

    def test_unread_notifications_count(self, api_client, employee):
        Notification.objects.create(
            user=employee,
            notification_type='system',
            title='Notif 1',
            is_read=False,
        )
        Notification.objects.create(
            user=employee,
            notification_type='system',
            title='Notif 2',
            is_read=False,
        )
        Notification.objects.create(
            user=employee,
            notification_type='system',
            title='Already read',
            is_read=True,
        )

        api_client.force_authenticate(user=employee)
        resp = api_client.get(DASHBOARD_URL)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['unread_notifications_count'] == 2

    def test_employee_sees_only_own_bookings(
        self, api_client, employee, company_admin, desk_resource, company,
    ):
        _booking_today(company_admin, desk_resource, company=company)
        _booking_today(employee, desk_resource, company=company)

        api_client.force_authenticate(user=employee)
        resp = api_client.get(DASHBOARD_URL)
        assert resp.data['my_bookings_today'] == 1


# ─────────────────────────────────────────────────────────────────────────────
# Guest widgets
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestGuestDashboard:
    REQUIRED_KEYS = {'role', 'user', 'my_bookings_today', 'quick_booking', 'bc_announcements'}

    def test_guest_has_correct_widget_keys(self, api_client, guest_user):
        api_client.force_authenticate(user=guest_user)
        resp = api_client.get(DASHBOARD_URL)
        assert resp.status_code == status.HTTP_200_OK
        assert self.REQUIRED_KEYS.issubset(set(resp.data.keys()))

    def test_quick_booking_has_available_desks_and_rooms(self, api_client, guest_user):
        api_client.force_authenticate(user=guest_user)
        resp = api_client.get(DASHBOARD_URL)
        assert resp.status_code == status.HTTP_200_OK
        qb = resp.data['quick_booking']
        assert 'available_desks' in qb
        assert 'available_rooms' in qb

    def test_available_desks_counts_active_resources(
        self, api_client, guest_user, desk_resource, room_resource,
    ):
        api_client.force_authenticate(user=guest_user)
        resp = api_client.get(DASHBOARD_URL)
        assert resp.status_code == status.HTTP_200_OK
        qb = resp.data['quick_booking']
        assert qb['available_desks'] >= 1
        assert qb['available_rooms'] >= 1

    def test_my_bookings_today_guest(self, api_client, guest_user, desk_resource):
        _booking_today(guest_user, desk_resource)
        _booking_past(guest_user, desk_resource)

        api_client.force_authenticate(user=guest_user)
        resp = api_client.get(DASHBOARD_URL)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['my_bookings_today'] == 1

    def test_bc_announcements_max_5_building_only(self, api_client, guest_user, company, company_admin):
        # Building announcements (company=None)
        for i in range(7):
            Announcement.objects.create(
                title=f'BC Ann {i}',
                body='body',
                author=company_admin,
            )
        # Company-scoped announcement — should NOT appear for guest
        Announcement.objects.create(
            title='Company Private',
            body='body',
            company=company,
            author=company_admin,
        )

        api_client.force_authenticate(user=guest_user)
        resp = api_client.get(DASHBOARD_URL)
        assert resp.status_code == status.HTTP_200_OK
        bc = resp.data['bc_announcements']
        assert isinstance(bc, list)
        assert len(bc) <= 5
        titles = [a['title'] for a in bc]
        assert 'Company Private' not in titles

    def test_guest_no_employee_keys(self, api_client, guest_user):
        api_client.force_authenticate(user=guest_user)
        resp = api_client.get(DASHBOARD_URL)
        assert 'employee_count' not in resp.data
        assert 'unread_notifications_count' not in resp.data
        assert 'announcement_feed' not in resp.data
