from datetime import date, timedelta

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.hr.models import LeaveBalance, LeaveRequest
from apps.users.models import User

LEAVES_URL = '/api/v1/hr/leaves/'


def _results(response):
    data = response.json()
    if isinstance(data, dict) and 'results' in data:
        return data['results']
    return data


def _iso(day):
    return day.isoformat()


def leaves_detail_url(leave_id):
    return f'/api/v1/hr/leaves/{leave_id}/'


def auth(client, user):
    client.force_authenticate(user=user)


def create_leave(*, user, company, leave_type='vacation', start_date=None, end_date=None, status_value='pending'):
    today = timezone.localdate()
    return LeaveRequest.objects.create(
        user=user,
        company=company,
        leave_type=leave_type,
        start_date=start_date or (today + timedelta(days=5)),
        end_date=end_date or (today + timedelta(days=6)),
        status=status_value,
    )


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company_a(db):
    return Company.objects.create(name='HR Alpha', plan='basic')


@pytest.fixture
def company_b(db):
    return Company.objects.create(name='HR Beta', plan='basic')


@pytest.fixture
def employee_a(db, company_a):
    return User.objects.create_user(
        email='employee-a@hr.test',
        password='pass',
        first_name='Emp',
        last_name='A',
        role='employee',
        company=company_a,
    )


@pytest.fixture
def employee_b(db, company_a):
    return User.objects.create_user(
        email='employee-b@hr.test',
        password='pass',
        first_name='Emp',
        last_name='B',
        role='employee',
        company=company_a,
    )


@pytest.fixture
def employee_other_company(db, company_b):
    return User.objects.create_user(
        email='employee-other@hr.test',
        password='pass',
        first_name='Emp',
        last_name='Other',
        role='employee',
        company=company_b,
    )


@pytest.fixture
def admin_a(db, company_a):
    return User.objects.create_user(
        email='admin-a@hr.test',
        password='pass',
        first_name='Admin',
        last_name='A',
        role='company_admin',
        company=company_a,
    )


