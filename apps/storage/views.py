from django.conf import settings
from django.db.models import Case, CharField, Count, Exists, F, OuterRef, Q, Subquery, Sum, Value, When
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers as drf_serializers
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiResponse, inline_serializer
from apps.companies.limits import (
    get_company_storage_used_bytes,
    get_guest_storage_used_bytes,
    notify_company_admins_limit_thresholds,
)
from apps.core.error_codes import STORAGE_LIMIT_EXCEEDED
from apps.core.exceptions import LocalizedError, raise_validation_error
from apps.core.i18n import translate, get_lang
from apps.core.permissions import IsCompanyAdmin, IsCompanyMember, IsGuestOrCompanyMember
from apps.crm.models import TaskAttachment
from apps.notifications.utils import create_notification
from .constants import ARCHIVE_MIME_TYPES
from .models import Folder, File, FileShare, FolderPermission
from .s3_helpers import presigned_get_url_for_fieldfile, _delete_fieldfile_with_retry
from .serializers import (
    FolderSerializer, FileSerializer, FileShareSerializer, FolderPermissionSerializer,
    StorageUsageSerializer, TrashItemSerializer,
)


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


def _collect_all_folder_files(folder):
    """Yield all File records in folder's entire subtree (uses all_objects — includes soft-deleted)."""
    for f in File.all_objects.filter(folder=folder):
        yield f
    for child in Folder.all_objects.filter(parent=folder):
        yield from _collect_all_folder_files(child)


def _check_folder_permission(request, folder):
    """Raise PermissionDenied if the request user cannot modify this folder."""
    user = request.user
    if user.role == 'superadmin':
        return
    if folder.owner_id == user.id:
        return
    if (
        folder.scope == 'company'
        and user.company_id
        and folder.company_id == user.company_id
        and user.role == 'company_admin'
    ):
        return
    raise PermissionDenied(translate('storage.folder_action_forbidden', get_lang(request)))


_FOLDER_PERM_LEVELS = {'view': 1, 'upload': 2, 'full': 3}


