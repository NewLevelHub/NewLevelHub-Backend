from datetime import date, datetime, time, timedelta

from django.db.models import Prefetch, Q
from django.http import Http404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.db import transaction
from rest_framework import serializers, viewsets, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError, PermissionDenied, NotFound
from rest_framework.response import Response
from drf_spectacular.utils import (
    extend_schema,
    extend_schema_view,
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    inline_serializer,
)
from drf_spectacular.types import OpenApiTypes
import rest_framework.fields as fields

from apps.core.permissions import (
    IsSuperAdmin, IsCompanyAdmin, IsCompanyMember, IsOwnerOrAdmin, IsOwnerOrSuperAdmin,
    IsEmailVerifiedOrSuperAdmin,
)
from apps.notifications.utils import create_notification
from apps.core.mixins import CompanyIsolationMixin, SetCompanyOnCreateMixin
from apps.users.models import User
from .models import (
    Resource,
    Booking,
    BookingParticipant,
    RecurringBooking,
    ResourceBlock,
    BookingCancellationAudit,
    BookingChangeAudit,
)
from .serializers import (
    ResourceSerializer,
    ResourceDetailSerializer,
    ResourceListSerializer,
    ResourceBulkCreateSerializer,
    ResourceDayScheduleSlotSerializer,
    BookingSerializer,
    BookingCreateSerializer,
    RecurringBookingSerializer,
    RecurringBookingCreateSerializer,
    ResourceBlockSerializer,
    _EQUIPMENT_KEYS,
)
from .filters import ResourceFilter, BookingFilter
from .schedule import get_schedule_status, week_range_for_date
from .tasks import create_bookings_for_recurring


_RecurringBookingCreatedSchema = inline_serializer(
    name='RecurringBookingCreated',
    fields={
        'id': fields.IntegerField(),
        'resource': fields.IntegerField(help_text='Resource primary key.'),
        'resource_id': fields.IntegerField(help_text='Same as `resource` (read-only alias).'),
        'user': fields.IntegerField(),
        'company': fields.IntegerField(allow_null=True),
        'day_of_week': fields.IntegerField(help_text='0=Monday … 6=Sunday'),
        'start_time': fields.TimeField(),
        'end_time': fields.TimeField(),
        'is_active': fields.BooleanField(),
        'valid_from': fields.DateField(),
        'valid_until': fields.DateField(allow_null=True, help_text='Series end date (from `repeat_until` on create).'),
        'created_at': fields.DateTimeField(),
        'updated_at': fields.DateTimeField(),
        'skipped_dates': serializers.ListField(
            child=serializers.CharField(),
            help_text='Local calendar dates (YYYY-MM-DD) where a slot conflicted and was skipped.',
        ),
    },
)


# ---------------------------------------------------------------------------
# Shared OpenApiExample sets — reused across list / create / retrieve actions
# ---------------------------------------------------------------------------

_RESOURCE_REQUEST_EXAMPLES = [
    OpenApiExample(
        name='Desk',
        summary='Create a desk resource',
        value={
            'type': 'desk',
            'name': 'Desk A-01',
            'floor': 2,
            'zone': 'Open Space',
            'capacity': 1,
            'is_hot_desk': True,
        },
        request_only=True,
    ),
    OpenApiExample(
        name='Meeting Room',
        summary='Create a meeting-room resource',
        value={
            'type': 'meeting_room',
            'name': 'Boardroom Alpha',
            'floor': 3,
            'capacity': 12,
            'equipment': {
                'projector': True,
                'whiteboard': True,
                'tv': False,
                'video_conf': True,
                'monitor': False,
                'dock': False,
                'power_outlet': True,
            },
        },
        request_only=True,
    ),
    OpenApiExample(
        name='Parking Spot',
        summary='Create a parking resource',
        value={
            'type': 'parking',
            'name': 'Spot P-07',
            'floor': 0,
            'parking_type': 'regular',
        },
        request_only=True,
    ),
    OpenApiExample(
        name='Capsule',
        summary='Create a capsule resource',
        value={
            'type': 'capsule',
            'name': 'Capsule Q-03',
            'floor': 1,
            'capsule_zone': 'quiet',
        },
        request_only=True,
    ),
]

_RESOURCE_RESPONSE_EXAMPLE = OpenApiExample(
    name='Resource (desk)',
    summary='Typical resource object',
    value={
        'id': 42,
        'type': 'desk',
        'name': 'Desk A-01',
        'floor': 2,
        'zone': 'Open Space',
        'description': '',
        'photo': None,
        'capacity': 1,
        'equipment': None,
        'is_active': True,
        'has_monitor': False,
        'has_dock': False,
        'has_power_outlet': True,
        'is_hot_desk': True,
        'assigned_company': None,
        'min_duration_minutes': 30,
        'max_duration_minutes': 480,
        'availability_start': '08:00:00',
        'availability_end': '22:00:00',
        'availability_days': [0, 1, 2, 3, 4],
        'parking_type': None,
        'capsule_zone': '',
        'created_at': '2025-01-15T09:00:00+06:00',
        'updated_at': '2025-01-15T09:00:00+06:00',
    },
    response_only=True,
)

_RESOURCE_400_EXAMPLE = OpenApiExample(
    name='Validation error',
    summary='Missing required field',
    value={'error': True, 'status_code': 400, 'detail': {'capacity': ['This field is required for meeting_room.']}},
    response_only=True,
    status_codes=['400'],
)

_AUTH_401_EXAMPLE = OpenApiExample(
    name='Unauthenticated',
    value={'error': True, 'status_code': 401, 'detail': 'Authentication credentials were not provided.'},
    response_only=True,
    status_codes=['401'],
)

_FORBIDDEN_403_EXAMPLE = OpenApiExample(
    name='Forbidden',
    value={'error': True, 'status_code': 403, 'detail': 'You do not have permission to perform this action.'},
    response_only=True,
    status_codes=['403'],
)

# BookingCreateSerializer request examples
_BOOKING_REQUEST_EXAMPLES = [
    OpenApiExample(
        name='Book a desk',
        summary='Desk — single working day, max 14 days ahead',
        description=(
            'Desk bookings must start within 14 calendar days from now. '
            'The booking must fall within a single calendar day and inside '
            'the resource availability window (default 08:00–22:00).'
        ),
        value={
            'resource_id': 42,
            'start_time': '2025-04-20T09:00:00+06:00',
            'end_time': '2025-04-20T18:00:00+06:00',
            'description': 'Working from the office today',
        },
        request_only=True,
    ),
    OpenApiExample(
        name='Book a meeting room',
        summary='Meeting room — 30 min to 4 hours, participant_ids supported',
        description=(
            'Meeting room bookings must be between 30 minutes and 4 hours. '
            'Pass participant_ids (list of user PKs) to invite colleagues; '
            'they will receive in-app notifications.'
        ),
        value={
            'resource_id': 7,
            'start_time': '2025-04-21T14:00:00+06:00',
            'end_time': '2025-04-21T15:30:00+06:00',
            'description': 'Q2 planning sync',
            'participant_ids': [3, 8, 15],
        },
        request_only=True,
    ),
    OpenApiExample(
        name='Book a parking spot',
        summary='Parking — whole-day only, max 7 days ahead',
        description=(
            'Parking bookings are whole-day: start_time must be 00:00 and '
            'end_time must be 23:59 (same day) or 00:00 (next day). '
            'Must start within 7 days from now.'
        ),
        value={
            'resource_id': 19,
            'start_time': '2025-04-20T00:00:00+06:00',
            'end_time': '2025-04-20T23:59:00+06:00',
            'description': '',
        },
        request_only=True,
    ),
    OpenApiExample(
        name='Book a capsule',
        summary='Capsule — 1 hour to 8 hours',
        description=(
            'Capsule bookings must be between 1 hour (60 minutes) and 8 hours (480 minutes). '
            'Must fall within a single calendar day and inside availability hours.'
        ),
        value={
            'resource_id': 33,
            'start_time': '2025-04-20T10:00:00+06:00',
            'end_time': '2025-04-20T14:00:00+06:00',
            'description': 'Focus session',
        },
        request_only=True,
    ),
]

_BOOKING_201_EXAMPLE = OpenApiExample(
    name='Booking created',
    summary='Successfully created booking',
    value={
        'id': 101,
        'resource': 42,
        'resource_name': 'Desk A-01',
        'user': 5,
        'user_name': 'Aibek Seitkali',
        'company': 2,
        'start_time': '2025-04-20T09:00:00+06:00',
        'end_time': '2025-04-20T18:00:00+06:00',
        'status': 'confirmed',
        'description': 'Working from the office today',
        'cancelled_by': None,
        'cancel_reason': '',
        'participants': [],
        'created_at': '2025-04-15T10:00:00+06:00',
        'updated_at': '2025-04-15T10:00:00+06:00',
    },
    response_only=True,
    status_codes=['201'],
)

_BOOKING_409_EXAMPLE = OpenApiExample(
    name='Conflict',
    summary='Resource already occupied for the requested time slot',
    value={'error': True, 'status_code': 409, 'detail': 'Selected time slot is already occupied.'},
    response_only=True,
    status_codes=['409'],
)

