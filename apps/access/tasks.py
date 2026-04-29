from celery import shared_task
from django.core.mail import EmailMessage
from django.utils import timezone

from .models import GuestPass


def send_guest_pass_email_now(guest_pass_id):
    """Send pass details and QR image to guest email immediately."""
    try:
        guest_pass = GuestPass.objects.select_related('created_by').get(id=guest_pass_id)
    except GuestPass.DoesNotExist:
        return

    subject = 'Your NewLevelHub guest pass'
    body = (
        f'Hello {guest_pass.guest_name},\n\n'
        f'Your digital pass is ready.\n'
        f'Purpose: {guest_pass.visit_purpose}\n'
        f'Valid from: {guest_pass.valid_from}\n'
        f'Valid until: {guest_pass.valid_until}\n'
        f'Created by: {guest_pass.created_by.full_name}\n\n'
        f'Please present the attached QR code at reception.'
    )
    email = EmailMessage(subject=subject, body=body, to=[guest_pass.guest_email])
    if guest_pass.qr_image:
        guest_pass.qr_image.open('rb')
        try:
            email.attach(guest_pass.qr_image.name.split('/')[-1], guest_pass.qr_image.read(), 'image/png')
        finally:
            guest_pass.qr_image.close()
    email.send(fail_silently=False)


@shared_task
def send_guest_pass_email(guest_pass_id):
    """Celery wrapper around direct guest pass email sending."""
    send_guest_pass_email_now(guest_pass_id)


@shared_task
def expire_guest_passes():
    """Периодическая задача: пометить просроченные пропуска."""
    GuestPass.objects.filter(status='active', valid_until__lt=timezone.now()).update(status='expired')


@shared_task
def notify_pass_creator_on_entry(guest_pass_id):
    """Уведомить создателя пропуска, что гость прошёл."""
    # TODO: отправить уведомление
    pass
