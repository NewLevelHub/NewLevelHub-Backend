"""Allow Invitation.company to be null for building-staff invites (DEV-222)."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('companies', '0009_alter_companysettings_options'),
    ]

    operations = [
        migrations.AlterField(
            model_name='invitation',
            name='company',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name='invitations',
                to='companies.company',
            ),
        ),
    ]
