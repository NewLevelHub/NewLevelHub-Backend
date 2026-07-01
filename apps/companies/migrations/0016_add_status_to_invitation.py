from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('companies', '0015_merge_20260604_0257'),
    ]

    operations = [
        migrations.AddField(
            model_name='invitation',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending', 'Pending'),
                    ('accepted', 'Accepted'),
                    ('expired', 'Expired'),
                    ('revoked', 'Revoked'),
                ],
                db_index=True,
                default='pending',
                max_length=20,
            ),
        ),
    ]
