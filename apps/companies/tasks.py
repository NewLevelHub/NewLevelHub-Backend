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
        subject=f'Вас пригласили в {company_name}',
        message=(
            f'Здравствуйте,\n\n'
            f'{inviter_name} пригласил вас присоединиться к {company_name} в роли {invitation.role}.\n'
            f'Используйте эту ссылку, чтобы принять приглашение:\n{invite_url}\n\n'
            'Это приглашение действительно в течение 72 часов.'
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
