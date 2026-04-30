"""
Tests for DEV-63: AccessLog view, filters, export, and company_admin isolation.

AC:
  - GET /api/v1/access/logs/ (superadmin) — all logs; fields: guest_pass, invited_by,
    validated_at, validated_by
  - Filters: company_id, date_from/to, search; sort: -validated_at; pagination
  - GET .../logs/export/?date_from=...&date_to=...&format=csv — CSV download
  - company_admin sees only their company's logs
"""
import csv
import io
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.access.models import AccessLog, GuestPass
from apps.companies.models import Company
from apps.users.models import User

LOGS_URL = '/api/v1/access/logs/'
LOGS_EXPORT_URL = '/api/v1/access/logs/export/'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_company(name='Log AC Co'):
    return Company.objects.create(name=name, plan='basic')


def _make_user(email, role, company=None):
    return User.objects.create_user(
        email=email,
        password='pass',
        first_name='Test',
        last_name='User',
        role=role,
        company=company,
        is_email_verified=True,
    )


def _make_pass(creator, company=None, guest_name='Test Guest', guest_email='tg@test.local'):
    now = timezone.now()
    return GuestPass.objects.create(
        created_by=creator,
        company=company or creator.company,
        guest_name=guest_name,
        guest_email=guest_email,
        visit_purpose='AC test',
        status='used',
        usage_type='single',
        times_used=1,
        valid_from=now - timedelta(hours=2),
        valid_until=now + timedelta(days=1),
    )


def _make_log(guest_pass, checked_by, created_at=None):
    log = AccessLog.objects.create(
        guest_pass=guest_pass,
        checked_by=checked_by,
        method='qr',
    )
    if created_at is not None:
        AccessLog.objects.filter(pk=log.pk).update(created_at=created_at)
        log.refresh_from_db()
    return log


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company_a(db):
    return _make_company('Log AC Company A')


@pytest.fixture
def company_b(db):
    return _make_company('Log AC Company B')


@pytest.fixture
def superadmin(db):
    return _make_user('log-superadmin@test.local', 'superadmin')


@pytest.fixture
def admin_a(db, company_a):
    return _make_user('log-admin-a@test.local', 'company_admin', company_a)


@pytest.fixture
def admin_b(db, company_b):
    return _make_user('log-admin-b@test.local', 'company_admin', company_b)


@pytest.fixture
def employee_a(db, company_a):
    return _make_user('log-employee-a@test.local', 'employee', company_a)


@pytest.fixture
def reception_a(db, company_a):
    return _make_user('log-reception-a@test.local', 'reception', company_a)


@pytest.fixture
def guest_user(db):
    return _make_user('log-guest@test.local', 'guest')


@pytest.fixture
def pass_a(db, admin_a):
    return _make_pass(admin_a, guest_name='LogAcGuestAlpha', guest_email='log-ac-guest-alpha@test.local')


@pytest.fixture
def pass_b(db, admin_b):
    return _make_pass(admin_b, guest_name='LogAcGuestBeta', guest_email='log-ac-guest-beta@test.local')


@pytest.fixture
def log_a(db, pass_a, reception_a):
    return _make_log(pass_a, reception_a)


@pytest.fixture
def log_b(db, pass_b, superadmin):
    return _make_log(pass_b, superadmin)


# ===========================================================================
# 1. Access control — who can list logs
# ===========================================================================

@pytest.mark.django_db
class TestAccessLogPermissionsAC:
    def test_superadmin_can_list_logs(self, api_client, superadmin, log_a):
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(LOGS_URL)
        assert response.status_code == status.HTTP_200_OK

    def test_company_admin_can_list_logs(self, api_client, admin_a, log_a):
        api_client.force_authenticate(user=admin_a)
        response = api_client.get(LOGS_URL)
        assert response.status_code == status.HTTP_200_OK

    def test_employee_gets_403(self, api_client, employee_a, log_a):
        api_client.force_authenticate(user=employee_a)
        response = api_client.get(LOGS_URL)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_guest_gets_403(self, api_client, guest_user, log_a):
        api_client.force_authenticate(user=guest_user)
        response = api_client.get(LOGS_URL)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_unauthenticated_gets_401(self, api_client, log_a):
        response = api_client.get(LOGS_URL)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


# ===========================================================================
# 2. Response fields (AC: guest_pass, invited_by, validated_at, validated_by)
# ===========================================================================

