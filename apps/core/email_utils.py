"""Helpers for transactional HTML emails."""
from django.conf import settings


def get_public_api_base_url() -> str:
    """
    Base URL for links and images in outbound email (Celery has no request).

    Set BACKEND_URL to the public API origin (e.g. https://api.staging.example.com).
    Falls back to FRONTEND_URL when BACKEND_URL is unset (single-host deployments).
    """
    backend_url = (getattr(settings, 'BACKEND_URL', None) or '').strip()
    if backend_url:
        return backend_url.rstrip('/')
    return settings.FRONTEND_URL.rstrip('/')


def build_public_api_url(path: str) -> str:
    """Build absolute HTTPS/HTTP URL for an API path."""
    return f'{get_public_api_base_url()}/{path.lstrip("/")}'


def guest_pass_qr_email_image_url(qr_code) -> str:
    """Stable public URL for guest pass QR image (Gmail/Outlook load via https)."""
    return build_public_api_url(f'/api/v1/access/passes/qr/{qr_code}/image/')
