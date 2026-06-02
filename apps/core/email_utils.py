"""Helpers for HTML emails with inline images (Outlook-safe)."""
import base64
from email.mime.image import MIMEImage

# Content-ID referenced in templates as cid:<this value> (no angle brackets).
GUEST_PASS_QR_CID = 'qr_code@newlevelhub'


def png_to_data_uri(image_bytes: bytes) -> str:
    """Return a data: URI suitable for <img src=...> in HTML email."""
    encoded = base64.b64encode(image_bytes).decode('ascii')
    return f'data:image/png;base64,{encoded}'


def build_inline_png_attachment(image_bytes: bytes, content_id: str, filename: str) -> MIMEImage:
    """Build an inline PNG part for multipart/related HTML emails."""
    mime_img = MIMEImage(image_bytes, _subtype='png')
    mime_img.add_header('Content-ID', f'<{content_id}>')
    mime_img.add_header('Content-Disposition', 'inline', filename=filename)
    # Required by Outlook on Windows to resolve cid: references.
    mime_img.add_header('X-Attachment-Id', content_id)
    return mime_img


def attach_html_with_inline_image(email, html_body: str, mime_img: MIMEImage) -> None:
    """
    Attach HTML + inline image in multipart/related order preferred by Outlook.

    Image is attached before the HTML alternative so desktop Outlook resolves cid: reliably.
    """
    email.mixed_subtype = 'related'
    email.attach(mime_img)
    email.attach_alternative(html_body, 'text/html')
