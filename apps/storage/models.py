from django.conf import settings
from django.db import models
from apps.core.models import TimeStampedModel, SoftDeleteModel
from apps.storage.upload_paths import company_storage_file_upload_to


class Folder(TimeStampedModel, SoftDeleteModel):
    SCOPE_CHOICES = [
        ('personal', 'Personal'),
        ('company', 'Company'),
    ]

    name = models.CharField(max_length=255)
    scope = models.CharField(max_length=10, choices=SCOPE_CHOICES, default='personal')
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='folders')
    company = models.ForeignKey(
        'companies.Company', on_delete=models.CASCADE,
        null=True, blank=True, related_name='folders',
    )
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='children')

    class Meta:
        db_table = 'storage_folders'

    def __str__(self):
        return self.name


class File(TimeStampedModel, SoftDeleteModel):
    name = models.CharField(max_length=255)
    file = models.FileField(upload_to=company_storage_file_upload_to)
    file_size = models.PositiveBigIntegerField(default=0)
    content_type = models.CharField(max_length=100, blank=True, default='')

    folder = models.ForeignKey(Folder, on_delete=models.CASCADE, null=True, blank=True, related_name='files')
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='files')
    company = models.ForeignKey(
        'companies.Company', on_delete=models.CASCADE,
        null=True, blank=True, related_name='files',
    )

    class Meta:
        db_table = 'storage_files'

    def __str__(self):
        return self.name


class FileShare(TimeStampedModel):
    PERMISSION_CHOICES = [
        ('view', 'View only'),
        ('download', 'View + Download'),
        ('full', 'Full access'),
    ]

    file = models.ForeignKey(File, on_delete=models.CASCADE, related_name='shares')
    shared_with = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='shared_files')
    shared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='sent_shares')
    permission = models.CharField(max_length=10, choices=PERMISSION_CHOICES, default='view')
    comment = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'storage_file_shares'
        unique_together = ['file', 'shared_with']
