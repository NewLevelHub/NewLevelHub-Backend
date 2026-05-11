"""
Acceptance-criteria tests for DEV-110:
  Отметка прочитанного и email-рассылка для важных объявлений.

AC items covered:
  1. POST .../announcements/<id>/read/ — creates AnnouncementRead; is_read=true in response
  2. Every announcement in response: is_read (current user), read_count (for author)
  3. is_pinned=true AND notify_email=true → Celery task enqueued (БЦ = all users,
     company = company employees)
"""

from unittest.mock import patch

import pytest
from django.core import mail
from rest_framework import status
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.services.models import Announcement, AnnouncementRead
from apps.users.models import User


ANNOUNCEMENTS_URL = '/api/v1/services/announcements/'


def read_url(announcement_id):
    return f'/api/v1/services/announcements/{announcement_id}/read/'


def detail_url(announcement_id):
    return f'/api/v1/services/announcements/{announcement_id}/'


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def company(db):
    return Company.objects.create(name='ReadAC Corp', plan='basic')


@pytest.fixture
def other_company(db):
    return Company.objects.create(name='ReadAC Other', plan='basic')


@pytest.fixture
def superadmin(db):
    return User.objects.create_user(
        email='read-superadmin@test.local',
        password='pass',
        first_name='Read',
        last_name='SuperAdmin',
        role='superadmin',
        is_email_verified=True,
    )


