from datetime import datetime, timedelta

from django.db.models import Prefetch, Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.db import transaction
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from drf_spectacular.utils import (
    extend_schema,
    extend_schema_view,
    OpenApiParameter,
    OpenApiResponse,
    inline_serializer,
)
from drf_spectacular.types import OpenApiTypes
import rest_framework.fields as fields

from apps.core.permissions import IsSuperAdmin, IsCompanyMember, IsEmailVerifiedOrSuperAdmin
from apps.notifications.models import Notification
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
    ResourceScheduleSlotSerializer,
    BookingSerializer,
    BookingCreateSerializer,
    RecurringBookingSerializer,
    ResourceBlockSerializer,
    _EQUIPMENT_KEYS,
)
from .filters import ResourceFilter, BookingFilter
from .schedule import busy_slots_for_resource, day_range_aware, week_range_for_date


@extend_schema_view(
    list=extend_schema(
        tags=['Bookings'],
        summary='List resources (catalog)',
        parameters=[
            OpenApiParameter(
                name='available_from',
                type=OpenApiTypes.DATETIME,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Свободен с (ISO datetime); вместе с available_to исключает ресурсы с пересечениями.',
            ),
            OpenApiParameter(
                name='available_to',
                type=OpenApiTypes.DATETIME,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Свободен до (ISO datetime).',
            ),
        ],
        responses={200: ResourceListSerializer(many=True)},
    ),
    retrieve=extend_schema(
        tags=['Bookings'],
        summary='Get resource details and 7-day busy schedule',
        responses={200: ResourceDetailSerializer, 404: OpenApiResponse(description='Not found')},
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
        summary='Resource busy schedule for a calendar day or week',
        parameters=[
            OpenApiParameter(
                name='date',
                type=OpenApiTypes.DATE,
                location=OpenApiParameter.QUERY,
                required=False,
                description='YYYY-MM-DD — один календарный день (локальная таймзона сервера).',
            ),
            OpenApiParameter(
                name='week',
                type=OpenApiTypes.DATE,
                location=OpenApiParameter.QUERY,
                required=False,
                description='YYYY-MM-DD — любой день; возвращается неделя с понедельника по воскресенье.',
            ),
        ],
        responses={
            200: ResourceScheduleSlotSerializer(many=True),
            400: OpenApiResponse(description='Bad query'),
            404: OpenApiResponse(description='Not found'),
        },
    )
    @action(detail=True, methods=['get'], url_path='schedule')
    def schedule(self, request, pk=None):
        resource = self.get_object()
        date_s = request.query_params.get('date')
        week_s = request.query_params.get('week')
        if date_s and week_s:
            raise ValidationError({'detail': 'Укажите только один параметр: date или week.'})
        if not date_s and not week_s:
            raise ValidationError(
                {'detail': 'Нужен query-параметр date=YYYY-MM-DD или week=YYYY-MM-DD.'}
            )

        def _parse_date(label, s):
            try:
                return datetime.strptime(s, '%Y-%m-%d').date()
            except (TypeError, ValueError):
                raise ValidationError(
                    {'detail': f'Неверный формат {label}; ожидается YYYY-MM-DD.'}
                ) from None

        if date_s:
            d = _parse_date('date', date_s)
            range_start, range_end = day_range_aware(d)
        else:
            d = _parse_date('week', week_s)
            range_start, range_end = week_range_for_date(d)

        slots = busy_slots_for_resource(resource.id, range_start, range_end)
        return Response(ResourceScheduleSlotSerializer(slots, many=True).data)

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
            409: OpenApiResponse(description='Resource already occupied'),
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

    def partial_update(self, request, *args, **kwargs):
        # AC DEV-77: when updating reservation times, reuse creation conflict-control.
        updates_time = 'start_time' in request.data or 'end_time' in request.data
        if not updates_time:
            return super().partial_update(request, *args, **kwargs)
        if 'start_time' not in request.data or 'end_time' not in request.data:
            raise ValidationError({'detail': 'Both start_time and end_time are required for time updates.'})

        with transaction.atomic():
            current = self.get_queryset().select_related('resource').select_for_update().get(pk=kwargs['pk'])
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
        user = request.user
        # Only the booking owner, a company_admin of the same company, or a superadmin may cancel.
        if (
            booking.user != user
            and not user.is_company_admin()
            and not user.is_superadmin()
        ):
            raise PermissionDenied('You can only cancel your own bookings.')
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
        return Response(BookingSerializer(booking).data)

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
        summary='Admin cancel booking',
        request=inline_serializer(
            name='AdminCancelBookingRequest',
            fields={
                'reason': fields.CharField(required=True, allow_blank=False),
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
                Notification.objects.create(
                    user=user,
                    notification_type='booking_confirmed',
                    title=f'You were added to meeting: {booking.resource.name}',
                    body='Check your bookings for updated participants.',
                    url='',
                )
            self._create_change_audit(
                booking=booking,
                action=BookingChangeAudit.ACTION_PARTICIPANTS_ADDED,
                payload={'user_ids': added_ids},
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
            400: OpenApiResponse(description='reason is required'),
            403: OpenApiResponse(description='Company admin or superadmin only'),
            404: OpenApiResponse(description='Booking not found'),
        },
    )
    @action(detail=True, methods=['post'], url_path='admin-cancel')
    def admin_cancel(self, request, pk=None):
        user = request.user
        if not user.is_company_admin():
            raise PermissionDenied('Only company admins can perform admin cancellation.')

        booking = self.get_object()
        reason = str(request.data.get('reason', '')).strip()
        if not reason:
            raise ValidationError({'reason': 'This field is required.'})

        booking.status = 'cancelled'
        booking.cancelled_by = user
        booking.cancel_reason = reason
        booking.save(update_fields=['status', 'cancelled_by', 'cancel_reason', 'updated_at'])

        Notification.objects.create(
            user=booking.user,
            notification_type='booking_cancelled',
            title=f'Бронирование отменено администратором: {booking.resource.name}',
            body=reason,
            url='',
        )
        return Response(BookingSerializer(booking, context=self.get_serializer_context()).data)

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
        },
    )
    @action(detail=False, methods=['get'], url_path='my')
    def my_bookings(self, request):
        if 'user' in request.query_params:
            raise ValidationError(
                "The 'user' filter is not supported on this endpoint. "
                "Use /bookings/reservations/ to filter by user."
            )
        qs = Booking.objects.filter(user=request.user).select_related('resource')

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
        elif status_filter == 'past':
            qs = qs.order_by('-start_time')
        else:
            qs = qs.order_by('-start_time')

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
