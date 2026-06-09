from django.db import migrations


def enable_new_employee_email(apps, schema_editor):
    NotificationPreference = apps.get_model('notifications', 'NotificationPreference')
    NotificationPreference.objects.filter(new_employee_email=False).update(new_employee_email=True)


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0011_notificationpreference_new_employee_email_and_more'),
    ]

    operations = [
        migrations.RunPython(enable_new_employee_email, migrations.RunPython.noop),
    ]
