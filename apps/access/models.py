import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from apps.core.models import TimeStampedModel


def _guest_pass_qr_upload_path(instance, filename):
    return f'guest-passes/qr/{instance.id}/{filename}'


class GuestPass(TimeStampedModel):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('used', 'Used'),
        ('expired', 'Expired'),
        ('revoked', 'Revoked'),
    ]
    USAGE_CHOICES = [
        ('single', 'Single use'),
        ('multi', 'Multi use'),
    ]

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='guest_passes')
    company = models.ForeignKey(
        'companies.Company', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='guest_passes',
    )

    guest_name = models.CharField(max_length=255)
    guest_email = models.EmailField()
    guest_phone = models.CharField(max_length=20, blank=True, default='')
    visit_purpose = models.TextField(blank=True, default='')

    qr_code = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True)
    qr_image = models.ImageField(upload_to=_guest_pass_qr_upload_path, blank=True, default='')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='active', db_index=True)
    usage_type = models.CharField(max_length=10, choices=USAGE_CHOICES, default='single')
    times_used = models.PositiveIntegerField(default=0)
    resend_attempts_in_window = models.PositiveSmallIntegerField(default=0)
    resend_window_started_at = models.DateTimeField(null=True, blank=True)

    valid_from = models.DateTimeField()
    valid_until = models.DateTimeField()

    class Meta:
        db_table = 'guest_passes'

    def __str__(self):
        return f'Pass for {self.guest_name} by {self.created_by.email}'

    @property
    def is_expired(self):
        return timezone.now() > self.valid_until

    @property
    def is_valid(self):
        if self.status != 'active':
            return False
        if self.is_expired:
            return False
        if self.usage_type == 'single' and self.times_used > 0:
            return False
        return True


class AccessLog(TimeStampedModel):
    METHOD_CHOICES = [
        ('qr', 'QR Code'),
        ('manual', 'Manual'),
    ]

    guest_pass = models.ForeignKey(
        GuestPass, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='access_logs'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='access_logs',
    )
    checked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='checked_access_logs',
    )

    entry_point = models.CharField(max_length=100, blank=True, default='')
    method = models.CharField(max_length=10, choices=METHOD_CHOICES, default='manual')
    is_entry = models.BooleanField(default=True, help_text='True=entry, False=exit')

    class Meta:
        db_table = 'access_logs'
        ordering = ['-created_at']
