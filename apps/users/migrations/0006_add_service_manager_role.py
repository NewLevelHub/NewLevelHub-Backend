"""Add service_manager role to User.ROLE_CHOICES (DEV-222)."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0005_backfill_is_email_verified_for_existing_users'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='role',
            field=models.CharField(
                choices=[
                    ('superadmin', 'Super Admin'),
                    ('company_admin', 'Company Admin'),
                    ('employee', 'Employee'),
                    ('reception', 'Reception'),
                    ('service_manager', 'Service Manager'),
                    ('guest', 'Guest'),
                ],
                db_index=True,
                default='guest',
                max_length=20,
            ),
        ),
    ]
