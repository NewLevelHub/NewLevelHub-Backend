from datetime import date

from django.conf import settings
from django.db import models
from apps.core.models import TimeStampedModel


def current_year():
    return date.today().year


class LeaveRequest(TimeStampedModel):
    TYPE_CHOICES = [
        ('vacation', 'Vacation'),
        ('day_off', 'Day Off'),
        ('sick_leave', 'Sick Leave'),
        ('remote', 'Remote Work'),
    ]
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('cancelled', 'Cancelled'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='leave_requests')
    company = models.ForeignKey('companies.Company', on_delete=models.CASCADE, related_name='leave_requests')
    leave_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', db_index=True)

    start_date = models.DateField()
    end_date = models.DateField()
    comment = models.TextField(blank=True, default='')

    assigned_reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='assigned_leave_reviews',
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='reviewed_leave_requests',
    )
    review_comment = models.TextField(blank=True, default='')
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'hr_leave_requests'

    def __str__(self):
        return f'{self.user.email} — {self.get_leave_type_display()} ({self.start_date} → {self.end_date})'

    @property
    def duration_days(self):
        return (self.end_date - self.start_date).days + 1


class LeaveBalance(TimeStampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='leave_balances')
    year = models.PositiveIntegerField(default=current_year, db_index=True)
    total_days = models.PositiveIntegerField(default=24)
    used_days = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'hr_leave_balances'
        unique_together = ['user', 'year']

    @property
    def remaining_days(self):
        return max(self.total_days - self.used_days, 0)


class OnboardingTemplate(TimeStampedModel):
    company = models.ForeignKey('companies.Company', on_delete=models.CASCADE, related_name='onboarding_templates')
    title = models.CharField(max_length=255, default='Default onboarding')
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False, db_index=True)

    class Meta:
        db_table = 'hr_onboarding_templates'

    def set_as_default(self):
        """Назначить этот шаблон дефолтным, сняв флаг с остальных шаблонов компании."""
        OnboardingTemplate.objects.filter(company=self.company, is_default=True).exclude(pk=self.pk).update(
            is_default=False
        )
        if not self.is_default:
            self.is_default = True
            self.save(update_fields=['is_default', 'updated_at'])


class OnboardingStep(TimeStampedModel):
    template = models.ForeignKey(OnboardingTemplate, on_delete=models.CASCADE, related_name='steps')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default='')
    url = models.URLField(blank=True, default='')
    position = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'hr_onboarding_steps'
        ordering = ['position']


class UserOnboardingProgress(TimeStampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='onboarding_progress')
    step = models.ForeignKey(OnboardingStep, on_delete=models.CASCADE, related_name='progress')
    is_completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'hr_onboarding_progress'
        unique_together = ['user', 'step']
        verbose_name = "User Onboarding Progress"
        verbose_name_plural = "User Onboarding Progresses"
