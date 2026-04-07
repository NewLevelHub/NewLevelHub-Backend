from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiResponse, inline_serializer
import rest_framework.fields as fields

from apps.core.permissions import IsSuperAdmin, IsCompanyMember
from apps.core.mixins import CompanyIsolationMixin, SetCompanyOnCreateMixin
from .models import Resource, Booking, RecurringBooking
from .serializers import (
    ResourceSerializer,
    ResourceListSerializer,
    BookingSerializer,
    BookingCreateSerializer,
    RecurringBookingSerializer,
    ResourceBlockSerializer,
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
        responses={204: OpenApiResponse(description='Deleted'), 403: OpenApiResponse(description='Superadmin only')},
    ),
)
class ResourceViewSet(viewsets.ModelViewSet):
    queryset = Resource.objects.filter(is_active=True)
    permission_classes = [IsCompanyMember]
    filterset_class = ResourceFilter
    search_fields = ['name', 'zone']
    ordering_fields = ['name', 'floor', 'capacity']

    def get_serializer_class(self):
        if self.action == 'list':
            return ResourceListSerializer
        return ResourceSerializer

    def get_permissions(self):
        if self.action in ('create', 'update', 'partial_update', 'destroy'):
            return [IsSuperAdmin()]
        return [IsCompanyMember()]

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
        summary='Block resource (superadmin)',
        request=ResourceBlockSerializer,
        responses={
            201: ResourceBlockSerializer,
            400: OpenApiResponse(description='Validation error'),
            403: OpenApiResponse(description='Superadmin only'),
        },
    )
    @action(detail=True, methods=['post'], url_path='block', permission_classes=[IsSuperAdmin])
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
    permission_classes = [IsCompanyMember]
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
    permission_classes = [IsCompanyMember]
    queryset = RecurringBooking.objects.all()
    http_method_names = ['get', 'post', 'patch', 'delete']

    def perform_create(self, serializer):
        serializer.save(user=self.request.user, company=self.request.user.company)
