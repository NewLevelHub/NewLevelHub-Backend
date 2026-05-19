"""
Tests for GET /api/v1/access/logs/export/ CSV export endpoint.

Covers:
  1. Unauthenticated → 401
  2. Authenticated → 200 with text/csv content type
  3. Response body starts with UTF-8 BOM (\\xef\\xbb\\xbf)
  4. First row is Russian header row
  5. Log entry data appears in CSV
  6. AccessLog with guest_pass=None produces empty fields for cols 1-3
  7. date_from filter — only entries on/after date_from appear
  8. date_to filter — only entries on/before date_to appear
  9. company_id param ignored for non-superadmin
  10. Superadmin can filter by company_id
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

EXPORT_URL = '/api/v1/access/logs/export/'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_company(name):
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


def _make_pass(creator, company=None, guest_name='Export Guest', guest_email='export-guest@test.local'):
    now = timezone.now()
    return GuestPass.objects.create(
        created_by=creator,
        company=company or creator.company,
        guest_name=guest_name,
        guest_email=guest_email,
        visit_purpose='Export test',
        status='used',
        usage_type='single',
        times_used=1,
        valid_from=now - timedelta(hours=2),
        valid_until=now + timedelta(days=1),
    )


def _make_log(guest_pass, checked_by=None, created_at=None, method='qr'):
    log = AccessLog.objects.create(
        guest_pass=guest_pass,
        checked_by=checked_by,
        method=method,
    )
    if created_at is not None:
        AccessLog.objects.filter(pk=log.pk).update(created_at=created_at)
        log.refresh_from_db()
    return log


def _get_response_bytes(response):
    """Return the full body bytes from a regular or streaming response."""
    if hasattr(response, 'streaming_content'):
        return b''.join(response.streaming_content)
    return response.content


def _parse_csv(response):
    """Decode response body (stripping BOM) and return list of row dicts."""
    content = _get_response_bytes(response).decode('utf-8-sig')
    return list(csv.DictReader(io.StringIO(content)))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company_a(db):
    return _make_company('Export CSV Company A')


@pytest.fixture
def company_b(db):
    return _make_company('Export CSV Company B')


@pytest.fixture
def superadmin(db):
    return _make_user('export-superadmin@test.local', 'superadmin')


@pytest.fixture
def admin_a(db, company_a):
    return _make_user('export-admin-a@test.local', 'company_admin', company_a)


@pytest.fixture
def admin_b(db, company_b):
    return _make_user('export-admin-b@test.local', 'company_admin', company_b)


@pytest.fixture
def employee_a(db, company_a):
    return _make_user('export-employee-a@test.local', 'employee', company_a)


@pytest.fixture
def guest_user(db):
    return _make_user('export-guest-user@test.local', 'guest')


@pytest.fixture
def pass_a(db, admin_a, company_a):
    return _make_pass(admin_a, company=company_a,
                      guest_name='ExportGuestAlpha', guest_email='export-alpha@test.local')


@pytest.fixture
def pass_b(db, admin_b, company_b):
    return _make_pass(admin_b, company=company_b,
                      guest_name='ExportGuestBeta', guest_email='export-beta@test.local')


@pytest.fixture
def log_a(db, pass_a, admin_a):
    return _make_log(pass_a, checked_by=admin_a)


@pytest.fixture
def log_b(db, pass_b, admin_b):
    return _make_log(pass_b, checked_by=admin_b)


# ---------------------------------------------------------------------------
# 1. Unauthenticated → 401
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_unauthenticated_returns_401(api_client):
    response = api_client.get(EXPORT_URL)
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


# ---------------------------------------------------------------------------
# 2. Authenticated → 200 with text/csv content type
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_authenticated_returns_csv_content_type(api_client, superadmin, log_a):
    api_client.force_authenticate(user=superadmin)
    response = api_client.get(EXPORT_URL)
    assert response.status_code == status.HTTP_200_OK
    assert 'text/csv' in response['Content-Type']


# ---------------------------------------------------------------------------
# 3. Response body starts with UTF-8 BOM
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_csv_has_bom(api_client, superadmin, log_a):
    api_client.force_authenticate(user=superadmin)
    response = api_client.get(EXPORT_URL)
    assert response.status_code == status.HTTP_200_OK
    # BOM bytes: EF BB BF
    body = _get_response_bytes(response)
    assert body[:3] == b'\xef\xbb\xbf'


# ---------------------------------------------------------------------------
# 4. First data row is the Russian header row
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_csv_headers_ru(api_client, superadmin, log_a):
    api_client.force_authenticate(user=superadmin)
    response = api_client.get(EXPORT_URL)
    assert response.status_code == status.HTTP_200_OK
    content = _get_response_bytes(response).decode('utf-8-sig')
    reader = csv.reader(io.StringIO(content))
    first_row = next(reader)
    expected = [
        'Имя гостя',
        'Email гостя',
        'Компания',
        'Пригласил',
        'Проверил',
        'Валидирован',
        'Метод',
    ]
    assert first_row == expected


# ---------------------------------------------------------------------------
# 5. Log entry data appears in CSV
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_csv_contains_log_entry(api_client, superadmin, log_a, pass_a, admin_a, company_a):
    api_client.force_authenticate(user=superadmin)
    response = api_client.get(EXPORT_URL)
    assert response.status_code == status.HTTP_200_OK
    rows = _parse_csv(response)

    assert len(rows) >= 1
    row = rows[0]  # newest first

    assert row['Имя гостя'] == pass_a.guest_name
    assert row['Email гостя'] == pass_a.guest_email
    assert row['Компания'] == company_a.name
    assert row['Пригласил'] == admin_a.full_name
    assert row['Проверил'] == admin_a.full_name
    assert row['Метод'] == 'qr'
    # Validated at must be a date in DD.MM.YYYY HH:MM format
    assert len(row['Валидирован']) == 16
    assert '.' in row['Валидирован']


# ---------------------------------------------------------------------------
# 6. AccessLog with guest_pass=None → empty fields for cols 1-3
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_guest_pass_null_row_has_empty_fields(api_client, superadmin, admin_a):
    # Create a log without a guest_pass
    log = AccessLog.objects.create(guest_pass=None, checked_by=admin_a, method='manual')
    api_client.force_authenticate(user=superadmin)
    response = api_client.get(EXPORT_URL)
    assert response.status_code == status.HTTP_200_OK
    rows = _parse_csv(response)
    null_rows = [r for r in rows if r.get('Метод') == 'manual' and r.get('Имя гостя') == '']
    assert len(null_rows) >= 1
    null_row = null_rows[0]
    assert null_row['Имя гостя'] == ''
    assert null_row['Email гостя'] == ''
    assert null_row['Компания'] == ''
    # Cleanup
    log.delete()


# ---------------------------------------------------------------------------
# 7. date_from filter
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_date_from_filter(api_client, superadmin, pass_a, admin_a):
    now = timezone.now()
    old_log = _make_log(pass_a, checked_by=admin_a, created_at=now - timedelta(days=5))
    new_log = _make_log(pass_a, checked_by=admin_a, created_at=now)

    api_client.force_authenticate(user=superadmin)
    date_from = (now - timedelta(days=1)).date().isoformat()
    response = api_client.get(EXPORT_URL, {'date_from': date_from})
    assert response.status_code == status.HTTP_200_OK

    rows = _parse_csv(response)
    validated_ats = [r.get('Валидирован', '') for r in rows]

    assert any(new_log.created_at.strftime('%d.%m.%Y') in v for v in validated_ats)
    assert not any(old_log.created_at.strftime('%d.%m.%Y') in v for v in validated_ats)


# ---------------------------------------------------------------------------
# 8. date_to filter
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_date_to_filter(api_client, superadmin, pass_a, admin_a):
    now = timezone.now()
    old_log = _make_log(pass_a, checked_by=admin_a, created_at=now - timedelta(days=5))
    _make_log(pass_a, checked_by=admin_a, created_at=now)

    api_client.force_authenticate(user=superadmin)
    date_to = (now - timedelta(days=2)).date().isoformat()
    response = api_client.get(EXPORT_URL, {'date_to': date_to})
    assert response.status_code == status.HTTP_200_OK

    rows = _parse_csv(response)
    validated_ats = [r.get('Валидирован', '') for r in rows]

    assert any(old_log.created_at.strftime('%d.%m.%Y') in v for v in validated_ats)
    today_fmt = now.strftime('%d.%m.%Y')
    assert not any(today_fmt in v for v in validated_ats)


# ---------------------------------------------------------------------------
# 9. company_id param ignored for non-superadmin
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_company_id_ignored_for_non_superadmin(api_client, admin_a, log_a, log_b, pass_a, pass_b, company_b):
    # admin_a tries to pass company_b's id — should still only see their own data
    api_client.force_authenticate(user=admin_a)
    response = api_client.get(EXPORT_URL, {'company_id': company_b.id})
    assert response.status_code == status.HTTP_200_OK
    rows = _parse_csv(response)
    names = [r.get('Имя гостя', '') for r in rows]
    # Should see company_a guest, not company_b guest
    assert pass_a.guest_name in names
    assert pass_b.guest_name not in names


# ---------------------------------------------------------------------------
# 10. Superadmin can filter by company_id
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_superadmin_can_filter_by_company_id(api_client, superadmin, log_a, log_b, pass_a, pass_b, company_a):
    api_client.force_authenticate(user=superadmin)
    response = api_client.get(EXPORT_URL, {'company_id': company_a.id})
    assert response.status_code == status.HTTP_200_OK
    rows = _parse_csv(response)
    names = [r.get('Имя гостя', '') for r in rows]
    assert pass_a.guest_name in names
    assert pass_b.guest_name not in names
