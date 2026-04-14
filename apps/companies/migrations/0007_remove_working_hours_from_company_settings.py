from django.db import migrations


class Migration(migrations.Migration):
    """
    Remove working_hours_start and working_hours_end from the company_settings table.

    Working hours are the canonical responsibility of the Company model (added in
    0006_add_working_hours_to_company).  Keeping duplicated columns in CompanySettings
    created a split-brain situation where the two endpoints (/companies/{id}/ and
    /companies/{id}/settings/) could return different values for the same company.

    This migration removes the redundant columns so there is a single source of truth.
    """

    dependencies = [
        ('companies', '0006_add_working_hours_to_company'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='companysettings',
            name='working_hours_start',
        ),
        migrations.RemoveField(
            model_name='companysettings',
            name='working_hours_end',
        ),
    ]
