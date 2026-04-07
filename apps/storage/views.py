from rest_framework import viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from django.db.models import Sum
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiResponse
from apps.core.permissions import IsCompanyMember
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
            return Folder.objects.all()
        return Folder.objects.filter(owner=user) | Folder.objects.filter(company=user.company, scope='company')

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user, company=self.request.user.company)


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
    ordering_fields = ['name', 'file_size', 'created_at']

    def get_queryset(self):
        user = self.request.user
        if user.role == 'superadmin':
            return File.objects.all()
        own = File.objects.filter(owner=user)
        shared = File.objects.filter(company=user.company)
        return (own | shared).distinct()

    def perform_create(self, serializer):
        f = self.request.FILES.get('file')
        serializer.save(
            owner=self.request.user,
            company=self.request.user.company,
            file_size=f.size if f else 0,
            content_type=f.content_type if f else '',
        )
        # TODO: проверить квоту хранилища компании


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
    http_method_names = ['get', 'post', 'delete']

    def get_queryset(self):
        return (
            FileShare.objects.filter(shared_by=self.request.user)
            | FileShare.objects.filter(shared_with=self.request.user)
        )

    def perform_create(self, serializer):
        serializer.save(shared_by=self.request.user)


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
    if user.company_id:
        used = File.objects.filter(company=user.company).aggregate(total=Sum('file_size'))['total'] or 0
        limit = user.company.storage_limit_gb * 1024 * 1024 * 1024
    else:
        used = File.objects.filter(owner=user).aggregate(total=Sum('file_size'))['total'] or 0
        limit = 1 * 1024 * 1024 * 1024  # 1 GB default for guests
    return Response(StorageUsageSerializer({
        'used_bytes': used,
        'limit_bytes': limit,
        'used_percent': round(used / limit * 100, 2) if limit else 0,
    }).data)
