from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('bookings', '0003_add_reminder_sent_to_booking'),
        ('bookings', '0003_booking_cancellation_audit'),
    ]

    operations = []
