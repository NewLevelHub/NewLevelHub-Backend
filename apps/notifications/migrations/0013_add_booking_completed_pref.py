from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0010_backfill_transactional_email_defaults'),
    ]

    operations = [
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
    ]
