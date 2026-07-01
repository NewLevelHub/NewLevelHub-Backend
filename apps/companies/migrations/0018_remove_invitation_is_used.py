from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('companies', '0017_backfill_invitation_status'),
    ]

    operations = [
        # Use RunSQL with IF EXISTS to safely drop the column even if it was
        # already removed by a previous conflicting migration on some databases.
        migrations.RunSQL(
            sql="ALTER TABLE invitations DROP COLUMN IF EXISTS is_used;",
            reverse_sql=migrations.RunSQL.noop,
            state_operations=[
                migrations.RemoveField(
                    model_name='invitation',
                    name='is_used',
                ),
            ],
        ),
    ]
