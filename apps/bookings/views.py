from django.db.models import Prefetch, Q
from django.utils import timezone
from django.db import transaction
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiResponse, inline_serializer
import rest_framework.fields as fields

from apps.core.permissions import IsSuperAdmin, IsCompanyMember, IsEmailVerifiedOrSuperAdmin
from apps.notifications.models import Notification
from apps.core.mixins import CompanyIsolationMixin, SetCompanyOnCreateMixin
from .models import Resource, Booking, RecurringBooking, ResourceBlock
from .serializers import (
    ResourceSerializer,
    ResourceListSerializer,
    ResourceBulkCreateSerializer,
    BookingSerializer,
    BookingCreateSerializer,
    RecurringBookingSerializer,
    ResourceBlockSerializer,
    _EQUIPMENT_KEYS,
)
from .filters import ResourceFilter, BookingFilter


@extend_schema_view(
    list=extend_schema(
        tags=['Bookings'],
        summary='List resources (catalog)',
        responses={200: ResourceListSerializer(many=True)},
    ),
    retrieve=extend_schema(
        tags=['Bookings'],
        summary='Get resource details',
        responses={200: ResourceSerializer, 404: OpenApiResponse(description='Not found')},
    ),
    create=extend_schema(
        tags=['Bookings'],
        summary='Create resource (superadmin)',
        request=ResourceSerializer,
        responses={
            201: ResourceSerializer,
            400: OpenApiResponse(description='Validation error'),
            403: OpenApiResponse(description='Superadmin only'),
        },
    ),
    update=extend_schema(
        tags=['Bookings'],
        summary='Update resource (superadmin)',
        request=ResourceSerializer,
        responses={200: ResourceSerializer, 403: OpenApiResponse(description='Superadmin only')},
    ),
    partial_update=extend_schema(
        tags=['Bookings'],
        summary='Partial update resource',
        request=ResourceSerializer,
        responses={200: ResourceSerializer, 403: OpenApiResponse(description='Superadmin only')},
    ),
    destroy=extend_schema(
        tags=['Bookings'],
        summary='Delete resource (superadmin)',
        responses={
            204: OpenApiResponse(description='Deleted'),
            400: OpenApiResponse(description='Has future bookings'),
            403: OpenApiResponse(description='Superadmin only'),
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
            if company and getattr(company, 'plan', None) == 'premium':
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
        for _key in ('equipment', 'has_projector', 'has_tv', 'has_video_conf'):
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
        if self.action in ('create', 'update', 'partial_update', 'destroy', 'block', 'bulk_create'):
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
            Notification.objects.create(
                user=booking.user,
                notification_type='booking_cancelled',
                title=f'Бронирование отменено: {resource.name}',
                body='Ресурс деактивирован администратором.',
                url='',
            )

    @extend_schema(
        tags=['Bookings'],
        summary='Get resource schedule for a day',
        responses={200: BookingSerializer(many=True), 404: OpenApiResponse(description='Not found')},
    )
    @action(detail=True, methods=['get'], url_path='schedule')
    def schedule(self, request, pk=None):
        # TODO: получить дату из query param, вернуть bookings + blocks за этот день
        resource = self.get_object()
        bookings = Booking.objects.filter(
            resource=resource, status='confirmed',
        ).order_by('start_time')
        return Response(BookingSerializer(bookings, many=True).data)

    @extend_schema(
        tags=['Bookings'],
        summary='Bulk create resources (superadmin)',
        request=ResourceBulkCreateSerializer,
        responses={
            201: ResourceSerializer(many=True),
            400: OpenApiResponse(description='Validation error'),
            403: OpenApiResponse(description='Superadmin only'),
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
        tags=['Bookings'],
        summary='Block resource (superadmin)',
        request=ResourceBlockSerializer,
        responses={
            201: ResourceBlockSerializer,
            400: OpenApiResponse(description='Validation error'),
            403: OpenApiResponse(description='Superadmin only'),
        },
    )
    @action(detail=True, methods=['post'], url_path='block')
    def block(self, request, pk=None):
        resource = self.get_object()
        serializer = ResourceBlockSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(resource=resource, blocked_by=request.user)
        # TODO: отменить пересекающиеся бронирования с уведомлением
        return Response(serializer.data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    list=extend_schema(
        tags=['Bookings'],
        summary='List bookings',
        responses={200: BookingSerializer(many=True)},
    ),
    retrieve=extend_schema(
        tags=['Bookings'],
        summary='Get booking details',
        responses={200: BookingSerializer, 404: OpenApiResponse(description='Not found')},
    ),
    create=extend_schema(
        tags=['Bookings'],
        summary='Create booking',
        request=BookingCreateSerializer,
        responses={
            201: BookingSerializer,
            400: OpenApiResponse(description='Validation error or scheduling conflict'),
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

    @extend_schema(
        tags=['Bookings'],
        summary='Cancel booking',
        request=inline_serializer(
            name='CancelBookingRequest',
            fields={'reason': fields.CharField(required=False, default='')},
        ),
        responses={
            200: BookingSerializer,
            401: OpenApiResponse(description='Not authenticated'),
            404: OpenApiResponse(description='Booking not found'),
        },
    )
    @action(detail=True, methods=['post'], url_path='cancel')
    def cancel(self, request, pk=None):
        booking = self.get_object()
        # TODO: проверить min_cancel_minutes, отправить уведомление
        booking.status = 'cancelled'
        booking.cancelled_by = request.user
        booking.cancel_reason = request.data.get('reason', '')
        booking.save()
        return Response(BookingSerializer(booking).data)

    @extend_schema(
        tags=['Bookings'],
        summary='My bookings',
        responses={200: BookingSerializer(many=True)},
    )
    @action(detail=False, methods=['get'], url_path='my')
    def my_bookings(self, request):
        qs = Booking.objects.filter(user=request.user).order_by('-start_time')
        page = self.paginate_queryset(qs)
        if page is not None:
            return self.get_paginated_response(BookingSerializer(page, many=True).data)
        return Response(BookingSerializer(qs, many=True).data)


@extend_schema_view(
    list=extend_schema(
        tags=['Bookings'],
        summary='List recurring bookings',
        responses={200: RecurringBookingSerializer(many=True)},
    ),
    create=extend_schema(
        tags=['Bookings'],
        summary='Create recurring booking',
        request=RecurringBookingSerializer,
        responses={
            201: RecurringBookingSerializer,
            400: OpenApiResponse(description='Validation error'),
            401: OpenApiResponse(description='Not authenticated'),
        },
    ),
)
class RecurringBookingViewSet(CompanyIsolationMixin, viewsets.ModelViewSet):
    serializer_class = RecurringBookingSerializer
    permission_classes = [IsCompanyMember, IsEmailVerifiedOrSuperAdmin]
    queryset = RecurringBooking.objects.all()
    http_method_names = ['get', 'post', 'patch', 'delete']

    def perform_create(self, serializer):
        serializer.save(user=self.request.user, company=self.request.user.company)
