import re

from rest_framework import serializers
from .models import Board, Column, Label, Task, Checklist, ChecklistItem, Comment, TaskAttachment, TaskHistory


class LabelSerializer(serializers.ModelSerializer):
    class Meta:
        model = Label
        fields = ['id', 'name', 'color', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_color(self, value):
        if not re.match(r'^#[0-9a-fA-F]{6}$', value):
            raise serializers.ValidationError('Color must be a valid hex code, e.g. #ff00aa.')
        return value.lower()

    def validate(self, attrs):
        company = self.context['request'].user.company
        name = attrs.get('name', getattr(self.instance, 'name', None))
        qs = Label.objects.filter(company=company, name__iexact=name)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError({'name': 'Label with this name already exists in your company.'})
        return attrs


class ChecklistItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChecklistItem
        fields = ['id', 'text', 'is_done', 'position']
        read_only_fields = ['id']


class ChecklistSerializer(serializers.ModelSerializer):
    items = ChecklistItemSerializer(many=True, read_only=True)

    class Meta:
        model = Checklist
        fields = ['id', 'title', 'items']
        read_only_fields = ['id']


class CommentSerializer(serializers.ModelSerializer):
    author_name = serializers.CharField(source='author.full_name', read_only=True)

    class Meta:
        model = Comment
        fields = ['id', 'author', 'author_name', 'text', 'created_at']
        read_only_fields = ['id', 'author', 'created_at']


class TaskAttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskAttachment
        fields = ['id', 'file', 'filename', 'file_size', 'uploaded_by', 'created_at']
        read_only_fields = ['id', 'uploaded_by', 'file_size', 'created_at']


class TaskHistorySerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source='user.full_name', read_only=True)

    class Meta:
        model = TaskHistory
        fields = ['id', 'user', 'user_name', 'action', 'old_value', 'new_value', 'created_at']


class TaskSerializer(serializers.ModelSerializer):
    label_ids = serializers.PrimaryKeyRelatedField(
        queryset=Label.objects.all(), many=True, source='labels', required=False,
    )
    checklists = ChecklistSerializer(many=True, read_only=True)
    comments_count = serializers.IntegerField(source='comments.count', read_only=True)
    attachments_count = serializers.IntegerField(source='attachments.count', read_only=True)
    assignee_name = serializers.CharField(source='assignee.full_name', read_only=True, default=None)

    class Meta:
        model = Task
        fields = [
            'id', 'column', 'title', 'description', 'priority', 'position',
            'assignee', 'assignee_name', 'created_by', 'deadline',
            'label_ids', 'labels', 'is_archived',
            'checklists', 'comments_count', 'attachments_count',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_by', 'created_at', 'updated_at']


class TaskMoveSerializer(serializers.Serializer):
    """Перемещение задачи между колонками / изменение позиции."""
    column_id = serializers.IntegerField()
    position = serializers.IntegerField()


class ColumnSerializer(serializers.ModelSerializer):
    tasks = TaskSerializer(many=True, read_only=True)
    task_count = serializers.IntegerField(source='tasks.count', read_only=True)
    board = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Column
        fields = ['id', 'name', 'position', 'wip_limit', 'board', 'tasks', 'task_count', 'created_at']
        read_only_fields = ['id', 'board', 'created_at']


class ColumnWriteSerializer(serializers.ModelSerializer):
    """Used for create / partial_update — no nested task data."""
    wip_limit = serializers.IntegerField(required=False, allow_null=True, min_value=0, default=0)
    position = serializers.IntegerField(required=False, min_value=1)

    class Meta:
        model = Column
        fields = ['id', 'name', 'position', 'wip_limit', 'board', 'created_at']
        read_only_fields = ['id', 'board', 'created_at']


class ColumnReorderSerializer(serializers.Serializer):
    """Validates the ordered list of column IDs for a board reorder operation."""
    column_ids = serializers.ListField(
        child=serializers.IntegerField(),
        allow_empty=False,
        error_messages={'empty': 'column_ids must not be empty.'},
    )


class BoardSerializer(serializers.ModelSerializer):
    columns = ColumnSerializer(many=True, read_only=True)

    class Meta:
        model = Board
        fields = [
            'id', 'name', 'description', 'is_archived', 'company',
            'created_by', 'columns', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'company', 'created_by', 'created_at', 'updated_at']


class BoardListSerializer(serializers.ModelSerializer):
    column_count = serializers.IntegerField(source='columns.count', read_only=True)
    task_count = serializers.SerializerMethodField()

    class Meta:
        model = Board
        fields = [
            'id', 'name', 'description', 'is_archived', 'company',
            'column_count', 'task_count', 'created_at',
        ]
        read_only_fields = ['id', 'company', 'created_at']

    def get_task_count(self, obj):
        return Task.objects.filter(column__board=obj, is_deleted=False).count()
