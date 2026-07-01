from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string

from .models import Invitation


@shared_task
def send_invitation_email(invitation_id):
    """Отправка email с инвайт-ссылкой для регистрации сотрудника."""
    invitation = Invitation.objects.select_related('company', 'invited_by').filter(id=invitation_id).first()
    if not invitation:
        return

    invite_url = f"{settings.FRONTEND_URL.rstrip('/')}/invite?token={invitation.token}"
    inviter_name = invitation.invited_by.full_name or invitation.invited_by.email

    if invitation.company_id:
        target = invitation.company.name
        subject = f'Вас пригласили в {target}'
        body_intro = f'{inviter_name} пригласил вас присоединиться к {target} в роли {invitation.role}.'
    else:
        # Building-wide staff (reception, service_manager) — invite is not tied to a company.
        subject = 'Вас пригласили в качестве сотрудника здания'
        body_intro = (
            f'{inviter_name} пригласил вас присоединиться в качестве сотрудника здания '
            f'в роли {invitation.role}.'
        )

    html_message = render_to_string('emails/company_invitation.html', {
        'inviter_name': inviter_name,
        'role': invitation.role,
        'invite_url': invite_url,
        'target': invitation.company.name if invitation.company_id else None,
        'is_company_invite': bool(invitation.company_id),
    })
    send_mail(
        subject=subject,
        message=(
            f'Здравствуйте,\n\n'
            f'{body_intro}\n'
            f'Используйте эту ссылку, чтобы принять приглашение:\n{invite_url}\n\n'
            'Это приглашение действительно в течение 72 часов.'
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[invitation.email],
        html_message=html_message,
        fail_silently=False,
    )


@shared_task
def check_expired_invitations():
    """Периодическая задача: пометить просроченные инвайты."""
    # TODO: Invitation.objects.filter(expires_at__lt=now, status='pending') — при необходимости обработать
    pass
