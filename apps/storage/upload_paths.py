"""S3 object key layout for storage files and CRM task attachments."""
import uuid
from pathlib import PurePath

from django.utils import timezone


def company_storage_file_upload_to(instance, filename):
    """
    companies/{company_id}/files/{year}/{month}/{uuid}_{filename}

    company_id is None for personal files — use 'unscoped' segment.
    """
    safe_name = PurePath(filename).name
    key = f'{uuid.uuid4().hex}_{safe_name}'
    company_id = getattr(instance, 'company_id', None)
    cid = company_id if company_id is not None else 'unscoped'
    now = timezone.now()
    return f'companies/{cid}/files/{now.year}/{now.month:02d}/{key}'


def task_attachment_upload_to(instance, filename):
    """tasks/{task_id}/attachments/{uuid}_{filename}"""
    safe_name = PurePath(filename).name
    key = f'{uuid.uuid4().hex}_{safe_name}'
    task_id = getattr(instance, 'task_id', None)
    if task_id is None and getattr(instance, 'task', None) is not None:
        task_id = instance.task.pk
    if task_id is None:
        task_id = 'pending'
    return f'tasks/{task_id}/attachments/{key}'
