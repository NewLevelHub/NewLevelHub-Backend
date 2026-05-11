from django.conf import settings
from storages.backends.s3boto3 import S3Boto3Storage


class MinIOStorage(S3Boto3Storage):
    """S3Boto3Storage with public URL rewriting for MinIO.

    boto3 uses AWS_S3_ENDPOINT_URL (internal Docker hostname) to upload files.
    Browsers need the public-facing URL instead. This class swaps the host in
    every generated URL so the browser can reach the file directly.
    """

    def url(self, name, parameters=None, expire=None, http_method=None):
        url = super().url(name, parameters, expire, http_method)
        internal = getattr(settings, 'AWS_S3_ENDPOINT_URL', '')
        public = getattr(settings, 'MINIO_PUBLIC_URL', '')
        if internal and public and internal != public:
            url = url.replace(internal, public, 1)
        return url
