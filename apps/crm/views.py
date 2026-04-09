from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiResponse

from apps.companies.limits import notify_company_admins_limit_thresholds
from apps.core.permissions import IsCompanyMember, IsEmailVerifiedOrSuperAdmin
from apps.core.mixins import CompanyIsolationMixin, SetCompanyOnCreateMixin
from .models import Board, Column, Label, Task, Comment, TaskHistory
from .serializers import (
    BoardSerializer, BoardListSerializer, ColumnSerializer,
    LabelSerializer, TaskSerializer, TaskMoveSerializer,
    CommentSerializer, TaskHistorySerializer,
)


@extend_schema_view(
    list=extend_schema(
        tags=['CRM'],
        summary='List boards',
        responses={200: BoardListSerializer(many=True)},
    ),
    retrieve=extend_schema(
        tags=['CRM'],
        summary='Get board with columns and tasks',
        responses={200: BoardSerializer, 404: OpenApiResponse(description='Not found')},
    ),
    create=extend_schema(
        tags=['CRM'],
        summary='Create board',
        request=BoardSerializer,
        responses={
            201: BoardSerializer,
            400: OpenApiResponse(description='Validation error'),
            403: OpenApiResponse(description='Company members only'),
        },
    ),
    partial_update=extend_schema(
        tags=['CRM'],
        summary='Update board',
        request=BoardSerializer,
        responses={200: BoardSerializer, 400: OpenApiResponse(description='Validation error')},
    ),
    destroy=extend_schema(
        tags=['CRM'],
        summary='Delete board',
        responses={204: OpenApiResponse(description='Deleted')},
    ),
)
class BoardViewSet(CompanyIsolationMixin, SetCompanyOnCreateMixin, viewsets.ModelViewSet):
    serializer_class = BoardSerializer
    permission_classes = [IsCompanyMember, IsEmailVerifiedOrSuperAdmin]

    def get_queryset(self):
        return Board.objects.filter(is_archived=False).prefetch_related('columns__tasks')

    def get_serializer_class(self):
        if self.action == 'list':
            return BoardListSerializer
        return BoardSerializer

    def create(self, request, *args, **kwargs):
        company = request.user.company
        current_boards = company.boards.count()
        if current_boards >= company.max_boards:
            raise ValidationError('Board limit reached')

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
        request=None,
        responses={
            200: OpenApiResponse(description='Board archived'),
            401: OpenApiResponse(description='Not authenticated'),
            404: OpenApiResponse(description='Not found'),
        },
    )
    @action(detail=True, methods=['post'], url_path='archive')
    def archive(self, request, pk=None):
        board = self.get_object()
        board.is_archived = True
        board.save(update_fields=['is_archived'])
        return Response({'detail': 'Board archived'})


@extend_schema_view(
    list=extend_schema(
        tags=['CRM'],
        summary='List columns of a board',
        responses={200: ColumnSerializer(many=True)},
    ),
    create=extend_schema(
        tags=['CRM'],
        summary='Add column to board',
        request=ColumnSerializer,
        responses={201: ColumnSerializer, 400: OpenApiResponse(description='Validation error')},
    ),
    partial_update=extend_schema(
        tags=['CRM'],
        summary='Update column',
        request=ColumnSerializer,
        responses={200: ColumnSerializer, 400: OpenApiResponse(description='Validation error')},
    ),
    destroy=extend_schema(
        tags=['CRM'],
        summary='Delete column',
        responses={204: OpenApiResponse(description='Deleted')},
    ),
)
class ColumnViewSet(viewsets.ModelViewSet):
    serializer_class = ColumnSerializer
    permission_classes = [IsCompanyMember, IsEmailVerifiedOrSuperAdmin]

    def get_queryset(self):
        return Column.objects.filter(board_id=self.kwargs.get('board_pk')).prefetch_related('tasks')


@extend_schema_view(
    list=extend_schema(
        tags=['CRM'],
        summary='List tasks',
        responses={200: TaskSerializer(many=True)},
    ),
    retrieve=extend_schema(
        tags=['CRM'],
        summary='Get task details',
        responses={200: TaskSerializer, 404: OpenApiResponse(description='Not found')},
    ),
    create=extend_schema(
        tags=['CRM'],
        summary='Create task',
        request=TaskSerializer,
        responses={
            201: TaskSerializer,
            400: OpenApiResponse(description='Validation error'),
            403: OpenApiResponse(description='Company members only'),
        },
    ),
    partial_update=extend_schema(
        tags=['CRM'],
        summary='Update task',
        request=TaskSerializer,
        responses={200: TaskSerializer, 400: OpenApiResponse(description='Validation error')},
    ),
    destroy=extend_schema(
        tags=['CRM'],
        summary='Delete task',
        responses={204: OpenApiResponse(description='Deleted')},
    ),
)
class TaskViewSet(viewsets.ModelViewSet):
    serializer_class = TaskSerializer
    permission_classes = [IsCompanyMember, IsEmailVerifiedOrSuperAdmin]
    search_fields = ['title']

    def get_queryset(self):
        user = self.request.user
        qs = Task.objects.select_related('column__board', 'assignee')
        if user.role == 'superadmin':
            return qs
        if user.company_id:
            return qs.filter(column__board__company=user.company)
        return qs.none()

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @extend_schema(
        tags=['CRM'],
        summary='Move task (drag & drop)',
        request=TaskMoveSerializer,
        responses={
            200: TaskSerializer,
            400: OpenApiResponse(description='Validation error'),
            404: OpenApiResponse(description='Task not found'),
        },
    )
    @action(detail=True, methods=['post'], url_path='move')
    def move(self, request, pk=None):
        task = self.get_object()
        serializer = TaskMoveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        old_col = task.column_id
        task.column_id = serializer.validated_data['column_id']
        task.position = serializer.validated_data['position']
        task.save(update_fields=['column', 'position'])
        # TODO: проверить WIP-лимит колонки, записать TaskHistory
        TaskHistory.objects.create(
            task=task, user=request.user, action='moved',
            old_value=str(old_col), new_value=str(task.column_id),
        )
        return Response(TaskSerializer(task).data)

    @extend_schema(
        tags=['CRM'],
        summary='My tasks across all boards',
        responses={200: TaskSerializer(many=True)},
    )
    @action(detail=False, methods=['get'], url_path='my')
    def my_tasks(self, request):
        qs = self.get_queryset().filter(assignee=request.user, is_archived=False)
        serializer = TaskSerializer(qs, many=True)
        return Response(serializer.data)

    @extend_schema(
        tags=['CRM'],
        summary='Task history',
        responses={200: TaskHistorySerializer(many=True), 404: OpenApiResponse(description='Not found')},
    )
    @action(detail=True, methods=['get'], url_path='history')
    def history(self, request, pk=None):
        task = self.get_object()
        return Response(TaskHistorySerializer(task.history.all(), many=True).data)


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
