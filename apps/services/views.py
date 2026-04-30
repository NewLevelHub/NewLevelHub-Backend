import os

from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    extend_schema, extend_schema_view, OpenApiParameter, OpenApiResponse,
    OpenApiExample, inline_serializer,
)
import rest_framework.fields as fields

from apps.core.permissions import IsSuperAdmin, IsCompanyMember, IsCompanyAdminOrReadOnly
from apps.core.mixins import SetCompanyOnCreateMixin
from .models import Floor, MapPoint, ServiceRequest, Announcement, AnnouncementRead
from .serializers import (
    FloorSerializer, FloorDetailSerializer, MapPointSerializer, MapPointSearchSerializer,
    ServiceRequestSerializer, ServiceRequestUpdateSerializer,
    AnnouncementSerializer,
)


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

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.role == 'superadmin':
            return qs
        if user.company_id:
            return qs.filter(company__isnull=True) | qs.filter(company=user.company_id)
        # guest or user without company — show only global floors
        return qs.filter(company__isnull=True)

    def perform_create(self, serializer):
        # Floors are global; superadmin creates them without a company.
        serializer.save(company=None)

    def get_permissions(self):
        if self.action in ('create', 'update', 'partial_update', 'destroy'):
            return [IsSuperAdmin()]
        return [IsCompanyMember()]

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

        points = MapPoint.objects.select_related('resource', 'company').filter(floor=floor)
        serializer = MapPointSerializer(
            points,
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
        old_image = self.get_object().plan_image
        instance = serializer.save()
        # Если файл заменили — удалить старый с диска
        if old_image and old_image != instance.plan_image:
            if os.path.isfile(old_image.path):
                os.remove(old_image.path)

    def perform_destroy(self, instance):
        from django.conf import settings
        # Save the file name BEFORE deleting the DB row.
        image_name = instance.plan_image.name if instance.plan_image else None
        instance.delete()
        # Physically remove the file only after the DB row is gone.
        if image_name:
            image_path = os.path.join(settings.MEDIA_ROOT, image_name)
            if os.path.isfile(image_path):
                os.remove(image_path)


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
            MapPoint.objects
            .select_related('resource', 'floor')
            .filter(Q(label__icontains=q) | Q(resource__name__icontains=q))
        )
        serializer = MapPointSearchSerializer(points, many=True)
        return Response(serializer.data)


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
