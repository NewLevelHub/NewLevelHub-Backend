from django.db.models import F, Q, Sum
from django.http import FileResponse
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied, ValidationError
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiResponse
from apps.companies.limits import (
    get_company_storage_used_bytes,
    notify_company_admins_limit_thresholds,
)
from apps.core.permissions import IsCompanyMember
from apps.notifications.utils import create_notification
from .models import Folder, File, FileShare
from .serializers import FolderSerializer, FileSerializer, FileShareSerializer, StorageUsageSerializer


@extend_schema_view(
    list=extend_schema(
        tags=['Storage'],
        summary='List folders',
        responses={200: FolderSerializer(many=True)},
    ),
    create=extend_schema(
        tags=['Storage'],
        summary='Create folder',
        request=FolderSerializer,
        responses={
            201: FolderSerializer,
            400: OpenApiResponse(description='Validation error'),
            401: OpenApiResponse(description='Not authenticated'),
        },
    ),
    partial_update=extend_schema(
        tags=['Storage'],
        summary='Rename / move folder',
        request=FolderSerializer,
        responses={200: FolderSerializer, 400: OpenApiResponse(description='Validation error')},
    ),
    destroy=extend_schema(
        tags=['Storage'],
        summary='Delete folder',
        responses={204: OpenApiResponse(description='Deleted'), 401: OpenApiResponse(description='Not authenticated')},
    ),
)
class FolderViewSet(viewsets.ModelViewSet):
    serializer_class = FolderSerializer
    permission_classes = [IsCompanyMember]

    def get_queryset(self):
        user = self.request.user
        if user.role == 'superadmin':
            queryset = Folder.objects.all()
        else:
            queryset = (
                Folder.objects.filter(owner=user, scope='personal')
                | Folder.objects.filter(company=user.company, scope='company')
            ).distinct()

        scope = self.request.query_params.get('scope')
        if scope in ('personal', 'company'):
            queryset = queryset.filter(scope=scope)

        parent_id = self.request.query_params.get('parent_id')
        if parent_id is not None:
            if parent_id == 'null':
                queryset = queryset.filter(parent__isnull=True)
            else:
                try:
                    queryset = queryset.filter(parent_id=int(parent_id))
                except (TypeError, ValueError):
                    raise ValidationError({'parent_id': 'Must be an integer or "null".'})
        return queryset.order_by('-created_at')

    def _resolve_scope(self):
        if 'is_company_shared' in self.request.data:
            raw = self.request.data.get('is_company_shared')
            return 'company' if str(raw).lower() in ('1', 'true', 'yes', 'on') else 'personal'
        requested_scope = self.request.data.get('scope')
        if requested_scope in ('personal', 'company'):
            return requested_scope
        return 'personal'

    def _resolve_parent(self, scope):
        parent_id = self.request.data.get('parent_id', self.request.data.get('parent'))
        if parent_id in (None, '', 'null'):
            return None
        try:
            parent_id = int(parent_id)
        except (TypeError, ValueError):
            raise ValidationError({'parent_id': 'Must be an integer, null, or empty.'})

        try:
            parent = Folder.objects.get(pk=parent_id)
        except Folder.DoesNotExist:
            raise ValidationError({'parent_id': 'Parent folder not found.'})

        user = self.request.user
        if scope == 'personal':
            if parent.scope != 'personal' or parent.owner_id != user.id:
                raise ValidationError({'parent_id': 'Personal parent folder is not accessible.'})
        else:
            if user.role != 'superadmin' and (parent.scope != 'company' or parent.company_id != user.company_id):
                raise ValidationError({'parent_id': 'Company parent folder is not accessible.'})
        return parent

    def create(self, request, *args, **kwargs):
        scope = self._resolve_scope()
        parent = self._resolve_parent(scope)

        payload = {'name': request.data.get('name'), 'scope': scope, 'parent': parent.id if parent else None}
        serializer = self.get_serializer(data=payload)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user, company=self.request.user.company)

    def retrieve(self, request, *args, **kwargs):
        folder = self.get_object()
        user = request.user
        data = self.get_serializer(folder).data

        if folder.scope == 'personal':
            child_folders = Folder.objects.filter(parent=folder, scope='personal', owner=user)
            files = File.objects.filter(folder=folder, owner=user)
        else:
            if user.role == 'superadmin':
                child_folders = Folder.objects.filter(parent=folder, scope='company')
                files = File.objects.filter(folder=folder)
            else:
                child_folders = Folder.objects.filter(parent=folder, scope='company', company=user.company)
                files = File.objects.filter(folder=folder, company=user.company)

        data['folders'] = FolderSerializer(child_folders, many=True).data
        data['files'] = FileSerializer(files, many=True).data
        return Response(data)