_BOOKING_400_EXAMPLES = [
    OpenApiExample(
        name='Desk — too far in advance',
        value={
            'error': True,
            'status_code': 400,
            'detail': {'detail': 'Desk booking must start within 14 days from now.'},
        },
        response_only=True,
        status_codes=['400'],
    ),
    OpenApiExample(
        name='Meeting room — duration too short',
        value={
            'error': True,
            'status_code': 400,
            'detail': {
                'detail': 'Meeting room booking minimum duration is 30 minutes.'
            },
        },
        response_only=True,
        status_codes=['400'],
    ),
    OpenApiExample(
        name='Meeting room — duration too long',
        value={
            'error': True,
            'status_code': 400,
            'detail': {'detail': 'Meeting room booking maximum duration is 4 hours.'},
        },
        response_only=True,
        status_codes=['400'],
    ),
    OpenApiExample(
        name='Parking — not whole day',
        value={
            'error': True,
            'status_code': 400,
            'detail': {'detail': 'Parking booking must be whole-day only (start 00:00, end 23:59 or next day 00:00).'},
        },
        response_only=True,
        status_codes=['400'],
    ),
    OpenApiExample(
        name='Capsule — duration too short',
        value={
            'error': True,
            'status_code': 400,
            'detail': {'detail': 'Capsule booking minimum duration is 1 hour.'},
        },
        response_only=True,
        status_codes=['400'],
    ),
    OpenApiExample(
        name='Active booking limit exceeded',
        value={
            'error': True,
            'status_code': 400,
            'detail': {'detail': 'Active booking limit exceeded (5).'},
        },
        response_only=True,
        status_codes=['400'],
    ),
]


# ---------------------------------------------------------------------------
# ResourceViewSet
# ---------------------------------------------------------------------------

