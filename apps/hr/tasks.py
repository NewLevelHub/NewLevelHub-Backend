from celery import shared_task

from django.contrib.auth import get_user_model

from .models import OnboardingTemplate, UserOnboardingProgress


def initialize_user_onboarding_progress(user):
    """
    Create progress rows for the active company template.
    Safe to call repeatedly: existing rows are preserved.
    """
    if not user or not user.company_id:
        return 0

    template = (
        OnboardingTemplate.objects.filter(company_id=user.company_id, is_active=True)
        .order_by('-created_at')
        .first()
    )
    if not template:
        return 0

    created_count = 0
    steps = template.steps.all()
    for step in steps:
        _, created = UserOnboardingProgress.objects.get_or_create(
            user=user,
            step=step,
            defaults={'is_completed': False},
        )
        if created:
            created_count += 1
    return created_count


@shared_task
def notify_leave_request_submitted(leave_request_id):
    """Уведомить админа компании о новой заявке на отпуск."""
    # TODO: получить LeaveRequest, отправить уведомление админу
    pass


@shared_task
def notify_leave_request_reviewed(leave_request_id):
    """Уведомить сотрудника о решении по заявке."""
    # TODO: получить LeaveRequest, отправить уведомление пользователю
    pass


@shared_task
def initialize_onboarding(user_id):
    """Создать UserOnboardingProgress для нового сотрудника."""
    user = get_user_model().objects.filter(pk=user_id).first()
    if not user:
        return 0
    return initialize_user_onboarding_progress(user)
