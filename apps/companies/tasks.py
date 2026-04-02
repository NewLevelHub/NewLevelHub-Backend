from celery import shared_task


@shared_task
def send_invitation_email(invitation_id):
    """Отправка email с инвайт-ссылкой для регистрации сотрудника."""
    # TODO: получить Invitation, сгенерировать URL с токеном, отправить email
    pass


@shared_task
def check_expired_invitations():
    """Периодическая задача: пометить просроченные инвайты."""
    # TODO: Invitation.objects.filter(expires_at__lt=now, is_used=False) — при необходимости обработать
    pass
