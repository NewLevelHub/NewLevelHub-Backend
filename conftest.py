import os

import pytest
from django.conf import settings


@pytest.fixture(scope='session')
def django_db_setup():
    host = os.environ.get('POSTGRES_HOST', 'db')
    # Mutate in-place so Django's ConnectionHandler picks up the changes.
    settings.DATABASES['default'].update({
        'NAME': 'test_newlevelhub',
        'USER': 'test_user',
        'PASSWORD': 'test_password',
        'HOST': host,
        'PORT': '5432',
    })
