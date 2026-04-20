from django.contrib import admin, messages
from .models import Board, Column, Label, Task, Checklist, ChecklistItem, Comment, TaskAttachment, TaskHistory


@admin.register(Board)
class BoardAdmin(admin.ModelAdmin):
    list_display = ['name', 'company', 'is_archived', 'created_by', 'created_at']
    list_filter = ['is_archived', 'company']

    def save_model(self, request, obj, form, change):
        if change:
            original = Board.objects.get(pk=obj.pk)
            if original.is_archived and not obj.is_archived:
                active_count = obj.company.boards.filter(is_archived=False).exclude(pk=obj.pk).count()
                if active_count >= obj.company.max_boards:
                    self.message_user(
                        request,
                        (
                            f"Невозможно разархивировать: достигнут лимит досок "
                            f"({obj.company.max_boards}) для компании «{obj.company.name}»."
                        ),
                        level=messages.ERROR,
                    )
                    return
        super().save_model(request, obj, form, change)


class ChecklistItemInline(admin.TabularInline):
    model = ChecklistItem
    extra = 0


@admin.register(Column)
class ColumnAdmin(admin.ModelAdmin):
    list_display = ['name', 'board', 'position', 'wip_limit']


@admin.register(Label)
class LabelAdmin(admin.ModelAdmin):
    list_display = ['name', 'color', 'company']


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ['title', 'column', 'priority', 'assignee', 'deadline', 'is_archived']
    list_filter = ['priority', 'is_archived']
    search_fields = ['title']


@admin.register(Checklist)
class ChecklistAdmin(admin.ModelAdmin):
    list_display = ['title', 'task']
    inlines = [ChecklistItemInline]


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ['task', 'author', 'created_at']


@admin.register(TaskAttachment)
class TaskAttachmentAdmin(admin.ModelAdmin):
    list_display = ['filename', 'task', 'uploaded_by', 'created_at']


@admin.register(TaskHistory)
class TaskHistoryAdmin(admin.ModelAdmin):
    list_display = ['task', 'user', 'action', 'old_value', 'new_value', 'created_at']
