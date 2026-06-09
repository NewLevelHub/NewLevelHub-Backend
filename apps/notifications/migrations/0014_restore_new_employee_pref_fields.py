from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Restores new_employee_in_app / new_employee_email to Django's migration
    state. The columns already exist in the database (added by an earlier
    migration that was later lost), so database_operations is intentionally
    empty — SeparateDatabaseAndState prevents Django from re-running the DDL.
    """

    dependencies = [
        ('notifications', '0013_add_booking_completed_pref'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name='notificationpreference',
                    name='new_employee_in_app',
                    field=models.BooleanField(default=True),
                ),
                migrations.AddField(
                    model_name='notificationpreference',
                    name='new_employee_email',
                    field=models.BooleanField(default=True),
                ),
            ],
            database_operations=[],
        ),
    ]
