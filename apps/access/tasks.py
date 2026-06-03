from datetime import timedelta
from email.mime.image import MIMEImage

from celery import shared_task
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone

from apps.notifications.models import Notification
from apps.notifications.utils import create_notification

from .models import GuestPass
from .qr_image import generate_guest_pass_qr_image


# NOTE: In-app guest_validated notification is created synchronously in validate_qr;
# this task sends email only.


@shared_task
def send_guest_pass_email(guest_pass_id):
    """Send pass details and QR image to guest email."""
    try:
        guest_pass = GuestPass.objects.select_related('created_by').get(id=guest_pass_id)
    except GuestPass.DoesNotExist:
        return

    if not guest_pass.qr_image:
        generate_guest_pass_qr_image(guest_pass)

    subject = 'Ваш гостевой пропуск — NewLevelHub'
    body = (
        f'Здравствуйте, {guest_pass.guest_name}!\n\n'
        f'Ваш цифровой пропуск готов.\n'
        f'Цель визита: {guest_pass.visit_purpose}\n'
        f'Действителен с: {guest_pass.valid_from}\n'
        f'Действителен до: {guest_pass.valid_until}\n'
        f'Пригласил: {guest_pass.created_by.full_name}\n\n'
        f'Пожалуйста, предъявите QR-код на стойке регистрации.'
    )
    html_body = render_to_string('emails/guest_pass.html', {
        'guest_name': guest_pass.guest_name,
        'visit_purpose': guest_pass.visit_purpose,
        'valid_from': guest_pass.valid_from,
        'valid_until': guest_pass.valid_until,
        'created_by_name': guest_pass.created_by.full_name,
        'qr_image_url': 'cid:qr_code_image',
    })

    email = EmailMultiAlternatives(subject=subject, body=body, to=[guest_pass.guest_email])
    email.mixed_subtype = 'related'
    email.attach_alternative(html_body, 'text/html')

    guest_pass.qr_image.open('rb')
    try:
        mime_img = MIMEImage(guest_pass.qr_image.read())
    finally:
        guest_pass.qr_image.close()
    mime_img.add_header('Content-ID', '<qr_code_image>')
    mime_img.add_header('Content-Disposition', 'inline', filename='qr_code.png')
    email.attach(mime_img)

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