@pytest.fixture
def company_admin(db, company):
    return User.objects.create_user(
        email='read-admin@test.local',
        password='pass',
        first_name='Read',
        last_name='Admin',
        role='company_admin',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def employee(db, company):
    return User.objects.create_user(
        email='read-employee@test.local',
        password='pass',
        first_name='Read',
        last_name='Employee',
        role='employee',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def employee2(db, company):
    return User.objects.create_user(
        email='read-employee2@test.local',
        password='pass',
        first_name='Read2',
        last_name='Employee2',
        role='employee',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def other_company_employee(db, other_company):
    return User.objects.create_user(
        email='read-other-emp@test.local',
        password='pass',
        first_name='Other',
        last_name='Emp',
        role='employee',
        company=other_company,
        is_email_verified=True,
    )


@pytest.fixture
def guest_user(db):
    return User.objects.create_user(
        email='read-guest@test.local',
        password='pass',
        first_name='Read',
        last_name='Guest',
        role='guest',
        is_email_verified=True,
    )


def _make_announcement(*, author, company=None, title='Test Announcement',
                       is_pinned=False, notify_email=False):
    return Announcement.objects.create(
        title=title,
        body='Body text.',
        category='info',
        is_pinned=is_pinned,
        notify_email=notify_email,
        author=author,
        company=company,
        scope='company' if company else 'building',
    )


# ---------------------------------------------------------------------------
# AC #1 — POST .../announcements/<id>/read/
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestMarkReadAC:
    """AC: POST .../announcements/<id>/read/ — creates AnnouncementRead; is_read=true in response."""

    def test_unauthenticated_mark_read_returns_401(self, api_client, superadmin):
        announcement = _make_announcement(author=superadmin)
        response = api_client.post(read_url(announcement.id))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_mark_read_creates_announcement_read_record(self, api_client, employee, superadmin):
        announcement = _make_announcement(author=superadmin)
        api_client.force_authenticate(user=employee)
        response = api_client.post(read_url(announcement.id))
        assert response.status_code == status.HTTP_200_OK
        assert AnnouncementRead.objects.filter(
            announcement=announcement, user=employee
        ).exists()

    def test_mark_read_response_contains_is_read_true(self, api_client, employee, superadmin):
        announcement = _make_announcement(author=superadmin)
        api_client.force_authenticate(user=employee)
        response = api_client.post(read_url(announcement.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data.get('is_read') is True

    def test_mark_read_response_contains_announcement_fields(self, api_client, employee, superadmin):
        """Response must include announcement data (title, text, etc.), not just a detail message."""
        announcement = _make_announcement(author=superadmin, title='Important Notice')
        api_client.force_authenticate(user=employee)
        response = api_client.post(read_url(announcement.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data.get('id') == announcement.id
        assert response.data.get('title') == 'Important Notice'

    def test_mark_read_is_idempotent(self, api_client, employee, superadmin):
        """Calling mark_read twice must not error or create duplicate records."""
        announcement = _make_announcement(author=superadmin)
        api_client.force_authenticate(user=employee)
        api_client.post(read_url(announcement.id))
        response = api_client.post(read_url(announcement.id))
        assert response.status_code == status.HTTP_200_OK
        assert AnnouncementRead.objects.filter(
            announcement=announcement, user=employee
        ).count() == 1

    def test_mark_read_returns_is_read_true_on_second_call(self, api_client, employee, superadmin):
        announcement = _make_announcement(author=superadmin)
        api_client.force_authenticate(user=employee)
        api_client.post(read_url(announcement.id))
        response = api_client.post(read_url(announcement.id))
        assert response.data.get('is_read') is True

    def test_mark_read_returns_404_for_nonexistent_announcement(self, api_client, employee):
        api_client.force_authenticate(user=employee)
        response = api_client.post(read_url(99999))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_employee_can_mark_building_announcement_read(self, api_client, employee, superadmin):
        announcement = _make_announcement(author=superadmin, company=None)
        api_client.force_authenticate(user=employee)
        response = api_client.post(read_url(announcement.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data.get('is_read') is True

    def test_employee_can_mark_own_company_announcement_read(
        self, api_client, employee, company_admin, company
    ):
        announcement = _make_announcement(author=company_admin, company=company)
        api_client.force_authenticate(user=employee)
        response = api_client.post(read_url(announcement.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data.get('is_read') is True


# ---------------------------------------------------------------------------
# AC #2 — is_read and read_count fields in announcement responses
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestIsReadFieldAC:
    """AC: is_read (для текущего пользователя) присутствует в ответе."""

    def test_list_response_includes_is_read_field(self, api_client, employee, superadmin):
        _make_announcement(author=superadmin)
        api_client.force_authenticate(user=employee)
        response = api_client.get(ANNOUNCEMENTS_URL)
        assert response.status_code == status.HTTP_200_OK
        results = response.data['results']
        assert len(results) >= 1
        assert 'is_read' in results[0]

    def test_detail_response_includes_is_read_field(self, api_client, employee, superadmin):
        announcement = _make_announcement(author=superadmin)
        api_client.force_authenticate(user=employee)
        response = api_client.get(detail_url(announcement.id))
        assert response.status_code == status.HTTP_200_OK
        assert 'is_read' in response.data

    def test_is_read_false_before_marking(self, api_client, employee, superadmin):
        announcement = _make_announcement(author=superadmin)
        api_client.force_authenticate(user=employee)
        response = api_client.get(detail_url(announcement.id))
        assert response.data.get('is_read') is False

    def test_is_read_true_after_marking(self, api_client, employee, superadmin):
        announcement = _make_announcement(author=superadmin)
        api_client.force_authenticate(user=employee)
        api_client.post(read_url(announcement.id))
        response = api_client.get(detail_url(announcement.id))
        assert response.data.get('is_read') is True

    def test_is_read_is_per_user(self, api_client, employee, employee2, superadmin):
        """is_read for one user must not affect another user's is_read."""
        announcement = _make_announcement(author=superadmin)
        api_client.force_authenticate(user=employee)
        api_client.post(read_url(announcement.id))

        api_client.force_authenticate(user=employee2)
        response = api_client.get(detail_url(announcement.id))
        assert response.data.get('is_read') is False

    def test_is_read_list_reflects_current_user(self, api_client, employee, employee2, superadmin):
        announcement = _make_announcement(author=superadmin)
        AnnouncementRead.objects.create(announcement=announcement, user=employee)

        api_client.force_authenticate(user=employee)
        r1 = api_client.get(ANNOUNCEMENTS_URL)
        item1 = next(r for r in r1.data['results'] if r['id'] == announcement.id)
        assert item1['is_read'] is True

        api_client.force_authenticate(user=employee2)
        r2 = api_client.get(ANNOUNCEMENTS_URL)
        item2 = next(r for r in r2.data['results'] if r['id'] == announcement.id)
        assert item2['is_read'] is False


@pytest.mark.django_db
class TestReadCountFieldAC:
    """AC: read_count (для автора) присутствует в ответе."""

    def test_list_response_includes_read_count_field(self, api_client, superadmin):
        _make_announcement(author=superadmin)
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(ANNOUNCEMENTS_URL)
        assert response.status_code == status.HTTP_200_OK
        results = response.data['results']
        assert len(results) >= 1
        assert 'read_count' in results[0]

    def test_detail_response_includes_read_count_field(self, api_client, superadmin):
        announcement = _make_announcement(author=superadmin)
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(detail_url(announcement.id))
        assert response.status_code == status.HTTP_200_OK
        assert 'read_count' in response.data

    def test_read_count_is_zero_for_new_announcement(self, api_client, superadmin):
        announcement = _make_announcement(author=superadmin)
        api_client.force_authenticate(user=superadmin)
        response = api_client.get(detail_url(announcement.id))
        assert response.data.get('read_count') == 0

    def test_read_count_increments_when_user_reads(self, api_client, employee, superadmin):
        announcement = _make_announcement(author=superadmin)
        api_client.force_authenticate(user=employee)
        api_client.post(read_url(announcement.id))

        api_client.force_authenticate(user=superadmin)
        response = api_client.get(detail_url(announcement.id))
        assert response.data.get('read_count') == 1

    def test_read_count_counts_unique_users(
        self, api_client, employee, employee2, superadmin
    ):
        announcement = _make_announcement(author=superadmin)
        AnnouncementRead.objects.create(announcement=announcement, user=employee)
        AnnouncementRead.objects.create(announcement=announcement, user=employee2)

        api_client.force_authenticate(user=superadmin)
        response = api_client.get(detail_url(announcement.id))
        assert response.data.get('read_count') == 2

    def test_read_count_not_incremented_by_duplicate_mark(
        self, api_client, employee, superadmin
    ):
        """Idempotent mark_read must not inflate read_count."""
        announcement = _make_announcement(author=superadmin)
        api_client.force_authenticate(user=employee)
        api_client.post(read_url(announcement.id))
        api_client.post(read_url(announcement.id))

        api_client.force_authenticate(user=superadmin)
        response = api_client.get(detail_url(announcement.id))
        assert response.data.get('read_count') == 1

    def test_read_count_visible_to_non_author(self, api_client, employee, superadmin, company_admin, company):
        """read_count is visible to all authenticated users, not just the author."""
        announcement = _make_announcement(author=company_admin, company=company)
        AnnouncementRead.objects.create(announcement=announcement, user=employee)

        api_client.force_authenticate(user=employee)
        response = api_client.get(detail_url(announcement.id))
        assert 'read_count' in response.data
        assert response.data['read_count'] == 1


# ---------------------------------------------------------------------------
# AC #3 — Celery email task for is_pinned + notify_email
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestEmailNotificationOnCreateAC:
    """
    AC: если is_pinned=true и notify_email=true — Celery task email всем (БЦ)
    или сотрудникам компании.
    """

    def _post_announcement(self, api_client, user, payload):
        api_client.force_authenticate(user=user)
        return api_client.post(ANNOUNCEMENTS_URL, payload, format='json')

    def test_celery_task_enqueued_when_pinned_and_notify_email_building(
        self, api_client, superadmin
    ):
        """Building-wide pinned+notify_email announcement must enqueue Celery task."""
        with patch('apps.services.tasks.send_announcement_emails.delay') as mock_delay:
            response = self._post_announcement(api_client, superadmin, {
                'title': 'Urgent BC',
                'text': 'Body',
                'category': 'important',
                'is_pinned': True,
                'notify_email': True,
                'company_id': None,
            })
            assert response.status_code == status.HTTP_201_CREATED, response.data
            mock_delay.assert_called_once_with(response.data['id'])

    def test_celery_task_enqueued_when_pinned_and_notify_email_company(
        self, api_client, company_admin, company
    ):
        """Company-scoped pinned+notify_email announcement must enqueue Celery task."""
        with patch('apps.services.tasks.send_announcement_emails.delay') as mock_delay:
            response = self._post_announcement(api_client, company_admin, {
                'title': 'Company Urgent',
                'text': 'Body',
                'category': 'important',
                'is_pinned': True,
                'notify_email': True,
            })
            assert response.status_code == status.HTTP_201_CREATED, response.data
            mock_delay.assert_called_once_with(response.data['id'])

    def test_celery_task_not_enqueued_when_not_pinned(self, api_client, superadmin):
        """notify_email=true but is_pinned=false → task must NOT be enqueued."""
        with patch('apps.services.tasks.send_announcement_emails.delay') as mock_delay:
            response = self._post_announcement(api_client, superadmin, {
                'title': 'Not Pinned',
                'text': 'Body',
                'category': 'info',
                'is_pinned': False,
                'notify_email': True,
                'company_id': None,
            })
            assert response.status_code == status.HTTP_201_CREATED, response.data
            mock_delay.assert_not_called()

    def test_celery_task_not_enqueued_when_notify_email_false(self, api_client, superadmin):
        """is_pinned=true but notify_email=false → task must NOT be enqueued."""
        with patch('apps.services.tasks.send_announcement_emails.delay') as mock_delay:
            response = self._post_announcement(api_client, superadmin, {
                'title': 'Pinned No Email',
                'text': 'Body',
                'category': 'info',
                'is_pinned': True,
                'notify_email': False,
                'company_id': None,
            })
            assert response.status_code == status.HTTP_201_CREATED, response.data
            mock_delay.assert_not_called()

    def test_celery_task_not_enqueued_when_both_false(self, api_client, superadmin):
        with patch('apps.services.tasks.send_announcement_emails.delay') as mock_delay:
            response = self._post_announcement(api_client, superadmin, {
                'title': 'Plain',
                'text': 'Body',
                'category': 'info',
                'is_pinned': False,
                'notify_email': False,
                'company_id': None,
            })
            assert response.status_code == status.HTTP_201_CREATED, response.data
            mock_delay.assert_not_called()


@pytest.mark.django_db
class TestEmailNotificationTaskAC:
    """
    AC: task sends emails — to all users for building-wide,
    to company employees for company-scoped.
    """

    def test_task_sends_email_to_all_users_for_building_announcement(
        self, superadmin, employee, employee2, other_company_employee
    ):
        """Building-wide announcement → emails to all active users."""
        from apps.services.tasks import send_announcement_emails

        announcement = _make_announcement(
            author=superadmin, company=None,
            title='BC Announcement', is_pinned=True, notify_email=True,
        )
        send_announcement_emails(announcement.id)

        # All active verified users should receive an email
        all_user_emails = set(
            User.objects.filter(is_active=True, is_email_verified=True).values_list('email', flat=True)
        )
        sent_to = {msg.to[0] for msg in mail.outbox}
        assert all_user_emails.issubset(sent_to)

    def test_task_sends_email_to_company_members_for_company_announcement(
        self, company_admin, employee, employee2, other_company_employee, company
    ):
        """Company-scoped announcement → emails only to company members."""
        from apps.services.tasks import send_announcement_emails

        announcement = _make_announcement(
            author=company_admin, company=company,
            title='Company Announcement', is_pinned=True, notify_email=True,
        )
        send_announcement_emails(announcement.id)

        sent_to = {msg.to[0] for msg in mail.outbox}
        company_member_emails = set(
            User.objects.filter(company=company, is_active=True).values_list('email', flat=True)
        )
        # All company members should receive emails
        assert company_member_emails.issubset(sent_to)
        # Employee from other company should NOT receive email
        assert other_company_employee.email not in sent_to

    def test_task_email_contains_announcement_title(self, superadmin):
        from apps.services.tasks import send_announcement_emails

        announcement = _make_announcement(
            author=superadmin, company=None,
            title='Special Notice', is_pinned=True, notify_email=True,
        )
        send_announcement_emails(announcement.id)

        assert len(mail.outbox) >= 1
        subjects = [msg.subject for msg in mail.outbox]
        assert any('Special Notice' in subject for subject in subjects)

    def test_task_does_nothing_for_nonexistent_announcement(self):
        from apps.services.tasks import send_announcement_emails

        # Should not raise
        send_announcement_emails(99999)
        assert len(mail.outbox) == 0
