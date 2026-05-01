from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.utils import timezone
from drf_spectacular.utils import (
    extend_schema, extend_schema_view, OpenApiResponse, OpenApiParameter, inline_serializer,
)
import rest_framework.fields as drf_fields

from apps.core.permissions import IsSuperAdmin, IsCompanyMember, IsCompanyAdmin, IsCompanyAdminOrReadOnly
from apps.core.mixins import CompanyIsolationMixin, SetCompanyOnCreateMixin
from apps.core.pagination import StandardPagination
from apps.notifications.utils import create_notification
from .models import Floor, MapPoint, ServiceRequest, Announcement, AnnouncementRead
from .serializers import (
    FloorSerializer, MapPointSerializer,
    ServiceRequestSerializer, ServiceRequestStatusSerializer,
    ServiceRequestRateSerializer,
    AnnouncementSerializer,
)
from .filters import ServiceRequestFilter


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

    def perform_destroy(self, instance):
        # Delete plan image file before removing the DB row
        if instance.plan_image:
            instance.plan_image.delete(save=False)
        instance.delete()

    def perform_update(self, serializer):
        old_image = serializer.instance.plan_image
        instance = serializer.save()
        new_image = instance.plan_image
        # If image was replaced, delete the old file
        if old_image and old_image != new_image:
            old_image.delete(save=False)


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
        parameters=[
            OpenApiParameter(name='request_type', description='Filter by type', required=False, type=str,
                             enum=['cleaning', 'repair', 'supplies', 'general']),
            OpenApiParameter(
                name='type',
                description='Backward-compatible alias for request_type',
                required=False,
                type=str,
                enum=['cleaning', 'repair', 'supplies', 'general'],
            ),
            OpenApiParameter(name='status', description='Filter by status', required=False, type=str,
                             enum=['new', 'accepted', 'in_progress', 'completed']),
            OpenApiParameter(name='urgency', description='Filter by urgency', required=False, type=str,
                             enum=['low', 'medium', 'high', 'normal', 'urgent']),
            OpenApiParameter(name='floor', description='Filter by floor ID', required=False, type=int),
        ],
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
            403: OpenApiResponse(description='Forbidden'),
        },
    ),
    retrieve=extend_schema(
        tags=['Services'],
        summary='Get service request details',
        responses={
            200: ServiceRequestSerializer,
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Forbidden'),
            404: OpenApiResponse(description='Not found'),
        },
    ),
)
class ServiceRequestViewSet(CompanyIsolationMixin, viewsets.ModelViewSet):
    serializer_class = ServiceRequestSerializer
    permission_classes = [IsCompanyMember]
    pagination_class = StandardPagination
    filterset_class = ServiceRequestFilter
    ordering = ['-created_at']
    # CompanyIsolationMixin uses company_field='company' — matches our FK name
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    def get_queryset(self):
        user = self.request.user
        base_qs = ServiceRequest.objects.select_related(
            'created_by', 'assigned_to', 'floor', 'company',
        ).order_by('-created_at')
        if user.role == 'superadmin':
            return base_qs
        if user.role == 'company_admin' and user.company_id:
            # Company admins see all requests within their company
            return base_qs.filter(company=user.company_id)
        # Regular employees see only their own requests
        return base_qs.filter(created_by=user)

    def perform_create(self, serializer):
        serializer.save(
            created_by=self.request.user,
            company=self.request.user.company,
        )

    @extend_schema(
        tags=['Services'],
        summary='Quick cleaning request',
        request=inline_serializer(
            name='QuickCleaningRequest',
            fields={'floor': drf_fields.IntegerField(required=False, allow_null=True)},
        ),
        responses={
            201: ServiceRequestSerializer,
            400: OpenApiResponse(description='No floor could be determined'),
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Forbidden'),
        },
    )
    @action(detail=False, methods=['post'], url_path='quick-cleaning')
    def quick_cleaning(self, request):
        return self._handle_quick_cleaning(request)

    @extend_schema(
        tags=['Services'],
        summary='[Deprecated] Quick cleaning request (legacy alias)',
        description='Backward-compatible alias for POST /api/v1/services/requests/quick-cleaning/. '
                    'Use /quick-cleaning/ as canonical endpoint.',
        request=inline_serializer(
            name='QuickCleaningRequestLegacy',
            fields={'floor': drf_fields.IntegerField(required=False, allow_null=True)},
        ),
        responses={
            201: ServiceRequestSerializer,
            400: OpenApiResponse(description='No floor could be determined'),
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Forbidden'),
        },
        deprecated=True,
    )
    @action(detail=False, methods=['post'], url_path='cleaning')
    def quick_cleaning_legacy(self, request):
        return self._handle_quick_cleaning(request)

    def _handle_quick_cleaning(self, request):
        from apps.bookings.models import Booking

        floor_id = request.data.get('floor')
        floor_obj = None

        if floor_id is not None:
            try:
                floor_obj = Floor.objects.get(pk=floor_id)
            except Floor.DoesNotExist:
                return Response(
                    {'detail': 'Floor not found.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            # Try to determine floor from user's most recent booking
            last_booking = (
                Booking.objects.filter(
                    user=request.user,
                    company=request.user.company,
                )
                .select_related('resource')
                .order_by('-start_time')
                .first()
            )
            if last_booking and last_booking.resource and last_booking.resource.floor:
                # resource.floor is a PositiveIntegerField (floor number),
                # try to find the Floor object by number
                floor_obj = Floor.objects.filter(
                    number=last_booking.resource.floor
                ).first()

            if floor_obj is None:
                return Response(
                    {'detail': 'No floor provided and no recent booking found to determine floor.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        sr = ServiceRequest.objects.create(
            created_by=request.user,
            company=request.user.company,
            request_type='cleaning',
            urgency='low',
            floor=floor_obj,
            description='Quick cleaning request',
        )
        serializer = ServiceRequestSerializer(sr, context={'request': request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(
        tags=['Services'],
        summary='Update request status (company_admin or superadmin)',
        request=ServiceRequestStatusSerializer,
        responses={
            200: ServiceRequestSerializer,
            400: OpenApiResponse(description='Invalid status transition'),
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Admin only'),
            404: OpenApiResponse(description='Not found'),
        },
    )
    @action(detail=True, methods=['patch'], url_path='status', permission_classes=[IsCompanyAdmin])
    def update_status(self, request, pk=None):
        return self._handle_status_update(request)

    @extend_schema(
        tags=['Services'],
        summary='[Deprecated] Update request status (legacy alias for /status/)',
        description='Backward-compatible alias for PATCH /api/v1/services/requests/{id}/status/. '
                    'Use /status/ as canonical endpoint.',
        request=ServiceRequestStatusSerializer,
        responses={
            200: ServiceRequestSerializer,
            400: OpenApiResponse(description='Invalid status transition'),
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Admin only'),
            404: OpenApiResponse(description='Not found'),
        },
        deprecated=True,
    )
    @action(detail=True, methods=['patch'], url_path='update-status', permission_classes=[IsCompanyAdmin])
    def update_status_legacy(self, request, pk=None):
        return self._handle_status_update(request)

    def _handle_status_update(self, request):
        if 'status' not in request.data:
            return Response(
                {'status': ['This field is required.']},
                status=status.HTTP_400_BAD_REQUEST,
            )

        sr = self.get_object()
        previous_status = sr.status
        serializer = ServiceRequestStatusSerializer(sr, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        sr.refresh_from_db()
        status_changed = sr.status != previous_status

        if status_changed and sr.status == 'completed' and sr.completed_at is None:
            sr.completed_at = timezone.now()
            sr.save(update_fields=['completed_at'])

        if status_changed and sr.created_by:
            create_notification(
                user=sr.created_by,
                notification_type='service_request_update',
                title='Service request status updated',
                message=f'Your service request status has been changed to: {sr.get_status_display()}',
            )

        return Response(
            ServiceRequestSerializer(sr, context={'request': request}).data,
        )

    @extend_schema(
        tags=['Services'],
        summary='Rate completed request',
        request=ServiceRequestRateSerializer,
        responses={
            200: ServiceRequestSerializer,
            400: OpenApiResponse(description='Request not completed or already rated'),
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Not the request creator'),
            404: OpenApiResponse(description='Not found'),
        },
    )
    @action(detail=True, methods=['post'], url_path='rate')
    def rate(self, request, pk=None):
        sr = self.get_object()

        if sr.created_by != request.user:
            return Response(
                {'detail': 'You can only rate your own requests.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        if sr.status != 'completed':
            return Response(
                {'detail': 'Request must be completed before rating.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if sr.rating is not None:
            return Response(
                {'detail': 'This request has already been rated.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = ServiceRequestRateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sr.rating = serializer.validated_data['rating']
        sr.save(update_fields=['rating'])

        return Response(
            ServiceRequestSerializer(sr, context={'request': request}).data,
        )


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
class AnnouncementViewSet(SetCompanyOnCreateMixin, viewsets.ModelViewSet):
    serializer_class = AnnouncementSerializer
    permission_classes = [IsCompanyAdminOrReadOnly]
    filterset_fields = ['scope', 'category', 'is_pinned']

    def get_queryset(self):
        user = self.request.user
        building_qs = Announcement.objects.filter(scope='building')
        if user.company_id:
            company_qs = Announcement.objects.filter(scope='company', company=user.company)
            return (building_qs | company_qs).distinct()
        return building_qs

    def perform_create(self, serializer):
        serializer.save(author=self.request.user, company=self.request.user.company)
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
