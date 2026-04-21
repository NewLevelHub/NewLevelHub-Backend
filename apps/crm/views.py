from django.db import models
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, NotFound, ValidationError
from rest_framework.response import Response
from rest_framework import serializers as drf_serializers
from drf_spectacular.utils import (
    extend_schema, extend_schema_view,
    OpenApiParameter, OpenApiExample, OpenApiResponse, inline_serializer,
)

from apps.companies.limits import notify_company_admins_limit_thresholds
from apps.core.permissions import IsCompanyMember, IsEmailVerifiedOrSuperAdmin
from apps.core.mixins import CompanyIsolationMixin, SetCompanyOnCreateMixin
from apps.notifications.models import Notification
from .models import Board, Column, Label, Task, Comment, TaskHistory
from .serializers import (
    BoardSerializer, BoardListSerializer, ColumnSerializer, ColumnWriteSerializer, ColumnReorderSerializer,
    LabelSerializer, TaskSerializer, TaskDetailSerializer, TaskMoveSerializer,
    CommentSerializer, TaskHistorySerializer,
)


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
        description='Returns full board detail including nested columns and their tasks.',
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
        new_position = serializer.validated_data.get('position')

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
    search_fields = ['title']
    filterset_class = None  # set in __init_subclass__ via get_filterset_class; assigned below

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
        from django_filters.rest_framework import DjangoFilterBackend
        from rest_framework.filters import SearchFilter

        for backend in [DjangoFilterBackend(), SearchFilter()]:
            queryset = backend.filter_queryset(self.request, queryset, self)

        # Apply TaskFilter manually
        f = TaskFilter(self.request.query_params, queryset=queryset)
        return f.qs.distinct()

    def perform_create(self, serializer):
        task = serializer.save(created_by=self.request.user)
        if task.assignee and task.assignee != self.request.user:
            Notification.objects.create(
                user=task.assignee,
                notification_type='task_assigned',
                title='Вам назначена задача',
                body=task.title,
                url=f'/crm/tasks/{task.pk}/',
            )

    def perform_update(self, serializer):
        old_assignee_id = serializer.instance.assignee_id
        instance = serializer.save()
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

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.soft_delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        tags=['CRM'],
        summary='Move task (drag & drop)',
        description=(
            'Moves a task to a target column. The target column must belong to the same board '
            'and the same company. If the column has a WIP limit set (wip_limit > 0), the move '
            'is rejected when the target column already has that many active tasks. '
            'If `order` is omitted, the task is appended to the end of the target column.'
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

        old_col = task.column_id

        # Determine position: use provided order or append to end.
        order = serializer.validated_data.get('order')
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

        TaskHistory.objects.create(
            task=task, user=request.user, action='moved',
            old_value=str(old_col), new_value=str(task.column_id),
        )
        return Response(TaskSerializer(task, context={'request': request}).data)

    @extend_schema(
        tags=['CRM'],
        summary='My tasks across all boards',
        responses={200: TaskSerializer(many=True)},
    )
    @action(detail=False, methods=['get'], url_path='my')
    def my_tasks(self, request):
        queryset = self.filter_queryset(
            self.get_queryset().filter(assignee=request.user, is_archived=False)
        )
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
        task.soft_delete()
        return Response({'detail': 'Task archived'}, status=status.HTTP_200_OK)


@extend_schema_view(
    list=extend_schema(
        tags=['CRM'],
        summary='List comments',
        responses={200: CommentSerializer(many=True)},
    ),
    create=extend_schema(
        tags=['CRM'],
        summary='Add comment',
        request=CommentSerializer,
        responses={
            201: CommentSerializer,
            400: OpenApiResponse(description='Validation error'),
            403: OpenApiResponse(description='Company members only'),
        },
    ),
)
class CommentViewSet(viewsets.ModelViewSet):
    serializer_class = CommentSerializer
    permission_classes = [IsCompanyMember, IsEmailVerifiedOrSuperAdmin]
    http_method_names = ['get', 'post', 'delete']

    def get_queryset(self):
        return Comment.objects.filter(task_id=self.kwargs.get('task_pk'))

    def perform_create(self, serializer):
        serializer.save(author=self.request.user, task_id=self.kwargs.get('task_pk'))
        # TODO: уведомление assignee задачи


@extend_schema_view(
    list=extend_schema(
        tags=['CRM'],
        summary='List labels',
        responses={200: LabelSerializer(many=True)},
    ),
    create=extend_schema(
        tags=['CRM'],
        summary='Create label',
        request=LabelSerializer,
        responses={
            201: LabelSerializer,
            400: OpenApiResponse(description='Validation error'),
        },
    ),
)
class LabelViewSet(CompanyIsolationMixin, SetCompanyOnCreateMixin, viewsets.ModelViewSet):
    serializer_class = LabelSerializer
    permission_classes = [IsCompanyMember, IsEmailVerifiedOrSuperAdmin]
    queryset = Label.objects.all()
