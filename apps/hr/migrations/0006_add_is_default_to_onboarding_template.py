from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hr', '0005_alter_useronboardingprogress_options'),
    ]

    operations = [
        migrations.AddField(
            model_name='onboardingtemplate',
            name='is_default',
            field=models.BooleanField(default=False, db_index=True),
        ),
    ]
