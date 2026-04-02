from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, extend_schema_view

from apps.core.permissions import IsSuperAdmin
from .models import Resource, Booking, RecurringBooking, ResourceBlock
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
    list=extend_schema(tags=['Bookings'], summary='List resources (catalog)'),
    retrieve=extend_schema(tags=['Bookings'], summary='Get resource details'),
    create=extend_schema(tags=['Bookings'], summary='Create resource (superadmin)'),
    update=extend_schema(tags=['Bookings'], summary='Update resource (superadmin)'),
    partial_update=extend_schema(tags=['Bookings'], summary='Partial update resource'),
    destroy=extend_schema(tags=['Bookings'], summary='Delete resource (superadmin)'),
)
class ResourceViewSet(viewsets.ModelViewSet):
    queryset = Resource.objects.filter(is_active=True)
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
        return [IsAuthenticated()]

    @extend_schema(tags=['Bookings'], summary='Get resource schedule for a day')
    @action(detail=True, methods=['get'], url_path='schedule')
    def schedule(self, request, pk=None):
        # TODO: получить дату из query param, вернуть bookings + blocks за этот день
        resource = self.get_object()
        bookings = Booking.objects.filter(
            resource=resource, status='confirmed',
        ).order_by('start_time')
        return Response(BookingSerializer(bookings, many=True).data)

    @extend_schema(tags=['Bookings'], summary='Block resource (superadmin)')
    @action(detail=True, methods=['post'], url_path='block', permission_classes=[IsSuperAdmin])
    def block(self, request, pk=None):
        resource = self.get_object()
        serializer = ResourceBlockSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(resource=resource, blocked_by=request.user)
        # TODO: отменить пересекающиеся бронирования с уведомлением
        return Response(serializer.data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    list=extend_schema(tags=['Bookings'], summary='List bookings'),
    retrieve=extend_schema(tags=['Bookings'], summary='Get booking details'),
    create=extend_schema(tags=['Bookings'], summary='Create booking'),
)
class BookingViewSet(viewsets.ModelViewSet):
    serializer_class = BookingSerializer
    filterset_class = BookingFilter
    ordering_fields = ['start_time', 'created_at']
    http_method_names = ['get', 'post', 'patch', 'delete']

    def get_queryset(self):
        user = self.request.user
        if user.role == 'superadmin':
            return Booking.objects.all()
        if user.role == 'company_admin' and user.company_id:
            return Booking.objects.filter(company=user.company)
        return Booking.objects.filter(user=user)

    def get_serializer_class(self):
        if self.action == 'create':
            return BookingCreateSerializer
        return BookingSerializer

    @extend_schema(tags=['Bookings'], summary='Cancel booking')
    @action(detail=True, methods=['post'], url_path='cancel')
    def cancel(self, request, pk=None):
        booking = self.get_object()
        # TODO: проверить min_cancel_minutes, отправить уведомление
        booking.status = 'cancelled'
        booking.cancelled_by = request.user
        booking.cancel_reason = request.data.get('reason', '')
        booking.save()
        return Response(BookingSerializer(booking).data)

    @extend_schema(tags=['Bookings'], summary='My bookings')
    @action(detail=False, methods=['get'], url_path='my')
    def my_bookings(self, request):
        qs = Booking.objects.filter(user=request.user).order_by('-start_time')
        page = self.paginate_queryset(qs)
        if page is not None:
            return self.get_paginated_response(BookingSerializer(page, many=True).data)
        return Response(BookingSerializer(qs, many=True).data)


@extend_schema_view(
    list=extend_schema(tags=['Bookings'], summary='List recurring bookings'),
    create=extend_schema(tags=['Bookings'], summary='Create recurring booking'),
)
class RecurringBookingViewSet(viewsets.ModelViewSet):
    serializer_class = RecurringBookingSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'patch', 'delete']

    def get_queryset(self):
        user = self.request.user
        if user.role == 'superadmin':
            return RecurringBooking.objects.all()
        return RecurringBooking.objects.filter(user=user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user, company=self.request.user.company)
