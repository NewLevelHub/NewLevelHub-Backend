from django.db import migrations


def backfill_assignments(apps, schema_editor):
    """
    For each company: find the is_default template.
    For each employee who has UserOnboardingProgress rows on steps of that template
    → create an OnboardingAssignment(user=employee, template=template, assigned_by=None).
    """
    OnboardingTemplate = apps.get_model('hr', 'OnboardingTemplate')
    OnboardingAssignment = apps.get_model('hr', 'OnboardingAssignment')
    User = apps.get_model('users', 'User')

    for template in OnboardingTemplate.objects.filter(is_default=True).select_related('company'):
        # Find employees in this company that have progress rows linked to this template's steps
        employee_ids = (
            User.objects
            .filter(
                company=template.company,
                role='employee',
                onboarding_progress__step__template=template,
            )
            .values_list('id', flat=True)
            .distinct()
        )
        for user_id in employee_ids:
            OnboardingAssignment.objects.get_or_create(
                user_id=user_id,
                defaults={
                    'template': template,
                    'assigned_by': None,
                    'note': '',
                },
            )


def reverse_backfill(apps, schema_editor):
    """No meaningful reverse — just wipe all backfilled assignments."""
    OnboardingAssignment = apps.get_model('hr', 'OnboardingAssignment')
    OnboardingAssignment.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('hr', '0012_add_onboarding_assignment'),
    ]

    operations = [
        migrations.RunPython(backfill_assignments, reverse_code=reverse_backfill),
    ]
