from celery import shared_task
from io import BytesIO
from django.core.mail import EmailMessage
from django.utils import timezone
import qrcode

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

    # Build QR PNG in-memory to avoid worker dependency on shared media volume.
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(str(guest_pass.qr_code))
    qr.make(fit=True)
    qr_image = qr.make_image(fill_color='black', back_color='white')
    buffer = BytesIO()
    qr_image.save(buffer, format='PNG')
    buffer.seek(0)
    email.attach(f'guest-pass-{guest_pass.qr_code}.png', buffer.read(), 'image/png')

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
