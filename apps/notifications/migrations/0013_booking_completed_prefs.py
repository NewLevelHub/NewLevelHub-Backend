from django.db import migrations, models


class Migration(migrations.Migration):
    """
    booking_completed_in_app / booking_completed_email columns already exist in
    the DB (added outside the migration history) but are missing from the model.
    This migration:
      - DB level  : backfills any NULL rows and sets column-level defaults so
                    future INSERTs that predate the Django field work correctly.
      - State level: registers the two fields in Django's migration state so
                    the ORM includes them in all INSERT/UPDATE statements going
                    forward.
    """

    dependencies = [
        ('notifications', '0012_fix_new_employee_email_default'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql="""
                        UPDATE notification_preferences
                           SET booking_completed_in_app = TRUE
                         WHERE booking_completed_in_app IS NULL;

                        ALTER TABLE notification_preferences
                          ALTER COLUMN booking_completed_in_app SET DEFAULT TRUE;

                        UPDATE notification_preferences
                           SET booking_completed_email = FALSE
                         WHERE booking_completed_email IS NULL;

                        ALTER TABLE notification_preferences
                          ALTER COLUMN booking_completed_email SET DEFAULT FALSE;
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
