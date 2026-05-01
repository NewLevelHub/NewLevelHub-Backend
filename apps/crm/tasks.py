from celery import shared_task


@shared_task
def notify_task_assigned(task_id):
    """
    Send an in-app notification and email to the task assignee.

    Called from TaskViewSet.perform_create / perform_update when the assignee
    field is set or changed.  The view already fires
    send_notification_email.delay() directly, so this task exists as a
    re-usable entry-point that can be scheduled or called from other places.
    """
    from apps.crm.models import Task
    from apps.notifications.models import Notification
    from apps.notifications.tasks import send_notification_email

    try:
        task = Task.objects.select_related('assignee', 'column__board').get(pk=task_id)
    except Task.DoesNotExist:
        return

    if not task.assignee:
        return

    Notification.objects.get_or_create(
        user=task.assignee,
        notification_type='task_assigned',
        url=f'/crm/tasks/{task.pk}/',
        defaults={
            'title': 'Вам назначена задача',
            'body': task.title,
        },
    )

    send_notification_email.delay(
        task.assignee.id,
        'task_assigned',
        {
            'subject': 'Вам назначена задача',
            'task_title': task.title,
            'board_name': task.column.board.name if task.column else '',
            'action_url': f'/crm/tasks/{task.pk}/',
        },
    )


@shared_task
def notify_deadline_approaching():
    """Уведомление: дедлайн задачи наступает завтра."""
    # TODO: найти задачи с deadline = tomorrow, отправить уведомления
    pass


@shared_task
def notify_deadline_overdue():
    """Уведомление: дедлайн просрочен."""
    # TODO: найти задачи с deadline < now и статус не «Готово», уведомить
    pass
