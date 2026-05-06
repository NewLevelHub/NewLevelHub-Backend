import csv
from datetime import timedelta

from django.core.cache import cache
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
    inline_serializer,
)
import rest_framework.fields as fields
from rest_framework import renderers, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import BasePermission
from rest_framework.response import Response

from apps.core.permissions import IsCompanyAdmin, IsCompanyMember
from apps.core.mixins import CompanyIsolationMixin, SetCompanyOnCreateMixin
from apps.notifications.utils import create_notification
from .filters import AccessLogFilter, GuestPassFilter
from .models import AccessLog, GuestPass
from . import tasks
from .tasks import notify_pass_creator_on_entry
from .serializers import (
    AccessLogSerializer, GuestPassSerializer, GuestPassCreateSerializer, GuestPassValidateSerializer,
)


class CSVPassthroughRenderer(renderers.BaseRenderer):
    media_type = 'text/csv'
    format = 'csv'
    charset = 'utf-8'

    def render(self, data, accepted_media_type=None, renderer_context=None):
        if data is None:
            return b''
        if isinstance(data, bytes):
            return data
        if isinstance(data, str):
            return data.encode(self.charset)
        return str(data).encode(self.charset)


@extend_schema_view(
    list=extend_schema(
        tags=['Access'],
        summary='List guest passes',
        responses={200: GuestPassSerializer(many=True)},
    ),
    retrieve=extend_schema(
        tags=['Access'],
        summary='Get guest pass details',
        responses={200: GuestPassSerializer, 404: OpenApiResponse(description='Not found')},
    ),
    create=extend_schema(
        tags=['Access'],
        summary='Create guest pass',
        request=GuestPassCreateSerializer,
        responses={
            201: GuestPassSerializer,
            400: OpenApiResponse(description='Validation error'),
            401: OpenApiResponse(description='Not authenticated'),
        },
    ),
)
class GuestPassViewSet(CompanyIsolationMixin, SetCompanyOnCreateMixin, viewsets.ModelViewSet):
    serializer_class = GuestPassSerializer
    permission_classes = [IsCompanyAdmin]
    queryset = GuestPass.objects.select_related('created_by', 'company').order_by('-created_at')
    http_method_names = ['get', 'post']
    filterset_class = GuestPassFilter

    def get_permissions(self):
        if self.action in ('list', 'retrieve', 'create'):
            return [IsCompanyMember()]
        return [permission() for permission in self.permission_classes]

    def get_serializer_class(self):
        if self.action == 'create':
            return GuestPassCreateSerializer
        return GuestPassSerializer

    def get_queryset(self):
        user = self.request.user
        qs = super().get_queryset()
        if user.role == 'superadmin':
            return qs
        if user.role == 'company_admin':
            return qs.filter(company=user.company)
        return qs.filter(created_by=user)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        guest_pass = serializer.save()
        output_serializer = GuestPassSerializer(guest_pass, context=self.get_serializer_context())
        headers = self.get_success_headers(output_serializer.data)
        return Response(output_serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    @extend_schema(
        tags=['Access'],
        summary='Revoke guest pass',
        request=None,
        responses={
            200: OpenApiResponse(description='Pass revoked'),
            400: OpenApiResponse(description='Cannot revoke used or expired pass'),
            401: OpenApiResponse(description='Not authenticated'),
            404: OpenApiResponse(description='Not found'),
        },
    )
    @action(detail=True, methods=['post'], url_path='revoke')
    def revoke(self, request, pk=None):
        guest_pass = self.get_object()
        if guest_pass.status in ('used', 'expired'):
            return Response(
                {'detail': 'Used or expired passes cannot be revoked.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        guest_pass.status = 'revoked'
        guest_pass.save(update_fields=['status'])
        return Response({'detail': 'Pass revoked'})

    @extend_schema(
        tags=['Access'],
        summary='Resend QR to guest email',
        request=None,
        responses={
            200: OpenApiResponse(description='QR code resent'),
            400: OpenApiResponse(description='Cannot resend for revoked pass'),
            429: OpenApiResponse(description='Rate limit exceeded (3 per hour)'),
            401: OpenApiResponse(description='Not authenticated'),
            404: OpenApiResponse(description='Not found'),
        },
    )
    @action(detail=True, methods=['post'], url_path='resend')
    def resend(self, request, pk=None):
        guest_pass = self.get_object()
        if guest_pass.status != 'active':
            return Response(
                {'detail': f'Cannot resend pass with status "{guest_pass.status}"'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        now = timezone.now()
        window_start = now - timedelta(hours=1)
        # Keep cache fast-path for shared cache deployments, but enforce the hard limit via DB
        # so behavior is stable even with process-local caches.
        cache_key = f'guest-pass-resend:{guest_pass.id}'
        cache_attempts = None
        try:
            if cache.add(cache_key, 1, timeout=3600):
                cache_attempts = 1
            else:
                cache_attempts = cache.incr(cache_key)
        except Exception:
            cache_attempts = None

        with transaction.atomic():
            locked_pass = GuestPass.objects.select_for_update().get(pk=guest_pass.pk)
            if (
                locked_pass.resend_window_started_at is None
                or locked_pass.resend_window_started_at < window_start
            ):
                locked_pass.resend_window_started_at = now
                locked_pass.resend_attempts_in_window = 0

            if locked_pass.resend_attempts_in_window >= 3 or (cache_attempts is not None and cache_attempts > 3):
                return Response(
                    {'detail': 'Rate limit exceeded. Max 3 resends per hour.'},
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )

            locked_pass.resend_attempts_in_window += 1
            locked_pass.save(update_fields=['resend_window_started_at', 'resend_attempts_in_window'])

        tasks.send_guest_pass_email.delay(guest_pass.id)
        return Response({'detail': 'QR code resent'})


class IsSuperAdminOrReception(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        return bool(
            user and user.is_authenticated and user.role in ('superadmin', 'reception')
        )


@extend_schema(
    tags=['Access'],
    summary='Validate QR code (reception desk)',
    request=GuestPassValidateSerializer,
    responses={
        200: inline_serializer(
            name='QRValidateSuccess',
            fields={
                'valid': fields.BooleanField(),
                'guest_name': fields.CharField(),
                'purpose': fields.CharField(),
                'invited_by': fields.CharField(),
                'valid_from': fields.DateTimeField(),
                'valid_until': fields.DateTimeField(),
            },
        ),
        400: OpenApiResponse(description='Invalid or missing QR code in payload'),
        401: OpenApiResponse(description='Not authenticated'),
        403: OpenApiResponse(description='Pass not yet active or expired (time-based check), '
                             'or only superadmin and reception can validate'),
    },
)
@api_view(['POST'])
@permission_classes([IsSuperAdminOrReception])
def validate_qr(request):
    serializer = GuestPassValidateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        guest_pass = GuestPass.objects.select_related('created_by').get(
            qr_code=serializer.validated_data['qr_code']
        )
    except GuestPass.DoesNotExist:
        return Response({'valid': False, 'reason': 'not_found'})

    now = timezone.now()

    # Check not-yet-active (before start time) — must come before expiry check
    if now < guest_pass.valid_from:
        formatted = guest_pass.valid_from.astimezone(
            timezone.get_current_timezone()
        ).strftime('%d.%m.%Y %H:%M')
        return Response(
            {'detail': f'Доступ ещё не разрешён. Начало доступа: {formatted}'},
            status=status.HTTP_403_FORBIDDEN,
        )

    # Check expired (after end time) — 403, not 200
    if guest_pass.valid_until < now:
        return Response(
            {'detail': 'Срок действия QR-кода истек'},
            status=status.HTTP_403_FORBIDDEN,
        )

    if guest_pass.status == 'revoked':
        return Response({'valid': False, 'reason': 'revoked'})
    if guest_pass.status == 'used' or (guest_pass.usage_type == 'single' and guest_pass.times_used > 0):
        return Response({'valid': False, 'reason': 'already_used'})
    if guest_pass.status != 'active':
        return Response({'valid': False, 'reason': 'not_found'})

    # Зафиксировать использование
    guest_pass.times_used += 1
    if guest_pass.usage_type == 'single':
        guest_pass.status = 'used'
    guest_pass.save()

    AccessLog.objects.create(
        guest_pass=guest_pass,
        checked_by=request.user,
        method='qr',
    )
    create_notification(
        user=guest_pass.created_by,
        notification_type='guest_validated',
        title='Гостевой пропуск подтверждён',
        message=(
            f'{guest_pass.guest_name} — {guest_pass.visit_purpose or "визит"} '
            f'({timezone.now().strftime("%Y-%m-%d %H:%M")})'
        ),
        link='/access/',
    )
    try:
        notify_pass_creator_on_entry.delay(guest_pass.id)
    except Exception:
        pass
    return Response({
        'valid': True,
        'guest_name': guest_pass.guest_name,
        'purpose': guest_pass.visit_purpose,
        'invited_by': guest_pass.created_by.full_name,
        'valid_from': guest_pass.valid_from,
        'valid_until': guest_pass.valid_until,
    })


@extend_schema_view(
    list=extend_schema(
        tags=['Access'],
        summary='List access logs',
        parameters=[
            OpenApiParameter('company_id', int, description='Filter by company'),
            OpenApiParameter('date_from', str, description='Filter from date (YYYY-MM-DD)'),
            OpenApiParameter('date_to', str, description='Filter to date (YYYY-MM-DD)'),
            OpenApiParameter('search', str, description='Search by guest name or email'),
        ],
        responses={
            200: AccessLogSerializer(many=True),
            403: OpenApiResponse(description='Company admin or superadmin required'),
        },
    ),
    create=extend_schema(
        tags=['Access'],
        summary='Create manual access log entry',
        request=AccessLogSerializer,
        responses={
            201: AccessLogSerializer,
            400: OpenApiResponse(description='Validation error'),
            403: OpenApiResponse(description='Company admin or superadmin required'),
        },
    ),
)
class AccessLogViewSet(viewsets.ModelViewSet):
    serializer_class = AccessLogSerializer
    permission_classes = [IsCompanyAdmin]
    renderer_classes = [renderers.JSONRenderer, CSVPassthroughRenderer]
    http_method_names = ['get', 'post']
    queryset = AccessLog.objects.select_related(
        'guest_pass__created_by', 'guest_pass__company', 'checked_by', 'user',
    ).order_by('-created_at')
    filterset_class = AccessLogFilter
    search_fields = ['guest_pass__guest_name', 'guest_pass__guest_email']

    def get_queryset(self):
        user = self.request.user
        qs = super().get_queryset()
        if user.role == 'superadmin':
            return qs
        return qs.filter(guest_pass__company=user.company)

    @extend_schema(
        tags=['Access'],
        summary='Export access logs as CSV',
        parameters=[
            OpenApiParameter('date_from', str, description='From date (YYYY-MM-DD)'),
            OpenApiParameter('date_to', str, description='To date (YYYY-MM-DD)'),
            OpenApiParameter('company_id', int, description='Filter by company (superadmin only)'),
        ],
        responses={200: OpenApiResponse(description='CSV file download')},
    )
    @action(detail=False, methods=['get'], url_path='export')
    def export(self, request):
        qs = self.filter_queryset(self.get_queryset())

        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="access_logs.csv"'

        writer = csv.writer(response)
        writer.writerow(['id', 'guest_pass', 'invited_by', 'validated_at', 'validated_by',
                         'entry_point', 'method', 'is_entry'])

        for log in qs:
            invited_by = ''
            if log.guest_pass and log.guest_pass.created_by:
                invited_by = log.guest_pass.created_by.full_name
            validated_by = log.checked_by.full_name if log.checked_by else ''
            guest_pass_id = log.guest_pass_id or ''

            writer.writerow([
                log.id,
                guest_pass_id,
                invited_by,
                log.created_at.isoformat() if log.created_at else '',
                validated_by,
                log.entry_point,
                log.method,
                log.is_entry,
            ])

        return response
