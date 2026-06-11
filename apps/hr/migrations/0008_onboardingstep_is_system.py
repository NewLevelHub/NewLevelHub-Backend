from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hr', '0007_leaverequest_assigned_reviewer_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='onboardingstep',
            name='is_system',
            field=models.BooleanField(default=False),
        ),
    ]
