"""Change avatar upload_to to avatars/<user_id>/ per-user directory."""
from django.db import migrations, models
import apps.users.models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='avatar',
            field=models.ImageField(
                blank=True,
                null=True,
                upload_to=apps.users.models._avatar_upload_path,
            ),
        ),
    ]