@pytest.mark.django_db
class TestLeaveCreateValidation:
    def test_create_leave_request_success(self, api_client, employee_a):
        today = timezone.localdate()
        auth(api_client, employee_a)
        payload = {
            'leave_type': 'vacation',
            'start_date': _iso(today + timedelta(days=1)),
            'end_date': _iso(today + timedelta(days=3)),
            'comment': 'Family trip',
        }

        response = api_client.post(LEAVES_URL, payload, format='json')

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['user']['id'] == employee_a.pk
        assert response.data['leave_type'] == 'vacation'
        assert response.data['status'] == 'pending'

    def test_start_date_after_end_date_returns_400(self, api_client, employee_a):
        today = timezone.localdate()
        auth(api_client, employee_a)
        payload = {
            'leave_type': 'day_off',
            'start_date': _iso(today + timedelta(days=5)),
            'end_date': _iso(today + timedelta(days=2)),
        }

        response = api_client.post(LEAVES_URL, payload, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_start_date_in_past_returns_400(self, api_client, employee_a):
        today = timezone.localdate()
        auth(api_client, employee_a)
        payload = {
            'leave_type': 'sick_leave',
            'start_date': _iso(today - timedelta(days=1)),
            'end_date': _iso(today + timedelta(days=1)),
        }

        response = api_client.post(LEAVES_URL, payload, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_overlap_with_approved_request_returns_400(self, api_client, employee_a, company_a):
        today = timezone.localdate()
        create_leave(
            user=employee_a,
            company=company_a,
            leave_type='vacation',
            start_date=today + timedelta(days=10),
            end_date=today + timedelta(days=12),
            status_value='approved',
        )

        auth(api_client, employee_a)
        payload = {
            'leave_type': 'remote',
            'start_date': _iso(today + timedelta(days=11)),
            'end_date': _iso(today + timedelta(days=13)),
        }
        response = api_client.post(LEAVES_URL, payload, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_vacation_without_remaining_days_returns_400(self, api_client, employee_a):
        today = timezone.localdate()
        LeaveBalance.objects.create(
            user=employee_a,
            year=(today + timedelta(days=10)).year,
            total_days=2,
            used_days=2,
        )

        auth(api_client, employee_a)
        payload = {
            'leave_type': 'vacation',
            'start_date': _iso(today + timedelta(days=10)),
            'end_date': _iso(today + timedelta(days=11)),
        }
        response = api_client.post(LEAVES_URL, payload, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data['error']['details']['non_field_errors'][0] == (
            'Недостаточно дней отпуска для выбранных дат.'
        )


@pytest.mark.django_db
class TestLeaveListAccessAndFilters:
    def test_employee_sees_only_own_requests(self, api_client, employee_a, employee_b, company_a):
        own_leave = create_leave(user=employee_a, company=company_a, leave_type='vacation')
        create_leave(user=employee_b, company=company_a, leave_type='remote')

        auth(api_client, employee_a)
        response = api_client.get(LEAVES_URL)

        assert response.status_code == status.HTTP_200_OK
        ids = {item['id'] for item in _results(response)}
        assert own_leave.pk in ids
        assert len(ids) == 1

    def test_company_admin_sees_company_requests_only(
        self,
        api_client,
        admin_a,
        employee_a,
        employee_b,
        employee_other_company,
        company_a,
        company_b,
    ):
        leave_a = create_leave(user=employee_a, company=company_a, leave_type='vacation')
        leave_b = create_leave(user=employee_b, company=company_a, leave_type='day_off')
        leave_other = create_leave(user=employee_other_company, company=company_b, leave_type='remote')

        auth(api_client, admin_a)
        response = api_client.get(LEAVES_URL)

        assert response.status_code == status.HTTP_200_OK
        ids = {item['id'] for item in _results(response)}
        assert leave_a.pk in ids
        assert leave_b.pk in ids
        assert leave_other.pk not in ids

    def test_filters_status_and_leave_type_work(self, api_client, admin_a, employee_a, company_a):
        approved_remote = create_leave(
            user=employee_a,
            company=company_a,
            leave_type='remote',
            status_value='approved',
        )
        create_leave(
            user=employee_a,
            company=company_a,
            leave_type='remote',
            status_value='pending',
        )
        create_leave(
            user=employee_a,
            company=company_a,
            leave_type='vacation',
            status_value='approved',
        )

        auth(api_client, admin_a)
        response = api_client.get(LEAVES_URL, {'status': 'approved', 'leave_type': 'remote'})

        assert response.status_code == status.HTTP_200_OK
        ids = [item['id'] for item in _results(response)]
        assert ids == [approved_remote.pk]

    def test_year_filter_returns_only_requests_for_selected_start_year(
        self, api_client, admin_a, employee_a, company_a
    ):
        leave_2025 = create_leave(
            user=employee_a,
            company=company_a,
            leave_type='vacation',
            start_date=date(2025, 5, 2),
            end_date=date(2025, 5, 3),
        )
        leave_2026 = create_leave(
            user=employee_a,
            company=company_a,
            leave_type='vacation',
            start_date=date(2026, 5, 2),
            end_date=date(2026, 5, 3),
        )

        auth(api_client, admin_a)
        response = api_client.get(LEAVES_URL, {'year': 2025})

        assert response.status_code == status.HTTP_200_OK
        ids = {item['id'] for item in _results(response)}
        assert leave_2025.pk in ids
        assert leave_2026.pk not in ids


@pytest.mark.django_db
class TestLeaveRetrieve:
    def test_retrieve_contains_required_fields(self, api_client, employee_a, company_a):
        leave = create_leave(user=employee_a, company=company_a, leave_type='sick_leave')
        auth(api_client, employee_a)

        response = api_client.get(leaves_detail_url(leave.pk))

        assert response.status_code == status.HTTP_200_OK
        for field in (
            'user',
            'leave_type',
            'start_date',
            'end_date',
            'status',
            'reviewed_by',
            'review_comment',
            'created_at',
        ):
            assert field in response.data

    def test_employee_cannot_retrieve_other_employee_leave(self, api_client, employee_a, employee_b, company_a):
        leave_other = create_leave(user=employee_b, company=company_a, leave_type='vacation')
        auth(api_client, employee_a)

        response = api_client.get(leaves_detail_url(leave_other.pk))

        assert response.status_code == status.HTTP_404_NOT_FOUND
