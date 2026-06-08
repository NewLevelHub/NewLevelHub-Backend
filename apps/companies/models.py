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
    PLAN_DEFAULT_LIMITS = {
        'basic': {'max_employees': 10, 'max_boards': 1, 'storage_limit_gb': 5},
        'standard': {'max_employees': 30, 'max_boards': 5, 'storage_limit_gb': 20},
        'premium': {'max_employees': 9999, 'max_boards': 9999, 'storage_limit_gb': 100},
    }

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default='')
    logo = models.ImageField(upload_to='company_logos/', null=True, blank=True)
    floor = models.CharField(max_length=50, blank=True, default='')
    floor_fk = models.ForeignKey(
        'services.Floor',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='companies',
    )
    office_number = models.CharField(max_length=50, blank=True, default='')
    categories = models.JSONField(default=list, blank=True)

    plan = models.CharField(max_length=20, choices=PLAN_CHOICES, default='basic', db_index=True)
    max_employees = models.PositiveIntegerField(default=10)
    storage_limit_gb = models.DecimalField(
        max_digits=8,
        decimal_places=3,
        default=5,
        verbose_name='Storage limit (GB, e.g. 0.1 = 100 MB)',
    )
    max_boards = models.PositiveIntegerField(default=1)

    is_active = models.BooleanField(default=True, db_index=True)

    working_hours_start = models.TimeField(default='09:00')
    working_hours_end = models.TimeField(default='18:00')

    class Meta:
        db_table = 'companies'
        verbose_name_plural = 'companies'

    def save(self, *args, **kwargs):
        """
        Apply plan-based defaults on create for flows that bypass DRF serializers
        (e.g. Django admin / direct ORM create).
        """
        if self._state.adding:
            basic_defaults = self.PLAN_DEFAULT_LIMITS['basic']
            plan_defaults = self.PLAN_DEFAULT_LIMITS.get(self.plan, basic_defaults)

            # Model field defaults are "basic". If company is created with another
            # plan and limit fields were left untouched, switch them to plan defaults.
            if self.max_employees == basic_defaults['max_employees']:
                self.max_employees = plan_defaults['max_employees']
            if self.max_boards == basic_defaults['max_boards']:
                self.max_boards = plan_defaults['max_boards']
            if self.storage_limit_gb == basic_defaults['storage_limit_gb']:
                self.storage_limit_gb = plan_defaults['storage_limit_gb']

        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    @property
    def employee_count(self):
        # Global superadmin may have company_id set; they never count as company roster/limit.
        return self.members.filter(is_active=True).exclude(role='superadmin').count()

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
    onboarding_completed = models.BooleanField(default=False)
    brand_primary_color = models.CharField(max_length=7, null=True, blank=True, default='')

    class Meta:
        db_table = 'company_settings'
        verbose_name_plural = "Company Settings"


class Invitation(TimeStampedModel):
    # Nullable so that "building staff" roles (reception, service_manager) can be invited
    # without being tied to a specific company. Company-scoped roles (employee, company_admin)
    # always carry a non-null company.
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='invitations',
        null=True, blank=True,
    )
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
        target = self.company.name if self.company_id else 'building staff'
        return f'Invite {self.email} → {target}'

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