@extend_schema_view(
    list=extend_schema(
        tags=['Resources'],
        summary='List resources (catalog)',
        description=(
            'Returns a paginated catalog of resources visible to the current user.\n\n'
            '**Access rules:**\n'
            '- Any authenticated user (including `guest`) can browse the catalog.\n'
            '- `superadmin` sees all resources (active and inactive).\n'
            '- All other roles see only `is_active=True` resources.\n'
            '- Users whose company has a `premium` plan also see resources assigned to '
            'their company (`assigned_company=<their company>`), in addition to unassigned ones.\n\n'
            '**Extra response field:** `meeting_room_equipment_keys` — list of equipment keys '
            '(`projector`, `tv`, `whiteboard`, `video_conf`, `monitor`, `dock`, `power_outlet`) '
            'that exist on at least one meeting room matching the current filters (used to build '
            'dynamic filter chips on the frontend).\n\n'
            '**Availability filter:** pass both `available_from` and `available_to` (ISO 8601 '
            'datetime strings) to exclude resources that have a confirmed booking or admin block '
            'overlapping that interval.'
        ),
        parameters=[
            OpenApiParameter(
                name='type',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Filter by resource type: `desk`, `meeting_room`, `parking`, `capsule`.',
                enum=['desk', 'meeting_room', 'parking', 'capsule'],
            ),
            OpenApiParameter(
                name='resource_type',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Alias for `type` — both map to the same model field.',
            ),
            OpenApiParameter(
                name='floor',
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Exact floor number.',
            ),
            OpenApiParameter(
                name='capacity_min',
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Minimum seating capacity (inclusive).',
            ),
            OpenApiParameter(
                name='capacity_max',
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Maximum seating capacity (inclusive).',
            ),
            OpenApiParameter(
                name='is_active',
                type=OpenApiTypes.BOOL,
                location=OpenApiParameter.QUERY,
                required=False,
                description='`true` / `false`. Superadmin only — non-superadmin always gets active resources.',
            ),
            OpenApiParameter(
                name='equipment',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    'Comma-separated equipment keys for meeting rooms: '
                    '`projector`, `tv`, `whiteboard`, `video_conf`, `monitor`, `dock`, `power_outlet`. '
                    'Example: `?equipment=projector,whiteboard`'
                ),
            ),
            OpenApiParameter(
                name='has_projector',
                type=OpenApiTypes.BOOL,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Filter meeting rooms that have a projector.',
            ),
            OpenApiParameter(
                name='has_tv',
                type=OpenApiTypes.BOOL,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Filter meeting rooms that have a TV.',
            ),
            OpenApiParameter(
                name='has_video_conf',
                type=OpenApiTypes.BOOL,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Filter meeting rooms that have video-conferencing equipment.',
            ),
            OpenApiParameter(
                name='available_from',
                type=OpenApiTypes.DATETIME,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    'ISO 8601 datetime — start of the requested free interval. '
                    'Must be combined with `available_to`. '
                    'Resources with an overlapping confirmed booking or admin block are excluded.'
                ),
            ),
            OpenApiParameter(
                name='available_to',
                type=OpenApiTypes.DATETIME,
                location=OpenApiParameter.QUERY,
                required=False,
                description='ISO 8601 datetime — end of the requested free interval (see `available_from`).',
            ),
            OpenApiParameter(
                name='search',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Full-text search on the resource `name` field.',
            ),
            OpenApiParameter(
                name='ordering',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    'Sort order. Allowed fields: `name`, `floor`, `capacity`, `id`. '
                    'Prefix with `-` for descending. Example: `?ordering=-capacity,name`'
                ),
            ),
            OpenApiParameter(
                name='page',
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Page number (default: 1).',
            ),
            OpenApiParameter(
                name='page_size',
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Number of results per page (default: 20, max: 100).',
            ),
        ],
        responses={
            200: ResourceListSerializer(many=True),
            401: OpenApiResponse(
                description='Not authenticated.',
                examples=[_AUTH_401_EXAMPLE],
            ),
        },
    ),
    retrieve=extend_schema(
        tags=['Resources'],
        summary='Get resource details + 7-day busy schedule',
        description=(
            'Returns the full resource object plus a `schedule` array that lists all '
            'confirmed bookings and admin blocks for the next 7 calendar days '
            '(starting from today in the server timezone, `Asia/Almaty`).\n\n'
            '**Access:** any authenticated user.'
        ),
        responses={
            200: ResourceDetailSerializer,
            401: OpenApiResponse(description='Not authenticated.', examples=[_AUTH_401_EXAMPLE]),
            404: OpenApiResponse(description='Resource not found.'),
        },
    ),
    create=extend_schema(
        tags=['Resources'],
        summary='Create a resource (superadmin only)',
        description=(
            'Creates a new bookable resource.\n\n'
            '**Access:** `superadmin` only.\n\n'
            '**Type-specific required fields:**\n'
            '- `desk` — no extra required fields.\n'
            '- `meeting_room` — `capacity` is required (>= 1); optionally pass `equipment` JSON.\n'
            '- `parking` — `parking_type` is required (`regular` or `vip`).\n'
            '- `capsule` — `capsule_zone` is required (`quiet` or `regular`).\n\n'
            '**Equipment JSON** (meeting rooms only): '
            '`{"projector": true, "whiteboard": true, "tv": false, "video_conf": true, '
            '"monitor": false, "dock": false, "power_outlet": true}`\n\n'
            '**Availability window** (`availability_start` / `availability_end`):\n'
            'Time-of-day window when the resource can be booked (default 08:00–22:00). '
            '`availability_days` is a list of weekday integers (0=Monday … 6=Sunday).'
        ),
        request=ResourceSerializer,
        examples=_RESOURCE_REQUEST_EXAMPLES + [_RESOURCE_400_EXAMPLE, _FORBIDDEN_403_EXAMPLE],
        responses={
            201: OpenApiResponse(
                response=ResourceSerializer,
                description='Resource created.',
                examples=[_RESOURCE_RESPONSE_EXAMPLE],
            ),
            400: OpenApiResponse(
                description='Validation error.',
                examples=[_RESOURCE_400_EXAMPLE],
            ),
            401: OpenApiResponse(description='Not authenticated.', examples=[_AUTH_401_EXAMPLE]),
            403: OpenApiResponse(description='Superadmin only.', examples=[_FORBIDDEN_403_EXAMPLE]),
        },
    ),
    update=extend_schema(
        tags=['Resources'],
        summary='Full update of a resource (superadmin only)',
        description=(
            'Replaces all writable fields on an existing resource. '
            'If the resource is deactivated (`is_active=false`), '
            'all future confirmed bookings are automatically cancelled '
            'and the booking owners receive in-app notifications.\n\n'
            '**Access:** `superadmin` only.'
        ),
        request=ResourceSerializer,
        examples=_RESOURCE_REQUEST_EXAMPLES,
        responses={
            200: OpenApiResponse(
                response=ResourceSerializer,
                description='Resource updated.',
                examples=[_RESOURCE_RESPONSE_EXAMPLE],
            ),
            400: OpenApiResponse(description='Validation error.', examples=[_RESOURCE_400_EXAMPLE]),
            401: OpenApiResponse(description='Not authenticated.', examples=[_AUTH_401_EXAMPLE]),
            403: OpenApiResponse(description='Superadmin only.', examples=[_FORBIDDEN_403_EXAMPLE]),
            404: OpenApiResponse(description='Resource not found.'),
        },
    ),
    partial_update=extend_schema(
        tags=['Resources'],
        summary='Partial update of a resource (superadmin only)',
        description=(
            'Updates one or more fields on an existing resource. '
            'Same deactivation side-effect as full update: '
            'deactivating cancels future bookings.\n\n'
            '**Access:** `superadmin` only.'
        ),
        request=ResourceSerializer,
        examples=_RESOURCE_REQUEST_EXAMPLES,
        responses={
            200: OpenApiResponse(
                response=ResourceSerializer,
                description='Resource updated.',
                examples=[_RESOURCE_RESPONSE_EXAMPLE],
            ),
            400: OpenApiResponse(description='Validation error.', examples=[_RESOURCE_400_EXAMPLE]),
            401: OpenApiResponse(description='Not authenticated.', examples=[_AUTH_401_EXAMPLE]),
            403: OpenApiResponse(description='Superadmin only.', examples=[_FORBIDDEN_403_EXAMPLE]),
            404: OpenApiResponse(description='Resource not found.'),
        },
    ),
    destroy=extend_schema(
        tags=['Resources'],
        summary='Delete a resource (superadmin only)',
        description=(
            'Permanently deletes a resource. '
            'Deletion is blocked if the resource has any **future confirmed bookings** '
            '— cancel them first (or deactivate the resource, which cancels them automatically).\n\n'
            '**Access:** `superadmin` only.'
        ),
        responses={
            204: OpenApiResponse(description='Deleted successfully.'),
            400: OpenApiResponse(
                description='Resource has future confirmed bookings.',
                examples=[
                    OpenApiExample(
                        name='Has future bookings',
                        value={
                            'error': True,
                            'status_code': 400,
                            'detail': {'detail': 'Cannot delete a resource that has future confirmed bookings.'},
                        },
                        response_only=True,
                        status_codes=['400'],
                    ),
                ],
            ),
            401: OpenApiResponse(description='Not authenticated.', examples=[_AUTH_401_EXAMPLE]),
            403: OpenApiResponse(description='Superadmin only.', examples=[_FORBIDDEN_403_EXAMPLE]),
            404: OpenApiResponse(description='Resource not found.'),
        },
    ),
)
class ResourceViewSet(viewsets.ModelViewSet):
    queryset = Resource.objects.all()
    permission_classes = [IsAuthenticated]
    filterset_class = ResourceFilter
    search_fields = ['name']
    ordering_fields = ['name', 'floor', 'capacity', 'id']
    # Вторичный ключ id — стабильный порядок при одинаковых именах.
    ordering = ['name', 'id']

    def get_serializer_class(self):
        if self.action == 'list':
            return ResourceListSerializer
        if self.action == 'retrieve':
            return ResourceDetailSerializer
        return ResourceSerializer

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        if self.action == 'list':
            ctx['catalog_now'] = getattr(self, '_catalog_now', timezone.now())
        return ctx

    def get_queryset(self):
        qs = Resource.objects.all()
        user = self.request.user
        if not user.is_authenticated:
            return Resource.objects.none()
        if getattr(user, 'role', None) == 'superadmin':
            pass
        else:
            qs = qs.filter(is_active=True)
            company = getattr(user, 'company', None)
            if company:
                qs = qs.filter(Q(assigned_company__isnull=True) | Q(assigned_company_id=company.id))
            else:
                qs = qs.filter(assigned_company__isnull=True)

        if self.action == 'list':
            catalog_now = timezone.now()
            self._catalog_now = catalog_now
            qs = qs.prefetch_related(
                Prefetch(
                    'bookings',
                    queryset=Booking.objects.filter(
                        status='confirmed',
                        start_time__lte=catalog_now,
                        end_time__gt=catalog_now,
                    ).order_by('end_time'),
                    to_attr='_active_bookings_prefetch',
                ),
                Prefetch(
                    'blocks',
                    queryset=ResourceBlock.objects.filter(
                        start_time__lte=catalog_now,
                        end_time__gt=catalog_now,
                    ).order_by('end_time'),
                    to_attr='_active_blocks_prefetch',
                ),
            )
            # TimeStampedModel задаёт Meta.ordering = -created_at; без сброса БД может
            # вернуть строки в порядке создания, игнорируя ?ordering=name (QA DEV-71).
            return qs.order_by()
        return qs.order_by('-created_at')

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        facet_params = request.query_params.copy()
        for _key in (
            'equipment',
            'has_projector',
            'has_tv',
            'has_video_conf',
            'available_from',
            'available_to',
        ):
            facet_params.pop(_key, None)
        facet_filter = ResourceFilter(
            data=facet_params,
            queryset=self.get_queryset(),
            request=request,
        )
        facet_mr = facet_filter.qs.filter(resource_type='meeting_room')
        meeting_room_equipment_keys = [
            key for key, field in _EQUIPMENT_KEYS.items()
            if facet_mr.filter(**{field: True}).exists()
        ]

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            response = self.get_paginated_response(serializer.data)
            response.data['meeting_room_equipment_keys'] = meeting_room_equipment_keys
            return response

        serializer = self.get_serializer(queryset, many=True)
        return Response({
            'count': queryset.count(),
            'next': None,
            'previous': None,
            'results': serializer.data,
            'meeting_room_equipment_keys': meeting_room_equipment_keys,
        })

    def get_permissions(self):
        if self.action == 'schedule':
            return [IsCompanyMember()]
        if self.action in (
            'create',
            'update',
            'partial_update',
            'destroy',
            'block',
            'blocks',
            'unblock',
            'bulk_create',
            'activate',
            'deactivate',
        ):
            return [IsSuperAdmin()]
        return [IsAuthenticated()]

    def perform_update(self, serializer):
        was_active = serializer.instance.is_active
        resource = serializer.save()
        if was_active and not resource.is_active:
            self._cancel_future_bookings(resource)

    def destroy(self, request, *args, **kwargs):
        resource = self.get_object()
        now = timezone.now()
        if resource.bookings.filter(
            end_time__gt=now,
            status='confirmed',
        ).exists():
            raise ValidationError(
                {'detail': 'Cannot delete a resource that has future confirmed bookings.'}
            )
        return super().destroy(request, *args, **kwargs)

    def _cancel_future_bookings(self, resource):
        now = timezone.now()
        qs = resource.bookings.filter(end_time__gt=now, status='confirmed')
        admin = self.request.user
        for booking in qs:
            booking.status = 'cancelled'
            booking.cancelled_by = admin
            booking.cancel_reason = 'Resource deactivated'
            booking.save()
            create_notification(
                user=booking.user,
                notification_type='booking_cancelled',
                title=f'Бронирование отменено: {resource.name}',
                message='Ресурс деактивирован администратором.',
            )

    def _cancel_overlapping_bookings_for_block(self, *, resource, block, admin):
        cancellation_reason = block.reason or 'Resource blocked by administrator'
        now = timezone.now()
        overlaps = resource.bookings.filter(
            status='confirmed',
            start_time__lt=block.end_time,
            end_time__gt=block.start_time,
        )
        for booking in overlaps:
            booking.status = 'cancelled'
            booking.cancelled_by = admin
            booking.cancel_reason = cancellation_reason
            booking.save(update_fields=['status', 'cancelled_by', 'cancel_reason', 'updated_at'])
            BookingCancellationAudit.objects.create(
                booking=booking,
                cancelled_by=admin,
                cancel_reason=cancellation_reason,
                cancelled_at=now,
            )
            create_notification(
                user=booking.user,
                notification_type='booking_cancelled',
                title=f'Бронирование отменено: {resource.name}',
                message=cancellation_reason,
            )

    @extend_schema(
        tags=['Resources'],
        summary='Activate a resource (superadmin)',
        request=None,
        responses={
            200: ResourceSerializer,
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Superadmin only'),
            404: OpenApiResponse(description='Resource not found'),
        },
    )
    @action(detail=True, methods=['post'], url_path='activate',
            permission_classes=[IsSuperAdmin])
    def activate(self, request, pk=None):
        resource = self.get_object()
        resource.is_active = True
        resource.save(update_fields=['is_active', 'updated_at'])
        return Response(ResourceSerializer(resource).data)

    @extend_schema(
        tags=['Resources'],
        summary='Deactivate a resource and cancel its future bookings (superadmin)',
        request=None,
        responses={
            200: ResourceSerializer,
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Superadmin only'),
            404: OpenApiResponse(description='Resource not found'),
        },
    )
    @action(detail=True, methods=['post'], url_path='deactivate',
            permission_classes=[IsSuperAdmin])
    def deactivate(self, request, pk=None):
        resource = self.get_object()
        if not resource.is_active:
            return Response(ResourceSerializer(resource).data)
        resource.is_active = False
        resource.save(update_fields=['is_active', 'updated_at'])
        self._cancel_future_bookings(resource)
        return Response(ResourceSerializer(resource).data)

    @extend_schema(
        tags=['Resources'],
        summary='Resource booking schedule for a day or week',
        description=(
            'Returns confirmed bookings for the given resource.\n\n'
            '- `?week=YYYY-MM-DD` — returns all slots for the ISO week (Mon–Sun) that contains the given date. '
            'Takes priority over `?date=` when both are supplied.\n'
            '- `?date=YYYY-MM-DD` — returns slots for that single calendar day.\n'
            '- No params — defaults to today (server local date, `Asia/Almaty`).\n\n'
            'Each slot includes a `status` field:\n'
            '- `"occupied"` — booking is ongoing or in the future with more than '
            '`SOON_AVAILABLE_MINUTES` (15 min) until it ends.\n'
            '- `"soon_available"` — booking is still ongoing but ends within 15 minutes.\n\n'
            'Datetimes are returned with the `+05:00` timezone offset, not UTC `Z`.\n\n'
            '**Access:** `company_admin` or `employee` (company member) or `superadmin`. '
            'Guests are blocked.'
        ),
        parameters=[
            OpenApiParameter(
                name='week',
                type=OpenApiTypes.DATE,
                location=OpenApiParameter.QUERY,
                required=False,
                description='YYYY-MM-DD — returns slots for the full ISO week (Mon–Sun) containing this date. '
                            'Takes priority over ?date= when both are provided.',
            ),
            OpenApiParameter(
                name='date',
                type=OpenApiTypes.DATE,
                location=OpenApiParameter.QUERY,
                required=False,
                description='YYYY-MM-DD — returns slots for this single calendar day. Defaults to today.',
            ),
        ],
        responses={
            200: ResourceDayScheduleSlotSerializer(many=True),
            400: OpenApiResponse(
                description='Invalid date or week format.',
                examples=[
                    OpenApiExample(
                        name='Invalid date',
                        value={
                            'error': True,
                            'status_code': 400,
                            'detail': 'Invalid date format. Use YYYY-MM-DD.',
                        },
                        response_only=True,
                        status_codes=['400'],
                    ),
                    OpenApiExample(
                        name='Invalid week',
                        value={
                            'error': True,
                            'status_code': 400,
                            'detail': 'Invalid week format. Use YYYY-MM-DD.',
                        },
                        response_only=True,
                        status_codes=['400'],
                    ),
                ],
            ),
            401: OpenApiResponse(description='Not authenticated.', examples=[_AUTH_401_EXAMPLE]),
            403: OpenApiResponse(description='Company members only.', examples=[_FORBIDDEN_403_EXAMPLE]),
            404: OpenApiResponse(description='Resource not found.'),
        },
    )
    @action(detail=True, methods=['get'], url_path='schedule')
    def schedule(self, request, pk=None):
        resource = self.get_object()
        week_param = request.query_params.get('week')
        date_param = request.query_params.get('date')
        local_tz = timezone.get_current_timezone()
        now = timezone.now()

        if week_param:
            try:
                anchor = date.fromisoformat(week_param)
            except ValueError:
                return Response(
                    {'detail': 'Invalid week format. Use YYYY-MM-DD.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            range_start, range_end = week_range_for_date(anchor)
            bookings = Booking.objects.filter(
                resource=resource,
                status='confirmed',
                start_time__gte=range_start,
                start_time__lt=range_end,
            ).order_by('start_time')
        else:
            if date_param:
                try:
                    target_date = date.fromisoformat(date_param)
                except ValueError:
                    return Response(
                        {'detail': 'Invalid date format. Use YYYY-MM-DD.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            else:
                target_date = now.astimezone(local_tz).date()

            # Filter by local-date range to avoid UTC-boundary mismatches for early-morning bookings.
            local_start = timezone.make_aware(datetime.combine(target_date, time.min), local_tz)
            local_end = timezone.make_aware(datetime.combine(target_date, time.max), local_tz) + timedelta(seconds=1)
            bookings = Booking.objects.filter(
                resource=resource,
                status='confirmed',
                start_time__gte=local_start,
                start_time__lt=local_end,
            ).order_by('start_time')

        result = []
        for booking in bookings:
            start_local = booking.start_time.astimezone(local_tz)
            end_local = booking.end_time.astimezone(local_tz)
            status_val = get_schedule_status(booking, now)
            result.append({
                'booking_id': booking.pk,
                'start': start_local.isoformat(),
                'end': end_local.isoformat(),
                'status': status_val,
            })

        return Response(result)

    @extend_schema(
        tags=['Resources'],
        summary='Bulk-create resources from a template (superadmin only)',
        description=(
            'Creates `count` resources by cloning a `template` payload and '
            'appending a numeric suffix to each name: `{name_prefix} 1`, `{name_prefix} 2`, …\n\n'
            'The `template` object is validated with the same rules as a single resource create. '
            'The entire operation runs in a single database transaction.\n\n'
            '**Access:** `superadmin` only.'
        ),
        request=ResourceBulkCreateSerializer,
        examples=[
            OpenApiExample(
                name='Bulk create desks',
                summary='Create 5 desks on floor 2',
                value={
                    'template': {
                        'type': 'desk',
                        'floor': 2,
                        'zone': 'Open Space',
                        'capacity': 1,
                    },
                    'count': 5,
                    'name_prefix': 'Desk A',
                },
                request_only=True,
            ),
            OpenApiExample(
                name='Bulk create capsules',
                summary='Create 3 quiet capsules on floor 1',
                value={
                    'template': {
                        'type': 'capsule',
                        'floor': 1,
                        'capsule_zone': 'quiet',
                    },
                    'count': 3,
                    'name_prefix': 'Capsule Q',
                },
                request_only=True,
            ),
        ],
        responses={
            201: OpenApiResponse(
                response=ResourceSerializer(many=True),
                description='List of created resources.',
            ),
            400: OpenApiResponse(
                description='Validation error in template or parameters.',
                examples=[_RESOURCE_400_EXAMPLE],
            ),
            401: OpenApiResponse(description='Not authenticated.', examples=[_AUTH_401_EXAMPLE]),
            403: OpenApiResponse(description='Superadmin only.', examples=[_FORBIDDEN_403_EXAMPLE]),
        },
    )
    @action(detail=False, methods=['post'], url_path='bulk-create')
    def bulk_create(self, request):
        bulk_serializer = ResourceBulkCreateSerializer(
            data=request.data,
            context={'request': request},
        )
        bulk_serializer.is_valid(raise_exception=True)

        template_data = bulk_serializer.validated_data['template_data']
        count = bulk_serializer.validated_data['count']
        name_prefix = bulk_serializer.validated_data['name_prefix']

        created_resources = []
        writer_serializer = ResourceSerializer(context={'request': request})
        with transaction.atomic():
            for idx in range(1, count + 1):
                resource_data = dict(template_data)
                resource_data['name'] = f'{name_prefix} {idx}'
                created_resources.append(writer_serializer.create(resource_data))

        return Response(
            ResourceSerializer(created_resources, many=True).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        tags=['Resources'],
        summary='Block a resource for a time interval (superadmin only)',
        description=(
            'Creates an admin block that prevents bookings during the specified interval. '
            'Typical use cases: maintenance, internal events, cleaning.\n\n'
            'Overlapping confirmed bookings are automatically cancelled and affected users '
            'receive in-app notifications with the block reason.\n\n'
            '**Access:** `superadmin` only.'
        ),
        request=ResourceBlockSerializer,
        examples=[
            OpenApiExample(
                name='Maintenance block',
                summary='Block Desk A-01 for maintenance',
                value={
                    'start_time': '2025-04-22T08:00:00+06:00',
                    'end_time': '2025-04-22T18:00:00+06:00',
                    'reason': 'Plumbing maintenance on floor 2',
                },
                request_only=True,
            ),
        ],
        responses={
            201: OpenApiResponse(
                response=ResourceBlockSerializer,
                description='Block created.',
                examples=[
                    OpenApiExample(
                        name='Block created',
                        value={
                            'id': 12,
                            'resource': 42,
                            'blocked_by': 1,
                            'start_time': '2025-04-22T08:00:00+06:00',
                            'end_time': '2025-04-22T18:00:00+06:00',
                            'reason': 'Plumbing maintenance on floor 2',
                            'created_at': '2025-04-15T10:00:00+06:00',
                            'updated_at': '2025-04-15T10:00:00+06:00',
                        },
                        response_only=True,
                        status_codes=['201'],
                    ),
                ],
            ),
            400: OpenApiResponse(description='Validation error.', examples=[_RESOURCE_400_EXAMPLE]),
            401: OpenApiResponse(description='Not authenticated.', examples=[_AUTH_401_EXAMPLE]),
            403: OpenApiResponse(description='Superadmin only.', examples=[_FORBIDDEN_403_EXAMPLE]),
            404: OpenApiResponse(description='Resource not found.'),
        },
    )
    @action(detail=True, methods=['post'], url_path='block')
    def block(self, request, pk=None):
        resource = self.get_object()
        payload = dict(request.data)
        payload['resource'] = resource.id
        serializer = ResourceBlockSerializer(data=payload)
        serializer.is_valid(raise_exception=True)
        block = serializer.save(resource=resource, blocked_by=request.user)
        self._cancel_overlapping_bookings_for_block(
            resource=resource,
            block=block,
            admin=request.user,
        )
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(
        tags=['Resources'],
        summary='List resource blocks (superadmin only)',
        responses={200: ResourceBlockSerializer(many=True)},
    )
    @action(detail=True, methods=['get'], url_path='blocks')
    def blocks(self, request, pk=None):
        resource = self.get_object()
        queryset = resource.blocks.order_by('start_time', 'id')
        return Response(ResourceBlockSerializer(queryset, many=True).data)

    @extend_schema(
        tags=['Resources'],
        summary='Remove resource block (superadmin only)',
        responses={204: OpenApiResponse(description='Block removed.')},
    )
    @action(detail=True, methods=['delete'], url_path=r'blocks/(?P<block_id>[^/.]+)')
    def unblock(self, request, pk=None, block_id=None):
        resource = self.get_object()
        block = resource.blocks.filter(pk=block_id).first()
        if block is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        block.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# BookingViewSet
# ---------------------------------------------------------------------------

@extend_schema_view(
    list=extend_schema(
        tags=['Bookings'],
        summary='List bookings',
        description=(
            'Returns a paginated list of bookings scoped to the current user\'s company.\n\n'
            '**Access:** `company_admin`, `employee`, or `superadmin`. Guests are blocked.\n\n'
            '`superadmin` sees all bookings across all companies. '
            'Company admins and employees see only their own company\'s bookings.\n\n'
            '**Filters:**\n'
            '- `status` — `confirmed`, `cancelled`, `completed`, `no_show`\n'
            '- `resource_type` — `desk`, `meeting_room`, `parking`, `capsule`\n'
            '- `resource` — resource PK\n'
            '- `user` — user PK\n'
            '- `company` — company PK (superadmin only in practice)\n'
            '- `date_from` — bookings starting on or after this datetime (ISO 8601)\n'
            '- `date_to` — bookings ending on or before this datetime (ISO 8601)\n\n'
            '**Ordering:** `start_time`, `created_at` (prefix with `-` for descending).'
        ),
        parameters=[
            OpenApiParameter(
                name='status',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                enum=['confirmed', 'cancelled', 'completed', 'no_show'],
                description='Filter by booking status.',
            ),
            OpenApiParameter(
                name='resource_type',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                enum=['desk', 'meeting_room', 'parking', 'capsule'],
                description='Filter by the type of the booked resource.',
            ),
            OpenApiParameter(
                name='resource',
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Filter by resource PK.',
            ),
            OpenApiParameter(
                name='user',
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Filter by user PK.',
            ),
            OpenApiParameter(
                name='company',
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Filter by company PK.',
            ),
            OpenApiParameter(
                name='date_from',
                type=OpenApiTypes.DATETIME,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Return bookings whose start_time >= this value (ISO 8601).',
            ),
            OpenApiParameter(
                name='date_to',
                type=OpenApiTypes.DATETIME,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Return bookings whose end_time <= this value (ISO 8601).',
            ),
            OpenApiParameter(
                name='ordering',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Sort: `start_time`, `-start_time`, `created_at`, `-created_at`.',
            ),
            OpenApiParameter(
                name='page',
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Page number.',
            ),
            OpenApiParameter(
                name='page_size',
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Results per page (default 20, max 100).',
            ),
        ],
        responses={
            200: BookingSerializer(many=True),
            401: OpenApiResponse(description='Not authenticated.', examples=[_AUTH_401_EXAMPLE]),
            403: OpenApiResponse(description='Company members only.', examples=[_FORBIDDEN_403_EXAMPLE]),
        },
    ),
    retrieve=extend_schema(
        tags=['Bookings'],
        summary='Get booking details',
        description=(
            'Returns the full booking object including participant emails.\n\n'
            '**Access:** company member or superadmin. '
            'A regular employee can only see bookings that belong to their company '
            '(enforced by `CompanyIsolationMixin`).'
        ),
        responses={
            200: BookingSerializer,
            401: OpenApiResponse(description='Not authenticated.', examples=[_AUTH_401_EXAMPLE]),
            403: OpenApiResponse(description='Company members only.', examples=[_FORBIDDEN_403_EXAMPLE]),
            404: OpenApiResponse(description='Booking not found.'),
        },
    ),
    create=extend_schema(
        tags=['Bookings'],
        summary='Create a booking',
        description=(
            'Creates a new booking with conflict detection. '
            'All validations run inside a **serialized database transaction** '
            '(`SELECT FOR UPDATE` on the resource row) to prevent race conditions.\n\n'
            '**Access:** `company_admin` or `employee` with a verified email, or `superadmin`.\n\n'
            '---\n\n'
            '### Type-specific rules\n\n'
            '**Desk** (`type=desk`)\n'
            '- Must start within **14 days** from now.\n'
            '- Must fall within a single calendar day.\n'
            '- Must be within the resource availability hours (default 08:00–22:00).\n\n'
            '**Meeting Room** (`type=meeting_room`)\n'
            '- Duration: **30 minutes minimum, 4 hours maximum**.\n'
            '- Must fall within a single calendar day.\n'
            '- Pass `participant_ids` (list of user PKs) to add colleagues as participants; '
            'they receive in-app notifications.\n\n'
            '**Parking** (`type=parking`)\n'
            '- **Whole-day only**: `start_time` must be `00:00`, '
            '`end_time` must be `23:59` (same day) or `00:00` (next day).\n'
            '- Must start within **7 days** from now.\n\n'
            '**Capsule** (`type=capsule`)\n'
            '- Duration: **1 hour minimum, 8 hours maximum**.\n'
            '- Must fall within a single calendar day.\n\n'
            '---\n\n'
            '### Conflict rules\n'
            '- Returns **409** if another confirmed booking or an admin block overlaps '
            'the requested interval on the same resource.\n\n'
            '### Active booking limit\n'
            '- Users cannot exceed 5 simultaneous active (confirmed, future-ending) bookings '
            '(configurable via `MAX_ACTIVE_BOOKINGS_PER_USER` in settings).'
        ),
        request=BookingCreateSerializer,
        examples=_BOOKING_REQUEST_EXAMPLES + _BOOKING_400_EXAMPLES + [
            _BOOKING_201_EXAMPLE,
            _BOOKING_409_EXAMPLE,
            _AUTH_401_EXAMPLE,
            _FORBIDDEN_403_EXAMPLE,
        ],
        responses={
            201: OpenApiResponse(
                response=BookingSerializer,
                description='Booking created successfully.',
                examples=[_BOOKING_201_EXAMPLE],
            ),
            400: OpenApiResponse(
                description='Validation error — see examples for all possible messages.',
                examples=_BOOKING_400_EXAMPLES,
            ),
            401: OpenApiResponse(description='Not authenticated.', examples=[_AUTH_401_EXAMPLE]),
            403: OpenApiResponse(
                description='Company member with verified email required.',
                examples=[_FORBIDDEN_403_EXAMPLE],
            ),
            409: OpenApiResponse(
                description='Time slot conflict — another booking or admin block overlaps.',
                examples=[_BOOKING_409_EXAMPLE],
            ),
        },
    ),
    partial_update=extend_schema(
        tags=['Bookings'],
        summary='Update booking (partial)',
        description=(
            'PATCH a booking. When changing the reservation window, send both `start_time` and '
            '`end_time` (ISO 8601 with timezone); the same overlap rules apply as for creation '
            '(409 if the slot conflicts). Other writable fields use the standard serializer rules.'
        ),
        request=BookingSerializer,
        responses={
            200: BookingSerializer,
            400: OpenApiResponse(description='Validation error'),
            403: OpenApiResponse(description='Forbidden'),
            404: OpenApiResponse(description='Booking not found'),
            409: OpenApiResponse(description='Time slot conflict with another booking or block'),
            401: OpenApiResponse(description='Not authenticated'),
        },
    ),
)
class BookingViewSet(CompanyIsolationMixin, SetCompanyOnCreateMixin, viewsets.ModelViewSet):
    serializer_class = BookingSerializer
    permission_classes = [IsCompanyMember, IsEmailVerifiedOrSuperAdmin]
    queryset = Booking.objects.all()
    filterset_class = BookingFilter
    ordering_fields = ['start_time', 'created_at']
    http_method_names = ['get', 'post', 'patch', 'delete']

    def get_serializer_class(self):
        if self.action == 'create':
            return BookingCreateSerializer
        return BookingSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        booking = serializer.save()
        output = BookingSerializer(booking, context=self.get_serializer_context())
        headers = self.get_success_headers(output.data)
        return Response(output.data, status=status.HTTP_201_CREATED, headers=headers)

    def _create_change_audit(self, *, booking, action, payload):
        BookingChangeAudit.objects.create(
            booking=booking,
            changed_by=self.request.user,
            action=action,
            payload=payload,
            changed_at=timezone.now(),
        )

    def _ensure_participants_manage_permission(self, user):
        if user.is_superadmin() or user.is_company_admin():
            return
        raise PermissionDenied('Only company admins can manage participants.')

    def partial_update(self, request, *args, **kwargs):
        # AC DEV-77: when updating reservation times, reuse creation conflict-control.
        updates_time = 'start_time' in request.data or 'end_time' in request.data
        if not updates_time:
            return super().partial_update(request, *args, **kwargs)
        if 'start_time' not in request.data or 'end_time' not in request.data:
            raise ValidationError({'detail': 'Both start_time and end_time are required for time updates.'})

        with transaction.atomic():
            current = (
                self.get_queryset()
                .select_related('resource')
                .select_for_update()
                .filter(pk=kwargs['pk'])
                .first()
            )
            if current is None:
                raise NotFound('Not found.')
            serializer = BookingSerializer(
                current,
                data=request.data,
                partial=True,
                context=self.get_serializer_context(),
            )
            serializer.is_valid(raise_exception=True)

            new_start = serializer.validated_data.get('start_time', current.start_time)
            new_end = serializer.validated_data.get('end_time', current.end_time)
            if new_start >= new_end:
                raise ValidationError({'detail': 'start_time must be before end_time'})

            validator = BookingCreateSerializer(
                data=request.data,
                partial=True,
                context={'request': request},
            )
            validator._check_timezone_aware('start_time')
            validator._check_timezone_aware('end_time')
            resource = Resource.objects.select_for_update().get(pk=current.resource_id)
            validator._validate_availability_window(
                resource=resource,
                start_time=new_start,
                end_time=new_end,
            )
            validator._validate_type_specific_rules(
                resource=resource,
                start_time=new_start,
                end_time=new_end,
            )
            validator._ensure_no_conflicts(
                resource=resource,
                start_time=new_start,
                end_time=new_end,
                exclude_booking_id=current.id,
            )

            booking = serializer.save()
            self._create_change_audit(
                booking=booking,
                action=BookingChangeAudit.ACTION_TIME_UPDATED,
                payload={
                    'start_time': booking.start_time.isoformat(),
                    'end_time': booking.end_time.isoformat(),
                },
            )

        return Response(BookingSerializer(booking, context=self.get_serializer_context()).data)

    def get_queryset(self):
        return super().get_queryset().order_by('-start_time', '-id')

    @extend_schema(
        tags=['Bookings'],
        summary='Cancel a booking',
        description=(
            'Cancels a booking by setting its status to `cancelled`. '
            'An optional `reason` field is stored on the booking record.\n\n'
            '**Access:** any authenticated company member or superadmin. '
            'Object-level ownership is **not** enforced here — any company member can cancel '
            'any booking within their company (admin use case). '
            'The cancelling user is recorded in `cancelled_by`.\n\n'
            '**Note:** minimum-notice cancellation enforcement (`min_cancel_minutes`) '
            'is not yet implemented (TODO).'
        ),
        request=inline_serializer(
            name='CancelBookingRequest',
            fields={'reason': fields.CharField(required=False, default='', help_text='Optional cancellation reason.')},
        ),
        examples=[
            OpenApiExample(
                name='Cancel with reason',
                value={'reason': 'Meeting rescheduled to next week'},
                request_only=True,
            ),
            OpenApiExample(
                name='Cancel without reason',
                value={},
                request_only=True,
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=BookingSerializer,
                description='Booking cancelled.',
                examples=[
                    OpenApiExample(
                        name='Cancelled booking',
                        value={
                            'id': 101,
                            'resource': 42,
                            'resource_name': 'Desk A-01',
                            'user': 5,
                            'user_name': 'Aibek Seitkali',
                            'company': 2,
                            'start_time': '2025-04-20T09:00:00+06:00',
                            'end_time': '2025-04-20T18:00:00+06:00',
                            'status': 'cancelled',
                            'description': 'Working from the office today',
                            'cancelled_by': 3,
                            'cancel_reason': 'Meeting rescheduled to next week',
                            'participants': [],
                            'created_at': '2025-04-15T10:00:00+06:00',
                            'updated_at': '2025-04-15T12:00:00+06:00',
                        },
                        response_only=True,
                        status_codes=['200'],
                    ),
                ],
            ),
            401: OpenApiResponse(description='Not authenticated.', examples=[_AUTH_401_EXAMPLE]),
            403: OpenApiResponse(description='Company members only.', examples=[_FORBIDDEN_403_EXAMPLE]),
            404: OpenApiResponse(description='Booking not found.'),
        },
    )
    @action(detail=True, methods=['post'], url_path='cancel',
            permission_classes=[IsOwnerOrAdmin])
    def cancel(self, request, pk=None):
        booking = self.get_object()
        if booking.status == 'cancelled':
            raise ValidationError({'detail': 'Booking is already cancelled.'})

        now = timezone.now()
        if booking.start_time <= now:
            raise ValidationError({'detail': 'Cannot cancel a booking that has already started.'})

        min_cancel_minutes = booking.resource.min_cancel_minutes
        if booking.start_time - now < timedelta(minutes=min_cancel_minutes):
            raise ValidationError(
                {
                    'detail': (
                        f'Booking can only be cancelled at least '
                        f'{min_cancel_minutes} minutes before start.'
                    )
                }
            )

        booking.status = 'cancelled'
        booking.cancelled_by = request.user
        booking.cancel_reason = request.data.get('reason', '')
        booking.save()
        BookingCancellationAudit.objects.create(
            booking=booking,
            cancelled_by=request.user,
            cancel_reason=booking.cancel_reason,
            cancelled_at=now,
        )

        reason_text = (booking.cancel_reason or '').strip()
        create_notification(
            user=booking.user,
            notification_type='booking_cancelled',
            title=f'Бронирование отменено: {booking.resource.name}',
            message=reason_text or 'Бронирование отменено.',
            link=f'/bookings/{booking.id}',
        )

        return Response(BookingSerializer(booking).data)

    @extend_schema(
        tags=['Bookings'],
        summary='Admin cancel booking',
        request=inline_serializer(
            name='AdminCancelBookingRequest',
            fields={
                'reason': fields.CharField(required=True, allow_blank=False),
            },
        ),
        responses={
            200: BookingSerializer,
            400: OpenApiResponse(description='reason is required'),
            403: OpenApiResponse(description='Company admin or superadmin only'),
            404: OpenApiResponse(description='Booking not found'),
        },
    )
    @action(detail=True, methods=['post'], url_path='admin-cancel',
            permission_classes=[IsCompanyAdmin])
    def admin_cancel(self, request, pk=None):
        user = request.user
        booking = self.get_object()
        reason = str(request.data.get('reason', '')).strip()
        if not reason:
            raise ValidationError({'reason': 'This field is required.'})

        now = timezone.now()
        booking.status = 'cancelled'
        booking.cancelled_by = user
        booking.cancel_reason = reason
        booking.save(update_fields=['status', 'cancelled_by', 'cancel_reason', 'updated_at'])
        BookingCancellationAudit.objects.create(
            booking=booking,
            cancelled_by=user,
            cancel_reason=reason,
            cancelled_at=now,
        )

        create_notification(
            user=booking.user,
            notification_type='booking_cancelled',
            title=f'Бронирование отменено администратором: {booking.resource.name}',
            message=reason,
            link=f'/bookings/{booking.id}',
        )
        return Response(BookingSerializer(booking, context=self.get_serializer_context()).data)

    @extend_schema(
        tags=['Bookings'],
        summary='Add booking participants',
        request=inline_serializer(
            name='AddParticipantsRequest',
            fields={
                'user_ids': fields.ListField(
                    child=fields.IntegerField(min_value=1),
                    required=True,
                ),
            },
        ),
        responses={
            200: BookingSerializer,
            400: OpenApiResponse(description='Validation error'),
            404: OpenApiResponse(description='Booking not found'),
        },
    )
    @action(detail=True, methods=['post'], url_path='participants')
    def add_participants(self, request, pk=None):
        self._ensure_participants_manage_permission(request.user)
        booking = self.get_object()
        if booking.resource.resource_type != 'meeting_room':
            raise ValidationError({'detail': 'Participants can only be managed for meeting_room bookings.'})

        user_ids = request.data.get('user_ids')
        if not isinstance(user_ids, list) or not user_ids:
            raise ValidationError({'user_ids': 'Provide a non-empty list of user ids.'})
        if not all(isinstance(uid, int) for uid in user_ids):
            raise ValidationError({'user_ids': 'All user ids must be integers.'})

        users = list(
            User.objects.filter(
                id__in=user_ids,
                company_id=booking.company_id,
            )
        )
        found_ids = {u.id for u in users}
        missing_ids = sorted(set(user_ids) - found_ids)
        if missing_ids:
            raise ValidationError({'user_ids': f'Users not found in company: {missing_ids}'})

        added_ids = []
        with transaction.atomic():
            for user in users:
                _, created = BookingParticipant.objects.get_or_create(booking=booking, user=user)
                if not created:
                    continue
                added_ids.append(user.id)
                create_notification(
                    user=user,
                    notification_type='booking_confirmed',
                    title=f'You were added to meeting: {booking.resource.name}',
                    message='Check your bookings for updated participants.',
                )
            self._create_change_audit(
                booking=booking,
                action=BookingChangeAudit.ACTION_PARTICIPANTS_ADDED,
                payload={'user_ids': added_ids},
            )
        from apps.notifications.tasks import send_notification_email
        for user in users:
            if user.id in added_ids:
                send_notification_email.delay(
                    user.id,
                    'booking_confirmed',
                    {
                        'subject': f'Вы добавлены на встречу: {booking.resource.name}',
                        'resource_name': booking.resource.name,
                        'start_time': booking.start_time.strftime('%d.%m.%Y %H:%M'),
                        'end_time': booking.end_time.strftime('%d.%m.%Y %H:%M'),
                        'action_url': f'/bookings/{booking.id}',
                    },
                )
        booking.refresh_from_db()
        return Response(BookingSerializer(booking, context=self.get_serializer_context()).data)

    @extend_schema(
        tags=['Bookings'],
        summary='Remove booking participant',
        responses={
            204: OpenApiResponse(description='Participant removed'),
            400: OpenApiResponse(description='Validation error'),
            404: OpenApiResponse(description='Booking or participant not found'),
        },
    )
    @action(detail=True, methods=['delete'], url_path=r'participants/(?P<user_id>[^/.]+)')
    def remove_participant(self, request, pk=None, user_id=None):
        self._ensure_participants_manage_permission(request.user)
        booking = self.get_object()
        if booking.resource.resource_type != 'meeting_room':
            raise ValidationError({'detail': 'Participants can only be managed for meeting_room bookings.'})

        try:
            user_id_int = int(user_id)
        except (TypeError, ValueError) as exc:
            raise ValidationError({'user_id': 'User id must be an integer.'}) from exc

        deleted, _ = BookingParticipant.objects.filter(booking=booking, user_id=user_id_int).delete()
        if not deleted:
            raise ValidationError({'detail': 'Participant is not attached to this booking.'})

        self._create_change_audit(
            booking=booking,
            action=BookingChangeAudit.ACTION_PARTICIPANT_REMOVED,
            payload={'user_id': user_id_int},
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        tags=['Bookings'],
        summary='My bookings',
        description=(
            'Returns only bookings belonging to the authenticated user. '
            'The `user` query parameter is not supported on this endpoint; '
            'use /bookings/reservations/ to filter by user.'
        ),
        responses={
            200: BookingSerializer(many=True),
            400: OpenApiResponse(description="'user' filter is not supported on this endpoint"),
            401: OpenApiResponse(description='Not authenticated.', examples=[_AUTH_401_EXAMPLE]),
        },
    )
    @action(detail=False, methods=['get'], url_path='my')
    def my_bookings(self, request):
        if 'user' in request.query_params:
            raise ValidationError(
                "The 'user' filter is not supported on this endpoint. "
                "Use /bookings/reservations/ to filter by user."
            )
        qs = (
            Booking.objects
            .filter(Q(user=request.user) | Q(participants__user=request.user))
            .distinct()
            .select_related('resource')
        )

        status_filter = request.query_params.get('status')
        resource_type = request.query_params.get('resource_type')
        date_from_raw = request.query_params.get('date_from')
        date_to_raw = request.query_params.get('date_to')
        now = timezone.now()

        if status_filter:
            if status_filter == 'upcoming':
                qs = qs.filter(status='confirmed', start_time__gt=now)
            elif status_filter == 'past':
                qs = qs.exclude(status='cancelled').filter(start_time__lt=now)
            elif status_filter == 'cancelled':
                qs = qs.filter(status='cancelled')
            else:
                raise ValidationError(
                    {
                        'status': (
                            "Unsupported status filter. "
                            "Use one of: upcoming, past, cancelled."
                        )
                    }
                )

        if resource_type:
            qs = qs.filter(resource__resource_type=resource_type)

        if date_from_raw:
            date_from = parse_datetime(date_from_raw)
            if date_from is None:
                raise ValidationError({'date_from': 'Invalid datetime format.'})
            qs = qs.filter(start_time__gte=date_from)

        if date_to_raw:
            date_to = parse_datetime(date_to_raw)
            if date_to is None:
                raise ValidationError({'date_to': 'Invalid datetime format.'})
            qs = qs.filter(start_time__lte=date_to)

        if status_filter == 'upcoming':
            qs = qs.order_by('start_time')
        else:
            qs = qs.order_by('-start_time')

        page = self.paginate_queryset(qs)
        if page is not None:
            return self.get_paginated_response(BookingSerializer(page, many=True).data)
        return Response(BookingSerializer(qs, many=True).data)

    @extend_schema(
        tags=['Bookings'],
        summary='Check in to a booking',
        description=(
            'Confirms presence at the booked resource. '
            'Sets `checked_in_at` to the current timestamp, preventing the booking from '
            'being marked as `no_show` by the periodic task.\n\n'
            '**Access:** the booking owner, a company_admin of the same company, or superadmin.\n\n'
            '**Validation:**\n'
            '- Booking must be in `confirmed` status.\n'
            '- Booking must not have already been checked in (`checked_in_at` is null).'
        ),
        request=None,
        responses={
            200: OpenApiResponse(
                response=BookingSerializer,
                description='Check-in recorded.',
                examples=[
                    OpenApiExample(
                        name='Checked in',
                        value={
                            'id': 101,
                            'status': 'confirmed',
                            'checked_in_at': '2025-04-20T09:05:00+06:00',
                        },
                        response_only=True,
                        status_codes=['200'],
                    ),
                ],
            ),
            400: OpenApiResponse(
                description='Booking already checked in or not in a confirmable state.',
                examples=[
                    OpenApiExample(
                        name='Already checked in',
                        value={
                            'error': True,
                            'status_code': 400,
                            'detail': {'detail': 'Booking has already been checked in.'},
                        },
                        response_only=True,
                        status_codes=['400'],
                    ),
                    OpenApiExample(
                        name='Wrong status',
                        value={
                            'error': True,
                            'status_code': 400,
                            'detail': {'detail': 'Check-in is only allowed for confirmed bookings.'},
                        },
                        response_only=True,
                        status_codes=['400'],
                    ),
                ],
            ),
            401: OpenApiResponse(description='Not authenticated.', examples=[_AUTH_401_EXAMPLE]),
            403: OpenApiResponse(description='Not allowed.', examples=[_FORBIDDEN_403_EXAMPLE]),
            404: OpenApiResponse(description='Booking not found.'),
        },
    )
    @action(detail=True, methods=['post'], url_path='check-in',
            permission_classes=[IsOwnerOrSuperAdmin])
    def check_in(self, request, pk=None):
        with transaction.atomic():
            try:
                booking = (
                    self.get_queryset()
                    .select_for_update()
                    .get(pk=self.kwargs['pk'])
                )
            except Booking.DoesNotExist:
                raise Http404
            self.check_object_permissions(request, booking)

            now = timezone.now()
            if now < booking.start_time:
                raise ValidationError({'detail': 'Check-in is not allowed before the booking start time.'})

            if booking.status != 'confirmed':
                raise ValidationError({'detail': 'Check-in is only allowed for confirmed bookings.'})

            if booking.checked_in_at is not None:
                raise ValidationError({'detail': 'Booking has already been checked in.'})

            booking.checked_in_at = now
            booking.save(update_fields=['checked_in_at'])
        return Response(BookingSerializer(booking, context=self.get_serializer_context()).data)

    @extend_schema(
        tags=['Bookings'],
        summary='Manually trigger auto-complete bookings (superadmin)',
        description=(
            'Runs the auto_complete_bookings Celery task synchronously. '
            'Marks all confirmed bookings whose end_time is in the past as completed. '
            'Superadmin only.'
        ),
        request=None,
        responses={
            200: inline_serializer(
                name='AutoCompleteResponse',
                fields={'completed': fields.IntegerField()},
            ),
            403: OpenApiResponse(description='Superadmin only'),
        },
    )
    @action(detail=False, methods=['post'], url_path='run-auto-complete',
            permission_classes=[IsSuperAdmin])
    def run_auto_complete(self, request):
        from .tasks import auto_complete_bookings
        count = auto_complete_bookings()
        return Response({'completed': count})

    @extend_schema(
        tags=['Bookings'],
        summary='Manually trigger booking reminders (superadmin)',
        description=(
            'Runs the send_booking_reminders Celery task synchronously. '
            'Sends reminders for bookings that are starting within the configured reminder window. '
            'Superadmin only.'
        ),
        request=None,
        responses={
            200: inline_serializer(
                name='RemindersResponse',
                fields={'reminders_sent': fields.IntegerField()},
            ),
            403: OpenApiResponse(description='Superadmin only'),
        },
    )
    @action(detail=False, methods=['post'], url_path='run-reminders',
            permission_classes=[IsSuperAdmin])
    def run_reminders(self, request):
        from .tasks import send_booking_reminders
        count = send_booking_reminders()
        return Response({'reminders_sent': count})

    @extend_schema(
        tags=['Bookings'],
        summary='Manually trigger no-show detection (superadmin)',
        description=(
            'Runs the mark_no_show_bookings Celery task synchronously. '
            'Marks meeting_room bookings as no_show when start_time is more than '
            'NO_SHOW_MINUTES in the past and no check-in was recorded. '
            'Superadmin only.'
        ),
        request=None,
        responses={
            200: inline_serializer(
                name='NoShowResponse',
                fields={'no_show_marked': fields.IntegerField()},
            ),
            403: OpenApiResponse(description='Superadmin only'),
        },
    )
    @action(detail=False, methods=['post'], url_path='run-no-show',
            permission_classes=[IsSuperAdmin])
    def run_no_show(self, request):
        from .tasks import mark_no_show_bookings
        count = mark_no_show_bookings()
        return Response({'no_show_marked': count})


# ---------------------------------------------------------------------------
# RecurringBookingViewSet
# ---------------------------------------------------------------------------

@extend_schema_view(
    list=extend_schema(
        tags=['Bookings'],
        summary='List recurring booking series',
        description=(
            'Returns recurring booking series **for the authenticated user only** '
            '(company_admin / employee; guests are blocked by permissions).\n\n'
            '**Access:** company member (non-guest) or superadmin.\n\n'
            'Each row is a series template with `valid_from` / `valid_until` window; '
            'individual `Booking` rows reference it via `recurring_booking_id`.'
        ),
        responses={
            200: RecurringBookingSerializer(many=True),
            401: OpenApiResponse(description='Not authenticated.', examples=[_AUTH_401_EXAMPLE]),
            403: OpenApiResponse(description='Company members only.', examples=[_FORBIDDEN_403_EXAMPLE]),
        },
    ),
    retrieve=extend_schema(
        tags=['Bookings'],
        summary='Get a recurring booking template',
        description='Returns a single recurring booking template by PK.',
        responses={
            200: RecurringBookingSerializer,
            401: OpenApiResponse(description='Not authenticated.', examples=[_AUTH_401_EXAMPLE]),
            403: OpenApiResponse(description='Company members only.', examples=[_FORBIDDEN_403_EXAMPLE]),
            404: OpenApiResponse(description='Not found.'),
        },
    ),
    create=extend_schema(
        tags=['Bookings'],
        summary='Create a recurring booking series',
        description=(
            'Creates a recurring booking series and **materialises `Booking` rows** for every '
            'matching weekday from today (`valid_from`) through `repeat_until` (stored as `valid_until`).\n\n'
            'Overlaps with existing confirmed bookings or resource blocks are skipped; those local '
            'dates are returned in `skipped_dates`.\n\n'
            'The `user` and `company` fields are set from the request user.\n\n'
            '**Access:** company_admin / employee (and superadmin); **guest → 403**.\n\n'
            '**Request body:**\n'
            '- `resource_id` — resource primary key\n'
            '- `day_of_week` — 0=Monday … 6=Sunday\n'
            '- `start_time` / `end_time` — local time of day (HH:MM), within resource availability\n'
            '- `repeat_until` — inclusive end **date** for generated occurrences\n\n'
            'A weekly Celery job extends active series further; PATCH still uses the template serializer.'
        ),
        request=RecurringBookingCreateSerializer,
        examples=[
            OpenApiExample(
                name='Weekly Monday standup',
                summary='Reserve Boardroom Alpha every Monday 10:00–11:00 until a date',
                value={
                    'resource_id': 7,
                    'day_of_week': 0,
                    'start_time': '10:00',
                    'end_time': '11:00',
                    'repeat_until': '2025-12-31',
                },
                request_only=True,
            ),
        ],
        responses={
            201: OpenApiResponse(
                response=_RecurringBookingCreatedSchema,
                description='Series created; response includes `skipped_dates`.',
            ),
            400: OpenApiResponse(description='Validation error.', examples=[_RESOURCE_400_EXAMPLE]),
            401: OpenApiResponse(description='Not authenticated.', examples=[_AUTH_401_EXAMPLE]),
            403: OpenApiResponse(description='Company members only.', examples=[_FORBIDDEN_403_EXAMPLE]),
        },
    ),
    update=extend_schema(
        tags=['Bookings'],
        summary='Full update of a recurring booking template',
        description='Replaces all fields on an existing recurring booking template.',
        request=RecurringBookingSerializer,
        responses={
            200: RecurringBookingSerializer,
            400: OpenApiResponse(description='Validation error.', examples=[_RESOURCE_400_EXAMPLE]),
            401: OpenApiResponse(description='Not authenticated.', examples=[_AUTH_401_EXAMPLE]),
            403: OpenApiResponse(description='Company members only.', examples=[_FORBIDDEN_403_EXAMPLE]),
            404: OpenApiResponse(description='Not found.'),
        },
    ),
    partial_update=extend_schema(
        tags=['Bookings'],
        summary='Partial update of a recurring booking template',
        description='Updates one or more fields on an existing recurring booking template.',
        request=RecurringBookingSerializer,
        responses={
            200: RecurringBookingSerializer,
            400: OpenApiResponse(description='Validation error.', examples=[_RESOURCE_400_EXAMPLE]),
            401: OpenApiResponse(description='Not authenticated.', examples=[_AUTH_401_EXAMPLE]),
            403: OpenApiResponse(description='Company members only.', examples=[_FORBIDDEN_403_EXAMPLE]),
            404: OpenApiResponse(description='Not found.'),
        },
    ),
    destroy=extend_schema(
        tags=['Bookings'],
        summary='Cancel a recurring booking series',
        description=(
            'Deletes the recurring series and **removes all future `Booking` rows** tied to it. '
            'Past bookings in the series are left unchanged.'
        ),
        responses={
            204: OpenApiResponse(description='Deleted.'),
            401: OpenApiResponse(description='Not authenticated.', examples=[_AUTH_401_EXAMPLE]),
            403: OpenApiResponse(description='Company members only.', examples=[_FORBIDDEN_403_EXAMPLE]),
            404: OpenApiResponse(description='Not found.'),
        },
    ),
)
class RecurringBookingViewSet(CompanyIsolationMixin, viewsets.ModelViewSet):
    serializer_class = RecurringBookingSerializer
    permission_classes = [IsCompanyMember, IsEmailVerifiedOrSuperAdmin]
    queryset = RecurringBooking.objects.all()
    http_method_names = ['get', 'post', 'patch', 'delete']

    def get_serializer_class(self):
        if self.action == 'create':
            return RecurringBookingCreateSerializer
        return RecurringBookingSerializer

    def get_queryset(self):
        queryset = (
            super()
            .get_queryset()
            .select_related('resource', 'user', 'company')
            .order_by('id')
        )
        user = self.request.user
        if user.is_superadmin():
            return queryset
        if user.is_company_admin():
            return queryset.filter(
                Q(user_id=user.id)
                | Q(company_id=user.company_id, user__role='employee')
            )
        return queryset.filter(user_id=user.id)

    def _get_recurring_for_destroy(self, *, pk):
        user = self.request.user
        queryset = RecurringBooking.objects.select_related('resource', 'user', 'company')

        if user.is_superadmin():
            return queryset.filter(pk=pk).first()

        if user.is_company_admin():
            return queryset.filter(pk=pk).filter(
                Q(user_id=user.id)
                | Q(company_id=user.company_id, user__role='employee')
            ).first()

        return queryset.filter(pk=pk, user_id=user.id).first()

    @staticmethod
    def _next_matching_weekday(*, day_of_week, base_date):
        """Return the next calendar date for weekday (strictly after base_date)."""
        days_ahead = (day_of_week - base_date.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        return base_date + timedelta(days=days_ahead)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        valid_from = self._next_matching_weekday(
            day_of_week=serializer.validated_data['day_of_week'],
            base_date=timezone.localdate(),
        )

        with transaction.atomic():
            recurring_booking = RecurringBooking.objects.create(
                resource=serializer.validated_data['resource'],
                user=request.user,
                company=request.user.company,
                day_of_week=serializer.validated_data['day_of_week'],
                start_time=serializer.validated_data['start_time'],
                end_time=serializer.validated_data['end_time'],
                valid_from=valid_from,
                valid_until=serializer.validated_data['repeat_until'],
                is_active=True,
            )
            skipped_dates = create_bookings_for_recurring(
                recurring_booking,
                start_date=recurring_booking.valid_from,
                end_date=recurring_booking.valid_until,
            )

        output = RecurringBookingSerializer(recurring_booking, context=self.get_serializer_context()).data
        output['skipped_dates'] = skipped_dates
        headers = self.get_success_headers(output)
        return Response(output, status=status.HTTP_201_CREATED, headers=headers)

    def destroy(self, request, *args, **kwargs):
        recurring_booking = self._get_recurring_for_destroy(pk=kwargs.get('pk'))
        if recurring_booking is None:
            raise Http404
        Booking.objects.filter(
            recurring_booking=recurring_booking,
            start_time__gt=timezone.now(),
        ).delete()
        recurring_booking.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
