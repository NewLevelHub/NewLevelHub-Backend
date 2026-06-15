from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import viewsets, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    extend_schema, extend_schema_view, OpenApiResponse, OpenApiExample, OpenApiParameter, inline_serializer,
)
import rest_framework.fields as fields


from apps.core.permissions import (
    IsSuperAdmin,
    IsCompanyAdminOrReadOnly,
    IsOwnerOrSuperAdmin,
    IsServiceManager,
    IsServiceRequestManager,
    IsGuestOrCompanyMember,
)
from apps.core.mixins import CompanyIsolationMixin
from apps.core.pagination import StandardPagination, FeedCursorPagination
from apps.notifications.utils import create_notification

from .models import Floor, MapPoint, ServiceRequest, Announcement, AnnouncementRead
from .utils import annotate_floor_occupancy
from .serializers import (
    FloorSerializer, FloorDetailSerializer, MapPointSerializer,
    MapPointSearchSerializer,
    ServiceRequestSerializer, ServiceRequestStatusSerializer, ServiceRequestRateSerializer,
    ServiceRequestAssignSerializer,
    AnnouncementSerializer, SOON_AVAILABLE_MINUTES,
)
from .filters import ServiceRequestFilter
from .tasks import send_announcement_emails, notify_announcement_subscribers


class FloorsListPagination(PageNumberPagination):
    """Floors list can grow with map points; allow clients to request enough rows in one page."""

    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 500


# ── Карта здания ──────────────────────────────────────────────────────

_FLOOR_EXAMPLE = {
    'id': 1,
    'number': 3,
    'name': 'Third Floor',
    'plan_image': 'floors/plan_3.png',
    'plan_image_url': 'https://api.example.com/media/floors/plan_3.png',
    'company': 7,
    'created_at': '2024-01-15T09:00:00+06:00',
    'updated_at': '2024-03-20T14:30:00+06:00',
}

_FLOOR_WITH_POINTS_EXAMPLE = {
    **_FLOOR_EXAMPLE,
    'map_points': [
        {
            'id': 12,
            'floor': 1,
            'point_type': 'desk',
            'label': 'Desk A1',
            'x': 120.5,
            'y': 87.3,
            'resource': 5,
            'company': 7,
        },
        {
            'id': 13,
            'floor': 1,
            'point_type': 'meeting_room',
            'label': 'Conf Room B',
            'x': 340.0,
            'y': 200.0,
            'resource': None,
            'company': 7,
        },
    ],
}

_ERROR_400 = {'error': True, 'status_code': 400, 'detail': {'number': ['This field is required.']}}
_ERROR_401 = {'error': True, 'status_code': 401, 'detail': 'Authentication credentials were not provided.'}
_ERROR_403 = {'error': True, 'status_code': 403, 'detail': 'You do not have permission to perform this action.'}
_ERROR_404 = {'error': True, 'status_code': 404, 'detail': 'Not found.'}


