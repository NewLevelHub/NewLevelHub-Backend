from celery import shared_task


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
    # TODO: найти активный OnboardingTemplate компании,
    #       создать UserOnboardingProgress для каждого шага
    pass
