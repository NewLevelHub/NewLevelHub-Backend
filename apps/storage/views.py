from django.conf import settings
from django.db.models import F, Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers as drf_serializers
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiResponse, inline_serializer
from apps.companies.limits import (
    get_company_storage_used_bytes,
    get_guest_storage_used_bytes,
    notify_company_admins_limit_thresholds,
)
from apps.core.error_codes import STORAGE_LIMIT_EXCEEDED
from apps.core.exceptions import LocalizedError, raise_validation_error
from apps.core.i18n import translate, get_lang
from apps.core.permissions import IsCompanyMember, IsGuestOrCompanyMember
from apps.crm.models import TaskAttachment
from apps.notifications.utils import create_notification
from .models import Folder, File, FileShare
from .s3_helpers import presigned_get_url_for_fieldfile
from .serializers import FolderSerializer, FileSerializer, FileShareSerializer, StorageUsageSerializer


_FileDownloadResponseSerializer = inline_serializer(
    name='StorageFileDownloadResponse',
    fields={
        'url': drf_serializers.URLField(),
        'expires_in': drf_serializers.IntegerField(),
    },
)


def _soft_delete_folder_recursive(folder):
    now = timezone.now()
    File.all_objects.filter(folder=folder, is_deleted=False).update(
        is_deleted=True, deleted_at=now
    )
    for child in Folder.all_objects.filter(parent=folder, is_deleted=False):
        _soft_delete_folder_recursive(child)
    folder.is_deleted = True
    folder.deleted_at = now
    folder.save(update_fields=['is_deleted', 'deleted_at'])


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
    permission_classes = [IsGuestOrCompanyMember]

    def get_queryset(self):
        # CompanyIsolationMixin not used: Folder has a direct company FK but access
        # is split across two independent scopes — personal folders (owned by user,
        # company-agnostic) and company folders (scoped by company FK). A simple
        # company filter would hide all personal folders. Isolation is implemented
        # manually via the union queryset below.
        user = self.request.user
        if user.role == 'superadmin':
            queryset = Folder.objects.all()
        elif user.role == 'guest':
            # Guests only have personal storage; never expose company-scope folders.
            queryset = Folder.objects.filter(owner=user, scope='personal')
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
                    raise_validation_error('parent_id', 'storage.parent_id_invalid')
        return queryset.order_by('-created_at')

    def _resolve_scope(self):
        if self.request.user.role == 'guest':
            return 'personal'
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
            raise_validation_error('parent_id', 'storage.parent_id_not_integer')

        try:
            parent = Folder.objects.get(pk=parent_id)
        except Folder.DoesNotExist:
            raise_validation_error('parent_id', 'storage.parent_folder_not_found')

        user = self.request.user
        if scope == 'personal':
            if parent.scope != 'personal' or parent.owner_id != user.id:
                raise_validation_error('parent_id', 'storage.personal_parent_inaccessible')
        else:
            if user.role != 'superadmin' and (parent.scope != 'company' or parent.company_id != user.company_id):
                raise_validation_error('parent_id', 'storage.company_parent_inaccessible')
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

    def perform_destroy(self, instance):
        _soft_delete_folder_recursive(instance)

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
    permission_classes = [IsGuestOrCompanyMember]
    search_fields = ['name']
    ordering_fields = ['name', 'file_size', 'size', 'created_at']
    _PERMISSION_LEVELS = {'view': 1, 'download': 2, 'full': 3}

    def get_queryset(self):
        # CompanyIsolationMixin not used: File.company is nullable — personal files
        # have company=None. Access spans personal-owned, company-shared, explicitly
        # shared, and legacy (no-folder) rows. A single company filter cannot express
        # this multi-scope union; isolation is implemented manually below.
        user = self.request.user
        if user.role == 'superadmin':
            queryset = File.objects.all()
        elif user.role == 'guest':
            # Guests only have personal storage; never expose company-scope files.
            queryset = (
                File.objects.filter(owner=user, folder__scope='personal')
                | File.objects.filter(owner=user, folder__isnull=True, company__isnull=True)
            ).distinct()
        else:
            # Company scope is visible to all company members.
            company_files = File.objects.filter(company=user.company, folder__scope='company')
            company_root_files = File.objects.filter(company=user.company, folder__isnull=True)

            # Personal scope: only files owned by this user.
            personal_owned = File.objects.filter(owner=user, folder__scope='personal')
            legacy_owned = File.objects.filter(owner=user, folder__isnull=True)

            # Files explicitly shared with this user (e.g. personal files from another user).
            explicitly_shared = File.objects.filter(shares__shared_with=user)

            queryset = (
                company_files
                | company_root_files
                | personal_owned
                | legacy_owned
                | explicitly_shared
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
                    raise_validation_error('folder_id', 'storage.folder_id_invalid')

        return queryset.annotate(size=F('file_size')).order_by('-created_at')

    def _resolve_scope(self):
        if self.request.user.role == 'guest':
            return 'personal'
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
            raise_validation_error('folder_id', 'storage.folder_not_found')

        user = self.request.user
        if folder.scope == 'personal' and folder.owner_id != user.id:
            raise_validation_error('folder_id', 'storage.personal_folder_inaccessible')
        if folder.scope == 'company' and user.role != 'superadmin' and folder.company_id != user.company_id:
            raise_validation_error('folder_id', 'storage.company_folder_inaccessible')
        return folder

    def _get_share_for_user(self, file_obj, user):
        return FileShare.objects.filter(file=file_obj, shared_with=user).first()

    def _ensure_file_permission(self, file_obj, required_permission):
        user = self.request.user
        if user.role == 'superadmin' or file_obj.owner_id == user.id:
            return

        # An explicit FileShare always takes precedence over company-level defaults.
        # This lets owners grant view-only access to a file that would otherwise be
        # downloadable by any company member.
        share = self._get_share_for_user(file_obj, user)
        if share is not None:
            share_level = self._PERMISSION_LEVELS.get(share.permission, 0)
            required_level = self._PERMISSION_LEVELS.get(required_permission, 0)
            if share_level < required_level:
                lang = get_lang(self.request)
                raise PermissionDenied(translate('storage.file_action_forbidden', lang))
            return

        folder = getattr(file_obj, 'folder', None)
        is_company_file = (
            user.company_id is not None
            and file_obj.company_id == user.company_id
            and (folder is None or folder.scope == 'company')
        )
        if (
            is_company_file
            and (
                required_permission in ('view', 'download')
                or (required_permission == 'full' and user.role == 'company_admin')
            )
        ):
            return

        lang = get_lang(self.request)
        raise PermissionDenied(translate('storage.file_access_denied', lang))

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
                raise LocalizedError(
                    code=STORAGE_LIMIT_EXCEEDED,
                    i18n_key='storage.limit_exceeded',
                )
        elif request.user.role == 'guest':
            current_storage_used = get_guest_storage_used_bytes(request.user)
            guest_limit_bytes = settings.GUEST_STORAGE_LIMIT_GB * 1024 * 1024 * 1024
            if current_storage_used + uploaded_size > guest_limit_bytes:
                raise LocalizedError(
                    code=STORAGE_LIMIT_EXCEEDED,
                    i18n_key='storage.limit_exceeded',
                )
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
        summary='Get presigned download URL for file',
        responses={
            200: OpenApiResponse(response=_FileDownloadResponseSerializer, description='Presigned GET URL'),
            403: OpenApiResponse(description='Forbidden'),
            404: OpenApiResponse(description='Not found'),
        },
    )
    @action(detail=True, methods=['get'])
    def download(self, request, pk=None):
        # Load by pk outside get_queryset() so denied access returns 403, not 404.
        file_obj = get_object_or_404(File, pk=pk)
        self._ensure_file_permission(file_obj, 'download')
        if not file_obj.file:
            return Response({'detail': 'File not found.'}, status=status.HTTP_404_NOT_FOUND)
        url, expires_in = presigned_get_url_for_fieldfile(file_obj.file, filename=file_obj.name)
        if not url:
            return Response({'detail': 'File not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response({'url': url, 'expires_in': expires_in})

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
            lang = get_lang(request)
            raise PermissionDenied(translate('storage.recipients_owner_only', lang))

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
        # CompanyIsolationMixin not used: FileShare has no company FK at all.
        # Isolation is user-centric: a share record is visible to the user who
        # created it (shared_by) or the user it was shared with (shared_with).
        user = self.request.user
        shared_with_me = str(self.request.query_params.get('shared_with_me', '')).lower() in ('1', 'true', 'yes', 'on')

        if shared_with_me:
            return FileShare.objects.filter(
                shared_with=user, file__is_deleted=False,
            ).order_by('-created_at')

        if self.request.method in ('PATCH', 'PUT', 'DELETE'):
            return FileShare.objects.filter(
                shared_by=user, file__is_deleted=False,
            ).order_by('-created_at')

        return (
            FileShare.objects.filter(shared_by=user, file__is_deleted=False)
            | FileShare.objects.filter(shared_with=user, file__is_deleted=False)
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
@permission_classes([IsGuestOrCompanyMember])
def storage_usage(request):
    user = request.user

    if user.role == 'guest':
        personal_qs = File.objects.filter(owner=user, company__isnull=True, is_deleted=False)
        personal_used = personal_qs.aggregate(total=Sum('file_size'))['total'] or 0
        personal_count = personal_qs.count()
        personal_limit = int(settings.GUEST_STORAGE_LIMIT_GB * 1024 * 1024 * 1024)
        company_data = None
    elif user.company_id:
        company = user.company
        storage_limit_bytes = int(company.storage_limit_gb * 1024 * 1024 * 1024)

        # Personal files: company=NULL, owned by any employee of this company.
        personal_qs = File.objects.filter(
            company__isnull=True, owner__company=company, is_deleted=False
        )
        personal_used = personal_qs.aggregate(total=Sum('file_size'))['total'] or 0
        personal_count = personal_qs.count()

        # Company-scoped storage files only — excludes personal files so that
        # personal.used_bytes + company.used_bytes == total without double-counting.
        company_qs = File.objects.filter(company=company, is_deleted=False)
        company_files_used = company_qs.aggregate(total=Sum('file_size'))['total'] or 0
        company_count = company_qs.count()
        # Direct-upload CRM attachments (storage_file=None) are not in File but
        # do consume company storage — include them in the displayed total.
        crm_direct_bytes = (
            TaskAttachment.objects.filter(
                task__column__board__company=company,
                storage_file__isnull=True,
                file__isnull=False,
            ).aggregate(total=Sum('file_size'))['total'] or 0
        )
        company_used = company_files_used + crm_direct_bytes

        company_data = {
            'used_bytes': company_used,
            'limit_bytes': storage_limit_bytes,
            'file_count': company_count,
        }
        personal_limit = storage_limit_bytes
    else:
        personal_qs = File.objects.filter(owner=user, company__isnull=True, is_deleted=False)
        personal_used = personal_qs.aggregate(total=Sum('file_size'))['total'] or 0
        personal_count = personal_qs.count()
        personal_limit = None
        company_data = None

    return Response(StorageUsageSerializer({
        'personal': {
            'used_bytes': personal_used,
            'file_count': personal_count,
            'limit_bytes': personal_limit,
        },
        'company': company_data,
    }).data)
