from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.access.models import GuestPass
from apps.bookings.models import Booking, Resource
from apps.companies.models import Company
from apps.crm.models import Board, Column, Task
from apps.hr.models import LeaveRequest
from apps.storage.models import File
from apps.users.models import User

URL = '/api/v1/analytics/company/'


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='AC Company', storage_limit_gb=Decimal('10.000'))


@pytest.fixture
def other_company(db):
    return Company.objects.create(name='Other AC Company', storage_limit_gb=Decimal('3.000'))


@pytest.fixture
def company_admin(db, company):
    user = User.objects.create_user(
        email='company-admin-analytics@test.local',
        password='pass',
        first_name='Main',
        last_name='Admin',
        role='company_admin',
        company=company,
        is_email_verified=True,
    )
    user.last_login = timezone.now() - timedelta(days=2)
    user.save(update_fields=['last_login'])
    return user


@pytest.fixture
def employee_a(db, company):
    user = User.objects.create_user(
        email='employee-a-analytics@test.local',
        password='pass',
        first_name='Alice',
        last_name='Worker',
        role='employee',
        company=company,
        is_email_verified=True,
    )
    user.last_login = timezone.now() - timedelta(days=1)
    user.save(update_fields=['last_login'])
    return user


@pytest.fixture
def employee_b(db, company):
    user = User.objects.create_user(
        email='employee-b-analytics@test.local',
        password='pass',
        first_name='Bob',
        last_name='Worker',
        role='employee',
        company=company,
        is_email_verified=True,
    )
    user.last_login = timezone.now() - timedelta(days=20)
    user.save(update_fields=['last_login'])
    return user


