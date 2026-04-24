from django.db import models
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, NotFound, ValidationError
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.response import Response
from rest_framework import serializers as drf_serializers
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import (
    extend_schema, extend_schema_view,
    OpenApiParameter, OpenApiExample, OpenApiResponse, inline_serializer,
)

from apps.companies.limits import notify_company_admins_limit_thresholds
from apps.core.pagination import StandardPagination
from apps.core.permissions import IsCompanyAdmin, IsCompanyMember, IsEmailVerifiedOrSuperAdmin, IsOwnerOrAdmin
from apps.core.mixins import CompanyIsolationMixin, SetCompanyOnCreateMixin
from apps.notifications.models import Notification
from .models import Board, Column, Label, Task, Comment, TaskHistory, Checklist, ChecklistItem
from .serializers import (
    BoardSerializer, BoardListSerializer, ColumnSerializer, ColumnWriteSerializer, ColumnReorderSerializer,
    LabelSerializer, TaskSerializer, TaskDetailSerializer, TaskMoveSerializer,
    CommentSerializer, TaskHistorySerializer,
    ChecklistSerializer, ChecklistItemSerializer, ChecklistItemWriteSerializer,
)


def _normalize_positions(column):
    """Re-number task positions in a column to be sequential (1, 2, 3, …).

    Uses SoftDeleteManager (Task.objects) so only active (non-deleted) tasks
    are counted. Uses bulk .update() per row to avoid triggering signals.
    """
    tasks = Task.objects.filter(column=column).order_by('position', 'created_at')
    for idx, task in enumerate(tasks, start=1):
        if task.position != idx:
            Task.objects.filter(pk=task.pk).update(position=idx)


