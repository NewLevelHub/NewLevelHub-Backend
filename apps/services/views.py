from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.utils import timezone
from drf_spectacular.utils import (
    extend_schema, extend_schema_view, OpenApiResponse, OpenApiParameter, inline_serializer,
)
import rest_framework.fields as fields

from apps.core.permissions import IsSuperAdmin, IsCompanyMember, IsCompanyAdminOrReadOnly
from apps.core.mixins import SetCompanyOnCreateMixin
from apps.notifications.utils import create_notification
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

# Valid status transitions: new → accepted → in_progress → completed
_STATUS_TRANSITIONS = {
    'new': 'accepted',
    'accepted': 'in_progress',
    'in_progress': 'completed',
}


@extend_schema_view(
    list=extend_schema(
        tags=['Services'],
        summary='List service requests',
        parameters=[
            OpenApiParameter(name='request_type', description='Filter by type', required=False, type=str,
                             enum=['cleaning', 'repair', 'supplies', 'general']),
            OpenApiParameter(name='status', description='Filter by status', required=False, type=str,
                             enum=['new', 'accepted', 'in_progress', 'completed']),
            OpenApiParameter(name='urgency', description='Filter by urgency', required=False, type=str,
                             enum=['normal', 'urgent']),
            OpenApiParameter(name='floor', description='Filter by floor number', required=False, type=int),
        ],
        responses={200: ServiceRequestSerializer(many=True)},
    ),
    create=extend_schema(
        tags=['Services'],
        summary='Create service request',
        request=inline_serializer(
            name='ServiceRequestCreate',
            fields={
                'request_type': fields.ChoiceField(choices=['cleaning', 'repair', 'supplies', 'general']),
                'urgency': fields.ChoiceField(choices=['normal', 'urgent'], required=False),
                'floor': fields.IntegerField(required=False, allow_null=True),
                'location': fields.CharField(required=False),
                'description': fields.CharField(required=False),
                'photo': fields.ImageField(required=False, allow_null=True),
            },
        ),
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
class ServiceRequestViewSet(viewsets.ModelViewSet):
    serializer_class = ServiceRequestSerializer
    permission_classes = [IsCompanyMember]
    filterset_fields = ['request_type', 'status', 'urgency', 'floor']
    ordering = ['-created_at']

    def get_queryset(self):
        user = self.request.user
        if user.role == 'superadmin':
            return ServiceRequest.objects.all().order_by('-created_at')
        if user.role == 'company_admin':
            return ServiceRequest.objects.filter(
                user__company=user.company,
            ).order_by('-created_at')
        return ServiceRequest.objects.filter(user=user).order_by('-created_at')

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @extend_schema(
        tags=['Services'],
        summary='Quick cleaning request',
        request=inline_serializer(
            name='QuickCleaningRequest',
            fields={'floor': fields.IntegerField(required=False, allow_null=True)},
        ),
        responses={
            201: ServiceRequestSerializer,
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Forbidden'),
        },
    )
    @action(detail=False, methods=['post'], url_path='cleaning')
    def quick_cleaning(self, request):
        from apps.bookings.models import Booking

        floor = request.data.get('floor')
        if floor is None:
            last_booking = (
                Booking.objects.filter(user=request.user, status='confirmed')
                .select_related('resource')
                .order_by('-created_at')
                .first()
            )
            if last_booking and last_booking.resource:
                floor = last_booking.resource.floor

        sr = ServiceRequest.objects.create(
            user=request.user,
            request_type='cleaning',
            urgency='normal',
            floor=floor,
            description='Quick cleaning request',
        )
        return Response(ServiceRequestSerializer(sr).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        tags=['Services'],
        summary='Update request status (superadmin)',
        request=inline_serializer(
            name='ServiceRequestStatusUpdate',
            fields={
                'status': fields.ChoiceField(choices=['new', 'accepted', 'in_progress', 'completed']),
                'assigned_to': fields.IntegerField(required=False, allow_null=True),
            },
        ),
        responses={
            200: ServiceRequestSerializer,
            400: OpenApiResponse(description='Invalid status transition'),
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Superadmin only'),
            404: OpenApiResponse(description='Not found'),
        },
    )
    @action(detail=True, methods=['patch'], url_path='update-status', permission_classes=[IsSuperAdmin])
    def update_status(self, request, pk=None):
        sr = self.get_object()
        new_status = request.data.get('status')

        if new_status and new_status != sr.status:
            allowed_next = _STATUS_TRANSITIONS.get(sr.status)
            if new_status != allowed_next:
                return Response(
                    {'detail': f'Invalid status transition: {sr.status} → {new_status}. '
                               f'Expected next status: {allowed_next}.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        serializer = ServiceRequestUpdateSerializer(sr, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        sr.refresh_from_db()
        if sr.status == 'completed' and sr.completed_at is None:
            sr.completed_at = timezone.now()
            sr.save(update_fields=['completed_at'])

        create_notification(
            user=sr.user,
            notification_type='service_request_update',
            title='Статус заявки изменён',
            message=f'Статус вашей заявки изменён на: {sr.get_status_display()}',
        )
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
            400: OpenApiResponse(description='Request not completed or already rated'),
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Not the request owner'),
            404: OpenApiResponse(description='Not found'),
        },
    )
    @action(detail=True, methods=['post'], url_path='rate')
    def rate(self, request, pk=None):
        sr = self.get_object()

        if sr.user != request.user:
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

        rating = request.data.get('rating')
        try:
            rating = int(rating)
        except (TypeError, ValueError):
            return Response(
                {'detail': 'Rating must be an integer between 1 and 5.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if rating not in range(1, 6):
            return Response({'detail': 'Rating must be between 1 and 5.'}, status=status.HTTP_400_BAD_REQUEST)

        sr.rating = rating
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
