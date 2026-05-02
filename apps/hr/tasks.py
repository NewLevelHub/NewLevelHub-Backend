from celery import shared_task

from django.contrib.auth import get_user_model

from apps.notifications.utils import create_notification

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
    """Notify company admins that a leave request needs review."""
    from apps.hr.models import LeaveRequest

    try:
        lr = LeaveRequest.objects.select_related('user', 'company').get(pk=leave_request_id)
    except LeaveRequest.DoesNotExist:
        return

    company = lr.company
    employee = lr.user
    if not company:
        return

    User = get_user_model()
    admins = User.objects.filter(
        company=company,
        role='company_admin',
        is_active=True,
    )
    for admin in admins:
        create_notification(
            user=admin,
            notification_type='leave_review',
            title=f'Заявка на отпуск от {employee.full_name}',
            message=f'{employee.full_name} подал(а) заявку на отпуск.',
            link='/hr/leaves',
        )


@shared_task
def notify_leave_request_reviewed(leave_request_id):
    """Notify employee about approval/rejection (mirrors review endpoint)."""
    from apps.hr.models import LeaveRequest

    try:
        lr = LeaveRequest.objects.select_related('user').get(pk=leave_request_id)
    except LeaveRequest.DoesNotExist:
        return

    if lr.status == 'approved':
        notif_type = 'leave_approved'
        status_text = 'одобрена'
    elif lr.status == 'rejected':
        notif_type = 'leave_rejected'
        status_text = 'отклонена'
    else:
        return

    create_notification(
        user=lr.user,
        notification_type=notif_type,
        title=f'Ваша заявка на отпуск {status_text}',
        message=lr.review_comment or '',
        link='/leave',
    )


@shared_task
def initialize_onboarding(user_id):
    """Создать UserOnboardingProgress для нового сотрудника."""
    User = get_user_model()
    user = User.objects.filter(pk=user_id).first()
    if not user:
        return 0
    return initialize_user_onboarding_progress(user)
