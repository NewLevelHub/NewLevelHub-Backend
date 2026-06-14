import json

from django.db import models
from rest_framework import viewsets
from rest_framework.decorators import action, api_view, permission_classes
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
from apps.companies.models import Company
from apps.core.exceptions import raise_validation_error, LocalizedError
from apps.core.i18n import translate, get_lang
from apps.core.error_codes import STORAGE_LIMIT_EXCEEDED
from apps.core.pagination import StandardPagination
from apps.core.permissions import (
    IsCompanyAdmin, IsCompanyMember, IsEmailVerifiedOrSuperAdmin, IsOwnerOrAdmin, IsOwnerOrSuperAdmin,
)
from apps.core.mixins import CompanyIsolationMixin, SetCompanyOnCreateMixin
from apps.crm.tasks import maybe_notify_deadline_tomorrow_once
from apps.notifications.utils import create_notification
from apps.storage.s3_helpers import presigned_get_url_for_fieldfile
from .board_templates import BOARD_TEMPLATES, DEFAULT_TEMPLATE_ID
from .models import Board, Column, Label, Task, Comment, TaskHistory, Checklist, ChecklistItem, TaskAttachment
from .services import check_wip_limit
from .serializers import (
    BoardSerializer, BoardListSerializer, ColumnSerializer, ColumnWriteSerializer, ColumnReorderSerializer,
    LabelSerializer, TaskSerializer, TaskDetailSerializer, TaskMoveSerializer,
    CommentSerializer, TaskHistorySerializer,
    ChecklistSerializer, ChecklistItemSerializer, ChecklistItemWriteSerializer,
    TaskAttachmentSerializer,
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
            'Columns are automatically created based on the selected template. '
            'Optional field `template_id` sets the initial column structure. '
            'Available templates: basic (default), sales, recruitment, project. '
            'Use GET /crm/board-templates/ to get the full list with column names. '
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

        return qs.order_by('-created_at', '-id').prefetch_related('columns__tasks')

    def get_serializer_class(self):
        if self.action == 'list':
            return BoardListSerializer
        return BoardSerializer

    def create(self, request, *args, **kwargs):
        company = request.user.company
        current_boards = company.boards.filter(is_archived=False).count()
        if current_boards >= company.max_boards:
            lang = get_lang(request)
            return Response(
                {'detail': translate('crm.board_limit_exceeded', lang)},
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
        lang = get_lang(self.request)
        template_id = self.request.data.get('template_id') or DEFAULT_TEMPLATE_ID
        template = BOARD_TEMPLATES.get(template_id)
        if template is None:
            template = BOARD_TEMPLATES[DEFAULT_TEMPLATE_ID]
        for i, key in enumerate(template['column_keys']):
            Column.objects.create(board=board, name=translate(key, lang), position=i)

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

            if 'is_archived' not in request.query_params:
                tasks_qs = tasks_qs.filter(is_archived=False)

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
                            'detail': 'Cannot unarchive: board limit reached for your plan.',
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
                lang = get_lang(request)
                return Response(
                    {'detail': translate('crm.board_unarchive_limit', lang)},
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

    # CompanyIsolationMixin not used: Column has no direct company FK, and the
    # queryset must also be scoped to a specific board (nested resource via
    # board_pk URL kwarg).  Both concerns are handled together in get_queryset()
    # via _get_board_or_403(), which validates company ownership of the board
    # and supplies the board filter in a single DB lookup.
    # Traversal path documented here for auditability:
    company_lookup_filter = 'board__company_id'

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
            raise NotFound()
        user = self.request.user
        if user.role != 'superadmin' and board.company_id != user.company_id:
            lang = get_lang(self.request)
            raise PermissionDenied(translate('crm.board_access_denied', lang))
        return board

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Column.objects.none()
        # CompanyIsolationMixin not used: Column has no direct company FK.
        # Isolation is enforced via _get_board_or_403() which validates that
        # the parent board belongs to the request user's company.
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
            raise_validation_error('move_to', 'crm.move_to_required')

        # Cannot delete the last column
        board_columns = Column.objects.filter(board=board)
        if board_columns.count() <= 1:
            raise_validation_error('detail', 'crm.last_column_cannot_delete')

        # Validate move_to column
        try:
            move_to_id = int(move_to_id)
            target_column = board_columns.get(pk=move_to_id)
        except (ValueError, Column.DoesNotExist):
            raise_validation_error('move_to', 'crm.move_to_column_not_found')

        if target_column.pk == instance.pk:
            raise_validation_error('move_to', 'crm.move_to_same_column')

        active_in_source = Task.objects.filter(column=instance, is_archived=False).count()
        if active_in_source > 0:
            check_wip_limit(target_column, count=active_in_source)

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
                errors.append({'_i18n': True, 'key': 'crm.reorder_missing_columns',
                               'params': {'ids': str(sorted(missing))}})
            if extra:
                errors.append({'_i18n': True, 'key': 'crm.reorder_unknown_columns',
                               'params': {'ids': str(sorted(extra))}})
            raise ValidationError({'column_ids': errors})

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
            'By default only active (non-archived) tasks are returned. '
            'Pass `is_archived=true` to retrieve archived tasks instead. '
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
            OpenApiParameter(
                name='is_archived',
                type=bool,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    'Filter by archive status. '
                    'false (default) — active tasks only; true — archived tasks only.'
                ),
            ),
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
        summary='Create task',
        description=(
            'Creates a task. board_id and column_id must belong to the requesting user\'s company. '
            'assignee_id must be an employee of the same company. '
            'label_ids must belong to the same company.'
        ),
        request=inline_serializer(
            name='TaskCreateRequest',
            fields={
                'board_id': drf_serializers.IntegerField(
                    help_text='Board ID',
                ),
                'column_id': drf_serializers.IntegerField(
                    help_text='Column ID on the board',
                ),
                'title': drf_serializers.CharField(
                    max_length=255,
                    help_text='Task title',
                ),
                'description': drf_serializers.CharField(
                    required=False,
                    allow_blank=True,
                    help_text='Task description',
                ),
                'priority': drf_serializers.ChoiceField(
                    choices=['low', 'medium', 'high', 'critical'],
                    help_text='Task priority',
                ),
                'deadline': drf_serializers.DateField(
                    required=False,
                    allow_null=True,
                    help_text='Due date (YYYY-MM-DD)',
                ),
                'assignee_id': drf_serializers.IntegerField(
                    required=False,
                    allow_null=True,
                    help_text='Assignee user ID (must be an employee of the same company)',
                ),
                'label_ids': drf_serializers.ListField(
                    child=drf_serializers.IntegerField(),
                    required=False,
                    help_text='List of label IDs belonging to the board',
                ),
            },
        ),
        responses={
            201: TaskSerializer,
            400: OpenApiResponse(
                description='Validation error — assignee from a different company, invalid board/column.',
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
                    'title': 'Implement auth API',
                    'description': 'Add JWT authentication with refresh token support',
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
    ordering = ['-created_at']
    filterset_class = None  # TaskFilter applied manually in filter_queryset

    # CompanyIsolationMixin not used: get_queryset switches between Task.objects
    # and Task.all_objects depending on action (archived task visibility).
    # Traversal path documented here for auditability:
    company_lookup_filter = 'column__board__company_id'

    # Actions that must be able to see soft-deleted (archived) tasks so that
    # get_object() does not 404 on them.
    ARCHIVED_VISIBLE_ACTIONS = {'retrieve', 'history', 'unarchive', 'partial_update', 'update'}

    def get_queryset(self):
        # CompanyIsolationMixin not used: Task has no direct company FK.
        # Isolation is via column__board__company_id. Additionally, this
        # queryset switches between Task.objects (SoftDeleteManager) and
        # Task.all_objects depending on the action, which the mixin cannot
        # accommodate without being overridden entirely.
        user = self.request.user
        # Use all_objects (bypasses SoftDeleteManager) when:
        #   • listing with ?is_archived=true, OR
        #   • fetching a single task by PK (retrieve, history, unarchive) —
        #     the caller knows the ID and must be able to open archived tasks.
        want_archived = (
            (self.action == 'list'
             and self.request.query_params.get('is_archived', '').lower() == 'true')
            or self.action in self.ARCHIVED_VISIBLE_ACTIONS
        )
        manager = Task.all_objects if want_archived else Task.objects
        qs = manager.select_related(
            'column__board', 'assignee', 'created_by',
        ).prefetch_related('labels', 'checklists__items')
        if user.role == 'superadmin':
            return qs
        if user.company_id:
            return qs.filter(**{self.company_lookup_filter: user.company_id})
        return qs.none()

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return TaskDetailSerializer
        return TaskSerializer

    def filter_queryset(self, queryset):
        from .filters import TaskFilter

        # Apply TaskFilter (handles assignee_id, priority, label_ids, deadline enum,
        # board_id, column_id, search via icontains, deadline_from/deadline_to, is_archived)
        f = TaskFilter(self.request.query_params, queryset=queryset)
        queryset = f.qs

        if self.action == 'list' and 'is_archived' not in self.request.query_params:
            queryset = queryset.filter(is_archived=False)

        # Apply ordering via OrderingFilter
        ordering_filter = OrderingFilter()
        queryset = ordering_filter.filter_queryset(self.request, queryset, self)

        return queryset.distinct()

    def perform_create(self, serializer):
        task = serializer.save(created_by=self.request.user)
        # Normalize positions so the new task gets a clean sequential number
        # at the end of the column rather than inheriting any gaps.
        _normalize_positions(task.column)
        if task.assignee:
            create_notification(
                user=task.assignee,
                notification_type='task_assigned',
                title='Вам назначена задача',
                message=task.title,
                link=f'/crm/tasks/{task.pk}/',
            )
            # Email only when someone else assigns the task (avoid self-email noise).
            if task.assignee != self.request.user:
                import logging
                from apps.notifications.tasks import send_notification_email
                try:
                    send_notification_email.delay(
                        task.assignee.id,
                        'task_assigned',
                        {
                            'subject': 'Вам назначена задача',
                            'task_title': task.title,
                            'board_name': task.column.board.name if task.column else '',
                            'assigned_by': self.request.user.full_name,
                            'action_url': f'/crm/tasks/{task.pk}/',
                        },
                    )
                except Exception:
                    logging.getLogger(__name__).warning('Failed to enqueue notification email', exc_info=True)

        task = Task.objects.select_related('assignee', 'created_by', 'column').get(pk=task.pk)
        maybe_notify_deadline_tomorrow_once(task, skip_if_in_done_column=False)

    def perform_update(self, serializer):
        task = serializer.instance
        validated = serializer.validated_data

        # Prefetch relational fields needed for human-readable history values.
        task_prefetched = Task.all_objects.select_related('assignee', 'column').prefetch_related('labels').get(
            pk=task.pk
        )

        def _deadline_str(dt):
            """Normalise a deadline datetime to an ISO-8601 date string for comparison.

            deadline is stored as DateTimeField but is typically supplied as a date.
            Using isoformat() on the date portion avoids false mismatches caused by
            timezone suffix differences between the captured value and the DB-round-
            tripped value (e.g. '+06:00' vs 'UTC' representations).
            """
            if dt is None:
                return ''
            return dt.date().isoformat()

        # Capture old human-readable field values before saving.
        tracked_scalar_fields = {
            'title': task_prefetched.title,
            'description': task_prefetched.description,
            'priority': task_prefetched.priority,
            'deadline': _deadline_str(task_prefetched.deadline),
            'assignee': task_prefetched.assignee.full_name if task_prefetched.assignee else '',
            'column': task_prefetched.column.name,
            'is_archived': str(task_prefetched.is_archived),
        }
        # Determine which tracked fields were actually sent in this PATCH.
        # validated_data keys for relational fields use the source name:
        #   - assignee_id (write field) → source='assignee' → key 'assignee' in validated_data
        #   - column_id  (write field) → source='column'   → key 'column'   in validated_data
        requested_tracked = set()
        for field_key, vd_key in [
            ('title', 'title'), ('description', 'description'), ('priority', 'priority'),
            ('deadline', 'deadline'), ('assignee', 'assignee'), ('column', 'column'),
            ('is_archived', 'is_archived'),
        ]:
            if vd_key in validated:
                requested_tracked.add(field_key)

        # Capture old label IDs before saving.
        old_label_ids = set(task_prefetched.labels.values_list('id', flat=True))
        old_label_info = {
            label.id: json.dumps({'name': label.name, 'color': label.color})
            for label in task_prefetched.labels.all()
        }
        labels_in_request = 'labels' in validated

        old_assignee_id = task_prefetched.assignee_id

        instance = serializer.save()
        new_instance = Task.all_objects.select_related('assignee', 'column').get(pk=instance.pk)

        new_assignee_id = new_instance.assignee_id
        new_assignee = new_instance.assignee

        if new_assignee and new_assignee_id != old_assignee_id:
            create_notification(
                user=new_assignee,
                notification_type='task_assigned',
                title='Вам назначена задача',
                message=new_instance.title,
                link=f'/crm/tasks/{new_instance.pk}/',
            )
            if new_assignee != self.request.user:
                import logging
                from apps.notifications.tasks import send_notification_email
                try:
                    send_notification_email.delay(
                        new_assignee.id,
                        'task_assigned',
                        {
                            'subject': 'Вам назначена задача',
                            'task_title': new_instance.title,
                            'board_name': new_instance.column.board.name if new_instance.column else '',
                            'assigned_by': self.request.user.full_name,
                            'action_url': f'/crm/tasks/{new_instance.pk}/',
                        },
                    )
                except Exception:
                    logging.getLogger(__name__).warning('Failed to enqueue notification email', exc_info=True)

        # Log changes to scalar fields that were explicitly sent and actually changed.
        new_scalar_values = {
            'title': new_instance.title,
            'description': new_instance.description,
            'priority': new_instance.priority,
            'deadline': _deadline_str(new_instance.deadline),
            'assignee': new_instance.assignee.full_name if new_instance.assignee else '',
            'column': new_instance.column.name,
            'is_archived': str(new_instance.is_archived),
        }
        for field_key in requested_tracked:
            old_val = tracked_scalar_fields[field_key]
            new_val = new_scalar_values[field_key]
            if old_val != new_val:
                TaskHistory.objects.create(
                    task=new_instance,
                    user=self.request.user,
                    action='updated',
                    field_name=field_key,
                    old_value=old_val,
                    new_value=new_val,
                )

        # Log label changes using JSON-serialised label info (name + color) instead of plain names.
        if labels_in_request:
            new_label_ids = set(new_instance.labels.values_list('id', flat=True))
            new_label_info = {
                label.id: json.dumps({'name': label.name, 'color': label.color})
                for label in new_instance.labels.all()
            }
            for lid in new_label_ids - old_label_ids:
                TaskHistory.objects.create(
                    task=new_instance,
                    user=self.request.user,
                    action='label_added',
                    old_value='',
                    new_value=new_label_info.get(lid, str(lid)),
                )
            for lid in old_label_ids - new_label_ids:
                TaskHistory.objects.create(
                    task=new_instance,
                    user=self.request.user,
                    action='label_removed',
                    old_value=old_label_info.get(lid, str(lid)),
                    new_value='',
                )

        deadline_task = Task.all_objects.select_related('assignee', 'created_by', 'column').get(
            pk=new_instance.pk
        )
        new_deadline_str = _deadline_str(deadline_task.deadline)
        deadline_date_changed = ('deadline' in validated) and (
            _deadline_str(task_prefetched.deadline) != new_deadline_str
        )
        maybe_notify_deadline_tomorrow_once(
            deadline_task,
            skip_if_in_done_column=False,
            skip_same_calendar_day_dedup=deadline_date_changed,
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
            'is rejected when adding the task would exceed the limit. '
            'Returns `{"detail": "WIP limit reached (max N tasks)"}` with 400 when the WIP limit is hit. '
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
            raise_validation_error('column_id', 'crm.column_wrong_task_board')

        # Validate tenant isolation: target column's board must belong to the request user's company.
        # Superadmin bypasses this check.
        if request.user.role != 'superadmin':
            if target_column_obj.board.company_id != request.user.company_id:
                raise_validation_error('column_id', 'crm.column_wrong_company')

        check_wip_limit(target_column_obj, exclude_task_pk=task.pk)

        old_column = task.column
        old_column_name = old_column.name

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

        # Re-normalize both columns so positions are always sequential.
        _normalize_positions(old_column)
        _normalize_positions(target_column_obj)

        task.refresh_from_db()
        task = Task.objects.select_related('assignee', 'created_by').get(pk=task.pk)

        TaskHistory.objects.create(
            task=task, user=request.user, action='moved',
            old_value=old_column_name, new_value=target_column_obj.name,
        )

        if old_column.pk != target_column_obj.pk:
            actor_id = request.user.id
            recipients = []
            if task.assignee_id and task.assignee_id != actor_id and task.assignee:
                recipients.append(task.assignee)
            if task.created_by_id and task.created_by_id != actor_id and task.created_by:
                if task.created_by not in recipients:
                    recipients.append(task.created_by)
            for recipient in recipients:
                create_notification(
                    user=recipient,
                    notification_type='task_moved',
                    title='Задача перемещена',
                    message=f'«{task.title}»: {old_column_name} → {target_column_obj.name}',
                    link=f'/crm/tasks/{task.pk}/',
                )
        return Response(TaskSerializer(task, context={'request': request}).data)

    @extend_schema(
        tags=['CRM'],
        summary='My tasks grouped by board',
        description=(
            'Returns all non-archived tasks assigned to the current user, grouped by board. '
            'Each group contains up to 50 tasks ordered by `-created_at`, a `total` count, '
            'and a `has_more` flag that is `true` when the board has more than 50 assigned tasks. '
            'Boards are ordered alphabetically by name. '
            'Superadmin sees tasks across all companies; other roles see only their own company.'
        ),
        responses={
            200: inline_serializer(
                name='MyTasksGroupedResponse',
                fields={
                    'groups': drf_serializers.ListField(
                        child=inline_serializer(
                            name='MyTasksBoardGroup',
                            fields={
                                'board_id': drf_serializers.IntegerField(),
                                'board_name': drf_serializers.CharField(),
                                'tasks': TaskSerializer(many=True),
                                'total': drf_serializers.IntegerField(),
                                'has_more': drf_serializers.BooleanField(),
                            },
                        ),
                    ),
                },
            ),
            401: OpenApiResponse(description='Not authenticated.'),
            403: OpenApiResponse(description='Company members only.'),
        },
    )
    @action(detail=False, methods=['get'], url_path='my')
    def my_tasks(self, request):
        from collections import defaultdict

        user = request.user
        TASK_LIMIT = 50

        # Base queryset — assigned to me, not archived, board not archived.
        qs = Task.objects.select_related(
            'column__board', 'assignee', 'created_by',
        ).prefetch_related('labels', 'checklists__items').filter(
            assignee=user,
            is_archived=False,
            column__board__is_archived=False,
        )

        if user.role != 'superadmin':
            if not user.company_id:
                return Response({'groups': []})
            qs = qs.filter(column__board__company_id=user.company_id)

        # Apply all registered filters (priority, deadline, search, ordering …)
        # from query params.  filter_queryset() applies TaskFilter + OrderingFilter;
        # its `is_archived` guard only fires for action=='list', so it is a safe
        # no-op here (our base queryset already excludes archived tasks).
        qs = self.filter_queryset(qs)

        # Group by board AFTER filtering + ordering.
        groups = defaultdict(list)
        board_meta = {}
        for task in qs:
            board = task.column.board
            board_meta[board.id] = board.name
            groups[board.id].append(task)

        result = []
        for board_id, tasks in sorted(groups.items(), key=lambda x: board_meta[x[0]]):
            total = len(tasks)
            sliced = tasks[:TASK_LIMIT]
            serialized = TaskSerializer(sliced, many=True, context={'request': request})
            result.append({
                'board_id': board_id,
                'board_name': board_meta[board_id],
                'tasks': serialized.data,
                'total': total,
                'has_more': total > TASK_LIMIT,
            })

        return Response({'groups': result})

    @extend_schema(
        tags=['CRM'],
        summary='Task history',
        responses={200: TaskHistorySerializer(many=True), 404: OpenApiResponse(description='Not found')},
    )
    @action(detail=True, methods=['get'], url_path='history')
    def history(self, request, pk=None):
        task = self.get_object()
        qs = TaskHistory.objects.filter(task=task).order_by('-created_at')
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = TaskHistorySerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    @extend_schema(
        tags=['CRM'],
        summary='Archive task',
        description=(
            'Archives the task: sets is_deleted=True and is_archived=True. '
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
        task.is_archived = True
        task.save(update_fields=['is_archived'])
        _normalize_positions(column)
        TaskHistory.objects.create(
            task=task,
            user=request.user,
            action='archived',
            old_value='False',
            new_value='True',
        )
        lang = get_lang(request)
        return Response({'detail': translate('crm.task_archived', lang)}, status=status.HTTP_200_OK)

    @extend_schema(
        tags=['CRM'],
        summary='Unarchive task',
        description=(
            'Restores an archived task: sets is_deleted=False and is_archived=False. '
            'Returns 400 with wip_limit_exceeded if the column WIP limit would be breached. '
            'Permission: company members only (employee, company_admin, superadmin).'
        ),
        request=None,
        responses={
            200: inline_serializer(
                name='TaskUnarchiveResponse',
                fields={'detail': drf_serializers.CharField()},
            ),
            400: OpenApiResponse(description='WIP limit exceeded.'),
            401: OpenApiResponse(description='Not authenticated.'),
            403: OpenApiResponse(description='Company members only.'),
            404: OpenApiResponse(description='Task not found.'),
        },
    )
    @action(detail=True, methods=['post'], url_path='unarchive')
    def unarchive(self, request, pk=None):
        # get_queryset() already uses Task.all_objects for the 'unarchive' action,
        # so get_object() correctly finds soft-deleted (archived) tasks.
        task = self.get_object()

        lang = get_lang(request)
        if not task.is_archived:
            return Response({'detail': translate('crm.task_not_archived', lang)}, status=status.HTTP_400_BAD_REQUEST)

        check_wip_limit(task.column, exclude_task_pk=task.pk)

        task.is_deleted = False
        task.deleted_at = None
        task.is_archived = False
        task.save(update_fields=['is_deleted', 'deleted_at', 'is_archived'])
        _normalize_positions(task.column)
        TaskHistory.objects.create(
            task=task,
            user=request.user,
            action='unarchived',
            old_value='True',
            new_value='False',
        )
        return Response({'detail': translate('crm.task_unarchived', lang)}, status=status.HTTP_200_OK)


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
        if self.action == 'partial_update':
            perm = IsOwnerOrSuperAdmin()
            perm.owner_field = 'author'
            return [IsCompanyMember(), IsEmailVerifiedOrSuperAdmin(), perm]
        if self.action == 'destroy':
            perm = IsOwnerOrAdmin()
            perm.owner_field = 'author'
            return [IsCompanyMember(), IsEmailVerifiedOrSuperAdmin(), perm]
        return [IsCompanyMember(), IsEmailVerifiedOrSuperAdmin()]

    def _get_task_or_403(self):
        """
        Fetch the task identified by URL kwarg ``task_pk``.
        Raises NotFound if the task does not exist.
        Raises PermissionDenied if the task belongs to a different company (non-superadmin users only).
        Uses all_objects so that archived (soft-deleted) tasks are still accessible.
        """
        task_pk = self.kwargs.get('task_pk')
        try:
            task = Task.all_objects.select_related('column__board', 'assignee', 'created_by').get(pk=task_pk)
        except Task.DoesNotExist:
            raise NotFound()
        user = self.request.user
        if user.role != 'superadmin' and task.column.board.company_id != user.company_id:
            lang = get_lang(self.request)
            raise PermissionDenied(translate('crm.task_access_denied', lang))
        return task

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Comment.objects.none()
        # CompanyIsolationMixin not used: Comment has no direct company FK.
        # Isolation is enforced via _get_task_or_403() which traverses
        # task -> column -> board -> company.
        self._get_task_or_403()
        return Comment.objects.filter(task_id=self.kwargs.get('task_pk')).select_related('author')

    def perform_create(self, serializer):
        task = self._get_task_or_403()
        comment = serializer.save(author=self.request.user, task=task)
        self._notify_task_participants(task, comment)

    def _notify_task_participants(self, task, comment):
        """Notify task assignee and creator when a new comment is posted, skipping the author."""
        author_id = comment.author_id
        if author_id is None:
            author_id = self.request.user.pk

        # Re-load task so assignee/creator match persisted FKs (same pattern as task move).
        fresh = Task.all_objects.select_related('assignee', 'created_by').get(pk=task.pk)

        recipients = []
        if fresh.assignee_id and fresh.assignee_id != author_id and fresh.assignee:
            recipients.append(fresh.assignee)
        if fresh.created_by_id and fresh.created_by_id != author_id and fresh.created_by:
            if fresh.created_by not in recipients:
                recipients.append(fresh.created_by)

        link = f'/crm/tasks/{fresh.pk}/'
        for recipient in recipients:
            create_notification(
                user=recipient,
                notification_type='task_comment',
                title='Новый комментарий к задаче',
                message=fresh.title,
                link=link,
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

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.role == 'superadmin' and self.action == 'list':
            company_id = self.request.query_params.get('company_id')
            if company_id:
                qs = qs.filter(company_id=company_id)
        return qs

    def perform_create(self, serializer):
        user = self.request.user
        if user.role != 'superadmin':
            super().perform_create(serializer)
            return
        company_id = self.request.query_params.get('company_id')
        if not company_id:
            raise ValidationError({'company_id': 'Superadmin must provide company_id query parameter.'})
        try:
            company = Company.objects.get(pk=company_id)
        except Company.DoesNotExist:
            raise ValidationError({'company_id': 'Company not found.'})
        serializer.save(company=company)


class ChecklistViewSet(viewsets.ViewSet):
    """
    Checklists nested under a task.

    Routes:
      POST   /crm/tasks/<task_id>/checklists/   — create checklist
      GET    /crm/tasks/<task_id>/checklists/   — list checklists
      DELETE /crm/checklists/<id>/              — delete checklist

    CompanyIsolationMixin not used: Checklist has no direct company FK.
    Isolation is enforced per-action via _get_task_or_403() / inline checks
    that traverse checklist -> task -> column -> board -> company.
    """
    permission_classes = [IsCompanyMember, IsEmailVerifiedOrSuperAdmin]

    def _get_task_or_403(self, task_id):
        """Return the Task if it belongs to the request user's company; raise otherwise.
        Uses all_objects so that archived (soft-deleted) tasks are still accessible."""
        user = self.request.user
        try:
            task = Task.all_objects.select_related('column__board').get(pk=task_id)
        except Task.DoesNotExist:
            raise NotFound()
        if user.role != 'superadmin' and task.column.board.company_id != user.company_id:
            lang = get_lang(self.request)
            raise PermissionDenied(translate('crm.task_access_denied', lang))
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
        summary='Update checklist',
        operation_id='checklist_partial_update',
        request=inline_serializer(
            name='ChecklistPatchRequest',
            fields={
                'title': drf_serializers.CharField(required=False, help_text='Checklist title'),
            },
        ),
        responses={
            200: ChecklistSerializer,
            400: OpenApiResponse(description='Validation error'),
            401: OpenApiResponse(description='Not authenticated.'),
            403: OpenApiResponse(description='Forbidden — company members only.'),
            404: OpenApiResponse(description='Checklist not found.'),
        },
        examples=[
            OpenApiExample(
                name='Rename checklist',
                value={'title': 'Definition of Done'},
                request_only=True,
            ),
        ],
    )
    def partial_update(self, request, pk=None):
        user = request.user
        try:
            checklist = Checklist.objects.select_related('task__column__board').get(pk=pk)
        except Checklist.DoesNotExist:
            raise NotFound()
        if user.role != 'superadmin' and checklist.task.column.board.company_id != user.company_id:
            lang = get_lang(request)
            raise PermissionDenied(translate('crm.checklist_access_denied', lang))
        serializer = ChecklistSerializer(checklist, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(ChecklistSerializer(checklist).data)

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
            raise NotFound()
        if user.role != 'superadmin' and checklist.task.column.board.company_id != user.company_id:
            lang = get_lang(request)
            raise PermissionDenied(translate('crm.checklist_access_denied', lang))
        checklist.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ChecklistItemViewSet(viewsets.ViewSet):
    """
    Checklist items.

    Routes:
      POST   /crm/checklists/<id>/items/   — add item to checklist
      PATCH  /crm/items/<id>/              — update item
      DELETE /crm/items/<id>/              — delete item, re-normalise order

    CompanyIsolationMixin not used: ChecklistItem has no direct company FK.
    Isolation is enforced per-action via _get_checklist_or_403() / _get_item_or_403()
    which traverse item -> checklist -> task -> column -> board -> company.
    """
    permission_classes = [IsCompanyMember, IsEmailVerifiedOrSuperAdmin]

    def _get_checklist_or_403(self, checklist_id):
        user = self.request.user
        try:
            checklist = Checklist.objects.select_related('task__column__board').get(pk=checklist_id)
        except Checklist.DoesNotExist:
            raise NotFound()
        if user.role != 'superadmin' and checklist.task.column.board.company_id != user.company_id:
            lang = get_lang(self.request)
            raise PermissionDenied(translate('crm.checklist_access_denied', lang))
        return checklist

    def _get_item_or_403(self, item_id):
        user = self.request.user
        try:
            item = ChecklistItem.objects.select_related('checklist__task__column__board').get(pk=item_id)
        except ChecklistItem.DoesNotExist:
            raise NotFound()
        if user.role != 'superadmin' and item.checklist.task.column.board.company_id != user.company_id:
            lang = get_lang(self.request)
            raise PermissionDenied(translate('crm.checklist_item_access_denied', lang))
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


@extend_schema_view(
    list=extend_schema(
        tags=['CRM'],
        summary='List attachments for a task',
        responses={
            200: TaskAttachmentSerializer(many=True),
            401: OpenApiResponse(description='Not authenticated.'),
            403: OpenApiResponse(description='Company members only or task not accessible.'),
            404: OpenApiResponse(description='Task not found.'),
        },
    ),
    create=extend_schema(
        tags=['CRM'],
        summary='Add attachment to a task',
        description=(
            'Two modes:\n'
            '**Mode A — Direct upload**: multipart/form-data with `file` field. '
            'Max 50 MB. Allowed: PDF, Word, Excel, PNG, JPEG, GIF.\n'
            '**Mode B — Link from Storage**: JSON body with `storage_file_id` (must belong to same company).'
        ),
        request=TaskAttachmentSerializer,
        responses={
            201: TaskAttachmentSerializer,
            400: OpenApiResponse(description='Validation error (size, type, or missing fields).'),
            401: OpenApiResponse(description='Not authenticated.'),
            403: OpenApiResponse(description='Company members only or task not accessible.'),
            404: OpenApiResponse(description='Task or storage file not found.'),
        },
    ),
    destroy=extend_schema(
        tags=['CRM'],
        summary='Delete a task attachment',
        description=(
            'If the attachment is a direct upload, the underlying file is removed from storage. '
            'If it links to a Storage file, only the link record is removed. '
            'Only the uploader or a company_admin may delete.'
        ),
        responses={
            204: OpenApiResponse(description='Attachment deleted.'),
            401: OpenApiResponse(description='Not authenticated.'),
            403: OpenApiResponse(description='Only uploader or company admin can delete.'),
            404: OpenApiResponse(description='Attachment not found.'),
        },
    ),
    download=extend_schema(
        tags=['CRM'],
        summary='Get presigned download URL for task attachment',
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name='TaskAttachmentDownloadResponse',
                    fields={
                        'url': drf_serializers.URLField(),
                        'expires_in': drf_serializers.IntegerField(),
                    },
                ),
                description='Presigned GET URL',
            ),
            401: OpenApiResponse(description='Not authenticated.'),
            403: OpenApiResponse(description='Forbidden'),
            404: OpenApiResponse(description='Attachment or file not found.'),
        },
    ),
)
class TaskAttachmentViewSet(viewsets.GenericViewSet):
    serializer_class = TaskAttachmentSerializer
    permission_classes = [IsCompanyMember, IsEmailVerifiedOrSuperAdmin]

    def _get_task_or_403(self):
        """Uses all_objects so that archived (soft-deleted) tasks are still accessible."""
        task_pk = self.kwargs.get('task_pk')
        try:
            task = Task.all_objects.select_related('column__board').get(pk=task_pk)
        except Task.DoesNotExist:
            raise NotFound()
        user = self.request.user
        if user.role != 'superadmin' and task.column.board.company_id != user.company_id:
            lang = get_lang(self.request)
            raise PermissionDenied(translate('crm.task_access_denied', lang))
        return task

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return TaskAttachment.objects.none()
        # CompanyIsolationMixin not used: TaskAttachment has no direct company FK.
        # Isolation is enforced via _get_task_or_403() which traverses
        # task -> column -> board -> company.
        task = self._get_task_or_403()
        return TaskAttachment.objects.filter(task=task).select_related('uploaded_by', 'storage_file')

    def list(self, request, task_pk=None):
        qs = self.get_queryset()
        serializer = self.get_serializer(qs, many=True)
        return Response(serializer.data)

    def download(self, request, task_pk=None, pk=None):
        self._get_task_or_403()
        try:
            attachment = TaskAttachment.objects.select_related('storage_file').get(pk=pk, task_id=task_pk)
        except TaskAttachment.DoesNotExist:
            raise NotFound('Attachment not found.')
        if attachment.storage_file_id:
            field_file = attachment.storage_file.file
        else:
            field_file = attachment.file
        if not field_file or not getattr(field_file, 'name', None):
            raise NotFound('Attachment file not found.')
        url, expires_in = presigned_get_url_for_fieldfile(field_file)
        if not url:
            raise NotFound('Attachment file not found.')
        return Response({'url': url, 'expires_in': expires_in})

    def create(self, request, task_pk=None):
        from apps.companies.limits import get_company_storage_used_bytes, notify_company_admins_limit_thresholds
        from apps.storage.models import File as StorageFile
        task = self._get_task_or_403()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        vd = serializer.validated_data
        file_obj = vd.get('file')
        storage_file_id = vd.get('storage_file_id')

        if file_obj:
            # Check storage quota before persisting the file.
            company = request.user.company
            if company is not None:
                current_used = get_company_storage_used_bytes(company)
                storage_limit_bytes = company.storage_limit_gb * 1024 * 1024 * 1024
                if current_used + file_obj.size > storage_limit_bytes:
                    raise LocalizedError(
                        code=STORAGE_LIMIT_EXCEEDED,
                        i18n_key='storage.limit_exceeded',
                    )

            attachment = TaskAttachment.objects.create(
                task=task,
                file=file_obj,
                filename=file_obj.name,
                file_size=file_obj.size,
                mime_type=getattr(file_obj, 'content_type', ''),
                uploaded_by=request.user,
                storage_file=None,
            )

            if company is not None:
                projected_used_gb = (current_used + file_obj.size) / (1024 ** 3)
                notify_company_admins_limit_thresholds(
                    company=company,
                    metric='storage',
                    current_value=round(projected_used_gb, 2),
                    limit_value=company.storage_limit_gb,
                )
        else:
            user = request.user
            sf_qs = StorageFile.objects.filter(pk=storage_file_id)
            if user.role != 'superadmin':
                sf_qs = sf_qs.filter(company_id=user.company_id)
            try:
                storage_file = sf_qs.get()
            except StorageFile.DoesNotExist:
                raise NotFound()

            attachment = TaskAttachment.objects.create(
                task=task,
                file=None,
                storage_file=storage_file,
                filename=storage_file.name,
                file_size=storage_file.file_size,
                mime_type=storage_file.content_type,
                uploaded_by=request.user,
            )

        output = self.get_serializer(attachment)
        return Response(output.data, status=status.HTTP_201_CREATED)

    def destroy(self, request, task_pk=None, pk=None):
        try:
            attachment = TaskAttachment.objects.select_related(
                'uploaded_by', 'storage_file', 'task__column__board',
            ).get(pk=pk, task__id=task_pk)
        except TaskAttachment.DoesNotExist:
            raise NotFound()

        # Object-level permission check: uploader, company_admin of same company, or superadmin
        user = request.user
        is_owner = attachment.uploaded_by == user
        is_admin_same_company = (
            user.role in ('superadmin', 'company_admin')
            and (user.role == 'superadmin' or attachment.company_id == user.company_id)
        )
        if not (is_owner or is_admin_same_company):
            raise PermissionDenied(translate('crm.attachment_delete_forbidden', get_lang(request)))

        # Delete file from disk only for direct uploads
        if not attachment.storage_file_id and attachment.file:
            attachment.file.delete(save=False)

        attachment.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(
    tags=['CRM'],
    summary='List available board templates',
    responses={200: OpenApiResponse(description='List of board templates with column names.')},
)
@api_view(['GET'])
@permission_classes([IsCompanyMember])
def board_templates_list(request):
    lang = get_lang(request)
    result = []
    for template_id, template in BOARD_TEMPLATES.items():
        result.append({
            'id': template_id,
            'name': translate(template['name_key'], lang),
            'columns': [translate(key, lang) for key in template['column_keys']],
        })
    return Response(result)
