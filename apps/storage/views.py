from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.db.models import Sum
from drf_spectacular.utils import extend_schema, extend_schema_view

from apps.core.permissions import IsCompanyMember
from .models import Folder, File, FileShare
from .serializers import FolderSerializer, FileSerializer, FileShareSerializer, StorageUsageSerializer


@extend_schema_view(
    list=extend_schema(tags=['Storage'], summary='List folders'),
    create=extend_schema(tags=['Storage'], summary='Create folder'),
    partial_update=extend_schema(tags=['Storage'], summary='Rename / move folder'),
    destroy=extend_schema(tags=['Storage'], summary='Delete folder'),
)
class FolderViewSet(viewsets.ModelViewSet):
    serializer_class = FolderSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.role == 'superadmin':
            return Folder.objects.all()
        return Folder.objects.filter(owner=user) | Folder.objects.filter(company=user.company, scope='company')

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user, company=self.request.user.company)


@extend_schema_view(
    list=extend_schema(tags=['Storage'], summary='List files'),
    create=extend_schema(tags=['Storage'], summary='Upload file'),
    destroy=extend_schema(tags=['Storage'], summary='Delete file (soft)'),
)
class FileViewSet(viewsets.ModelViewSet):
    serializer_class = FileSerializer
    permission_classes = [IsAuthenticated]
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
    list=extend_schema(tags=['Storage'], summary='List file shares'),
    create=extend_schema(tags=['Storage'], summary='Share file'),
    destroy=extend_schema(tags=['Storage'], summary='Revoke share'),
)
class FileShareViewSet(viewsets.ModelViewSet):
    serializer_class = FileShareSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'delete']

    def get_queryset(self):
        return FileShare.objects.filter(shared_by=self.request.user) | FileShare.objects.filter(shared_with=self.request.user)

    def perform_create(self, serializer):
        serializer.save(shared_by=self.request.user)


@extend_schema(tags=['Storage'], summary='Get storage usage for current user / company')
@api_view(['GET'])
@permission_classes([IsAuthenticated])
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
