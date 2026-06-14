from django.db import migrations

SYSTEM_STEP_URLS = {
    'Заполнить профиль': '/profile',
    'Познакомиться с командой': '/team',
    'Изучить доски проектов': '/crm',
    'Забронировать рабочее место': '/bookings',
    'Прочитать правила бизнес-центра': '/rules',
    'Настроить уведомления': '/notifications',
}


def backfill_urls(apps, schema_editor):
    OnboardingStep = apps.get_model('hr', 'OnboardingStep')
    for title, url in SYSTEM_STEP_URLS.items():
        OnboardingStep.objects.filter(is_system=True, title=title).update(url=url)


class Migration(migrations.Migration):

    dependencies = [
        ('hr', '0008_onboardingstep_is_system'),
    ]

    operations = [
        migrations.RunPython(backfill_urls, migrations.RunPython.noop),
    ]
