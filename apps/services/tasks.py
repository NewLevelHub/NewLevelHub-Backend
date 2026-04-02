from celery import shared_task


@shared_task
def notify_service_request_status_change(request_id):
    """Уведомить пользователя о смене статуса заявки."""
    # TODO: получить ServiceRequest, отправить уведомление
    pass


@shared_task
def send_announcement_emails(announcement_id):
    """Рассылка email по объявлению (если notify_email=True)."""
    # TODO: получить Announcement, определить получателей (все или компания),
    #       отправить email каждому
    pass
