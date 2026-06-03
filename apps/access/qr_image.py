"""Guest pass QR PNG generation and persistence."""
from io import BytesIO

import qrcode
from django.core.files.base import ContentFile


def generate_guest_pass_qr_image(guest_pass, *, save=True):
    """Generate PNG for guest_pass.qr_code and store on guest_pass.qr_image."""
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(str(guest_pass.qr_code))
    qr.make(fit=True)
    image = qr.make_image(fill_color='black', back_color='white')
    if image.mode != 'RGB':
        image = image.convert('RGB')
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    buffer.seek(0)
    image_name = f'{guest_pass.qr_code}.png'
    guest_pass.qr_image.save(image_name, ContentFile(buffer.read()), save=False)
    if save:
        guest_pass.save(update_fields=['qr_image'])
