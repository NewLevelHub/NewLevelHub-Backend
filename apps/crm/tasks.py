from celery import shared_task


@shared_task
def notify_task_assigned(task_id):
    """Уведомление при назначении задачи."""
    # TODO: получить Task, отправить уведомление assignee
    pass


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
