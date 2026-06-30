from django.db import migrations


class Migration(migrations.Migration):
    """
    Data migration: correct OnboardingStep.is_system for steps that belong to
    non-system templates.  Previously the guard checked step.is_system rather
    than step.template.is_system, so custom-template steps could end up with
    is_system=True even though their template is not a system template.
    """

    dependencies = [
        ('hr', '0010_add_is_system_to_onboarding_template'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                UPDATE hr_onboarding_steps
                SET is_system = FALSE
                WHERE template_id IN (
                    SELECT id FROM hr_onboarding_templates WHERE is_system = FALSE
                );
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
