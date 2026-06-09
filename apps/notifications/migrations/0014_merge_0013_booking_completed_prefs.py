from django.db import migrations


class Migration(migrations.Migration):
    """Merge two parallel 0013 migrations that both add booking_completed_in_app
    and booking_completed_email to NotificationPreference."""

    dependencies = [
        ('notifications', '0013_add_booking_completed_pref'),
        ('notifications', '0013_booking_completed_prefs'),
    ]

    operations = []
