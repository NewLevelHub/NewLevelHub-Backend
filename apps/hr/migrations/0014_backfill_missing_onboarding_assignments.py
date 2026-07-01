from django.db import migrations


def backfill_missing_assignments(apps, schema_editor):
    """
    Find users who have UserOnboardingProgress rows but no OnboardingAssignment.
    This can happen when _setup_onboarding_for_invite_user fell into the legacy
    path (initialize_user_onboarding_progress) before the bug-fix.

    For each such user, derive the template from their progress rows and create
    the missing OnboardingAssignment.
    """
    UserOnboardingProgress = apps.get_model('hr', 'UserOnboardingProgress')
    OnboardingAssignment = apps.get_model('hr', 'OnboardingAssignment')

    # Find (user_id, template_id) pairs where the user has no assignment.
    # Group by user + template — take the smallest template_id per user in case
    # of multiple orphaned templates (edge case).
    seen_users = set()
    for progress in (
        UserOnboardingProgress.objects
        .select_related('user', 'step__template')
        .order_by('step__template_id')
    ):
        user = progress.user
        if user.pk in seen_users:
            continue
        # Skip users who already have an assignment.
        if OnboardingAssignment.objects.filter(user=user).exists():
            seen_users.add(user.pk)
            continue
        template = progress.step.template
        OnboardingAssignment.objects.create(
            user=user,
            template=template,
            assigned_by=None,
            note='',
        )
        seen_users.add(user.pk)


def reverse_backfill(apps, schema_editor):
    """No meaningful reverse — no-op."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('hr', '0013_backfill_onboarding_assignments'),
    ]

    operations = [
        migrations.RunPython(backfill_missing_assignments, reverse_code=reverse_backfill),
    ]