def _folder_access_level(user, folder):
    """Return effective permission level ('view'/'upload'/'full') or None if no access.

    Walks the ancestor chain: a folder with no explicit permissions inherits
    the first restriction found in its parent hierarchy.
    """
    if user.role == 'superadmin':
        return 'full'
    if folder.scope == 'personal':
        return 'full' if folder.owner_id == user.id else None
    # company folder
    if user.company_id is None or folder.company_id != user.company_id:
        return None
    if user.role == 'company_admin':
        return 'full'

    current = folder
    while current is not None:
        perms = list(FolderPermission.objects.filter(folder=current))
        if perms:
            user_perm = next((p for p in perms if p.user_id == user.id), None)
            if user_perm:
                return user_perm.permission
            role_perm = next((p for p in perms if p.role == user.role), None)
            if role_perm:
                return role_perm.permission
            return None  # folder is restricted but user has no matching entry
        # No permissions on this folder — check parent
        current = Folder.objects.filter(pk=current.parent_id).first() if current.parent_id else None

    return 'upload'  # reached root with no restrictions — open folder default


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
            personal_qs = Folder.objects.filter(owner=user, scope='personal')

            if user.role == 'company_admin':
                company_qs = Folder.objects.filter(company=user.company, scope='company')
            else:
                restricted_ids = FolderPermission.objects.filter(
                    folder__company=user.company, folder__scope='company',
                ).values('folder_id')

                permitted_ids = FolderPermission.objects.filter(
                    folder__company=user.company, folder__scope='company',
                ).filter(Q(user=user) | Q(role=user.role)).values('folder_id')

                company_qs = Folder.objects.filter(
                    company=user.company, scope='company',
                ).filter(
                    Q(id__in=permitted_ids) | ~Q(id__in=restricted_ids)
                )

            queryset = (personal_qs | company_qs).distinct()

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

        perm_exists_sq = FolderPermission.objects.filter(folder=OuterRef('pk'))
        queryset = queryset.annotate(perm_exists=Exists(perm_exists_sq))

        # For employees: annotate per-folder permission level to avoid N+1 in serializer.
        if user.role not in ('superadmin', 'company_admin', 'guest'):
            user_perm_sq = FolderPermission.objects.filter(
                folder=OuterRef('pk'), user=user
            ).values('permission')[:1]
            role_perm_sq = FolderPermission.objects.filter(
                folder=OuterRef('pk'), role=user.role
            ).values('permission')[:1]
            queryset = queryset.annotate(
                user_perm_ann=Subquery(user_perm_sq, output_field=CharField()),
                role_perm_ann=Subquery(role_perm_sq, output_field=CharField()),
            )

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
            if user.role not in ('superadmin', 'company_admin'):
                level = _folder_access_level(user, parent)
                if level is None or _FOLDER_PERM_LEVELS.get(level, 0) < _FOLDER_PERM_LEVELS['upload']:
                    raise_validation_error('parent_id', 'storage.folder_upload_forbidden')
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

    @extend_schema(
        tags=['Storage'],
        summary='Restore folder from trash',
        responses={
            204: OpenApiResponse(description='Restored'),
            403: OpenApiResponse(description='Forbidden'),
            404: OpenApiResponse(description='Not found'),
        },
    )
    @action(detail=True, methods=['post'], url_path='restore')
    def restore(self, request, pk=None):
        try:
            folder = Folder.all_objects.get(pk=pk, is_deleted=True)
        except Folder.DoesNotExist:
            raise NotFound()
        _check_folder_permission(request, folder)
        folder.restore()
        File.all_objects.filter(folder=folder, is_deleted=True).update(is_deleted=False, deleted_at=None)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        tags=['Storage'],
        summary='Permanently delete folder from trash',
        responses={
            204: OpenApiResponse(description='Deleted permanently'),
            403: OpenApiResponse(description='Forbidden'),
            404: OpenApiResponse(description='Not found'),
        },
    )
    @action(detail=True, methods=['delete'], url_path='permanent')
    def permanent_delete(self, request, pk=None):
        try:
            folder = Folder.all_objects.get(pk=pk, is_deleted=True)
        except Folder.DoesNotExist:
            raise NotFound()
        _check_folder_permission(request, folder)
        for f in _collect_all_folder_files(folder):
            if f.file:
                _delete_fieldfile_with_retry(f.file)
        folder.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        methods=['get'],
        tags=['Storage'],
        summary='List folder permissions',
        responses={200: FolderPermissionSerializer(many=True), 403: OpenApiResponse(description='Forbidden')},
    )
    @extend_schema(
        methods=['post'],
        tags=['Storage'],
        summary='Add folder permission',
        request=FolderPermissionSerializer,
        responses={
            201: FolderPermissionSerializer,
            400: OpenApiResponse(description='Validation error'),
            403: OpenApiResponse(description='Forbidden'),
        },
    )
    @action(detail=True, methods=['get', 'post'], url_path='permissions')
    def folder_permissions(self, request, pk=None):
        folder = self.get_object()
        if request.user.role not in ('superadmin', 'company_admin'):
            raise PermissionDenied(translate('storage.folder_action_forbidden', get_lang(request)))
        if request.method == 'GET':
            qs = (
                FolderPermission.objects.filter(folder=folder)
                .select_related('user', 'granted_by')
                .order_by('created_at')
            )
            return Response(FolderPermissionSerializer(qs, many=True, context={'request': request}).data)
        serializer = FolderPermissionSerializer(
            data=request.data, context={'request': request, 'folder': folder}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save(folder=folder, granted_by=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

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
            elif user.role == 'company_admin':
                child_folders = Folder.objects.filter(parent=folder, scope='company', company=user.company)
                files = File.objects.filter(folder=folder, company=user.company)
            else:
                restr_child_ids = FolderPermission.objects.filter(
                    folder__parent=folder, folder__scope='company',
                ).values('folder_id')

                permitted_child_ids = FolderPermission.objects.filter(
                    folder__parent=folder, folder__scope='company',
                ).filter(Q(user=user) | Q(role=user.role)).values('folder_id')

                child_folders = Folder.objects.filter(
                    parent=folder, scope='company', company=user.company,
                ).filter(
                    Q(id__in=permitted_child_ids) | ~Q(id__in=restr_child_ids)
                ).distinct()
                files = File.objects.filter(folder=folder, company=user.company)

        perm_exists_sq = FolderPermission.objects.filter(folder=OuterRef('pk'))
        child_folders = child_folders.annotate(perm_exists=Exists(perm_exists_sq))

        if user.role not in ('superadmin', 'company_admin', 'guest'):
            user_perm_sq = FolderPermission.objects.filter(
                folder=OuterRef('pk'), user=user
            ).values('permission')[:1]
            role_perm_sq = FolderPermission.objects.filter(
                folder=OuterRef('pk'), role=user.role
            ).values('permission')[:1]
            child_folders = child_folders.annotate(
                user_perm_ann=Subquery(user_perm_sq, output_field=CharField()),
                role_perm_ann=Subquery(role_perm_sq, output_field=CharField()),
            )

        data['folders'] = FolderSerializer(child_folders, many=True, context={'request': request}).data
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
    ordering_fields = ['name', 'file_size', 'size', 'created_at', 'file_category']
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
        elif user.role == 'company_admin':
            company_files = File.objects.filter(company=user.company, folder__scope='company')
            company_root_files = File.objects.filter(company=user.company, folder__isnull=True)
            personal_owned = File.objects.filter(owner=user, folder__scope='personal')
            legacy_owned = File.objects.filter(owner=user, folder__isnull=True)
            explicitly_shared = File.objects.filter(shares__shared_with=user)
            queryset = (
                company_files
                | company_root_files
                | personal_owned
                | legacy_owned
                | explicitly_shared
            ).distinct()
        else:
            # Exclude files from restricted folders the employee has no access to.
            restricted_folder_ids = FolderPermission.objects.filter(
                folder__company=user.company, folder__scope='company',
            ).values('folder_id')

            permitted_folder_ids = FolderPermission.objects.filter(
                folder__company=user.company, folder__scope='company',
            ).filter(Q(user=user) | Q(role=user.role)).values('folder_id')

            inaccessible_folder_ids = Folder.objects.filter(
                id__in=restricted_folder_ids, company=user.company, scope='company',
            ).exclude(id__in=permitted_folder_ids).values('id')

            company_files = File.objects.filter(
                company=user.company, folder__scope='company',
            ).exclude(folder_id__in=inaccessible_folder_ids)
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

        annotated = queryset.annotate(
            size=F('file_size'),
            file_category=Case(
                When(content_type__startswith='image/', then=Value('image')),
                When(content_type__startswith='video/', then=Value('media')),
                When(content_type__startswith='audio/', then=Value('media')),
                When(content_type__in=ARCHIVE_MIME_TYPES, then=Value('archive')),
                When(content_type__startswith='application/', then=Value('document')),
                When(content_type__startswith='text/', then=Value('document')),
                default=Value('other'),
                output_field=CharField(),
            ),
        )
        file_category = self.request.query_params.get('file_category')
        if file_category in {'image', 'media', 'archive', 'document', 'other'}:
            annotated = annotated.filter(file_category=file_category)
        return annotated.select_related('folder', 'owner').order_by('-created_at')

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
        if folder.scope == 'company' and user.role not in ('superadmin', 'company_admin'):
            level = _folder_access_level(user, folder)
            if level is None or _FOLDER_PERM_LEVELS.get(level, 0) < _FOLDER_PERM_LEVELS['upload']:
                raise_validation_error('folder_id', 'storage.folder_upload_forbidden')
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
        if is_company_file:
            if folder is not None:
                level = _folder_access_level(user, folder)
                if level is None:
                    raise PermissionDenied(translate('storage.file_access_denied', get_lang(self.request)))
                if required_permission in ('view', 'download'):
                    if _FOLDER_PERM_LEVELS.get(level, 0) >= _FOLDER_PERM_LEVELS['view']:
                        return
                elif required_permission == 'full':
                    if _FOLDER_PERM_LEVELS.get(level, 0) >= _FOLDER_PERM_LEVELS['full']:
                        return
                raise PermissionDenied(translate('storage.file_action_forbidden', get_lang(self.request)))
            else:
                # root-level company file (folder=None): legacy behaviour
                if required_permission in ('view', 'download'):
                    return
                if required_permission == 'full' and user.role == 'company_admin':
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
        summary='Bulk delete files',
        request=inline_serializer(
            name='BulkDeleteRequest',
            fields={'ids': drf_serializers.ListField(child=drf_serializers.IntegerField())},
        ),
        responses={
            204: OpenApiResponse(description='Deleted'),
            400: OpenApiResponse(description='Validation error'),
        },
    )
    @action(detail=False, methods=['post'], url_path='bulk_delete')
    def bulk_delete(self, request):
        ids = request.data.get('ids')
        if not isinstance(ids, list) or not ids:
            return Response({'detail': '"ids" must be a non-empty list.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            ids = [int(i) for i in ids]
        except (TypeError, ValueError):
            return Response({'detail': '"ids" must contain integers.'}, status=status.HTTP_400_BAD_REQUEST)

        queryset = self.get_queryset().filter(pk__in=ids)
        user = request.user
        for file_obj in queryset:
            folder = getattr(file_obj, 'folder', None)
            has_folder_full = (
                folder is not None
                and _FOLDER_PERM_LEVELS.get(_folder_access_level(user, folder) or '', 0)
                >= _FOLDER_PERM_LEVELS['full']
            )
            if not (user.role == 'superadmin' or file_obj.owner_id == user.id
                    or (user.company_id and file_obj.company_id == user.company_id and user.role == 'company_admin')
                    or has_folder_full):
                return Response(
                    {'detail': f'No permission to delete file {file_obj.id}.'},
                    status=status.HTTP_403_FORBIDDEN,
                )
        now = timezone.now()
        queryset.update(is_deleted=True, deleted_at=now)
        return Response(status=status.HTTP_204_NO_CONTENT)

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

    @extend_schema(
        tags=['Storage'],
        summary='Restore file from trash',
        responses={
            204: OpenApiResponse(description='Restored'),
            403: OpenApiResponse(description='Forbidden'),
            404: OpenApiResponse(description='Not found'),
        },
    )
    @action(detail=True, methods=['post'], url_path='restore')
    def restore(self, request, pk=None):
        try:
            file_obj = File.all_objects.get(pk=pk, is_deleted=True)
        except File.DoesNotExist:
            raise NotFound()
        self._ensure_file_permission(file_obj, 'full')
        file_obj.restore()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        tags=['Storage'],
        summary='Permanently delete file from trash',
        responses={
            204: OpenApiResponse(description='Deleted permanently'),
            403: OpenApiResponse(description='Forbidden'),
            404: OpenApiResponse(description='Not found'),
        },
    )
    @action(detail=True, methods=['delete'], url_path='permanent')
    def permanent_delete(self, request, pk=None):
        try:
            file_obj = File.all_objects.get(pk=pk, is_deleted=True)
        except File.DoesNotExist:
            raise NotFound()
        self._ensure_file_permission(file_obj, 'full')
        if file_obj.file:
            _delete_fieldfile_with_retry(file_obj.file)
        file_obj.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema_view(
    partial_update=extend_schema(
        tags=['Storage'],
        summary='Update folder permission',
        request=FolderPermissionSerializer,
        responses={200: FolderPermissionSerializer, 403: OpenApiResponse(description='Forbidden')},
    ),
    destroy=extend_schema(
        tags=['Storage'],
        summary='Delete folder permission',
        responses={204: OpenApiResponse(description='Deleted'), 403: OpenApiResponse(description='Forbidden')},
    ),
)
class FolderPermissionViewSet(viewsets.ModelViewSet):
    serializer_class = FolderPermissionSerializer
    permission_classes = [IsCompanyAdmin]
    http_method_names = ['patch', 'delete']

    def get_queryset(self):
        user = self.request.user
        if user.role == 'superadmin':
            return FolderPermission.objects.all().select_related('user', 'folder', 'granted_by')
        return FolderPermission.objects.filter(
            folder__company=user.company,
        ).select_related('user', 'folder', 'granted_by')


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
            ).select_related('shared_with', 'shared_by').order_by('-created_at')

        if self.request.method in ('PATCH', 'PUT', 'DELETE'):
            return FileShare.objects.filter(
                shared_by=user, file__is_deleted=False,
            ).select_related('shared_with', 'shared_by').order_by('-created_at')

        return (
            FileShare.objects.filter(shared_by=user, file__is_deleted=False)
            | FileShare.objects.filter(shared_with=user, file__is_deleted=False)
        ).select_related('shared_with', 'shared_by').order_by('-created_at')

    def perform_create(self, serializer):
        share = serializer.save(shared_by=self.request.user)
        create_notification(
            user=share.shared_with,
            notification_type='announcement_company',
            title='Вам открыли доступ к файлу',
            message=f'{share.shared_by.full_name} поделился(-ась) файлом "{share.file.name}" с вами.',
            link=f'/files?shared_file_id={share.file_id}',
        )


class TrashListView(APIView):
    permission_classes = [IsGuestOrCompanyMember]

    def _build_trash_querysets(self, request):
        """Return (files_qs, folders_qs) scoped to the requesting user."""
        user = request.user
        scope = request.query_params.get('scope', 'personal')

        if user.role == 'superadmin':
            files_qs = File.all_objects.filter(is_deleted=True)
            folders_qs = Folder.all_objects.filter(is_deleted=True)
        elif user.role == 'guest':
            files_qs = File.all_objects.filter(is_deleted=True, owner=user, company__isnull=True)
            folders_qs = Folder.all_objects.filter(is_deleted=True, owner=user, scope='personal')
        elif scope == 'company' and user.company_id:
            files_qs = File.all_objects.filter(is_deleted=True, company=user.company)
            folders_qs = Folder.all_objects.filter(is_deleted=True, scope='company', company=user.company)
        else:
            files_qs = File.all_objects.filter(is_deleted=True, owner=user, company__isnull=True)
            folders_qs = Folder.all_objects.filter(is_deleted=True, owner=user, scope='personal')

        return files_qs, folders_qs

    @extend_schema(
        tags=['Storage'],
        summary='List trash (soft-deleted files and folders)',
        responses={
            200: TrashItemSerializer(many=True),
            401: OpenApiResponse(description='Not authenticated'),
        },
    )
    def get(self, request):
        files_qs, folders_qs = self._build_trash_querysets(request)

        # Count all files (including soft-deleted) per folder in a single query.
        folders_qs = folders_qs.annotate(cached_files_count=Count('files'))

        items = []
        for f in files_qs.order_by('-deleted_at'):
            items.append({
                'id': f.id,
                'name': f.name,
                'item_type': 'file',
                'deleted_at': f.deleted_at,
                'scope': 'personal' if f.company_id is None else 'company',
                'file_size': f.file_size,
                'content_type': f.content_type,
                'files_count': None,
            })
        for folder in folders_qs.order_by('-deleted_at'):
            items.append({
                'id': folder.id,
                'name': folder.name,
                'item_type': 'folder',
                'deleted_at': folder.deleted_at,
                'scope': folder.scope,
                'file_size': None,
                'content_type': None,
                'files_count': folder.cached_files_count,
            })

        items.sort(key=lambda x: x['deleted_at'], reverse=True)
        serializer = TrashItemSerializer(items, many=True)
        return Response({'count': len(items), 'next': None, 'previous': None, 'results': serializer.data})

    @extend_schema(
        tags=['Storage'],
        summary='Empty trash (permanently delete all items in trash)',
        responses={
            204: OpenApiResponse(description='All trash items permanently deleted'),
            401: OpenApiResponse(description='Not authenticated'),
        },
    )
    def delete(self, request):
        files_qs, folders_qs = self._build_trash_querysets(request)

        for f in files_qs:
            if f.file:
                _delete_fieldfile_with_retry(f.file)
            f.delete()

        for folder in folders_qs:
            for f in _collect_all_folder_files(folder):
                if f.file:
                    _delete_fieldfile_with_retry(f.file)
            folder.delete()

        return Response(status=status.HTTP_204_NO_CONTENT)


_CATEGORY_ANNOTATION = Case(
    When(content_type__startswith='image/', then=Value('image')),
    When(content_type__startswith='video/', then=Value('media')),
    When(content_type__startswith='audio/', then=Value('media')),
    When(content_type__in=ARCHIVE_MIME_TYPES, then=Value('archive')),
    When(content_type__startswith='application/', then=Value('document')),
    When(content_type__startswith='text/', then=Value('document')),
    default=Value('other'),
    output_field=CharField(),
)

_BREAKDOWN_ZERO = {'document': 0, 'image': 0, 'archive': 0, 'media': 0, 'other': 0}


def _compute_breakdown(active_qs):
    """Return breakdown dict (bytes per category) for the given active-file queryset."""
    rows = (
        active_qs
        .annotate(file_category=_CATEGORY_ANNOTATION)
        .values('file_category')
        .annotate(total=Sum('file_size'))
    )
    result = dict(_BREAKDOWN_ZERO)
    for row in rows:
        cat = row['file_category']
        if cat in result:
            result[cat] = row['total'] or 0
    return result


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
        # used_bytes must include trashed files — they still occupy disk space.
        personal_qs = File.all_objects.filter(owner=user, company__isnull=True)
        personal_used = personal_qs.aggregate(total=Sum('file_size'))['total'] or 0
        personal_active_qs = personal_qs.filter(is_deleted=False)
        personal_count = personal_active_qs.count()
        personal_trash = (
            personal_qs.filter(is_deleted=True).aggregate(total=Sum('file_size'))['total'] or 0
        )
        personal_breakdown = _compute_breakdown(personal_active_qs)
        personal_limit = int(settings.GUEST_STORAGE_LIMIT_GB * 1024 * 1024 * 1024)
        company_data = None
    elif user.company_id:
        company = user.company
        storage_limit_bytes = int(company.storage_limit_gb * 1024 * 1024 * 1024)

        # Personal files: company=NULL, owned by any employee of this company.
        # Include trashed files in used_bytes — they still occupy disk space.
        personal_qs = File.all_objects.filter(
            company__isnull=True, owner__company=company
        )
        personal_used = personal_qs.aggregate(total=Sum('file_size'))['total'] or 0
        personal_active_qs = personal_qs.filter(is_deleted=False)
        personal_count = personal_active_qs.count()
        personal_trash = (
            personal_qs.filter(is_deleted=True).aggregate(total=Sum('file_size'))['total'] or 0
        )
        personal_breakdown = _compute_breakdown(personal_active_qs)

        # Company-scoped storage files only — excludes personal files so that
        # personal.used_bytes + company.used_bytes == total without double-counting.
        # Include trashed files in used_bytes — they still occupy disk space.
        company_qs = File.all_objects.filter(company=company)
        company_files_used = company_qs.aggregate(total=Sum('file_size'))['total'] or 0
        company_active_qs = company_qs.filter(is_deleted=False)
        company_count = company_active_qs.count()
        company_trash = (
            company_qs.filter(is_deleted=True).aggregate(total=Sum('file_size'))['total'] or 0
        )
        company_breakdown = _compute_breakdown(company_active_qs)
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
            'trash_bytes': company_trash,
            'breakdown': company_breakdown,
        }
        personal_limit = storage_limit_bytes
    else:
        personal_qs = File.all_objects.filter(owner=user, company__isnull=True)
        personal_used = personal_qs.aggregate(total=Sum('file_size'))['total'] or 0
        personal_active_qs = personal_qs.filter(is_deleted=False)
        personal_count = personal_active_qs.count()
        personal_trash = (
            personal_qs.filter(is_deleted=True).aggregate(total=Sum('file_size'))['total'] or 0
        )
        personal_breakdown = _compute_breakdown(personal_active_qs)
        personal_limit = None
        company_data = None

    return Response(StorageUsageSerializer({
        'personal': {
            'used_bytes': personal_used,
            'file_count': personal_count,
            'limit_bytes': personal_limit,
            'trash_bytes': personal_trash,
            'breakdown': personal_breakdown,
        },
        'company': company_data,
    }).data)
