import os
import time

import pytest
from django.conf import settings


@pytest.fixture(autouse=True)
def celery_eager(settings):
    """Run Celery tasks synchronously in tests (no broker required)."""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True


@pytest.fixture(scope='session')
def django_db_setup(worker_id):
    host = os.environ.get('POSTGRES_HOST', 'db')
    base_name = os.environ.get('POSTGRES_DB', 'test_newlevelhub')
    user = os.environ.get('POSTGRES_USER', 'test_user')
    password = os.environ.get('POSTGRES_PASSWORD', 'test_password')
    port = os.environ.get('POSTGRES_PORT', '5432')

    # When running under pytest-xdist each worker gets its own database cloned
    # from the migrated base, preventing unique-constraint deadlocks between workers.
    if worker_id != 'master':
        db_name = f'{base_name}_{worker_id}'
        _clone_database(
            template=base_name, target=db_name,
            user=user, password=password, host=host, port=port,
        )
    else:
        db_name = base_name

    # Mutate in-place so Django's ConnectionHandler picks up the changes.
    settings.DATABASES['default'].update({
        'NAME': db_name,
        'USER': user,
        'PASSWORD': password,
        'HOST': host,
        'PORT': port,
    })


def _clone_database(template, target, user, password, host, port):
    """Create *target* as a fresh copy of *template*.

    Retries when the template is briefly locked by a sibling worker that is
    also cloning from it (PostgreSQL requires no active connections on the
    template while CREATE DATABASE runs).
    """
    import psycopg2
    from django.db import connections

    last_exc = None
    for attempt in range(10):
        conn = psycopg2.connect(
            dbname='template1',
            user=user,
            password=password,
            host=host,
            port=int(port),
        )
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute(f'DROP DATABASE IF EXISTS "{target}"')
                cur.execute(f'CREATE DATABASE "{target}" TEMPLATE "{template}"')
            connections.close_all()
            return
        except psycopg2.Error as exc:
            last_exc = exc
            if 'is being accessed by other users' in str(exc):
                time.sleep(0.3 * (attempt + 1))  # back off and retry
            else:
                raise
        finally:
            conn.close()

    raise RuntimeError(
        f'Could not clone {template!r} → {target!r} after 10 attempts: {last_exc}'
    )
