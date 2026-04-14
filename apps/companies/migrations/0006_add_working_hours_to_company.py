from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Add working_hours_start and working_hours_end to the Company table.

    These columns were defined in the Company model and included in the
    0001_initial migration, but the database table was created without them
    (the initial migration was applied against a pre-existing table that lacked
    these columns).  This migration adds them directly so the schema matches
    the model.
    """

    dependencies = [
        ('companies', '0005_add_onboarding_completed_to_company_settings'),
    ]

    operations = [
        migrations.AddField(
            model_name='company',
            name='working_hours_start',
            field=models.TimeField(default='09:00'),
        ),
        migrations.AddField(
            model_name='company',
            name='working_hours_end',
            field=models.TimeField(default='18:00'),
        ),
    ]
