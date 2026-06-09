from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0012_fix_new_employee_email_default'),
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
