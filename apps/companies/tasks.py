from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail

from .models import Invitation


@shared_task
def send_invitation_email(invitation_id):
    """Отправка email с инвайт-ссылкой для регистрации сотрудника."""
    invitation = Invitation.objects.select_related('company', 'invited_by').filter(id=invitation_id).first()
    if not invitation:
        return

    invite_url = f"{settings.FRONTEND_URL.rstrip('/')}/invite?token={invitation.token}"
    inviter_name = invitation.invited_by.full_name or invitation.invited_by.email
    company_name = invitation.company.name

    send_mail(
        subject=f'Invitation to {company_name}',
        message=(
            f'Hello,\n\n'
            f'{inviter_name} invited you to join {company_name} as {invitation.role}.\n'
            f'Use this link to accept invitation:\n{invite_url}\n\n'
            'This invitation expires in 72 hours.'
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[invitation.email],
        fail_silently=False,
    )


@shared_task
def check_expired_invitations():
    """Периодическая задача: пометить просроченные инвайты."""
    # TODO: Invitation.objects.filter(expires_at__lt=now, is_used=False) — при необходимости обработать
    pass
