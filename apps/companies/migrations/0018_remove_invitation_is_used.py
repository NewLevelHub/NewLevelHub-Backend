# Generated manually

from django.db import migrations


class Migration(migrations.Migration):
    """
    Step 3/3: Drop the now-redundant is_used BooleanField from Invitation.
    The status field added in 0016 and backfilled in 0017 replaces it.
    """

    dependencies = [
        ('companies', '0017_backfill_invitation_status'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='invitation',
            name='is_used',
        ),
    ]
