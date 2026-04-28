from django.conf import settings
from django.db import models
from apps.core.models import TimeStampedModel, SoftDeleteModel


class Board(TimeStampedModel):
    company = models.ForeignKey('companies.Company', on_delete=models.CASCADE, related_name='boards')
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default='')
    is_archived = models.BooleanField(default=False, db_index=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)

    class Meta:
        db_table = 'crm_boards'

    def __str__(self):
        return f'{self.name} ({self.company.name})'


class Column(TimeStampedModel):
    board = models.ForeignKey(Board, on_delete=models.CASCADE, related_name='columns')
    name = models.CharField(max_length=255)
    position = models.PositiveIntegerField(default=0)
    wip_limit = models.PositiveIntegerField(default=0, help_text='0 = no limit')

    class Meta:
        db_table = 'crm_columns'
        ordering = ['position']

    def __str__(self):
        return self.name


class Label(TimeStampedModel):
    company = models.ForeignKey('companies.Company', on_delete=models.CASCADE, related_name='labels')
    name = models.CharField(max_length=100)
    color = models.CharField(max_length=7, default='#6366f1')

    class Meta:
        db_table = 'crm_labels'
        constraints = [
            models.UniqueConstraint(
                fields=['company', 'name'],
                name='unique_label_per_company',
            ),
        ]

    def __str__(self):
        return self.name


class Task(TimeStampedModel, SoftDeleteModel):
    PRIORITY_CHOICES = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('urgent', 'Urgent'),
    ]

    column = models.ForeignKey(Column, on_delete=models.CASCADE, related_name='tasks')
    title = models.CharField(max_length=500)
    description = models.TextField(blank=True, default='')
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='medium')
    position = models.PositiveIntegerField(default=0)

    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='assigned_tasks',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, related_name='created_tasks',
    )

    deadline = models.DateTimeField(null=True, blank=True, db_index=True)
    labels = models.ManyToManyField(Label, blank=True, related_name='tasks')
    is_archived = models.BooleanField(default=False)

    class Meta:
        db_table = 'crm_tasks'
        ordering = ['position']

    def __str__(self):
        return self.title


class Checklist(TimeStampedModel):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='checklists')
    title = models.CharField(max_length=255)

    class Meta:
        db_table = 'crm_checklists'


class ChecklistItem(TimeStampedModel):
    checklist = models.ForeignKey(Checklist, on_delete=models.CASCADE, related_name='items')
    text = models.CharField(max_length=500)
    is_done = models.BooleanField(default=False)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'crm_checklist_items'
        ordering = ['position']


class Comment(TimeStampedModel):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    text = models.TextField()

    class Meta:
        db_table = 'crm_comments'
        ordering = ['created_at']

    @property
    def company(self):
        """Proxy company from the parent task's board, used by IsOwnerOrAdmin."""
        return self.task.column.board.company

    @property
    def company_id(self):
        return self.task.column.board.company_id


def _task_attachment_upload_path(instance, filename):
    return f'task_attachments/{instance.task_id}/{filename}'


class TaskAttachment(TimeStampedModel):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='attachments')
    file = models.FileField(upload_to=_task_attachment_upload_path, null=True, blank=True)
    storage_file = models.ForeignKey(
        'storage.File', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='task_attachments',
    )
    filename = models.CharField(max_length=255)
    file_size = models.PositiveIntegerField(default=0)
    mime_type = models.CharField(max_length=100, blank=True, default='')
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)

    class Meta:
        db_table = 'crm_task_attachments'

    @property
    def company(self):
        """Proxy company from the parent task's board — used by IsOwnerOrAdmin."""
        return self.task.column.board.company

    @property
    def company_id(self):
        return self.task.column.board.company_id


class TaskHistory(TimeStampedModel):
    """Лог изменений задачи: перемещение, смена приоритета и т.д."""
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='history')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    action = models.CharField(max_length=50)
    old_value = models.CharField(max_length=255, blank=True, default='')
    new_value = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        db_table = 'crm_task_history'
        ordering = ['-created_at']
