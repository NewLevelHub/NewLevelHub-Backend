from django.db import migrations, models


def backfill_field_name(apps, schema_editor):
    TaskHistory = apps.get_model('crm', 'TaskHistory')
    to_update = []
    for row in TaskHistory.objects.filter(action__startswith='updated_'):
        row.field_name = row.action[len('updated_'):]
        row.action = 'updated'
        to_update.append(row)
    TaskHistory.objects.bulk_update(to_update, ['action', 'field_name'])


def reverse_backfill_field_name(apps, schema_editor):
    TaskHistory = apps.get_model('crm', 'TaskHistory')
    to_update = []
    for row in TaskHistory.objects.filter(action='updated', field_name__isnull=False):
        row.action = f'updated_{row.field_name}'
        row.field_name = None
        to_update.append(row)
    TaskHistory.objects.bulk_update(to_update, ['action', 'field_name'])


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0005_backfill_is_archived_for_soft_deleted_tasks'),
    ]

    operations = [
        migrations.AddField(
            model_name='taskhistory',
            name='field_name',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.RunPython(backfill_field_name, reverse_code=reverse_backfill_field_name),
    ]
