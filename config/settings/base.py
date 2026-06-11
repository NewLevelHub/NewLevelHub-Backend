"""Base settings for NewLevelHub project."""
from pathlib import Path
from datetime import timedelta
import os

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent.parent
SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-change-this-in-production')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Third-party
    'storages',
    'rest_framework',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    'corsheaders',
    'drf_spectacular',
    'django_filters',
    'django_celery_beat',
    # Local apps
    'apps.core',
    'apps.users',
    'apps.companies',
    'apps.bookings',
    'apps.crm',
    'apps.storage',
    'apps.hr',
    'apps.access',
    'apps.services',
    'apps.notifications',
    'apps.analytics',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'apps.users.middleware.UpdateLastActivityMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('POSTGRES_DB', 'newlevelhub_db'),
        'USER': os.getenv('POSTGRES_USER', 'nlh_user'),
        'PASSWORD': os.getenv('POSTGRES_PASSWORD', 'nlh_password'),
        'HOST': os.getenv('POSTGRES_HOST', 'localhost'),
        'PORT': os.getenv('POSTGRES_PORT', '5432'),
        'ATOMIC_REQUESTS': True,
    }
}

AUTH_USER_MODEL = 'users.User'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'ru'
TIME_ZONE = 'Asia/Almaty'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
_STATIC_SRC = BASE_DIR / 'static'
STATICFILES_DIRS = [_STATIC_SRC] if _STATIC_SRC.exists() else []
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# S3 / MinIO object storage (staging, production, or optional local via docker-compose)
USE_S3 = os.getenv('USE_S3', '').lower() in ('1', 'true', 'yes') or bool(
    (os.getenv('AWS_STORAGE_BUCKET_NAME') or '').strip()
)
AWS_S3_PRESIGNED_URL_EXPIRY = int(os.getenv('AWS_S3_PRESIGNED_URL_EXPIRY', '900'))

if USE_S3:
    AWS_STORAGE_BUCKET_NAME = os.getenv('AWS_STORAGE_BUCKET_NAME', '')
    AWS_S3_REGION_NAME = os.getenv('AWS_S3_REGION_NAME', 'eu-central-1')
    AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID', '')
    AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY', '')
    _s3_endpoint = (os.getenv('AWS_S3_ENDPOINT_URL') or '').strip()
    AWS_S3_ENDPOINT_URL = _s3_endpoint or None
    _addr_style = (os.getenv('AWS_S3_ADDRESSING_STYLE') or '').strip().lower()
    if _addr_style in ('path', 'virtual'):
        AWS_S3_ADDRESSING_STYLE = _addr_style
    elif AWS_S3_ENDPOINT_URL:
        AWS_S3_ADDRESSING_STYLE = 'path'
    AWS_DEFAULT_ACL = None
    AWS_S3_FILE_OVERWRITE = False
    _minio_public = (os.getenv('MINIO_PUBLIC_URL') or '').strip()
    if _minio_public:
        from urllib.parse import urlparse as _urlparse
        _p = _urlparse(_minio_public)
        # Include bucket name so path-style MinIO URLs resolve correctly.
        AWS_S3_CUSTOM_DOMAIN = f"{_p.netloc}/{AWS_STORAGE_BUCKET_NAME}"
        AWS_S3_URL_PROTOCOL = _p.scheme + ':'
        AWS_QUERYSTRING_AUTH = False
    STORAGES = {
        'default': {
            'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage',
        },
        'staticfiles': {
            'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
        },
    }
else:
    STORAGES = {
        'default': {
            'BACKEND': 'django.core.files.storage.FileSystemStorage',
        },
        'staticfiles': {
            'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
        },
    }

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'apps.users.authentication.IdleAwareJWTAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'apps.core.pagination.StandardPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'DEFAULT_RENDERER_CLASSES': ['rest_framework.renderers.JSONRenderer'],
    'DEFAULT_PARSER_CLASSES': [
        'rest_framework.parsers.JSONParser',
        'rest_framework.parsers.FormParser',
        'rest_framework.parsers.MultiPartParser',
    ],
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ],
    'EXCEPTION_HANDLER': 'apps.core.exceptions.custom_exception_handler',
}

ACCESS_TOKEN_LIFETIME = timedelta(minutes=int(os.getenv('ACCESS_TOKEN_LIFETIME', 15)))
REFRESH_TOKEN_LIFETIME = timedelta(days=int(os.getenv('REFRESH_TOKEN_LIFETIME', 7)))
REMEMBER_ME_LIFETIME = timedelta(days=int(os.getenv('REMEMBER_ME_LIFETIME', 30)))
IDLE_SESSION_TIMEOUT = timedelta(minutes=int(os.getenv('IDLE_SESSION_TIMEOUT_MINUTES', 5)))
ABSOLUTE_SESSION_TIMEOUT = timedelta(
    minutes=int(os.getenv('ABSOLUTE_SESSION_TIMEOUT_MINUTES', 15))
)

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': ACCESS_TOKEN_LIFETIME,
    'REFRESH_TOKEN_LIFETIME': REFRESH_TOKEN_LIFETIME,
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'UPDATE_LAST_LOGIN': True,
    'ALGORITHM': 'HS256',
    'SIGNING_KEY': SECRET_KEY,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
    'USER_AUTHENTICATION_RULE': 'apps.users.authentication.active_user_authentication_rule',
}

