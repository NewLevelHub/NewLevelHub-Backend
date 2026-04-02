from celery import shared_task


@shared_task
def send_guest_pass_email(guest_pass_id):
    """Отправить QR-код на email гостя."""
    # TODO: получить GuestPass, сгенерировать QR-изображение (qrcode lib),
    #       отправить email с QR и деталями визита
    pass


@shared_task
def expire_guest_passes():
    """Периодическая задача: пометить просроченные пропуска."""
    # TODO: GuestPass.objects.filter(status='active', valid_until__lt=now) → status='expired'
    pass


@shared_task
def notify_pass_creator_on_entry(guest_pass_id):
    """Уведомить создателя пропуска, что гость прошёл."""
    # TODO: отправить уведомление
    pass