@pytest.fixture
def superadmin(db, company):
    return User.objects.create_user(
        email='superadmin-analytics@test.local',
        password='pass',
        first_name='Super',
        last_name='Admin',
        role='superadmin',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def outsider_employee(db, other_company):
    return User.objects.create_user(
        email='outsider-employee-analytics@test.local',
        password='pass',
        first_name='Out',
        last_name='Sider',
        role='employee',
        company=other_company,
        is_email_verified=True,
    )


@pytest.fixture
def company_resource(db):
    return Resource.objects.create(name='Desk A1', resource_type='desk')


def _create_booking(*, company, resource, user, start_shift_days=0):
    start = timezone.now() + timedelta(days=start_shift_days)
    return Booking.objects.create(
        company=company,
        resource=resource,
        user=user,
        start_time=start,
        end_time=start + timedelta(hours=1),
        status='confirmed',
    )


def _create_guest_pass(*, creator, company, created_days_ago):
    now = timezone.now()
    gp = GuestPass.objects.create(
        created_by=creator,
        company=company,
        guest_name='Guest',
        guest_email=f'guest-{created_days_ago}@test.local',
        valid_from=now - timedelta(days=1),
        valid_until=now + timedelta(days=1),
        status='active',
    )
    GuestPass.objects.filter(pk=gp.pk).update(created_at=now - timedelta(days=created_days_ago))


def _create_company_file(*, owner, company, size_bytes):
    return File.objects.create(
        name=f'file-{size_bytes}.txt',
        file=SimpleUploadedFile(f'file-{size_bytes}.txt', b'payload'),
        file_size=size_bytes,
        owner=owner,
        company=company,
    )


@pytest.mark.django_db
class TestCompanyAnalyticsAC:
    def test_company_admin_gets_expected_company_analytics_payload(
        self, api_client, company_admin, employee_a, employee_b, company_resource, company,
    ):
        _create_booking(company=company, resource=company_resource, user=employee_a, start_shift_days=0)
        _create_booking(company=company, resource=company_resource, user=employee_a, start_shift_days=-5)
        _create_booking(company=company, resource=company_resource, user=employee_b, start_shift_days=-35)

        board = Board.objects.create(company=company, name='Main board', created_by=company_admin)
        col_todo = Column.objects.create(board=board, name='To Do', position=1)
        col_in_progress = Column.objects.create(board=board, name='In Progress', position=2)
        col_done = Column.objects.create(board=board, name='Done', position=3)
        Task.objects.create(column=col_todo, title='Task Todo', assignee=employee_a, created_by=company_admin)
        Task.objects.create(
            column=col_in_progress,
            title='Task WIP',
            assignee=employee_a,
            created_by=company_admin,
        )
        Task.objects.create(column=col_done, title='Task Done', assignee=employee_b, created_by=company_admin)

        _create_company_file(owner=company_admin, company=company, size_bytes=1024)
        _create_company_file(owner=employee_a, company=company, size_bytes=2048)

        _create_guest_pass(creator=company_admin, company=company, created_days_ago=0)
        _create_guest_pass(creator=company_admin, company=company, created_days_ago=40)

        api_client.force_authenticate(user=company_admin)
        response = api_client.get(URL)

        assert response.status_code == status.HTTP_200_OK
        data = response.data
        assert set(data.keys()) == {
            'total_employees',
            'active_7d',
            'bookings_month',
            'storage',
            'active_crm_tasks',
            'guest_visits_month',
            'employee_activity',
            'pending_approvals',
        }
        assert data['total_employees'] == 3
        assert data['active_7d'] == 2
        assert data['bookings_month'] == 2
        assert data['storage'] == {
            'used': 3072,
            'limit': int(company.storage_limit_gb * 1024 * 1024 * 1024),
        }
        act = data['active_crm_tasks']
        assert act['total'] == 3
        assert act['todo'] == 1
        assert act['in_progress'] == 1
        assert act['done'] == 1
        assert act['other'] == 0
        assert len(act['by_column']) == 3
        assert sum(c['count'] for c in act['by_column']) == 3
        assert data['guest_visits_month'] == 1

        by_user_id = {row['user']['id']: row for row in data['employee_activity']}
        assert set(by_user_id.keys()) == {company_admin.id, employee_a.id, employee_b.id}
        assert by_user_id[employee_a.id]['user']['full_name'] == employee_a.full_name
        assert by_user_id[employee_a.id]['user']['email'] == employee_a.email
        assert by_user_id[employee_a.id]['booking_count_30d'] == 2
        assert by_user_id[employee_a.id]['task_count_active'] == 2
        assert by_user_id[employee_a.id]['last_login'] is not None
        assert by_user_id[employee_b.id]['booking_count_30d'] == 0
        assert by_user_id[employee_b.id]['task_count_active'] == 0
        assert by_user_id[company_admin.id]['booking_count_30d'] == 0
        assert by_user_id[company_admin.id]['task_count_active'] == 0
        # last_login is a UTC datetime; the API serialises it in the server's local timezone
        # (Asia/Almaty, +05:00), so we must compare against the *local* date, not the UTC date.
        local_last_login_date = timezone.localtime(employee_a.last_login).date().isoformat()
        assert by_user_id[employee_a.id]['last_login'].startswith(local_last_login_date)

        # pending_approvals — no leaves; two active guest passes were created in this test
        approvals = data['pending_approvals']
        assert approvals['leaves'] == []
        assert len(approvals['guest_passes']) == 2
        assert approvals['guest_passes'][0]['guest_name'] == 'Guest'
        assert approvals['guest_passes'][0]['host_name'] == company_admin.full_name

    def test_employee_is_forbidden(self, api_client, employee_a):
        api_client.force_authenticate(user=employee_a)
        response = api_client.get(URL)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_superadmin_is_allowed(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(URL)
        assert response.status_code == status.HTTP_200_OK

    def test_data_is_isolated_to_admin_company(
        self, api_client, company_admin, company, other_company, outsider_employee, company_resource,
    ):
        _create_booking(company=other_company, resource=company_resource, user=outsider_employee, start_shift_days=-1)
        _create_company_file(owner=outsider_employee, company=other_company, size_bytes=9999)
        _create_guest_pass(creator=outsider_employee, company=other_company, created_days_ago=1)

        other_board = Board.objects.create(company=other_company, name='Other board', created_by=outsider_employee)
        other_col_done = Column.objects.create(board=other_board, name='Done', position=1)
        Task.objects.create(
            column=other_col_done,
            title='Other task',
            assignee=outsider_employee,
            created_by=outsider_employee,
        )

        api_client.force_authenticate(user=company_admin)
        response = api_client.get(URL)
        assert response.status_code == status.HTTP_200_OK
        assert response.data['bookings_month'] == 0
        assert response.data['storage']['used'] == 0
        assert response.data['guest_visits_month'] == 0
        z = response.data['active_crm_tasks']
        assert z['total'] == 0
        assert z['todo'] == z['in_progress'] == z['done'] == z['other'] == 0
        assert z['by_column'] == []

    def test_crm_statuses_are_counted_for_flexible_column_names(self, api_client, company_admin, company):
        board = Board.objects.create(company=company, name='Alt names board', created_by=company_admin)
        col_todo = Column.objects.create(board=board, name='ToDo', position=1)
        col_in_progress = Column.objects.create(board=board, name='В работе', position=2)
        col_done = Column.objects.create(board=board, name='Сделано', position=3)

        Task.objects.create(column=col_todo, title='todo', assignee=company_admin, created_by=company_admin)
        Task.objects.create(
            column=col_in_progress,
            title='in progress',
            assignee=company_admin,
            created_by=company_admin,
        )
        Task.objects.create(column=col_done, title='done', assignee=company_admin, created_by=company_admin)

        api_client.force_authenticate(user=company_admin)
        response = api_client.get(URL)
        assert response.status_code == status.HTTP_200_OK
        act = response.data['active_crm_tasks']
        assert act['total'] == 3
        assert act['todo'] == act['in_progress'] == act['done'] == 1
        assert act['other'] == 0

    def test_custom_crm_columns_are_in_total_and_other_bucket(
        self, api_client, company_admin, company,
    ):
        """Columns whose names do not match todo/in_progress/done heuristics still count in total and by_column."""
        board = Board.objects.create(company=company, name='Sprint', created_by=company_admin)
        col_review = Column.objects.create(board=board, name='Code review', position=0)
        col_todo = Column.objects.create(board=board, name='To Do', position=1)
        Task.objects.create(column=col_review, title='R1', assignee=company_admin, created_by=company_admin)
        Task.objects.create(column=col_review, title='R2', assignee=company_admin, created_by=company_admin)
        Task.objects.create(column=col_todo, title='T1', assignee=company_admin, created_by=company_admin)

        api_client.force_authenticate(user=company_admin)
        response = api_client.get(URL)
        assert response.status_code == status.HTTP_200_OK
        act = response.data['active_crm_tasks']
        assert act['total'] == 3
        assert act['todo'] == 1
        assert act['other'] == 2
        assert act['in_progress'] == act['done'] == 0
        assert len(act['by_column']) == 2
        by_name = {c['name']: c['count'] for c in act['by_column']}
        assert by_name == {'Code review': 2, 'To Do': 1}

    def test_pending_approvals_contains_leaves_and_guest_passes(
        self, api_client, company_admin, employee_a, company, other_company, outsider_employee,
    ):
        """Pending leaves and active guest passes for the company appear in pending_approvals."""
        now = timezone.now()

        # Two pending leaves for the company; one approved (must be excluded)
        pending_leave = LeaveRequest.objects.create(
            user=employee_a,
            company=company,
            leave_type='vacation',
            status='pending',
            start_date=(now + timedelta(days=5)).date(),
            end_date=(now + timedelta(days=10)).date(),
        )
        LeaveRequest.objects.create(
            user=employee_a,
            company=company,
            leave_type='sick_leave',
            status='approved',
            start_date=(now + timedelta(days=1)).date(),
            end_date=(now + timedelta(days=2)).date(),
        )
        # Pending leave for another company (must be excluded)
        LeaveRequest.objects.create(
            user=outsider_employee,
            company=other_company,
            leave_type='day_off',
            status='pending',
            start_date=(now + timedelta(days=3)).date(),
            end_date=(now + timedelta(days=3)).date(),
        )

        # One active guest pass for the company; one revoked (must be excluded)
        active_gp = GuestPass.objects.create(
            created_by=company_admin,
            company=company,
            guest_name='Jane Visitor',
            guest_email='jane@visitor.test',
            valid_from=now + timedelta(hours=1),
            valid_until=now + timedelta(days=1),
            status='active',
        )
        GuestPass.objects.create(
            created_by=company_admin,
            company=company,
            guest_name='Old Visitor',
            guest_email='old@visitor.test',
            valid_from=now - timedelta(days=2),
            valid_until=now - timedelta(days=1),
            status='revoked',
        )
        # Active guest pass for another company (must be excluded)
        GuestPass.objects.create(
            created_by=outsider_employee,
            company=other_company,
            guest_name='Other Visitor',
            guest_email='other@visitor.test',
            valid_from=now,
            valid_until=now + timedelta(days=1),
            status='active',
        )

        api_client.force_authenticate(user=company_admin)
        response = api_client.get(URL)
        assert response.status_code == status.HTTP_200_OK

        approvals = response.data['pending_approvals']
        assert set(approvals.keys()) == {'leaves', 'guest_passes'}

        assert len(approvals['leaves']) == 1
        leave = approvals['leaves'][0]
        assert leave['id'] == pending_leave.id
        assert leave['employee_name'] == employee_a.full_name
        assert leave['leave_type'] == 'vacation'
        assert str(leave['start_date']) == pending_leave.start_date.isoformat()
        assert str(leave['end_date']) == pending_leave.end_date.isoformat()
        assert leave['created_at'] is not None

        assert len(approvals['guest_passes']) == 1
        gp = approvals['guest_passes'][0]
        assert gp['id'] == active_gp.id
        assert gp['guest_name'] == 'Jane Visitor'
        assert gp['host_name'] == company_admin.full_name
        assert gp['visit_date'] is not None
        assert gp['created_at'] is not None
