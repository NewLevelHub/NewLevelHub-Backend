import logging
import os
import time
from datetime import timedelta

from botocore.exceptions import BotoCoreError, ClientError
from celery import shared_task
from django.utils import timezone

from .models import File, Folder

logger = logging.getLogger(__name__)


def _capture_sentry_exception(exc):
    dsn = os.getenv('SENTRY_DSN', '').strip()
    if not dsn:
        return
    try:
        import sentry_sdk

        sentry_sdk.capture_exception(exc)
    except Exception:  # pragma: no cover
        logger.exception('Failed to report exception to Sentry')


def _delete_fieldfile_with_retry(file_field, *, max_attempts=3):
    """Delete underlying storage object with exponential backoff (max 3 attempts)."""
    if not file_field or not getattr(file_field, 'name', None):
        return
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            file_field.delete(save=False)
            return
        except (BotoCoreError, ClientError, OSError) as exc:
            last_error = exc
            logger.warning(
                'storage.delete attempt %s/%s failed for %s: %s',
                attempt,
                max_attempts,
                getattr(file_field, 'name', ''),
                exc,
            )
            if attempt < max_attempts:
                time.sleep(2 ** (attempt - 1))
    if last_error is not None:
        _capture_sentry_exception(last_error)
        logger.exception('storage.delete failed after %s attempts', max_attempts)
        raise last_error


@shared_task
def cleanup_deleted_files():
    cutoff = timezone.now() - timedelta(days=30)
    stale_files = File.all_objects.filter(is_deleted=True, deleted_at__lte=cutoff)

    deleted_count = 0
    for file_obj in stale_files.iterator():
        if file_obj.file:
            _delete_fieldfile_with_retry(file_obj.file)
        file_obj.delete()
        deleted_count += 1

    # Delete soft-deleted folders after files to avoid FK constraint issues.
    folder_count, _ = Folder.all_objects.filter(is_deleted=True, deleted_at__lte=cutoff).delete()
    deleted_count += folder_count

    return deleted_count
