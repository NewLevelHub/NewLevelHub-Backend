from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from .models import File


@shared_task
def cleanup_deleted_files():
    cutoff = timezone.now() - timedelta(days=30)
    stale_files = File.all_objects.filter(is_deleted=True, deleted_at__lte=cutoff)

    deleted_count = 0
    for file_obj in stale_files.iterator():
        if file_obj.file:
            file_obj.file.delete(save=False)
        file_obj.delete()
        deleted_count += 1

    return deleted_count
