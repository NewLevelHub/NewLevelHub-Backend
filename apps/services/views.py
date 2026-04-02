from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.utils import timezone
from drf_spectacular.utils import extend_schema, extend_schema_view

from apps.core.permissions import IsSuperAdmin
from .models import Floor, MapPoint, ServiceRequest, Announcement, AnnouncementRead
from .serializers import (
    FloorSerializer, MapPointSerializer,
    ServiceRequestSerializer, ServiceRequestUpdateSerializer,
    AnnouncementSerializer,
)


# ── Карта здания ──────────────────────────────────────────────────────

@extend_schema_view(
    list=extend_schema(tags=['Services'], summary='List floors'),
    retrieve=extend_schema(tags=['Services'], summary='Get floor with map points'),
    create=extend_schema(tags=['Services'], summary='Create floor (superadmin)'),
    partial_update=extend_schema(tags=['Services'], summary='Update floor'),
    destroy=extend_schema(tags=['Services'], summary='Delete floor'),
)
class FloorViewSet(viewsets.ModelViewSet):
    queryset = Floor.objects.prefetch_related('points')
    serializer_class = FloorSerializer

    def get_permissions(self):
        if self.action in ('create', 'update', 'partial_update', 'destroy'):
            return [IsSuperAdmin()]
        return [IsAuthenticated()]


@extend_schema_view(
    list=extend_schema(tags=['Services'], summary='List map points'),
    create=extend_schema(tags=['Services'], summary='Create map point (superadmin)'),
    partial_update=extend_schema(tags=['Services'], summary='Update map point'),
    destroy=extend_schema(tags=['Services'], summary='Delete map point'),
)
class MapPointViewSet(viewsets.ModelViewSet):
    queryset = MapPoint.objects.all()
    serializer_class = MapPointSerializer

    def get_permissions(self):
        if self.action in ('create', 'update', 'partial_update', 'destroy'):
            return [IsSuperAdmin()]
        return [IsAuthenticated()]


# ── Сервисные заявки ──────────────────────────────────────────────────

@extend_schema_view(
    list=extend_schema(tags=['Services'], summary='List service requests'),
    create=extend_schema(tags=['Services'], summary='Create service request'),
    retrieve=extend_schema(tags=['Services'], summary='Get service request details'),
)
class ServiceRequestViewSet(viewsets.ModelViewSet):
    serializer_class = ServiceRequestSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['request_type', 'status', 'urgency']

    def get_queryset(self):
        user = self.request.user
        if user.role == 'superadmin':
            return ServiceRequest.objects.all()
        return ServiceRequest.objects.filter(user=user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @extend_schema(tags=['Services'], summary='Quick cleaning request')
    @action(detail=False, methods=['post'], url_path='cleaning')
    def quick_cleaning(self, request):
        sr = ServiceRequest.objects.create(
            user=request.user,
            request_type='cleaning',
            urgency='normal',
            floor=request.data.get('floor'),
            description='Quick cleaning request',
        )
        return Response(ServiceRequestSerializer(sr).data, status=status.HTTP_201_CREATED)

    @extend_schema(tags=['Services'], summary='Update request status (superadmin)')
    @action(detail=True, methods=['patch'], url_path='update-status', permission_classes=[IsSuperAdmin])
    def update_status(self, request, pk=None):
        sr = self.get_object()
        serializer = ServiceRequestUpdateSerializer(sr, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        if sr.status == 'completed':
            sr.completed_at = timezone.now()
            sr.save(update_fields=['completed_at'])
        # TODO: уведомить пользователя о смене статуса
        return Response(ServiceRequestSerializer(sr).data)

    @extend_schema(tags=['Services'], summary='Rate completed request')
    @action(detail=True, methods=['post'], url_path='rate')
    def rate(self, request, pk=None):
        sr = self.get_object()
        rating = request.data.get('rating')
        if not rating or int(rating) not in range(1, 6):
            return Response({'detail': 'Rating must be 1-5'}, status=status.HTTP_400_BAD_REQUEST)
        sr.rating = int(rating)
        sr.save(update_fields=['rating'])
        return Response({'detail': 'Rated'})


# ── Объявления ────────────────────────────────────────────────────────

@extend_schema_view(
    list=extend_schema(tags=['Services'], summary='List announcements'),
    create=extend_schema(tags=['Services'], summary='Create announcement'),
    retrieve=extend_schema(tags=['Services'], summary='Get announcement details'),
)
class AnnouncementViewSet(viewsets.ModelViewSet):
    serializer_class = AnnouncementSerializer
    filterset_fields = ['scope', 'category', 'is_pinned']

    def get_permissions(self):
        if self.action in ('create', 'update', 'partial_update', 'destroy'):
            return [IsAuthenticated()]  # TODO: IsSuperAdmin для building, IsCompanyAdmin для company
        return [IsAuthenticated()]

    def get_queryset(self):
        user = self.request.user
        building_qs = Announcement.objects.filter(scope='building')
        if user.company_id:
            company_qs = Announcement.objects.filter(scope='company', company=user.company)
            return (building_qs | company_qs).distinct()
        return building_qs

    def perform_create(self, serializer):
        serializer.save(author=self.request.user)
        # TODO: если notify_email=True — Celery task рассылки

    @extend_schema(tags=['Services'], summary='Mark announcement as read')
    @action(detail=True, methods=['post'], url_path='read')
    def mark_read(self, request, pk=None):
        announcement = self.get_object()
        AnnouncementRead.objects.get_or_create(announcement=announcement, user=request.user)
        return Response({'detail': 'Marked as read'})