@extend_schema_view(
    list=extend_schema(
        tags=['CRM'],
        summary='List boards',
        description=(
            'Returns all boards scoped to the authenticated user\'s company. '
            'Archived boards are excluded by default — pass `include_archived=true` to include them. '
            'Superadmin sees boards across all companies and can narrow results with `company_id`.'
        ),
        parameters=[
            OpenApiParameter(
                name='include_archived',
                type=bool,
                location=OpenApiParameter.QUERY,
                required=False,
                description='When true, archived boards are included in the response.',
            ),
            OpenApiParameter(
                name='company_id',
                type=int,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Superadmin only: filter boards by a specific company ID.',
            ),
        ],
        responses={200: BoardListSerializer(many=True)},
    ),
    retrieve=extend_schema(
        tags=['CRM'],
        summary='Get board',
        description=(
            'Returns full board detail including nested columns and their tasks. '
            'Pass `view=list` to get a flat paginated list of all tasks on the board '
            'with filters applied. Default (kanban) view returns columns with nested tasks.'
        ),
        parameters=[
            OpenApiParameter(
                name='view',
                type=str,
                location=OpenApiParameter.QUERY,
                required=False,
                enum=['kanban', 'list'],
                description=(
                    '`kanban` (default) — returns columns with nested tasks. '
                    '`list` — returns a flat paginated task list with filters applied.'
                ),
            ),
        ],
        responses={
            200: BoardSerializer,
            404: OpenApiResponse(description='Board not found or not accessible.'),
        },
    ),
    create=extend_schema(
        tags=['CRM'],
        summary='Create board',
        description=(
            'Creates a new board for the authenticated user\'s company. '
            'Three default columns ("К выполнению", "В работе", "Готово") are automatically created. '
            'Returns 400 if the company has reached its plan limit of non-archived boards.'
        ),
        request=BoardSerializer,
        responses={
            201: BoardSerializer,
            400: OpenApiResponse(
                description='Validation error or board limit reached.',
                examples=[
                    OpenApiExample(
                        name='Board limit exceeded',
                        value={'error': True, 'status_code': 400, 'detail': 'Board limit reached for your plan'},
                        response_only=True,
                        status_codes=['400'],
                    ),
                ],
            ),
        },
        examples=[
            OpenApiExample(
                name='Create sales pipeline',
                value={'name': 'Sales Pipeline', 'description': 'Track deals from lead to close'},
                request_only=True,
            ),
        ],
    ),
    partial_update=extend_schema(
        tags=['CRM'],
        summary='Update board',
        description='Partially updates a board. Only `name` and `description` are writable.',
        request=BoardSerializer,
        responses={
            200: BoardSerializer,
            400: OpenApiResponse(description='Validation error.'),
            404: OpenApiResponse(description='Board not found or not accessible.'),
        },
    ),
    destroy=extend_schema(
        tags=['CRM'],
        summary='Delete board',
        description='Permanently deletes a board and all its columns and tasks.',
        responses={
            204: OpenApiResponse(description='Board deleted successfully.'),
            404: OpenApiResponse(description='Board not found or not accessible.'),
        },
    ),
)
class BoardViewSet(CompanyIsolationMixin, SetCompanyOnCreateMixin, viewsets.ModelViewSet):
    serializer_class = BoardSerializer
    permission_classes = [IsCompanyMember, IsEmailVerifiedOrSuperAdmin]

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Board.objects.none()
        user = self.request.user
        if user.role == 'superadmin':
            qs = Board.objects.all()
            company_id = self.request.query_params.get('company_id')
            if company_id:
                qs = qs.filter(company_id=company_id)
        elif user.company_id:
            qs = Board.objects.filter(company_id=user.company_id)
        else:
            qs = Board.objects.none()

        include_archived = self.request.query_params.get('include_archived', '').lower() == 'true'
        if not include_archived:
            qs = qs.filter(is_archived=False)

        return qs.prefetch_related('columns__tasks')

    def get_serializer_class(self):
        if self.action == 'list':
            return BoardListSerializer
        return BoardSerializer

    def create(self, request, *args, **kwargs):
        company = request.user.company
        current_boards = company.boards.filter(is_archived=False).count()
        if current_boards >= company.max_boards:
            return Response(
                {'detail': 'Board limit reached for your plan'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        response = super().create(request, *args, **kwargs)
        notify_company_admins_limit_thresholds(
            company=company,
            metric='boards',
            current_value=current_boards + 1,
            limit_value=company.max_boards,
        )
        return response

    def perform_create(self, serializer):
        board = serializer.save(company=self.request.user.company, created_by=self.request.user)
        # Колонки по умолчанию
        for i, name in enumerate(['К выполнению', 'В работе', 'Готово']):
            Column.objects.create(board=board, name=name, position=i)

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        view_mode = request.query_params.get('view', 'kanban')

        if view_mode == 'list':
            from .filters import TaskFilter
            tasks_qs = Task.objects.select_related(
                'column__board', 'assignee', 'created_by',
            ).prefetch_related('labels', 'checklists__items').filter(
                column__board=instance,
            )
            # Apply filters
            task_filter = TaskFilter(request.query_params, queryset=tasks_qs)
            tasks_qs = task_filter.qs

            # Apply search via SearchFilter
            search_filter = SearchFilter()
            self.search_fields = ['title']
            tasks_qs = search_filter.filter_queryset(request, tasks_qs, self)

            # Apply ordering
            ordering_filter = OrderingFilter()
            self.ordering_fields = ['priority', 'deadline', 'created_at']
            self.ordering = ['created_at']
            tasks_qs = ordering_filter.filter_queryset(request, tasks_qs, self)

            tasks_qs = tasks_qs.distinct()
            paginator = StandardPagination()
            page = paginator.paginate_queryset(tasks_qs, request, view=self)
            if page is not None:
                serializer = TaskSerializer(page, many=True, context={'request': request})
                return paginator.get_paginated_response(serializer.data)
            serializer = TaskSerializer(tasks_qs, many=True, context={'request': request})
            return Response(serializer.data)

        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    @extend_schema(
        tags=['CRM'],
        summary='Archive board',
        description=(
            'Marks the board as archived (sets `is_archived=True`). '
            'Available to all company members (company_admin, employee) and superadmin. '
            'Archived boards are excluded from the default list response.'
        ),
        request=None,
        responses={
            200: BoardSerializer,
            401: OpenApiResponse(description='Not authenticated.'),
            403: OpenApiResponse(description='Company members only.'),
            404: OpenApiResponse(description='Board not found or not accessible.'),
        },
    )
    @action(detail=True, methods=['post'], url_path='archive')
    def archive(self, request, pk=None):
        user = request.user
        if user.role == 'superadmin':
            board = Board.objects.filter(pk=pk).first()
        else:
            board = Board.objects.filter(pk=pk, company=user.company).first()
        if board is None:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        if not board.is_archived:
            board.is_archived = True
            board.save(update_fields=['is_archived', 'updated_at'])
        return Response(BoardSerializer(board).data)

    @extend_schema(
        tags=['CRM'],
        summary='Unarchive board',
        description=(
            'Marks the board as active (sets `is_archived=False`). '
            'Available to all company members (company_admin, employee) and superadmin. '
            'Returns 400 if the company has already reached its plan limit of active boards. '
            'If the board is already active the action is idempotent and returns 200.'
        ),
        request=None,
        responses={
            200: BoardSerializer,
            400: OpenApiResponse(
                description='Board limit reached.',
                examples=[
                    OpenApiExample(
                        name='Limit exceeded',
                        value={
                            'error': True,
                            'status_code': 400,
                            'detail': 'Невозможно разархивировать: достигнут лимит досок для вашего тарифа',
                        },
                        response_only=True,
                        status_codes=['400'],
                    ),
                ],
            ),
            401: OpenApiResponse(description='Not authenticated.'),
            403: OpenApiResponse(description='Company members only.'),
            404: OpenApiResponse(description='Board not found or not accessible.'),
        },
    )
    @action(detail=True, methods=['post'], url_path='unarchive')
    def unarchive(self, request, pk=None):
        user = request.user
        if user.role == 'superadmin':
            board = Board.objects.filter(pk=pk).first()
        else:
            board = Board.objects.filter(pk=pk, company=user.company).first()
        if board is None:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        if board.is_archived:
            active_count = board.company.boards.filter(is_archived=False).count()
            if active_count >= board.company.max_boards:
                return Response(
                    {'detail': 'Невозможно разархивировать: достигнут лимит досок для вашего тарифа'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            board.is_archived = False
            board.save(update_fields=['is_archived', 'updated_at'])
        return Response(BoardSerializer(board).data)


@extend_schema_view(
    list=extend_schema(
        tags=['CRM'],
        summary='List columns of a board',
        responses={200: ColumnSerializer(many=True)},
    ),
    create=extend_schema(
        tags=['CRM'],
        summary='Add column to board',
        request=ColumnWriteSerializer,
        responses={
            201: ColumnSerializer,
            400: OpenApiResponse(description='Validation error'),
            403: OpenApiResponse(description='Forbidden'),
            404: OpenApiResponse(description='Board not found'),
        },
    ),
    partial_update=extend_schema(
        tags=['CRM'],
        summary='Update column (name, wip_limit, position)',
        request=ColumnWriteSerializer,
        responses={
            200: ColumnSerializer,
            400: OpenApiResponse(description='Validation error'),
            404: OpenApiResponse(description='Column or board not found'),
        },
    ),
    destroy=extend_schema(
        tags=['CRM'],
        summary='Delete column — moves tasks to another column first',
        parameters=[
            OpenApiParameter(
                name='move_to',
                location=OpenApiParameter.QUERY,
                required=True,
                type=int,
                description='ID of the column on the same board to receive all tasks from the deleted column.',
            ),
        ],
        responses={
            204: OpenApiResponse(description='Column deleted, tasks moved'),
            400: OpenApiResponse(description='move_to missing, last column, or wrong board'),
            403: OpenApiResponse(description='Company members only'),
            404: OpenApiResponse(description='Column or board not found'),
        },
    ),
)
class ColumnViewSet(viewsets.ModelViewSet):
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_permissions(self):
        return [IsCompanyMember(), IsEmailVerifiedOrSuperAdmin()]

    def _get_board_or_403(self):
        """
        Fetch the board identified by URL kwarg ``board_pk``.
        Raises NotFound if the board does not exist.
        Raises PermissionDenied if the board belongs to a different company
        (non-superadmin users only).
        """
        board_pk = self.kwargs.get('board_pk')
        try:
            board = Board.objects.get(pk=board_pk)
        except Board.DoesNotExist:
            raise NotFound('Board not found.')
        user = self.request.user
        if user.role != 'superadmin' and board.company_id != user.company_id:
            raise PermissionDenied('You do not have access to this board.')
        return board

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Column.objects.none()
        board = self._get_board_or_403()
        return Column.objects.filter(board=board).prefetch_related('tasks').order_by('position')

    def get_serializer_class(self):
        if self.action in ('create', 'partial_update'):
            return ColumnWriteSerializer
        if self.action == 'reorder':
            return ColumnReorderSerializer
        return ColumnSerializer

    def perform_create(self, serializer):
        board = self._get_board_or_403()
        last_position = (
            Column.objects.filter(board=board).order_by('-position').values_list('position', flat=True).first()
        )
        next_position = (last_position or 0) + 1
        serializer.save(board=board, position=next_position)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        output = ColumnSerializer(serializer.instance)
        return Response(output.data, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        instance = serializer.instance
        # Pop position first so that a null value sent by the client is never
        # written to the DB (the column has a NOT NULL constraint).
        new_position = serializer.validated_data.pop('position', None)

        if new_position is not None and new_position != instance.position:
            board = instance.board
            old_position = instance.position
            sibling_count = Column.objects.filter(board=board).exclude(pk=instance.pk).count()
            # Clamp new_position to valid range
            max_pos = sibling_count + 1
            new_position = max(1, min(new_position, max_pos))
            serializer.validated_data['position'] = new_position

            # Shift siblings to fill the gap created by moving this column
            if old_position < new_position:
                # Moving down: shift columns between old+1 and new_position up by 1
                Column.objects.filter(
                    board=board,
                    position__gt=old_position,
                    position__lte=new_position,
                ).exclude(pk=instance.pk).update(position=models.F('position') - 1)
            else:
                # Moving up: shift columns between new_position and old-1 down by 1
                Column.objects.filter(
                    board=board,
                    position__gte=new_position,
                    position__lt=old_position,
                ).exclude(pk=instance.pk).update(position=models.F('position') + 1)

        serializer.save()

    def partial_update(self, request, *args, **kwargs):
        self._get_board_or_403()
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        output = ColumnSerializer(serializer.instance)
        return Response(output.data, status=status.HTTP_200_OK)

    def destroy(self, request, *args, **kwargs):
        self._get_board_or_403()
        instance = self.get_object()
        board = instance.board

        move_to_id = request.query_params.get('move_to')
        if not move_to_id:
            raise ValidationError({'move_to': 'This query parameter is required.'})

        # Cannot delete the last column
        board_columns = Column.objects.filter(board=board)
        if board_columns.count() <= 1:
            raise ValidationError({'detail': 'Cannot delete the last column on a board.'})

        # Validate move_to column
        try:
            move_to_id = int(move_to_id)
            target_column = board_columns.get(pk=move_to_id)
        except (ValueError, Column.DoesNotExist):
            raise ValidationError({'move_to': 'Target column not found on this board.'})

        if target_column.pk == instance.pk:
            raise ValidationError({'move_to': 'Target column must differ from the deleted column.'})

        # Move all tasks
        Task.objects.filter(column=instance).update(column=target_column)

        # Delete the column
        instance.delete()

        # Re-normalize positions of remaining columns (fill gaps)
        remaining = Column.objects.filter(board=board).order_by('position')
        for idx, col in enumerate(remaining, start=1):
            if col.position != idx:
                col.position = idx
                col.save(update_fields=['position'])

        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        tags=['CRM'],
        summary='Reorder all columns on a board',
        request=ColumnReorderSerializer,
        responses={
            200: ColumnSerializer(many=True),
            400: OpenApiResponse(description='Invalid column_ids'),
            403: OpenApiResponse(description='Forbidden'),
            404: OpenApiResponse(description='Board not found'),
        },
        examples=[
            OpenApiExample(
                'Reorder example',
                value={'column_ids': [3, 1, 2]},
                request_only=True,
            ),
        ],
    )
    @action(detail=False, methods=['post'], url_path='reorder')
    def reorder(self, request, board_pk=None):
        board = self._get_board_or_403()
        serializer = ColumnReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        column_ids = serializer.validated_data['column_ids']
        board_column_ids = set(Column.objects.filter(board=board).values_list('id', flat=True))

        # All columns of the board must be present — no missing, no extra
        provided_ids = set(column_ids)
        if provided_ids != board_column_ids:
            missing = board_column_ids - provided_ids
            extra = provided_ids - board_column_ids
            errors = []
            if missing:
                errors.append(f'Missing column IDs: {sorted(missing)}.')
            if extra:
                errors.append(f'Unknown column IDs: {sorted(extra)}.')
            raise ValidationError({'column_ids': ' '.join(errors)})

        # Assign new positions in the given order
        columns_by_id = {col.id: col for col in Column.objects.filter(board=board)}
        for new_pos, col_id in enumerate(column_ids, start=1):
            col = columns_by_id[col_id]
            if col.position != new_pos:
                col.position = new_pos
                col.save(update_fields=['position'])

        result = Column.objects.filter(board=board).prefetch_related('tasks').order_by('position')
        return Response(ColumnSerializer(result, many=True).data, status=status.HTTP_200_OK)


@extend_schema_view(
    list=extend_schema(
        tags=['CRM'],
        summary='List tasks',
        description=(
            'Returns tasks scoped to the authenticated user\'s company. '
            'Filter by board_id, column_id, assignee_id, priority, label_ids, '
            'deadline_from, deadline_to, or use search= for title full-text search.'
        ),
        parameters=[
            OpenApiParameter(name='board_id', type=int, location=OpenApiParameter.QUERY,
                             required=False, description='Filter tasks by board.'),
            OpenApiParameter(name='column_id', type=int, location=OpenApiParameter.QUERY,
                             required=False, description='Filter tasks by column.'),
            OpenApiParameter(name='assignee_id', type=int, location=OpenApiParameter.QUERY,
                             required=False, description='Filter tasks by assignee.'),
            OpenApiParameter(name='priority', type=str, location=OpenApiParameter.QUERY,
                             required=False, description='Filter by priority (low/medium/high/urgent).'),
            OpenApiParameter(name='label_ids', type=str, location=OpenApiParameter.QUERY,
                             required=False, description='Comma-separated label IDs to filter by.'),
            OpenApiParameter(name='deadline_from', type=str, location=OpenApiParameter.QUERY,
                             required=False, description='Deadline >= this date (YYYY-MM-DD).'),
            OpenApiParameter(name='deadline_to', type=str, location=OpenApiParameter.QUERY,
                             required=False, description='Deadline <= this date (YYYY-MM-DD).'),
        ],
        responses={200: TaskSerializer(many=True)},
    ),
    retrieve=extend_schema(
        tags=['CRM'],
        summary='Get task details',
        description='Returns full task card including checklists, comments_count, attachments_count, and last 10 history entries.',  # noqa: E501
        responses={200: TaskDetailSerializer, 404: OpenApiResponse(description='Not found')},
    ),
    create=extend_schema(
        tags=['CRM'],
        operation_id='task_create',
        summary='Создать задачу',
        description=(
            'Creates a task. board_id and column_id must belong to the requesting user\'s company. '
            'assignee_id must be an employee of the same company. '
            'label_ids must belong to the same company.'
        ),
        request=inline_serializer(
            name='TaskCreateRequest',
            fields={
                'board_id': drf_serializers.IntegerField(
                    help_text='ID доски',
                ),
                'column_id': drf_serializers.IntegerField(
                    help_text='ID колонки на доске',
                ),
                'title': drf_serializers.CharField(
                    max_length=255,
                    help_text='Название задачи',
                ),
                'description': drf_serializers.CharField(
                    required=False,
                    allow_blank=True,
                    help_text='Описание задачи',
                ),
                'priority': drf_serializers.ChoiceField(
                    choices=['low', 'medium', 'high', 'critical'],
                    help_text='Приоритет задачи',
                ),
                'deadline': drf_serializers.DateField(
                    required=False,
                    allow_null=True,
                    help_text='Срок выполнения (YYYY-MM-DD)',
                ),
                'assignee_id': drf_serializers.IntegerField(
                    required=False,
                    allow_null=True,
                    help_text='ID исполнителя (сотрудник той же компании)',
                ),
                'label_ids': drf_serializers.ListField(
                    child=drf_serializers.IntegerField(),
                    required=False,
                    help_text='Список ID меток доски',
                ),
            },
        ),
        responses={
            201: TaskSerializer,
            400: OpenApiResponse(
                description='Validation error — assignee из другой компании, неверный board/column.',
            ),
            401: OpenApiResponse(description='Not authenticated.'),
            403: OpenApiResponse(description='Forbidden — company members only.'),
        },
        examples=[
            OpenApiExample(
                name='Create task example',
                value={
                    'board_id': 1,
                    'column_id': 3,
                    'title': 'Разработать API авторизации',
                    'description': 'Реализовать JWT-аутентификацию с refresh-токенами',
                    'priority': 'high',
                    'deadline': '2026-05-01',
                    'assignee_id': 42,
                    'label_ids': [1, 2],
                },
                request_only=True,
            ),
        ],
    ),
    partial_update=extend_schema(
        tags=['CRM'],
        summary='Update task',
        description=(
            'Partially updates a task. Updatable: title, description, priority, deadline, assignee_id, label_ids. '
            'When assignee changes, the new assignee receives an in-app notification.'
        ),
        request=TaskSerializer,
        responses={200: TaskSerializer, 400: OpenApiResponse(description='Validation error')},
    ),
    destroy=extend_schema(
        tags=['CRM'],
        summary='Delete task',
        description='Soft-deletes the task (Task extends SoftDeleteModel).',
        responses={204: OpenApiResponse(description='Deleted')},
    ),
)
class TaskViewSet(viewsets.ModelViewSet):
    permission_classes = [IsCompanyMember, IsEmailVerifiedOrSuperAdmin]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    search_fields = ['title']
    ordering_fields = ['priority', 'deadline', 'created_at']
    ordering = ['created_at']
    filterset_class = None  # TaskFilter applied manually in filter_queryset

    def get_queryset(self):
        user = self.request.user
        qs = Task.objects.select_related(
            'column__board', 'assignee', 'created_by',
        ).prefetch_related('labels', 'checklists__items')
        if user.role == 'superadmin':
            return qs
        if user.company_id:
            return qs.filter(column__board__company_id=user.company_id)
        return qs.none()

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return TaskDetailSerializer
        return TaskSerializer

    def filter_queryset(self, queryset):
        from .filters import TaskFilter

        # Apply TaskFilter (handles assignee_id, priority, label_ids, deadline enum,
        # board_id, column_id, search via icontains, deadline_from/deadline_to)
        f = TaskFilter(self.request.query_params, queryset=queryset)
        queryset = f.qs

        # Apply ordering via OrderingFilter
        ordering_filter = OrderingFilter()
        queryset = ordering_filter.filter_queryset(self.request, queryset, self)

        return queryset.distinct()

    def perform_create(self, serializer):
        column = serializer.validated_data.get('column')
        if column and column.wip_limit > 0:
            active_count = Task.objects.filter(column=column, is_archived=False).count()
            if active_count >= column.wip_limit:
                raise ValidationError(f'WIP limit reached (max {column.wip_limit} tasks)')
        task = serializer.save(created_by=self.request.user)
        # Normalize positions so the new task gets a clean sequential number
        # at the end of the column rather than inheriting any gaps.
        _normalize_positions(task.column)
        if task.assignee and task.assignee != self.request.user:
            Notification.objects.create(
                user=task.assignee,
                notification_type='task_assigned',
                title='Вам назначена задача',
                body=task.title,
                url=f'/crm/tasks/{task.pk}/',
            )

    def perform_update(self, serializer):
        task = serializer.instance
        validated = serializer.validated_data

        # Capture old scalar field values before saving — only for fields explicitly in the PATCH request.
        tracked_scalar_fields = {
            'title': task.title,
            'description': task.description,
            'priority': task.priority,
            'deadline': str(task.deadline) if task.deadline else '',
            'assignee': task.assignee_id,
            'column': task.column_id,
        }
        # Determine which tracked fields were actually sent in this PATCH.
        requested_tracked = set()
        for field_key, vd_key in [
            ('title', 'title'), ('description', 'description'), ('priority', 'priority'),
            ('deadline', 'deadline'), ('assignee', 'assignee'), ('column', 'column'),
        ]:
            if vd_key in validated:
                requested_tracked.add(field_key)

        # Capture old label IDs before saving.
        old_label_ids = set(task.labels.values_list('id', flat=True))
        labels_in_request = 'labels' in validated

        old_assignee_id = task.assignee_id

        instance = serializer.save()
        instance.refresh_from_db()

        new_assignee_id = instance.assignee_id
        new_assignee = instance.assignee

        if new_assignee and new_assignee_id != old_assignee_id and new_assignee != self.request.user:
            Notification.objects.create(
                user=new_assignee,
                notification_type='task_assigned',
                title='Вам назначена задача',
                body=instance.title,
                url=f'/crm/tasks/{instance.pk}/',
            )

        # Log changes to scalar fields that were explicitly sent and actually changed.
        new_scalar_values = {
            'title': instance.title,
            'description': instance.description,
            'priority': instance.priority,
            'deadline': str(instance.deadline) if instance.deadline else '',
            'assignee': instance.assignee_id,
            'column': instance.column_id,
        }
        for field_key in requested_tracked:
            old_val = tracked_scalar_fields[field_key]
            new_val = new_scalar_values[field_key]
            old_str = str(old_val) if old_val is not None else ''
            new_str = str(new_val) if new_val is not None else ''
            if old_str != new_str:
                TaskHistory.objects.create(
                    task=instance,
                    user=self.request.user,
                    action=f'updated_{field_key}',
                    old_value=old_str,
                    new_value=new_str,
                )

        # Log label changes.
        if labels_in_request:
            new_label_ids = set(instance.labels.values_list('id', flat=True))
            for lid in new_label_ids - old_label_ids:
                TaskHistory.objects.create(
                    task=instance,
                    user=self.request.user,
                    action='label_added',
                    old_value='',
                    new_value=str(lid),
                )
            for lid in old_label_ids - new_label_ids:
                TaskHistory.objects.create(
                    task=instance,
                    user=self.request.user,
                    action='label_removed',
                    old_value=str(lid),
                    new_value='',
                )

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        column = instance.column
        instance.soft_delete()
        _normalize_positions(column)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        tags=['CRM'],
        summary='Move task (drag & drop)',
        description=(
            'Moves a task to a target column. The target column must belong to the same board '
            'and the same company. If the column has a WIP limit set (wip_limit > 0), the move '
            'is rejected when the target column already has that many active tasks. '
            'If `position` is omitted, the task is appended to the end of the target column.'
        ),
        request=TaskMoveSerializer,
        responses={
            200: TaskSerializer,
            400: OpenApiResponse(description='Validation error or WIP limit reached'),
            404: OpenApiResponse(description='Task not found'),
        },
    )
    @action(detail=True, methods=['post'], url_path='move')
    def move(self, request, pk=None):
        task = self.get_object()
        serializer = TaskMoveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        target_column = serializer.validated_data['column_id']  # already a Column instance
        target_column_obj = Column.objects.select_related('board').get(pk=target_column.pk)

        if target_column_obj.board_id != task.column.board_id:
            raise ValidationError({'column_id': 'Target column must belong to the same board as the task.'})

        # Validate tenant isolation: target column's board must belong to the request user's company.
        # Superadmin bypasses this check.
        if request.user.role != 'superadmin':
            if target_column_obj.board.company_id != request.user.company_id:
                raise ValidationError({'column_id': 'Target column does not belong to your company.'})

        # WIP limit check: wip_limit == 0 means no limit.
        # Task.objects (SoftDeleteManager) already excludes is_deleted=True.
        if target_column_obj.wip_limit > 0:
            active_count = Task.objects.filter(
                column=target_column_obj,
                is_archived=False,
            ).exclude(pk=task.pk).count()
            if active_count >= target_column_obj.wip_limit:
                raise ValidationError(
                    f'WIP limit reached (max {target_column_obj.wip_limit} tasks)'
                )

        old_column = task.column
        old_col = task.column_id

        # Determine position: use provided position or append to end.
        order = serializer.validated_data.get('position')
        if order is not None:
            new_position = order
        else:
            max_pos = (
                Task.objects.filter(column=target_column_obj)
                .exclude(pk=task.pk)
                .aggregate(models.Max('position'))['position__max']
            )
            new_position = (max_pos or 0) + 1

        task.column = target_column_obj
        task.position = new_position
        task.save(update_fields=['column', 'position'])

        # Re-normalize both columns so positions are always sequential.
        _normalize_positions(old_column)
        _normalize_positions(target_column_obj)

        task.refresh_from_db()

        TaskHistory.objects.create(
            task=task, user=request.user, action='moved',
            old_value=str(old_col), new_value=str(task.column_id),
        )
        return Response(TaskSerializer(task, context={'request': request}).data)

    @extend_schema(
        tags=['CRM'],
        summary='My tasks across all boards',
        description=(
            'Returns all tasks assigned to the current user scoped to their company. '
            'Supports all task filters: `board_id`, `column_id`, `priority`, `label_ids`, '
            '`deadline` (overdue/today/this_week), `deadline_from`, `deadline_to`, `search`. '
            'Ordering: `ordering=priority`, `ordering=deadline`, `ordering=-created_at`, etc. '
            'Response is a flat paginated list; each task includes `board_id` and `board_title` fields.'
        ),
        parameters=[
            OpenApiParameter(name='board_id', type=int, location=OpenApiParameter.QUERY,
                             required=False, description='Filter by board.'),
            OpenApiParameter(name='column_id', type=int, location=OpenApiParameter.QUERY,
                             required=False, description='Filter by column.'),
            OpenApiParameter(name='priority', type=str, location=OpenApiParameter.QUERY,
                             required=False, description='Filter by priority (low/medium/high/urgent).'),
            OpenApiParameter(name='label_ids', type=str, location=OpenApiParameter.QUERY,
                             required=False, description='Comma-separated label IDs.'),
            OpenApiParameter(
                name='deadline', type=str, location=OpenApiParameter.QUERY,
                required=False, enum=['overdue', 'today', 'this_week'],
                description='Deadline shortcut filter.',
            ),
            OpenApiParameter(name='deadline_from', type=str, location=OpenApiParameter.QUERY,
                             required=False, description='Deadline >= this date (YYYY-MM-DD).'),
            OpenApiParameter(name='deadline_to', type=str, location=OpenApiParameter.QUERY,
                             required=False, description='Deadline <= this date (YYYY-MM-DD).'),
            OpenApiParameter(name='search', type=str, location=OpenApiParameter.QUERY,
                             required=False, description='Search by task title (case-insensitive).'),
            OpenApiParameter(name='ordering', type=str, location=OpenApiParameter.QUERY,
                             required=False,
                             description='Order results. Options: priority, deadline, created_at (prefix - for desc).'),
        ],
        responses={200: TaskSerializer(many=True)},
    )
    @action(detail=False, methods=['get'], url_path='my')
    def my_tasks(self, request):
        base_qs = self.get_queryset().filter(assignee=request.user, is_archived=False)
        queryset = self.filter_queryset(base_qs)
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @extend_schema(
        tags=['CRM'],
        summary='Task history',
        responses={200: TaskHistorySerializer(many=True), 404: OpenApiResponse(description='Not found')},
    )
    @action(detail=True, methods=['get'], url_path='history')
    def history(self, request, pk=None):
        task = self.get_object()
        # Limit to the 50 most recent entries for consistent, bounded responses.
        entries = task.history.all()[:50]
        page = self.paginate_queryset(entries)
        if page is not None:
            return self.get_paginated_response(TaskHistorySerializer(page, many=True).data)
        return Response(TaskHistorySerializer(entries, many=True).data)

    @extend_schema(
        tags=['CRM'],
        summary='Archive task',
        description=(
            'Soft-deletes the task by calling task.soft_delete() (sets is_deleted=True). '
            'Archived tasks are excluded from all list queries. '
            'Permission: company members only (employee, company_admin, superadmin).'
        ),
        request=None,
        responses={
            200: inline_serializer(
                name='TaskArchiveResponse',
                fields={'detail': drf_serializers.CharField()},
            ),
            401: OpenApiResponse(description='Not authenticated.'),
            403: OpenApiResponse(description='Company members only.'),
            404: OpenApiResponse(description='Task not found.'),
        },
    )
    @action(detail=True, methods=['post'], url_path='archive')
    def archive(self, request, pk=None):
        task = self.get_object()
        column = task.column
        task.soft_delete()
        _normalize_positions(column)
        TaskHistory.objects.create(
            task=task,
            user=request.user,
            action='archived',
            old_value='False',
            new_value='True',
        )
        return Response({'detail': 'Task archived'}, status=status.HTTP_200_OK)


@extend_schema_view(
    list=extend_schema(
        tags=['CRM'],
        summary='List comments for a task',
        description='Returns all comments for the specified task, ordered by creation time ascending.',
        responses={
            200: CommentSerializer(many=True),
            401: OpenApiResponse(description='Not authenticated.'),
            403: OpenApiResponse(description='Company members only or task not accessible.'),
            404: OpenApiResponse(description='Task not found.'),
        },
    ),
    create=extend_schema(
        tags=['CRM'],
        summary='Add comment to a task',
        description=(
            'Creates a comment on the specified task. '
            'Author is auto-set to the authenticated user. '
            'After creation, notifies the task assignee and creator (if different from the comment author).'
        ),
        request=CommentSerializer,
        responses={
            201: CommentSerializer,
            400: OpenApiResponse(description='Validation error.'),
            401: OpenApiResponse(description='Not authenticated.'),
            403: OpenApiResponse(description='Company members only or task belongs to another company.'),
        },
    ),
    partial_update=extend_schema(
        tags=['CRM'],
        summary='Edit a comment',
        description='Only the comment author can edit their own comment. Body: {text}.',
        request=CommentSerializer,
        responses={
            200: CommentSerializer,
            400: OpenApiResponse(description='Validation error.'),
            401: OpenApiResponse(description='Not authenticated.'),
            403: OpenApiResponse(description='Only the author can edit this comment.'),
            404: OpenApiResponse(description='Comment not found.'),
        },
    ),
    destroy=extend_schema(
        tags=['CRM'],
        summary='Delete a comment',
        description='Author or company_admin can delete a comment.',
        responses={
            204: OpenApiResponse(description='Comment deleted.'),
            401: OpenApiResponse(description='Not authenticated.'),
            403: OpenApiResponse(description='Only the author or company admin can delete this comment.'),
            404: OpenApiResponse(description='Comment not found.'),
        },
    ),
)
class CommentViewSet(viewsets.ModelViewSet):
    serializer_class = CommentSerializer
    permission_classes = [IsCompanyMember, IsEmailVerifiedOrSuperAdmin]
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_permissions(self):
        if self.action in ('partial_update', 'destroy'):
            perm = IsOwnerOrAdmin()
            perm.owner_field = 'author'
            return [perm, IsEmailVerifiedOrSuperAdmin()]
        return [IsCompanyMember(), IsEmailVerifiedOrSuperAdmin()]

    def _get_task_or_403(self):
        """
        Fetch the task identified by URL kwarg ``task_pk``.
        Raises NotFound if the task does not exist.
        Raises PermissionDenied if the task belongs to a different company (non-superadmin users only).
        """
        task_pk = self.kwargs.get('task_pk')
        try:
            task = Task.objects.select_related('column__board', 'assignee', 'created_by').get(pk=task_pk)
        except Task.DoesNotExist:
            raise NotFound('Task not found.')
        user = self.request.user
        if user.role != 'superadmin' and task.column.board.company_id != user.company_id:
            raise PermissionDenied('You do not have access to this task.')
        return task

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Comment.objects.none()
        self._get_task_or_403()
        return Comment.objects.filter(task_id=self.kwargs.get('task_pk')).select_related('author')

    def perform_create(self, serializer):
        task = self._get_task_or_403()
        comment = serializer.save(author=self.request.user, task=task)
        self._notify_task_participants(task, comment)

    def _notify_task_participants(self, task, comment):
        """Notify task assignee and creator when a new comment is posted, skipping the author."""
        author = comment.author
        recipients = set()

        if task.assignee and task.assignee != author:
            recipients.add(task.assignee)
        if task.created_by and task.created_by != author:
            recipients.add(task.created_by)

        for recipient in recipients:
            Notification.objects.create(
                user=recipient,
                notification_type='task_comment',
                title='Новый комментарий к задаче',
                body=task.title,
                url=f'/crm/tasks/{task.pk}/',
            )


@extend_schema_view(
    list=extend_schema(
        tags=['CRM'],
        summary='List labels',
        description='Returns all labels scoped to the authenticated user\'s company.',
        responses={200: LabelSerializer(many=True)},
    ),
    create=extend_schema(
        tags=['CRM'],
        summary='Create label',
        description='Creates a label. Name must be unique within the company. Color is a hex code.',
        request=LabelSerializer,
        responses={
            201: LabelSerializer,
            400: OpenApiResponse(description='Validation error (duplicate name or invalid color)'),
        },
    ),
    partial_update=extend_schema(
        tags=['CRM'],
        summary='Update label',
        description='Updates name and/or color. Restricted to company_admin or superadmin.',
        request=LabelSerializer,
        responses={
            200: LabelSerializer,
            400: OpenApiResponse(description='Validation error'),
            403: OpenApiResponse(description='Company admin required'),
        },
    ),
    destroy=extend_schema(
        tags=['CRM'],
        summary='Delete label',
        description=(
            'Deletes a label. M2M relation with tasks is automatically removed. '
            'Restricted to company_admin or superadmin.'
        ),
        responses={
            204: OpenApiResponse(description='Label deleted'),
            403: OpenApiResponse(description='Company admin required'),
        },
    ),
)
class LabelViewSet(CompanyIsolationMixin, SetCompanyOnCreateMixin, viewsets.ModelViewSet):
    serializer_class = LabelSerializer
    queryset = Label.objects.all()
    permission_classes = [IsCompanyMember]
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_permissions(self):
        if self.action in ('partial_update', 'destroy'):
            return [IsCompanyAdmin(), IsEmailVerifiedOrSuperAdmin()]
        return [IsCompanyMember(), IsEmailVerifiedOrSuperAdmin()]


class ChecklistViewSet(viewsets.ViewSet):
    """
    Checklists nested under a task.

    Routes:
      POST   /crm/tasks/<task_id>/checklists/   — create checklist
      GET    /crm/tasks/<task_id>/checklists/   — list checklists
      DELETE /crm/checklists/<id>/              — delete checklist
    """
    permission_classes = [IsCompanyMember, IsEmailVerifiedOrSuperAdmin]

    def _get_task_or_403(self, task_id):
        """Return the Task if it belongs to the request user's company; raise otherwise."""
        user = self.request.user
        try:
            task = Task.objects.select_related('column__board').get(pk=task_id)
        except Task.DoesNotExist:
            raise NotFound('Task not found.')
        if user.role != 'superadmin' and task.column.board.company_id != user.company_id:
            raise PermissionDenied('You do not have access to this task.')
        return task

    @extend_schema(
        tags=['CRM'],
        summary='List checklists for a task',
        responses={200: ChecklistSerializer(many=True)},
    )
    def list(self, request, task_pk=None):
        task = self._get_task_or_403(task_pk)
        checklists = Checklist.objects.filter(task=task).prefetch_related('items')
        return Response(ChecklistSerializer(checklists, many=True).data)

    @extend_schema(
        tags=['CRM'],
        summary='Create checklist for a task',
        request=ChecklistSerializer,
        responses={
            201: ChecklistSerializer,
            400: OpenApiResponse(description='Validation error'),
            403: OpenApiResponse(description='Company members only'),
            404: OpenApiResponse(description='Task not found'),
        },
    )
    def create(self, request, task_pk=None):
        task = self._get_task_or_403(task_pk)
        serializer = ChecklistSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        checklist = Checklist.objects.create(task=task, title=serializer.validated_data['title'])
        return Response(ChecklistSerializer(checklist).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        tags=['CRM'],
        summary='Delete checklist (cascades to items)',
        responses={
            204: OpenApiResponse(description='Deleted'),
            403: OpenApiResponse(description='Company members only'),
            404: OpenApiResponse(description='Checklist not found'),
        },
    )
    def destroy(self, request, pk=None):
        user = request.user
        try:
            checklist = Checklist.objects.select_related('task__column__board').get(pk=pk)
        except Checklist.DoesNotExist:
            raise NotFound('Checklist not found.')
        if user.role != 'superadmin' and checklist.task.column.board.company_id != user.company_id:
            raise PermissionDenied('You do not have access to this checklist.')
        checklist.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ChecklistItemViewSet(viewsets.ViewSet):
    """
    Checklist items.

    Routes:
      POST   /crm/checklists/<id>/items/   — add item to checklist
      PATCH  /crm/items/<id>/              — update item
      DELETE /crm/items/<id>/              — delete item, re-normalise order
    """
    permission_classes = [IsCompanyMember, IsEmailVerifiedOrSuperAdmin]

    def _get_checklist_or_403(self, checklist_id):
        user = self.request.user
        try:
            checklist = Checklist.objects.select_related('task__column__board').get(pk=checklist_id)
        except Checklist.DoesNotExist:
            raise NotFound('Checklist not found.')
        if user.role != 'superadmin' and checklist.task.column.board.company_id != user.company_id:
            raise PermissionDenied('You do not have access to this checklist.')
        return checklist

    def _get_item_or_403(self, item_id):
        user = self.request.user
        try:
            item = ChecklistItem.objects.select_related('checklist__task__column__board').get(pk=item_id)
        except ChecklistItem.DoesNotExist:
            raise NotFound('Item not found.')
        if user.role != 'superadmin' and item.checklist.task.column.board.company_id != user.company_id:
            raise PermissionDenied('You do not have access to this item.')
        return item

    @extend_schema(
        tags=['CRM'],
        summary='Add item to checklist',
        request=ChecklistItemWriteSerializer,
        responses={
            201: ChecklistItemSerializer,
            400: OpenApiResponse(description='Validation error'),
            403: OpenApiResponse(description='Company members only'),
            404: OpenApiResponse(description='Checklist not found'),
        },
    )
    def create(self, request, checklist_pk=None):
        checklist = self._get_checklist_or_403(checklist_pk)
        serializer = ChecklistItemWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        last_position = (
            ChecklistItem.objects.filter(checklist=checklist)
            .order_by('-position')
            .values_list('position', flat=True)
            .first()
        )
        next_position = (last_position or 0) + 1

        item = ChecklistItem.objects.create(
            checklist=checklist,
            text=serializer.validated_data['text'],
            is_done=serializer.validated_data.get('is_done', False),
            position=next_position,
        )
        return Response(ChecklistItemSerializer(item).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        tags=['CRM'],
        summary='Update checklist item',
        request=ChecklistItemWriteSerializer,
        responses={
            200: ChecklistItemSerializer,
            400: OpenApiResponse(description='Validation error'),
            403: OpenApiResponse(description='Company members only'),
            404: OpenApiResponse(description='Item not found'),
        },
    )
    def partial_update(self, request, pk=None):
        item = self._get_item_or_403(pk)
        serializer = ChecklistItemWriteSerializer(item, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        # Re-fetch to get clean state
        item.refresh_from_db()
        return Response(ChecklistItemSerializer(item).data)

    @extend_schema(
        tags=['CRM'],
        summary='Delete checklist item and re-normalise order',
        responses={
            204: OpenApiResponse(description='Deleted'),
            403: OpenApiResponse(description='Company members only'),
            404: OpenApiResponse(description='Item not found'),
        },
    )
    def destroy(self, request, pk=None):
        item = self._get_item_or_403(pk)
        checklist = item.checklist
        item.delete()

        # Re-normalise positions for the remaining items in this checklist
        remaining = ChecklistItem.objects.filter(checklist=checklist).order_by('position')
        for idx, remaining_item in enumerate(remaining, start=1):
            if remaining_item.position != idx:
                remaining_item.position = idx
                remaining_item.save(update_fields=['position'])

        return Response(status=status.HTTP_204_NO_CONTENT)