@extend_schema_view(
    list=extend_schema(
        tags=['Storage'],
        summary='List files',
        responses={200: FileSerializer(many=True)},
    ),
    create=extend_schema(
        tags=['Storage'],
        summary='Upload file',
        request=FileSerializer,
        responses={
            201: FileSerializer,
            400: OpenApiResponse(description='Validation error or quota exceeded'),
            401: OpenApiResponse(description='Not authenticated'),
        },
    ),
    destroy=extend_schema(
        tags=['Storage'],
        summary='Delete file (soft)',
        responses={204: OpenApiResponse(description='Deleted'), 401: OpenApiResponse(description='Not authenticated')},
    ),
)
class FileViewSet(viewsets.ModelViewSet):
    serializer_class = FileSerializer
    permission_classes = [IsCompanyMember]
    search_fields = ['name']
    ordering_fields = ['name', 'file_size', 'size', 'created_at']
    _PERMISSION_LEVELS = {'view': 1, 'download': 2, 'full': 3}

    def get_queryset(self):
        user = self.request.user
        if user.role == 'superadmin':
            queryset = File.objects.all()
        else:
            # Company scope is visible to all company members.
            company_files = File.objects.filter(company=user.company, folder__scope='company')
            company_root_files = File.objects.filter(company=user.company, folder__isnull=True)

            # Personal scope is visible only to the owner, plus explicitly shared files.
            personal_owned = File.objects.filter(owner=user, folder__scope='personal')
            personal_shared = File.objects.filter(shares__shared_with=user, folder__scope='personal')

            # Keep compatibility for legacy rows without folder:
            # owner keeps access; non-owners must use explicit sharing.
            legacy_owned = File.objects.filter(owner=user, folder__isnull=True)
            legacy_shared = File.objects.filter(shares__shared_with=user, folder__isnull=True)

            queryset = (
                company_files
                | company_root_files
                | personal_owned
                | personal_shared
                | legacy_owned
                | legacy_shared
            ).distinct()

        scope = self.request.query_params.get('scope')
        if scope in ('personal', 'company'):
            if scope == 'company':
                queryset = queryset.filter(
                    Q(folder__scope='company')
                    | Q(folder__isnull=True, company_id=user.company_id)
                )
            else:
                queryset = queryset.filter(
                    Q(folder__scope='personal')
                    | Q(folder__isnull=True, company__isnull=True)
                )

        folder_id = self.request.query_params.get('folder_id')
        if folder_id is not None:
            if folder_id == 'null':
                queryset = queryset.filter(folder__isnull=True)
            else:
                try:
                    queryset = queryset.filter(folder_id=int(folder_id))
                except (TypeError, ValueError):
                    raise ValidationError({'folder_id': 'Must be an integer or "null".'})

        return queryset.annotate(size=F('file_size')).order_by('-created_at')

    def _resolve_scope(self):
        if 'is_company_shared' in self.request.data:
            raw = self.request.data.get('is_company_shared')
            return 'company' if str(raw).lower() in ('1', 'true', 'yes', 'on') else 'personal'
        requested_scope = self.request.data.get('scope')
        if requested_scope in ('personal', 'company'):
            return requested_scope
        return 'personal'

    def _resolve_folder(self, folder_id):
        if folder_id in (None, '', 'null'):
            return None
        try:
            folder = Folder.objects.get(pk=int(folder_id))
        except (TypeError, ValueError, Folder.DoesNotExist):
            raise ValidationError({'folder_id': 'Folder not found.'})

        user = self.request.user
        if folder.scope == 'personal' and folder.owner_id != user.id:
            raise ValidationError({'folder_id': 'Personal folder is not accessible.'})
        if folder.scope == 'company' and user.role != 'superadmin' and folder.company_id != user.company_id:
            raise ValidationError({'folder_id': 'Company folder is not accessible.'})
        return folder

    def _get_share_for_user(self, file_obj, user):
        return FileShare.objects.filter(file=file_obj, shared_with=user).first()

    def _ensure_file_permission(self, file_obj, required_permission):
        user = self.request.user
        if user.role == 'superadmin' or file_obj.owner_id == user.id:
            return

        folder = getattr(file_obj, 'folder', None)
        if (
            folder is not None
            and folder.scope == 'company'
            and user.company_id is not None
            and file_obj.company_id == user.company_id
            and (
                required_permission in ('view', 'download')
                or (required_permission == 'full' and user.role == 'company_admin')
            )
        ):
            return

        share = self._get_share_for_user(file_obj, user)
        if share is None:
            raise PermissionDenied('You do not have access to this file.')

        share_level = self._PERMISSION_LEVELS.get(share.permission, 0)
        required_level = self._PERMISSION_LEVELS.get(required_permission, 0)
        if share_level < required_level:
            raise PermissionDenied('You do not have enough permissions for this file action.')

    def create(self, request, *args, **kwargs):
        uploaded_file = request.FILES.get('file')
        if uploaded_file is None:
            return Response({'detail': 'File is required'}, status=status.HTTP_400_BAD_REQUEST)

        uploaded_size = uploaded_file.size if uploaded_file else 0
        if uploaded_size > 100 * 1024 * 1024:
            return Response({'detail': 'File size exceeds 100 MB'}, status=status.HTTP_400_BAD_REQUEST)

        company = request.user.company
        if company is not None:
            current_storage_used = get_company_storage_used_bytes(company)
            storage_limit_bytes = company.storage_limit_gb * 1024 * 1024 * 1024
            if current_storage_used + uploaded_size > storage_limit_bytes:
                return Response({'detail': 'Storage limit exceeded'}, status=status.HTTP_400_BAD_REQUEST)
        else:
            current_storage_used = 0

        response = super().create(request, *args, **kwargs)
        if company is not None:
            projected_used_gb = (current_storage_used + uploaded_size) / (1024 ** 3)
            notify_company_admins_limit_thresholds(
                company=company,
                metric='storage',
                current_value=round(projected_used_gb, 2),
                limit_value=company.storage_limit_gb,
            )
        return response

    def perform_create(self, serializer):
        f = self.request.FILES.get('file')
        folder = self._resolve_folder(self.request.data.get('folder_id', self.request.data.get('folder')))
        scope = folder.scope if folder is not None else self._resolve_scope()
        company = self.request.user.company if scope == 'company' else None
        serializer.save(
            owner=self.request.user,
            company=company,
            file_size=f.size if f else 0,
            content_type=f.content_type if f else '',
            folder=folder,
        )

    def perform_destroy(self, instance):
        instance.soft_delete()

    def retrieve(self, request, *args, **kwargs):
        file_obj = self.get_object()
        self._ensure_file_permission(file_obj, 'view')
        serializer = self.get_serializer(file_obj)
        return Response(serializer.data)

    def update(self, request, *args, **kwargs):
        file_obj = self.get_object()
        self._ensure_file_permission(file_obj, 'full')
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        file_obj = self.get_object()
        self._ensure_file_permission(file_obj, 'full')
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        file_obj = self.get_object()
        self._ensure_file_permission(file_obj, 'full')
        return super().destroy(request, *args, **kwargs)

    @extend_schema(
        tags=['Storage'],
        summary='Download file as attachment',
        responses={200: OpenApiResponse(description='File content'), 404: OpenApiResponse(description='Not found')},
    )
    @action(detail=True, methods=['get'])
    def download(self, request, pk=None):
        file_obj = self.get_object()
        self._ensure_file_permission(file_obj, 'download')
        file_handle = file_obj.file.open('rb')
        return FileResponse(
            file_handle,
            as_attachment=True,
            filename=file_obj.name,
            content_type=file_obj.content_type or 'application/octet-stream',
        )

    @extend_schema(
        tags=['Storage'],
        summary='Move file to folder',
        request=None,
        responses={200: FileSerializer, 400: OpenApiResponse(description='Validation error')},
    )
    @action(detail=True, methods=['post'])
    def move(self, request, pk=None):
        file_obj = self.get_object()
        self._ensure_file_permission(file_obj, 'full')
        folder = self._resolve_folder(request.data.get('folder_id', request.data.get('folder')))
        file_obj.folder = folder
        file_obj.save(update_fields=['folder', 'updated_at'])
        serializer = self.get_serializer(file_obj)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=['Storage'],
        summary='List shares for file',
        responses={200: FileShareSerializer(many=True), 403: OpenApiResponse(description='Forbidden')},
    )
    @action(detail=True, methods=['get'])
    def shares(self, request, pk=None):
        file_obj = self.get_object()
        if request.user.role != 'superadmin' and file_obj.owner_id != request.user.id:
            raise PermissionDenied('Only the file owner can see share recipients.')

        queryset = FileShare.objects.filter(file=file_obj).order_by('-created_at')
        page = self.paginate_queryset(queryset)
        serializer = FileShareSerializer(
            page if page is not None else queryset,
            many=True,
            context={'request': request},
        )
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)