SPECTACULAR_SETTINGS = {
    'TITLE': 'New Level Hub API',
    'DESCRIPTION': 'Backend API для веб-платформы бизнес-центра нового поколения в Астане',
    'VERSION': '0.1.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'SCHEMA_PATH_PREFIX': '/api/v1/',
    'COMPONENT_SPLIT_REQUEST': True,
    'SERVE_PERMISSIONS': ['rest_framework.permissions.AllowAny'],
    'TAGS': [
        {'name': 'Auth', 'description': 'Регистрация, вход, JWT-токены'},
        {'name': 'Users', 'description': 'Профили пользователей'},
        {'name': 'Companies', 'description': 'Управление компаниями и мультитенанси'},
        {'name': 'Bookings', 'description': 'Бронирование ресурсов БЦ'},
        {'name': 'CRM', 'description': 'Канбан-доски, задачи, проекты'},
        {'name': 'Storage', 'description': 'Файловое хранилище'},
        {'name': 'HR', 'description': 'Отпуска, отгулы, онбординг'},
        {'name': 'Access', 'description': 'Гостевые пропуска и контроль доступа'},
        {'name': 'Services', 'description': 'Сервисы здания — карта, заявки, объявления'},
        {'name': 'Notifications', 'description': 'Уведомления'},
        {'name': 'Analytics', 'description': 'Аналитика и отчёты'},
        {'name': 'System', 'description': 'Health check и системные эндпоинты'},
    ],
    'POSTPROCESSING_HOOKS': [
        'drf_spectacular.hooks.postprocess_schema_enums',
        'apps.core.schema.normalize_operation_tags',
    ],
}

CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv('CORS_ALLOWED_ORIGINS', 'http://localhost:3000').split(',')
    if origin.strip()
]
CORS_ALLOW_ALL_ORIGINS = os.getenv('CORS_ALLOW_ALL_ORIGINS', 'False').lower() in ('1', 'true', 'yes')
CORS_ALLOW_CREDENTIALS = True

# Celery
CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE

from celery.schedules import crontab  # noqa: E402

CELERY_BEAT_SCHEDULE = {
    'send-booking-reminders': {
        'task': 'apps.bookings.tasks.send_booking_reminders',
        'schedule': crontab(minute='*/5'),
    },
    'auto-complete-bookings': {
        'task': 'apps.bookings.tasks.auto_complete_bookings',
        'schedule': crontab(minute='*/5'),
    },
    'mark-no-show-bookings': {
        'task': 'apps.bookings.tasks.mark_no_show_bookings',
        'schedule': crontab(minute='*/5'),
    },
    'notify-crm-deadlines-approaching': {
        'task': 'apps.crm.tasks.notify_deadline_approaching',
        'schedule': crontab(hour=8, minute=0),
    },
    'notify-crm-deadlines-overdue': {
        'task': 'apps.crm.tasks.notify_deadline_overdue',
        'schedule': crontab(hour=8, minute=10),
    },
    'expire-guest-passes': {
        'task': 'apps.access.tasks.expire_guest_passes',
        'schedule': crontab(minute='*/30'),
    },
    'notify-guest-passes-expiring': {
        'task': 'apps.access.tasks.notify_guest_passes_expiring_soon',
        'schedule': crontab(hour=9, minute=0),
    },
    'cleanup-deleted-storage-files': {
        'task': 'apps.storage.tasks.cleanup_deleted_files',
        'schedule': crontab(hour=3, minute=0),
    },
    'generate-recurring-bookings': {
        'task': 'apps.bookings.tasks.generate_recurring_bookings',
        'schedule': crontab(hour=0, minute=30, day_of_week=1),
    },
}

# Email
EMAIL_BACKEND = os.getenv('EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')
EMAIL_HOST = os.getenv('EMAIL_HOST', 'localhost')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', 25))
EMAIL_USE_SSL = os.getenv('EMAIL_USE_SSL', 'False') == 'True'
EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', 'False').lower() in ('1', 'true', 'yes')
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'noreply@newlevelhub.kz')
FRONTEND_URL = os.getenv('FRONTEND_URL', 'http://localhost:3000')
# Public API origin for email image/link URLs (Celery). Example: https://api.staging.newlevelhub.kz
BACKEND_URL = (os.getenv('BACKEND_URL') or '').strip() or FRONTEND_URL
MAX_ACTIVE_BOOKINGS_PER_USER = int(os.getenv('MAX_ACTIVE_BOOKINGS_PER_USER', 5))
REMINDER_MINUTES_BEFORE = int(os.getenv('REMINDER_MINUTES_BEFORE', 15))
NO_SHOW_MINUTES = int(os.getenv('NO_SHOW_MINUTES', 15))
SOON_AVAILABLE_MINUTES = int(os.getenv('SOON_AVAILABLE_MINUTES', 15))

# File upload limits (defaults to 100 MB for preview/staging uploads).
DEFAULT_UPLOAD_LIMIT_MB = int(os.getenv('UPLOAD_MAX_SIZE_MB', 100))
DEFAULT_UPLOAD_LIMIT_BYTES = DEFAULT_UPLOAD_LIMIT_MB * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv('FILE_UPLOAD_MAX_MEMORY_SIZE', DEFAULT_UPLOAD_LIMIT_BYTES))
DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv('DATA_UPLOAD_MAX_MEMORY_SIZE', DEFAULT_UPLOAD_LIMIT_BYTES))

# Personal storage quota for users with role='guest' (no company).
GUEST_STORAGE_LIMIT_GB = int(os.getenv('GUEST_STORAGE_LIMIT_GB', 1))

SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
