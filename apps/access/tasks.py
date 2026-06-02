from datetime import timedelta
from io import BytesIO

import qrcode
from celery import shared_task
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone

from apps.core.email_utils import (
    GUEST_PASS_QR_CID,
    attach_html_with_inline_image,
    build_inline_png_attachment,
    png_to_data_uri,
)
from apps.notifications.models import Notification
from apps.notifications.utils import create_notification

from .models import GuestPass


# NOTE: In-app guest_validated notification is created synchronously in validate_qr;
# this task sends email only.


@shared_task
def send_guest_pass_email(guest_pass_id):
    """Send pass details and QR image to guest email."""
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
    # Build QR PNG in-memory to avoid worker dependency on shared media volume.
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(str(guest_pass.qr_code))
    qr.make(fit=True)
    qr_image = qr.make_image(fill_color='black', back_color='white')
    if qr_image.mode != 'RGB':
        qr_image = qr_image.convert('RGB')
    buffer = BytesIO()
    qr_image.save(buffer, format='PNG')
    qr_bytes = buffer.getvalue()

    html_body = render_to_string('emails/guest_pass.html', {
        'guest_name': guest_pass.guest_name,
        'visit_purpose': guest_pass.visit_purpose,
        'valid_from': guest_pass.valid_from,
        'valid_until': guest_pass.valid_until,
        'created_by_name': guest_pass.created_by.full_name,
        'qr_cid': GUEST_PASS_QR_CID,
        'qr_data_uri': png_to_data_uri(qr_bytes),
    })

    email = EmailMultiAlternatives(subject=subject, body=body, to=[guest_pass.guest_email])
    mime_img = build_inline_png_attachment(
        qr_bytes,
        content_id=GUEST_PASS_QR_CID,
        filename=f'guest-pass-{guest_pass.qr_code}.png',
    )
    attach_html_with_inline_image(email, html_body, mime_img)
    email.send(fail_silently=True)


@shared_task
def expire_guest_passes():
    """Периодическая задача: пометить просроченные пропуска."""
    GuestPass.objects.filter(status='active', valid_until__lt=timezone.now()).update(status='expired')


@shared_task
def notify_pass_creator_on_entry(guest_pass_id):
    """Notify the pass creator via email that the guest has been validated at reception."""
    try:
        guest_pass = GuestPass.objects.select_related('created_by').get(id=guest_pass_id)
    except GuestPass.DoesNotExist:
        return

    from apps.notifications.tasks import send_notification_email
    send_notification_email.delay(
        guest_pass.created_by.id,
        'guest_validated',
        {
            'subject': 'Гостевой пропуск подтверждён',
            'guest_name': guest_pass.guest_name,
            'visit_purpose': guest_pass.visit_purpose,
            'validated_at': timezone.now().strftime('%Y-%m-%d %H:%M'),
            'action_url': '/access/',
        },
    )


@shared_task
def notify_guest_passes_expiring_soon():
    """Warn creators about guest passes expiring within the next 24 hours (once per pass per day)."""
    now = timezone.now()
    window_end = now + timedelta(hours=24)
    today = timezone.localdate()

    qs = GuestPass.objects.filter(
        status='active',
        valid_until__gt=now,
        valid_until__lte=window_end,
    ).select_related('created_by')

    for gp in qs.iterator(chunk_size=100):
        creator = gp.created_by
        if creator is None:
            continue
        marker = f'[gp:{gp.pk}]'
        if Notification.objects.filter(
            user=creator,
            notification_type='guest_pass_expiring',
            created_at__date=today,
            body__contains=marker,
        ).exists():
            continue
        create_notification(
            user=creator,
            notification_type='guest_pass_expiring',
            title=f'Пропуск скоро истечёт: {gp.guest_name}',
            message=(
                f'Действителен до {timezone.localtime(gp.valid_until):%d.%m.%Y %H:%M}. {marker}'
            ),
            link='/access/',
        )
