"""
Data migration: backfill task email preference fields for existing rows.

Migration 0005 added task_assigned_email, task_comment_email, task_deadline_email,
and task_moved_email with default=False.  Migration 0006 corrected the schema
default to True for new rows, but existing rows that were created between those
two migrations (or before 0006 was applied) still have False in the database.

This migration sets all four fields to True on every existing
NotificationPreference row where they are currently False, aligning existing
data with the intended default.
"""

from django.db import migrations


def backfill_task_email_defaults(apps, schema_editor):
    NotificationPreference = apps.get_model('notifications', 'NotificationPreference')
    NotificationPreference.objects.filter(task_assigned_email=False).update(task_assigned_email=True)
    NotificationPreference.objects.filter(task_comment_email=False).update(task_comment_email=True)
    NotificationPreference.objects.filter(task_deadline_email=False).update(task_deadline_email=True)
    NotificationPreference.objects.filter(task_moved_email=False).update(task_moved_email=True)


def reverse_backfill(apps, schema_editor):
    # Intentionally a no-op: we cannot know which rows were originally False
    # versus explicitly set to True by users.  Rolling back would incorrectly
    # flip user-controlled settings, so we leave them as-is.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0006_fix_task_email_defaults'),
    ]

    operations = [
        migrations.RunPython(backfill_task_email_defaults, reverse_code=reverse_backfill),
    ]
