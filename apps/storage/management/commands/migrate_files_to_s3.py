"""
Copy existing local media files into the configured default storage (S3/MinIO).

Idempotent: skips rows whose file name already uses the new key prefix and exists
in default storage. Does not delete local originals (remove manually after verification).
"""
from pathlib import PurePath

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage, default_storage
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.crm.models import TaskAttachment
from apps.storage.models import File as StorageFile
from apps.storage.upload_paths import company_storage_file_upload_to, task_attachment_upload_to


def _is_new_key_layout(name):
    if not name:
        return False
    return name.startswith('companies/') or name.startswith('tasks/')


class Command(BaseCommand):
    help = 'Copy local media files into S3-backed default storage (see DEV-141).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Log actions without writing to remote storage or updating the database.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        if not getattr(settings, 'USE_S3', False):
            self.stderr.write(self.style.ERROR('USE_S3 is disabled or AWS_STORAGE_BUCKET_NAME is not set.'))
            return

        legacy = FileSystemStorage(location=settings.MEDIA_ROOT, base_url=settings.MEDIA_URL)
        migrated_files = 0
        skipped_files = 0
        failed_files = 0
        migrated_attachments = 0
        skipped_attachments = 0
        failed_attachments = 0

        for file_obj in StorageFile.all_objects.exclude(file='').iterator():
            old_name = file_obj.file.name
            msg_prefix = f'StorageFile id={file_obj.pk} name={old_name!r}'

            if _is_new_key_layout(old_name) and default_storage.exists(old_name):
                self.stdout.write(f'{msg_prefix} -> skip (already on S3 layout)')
                skipped_files += 1
                continue

            if not legacy.exists(old_name):
                self.stderr.write(self.style.WARNING(f'{msg_prefix} -> missing on local disk, skip'))
                failed_files += 1
                continue

            dest_name = company_storage_file_upload_to(file_obj, PurePath(old_name).name)
            if dry_run:
                self.stdout.write(f'{msg_prefix} -> would copy to {dest_name!r}')
            else:
                self.stdout.write(f'{msg_prefix} -> {dest_name!r}')

            if dry_run:
                migrated_files += 1
                continue

            with legacy.open(old_name, 'rb') as src:
                data = src.read()
            stored_name = default_storage.save(
                dest_name,
                ContentFile(data, name=PurePath(dest_name).name),
            )
            StorageFile.all_objects.filter(pk=file_obj.pk).update(
                file=stored_name,
                updated_at=timezone.now(),
            )
            migrated_files += 1

        for att in TaskAttachment.objects.exclude(file='').iterator():
            old_name = att.file.name
            msg_prefix = f'TaskAttachment id={att.pk} name={old_name!r}'

            if _is_new_key_layout(old_name) and default_storage.exists(old_name):
                self.stdout.write(f'{msg_prefix} -> skip (already on S3 layout)')
                skipped_attachments += 1
                continue

            if not legacy.exists(old_name):
                self.stderr.write(self.style.WARNING(f'{msg_prefix} -> missing on local disk, skip'))
                failed_attachments += 1
                continue

            dest_name = task_attachment_upload_to(att, PurePath(old_name).name)
            if dry_run:
                self.stdout.write(f'{msg_prefix} -> would copy to {dest_name!r}')
            else:
                self.stdout.write(f'{msg_prefix} -> {dest_name!r}')

            if dry_run:
                migrated_attachments += 1
                continue

            with legacy.open(old_name, 'rb') as src:
                data = src.read()
            stored_name = default_storage.save(
                dest_name,
                ContentFile(data, name=PurePath(dest_name).name),
            )
            TaskAttachment.objects.filter(pk=att.pk).update(
                file=stored_name,
                updated_at=timezone.now(),
            )
            migrated_attachments += 1

        summary = (
            f'Done. storage_files: migrated={migrated_files}, skipped={skipped_files}, '
            f'missing_local={failed_files}; attachments: migrated={migrated_attachments}, '
            f'skipped={skipped_attachments}, missing_local={failed_attachments}; '
            f'dry_run={dry_run}'
        )
        self.stdout.write(self.style.NOTICE(summary))
