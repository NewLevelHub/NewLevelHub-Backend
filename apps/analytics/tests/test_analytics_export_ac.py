"""Acceptance tests for analytics CSV export (superadmin + company admin).

AC:
- GET /api/v1/analytics/superadmin/export/?format=csv&period=30d — CSV superadmin
- GET /api/v1/analytics/company/export/?format=csv — CSV company admin
- Content-Type: text/csv; Content-Disposition: attachment; UTF-8 BOM
- Only superadmin / company admin paths per endpoint; others — 403
"""

import csv
import io

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.users.models import User

SUPERADMIN_EXPORT_URL = '/api/v1/analytics/superadmin/export/'
COMPANY_EXPORT_URL = '/api/v1/analytics/company/export/'


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='Export Co', plan='basic')


@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email='sa-export@test.local',
        password='pass',
        first_name='S',
        last_name='A',
        role='superadmin',
    )


@pytest.fixture
def company_admin(db, company):
    return User.objects.create_user(
        email='ca-export@test.local',
        password='pass',
        first_name='C',
        last_name='A',
        role='company_admin',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='emp-export@test.local',
        password='pass',
        first_name='E',
        last_name='M',
        role='employee',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def guest_user(db, company):
    return User.objects.create_user(
        email='guest-export@test.local',
        password='pass',
        first_name='G',
        last_name='U',
        role='guest',
        company=company,
        is_email_verified=True,
    )


def _assert_csv_attachment_response(response):
    assert response.status_code == status.HTTP_200_OK
    content_type = response['Content-Type']
    assert 'text/csv' in content_type
    disposition = response['Content-Disposition']
    assert 'attachment' in disposition
    assert response.content.startswith(b'\xef\xbb\xbf')


def _decode_csv_body(response):
    text = response.content.decode('utf-8-sig')
    return text


@pytest.mark.django_db
class TestSuperadminAnalyticsExportAC:
    def test_superadmin_csv_export_ok(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(
            SUPERADMIN_EXPORT_URL,
            {'format': 'csv', 'period': '30d'},
        )
        _assert_csv_attachment_response(response)
        text = _decode_csv_body(response)
        assert text.strip()
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        assert len(rows) >= 2
        header = rows[0]
        assert 'period' in header
        assert 'total_companies' in header
        data_row = rows[1]
        assert data_row[header.index('period')] == '30d'

    def test_company_admin_forbidden(self, api_client, company_admin):
        api_client.force_authenticate(user=company_admin)
        response = api_client.get(SUPERADMIN_EXPORT_URL, {'format': 'csv', 'period': '30d'})
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_employee_forbidden(self, api_client, employee):
        api_client.force_authenticate(user=employee)
        response = api_client.get(SUPERADMIN_EXPORT_URL, {'format': 'csv', 'period': '30d'})
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_guest_forbidden(self, api_client, guest_user):
        api_client.force_authenticate(user=guest_user)
        response = api_client.get(SUPERADMIN_EXPORT_URL, {'format': 'csv', 'period': '30d'})
        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestCompanyAnalyticsExportAC:
    def test_company_admin_csv_export_ok(self, api_client, company_admin):
        api_client.force_authenticate(user=company_admin)
        response = api_client.get(COMPANY_EXPORT_URL, {'format': 'csv'})
        _assert_csv_attachment_response(response)
        text = _decode_csv_body(response)
        assert text.strip()
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        assert len(rows) >= 2
        assert 'total_employees' in rows[0]
        assert 'guest_visits_month' in rows[0]

    def test_superadmin_csv_export_ok(self, api_client, superadmin, company):
        """Same access model as GET /analytics/company/ (IsCompanyAdmin includes superadmin)."""
        superadmin.company = company
        superadmin.save(update_fields=['company'])
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(COMPANY_EXPORT_URL, {'format': 'csv'})
        _assert_csv_attachment_response(response)

    def test_employee_forbidden(self, api_client, employee):
        api_client.force_authenticate(user=employee)
        response = api_client.get(COMPANY_EXPORT_URL, {'format': 'csv'})
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_guest_forbidden(self, api_client, guest_user):
        api_client.force_authenticate(user=guest_user)
        response = api_client.get(COMPANY_EXPORT_URL, {'format': 'csv'})
        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestAnalyticsExportFormatValidation:
    def test_superadmin_requires_csv_format(self, api_client, superadmin):
        api_client.force_authenticate(user=superadmin)
        r = api_client.get(SUPERADMIN_EXPORT_URL, {'period': '30d'})
        assert r.status_code == status.HTTP_400_BAD_REQUEST

        r2 = api_client.get(SUPERADMIN_EXPORT_URL, {'format': 'pdf', 'period': '30d'})
        assert r2.status_code == status.HTTP_400_BAD_REQUEST

    def test_company_requires_csv_format(self, api_client, company_admin):
        api_client.force_authenticate(user=company_admin)
        r = api_client.get(COMPANY_EXPORT_URL)
        assert r.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestAnalyticsExportVsDashboardConsistency:
    """Export reuses the same figures as JSON dashboards (DEV-115 / DEV-117)."""

    def test_superadmin_csv_numbers_match_dashboard(self, api_client, superadmin):
        # Avoid JWT last_login updates changing active_users_7d between two GETs.
        superadmin.last_login = timezone.now()
        superadmin.save(update_fields=['last_login'])
        api_client.force_authenticate(user=superadmin)
        dash = api_client.get('/api/v1/analytics/superadmin/', {'period': '30d'})
        assert dash.status_code == status.HTTP_200_OK
        csv_resp = api_client.get(SUPERADMIN_EXPORT_URL, {'format': 'csv', 'period': '30d'})
        assert csv_resp.status_code == status.HTTP_200_OK
        text = _decode_csv_body(csv_resp)
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        assert len(rows) == 1
        row = rows[0]
        ov = dash.json()['overview']
        assert int(row['total_companies']) == ov['total_companies']
        assert int(row['active_users_7d']) == ov['active_users_7d']

    def test_company_csv_numbers_match_dashboard(self, api_client, company_admin):
        api_client.force_authenticate(user=company_admin)
        dash = api_client.get('/api/v1/analytics/company/')
        assert dash.status_code == status.HTTP_200_OK
        csv_resp = api_client.get(COMPANY_EXPORT_URL, {'format': 'csv'})
        assert csv_resp.status_code == status.HTTP_200_OK
        text = _decode_csv_body(csv_resp)
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        summary_headers = rows[0]
        summary_vals = rows[1]
        summary = dict(zip(summary_headers, summary_vals))
        dj = dash.json()
        assert int(summary['total_employees']) == dj['total_employees']
        assert int(summary['bookings_month']) == dj['bookings_month']
        assert int(summary['guest_visits_month']) == dj['guest_visits_month']
