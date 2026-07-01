# Generated manually

from django.db import migrations
from django.utils import timezone


def backfill_invitation_status(apps, schema_editor):
    """
    Data migration: derive `status` from the legacy `is_used` boolean and `expires_at`.

    Mapping:
      - is_used=True          → 'accepted'  (best approximation for old data)
      - is_used=False, expires_at < now → 'expired'
      - is_used=False, expires_at >= now → 'pending'  (already default, but explicit)
    """
    Invitation = apps.get_model('companies', 'Invitation')
    now = timezone.now()

    # Only run if is_used column exists (legacy databases)
    from django.db import connection
    with connection.cursor() as cursor:
        cols = [c.name for c in connection.introspection.get_table_description(cursor, 'invitations')]
    if 'is_used' not in cols:
        return

    # Mark accepted (previously consumed invitations)
    Invitation.objects.filter(is_used=True).update(status='accepted')

    # Mark expired (not used but expiry has passed)
    Invitation.objects.filter(is_used=False, expires_at__lt=now).update(status='expired')

    # Remaining (is_used=False, expires_at >= now) already have status='pending' from the default.


def reverse_backfill(apps, schema_editor):
    """
    Reverse: reconstruct is_used from status so migration 0016 can be reversed cleanly.
    """
    Invitation = apps.get_model('companies', 'Invitation')
    Invitation.objects.filter(status='accepted').update(is_used=True)
    Invitation.objects.filter(status__in=['expired', 'pending', 'revoked']).update(is_used=False)


class Migration(migrations.Migration):
    """
    Step 2/3: Backfill the new `status` field from legacy `is_used` + `expires_at`.
    """

    dependencies = [
        ('companies', '0016_add_status_to_invitation'),
    ]

    operations = [
        migrations.RunPython(backfill_invitation_status, reverse_code=reverse_backfill),
    ]
