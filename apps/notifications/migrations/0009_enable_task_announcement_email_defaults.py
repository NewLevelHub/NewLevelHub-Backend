from django.db import migrations, models


def enable_task_announcement_email(apps, schema_editor):
    NotificationPreference = apps.get_model('notifications', 'NotificationPreference')
    NotificationPreference.objects.filter(task_assigned_email=False).update(task_assigned_email=True)
    NotificationPreference.objects.filter(announcement_email=False).update(announcement_email=True)


def reverse_enable_task_announcement_email(apps, schema_editor):
    NotificationPreference = apps.get_model('notifications', 'NotificationPreference')
    NotificationPreference.objects.update(task_assigned_email=False, announcement_email=False)


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0008_task_email_prefs_default_false'),
    ]

    operations = [
        migrations.AlterField(
            model_name='notificationpreference',
            name='task_assigned_email',
            field=models.BooleanField(default=True),
        ),
        migrations.AlterField(
            model_name='notificationpreference',
            name='announcement_email',
            field=models.BooleanField(default=True),
        ),
        migrations.RunPython(
            enable_task_announcement_email,
            reverse_code=reverse_enable_task_announcement_email,
        ),
    ]
