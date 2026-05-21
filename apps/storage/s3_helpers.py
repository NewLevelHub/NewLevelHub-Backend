"""Presigned GET URLs for private object storage."""
from django.conf import settings

try:
    from storages.backends.s3boto3 import S3Boto3Storage
except ImportError:  # pragma: no cover
    S3Boto3Storage = ()  # type: ignore


def presigned_get_url_for_fieldfile(field_file):
    """
    Return (url, expires_in_seconds) for downloading via FieldFile.

    S3: presigned GET. Local FileSystemStorage: public MEDIA URL; expires_in
    matches settings for a stable API shape.
    """
    if not field_file or not getattr(field_file, 'name', None):
        return None, None

    storage = field_file.storage
    name = field_file.name
    expires_in = int(getattr(settings, 'AWS_S3_PRESIGNED_URL_EXPIRY', 900))

    if isinstance(storage, S3Boto3Storage):
        url = storage.url(name, expire=expires_in)
        return url, expires_in

    url = storage.url(name)
    return url, expires_in
