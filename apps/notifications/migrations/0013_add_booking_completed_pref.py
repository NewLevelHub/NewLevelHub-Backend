from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Adds booking_completed_in_app / booking_completed_email to NotificationPreference.

    Uses ADD COLUMN IF NOT EXISTS so this migration is idempotent when run after
    0013_booking_completed_prefs (which adds the same columns). Both migrations
    depend on 0012; a 0014 merge migration resolves the graph conflict.
    """

    dependencies = [
        ('notifications', '0012_fix_new_employee_email_default'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql="""
                        ALTER TABLE notification_preferences
                          ADD COLUMN IF NOT EXISTS booking_completed_in_app boolean NOT NULL DEFAULT TRUE;

                        ALTER TABLE notification_preferences
                          ADD COLUMN IF NOT EXISTS booking_completed_email boolean NOT NULL DEFAULT FALSE;
                    """,
                    reverse_sql=migrations.RunSQL.noop,
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name='notificationpreference',
                    name='booking_completed_in_app',
                    field=models.BooleanField(default=True),
                ),
                migrations.AddField(
                    model_name='notificationpreference',
                    name='booking_completed_email',
                    field=models.BooleanField(default=False),
                ),
            ],
        ),
    ]