@pytest.mark.django_db
class TestAccessLogFieldsAC:
    def test_list_response_contains_required_ac_fields(self, api_client, superadmin, log_a, pass_a, admin_a, reception_a):
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(LOGS_URL)
        assert response.status_code == status.HTTP_200_OK

        results = response.data.get('results', response.data)
        log_data = next(r for r in results if r['id'] == log_a.id)

        assert 'guest_pass' in log_data
        assert 'invited_by' in log_data
        assert 'validated_at' in log_data
        assert 'validated_by' in log_data

    def test_invited_by_is_pass_creator_full_name(self, api_client, superadmin, log_a, admin_a):
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(LOGS_URL)
        results = response.data.get('results', response.data)
        log_data = next(r for r in results if r['id'] == log_a.id)
        assert log_data['invited_by'] == admin_a.full_name

    def test_validated_by_is_checked_by_full_name(self, api_client, superadmin, log_a, reception_a):
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(LOGS_URL)
        results = response.data.get('results', response.data)
        log_data = next(r for r in results if r['id'] == log_a.id)
        assert log_data['validated_by'] == reception_a.full_name

    def test_validated_at_matches_created_at(self, api_client, superadmin, log_a):
        from django.utils.dateparse import parse_datetime
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(LOGS_URL)
        results = response.data.get('results', response.data)
        log_data = next(r for r in results if r['id'] == log_a.id)
        assert log_data['validated_at'] is not None
        log_a.refresh_from_db()
        # Compare as timezone-aware datetimes regardless of format
        parsed = parse_datetime(log_data['validated_at'])
        assert abs((parsed - log_a.created_at).total_seconds()) < 1


# ===========================================================================
# 3. Company isolation
# ===========================================================================

@pytest.mark.django_db
class TestAccessLogCompanyIsolationAC:
    def test_superadmin_sees_all_companies_logs(self, api_client, superadmin, log_a, log_b):
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(LOGS_URL)
        assert response.status_code == status.HTTP_200_OK
        ids = {r['id'] for r in response.data.get('results', response.data)}
        assert log_a.id in ids
        assert log_b.id in ids

    def test_company_admin_sees_only_own_company_logs(self, api_client, admin_a, log_a, log_b):
        api_client.force_authenticate(user=admin_a)
        response = api_client.get(LOGS_URL)
        assert response.status_code == status.HTTP_200_OK
        ids = {r['id'] for r in response.data.get('results', response.data)}
        assert log_a.id in ids
        assert log_b.id not in ids

    def test_company_admin_does_not_see_other_company_logs(self, api_client, admin_b, log_a, log_b):
        api_client.force_authenticate(user=admin_b)
        response = api_client.get(LOGS_URL)
        assert response.status_code == status.HTTP_200_OK
        ids = {r['id'] for r in response.data.get('results', response.data)}
        assert log_b.id in ids
        assert log_a.id not in ids


# ===========================================================================
# 4. Filters
# ===========================================================================

@pytest.mark.django_db
class TestAccessLogFiltersAC:
    def test_filter_by_company_id_returns_only_that_company(self, api_client, superadmin, log_a, log_b, company_a):
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(LOGS_URL, {'company_id': company_a.id})
        assert response.status_code == status.HTTP_200_OK
        ids = {r['id'] for r in response.data.get('results', response.data)}
        assert log_a.id in ids
        assert log_b.id not in ids

    def test_filter_by_date_from_excludes_earlier_logs(self, api_client, superadmin, admin_a, reception_a, pass_a):
        yesterday = timezone.now() - timedelta(days=1)
        today = timezone.now()

        old_log = _make_log(pass_a, reception_a, created_at=yesterday)
        new_log = _make_log(pass_a, reception_a, created_at=today)

        api_client.force_authenticate(user=superadmin)
        date_from = today.date().isoformat()
        response = api_client.get(LOGS_URL, {'date_from': date_from})
        assert response.status_code == status.HTTP_200_OK
        ids = {r['id'] for r in response.data.get('results', response.data)}
        assert new_log.id in ids
        assert old_log.id not in ids

    def test_filter_by_date_to_excludes_later_logs(self, api_client, superadmin, admin_a, reception_a, pass_a):
        yesterday = timezone.now() - timedelta(days=1)
        today = timezone.now()

        old_log = _make_log(pass_a, reception_a, created_at=yesterday)
        new_log = _make_log(pass_a, reception_a, created_at=today)

        api_client.force_authenticate(user=superadmin)
        date_to = yesterday.date().isoformat()
        response = api_client.get(LOGS_URL, {'date_to': date_to})
        assert response.status_code == status.HTTP_200_OK
        ids = {r['id'] for r in response.data.get('results', response.data)}
        assert old_log.id in ids
        assert new_log.id not in ids

    def test_search_by_guest_name_returns_matching_log(self, api_client, superadmin, log_a, log_b, pass_a):
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(LOGS_URL, {'search': 'LogAcGuestAlpha'})
        assert response.status_code == status.HTTP_200_OK
        ids = {r['id'] for r in response.data.get('results', response.data)}
        assert log_a.id in ids
        assert log_b.id not in ids

    def test_search_by_guest_email_returns_matching_log(self, api_client, superadmin, log_a, log_b):
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(LOGS_URL, {'search': 'log-ac-guest-alpha@test.local'})
        assert response.status_code == status.HTTP_200_OK
        ids = {r['id'] for r in response.data.get('results', response.data)}
        assert log_a.id in ids
        assert log_b.id not in ids


