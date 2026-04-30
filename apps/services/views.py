from django.db.models import Q
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.utils import timezone
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiResponse, inline_serializer
import rest_framework.fields as fields

from apps.core.pagination import FeedCursorPagination
from apps.core.permissions import (
    IsSuperAdmin, IsCompanyMember, IsCompanyAdminOrReadOnly, IsOwnerOrSuperAdmin,
)
from .models import Floor, MapPoint, ServiceRequest, Announcement, AnnouncementRead
from .serializers import (
    FloorSerializer, MapPointSerializer,
    ServiceRequestSerializer, ServiceRequestUpdateSerializer,
    AnnouncementSerializer,
)


# ── Карта здания ──────────────────────────────────────────────────────

@extend_schema_view(
    list=extend_schema(
        tags=['Services'],
        summary='List floors',
        responses={200: FloorSerializer(many=True)},
    ),
    retrieve=extend_schema(
        tags=['Services'],
        summary='Get floor with map points',
        responses={200: FloorSerializer, 404: OpenApiResponse(description='Not found')},
    ),
    create=extend_schema(
        tags=['Services'],
        summary='Create floor (superadmin)',
        request=FloorSerializer,
        responses={
            201: FloorSerializer,
            400: OpenApiResponse(description='Validation error'),
            403: OpenApiResponse(description='Superadmin only'),
        },
    ),
    partial_update=extend_schema(
        tags=['Services'],
        summary='Update floor',
        request=FloorSerializer,
        responses={200: FloorSerializer, 403: OpenApiResponse(description='Superadmin only')},
    ),
    destroy=extend_schema(
        tags=['Services'],
        summary='Delete floor',
        responses={204: OpenApiResponse(description='Deleted'), 403: OpenApiResponse(description='Superadmin only')},
    ),
)
class FloorViewSet(viewsets.ModelViewSet):
    queryset = Floor.objects.prefetch_related('points')
    serializer_class = FloorSerializer

    def get_permissions(self):
        if self.action in ('create', 'update', 'partial_update', 'destroy'):
            return [IsSuperAdmin()]
        return [IsAuthenticated()]


