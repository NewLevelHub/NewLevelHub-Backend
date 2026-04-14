import os

import pytest
from django.conf import settings


@pytest.fixture(scope='session')
def django_db_setup():
    host = os.environ.get('POSTGRES_HOST', 'db')
    # Mutate in-place so Django's ConnectionHandler picks up the changes.
    settings.DATABASES['default'].update({
        'NAME': os.environ.get('POSTGRES_DB', 'test_newlevelhub'),
        'USER': os.environ.get('POSTGRES_USER', 'test_user'),
        'PASSWORD': os.environ.get('POSTGRES_PASSWORD', 'test_password'),
        'HOST': host,
        'PORT': os.environ.get('POSTGRES_PORT', '5432'),
    })
