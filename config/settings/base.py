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

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Almaty'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
_STATIC_SRC = BASE_DIR / 'static'
STATICFILES_DIRS = [_STATIC_SRC] if _STATIC_SRC.exists() else []
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
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

CORS_ALLOWED_ORIGINS = [origin.strip() for origin in os.getenv('CORS_ALLOWED_ORIGINS', 'http://localhost:3000').split(',') if origin.strip()]
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
    'cleanup-deleted-storage-files': {
        'task': 'apps.storage.tasks.cleanup_deleted_files',
        'schedule': crontab(hour=3, minute=0),
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
MAX_ACTIVE_BOOKINGS_PER_USER = int(os.getenv('MAX_ACTIVE_BOOKINGS_PER_USER', 5))
REMINDER_MINUTES_BEFORE = int(os.getenv('REMINDER_MINUTES_BEFORE', 15))
NO_SHOW_MINUTES = int(os.getenv('NO_SHOW_MINUTES', 15))

# File upload limits (defaults to 100 MB for preview/staging uploads).
DEFAULT_UPLOAD_LIMIT_MB = int(os.getenv('UPLOAD_MAX_SIZE_MB', 100))
DEFAULT_UPLOAD_LIMIT_BYTES = DEFAULT_UPLOAD_LIMIT_MB * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv('FILE_UPLOAD_MAX_MEMORY_SIZE', DEFAULT_UPLOAD_LIMIT_BYTES))
DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv('DATA_UPLOAD_MAX_MEMORY_SIZE', DEFAULT_UPLOAD_LIMIT_BYTES))

SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