@extend_schema_view(
    list=extend_schema(
        tags=['Services'],
        summary='List map points',
        responses={200: MapPointSerializer(many=True)},
    ),
    create=extend_schema(
        tags=['Services'],
        summary='Create map point (superadmin)',
        request=MapPointSerializer,
        responses={
            201: MapPointSerializer,
            400: OpenApiResponse(description='Validation error'),
            403: OpenApiResponse(description='Superadmin only'),
        },
    ),
    partial_update=extend_schema(
        tags=['Services'],
        summary='Update map point',
        request=MapPointSerializer,
        responses={200: MapPointSerializer, 403: OpenApiResponse(description='Superadmin only')},
    ),
    destroy=extend_schema(
        tags=['Services'],
        summary='Delete map point',
        responses={204: OpenApiResponse(description='Deleted'), 403: OpenApiResponse(description='Superadmin only')},
    ),
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
    list=extend_schema(
        tags=['Services'],
        summary='List service requests',
        responses={200: ServiceRequestSerializer(many=True)},
    ),
    create=extend_schema(
        tags=['Services'],
        summary='Create service request',
        request=ServiceRequestSerializer,
        responses={
            201: ServiceRequestSerializer,
            400: OpenApiResponse(description='Validation error'),
            401: OpenApiResponse(description='Not authenticated'),
        },
    ),
    retrieve=extend_schema(
        tags=['Services'],
        summary='Get service request details',
        responses={200: ServiceRequestSerializer, 404: OpenApiResponse(description='Not found')},
    ),
)
class ServiceRequestViewSet(viewsets.ModelViewSet):
    serializer_class = ServiceRequestSerializer
    permission_classes = [IsCompanyMember]
    filterset_fields = ['request_type', 'status', 'urgency']

    def get_queryset(self):
        user = self.request.user
        if user.role == 'superadmin':
            return ServiceRequest.objects.all()
        return ServiceRequest.objects.filter(user=user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @extend_schema(
        tags=['Services'],
        summary='Quick cleaning request',
        request=inline_serializer(
            name='QuickCleaningRequest',
            fields={'floor': fields.IntegerField(required=False)},
        ),
        responses={
            201: ServiceRequestSerializer,
            401: OpenApiResponse(description='Not authenticated'),
        },
    )
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

    @extend_schema(
        tags=['Services'],
        summary='Update request status (superadmin)',
        request=ServiceRequestUpdateSerializer,
        responses={
            200: ServiceRequestSerializer,
            400: OpenApiResponse(description='Validation error'),
            403: OpenApiResponse(description='Superadmin only'),
            404: OpenApiResponse(description='Not found'),
        },
    )
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

    @extend_schema(
        tags=['Services'],
        summary='Rate completed request',
        request=inline_serializer(
            name='RateServiceRequest',
            fields={'rating': fields.IntegerField(min_value=1, max_value=5)},
        ),
        responses={
            200: OpenApiResponse(description='Rating saved'),
            400: OpenApiResponse(description='Rating must be 1-5'),
            401: OpenApiResponse(description='Not authenticated'),
            404: OpenApiResponse(description='Not found'),
        },
    )
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
    list=extend_schema(
        tags=['Services'],
        summary='List announcements',
        responses={200: AnnouncementSerializer(many=True)},
    ),
    create=extend_schema(
        tags=['Services'],
        summary='Create announcement',
        request=AnnouncementSerializer,
        responses={
            201: AnnouncementSerializer,
            400: OpenApiResponse(description='Validation error'),
            401: OpenApiResponse(description='Not authenticated'),
        },
    ),
    retrieve=extend_schema(
        tags=['Services'],
        summary='Get announcement details',
        responses={200: AnnouncementSerializer, 404: OpenApiResponse(description='Not found')},
    ),
)
class AnnouncementViewSet(viewsets.ModelViewSet):
    """
    DEV-100: building / company announcement feed.

    Visibility (queryset):
      - superadmin       → every announcement (БЦ + all companies)
      - company_admin    → БЦ (company=null) + own company
      - employee         → БЦ (company=null) + own company
      - guest            → БЦ only (company=null)

    Create:
      - superadmin sets ``company_id`` explicitly (null = БЦ); when omitted, the
        announcement is building-wide.
      - company_admin always posts under their own company (any value supplied
        in ``company_id`` is overwritten).
      - employees and guests are blocked by ``IsCompanyAdminOrReadOnly``.

    Delete (DEV-100 AC #5):
      - Only the author or a superadmin may delete.  Even another company_admin
        in the same company is rejected with 403.

    Pagination:
      - Cursor-based (``FeedCursorPagination``) for infinite scroll.
    """

    serializer_class = AnnouncementSerializer
    permission_classes = [IsCompanyAdminOrReadOnly]
    pagination_class = FeedCursorPagination
    filterset_fields = ['category', 'is_pinned']
    # Required by DRF when CursorPagination cohabits with OrderingFilter:
    # the global OrderingFilter must be able to derive a non-None default
    # ordering, otherwise pagination raises an AssertionError.
    ordering = ('-is_pinned', '-created_at', '-id')

    def get_queryset(self):
        user = self.request.user
        qs = Announcement.objects.all().select_related('author', 'company')
        if not user.is_authenticated:
            return qs.none()
        if user.role == 'superadmin':
            return qs
        if user.role == 'guest':
            return qs.filter(company__isnull=True)
        if user.company_id:
            return qs.filter(Q(company__isnull=True) | Q(company_id=user.company_id))
        return qs.filter(company__isnull=True)

    def get_permissions(self):
        if self.action == 'destroy':
            # AC: only the author or a superadmin may delete.
            perm = IsOwnerOrSuperAdmin()
            perm.owner_field = 'author'
            return [perm]
        return super().get_permissions()

    def perform_create(self, serializer):
        user = self.request.user
        if user.role == 'superadmin':
            # Honour ``company_id`` from validated data — null means БЦ.
            company = serializer.validated_data.get('company', None)
        else:
            # company_admin (and any future write-allowed role) is forced
            # onto their own company; never let them post under another tenant.
            company = user.company
        serializer.save(author=user, company=company)
        # TODO: если notify_email=True — Celery task рассылки

    @extend_schema(
        tags=['Services'],
        summary='Mark announcement as read',
        request=None,
        responses={
            200: OpenApiResponse(description='Marked as read'),
            401: OpenApiResponse(description='Not authenticated'),
            404: OpenApiResponse(description='Not found'),
        },
    )
    @action(detail=True, methods=['post'], url_path='read')
    def mark_read(self, request, pk=None):
        announcement = self.get_object()
        AnnouncementRead.objects.get_or_create(announcement=announcement, user=request.user)
        return Response({'detail': 'Marked as read'})
