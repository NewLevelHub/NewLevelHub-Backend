"""Tests for guest pass email QR embedding (cross-client compatibility)."""
from datetime import timedelta

import pytest
from django.core import mail
from django.test import override_settings
from django.utils import timezone

from apps.access.models import GuestPass
from apps.access.tasks import send_guest_pass_email
from apps.companies.models import Company
from apps.core.email_utils import GUEST_PASS_QR_CID
from apps.users.models import User


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


@pytest.mark.django_db
@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
def test_guest_pass_email_includes_data_uri_and_outlook_cid(company, company_admin):
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

    send_guest_pass_email(guest_pass.id)

    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    html_parts = [content for content, mimetype in message.alternatives if mimetype == 'text/html']
    assert html_parts, 'HTML alternative is required'
    html = html_parts[0]
    assert 'data:image/png;base64,' in html
    assert f'cid:{GUEST_PASS_QR_CID}' in html

    mime_message = message.message()
    related_parts = [
        part for part in mime_message.walk()
        if part.get_content_type() == 'image/png'
    ]
    assert related_parts, 'Inline PNG attachment is required'
    assert related_parts[0]['Content-ID'] == f'<{GUEST_PASS_QR_CID}>'
    assert related_parts[0].get('X-Attachment-Id') == GUEST_PASS_QR_CID
