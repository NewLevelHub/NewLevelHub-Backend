from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Adds booking_completed_in_app / booking_completed_email to NotificationPreference.

    The columns may or may not already exist in the target database:
      - Local/dev: were added outside the migration history (no-op for ADD COLUMN)
      - Staging/prod: do not exist yet (normal ADD COLUMN)

    ADD COLUMN IF NOT EXISTS makes the migration idempotent across all envs.
    The UPDATE ensures any pre-existing NULL rows are backfilled before the
    NOT NULL constraint is fully enforced by the ORM.
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

                        UPDATE notification_preferences
                           SET booking_completed_in_app = TRUE
                         WHERE booking_completed_in_app IS NULL;

                        UPDATE notification_preferences
                           SET booking_completed_email = FALSE
                         WHERE booking_completed_email IS NULL;
                    """,
                    reverse_sql="""
                        ALTER TABLE notification_preferences
                          DROP COLUMN IF EXISTS booking_completed_in_app;
                        ALTER TABLE notification_preferences
                          DROP COLUMN IF EXISTS booking_completed_email;
                    """,
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
