from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bookings', '0003_booking_cancellation_audit'),
    ]

    operations = [
        migrations.AddField(
            model_name='booking',
            name='reminder_sent',
            field=models.BooleanField(db_index=True, default=False),
        ),
    ]
