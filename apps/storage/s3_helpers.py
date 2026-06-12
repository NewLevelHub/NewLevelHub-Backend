"""S3/object-storage helpers: presigned URLs and safe deletion with retry."""
import logging
import os
import time
import urllib.parse

from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings

try:
    from storages.backends.s3boto3 import S3Boto3Storage
except ImportError:  # pragma: no cover
    S3Boto3Storage = ()  # type: ignore

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


def presigned_get_url_for_fieldfile(field_file, filename=None):
    """
    Return (url, expires_in_seconds) for downloading via FieldFile.

    S3: presigned GET with optional Content-Disposition so browsers use the
    original filename instead of the hashed S3 key.
    Local FileSystemStorage: public MEDIA URL; expires_in matches settings
    for a stable API shape.
    """
    if not field_file or not getattr(field_file, 'name', None):
        return None, None

    storage = field_file.storage
    name = field_file.name
    expires_in = int(getattr(settings, 'AWS_S3_PRESIGNED_URL_EXPIRY', 900))

    if isinstance(storage, S3Boto3Storage):
        parameters = {}
        if filename:
            encoded = urllib.parse.quote(filename, safe='')
            parameters['ResponseContentDisposition'] = (
                f'attachment; filename="{filename}"; filename*=UTF-8\'\'{encoded}'
            )
        url = storage.url(name, expire=expires_in, parameters=parameters)
        return url, expires_in

    url = storage.url(name)
    return url, expires_in