# ===========================================================================
# 5. Ordering & Pagination
# ===========================================================================

@pytest.mark.django_db
class TestAccessLogOrderingAndPaginationAC:
    def test_list_default_ordering_is_validated_at_desc(self, api_client, superadmin, admin_a, reception_a, pass_a):
        now = timezone.now()
        log_older = _make_log(pass_a, reception_a, created_at=now - timedelta(hours=2))
        log_newer = _make_log(pass_a, reception_a, created_at=now - timedelta(hours=1))

        api_client.force_authenticate(user=superadmin)
        response = api_client.get(LOGS_URL)
        assert response.status_code == status.HTTP_200_OK
        results = response.data.get('results', response.data)
        ids = [r['id'] for r in results]
        assert ids.index(log_newer.id) < ids.index(log_older.id)

    def test_list_returns_paginated_envelope(self, api_client, superadmin, log_a):
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(LOGS_URL)
        assert response.status_code == status.HTTP_200_OK
        assert 'count' in response.data
        assert 'results' in response.data


# ===========================================================================
# 6. CSV Export
# ===========================================================================

@pytest.mark.django_db
class TestAccessLogExportAC:
    def test_superadmin_export_csv_returns_200(self, api_client, superadmin, log_a):
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(LOGS_EXPORT_URL)
        assert response.status_code == status.HTTP_200_OK

    def test_export_response_content_type_is_csv(self, api_client, superadmin, log_a):
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(LOGS_EXPORT_URL)
        assert response.status_code == status.HTTP_200_OK
        assert 'text/csv' in response['Content-Type']

    def test_export_has_content_disposition_attachment(self, api_client, superadmin, log_a):
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(LOGS_EXPORT_URL)
        assert 'Content-Disposition' in response
        assert 'attachment' in response['Content-Disposition']

    def test_export_csv_has_required_columns(self, api_client, superadmin, log_a):
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(LOGS_EXPORT_URL)
        content = response.content.decode('utf-8')
        reader = csv.DictReader(io.StringIO(content))
        headers = reader.fieldnames or []
        for col in ('guest_pass', 'invited_by', 'validated_at', 'validated_by'):
            assert col in headers, f'Missing column: {col}'

    def test_export_csv_contains_log_data(self, api_client, superadmin, log_a, pass_a, admin_a, reception_a):
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(LOGS_EXPORT_URL)
        content = response.content.decode('utf-8')
        reader = csv.DictReader(io.StringIO(content))
        rows = list(reader)
        assert any(str(pass_a.id) in row.get('guest_pass', '') for row in rows)

    def test_export_filtered_by_date_from(self, api_client, superadmin, admin_a, reception_a, pass_a):
        now = timezone.now()
        old_log = _make_log(pass_a, reception_a, created_at=now - timedelta(days=5))
        new_log = _make_log(pass_a, reception_a, created_at=now)

        api_client.force_authenticate(user=superadmin)
        date_from = (now - timedelta(days=1)).date().isoformat()
        response = api_client.get(LOGS_EXPORT_URL, {'date_from': date_from})
        assert response.status_code == status.HTTP_200_OK
        content = response.content.decode('utf-8')
        assert str(new_log.id) in content
        assert str(old_log.id) not in content

    def test_export_filtered_by_date_to(self, api_client, superadmin, admin_a, reception_a, pass_a):
        now = timezone.now()
        old_log = _make_log(pass_a, reception_a, created_at=now - timedelta(days=5))
        new_log = _make_log(pass_a, reception_a, created_at=now)

        api_client.force_authenticate(user=superadmin)
        date_to = (now - timedelta(days=2)).date().isoformat()
        response = api_client.get(LOGS_EXPORT_URL, {'date_to': date_to})
        assert response.status_code == status.HTTP_200_OK
        content = response.content.decode('utf-8')
        assert str(old_log.id) in content
        assert str(new_log.id) not in content

    def test_company_admin_export_only_own_company(self, api_client, admin_a, log_a, log_b):
        api_client.force_authenticate(user=admin_a)
        response = api_client.get(LOGS_EXPORT_URL)
        assert response.status_code == status.HTTP_200_OK
        content = response.content.decode('utf-8')
        assert str(log_a.id) in content
        assert str(log_b.id) not in content

    def test_employee_export_gets_403(self, api_client, employee_a):
        api_client.force_authenticate(user=employee_a)
        response = api_client.get(LOGS_EXPORT_URL)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_unauthenticated_export_gets_401(self, api_client):
        response = api_client.get(LOGS_EXPORT_URL)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
