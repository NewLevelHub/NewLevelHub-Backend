from django.db import migrations
from django.utils import timezone


def backfill(apps, schema_editor):
    Invitation = apps.get_model('companies', 'Invitation')
    now = timezone.now()
    # Only run if is_used column exists (legacy databases)
    from django.db import connection
    with connection.cursor() as cursor:
        cols = [c.name for c in connection.introspection.get_table_description(cursor, 'invitations')]
    if 'is_used' not in cols:
        return
    Invitation.objects.filter(is_used=True).update(status='accepted')
    Invitation.objects.filter(is_used=False, expires_at__lt=now).update(status='expired')


class Migration(migrations.Migration):

    dependencies = [
        ('companies', '0016_add_status_to_invitation'),
    ]

    operations = [
        migrations.RunPython(backfill, reverse_code=migrations.RunPython.noop),
    ]
