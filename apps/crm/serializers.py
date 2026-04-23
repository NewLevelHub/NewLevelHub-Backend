import re

from rest_framework import serializers
from django.contrib.auth import get_user_model

from .models import Board, Column, Label, Task, Checklist, ChecklistItem, Comment, TaskAttachment, TaskHistory

User = get_user_model()


class AssigneeSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'first_name', 'last_name', 'avatar']


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
    is_completed = serializers.BooleanField(source='is_done', read_only=True)
    order = serializers.IntegerField(source='position', read_only=True)

    class Meta:
        model = ChecklistItem
        fields = ['id', 'text', 'is_completed', 'order']
        read_only_fields = ['id', 'is_completed', 'order']


class ChecklistItemWriteSerializer(serializers.ModelSerializer):
    """Used for PATCH on a single item — all fields optional, order >= 1."""
    is_completed = serializers.BooleanField(source='is_done', required=False)
    order = serializers.IntegerField(source='position', required=False, min_value=1)

    class Meta:
        model = ChecklistItem
        fields = ['text', 'is_completed', 'order']


class ChecklistSerializer(serializers.ModelSerializer):
    items = ChecklistItemSerializer(many=True, read_only=True)
    checklist_progress = serializers.SerializerMethodField()

    class Meta:
        model = Checklist
        fields = ['id', 'title', 'items', 'checklist_progress']
        read_only_fields = ['id']

    def get_checklist_progress(self, obj):
        all_items = list(obj.items.all())
        total = len(all_items)
        completed = sum(1 for item in all_items if item.is_done)
        return {'total': total, 'completed': completed}


class CommentAuthorSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'full_name', 'avatar']

    def get_full_name(self, obj):
        return obj.full_name


class CommentSerializer(serializers.ModelSerializer):
    author = CommentAuthorSerializer(read_only=True)

    class Meta:
        model = Comment
        fields = ['id', 'text', 'author', 'created_at']
        read_only_fields = ['id', 'author', 'created_at']


class TaskAttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskAttachment
        fields = ['id', 'file', 'filename', 'file_size', 'uploaded_by', 'created_at']
        read_only_fields = ['id', 'uploaded_by', 'file_size', 'created_at']


class TaskHistoryUserSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'full_name', 'avatar']

    def get_full_name(self, obj):
        return obj.full_name


class TaskHistorySerializer(serializers.ModelSerializer):
    user = TaskHistoryUserSerializer(read_only=True)

    class Meta:
        model = TaskHistory
        fields = ['id', 'user', 'action', 'old_value', 'new_value', 'created_at']


class TaskSerializer(serializers.ModelSerializer):
    """
    List serializer — lightweight, no nested history.
    Used for list action and as the base for writes.
    """
    label_ids = serializers.PrimaryKeyRelatedField(
        queryset=Label.objects.all(), many=True, source='labels', required=False,
    )
    assignee_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), source='assignee', required=False, allow_null=True,
        write_only=True,
    )
    assignee = AssigneeSerializer(read_only=True)
    column_id = serializers.PrimaryKeyRelatedField(
        queryset=Column.objects.all(), source='column',
    )
    board_id = serializers.IntegerField(write_only=True)
    checklists = ChecklistSerializer(many=True, read_only=True)
    comments_count = serializers.IntegerField(source='comments.count', read_only=True)
    attachments_count = serializers.IntegerField(source='attachments.count', read_only=True)

    class Meta:
        model = Task
        fields = [
            'id', 'column_id', 'board_id', 'title', 'description', 'priority', 'position',
            'assignee_id', 'assignee', 'created_by', 'deadline',
            'label_ids', 'labels', 'is_archived',
            'checklists', 'comments_count', 'attachments_count',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_by', 'position', 'is_archived', 'created_at', 'updated_at']

    def _get_company(self):
        request = self.context.get('request')
        if request and hasattr(request, 'user'):
            return request.user.company if request.user.role != 'superadmin' else None
        return None

    def validate(self, attrs):
        request = self.context.get('request')
        user = request.user if request else None
        company = user.company if (user and user.role != 'superadmin') else None

        board_id = attrs.pop('board_id', None)

        # On create, board_id is required to validate column ownership.
        # On update (partial), board_id may be absent; derive it from the instance.
        instance = getattr(self, 'instance', None)

        if board_id is not None:
            # Validate board belongs to company
            from .models import Board as BoardModel
            board_qs = BoardModel.objects.filter(pk=board_id)
            if company:
                board_qs = board_qs.filter(company=company)
            if not board_qs.exists():
                raise serializers.ValidationError({'board_id': 'Board not found or does not belong to your company.'})
            self._validated_board_id = board_id
        elif instance is not None:
            self._validated_board_id = instance.column.board_id
        else:
            raise serializers.ValidationError({'board_id': 'This field is required.'})

        # Validate column belongs to board
        column = attrs.get('column')
        if column is not None:
            if column.board_id != self._validated_board_id:
                raise serializers.ValidationError({'column_id': 'Column does not belong to the specified board.'})

        # Validate assignee belongs to same company
        assignee = attrs.get('assignee')
        if assignee is not None:
            if company and assignee.company_id != company.id:
                raise serializers.ValidationError(
                    {'assignee_id': 'Assignee must be an employee of the same company.'}
                )

        # Validate labels belong to the board's company.
        # Superadmin has company=None and is intentionally allowed to attach any label.
        labels = attrs.get('labels')
        if labels and company is not None:
            invalid = [lb for lb in labels if lb.company_id != company.id]
            if invalid:
                raise serializers.ValidationError(
                    {'label_ids': 'All labels must belong to your company.'}
                )

        return attrs

    def create(self, validated_data):
        labels = validated_data.pop('labels', [])
        task = Task.objects.create(**validated_data)
        if labels:
            task.labels.set(labels)
        return task

    def update(self, instance, validated_data):
        labels = validated_data.pop('labels', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if labels is not None:
            instance.labels.set(labels)
        return instance


class TaskDetailSerializer(TaskSerializer):
    """
    Detail serializer — adds nested history (last 10 entries).
    Used for retrieve action only.
    """
    history = serializers.SerializerMethodField()

    class Meta(TaskSerializer.Meta):
        fields = TaskSerializer.Meta.fields + ['history']

    def get_history(self, obj):
        entries = obj.history.all()[:10]
        return TaskHistorySerializer(entries, many=True).data


class TaskMoveSerializer(serializers.Serializer):
    """Перемещение задачи между колонками / изменение позиции."""
    column_id = serializers.PrimaryKeyRelatedField(queryset=Column.objects.all())
    position = serializers.IntegerField(required=False, allow_null=True, min_value=1)


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
    position = serializers.IntegerField(required=False, allow_null=True, min_value=1)

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
