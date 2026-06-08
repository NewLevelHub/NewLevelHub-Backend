from django.db import migrations


def backfill_floor_fk(apps, schema_editor):
    Company = apps.get_model('companies', 'Company')
    Floor = apps.get_model('services', 'Floor')

    for company in Company.objects.exclude(floor=''):
        try:
            floor_num = int(company.floor)
            floor_obj = Floor.objects.filter(number=floor_num).first()
            if floor_obj:
                company.floor_fk = floor_obj
                company.save(update_fields=['floor_fk'])
        except (ValueError, TypeError):
            pass


class Migration(migrations.Migration):

    dependencies = [
        ('companies', '0013_add_floor_fk_to_company'),
        ('services', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(backfill_floor_fk, migrations.RunPython.noop),
    ]