@extend_schema_view(
    create=extend_schema(
        tags=['Services'],
        summary='Create floor (superadmin only)',
        description=(
            'Creates a new floor for the company. '
            'Accepts multipart/form-data to allow uploading an optional floor plan image. '
            'Restricted to superadmin.'
        ),
        request=inline_serializer(
            name='FloorCreateRequest',
            fields={
                'number': fields.IntegerField(help_text='Floor number (e.g. 1, 2, 3).'),
                'name': fields.CharField(help_text='Human-readable floor name.'),
                'plan_image': fields.ImageField(required=False, help_text='Optional floor plan image file.'),
            },
        ),
        responses={
            201: OpenApiResponse(
                response=FloorSerializer,
                description='Floor created successfully.',
                examples=[
                    OpenApiExample(
                        'Created floor',
                        value=_FLOOR_EXAMPLE,
                        response_only=True,
                        status_codes=['201'],
                    ),
                ],
            ),
            400: OpenApiResponse(
                description='Validation error.',
                examples=[
                    OpenApiExample(
                        'Validation error',
                        value=_ERROR_400,
                        response_only=True,
                        status_codes=['400'],
                    ),
                ],
            ),
            401: OpenApiResponse(
                description='Not authenticated.',
                examples=[
                    OpenApiExample(
                        'Unauthenticated',
                        value=_ERROR_401,
                        response_only=True,
                        status_codes=['401'],
                    ),
                ],
            ),
            403: OpenApiResponse(
                description='Superadmin only.',
                examples=[
                    OpenApiExample(
                        'Forbidden',
                        value=_ERROR_403,
                        response_only=True,
                        status_codes=['403'],
                    ),
                ],
            ),
        },
    ),
    list=extend_schema(
        tags=['Services'],
        summary='List floors',
        description=(
            'Returns all floors for the company. '
            'Each floor includes `plan_image_url` (absolute URL or null). '
            'Accessible by any company member.'
        ),
        responses={
            200: OpenApiResponse(
                response=FloorSerializer(many=True),
                description='Floors retrieved successfully.',
                examples=[
                    OpenApiExample(
                        'Floor list',
                        value=[_FLOOR_EXAMPLE],
                        response_only=True,
                        status_codes=['200'],
                    ),
                ],
            ),
        },
    ),
    retrieve=extend_schema(
        tags=['Services'],
        summary='Get floor details with map points',
        description=(
            'Returns floor details including all `map_points` (nested). '
            'Accessible by any company member.'
        ),
        responses={
            200: OpenApiResponse(
                response=FloorDetailSerializer,
                description='Floor with nested map points.',
                examples=[
                    OpenApiExample(
                        'Floor detail',
                        value=_FLOOR_WITH_POINTS_EXAMPLE,
                        response_only=True,
                        status_codes=['200'],
                    ),
                ],
            ),
            404: OpenApiResponse(
                description='Floor not found.',
                examples=[
                    OpenApiExample(
                        'Not found',
                        value=_ERROR_404,
                        response_only=True,
                        status_codes=['404'],
                    ),
                ],
            ),
        },
    ),
    partial_update=extend_schema(
        tags=['Services'],
        summary='Partially update floor (superadmin only)',
        description='Updates one or more fields of a floor. All fields are optional. Restricted to superadmin.',
        request=inline_serializer(
            name='FloorPartialUpdateRequest',
            fields={
                'number': fields.IntegerField(
                    required=False, help_text='New floor number.'
                ),
                'name': fields.CharField(
                    required=False, help_text='New floor name.'
                ),
                'plan_image': fields.ImageField(
                    required=False, help_text='Replacement floor plan image file.'
                ),
            },
        ),
        responses={
            200: OpenApiResponse(
                response=FloorSerializer,
                description='Floor updated successfully.',
                examples=[
                    OpenApiExample(
                        'Updated floor',
                        value=_FLOOR_EXAMPLE,
                        response_only=True,
                        status_codes=['200'],
                    ),
                ],
            ),
            400: OpenApiResponse(
                description='Validation error.',
                examples=[
                    OpenApiExample(
                        'Validation error',
                        value=_ERROR_400,
                        response_only=True,
                        status_codes=['400'],
                    ),
                ],
            ),
            403: OpenApiResponse(
                description='Superadmin only.',
                examples=[
                    OpenApiExample(
                        'Forbidden',
                        value=_ERROR_403,
                        response_only=True,
                        status_codes=['403'],
                    ),
                ],
            ),
            404: OpenApiResponse(
                description='Floor not found.',
                examples=[
                    OpenApiExample(
                        'Not found',
                        value=_ERROR_404,
                        response_only=True,
                        status_codes=['404'],
                    ),
                ],
            ),
        },
    ),
    destroy=extend_schema(
        tags=['Services'],
        summary='Delete floor (superadmin only)',
        description=(
            'Permanently deletes the floor and the plan image file from disk. '
            'Cascades to all map_points on this floor. '
            'Restricted to superadmin.'
        ),
        responses={
            204: OpenApiResponse(description='Floor and all its map_points deleted.'),
            403: OpenApiResponse(
                description='Superadmin only.',
                examples=[
                    OpenApiExample(
                        'Forbidden',
                        value=_ERROR_403,
                        response_only=True,
                        status_codes=['403'],
                    ),
                ],
            ),
            404: OpenApiResponse(
                description='Floor not found.',
                examples=[
                    OpenApiExample(
                        'Not found',
                        value=_ERROR_404,
                        response_only=True,
                        status_codes=['404'],
                    ),
                ],
            ),
        },
    ),
)
class FloorViewSet(viewsets.ModelViewSet):
    """
    Floors are building-level objects, not company-scoped.
    Superadmin creates floors (company=null); all company members must see them.

    QuerySet rules:
      - superadmin  → all floors
      - company member → floors where company IS NULL  OR  company = user.company
      - no company (guest) → only global floors (company IS NULL)
    """

    queryset = Floor.objects.prefetch_related('points__resource', 'points__company').select_related('company')
    serializer_class = FloorSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    pagination_class = FloorsListPagination

    def _annotate_occupancy(self, qs):
        """Annotate each floor with occupancy_pct (integer, 0–100) in a single query."""
        return annotate_floor_occupancy(qs)

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.role == 'superadmin':
            return self._annotate_occupancy(qs)
        if user.company_id:
            # Single OR query avoids subtle bugs from queryset-| unions with prefetch/joins.
            filtered = qs.filter(Q(company__isnull=True) | Q(company_id=user.company_id)).distinct()
            return self._annotate_occupancy(filtered)
        # guest or user without company — show only global floors
        return self._annotate_occupancy(qs.filter(company__isnull=True))

    def perform_create(self, serializer):
        # Floors are global; superadmin creates them without a company.
        serializer.save(company=None)

    def get_permissions(self):
        if self.action in ('create', 'update', 'partial_update', 'destroy'):
            return [IsSuperAdmin()]
        return [IsGuestOrCompanyMember()]

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return FloorDetailSerializer
        return FloorSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if self.action == 'retrieve':
            context['now'] = timezone.now()
        return context

    @extend_schema(
        tags=['Services'],
        summary='Floor map with resource statuses',
        description=(
            'Returns map points with booking-derived `resource_status` for the requested time. '
            'Status precedence is deterministic: `blocked` > booking-derived states (`occupied`/`soon_available`) > '
            '`free`. For non-bookable points or points without a linked resource, `resource_status` is null. '
            f'`soon_available` means the active confirmed booking ends in <= {SOON_AVAILABLE_MINUTES} minutes.'
        ),
        parameters=[
            OpenApiParameter(
                'datetime',
                OpenApiTypes.DATETIME,
                OpenApiParameter.QUERY,
                required=False,
                description='Point in time for status calculation (ISO 8601). Defaults to now.',
            ),
        ],
        responses={
            200: inline_serializer(
                name='FloorMapResponse',
                fields={
                    'floor_id': fields.IntegerField(),
                    'floor_name': fields.CharField(),
                    'at_time': fields.DateTimeField(),
                    'points': MapPointSerializer(many=True),
                },
            ),
            400: OpenApiResponse(description='Invalid datetime parameter'),
            404: OpenApiResponse(description='Floor not found'),
        },
        examples=[
            OpenApiExample(
                'Map status example',
                value={
                    'floor_id': 2,
                    'floor_name': 'Second Floor',
                    'at_time': '2026-05-01T11:00:00+06:00',
                    'points': [
                        {
                            'id': 100,
                            'floor': 2,
                            'point_type': 'desk',
                            'label': 'Desk A-01',
                            'x': 15.0,
                            'y': 20.0,
                            'resource': 50,
                            'resource_name': 'Desk A-01',
                            'resource_status': 'soon_available',
                            'resource_status_reason': 'active_booking_ends_within_threshold',
                            'next_free_at': '2026-05-01T11:25:00+06:00',
                            'company': 7,
                            'company_name': 'ACME',
                        },
                        {
                            'id': 101,
                            'floor': 2,
                            'point_type': 'kitchen',
                            'label': 'Kitchen',
                            'x': 70.0,
                            'y': 40.0,
                            'resource': None,
                            'resource_name': None,
                            'resource_status': None,
                            'resource_status_reason': 'not_a_bookable_resource',
                            'next_free_at': None,
                            'company': 7,
                            'company_name': 'ACME',
                        },
                    ],
                },
                response_only=True,
                status_codes=['200'],
            ),
        ],
    )
    @action(detail=True, methods=['get'], url_path='map')
    def map(self, request, pk=None):
        floor = self.get_object()
        datetime_param = request.query_params.get('datetime')
        if datetime_param:
            at_time = parse_datetime(datetime_param)
            if at_time is None:
                return Response(
                    {'detail': 'Invalid datetime format. Use ISO 8601.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            at_time = timezone.now()

        user = request.user
        points_qs = MapPoint.objects.select_related(
            'resource', 'resource__assigned_company', 'company',
        ).filter(floor=floor)
        # Superadmin sees all points.
        # Premium-company points are private: visible only to that company's members.
        # We check both MapPoint.company (office-type points) and
        # resource.assigned_company (desk/meeting_room/etc. points) because for
        # bookable resource types MapPoint.company may be null while the exclusivity
        # is expressed on Resource.assigned_company.
        if user.role != 'superadmin':
            points_qs = points_qs.filter(
                Q(company__isnull=True)
                | Q(company__plan__in=['basic', 'standard'])
                | Q(company__plan='premium', company_id=user.company_id)
            ).filter(
                Q(resource__isnull=True)
                | Q(resource__assigned_company__isnull=True)
                | Q(resource__assigned_company__plan__in=['basic', 'standard'])
                | Q(resource__assigned_company__plan='premium', resource__assigned_company_id=user.company_id)
            )

        serializer = MapPointSerializer(
            points_qs,
            many=True,
            context={'now': at_time, 'request': request},
        )
        return Response({
            'floor_id': floor.id,
            'floor_name': floor.name,
            'at_time': at_time,
            'points': serializer.data,
        })

    def perform_update(self, serializer):
        instance = serializer.instance
        old_path = instance.plan_image.name if instance.plan_image else None
        instance = serializer.save()
        new_path = instance.plan_image.name if instance.plan_image else None
        # If the file was replaced — delete the old one from storage
        if old_path and old_path != new_path:
            instance.plan_image.storage.delete(old_path)

    def perform_destroy(self, instance):
        from django.db.models import Q
        from apps.bookings.models import Resource

        # Collect resources linked via map points on this floor
        point_resource_ids = list(
            instance.points.filter(resource__isnull=False)
            .values_list('resource_id', flat=True)
            .distinct()
        )
        floor_number = instance.number

        floor_pk = instance.pk
        image_name = instance.plan_image.name if instance.plan_image else None
        storage = instance.plan_image.storage if instance.plan_image else None

        # Collect all resource IDs linked to this floor via FK before deleting
        # (floor_fk becomes NULL after delete due to SET_NULL, so capture IDs now)
        from apps.bookings.models import Resource as _Resource
        floor_fk_resource_ids = list(
            _Resource.objects.filter(floor_fk_id=floor_pk).values_list('id', flat=True)
        )

        instance.delete()  # CASCADE deletes all map points on this floor

        # Delete resources by map-point link OR by floor number match OR by FK
        # (covers resources created for this floor but not yet placed on the map)
        all_resource_ids = set(point_resource_ids) | set(floor_fk_resource_ids)
        Resource.objects.filter(
            Q(id__in=all_resource_ids) | Q(floor=floor_number)
        ).delete()

        if image_name and storage:
            storage.delete(image_name)


@extend_schema_view(
    list=extend_schema(
        tags=['Services'],
        summary='List map points',
        parameters=[OpenApiParameter(
            'search', OpenApiTypes.STR, OpenApiParameter.QUERY,
            required=False, description='Filter by label (partial match).',
        )],
        responses={200: MapPointSerializer(many=True)},
    ),
    create=extend_schema(
        tags=['Services'],
        summary='Create map point (superadmin)',
        request=inline_serializer(
            name='MapPointCreateRequest',
            fields={
                'floor': fields.IntegerField(help_text='Floor ID.'),
                'point_type': fields.CharField(help_text='One of: desk, meeting_room, parking, capsule, toilet, '
                                               'kitchen, elevator, exit, office, other.'),
                'x': fields.FloatField(help_text='Horizontal position 0–100 (%).'),
                'y': fields.FloatField(help_text='Vertical position 0–100 (%).'),
                'label': fields.CharField(required=False, help_text='Optional human-readable label.'),
                'resource': fields.IntegerField(
                    required=False,
                    help_text='Resource ID (required for desk/meeting_room/parking/capsule).',
                ),
                'company': fields.IntegerField(required=False, help_text='Company ID (required for office).'),
            },
        ),
        responses={
            201: MapPointSerializer,
            400: OpenApiResponse(description='Validation error (missing resource/company, x/y out of range, etc.)'),
            403: OpenApiResponse(description='Superadmin only'),
        },
    ),
    partial_update=extend_schema(
        tags=['Services'],
        summary='Update map point (superadmin)',
        request=inline_serializer(
            name='MapPointUpdateRequest',
            fields={
                'x': fields.FloatField(required=False, help_text='Horizontal position 0–100 (%).'),
                'y': fields.FloatField(required=False, help_text='Vertical position 0–100 (%).'),
                'label': fields.CharField(required=False),
                'resource': fields.IntegerField(required=False),
                'company': fields.IntegerField(required=False),
            },
        ),
        responses={
            200: MapPointSerializer,
            400: OpenApiResponse(description='Validation error'),
            403: OpenApiResponse(description='Superadmin only'),
            404: OpenApiResponse(description='Not found'),
        },
    ),
    destroy=extend_schema(
        tags=['Services'],
        summary='Delete map point (superadmin)',
        responses={
            204: OpenApiResponse(description='Deleted'),
            403: OpenApiResponse(description='Superadmin only'),
            404: OpenApiResponse(description='Not found'),
        },
    ),
)
class MapPointViewSet(viewsets.ModelViewSet):
    queryset = MapPoint.objects.select_related('resource', 'company')
    serializer_class = MapPointSerializer
    filter_backends = [SearchFilter]
    search_fields = ['label']

    def get_queryset(self):
        user = self.request.user
        qs = MapPoint.objects.select_related('resource', 'resource__assigned_company', 'company')
        if user.role == 'superadmin':
            return qs
        return qs.filter(
            Q(company__isnull=True)
            | Q(company__plan__in=['basic', 'standard'])
            | Q(company__plan='premium', company_id=user.company_id)
        ).filter(
            Q(resource__isnull=True)
            | Q(resource__assigned_company__isnull=True)
            | Q(resource__assigned_company__plan__in=['basic', 'standard'])
            | Q(resource__assigned_company__plan='premium', resource__assigned_company_id=user.company_id)
        )

    def get_permissions(self):
        if self.action in ('create', 'update', 'partial_update', 'destroy'):
            return [IsSuperAdmin()]
        return [IsAuthenticated()]

    @extend_schema(
        tags=['Services'],
        summary='Search map points by label or resource name',
        parameters=[
            OpenApiParameter(
                'q',
                OpenApiTypes.STR,
                OpenApiParameter.QUERY,
                required=True,
                description='Search query matched against label and linked resource name.',
            ),
        ],
        responses={
            200: MapPointSearchSerializer(many=True),
            400: OpenApiResponse(description='Missing or empty q parameter'),
        },
    )
    @action(detail=False, methods=['get'], url_path='search')
    def search(self, request):
        q = request.query_params.get('q', '').strip()
        if not q:
            return Response(
                {'detail': 'Query parameter "q" is required and must not be empty.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        points = (
            self.get_queryset()
            .select_related('floor')
            .filter(Q(label__icontains=q) | Q(resource__name__icontains=q))
        )
        serializer = MapPointSearchSerializer(points, many=True)
        return Response(serializer.data)


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
    permission_classes = [IsGuestOrCompanyMember]
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
        # superadmin and service_manager are building-wide and see everything.
        if user.role in ('superadmin', 'service_manager'):
            return base_qs
        if user.role == 'company_admin' and user.company_id:
            # Company admins see all requests within their company
            return base_qs.filter(company=user.company_id)
        # Regular employees see only their own requests
        return base_qs.filter(created_by=user)

    def get_permissions(self):
        # service_manager is a building-wide responsible role with no company FK,
        # so IsGuestOrCompanyMember rejects it. Allow it through for read actions;
        # guests may also read their own requests. Action-level permission
        # decorators (status/assign) handle the manage-level gates separately.
        if self.action in ('list', 'retrieve'):
            return [(IsGuestOrCompanyMember | IsServiceManager)()]
        return super().get_permissions()

    def perform_create(self, serializer):
        instance = serializer.save(
            created_by=self.request.user,
            company=self.request.user.company,
        )
        self._notify_service_managers_new_request(instance)

    def _notify_service_managers_new_request(self, instance):
        from django.contrib.auth import get_user_model
        from apps.notifications.utils import create_notification

        User = get_user_model()
        creator = instance.created_by
        creator_name = (
            f'{creator.first_name} {creator.last_name}'.strip() or creator.email
        ) if creator else ''
        service_managers = User.objects.filter(role='service_manager', is_active=True)
        for manager in service_managers:
            create_notification(
                user=manager,
                notification_type='service_request_update',
                title='Новая сервисная заявка',
                message=f'{creator_name} создал(а) новую заявку: {instance.get_request_type_display()}',
                link='/service-requests',
            )

    @extend_schema(
        tags=['Services'],
        summary='Quick cleaning request',
        request=inline_serializer(
            name='QuickCleaningRequest',
            fields={'floor': fields.IntegerField(required=False, allow_null=True)},
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
            fields={'floor': fields.IntegerField(required=False, allow_null=True)},
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
                # Prefer the FK link; fall back to number-based lookup for legacy data
                floor_obj = getattr(last_booking.resource, 'floor_fk', None)
                if floor_obj is None:
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
            location=request.data.get('location', ''),
            description=request.data.get('description', 'Quick cleaning request'),
        )
        self._notify_service_managers_new_request(sr)
        serializer = ServiceRequestSerializer(sr, context={'request': request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(
        tags=['Services'],
        summary='Update request status (superadmin or service_manager)',
        request=ServiceRequestStatusSerializer,
        responses={
            200: ServiceRequestSerializer,
            400: OpenApiResponse(description='Invalid status transition'),
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='superadmin or service_manager only'),
            404: OpenApiResponse(description='Not found'),
        },
    )
    @action(
        detail=True, methods=['patch'], url_path='status',
        permission_classes=[IsServiceRequestManager],
    )
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
            403: OpenApiResponse(description='superadmin or service_manager only'),
            404: OpenApiResponse(description='Not found'),
        },
        deprecated=True,
    )
    @action(
        detail=True, methods=['patch'], url_path='update-status',
        permission_classes=[IsServiceRequestManager],
    )
    def update_status_legacy(self, request, pk=None):
        return self._handle_status_update(request)

    @extend_schema(
        tags=['Services'],
        summary='Assign executor to a service request',
        description=(
            'Assign or reassign the executor of a service request. '
            'Pass ``assigned_to: null`` to clear the assignment. '
            'Available to superadmin and service_manager only.'
        ),
        request=ServiceRequestAssignSerializer,
        responses={
            200: ServiceRequestSerializer,
            400: OpenApiResponse(description='Invalid assignee'),
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='superadmin or service_manager only'),
            404: OpenApiResponse(description='Not found'),
        },
    )
    @action(
        detail=True, methods=['patch'], url_path='assign',
        permission_classes=[IsServiceRequestManager],
    )
    def assign(self, request, pk=None):
        sr = self.get_object()
        serializer = ServiceRequestAssignSerializer(
            sr, data=request.data, partial=True, context={'request': request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        sr.refresh_from_db()
        return Response(
            ServiceRequestSerializer(sr, context={'request': request}).data,
        )

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
                title='Обновление заявки на сервис',
                message=f'Статус: {sr.get_status_display()}',
                link='/service-requests/',
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
        announcement = serializer.save(author=user, company=company)
        if announcement.is_pinned and announcement.notify_email:
            send_announcement_emails.delay(announcement.id)
        notify_announcement_subscribers.delay(announcement.id)

    @extend_schema(
        tags=['Services'],
        summary='Mark announcement as read',
        request=None,
        responses={
            200: AnnouncementSerializer,
            401: OpenApiResponse(description='Not authenticated'),
            404: OpenApiResponse(description='Not found'),
        },
    )
    @action(detail=True, methods=['post'], url_path='read', permission_classes=[IsAuthenticated])
    def mark_read(self, request, pk=None):
        announcement = self.get_object()
        AnnouncementRead.objects.get_or_create(announcement=announcement, user=request.user)
        serializer = AnnouncementSerializer(announcement, context={'request': request})
        return Response(serializer.data)
