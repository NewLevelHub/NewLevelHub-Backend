import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel, SoftDeleteModel


class Company(TimeStampedModel, SoftDeleteModel):
    PLAN_CHOICES = [
        ('basic', 'Basic'),
        ('standard', 'Standard'),
        ('premium', 'Premium'),
    ]

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default='')
    logo = models.ImageField(upload_to='company_logos/', null=True, blank=True)
    floor = models.CharField(max_length=50, blank=True, default='')
    office_number = models.CharField(max_length=50, blank=True, default='')
    contact_email = models.EmailField(blank=True, default='')
    contact_phone = models.CharField(max_length=20, blank=True, default='')

    plan = models.CharField(max_length=20, choices=PLAN_CHOICES, default='basic', db_index=True)
    max_employees = models.PositiveIntegerField(default=10)
    storage_limit_gb = models.PositiveIntegerField(default=5)
    max_boards = models.PositiveIntegerField(default=1)

    is_active = models.BooleanField(default=True, db_index=True)

    working_hours_start = models.TimeField(default='09:00')
    working_hours_end = models.TimeField(default='18:00')

    class Meta:
        db_table = 'companies'
        verbose_name_plural = 'companies'

    def __str__(self):
        return self.name

    @property
    def employee_count(self):
        return self.members.filter(is_active=True).count()

    @property
    def is_employee_limit_reached(self):
        return self.employee_count >= self.max_employees


class CompanySettings(TimeStampedModel):
    """Расширенные настройки компании (One-to-One)."""
    company = models.OneToOneField(Company, on_delete=models.CASCADE, related_name='settings')
    custom_task_categories = models.JSONField(default=list, blank=True)
    custom_labels = models.JSONField(default=list, blank=True)
    vacation_days_per_year = models.PositiveIntegerField(default=24)
    onboarding_enabled = models.BooleanField(default=True)
    brand_primary_color = models.CharField(max_length=7, blank=True, default='')

    class Meta:
        db_table = 'company_settings'


class Invitation(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='invitations')
    email = models.EmailField()
    token = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='sent_invitations',
    )
    role = models.CharField(max_length=20, default='employee')
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'invitations'

    def __str__(self):
        return f'Invite {self.email} → {self.company.name}'

    def save(self, *args, **kwargs):
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(hours=72)
        super().save(*args, **kwargs)

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at

    @property
    def is_valid(self):
        return not self.is_used and not self.is_expired
