"""
Data migration: ensure all existing NotificationPreference rows have the correct
opt-out defaults (True) for core transactional email notification fields.

AC DEV-113 #3 — Opt-out by default for transactional email preferences.

Fields covered:
  - booking_confirmed_email  : booking creation confirmation
  - task_assigned_email      : CRM task assignment
  - invitation_email         : company invitation received
  - guest_validated_email    : guest pass approved (company_admin)
  - leave_review_email       : leave request pending review (company_admin)

Strategy: update ALL existing rows where any of these fields is False. We cannot
distinguish "user explicitly opted out" from "row was created with an old
default=False", so we apply the correct transactional default to every row.
Users can always opt back out via PATCH /api/v1/notifications/preferences/.
"""

from django.db import migrations


def backfill_transactional_email_defaults(apps, schema_editor):
    NotificationPreference = apps.get_model('notifications', 'NotificationPreference')
    NotificationPreference.objects.filter(booking_confirmed_email=False).update(booking_confirmed_email=True)
    NotificationPreference.objects.filter(task_assigned_email=False).update(task_assigned_email=True)
    NotificationPreference.objects.filter(invitation_email=False).update(invitation_email=True)
    NotificationPreference.objects.filter(guest_validated_email=False).update(guest_validated_email=True)
    NotificationPreference.objects.filter(leave_review_email=False).update(leave_review_email=True)


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0009_enable_task_announcement_email_defaults'),
    ]

    operations = [
        migrations.RunPython(
            backfill_transactional_email_defaults,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
