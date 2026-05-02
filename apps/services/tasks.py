from celery import shared_task


@shared_task
def notify_service_request_status_change(request_id):
    """Notify request creator about status changes (e.g. called from async pipelines)."""
    from apps.services.models import ServiceRequest
    from apps.notifications.utils import create_notification

    try:
        sr = ServiceRequest.objects.select_related('created_by').get(pk=request_id)
    except ServiceRequest.DoesNotExist:
        return

    if not sr.created_by_id:
        return

    create_notification(
        user=sr.created_by,
        notification_type='service_request_update',
        title='Обновление заявки на сервис',
        message=f'Статус: {sr.get_status_display()}',
        link='/service-requests/',
    )


@shared_task
def send_announcement_emails(announcement_id):
    """Email blast when notify_email=True (delegates to notifications bulk helper)."""
    from apps.notifications.tasks import send_bulk_email

    send_bulk_email(announcement_id)
