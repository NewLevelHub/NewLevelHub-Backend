"""Tests for guest pass email QR embedding (Gmail-compatible hosted image URL)."""
from datetime import timedelta

import pytest
from django.core import mail
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.models import GuestPass
from apps.access.qr_image import generate_guest_pass_qr_image
from apps.access.tasks import send_guest_pass_email
from apps.companies.models import Company
from apps.users.models import User

QR_IMAGE_URL = '/api/v1/access/passes/qr/{qr_code}/image/'


@pytest.fixture
def company(db):
    return Company.objects.create(name='Email Test Co', plan='basic')


@pytest.fixture
def company_admin(db, company):
    return User.objects.create_user(
        email='email-admin@test.local',
        password='pass',
        first_name='Email',
        last_name='Admin',
        role='company_admin',
        company=company,
        is_email_verified=True,
    )


@pytest.fixture
def api_client():
    return APIClient()


@pytest.mark.django_db
@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    BACKEND_URL='https://api.example.com',
)
def test_guest_pass_email_uses_hosted_qr_url_without_attachments(company, company_admin):
    now = timezone.now()
    guest_pass = GuestPass.objects.create(
        created_by=company_admin,
        company=company,
        guest_name='Guest Test',
        guest_email='guest@example.com',
        visit_purpose='Meeting',
        valid_from=now,
        valid_until=now + timedelta(hours=2),
    )
    generate_guest_pass_qr_image(guest_pass)

    send_guest_pass_email(guest_pass.id)

    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    html_parts = [content for content, mimetype in message.alternatives if mimetype == 'text/html']
    assert html_parts, 'HTML alternative is required'
    html = html_parts[0]
    expected_url = f'https://api.example.com{QR_IMAGE_URL.format(qr_code=guest_pass.qr_code)}'
    assert expected_url in html
    assert 'cid:' not in html
    assert 'data:image/png;base64,' not in html

    mime_message = message.message()
    image_parts = [
        part for part in mime_message.walk()
        if part.get_content_type() == 'image/png'
    ]
    assert image_parts == [], 'Gmail breaks on inline PNG attachments; use hosted URL only'


@pytest.mark.django_db
def test_guest_pass_qr_image_endpoint_returns_png(api_client, company, company_admin):
    now = timezone.now()
    guest_pass = GuestPass.objects.create(
        created_by=company_admin,
        company=company,
        guest_name='Guest Test',
        guest_email='guest@example.com',
        visit_purpose='Meeting',
        valid_from=now,
        valid_until=now + timedelta(hours=2),
    )
    generate_guest_pass_qr_image(guest_pass)

    response = api_client.get(QR_IMAGE_URL.format(qr_code=guest_pass.qr_code))
    assert response.status_code == 200
    assert response['Content-Type'] == 'image/png'
    body = b''.join(response.streaming_content)
    assert body[:8] == b'\x89PNG\r\n\x1a\n'
