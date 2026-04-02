from celery import shared_task


@shared_task
def create_notification(user_id, notification_type, title, body='', url=''):
    """Универсальная задача создания уведомления с проверкой preferences."""
    # TODO: проверить NotificationPreference пользователя,
    #       создать Notification, при необходимости отправить email
    pass


@shared_task
def send_notification_email(notification_id):
    """Отправка email-версии уведомления."""
    # TODO: получить Notification, сформировать HTML из шаблона, отправить
    pass
