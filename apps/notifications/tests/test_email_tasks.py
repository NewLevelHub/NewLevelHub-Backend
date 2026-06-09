"""
Tests for apps/notifications/tasks.py — email notification Celery tasks.

Coverage:
  - send_notification_email: skips when preference _email=False
  - send_notification_email: sends even when DND is active (DND does not affect email)
  - send_notification_email: calls send_mail with correct args when enabled
  - send_notification_email: falls back to base template when type-specific template missing
  - send_bulk_email: sends to all active company members when notify_email=True
  - send_bulk_email: skips when notify_email=False
  - send_bulk_email: skips when announcement does not exist
"""

from unittest.mock import patch, MagicMock

import pytest

from apps.notifications.tasks import (
    send_notification_email,
    send_bulk_email,
    _check_preference,
    _check_dnd,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def make_user(db, django_user_model):
    import uuid

    def _make(email=None, role='employee', company=None, first_name='Ivan'):
        if email is None:
            email = f'user_{uuid.uuid4().hex[:8]}@example.com'
        user, _ = django_user_model.objects.get_or_create(
            email=email,
            defaults={
                'first_name': first_name,
                'last_name': 'Testov',
                'role': role,
                'company': company,
                'is_email_verified': True,
            },
        )
        # Update fields if user already exists (from a previous run)
        user.role = role
        user.company = company
        user.first_name = first_name
        user.is_email_verified = True
        user.save(update_fields=['role', 'company', 'first_name', 'is_email_verified'])
        return user

    return _make


@pytest.fixture
def make_company(db):
    from apps.companies.models import Company

    def _make(name='Test Corp'):
        return Company.objects.create(name=name)

    return _make


@pytest.fixture
def company(make_company):
    return make_company()


@pytest.fixture
def user(make_user, company):
    return make_user(company=company)


@pytest.fixture
def make_preference(db):
    from apps.notifications.models import NotificationPreference
    from django.db import connection

    def _make(user, **kwargs):
        # Use raw SQL INSERT to avoid issues with extra DB columns not in the model.
        # First delete any existing preference row for this user.
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM notification_preferences WHERE user_id = %s", [user.id])
            # Build INSERT with sensible defaults for all known NOT NULL columns.
            # The model-defined fields:
            model_defaults = {
                'booking_in_app': True, 'booking_email': True,
                'task_in_app': True, 'task_email': False,
                'access_in_app': True, 'access_email': True,
                'service_in_app': True, 'service_email': False,
                'announcement_in_app': True, 'announcement_email': False,
                'hr_in_app': True, 'hr_email': True,
                'do_not_disturb': False,
            }
            # Extra DB-level NOT NULL columns detected in the test DB:
            extra_defaults = {
                'dnd_enabled': False,
                'system_email': False, 'system_in_app': True,
                'booking_cancelled_email': True, 'booking_cancelled_in_app': True,
                'booking_confirmed_email': True, 'booking_confirmed_in_app': True,
                'booking_reminder_email': False, 'booking_reminder_in_app': True,
                'guest_pass_expiring_email': True, 'guest_pass_expiring_in_app': True,
                'guest_validated_email': True, 'guest_validated_in_app': True,
                'invitation_email': True, 'invitation_in_app': True,
                'leave_review_email': True, 'leave_review_in_app': True,
                'new_employee_email': True, 'new_employee_in_app': True,
                'service_request_update_email': False, 'service_request_update_in_app': True,
                'task_assigned_email': True, 'task_assigned_in_app': True,
                'task_comment_email': True, 'task_comment_in_app': True,
                'task_deadline_email': True, 'task_deadline_in_app': True,
                'task_moved_email': True, 'task_moved_in_app': True,
                'new_employee_email': True, 'new_employee_in_app': True,
                'booking_completed_email': False, 'booking_completed_in_app': True,
            }
            all_fields = {**model_defaults, **extra_defaults, **kwargs}
            all_fields['user_id'] = user.id

            columns = ', '.join(all_fields.keys())
            placeholders = ', '.join(['%s'] * len(all_fields))
            values = list(all_fields.values())
            cursor.execute(
                f"INSERT INTO notification_preferences (created_at, updated_at, {columns}) "
                f"VALUES (NOW(), NOW(), {placeholders})",
                values,
            )

        return NotificationPreference.objects.get(user=user)

    return _make


# ---------------------------------------------------------------------------
# Unit tests for _check_preference helper
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCheckPreference:
    def test_returns_true_when_no_prefs_row(self, user):
        """No preference row → default allow."""
        assert _check_preference(user, 'booking_confirmed') is True

    def test_returns_true_when_email_pref_true(self, user, make_preference):
        make_preference(user, booking_email=True)
        assert _check_preference(user, 'booking_confirmed') is True

    def test_returns_false_when_email_pref_false(self, user, make_preference):
        make_preference(user, booking_confirmed_email=False)
        assert _check_preference(user, 'booking_confirmed') is False

    def test_task_email_pref(self, user, make_preference):
        make_preference(user, task_assigned_email=False)
        assert _check_preference(user, 'task_assigned') is False

    def test_access_email_pref(self, user, make_preference):
        make_preference(user, guest_validated_email=False)
        assert _check_preference(user, 'guest_validated') is False

    def test_hr_email_pref(self, user, make_preference):
        make_preference(user, leave_review_email=False)
        assert _check_preference(user, 'leave_review') is False

    def test_system_type_always_true(self, user, make_preference):
        """system notification_type has no pref field → always allowed."""
        make_preference(user, hr_email=False, booking_email=False)
        assert _check_preference(user, 'system') is True


# ---------------------------------------------------------------------------
# Unit tests for _check_dnd helper
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCheckDnd:
    def test_returns_true_when_no_prefs_row(self, user):
        assert _check_dnd(user, 'booking_confirmed') is True

    def test_returns_true_when_dnd_false(self, user, make_preference):
        make_preference(user, do_not_disturb=False)
        assert _check_dnd(user, 'booking_confirmed') is True

    def test_returns_false_when_dnd_true(self, user, make_preference):
        make_preference(user, do_not_disturb=True)
        assert _check_dnd(user, 'booking_confirmed') is False

    def test_returns_false_when_time_based_dnd_active(self, user, make_preference):
        from django.utils import timezone
        from datetime import timedelta

        make_preference(user, dnd_enabled=True, dnd_until=timezone.now() + timedelta(hours=1))
        assert _check_dnd(user, 'task_assigned') is False

    def test_returns_true_when_time_based_dnd_expired(self, user, make_preference):
        from django.utils import timezone
        from datetime import timedelta

        make_preference(user, dnd_enabled=True, dnd_until=timezone.now() - timedelta(minutes=1))
        assert _check_dnd(user, 'task_assigned') is True


# ---------------------------------------------------------------------------
# Integration tests for send_notification_email task
# ---------------------------------------------------------------------------

class TestSendNotificationEmail:
    """
    These tests mock send_mail and render_to_string to avoid real email
    delivery and template rendering during CI.
    """

    @pytest.mark.django_db
    def test_sends_email_when_preferences_allow(self, user, make_preference):
        make_preference(user, booking_email=True, do_not_disturb=False)

        with patch('apps.notifications.tasks.send_mail') as mock_mail, \
             patch('apps.notifications.tasks.render_to_string', return_value='<html>body</html>'):
            send_notification_email(
                user.id,
                'booking_confirmed',
                {
                    'subject': 'Test Subject',
                    'resource_name': 'Desk A',
                    'action_url': '/bookings/1',
                },
            )

        mock_mail.assert_called_once()
        _, kwargs = mock_mail.call_args
        assert kwargs['subject'] == 'Test Subject'
        assert user.email in kwargs['recipient_list']
        assert kwargs.get('html_message') == '<html>body</html>'

    @pytest.mark.django_db
    def test_skips_when_email_pref_false(self, user, make_preference):
        make_preference(user, booking_confirmed_email=False)

        with patch('apps.notifications.tasks.send_mail') as mock_mail:
            send_notification_email(
                user.id,
                'booking_confirmed',
                {'subject': 'Should not send'},
            )

        mock_mail.assert_not_called()

    @pytest.mark.django_db
    def test_skips_when_dnd_active_boolean(self, user, make_preference):
        """DND (do_not_disturb=True) suppresses email delivery."""
        make_preference(user, booking_confirmed_email=True, do_not_disturb=True)

        with patch('apps.notifications.tasks.send_mail') as mock_mail:
            send_notification_email(
                user.id,
                'booking_confirmed',
                {'subject': 'Should be suppressed'},
            )

        mock_mail.assert_not_called()

    @pytest.mark.django_db
    def test_skips_when_time_based_dnd_active(self, user, make_preference):
        """DND with dnd_enabled=True and dnd_until in the future suppresses email (DEV-175)."""
        from django.utils import timezone
        from datetime import timedelta

        make_preference(
            user,
            task_assigned_email=True,
            dnd_enabled=True,
            dnd_until=timezone.now() + timedelta(hours=1),
        )

        with patch('apps.notifications.tasks.send_mail') as mock_mail:
            send_notification_email(
                user.id,
                'task_assigned',
                {'subject': 'Assigned task'},
            )

        mock_mail.assert_not_called()

    @pytest.mark.django_db
    def test_skips_when_email_pref_disabled_regardless_of_dnd(self, user, make_preference):
        """Email is skipped when the email preference is False, even if DND is also active."""
        make_preference(user, booking_confirmed_email=False, do_not_disturb=True)

        with patch('apps.notifications.tasks.send_mail') as mock_mail:
            send_notification_email(
                user.id,
                'booking_confirmed',
                {'subject': 'nope'},
            )

        mock_mail.assert_not_called()

    @pytest.mark.django_db
    def test_skips_when_user_not_found(self):
        with patch('apps.notifications.tasks.send_mail') as mock_mail:
            send_notification_email(999999, 'booking_confirmed', {})

        mock_mail.assert_not_called()

    @pytest.mark.django_db
    def test_uses_default_subject_when_not_in_context(self, user, make_preference):
        make_preference(user, booking_email=True, do_not_disturb=False)

        with patch('apps.notifications.tasks.send_mail') as mock_mail, \
             patch('apps.notifications.tasks.render_to_string', return_value='<html/>'):
            send_notification_email(
                user.id,
                'booking_confirmed',
                {},
            )

        mock_mail.assert_called_once()
        _, call_kwargs = mock_mail.call_args
        assert call_kwargs['subject'] == 'Ваше бронирование подтверждено'

    @pytest.mark.django_db
    def test_falls_back_to_base_template_for_unknown_type(self, user, make_preference):
        """If a type-specific template does not exist, base_notification.html is used."""
        make_preference(user, do_not_disturb=False)

        from django.template import TemplateDoesNotExist

        call_count = []

        def fake_render(template_name, ctx):
            call_count.append(template_name)
            if 'base_notification' not in template_name:
                raise TemplateDoesNotExist(template_name)
            return '<html>fallback</html>'

        with patch('apps.notifications.tasks.send_mail') as mock_mail, \
             patch('apps.notifications.tasks.render_to_string', side_effect=fake_render):
            send_notification_email(user.id, 'system', {'subject': 'Sys'})

        # Should have tried the type-specific template, then fallen back to base
        assert any('system' in t for t in call_count)
        assert any('base_notification' in t for t in call_count)
        mock_mail.assert_called_once()
        assert mock_mail.call_args[1]['html_message'] == '<html>fallback</html>'

    @pytest.mark.django_db
    def test_recipient_is_user_email(self, user, make_preference):
        make_preference(user, booking_email=True, do_not_disturb=False)

        with patch('apps.notifications.tasks.send_mail') as mock_mail, \
             patch('apps.notifications.tasks.render_to_string', return_value='<html/>'):
            send_notification_email(
                user.id,
                'booking_confirmed',
                {'subject': 'Booking', 'action_url': '/bookings/1'},
            )

        _, call_kwargs = mock_mail.call_args
        assert user.email in call_kwargs['recipient_list']


# ---------------------------------------------------------------------------
# Integration tests for send_bulk_email task
# ---------------------------------------------------------------------------

class TestSendBulkEmail:
    @pytest.mark.django_db
    def test_sends_to_all_active_members_when_notify_email_true(self, company, make_user):
        from apps.services.models import Announcement

        user1 = make_user(company=company)
        user2 = make_user(company=company)
        # Inactive user should be skipped
        inactive = make_user(company=company)
        inactive.is_active = False
        inactive.save(update_fields=['is_active'])

        announcement = Announcement.objects.create(
            title='Company News',
            body='Important update.',
            company=company,
            scope='company',
            notify_email=True,
        )

        with patch('apps.notifications.tasks.send_notification_email') as mock_task:
            mock_task.delay = MagicMock()
            send_bulk_email(announcement.id)

        called_user_ids = {c[0][0] for c in mock_task.delay.call_args_list}
        assert user1.id in called_user_ids
        assert user2.id in called_user_ids
        assert inactive.id not in called_user_ids

    @pytest.mark.django_db
    def test_skips_when_notify_email_false(self, company):
        from apps.services.models import Announcement

        announcement = Announcement.objects.create(
            title='Silent',
            body='No email.',
            company=company,
            scope='company',
            notify_email=False,
        )

        with patch('apps.notifications.tasks.send_notification_email') as mock_task:
            mock_task.delay = MagicMock()
            send_bulk_email(announcement.id)

        mock_task.delay.assert_not_called()

    @pytest.mark.django_db
    def test_skips_when_announcement_does_not_exist(self):
        with patch('apps.notifications.tasks.send_notification_email') as mock_task:
            mock_task.delay = MagicMock()
            send_bulk_email(999999)

        mock_task.delay.assert_not_called()

    @pytest.mark.django_db
    def test_notification_type_is_announcement_company(self, company, make_user):
        from apps.services.models import Announcement

        make_user(company=company)

        announcement = Announcement.objects.create(
            title='News',
            body='Body',
            company=company,
            scope='company',
            notify_email=True,
        )

        with patch('apps.notifications.tasks.send_notification_email') as mock_task:
            mock_task.delay = MagicMock()
            send_bulk_email(announcement.id)

        # Check that the correct notification_type is passed
        for c in mock_task.delay.call_args_list:
            assert c[0][1] == 'announcement_company'
