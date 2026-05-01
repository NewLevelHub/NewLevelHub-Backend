"""Add reception role to User.ROLE_CHOICES."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0002_avatar_upload_path'),
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
                    ('guest', 'Guest'),
                ],
                db_index=True,
                default='guest',
                max_length=20,
            ),
        ),
    ]
