from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from .models import File, Folder
from .s3_helpers import _delete_fieldfile_with_retry


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
