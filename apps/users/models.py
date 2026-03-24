from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone
from apps.core.models import TimeStampedModel


class UserManager(BaseUserManager):
    """
    Custom manager for User model.
    """
    def create_user(self, email, password=None, **extra_fields):
        """Create and return a regular user."""
        if not email:
            raise ValueError('User must have an email address')

        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        """Create and return a superuser."""
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('role', 'supermentor')

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True')

        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin, TimeStampedModel):
    """
    Custom User model with role-based access control.

    Roles:
    - supermentor: Полный доступ ко всему
    - admin: Администратор бизнес-центра
    - tenant: Арендатор офиса
    - employee: Рядовой сотрудник
    - guest: Гость (временный доступ)
    """

    ROLE_CHOICES = [
        ('supermentor', 'Supermentor'),
        ('admin', 'Administrator'),
        ('tenant', 'Tenant'),
        ('employee', 'Employee'),
        ('guest', 'Guest'),
    ]

    ROLE_DESCRIPTIONS = {
        'supermentor': 'Полный доступ ко всему',
        'admin': 'Администратор бизнес-центра',
        'tenant': 'Арендатор офиса',
        'employee': 'Рядовой сотрудник',
        'guest': 'Гость (временный доступ)',
    }

    # Basic fields
    email = models.EmailField(unique=True, db_index=True, verbose_name='Email address')
    phone = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        unique=True,
        db_index=True,
        verbose_name='Phone number',
    )

    # Profile fields
    first_name = models.CharField(max_length=150, blank=True, verbose_name='First name')
    last_name = models.CharField(max_length=150, blank=True, verbose_name='Last name')

    # Role & Status
    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default='employee',
        db_index=True,
        verbose_name='User role',
    )
    is_active = models.BooleanField(default=True, verbose_name='Active')
    is_staff = models.BooleanField(default=False, verbose_name='Staff status')

    # Biometric data reference (для Face ID/Touch ID)
    face_data_ref = models.CharField(max_length=255, blank=True, null=True, verbose_name='Face data reference')

    # Dates
    last_login = models.DateTimeField(null=True, blank=True, verbose_name='Last login')
    date_joined = models.DateTimeField(default=timezone.now, verbose_name='Date joined')

    objects = UserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    class Meta:
        db_table = 'users'
        verbose_name = 'User'
        verbose_name_plural = 'Users'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['email', 'is_active']),
            models.Index(fields=['role', 'is_active']),
        ]

    def __str__(self):
        return f"{self.email} ({self.get_role_display()})"

    @property
    def full_name(self):
        """Return full name."""
        return f"{self.first_name} {self.last_name}".strip() or self.email

    def is_supermentor(self):
        """Check if user is supermentor."""
        return self.role == 'supermentor'

    def is_admin_user(self):
        """Check if user is admin or supermentor."""
        return self.role in ['admin', 'supermentor']

    def is_tenant_user(self):
        """Check if user is tenant, admin, or supermentor."""
        return self.role in ['tenant', 'admin', 'supermentor']

    @classmethod
    def get_role_definitions(cls):
        """Return stable role reference list for API responses."""
        return [
            {
                'code': code,
                'label': label,
                'description': cls.ROLE_DESCRIPTIONS.get(code, ''),
            }
            for code, label in cls.ROLE_CHOICES
        ]