@extend_schema_view(
    list=extend_schema(
        tags=['Storage'],
        summary='List file shares',
        responses={200: FileShareSerializer(many=True)},
    ),
    create=extend_schema(
        tags=['Storage'],
        summary='Share file',
        request=FileShareSerializer,
        responses={
            201: FileShareSerializer,
            400: OpenApiResponse(description='Validation error'),
            401: OpenApiResponse(description='Not authenticated'),
        },
    ),
    destroy=extend_schema(
        tags=['Storage'],
        summary='Revoke share',
        responses={
            204: OpenApiResponse(description='Share revoked'),
            401: OpenApiResponse(description='Not authenticated'),
        },
    ),
)
class FileShareViewSet(viewsets.ModelViewSet):
    serializer_class = FileShareSerializer
    permission_classes = [IsCompanyMember]
    http_method_names = ['get', 'post', 'patch', 'delete']

    def get_queryset(self):
        user = self.request.user
        shared_with_me = str(self.request.query_params.get('shared_with_me', '')).lower() in ('1', 'true', 'yes', 'on')

        if shared_with_me:
            return FileShare.objects.filter(shared_with=user).order_by('-created_at')

        if self.request.method in ('PATCH', 'PUT', 'DELETE'):
            return FileShare.objects.filter(shared_by=user).order_by('-created_at')

        return (
            FileShare.objects.filter(shared_by=user)
            | FileShare.objects.filter(shared_with=user)
        ).order_by('-created_at')

    def perform_create(self, serializer):
        share = serializer.save(shared_by=self.request.user)
        create_notification(
            user=share.shared_with,
            notification_type='announcement_company',
            title='File shared with you',
            message=f'{share.shared_by.full_name} shared "{share.file.name}" with you.',
            link=f'/files?shared_file_id={share.file_id}',
        )


@extend_schema(
    tags=['Storage'],
    summary='Get storage usage for current user / company',
    responses={
        200: StorageUsageSerializer,
        401: OpenApiResponse(description='Not authenticated'),
    },
)
@api_view(['GET'])
@permission_classes([IsCompanyMember])
def storage_usage(request):
    user = request.user

    personal_qs = File.objects.filter(owner=user, company__isnull=True, is_deleted=False)
    personal_used = personal_qs.aggregate(total=Sum('file_size'))['total'] or 0
    personal_count = personal_qs.count()

    if user.company_id:
        company = user.company
        company_used = get_company_storage_used_bytes(company)
        company_limit = company.storage_limit_gb * 1024 * 1024 * 1024
        company_count = (
            File.objects.filter(is_deleted=False)
            .filter(
                Q(company=company)
                | Q(company__isnull=True, owner__company=company)
            )
            .count()
        )
        company_data = {
            'used_bytes': company_used,
            'limit_bytes': company_limit,
            'file_count': company_count,
        }
    else:
        company_data = None

    return Response(StorageUsageSerializer({
        'personal': {
            'used_bytes': personal_used,
            'file_count': personal_count,
        },
        'company': company_data,
    }).data)
