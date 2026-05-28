"""End-to-end reproduction of the reported visibility bug:

  1. Admin A creates leave via API with assigned_reviewer=B.
  2. Superadmin deactivates / removes B via the company endpoint.
  3. Admin A lists their leaves — leave MUST still be in results.
"""
from datetime import date

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.hr.models import LeaveRequest
from apps.users.models import User

LEAVES_URL = '/api/v1/hr/leaves/'


def _auth(client, user):
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='Repro Co', plan='basic')


@pytest.fixture
def admin_a(db, company):
    return User.objects.create_user(
        email='a@repro.test', password='pass', first_name='A', last_name='Admin',
        role='company_admin', company=company,
    )


@pytest.fixture
def admin_b(db, company):
    return User.objects.create_user(
        email='b@repro.test', password='pass', first_name='B', last_name='Admin',
        role='company_admin', company=company,
    )


@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email='su@repro.test', password='pass', first_name='Super', last_name='Admin',
        role='superadmin',
    )


def _create_leave_via_api(api_client, admin_a, admin_b):
    _auth(api_client, admin_a)
    payload = {
        'leave_type': 'remote',
        'start_date': str(date(2027, 6, 1)),
        'end_date': str(date(2027, 6, 1)),
        'comment': 'Repro',
        'assigned_reviewer': admin_b.id,
    }
    r = api_client.post(LEAVES_URL, payload, format='json')
    assert r.status_code == status.HTTP_201_CREATED, r.content
    return r.json()['id']


@pytest.mark.django_db
class TestReproduceBugReport:
    def test_after_deactivation_leave_still_in_list(
        self, api_client, admin_a, admin_b, company, superadmin,
    ):
        leave_id = _create_leave_via_api(api_client, admin_a, admin_b)

        # superadmin deactivates B
        api_client.force_authenticate(user=superadmin)
        r = api_client.post(f'/api/v1/companies/{company.id}/members/{admin_b.id}/deactivate/')
        assert r.status_code == status.HTTP_200_OK, r.content

        # admin A views list
        _auth(api_client, admin_a)
        list_r = api_client.get(LEAVES_URL)
        assert list_r.status_code == status.HTTP_200_OK
        body = list_r.json()
        ids = [row['id'] for row in body['results']]
        assert leave_id in ids, f'leave {leave_id} missing from {body}'

    def test_after_removal_leave_still_in_list(
        self, api_client, admin_a, admin_b, company, superadmin,
    ):
        leave_id = _create_leave_via_api(api_client, admin_a, admin_b)

        # superadmin removes B from company
        api_client.force_authenticate(user=superadmin)
        r = api_client.delete(f'/api/v1/companies/{company.id}/members/{admin_b.id}/')
        assert r.status_code == status.HTTP_200_OK, r.content

        # admin A views list
        _auth(api_client, admin_a)
        list_r = api_client.get(LEAVES_URL)
        assert list_r.status_code == status.HTTP_200_OK
        body = list_r.json()
        ids = [row['id'] for row in body['results']]
        assert leave_id in ids, f'leave {leave_id} missing from {body}'

        # Also confirm the row is still in DB.
        assert LeaveRequest.objects.filter(pk=leave_id).exists()
