import pytest
from django.conf import settings


@pytest.fixture(scope='session')
def django_db_setup():
    """Configure test database."""
    settings.DATABASES['default'] = {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'test_db',
        'USER': 'test_user',
        'PASSWORD': 'test_password',
        'HOST': 'localhost',
        'PORT': '5432',
        # DRF/Django use settings.DATABASES[*]['ATOMIC_REQUESTS'] inside request handling.
        # If this fixture fully overrides DATABASES without it, tests can crash with KeyError.
        'ATOMIC_REQUESTS': True,
    }
