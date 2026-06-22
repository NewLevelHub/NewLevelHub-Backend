import uuid

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel


def _avatar_upload_path(instance, filename):
    """Store avatars under avatars/<user_id>/<filename>."""
    return f'avatars/{instance.pk}/{filename}'


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('Email is required')
        email = self.normalize_email(email)
        extra_fields.setdefault('is_email_verified', False)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('role', 'superadmin')
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin, TimeStampedModel):
    ROLE_CHOICES = [
        ('superadmin', 'Super Admin'),
        ('company_admin', 'Company Admin'),
        ('employee', 'Employee'),
        ('reception', 'Reception'),
        ('service_manager', 'Service Manager'),
        ('guest', 'Guest'),
    ]

    email = models.EmailField(unique=True, db_index=True)
    phone = models.CharField(max_length=20, blank=True, default='')
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='guest', db_index=True)

    company = models.ForeignKey(
        'companies.Company',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='members',
    )

    position = models.CharField(max_length=100, blank=True, default='')
    avatar = models.ImageField(upload_to=_avatar_upload_path, null=True, blank=True)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    is_email_verified = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)

    # Soft-delete fields.  SoftDeleteModel mixin cannot be used here because
    # it replaces `objects` with SoftDeleteManager, which would conflict with
    # the custom UserManager.  We add the fields directly instead and handle
    # the deletion logic in the view / service layer.
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = UserManager()
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['first_name', 'last_name']

    class Meta:
        db_table = 'users'
        ordering = ['-date_joined']
        indexes = [
            models.Index(fields=['email', 'is_active']),
            models.Index(fields=['role', 'is_active']),
            models.Index(fields=['company', 'is_active']),
        ]
        constraints = [
            # Keep in sync with users.0004_user_company_required_roles (company_id, not company__isnull)
            models.CheckConstraint(
                check=(
                    ~models.Q(role__in=['employee', 'company_admin'])
                    | models.Q(company_id__isnull=False)
                ),
                name='users_employee_admin_requires_company',
            ),
        ]

    def clean(self):
        super().clean()
        if self.role in ('employee', 'company_admin') and self.company_id is None:
            raise ValidationError(
                {'company': 'Users with this role must be assigned to a company.'}
            )

    def __str__(self):
        return f'{self.email} ({self.get_role_display()})'

    @property
    def full_name(self):
        return f'{self.first_name} {self.last_name}'.strip() or self.email

    def is_superadmin(self):
        return self.role == 'superadmin'

    def is_company_admin(self):
        return self.role in ('superadmin', 'company_admin')

    def is_company_member(self):
        return self.role in ('company_admin', 'employee') and self.company_id is not None

    def is_service_manager(self):
        return self.role == 'service_manager'


class EmailVerificationToken(TimeStampedModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='email_tokens')
    token = models.UUIDField(default=uuid.uuid4, unique=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)

    class Meta:
        db_table = 'email_verification_tokens'

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at


class PasswordResetToken(TimeStampedModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='password_reset_tokens')
    token = models.UUIDField(default=uuid.uuid4, unique=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)

    class Meta:
        db_table = 'password_reset_tokens'

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at
