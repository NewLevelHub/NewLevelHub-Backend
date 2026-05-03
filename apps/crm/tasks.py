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


def maybe_notify_deadline_tomorrow_once(
    task,
    *,
    skip_if_in_done_column=True,
    skip_same_calendar_day_dedup=False,
):
    """
    Send in-app task_deadline (+ enqueue email) when the task's deadline falls on
    calendar *tomorrow* in the active timezone.

    Used by the periodic Celery job and synchronously after task create/update so that
    tasks created *after* the daily beat run still notify (otherwise Tuesday's 8:00 job
    misses tasks added Tuesday afternoon with deadline Wednesday).

    When ``skip_if_in_done_column`` is True (default, used by Celery), tasks in the
    column named "Готово" are skipped so finished work does not produce reminders.
    API create/update passes False so a task filed under "Готово" still notifies if the
    user sets deadline tomorrow—matching board UX where users often drop cards there first.

    ``skip_same_calendar_day_dedup`` is set True from PATCH when the deadline *value*
    actually changed (calendar date). Otherwise one reminder per task URL per day would
    block a second in-app row when the user edits the deadline later the same day.

    Also skips tasks without assignee/creator, and duplicate in-app rows for the same
    user/url/calendar day when dedup is enabled.
    """
    if task.deadline is None:
        return
    if skip_if_in_done_column and _is_done_column(task):
        return
    tomorrow = timezone.localdate() + timedelta(days=1)
    if timezone.localtime(task.deadline).date() != tomorrow:
        return
    user = _recipient_for_deadline(task)
    if user is None:
        return
    today = timezone.localdate()
    url = f'/crm/tasks/{task.pk}/'
    if not skip_same_calendar_day_dedup and Notification.objects.filter(
        user=user,
        notification_type='task_deadline',
        url=url,
        created_at__date=today,
    ).exists():
        return
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
def notify_deadline_approaching():
    """Notify assignee/creator about tasks whose deadline is tomorrow (local date)."""
    from apps.crm.models import Task

    qs = (
        Task.objects.filter(is_archived=False, deadline__isnull=False)
        .select_related('assignee', 'created_by', 'column')
    )

    for task in qs.iterator(chunk_size=200):
        maybe_notify_deadline_tomorrow_once(task)


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
