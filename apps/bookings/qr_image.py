"""Capsule booking QR PNG generation and persistence."""
import uuid
from io import BytesIO

import qrcode
from django.core.files.base import ContentFile


def generate_booking_qr_image(booking, *, save=True):
    """Generate PNG for booking.qr_code and store on booking.qr_image."""
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(str(booking.qr_code))
    qr.make(fit=True)
    image = qr.make_image(fill_color='black', back_color='white')
    if image.mode != 'RGB':
        image = image.convert('RGB')
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    buffer.seek(0)
    image_name = f'{booking.qr_code}.png'
    booking.qr_image.save(image_name, ContentFile(buffer.read()), save=False)
    if save:
        booking.save(update_fields=['qr_image'])


def ensure_capsule_booking_qr(booking):
    """Assign QR token and image for confirmed capsule bookings."""
    resource = booking.resource
    if resource.resource_type != 'capsule':
        return booking
    if booking.status != 'confirmed':
        return booking
    update_fields = []
    if booking.qr_code is None:
        booking.qr_code = uuid.uuid4()
        update_fields.append('qr_code')
    if update_fields:
        booking.save(update_fields=update_fields)
    if not booking.qr_image:
        generate_booking_qr_image(booking)
    return booking
