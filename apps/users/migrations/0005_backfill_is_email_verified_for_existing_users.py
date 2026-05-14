from django.db import migrations


def backfill_email_verified(apps, schema_editor):
    """
    Set is_email_verified=True for all active users who have is_email_verified=False.

    These are users that existed before the email verification feature was added
    (DEV-187). They should be treated as verified because they were created
    through a trusted path (Django admin, management commands, or direct DB)
    before verification was enforced.
    """
    User = apps.get_model('users', 'User')
    User.objects.filter(is_active=True, is_email_verified=False).update(is_email_verified=True)


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0004_user_company_required_roles'),
    ]

    operations = [
        migrations.RunPython(backfill_email_verified, migrations.RunPython.noop),
    ]
