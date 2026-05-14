"""
Tests for DEV-113 AC#3 — NotificationPreference email field defaults.

Verifies the opt-out vs opt-in design:
  Opt-out (default=True)  : booking_confirmed, task_assigned, invitation,
                            guest_validated, leave_review
  Opt-in  (default=False) : task_moved, task_comment, task_deadline,
                            service_request_update, announcement
"""

import pytest
from apps.notifications.models import NotificationPreference


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def make_user(db, django_user_model):
    def _make(email, role='employee', company=None):
        return django_user_model.objects.create_user(
            email=email,
            password='testpass123',
            first_name='Test',
            last_name='User',
            role=role,
            company=company,
        )
    return _make


@pytest.fixture
def make_company(db):
    from apps.companies.models import Company

    def _make(name='Test Co'):
        return Company.objects.create(name=name)
    return _make


@pytest.fixture
def company(make_company):
    return make_company()


@pytest.fixture
def employee(make_user, company):
    return make_user('ac3_employee@test.com', role='employee', company=company)


@pytest.fixture
def company_admin(make_user, company):
    return make_user('ac3_admin@test.com', role='company_admin', company=company)


# ---------------------------------------------------------------------------
# AC3: Default values on new NotificationPreference records
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTransactionalEmailDefaultsTrue:
    """Core transactional email fields must default to True (opt-out design)."""

    def test_booking_confirmed_email_defaults_true(self, employee):
        pref = NotificationPreference.objects.create(user=employee)
        assert pref.booking_confirmed_email is True

    def test_task_assigned_email_defaults_true(self, employee):
        pref = NotificationPreference.objects.create(user=employee)
        assert pref.task_assigned_email is True

    def test_invitation_email_defaults_true(self, company_admin):
        pref = NotificationPreference.objects.create(user=company_admin)
        assert pref.invitation_email is True

    def test_guest_validated_email_defaults_true(self, company_admin):
        pref = NotificationPreference.objects.create(user=company_admin)
        assert pref.guest_validated_email is True

    def test_leave_review_email_defaults_true(self, company_admin):
        pref = NotificationPreference.objects.create(user=company_admin)
        assert pref.leave_review_email is True


@pytest.mark.django_db
class TestInformationalEmailDefaultsFalse:
    """Informational/bulk email fields must default to False (opt-in design)."""

    def test_task_moved_email_defaults_false(self, employee):
        pref = NotificationPreference.objects.create(user=employee)
        assert pref.task_moved_email is False

    def test_task_comment_email_defaults_false(self, employee):
        pref = NotificationPreference.objects.create(user=employee)
        assert pref.task_comment_email is False

    def test_task_deadline_email_defaults_false(self, employee):
        pref = NotificationPreference.objects.create(user=employee)
        assert pref.task_deadline_email is False

    def test_service_request_update_email_defaults_false(self, employee):
        pref = NotificationPreference.objects.create(user=employee)
        assert pref.service_request_update_email is False

    def test_announcement_email_defaults_true(self, employee):
        # announcement_email was set to default=True in migration 0009 to match
        # the product spec for company-wide announcements.
        pref = NotificationPreference.objects.create(user=employee)
        assert pref.announcement_email is True


@pytest.mark.django_db
class TestGetOrCreateUsesCorrectDefaults:
    """get_or_create (used by views/utils) must also produce correct defaults."""

    def test_get_or_create_booking_confirmed_email_true(self, employee):
        pref, created = NotificationPreference.objects.get_or_create(user=employee)
        assert created is True
        assert pref.booking_confirmed_email is True

    def test_get_or_create_task_assigned_email_true(self, employee):
        pref, created = NotificationPreference.objects.get_or_create(user=employee)
        assert created is True
        assert pref.task_assigned_email is True

    def test_get_or_create_invitation_email_true(self, company_admin):
        pref, created = NotificationPreference.objects.get_or_create(user=company_admin)
        assert created is True
        assert pref.invitation_email is True

    def test_get_or_create_guest_validated_email_true(self, company_admin):
        pref, created = NotificationPreference.objects.get_or_create(user=company_admin)
        assert created is True
        assert pref.guest_validated_email is True

    def test_get_or_create_leave_review_email_true(self, company_admin):
        pref, created = NotificationPreference.objects.get_or_create(user=company_admin)
        assert created is True
        assert pref.leave_review_email is True

    def test_get_or_create_task_moved_email_false(self, employee):
        pref, created = NotificationPreference.objects.get_or_create(user=employee)
        assert created is True
        assert pref.task_moved_email is False

    def test_get_or_create_task_comment_email_false(self, employee):
        pref, created = NotificationPreference.objects.get_or_create(user=employee)
        assert created is True
        assert pref.task_comment_email is False
