from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from apps.notifications.models import Notification
from apps.notifications.utils import create_notification
from apps.notifications.tasks import send_notification_email


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


def _done_column_name():
    return 'Готово'


def _recipient_for_deadline(task):
    if task.assignee_id:
        return task.assignee
    if task.created_by_id:
        return task.created_by
    return None


def _is_done_column(task):
    return bool(task.column and task.column.name == _done_column_name())


@shared_task
def notify_deadline_approaching():
    """Notify assignee/creator about tasks whose deadline is tomorrow (local date)."""
    from apps.crm.models import Task

    tomorrow = timezone.localdate() + timedelta(days=1)
    today = timezone.localdate()

    qs = (
        Task.objects.filter(is_archived=False, deadline__isnull=False)
        .select_related('assignee', 'created_by', 'column')
    )

    for task in qs.iterator(chunk_size=200):
        if _is_done_column(task):
            continue
        if timezone.localtime(task.deadline).date() != tomorrow:
            continue
        user = _recipient_for_deadline(task)
        if user is None:
            continue
        url = f'/crm/tasks/{task.pk}/'
        if Notification.objects.filter(
            user=user,
            notification_type='task_deadline',
            url=url,
            created_at__date=today,
        ).exists():
            continue
        create_notification(
            user=user,
            notification_type='task_deadline',
            title=f'Дедлайн завтра: {task.title}',
            message=f'Срок выполнения — {timezone.localtime(task.deadline):%d.%m.%Y %H:%M}',
            link=url,
        )
        send_notification_email.delay(
            user.id,
            'task_deadline',
            {
                'subject': 'Дедлайн задачи завтра',
                'task_title': task.title,
                'action_url': url,
            },
        )


@shared_task
def notify_deadline_overdue():
    """Notify for tasks past deadline that are not in the Done column."""
    from apps.crm.models import Task

    today = timezone.localdate()

    qs = (
        Task.objects.filter(is_archived=False, deadline__isnull=False)
        .select_related('assignee', 'created_by', 'column')
    )

    for task in qs.iterator(chunk_size=200):
        if _is_done_column(task):
            continue
        deadline_date = timezone.localtime(task.deadline).date()
        if deadline_date >= today:
            continue
        user = _recipient_for_deadline(task)
        if user is None:
            continue
        url = f'/crm/tasks/{task.pk}/'
        if Notification.objects.filter(
            user=user,
            notification_type='task_deadline_overdue',
            url=url,
            created_at__date=today,
        ).exists():
            continue
        create_notification(
            user=user,
            notification_type='task_deadline_overdue',
            title=f'Просрочен дедлайн: {task.title}',
            message=f'Было до {timezone.localtime(task.deadline):%d.%m.%Y %H:%M}',
            link=url,
        )
        send_notification_email.delay(
            user.id,
            'task_deadline_overdue',
            {
                'subject': 'Просрочен дедлайн задачи',
                'task_title': task.title,
                'action_url': url,
            },
        )
