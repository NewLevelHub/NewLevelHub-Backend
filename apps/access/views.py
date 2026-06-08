import csv
import io
from datetime import timedelta

from django.core.cache import cache
from django.db import transaction
from django.db.models import Prefetch
from django.http import FileResponse, Http404, StreamingHttpResponse
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
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.core.exceptions import LocalizedError
from apps.core.i18n import get_lang, translate
from apps.core.permissions import IsCompanyAdmin, IsCompanyMember, IsGuestOrCompanyMember, IsSuperAdminOrReception
from apps.core.mixins import CompanyIsolationMixin, SetCompanyOnCreateMixin
from apps.notifications.utils import create_notification
from .filters import AccessLogFilter, GuestPassFilter
from .models import AccessLog, GuestPass
from . import tasks
from .tasks import notify_pass_creator_on_entry
from .qr_image import generate_guest_pass_qr_image
from .serializers import (
    AccessLogSerializer, GuestPassSerializer, GuestPassCreateSerializer, GuestPassValidateSerializer,
    GuestPassValidationLogSerializer,
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
    queryset = GuestPass.objects.select_related('created_by', 'company').prefetch_related(
        Prefetch(
            'access_logs',
            queryset=AccessLog.objects.select_related('checked_by').order_by('-created_at'),
            to_attr='prefetched_logs',
        )
    ).order_by('-created_at')
    http_method_names = ['get', 'post']
    filterset_class = GuestPassFilter

    def get_permissions(self):
        if self.action in ('create', 'list', 'retrieve'):
            return [IsGuestOrCompanyMember()]
        if self.action == 'validations':
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
        summary='Export guest passes as CSV (admin/superadmin)',
        responses={200: OpenApiResponse(description='CSV file')},
    )
    @action(detail=False, methods=['get'], url_path='export', permission_classes=[IsCompanyAdmin])
    def export(self, request):
        qs = self.filter_queryset(self.get_queryset())
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            'ID', 'Гость', 'Email гостя', 'Телефон', 'Цель',
            'Пригласил', 'Компания', 'Статус',
            'Действует с', 'Действует до', 'Использований',
            'Проверил', 'Валидирован', 'Метод',
        ])
        for p in qs.iterator():
            logs = getattr(p, 'prefetched_logs', None)
            if logs is not None:
                last = logs[0] if logs else None
            else:
                last = p.access_logs.select_related('checked_by').order_by('-created_at').first()
            writer.writerow([
                p.pk,
                p.guest_name,
                p.guest_email,
                p.guest_phone,
                p.visit_purpose,
                p.created_by.full_name if p.created_by_id else '',
                p.company.name if p.company_id else '',
                p.status,
                p.valid_from.strftime('%Y-%m-%d %H:%M') if p.valid_from else '',
                p.valid_until.strftime('%Y-%m-%d %H:%M') if p.valid_until else '',
                p.times_used,
                last.checked_by.full_name if last and last.checked_by else '',
                last.created_at.strftime('%Y-%m-%d %H:%M') if last else '',
                last.method if last else '',
            ])
        stamp = timezone.now().strftime('%Y-%m-%d')
        response = StreamingHttpResponse(buf.getvalue(), content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="guest_passes_{stamp}.csv"'
        return response

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
            raise LocalizedError(
                code='PASS_CANNOT_REVOKE',
                i18n_key='access.pass_cannot_revoke',
                http_status=400,
            )
        guest_pass.status = 'revoked'
        guest_pass.save(update_fields=['status'])
        return Response({'detail': translate('access.pass_revoke_success', get_lang(request))})

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
            lang = get_lang(request)
            translated_status = translate(f'access.status.{guest_pass.status}', lang)
            raise LocalizedError(
                code='RESEND_STATUS_INVALID',
                i18n_key='access.resend_status_invalid',
                params={'status': translated_status},
                http_status=400,
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
                raise LocalizedError(
                    code='RESEND_RATE_LIMIT_EXCEEDED',
                    i18n_key='access.resend_rate_limit_exceeded',
                    http_status=429,
                )

            locked_pass.resend_attempts_in_window += 1
            locked_pass.save(update_fields=['resend_window_started_at', 'resend_attempts_in_window'])

        tasks.send_guest_pass_email.delay(guest_pass.id)
        return Response({'detail': translate('access.qr_resent', get_lang(request))})

    @extend_schema(
        tags=['Access'],
        summary='List validations for a guest pass',
        responses={
            200: inline_serializer(
                name='GuestPassValidations',
                fields={
                    'total': fields.IntegerField(),
                    'results': GuestPassValidationLogSerializer(many=True),
                },
            ),
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Forbidden'),
            404: OpenApiResponse(description='Not found'),
        },
    )
    @action(detail=True, methods=['get'], url_path='validations')
    def validations(self, request, pk=None):
        guest_pass = self.get_object()
        logs = guest_pass.access_logs.select_related('checked_by').order_by('-created_at')
        serializer = GuestPassValidationLogSerializer(logs, many=True)
        return Response({'total': guest_pass.times_used, 'results': serializer.data})


@extend_schema(
    tags=['Access'],
    summary='Get guest pass QR image (public, for email clients)',
    responses={
        200: OpenApiResponse(description='PNG image'),
        404: OpenApiResponse(description='Pass or image not found'),
    },
)
@api_view(['GET'])
@permission_classes([AllowAny])
def guest_pass_qr_image(request, qr_code):
    """
    Serve QR PNG by pass UUID.

    Public endpoint so Gmail/Outlook can load <img src="https://..."> without cid: attachments.
    """
    try:
        guest_pass = GuestPass.objects.get(qr_code=qr_code)
    except GuestPass.DoesNotExist:
        raise Http404

    if guest_pass.status in ('revoked', 'expired'):
        raise Http404

    if not guest_pass.qr_image:
        generate_guest_pass_qr_image(guest_pass)

    try:
        image_file = guest_pass.qr_image.open('rb')
    except FileNotFoundError:
        generate_guest_pass_qr_image(guest_pass)
        image_file = guest_pass.qr_image.open('rb')

    response = FileResponse(image_file, content_type='image/png')
    response['Cache-Control'] = 'private, max-age=3600'
    return response


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
        403: OpenApiResponse(description='Only superadmin and reception can validate'),
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

    if now < guest_pass.valid_from:
        return Response({
            'valid': False,
            'reason': 'not_yet_active',
            'available_from': guest_pass.valid_from.isoformat(),
        })

    if guest_pass.valid_until < now:
        return Response({'valid': False, 'reason': 'expired'})

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

    def _export_filter_queryset(self, request, qs):
        """Apply filter/search backends, stripping company_id for non-superadmin users."""
        filter_params = request.query_params.copy()
        if request.user.role != 'superadmin':
            filter_params.pop('company_id', None)

        if self.filterset_class is not None:
            filterset = self.filterset_class(filter_params, queryset=qs, request=request)
            if filterset.is_valid():
                qs = filterset.qs

        from rest_framework.filters import SearchFilter, OrderingFilter
        for backend_class in self.filter_backends:
            if backend_class in (SearchFilter, OrderingFilter):
                qs = backend_class().filter_queryset(request, qs, self)

        return qs

    _EXPORT_HEADER_KEYS = [
        'access.csv.guest_name',
        'access.csv.guest_email',
        'access.csv.company',
        'access.csv.invited_by',
        'access.csv.validated_by',
        'access.csv.validated_at',
        'access.csv.method',
    ]

    @extend_schema(
        tags=['Access'],
        summary='Export access logs as CSV',
        parameters=[
            OpenApiParameter('date_from', str, description='From date (YYYY-MM-DD)'),
            OpenApiParameter('date_to', str, description='To date (YYYY-MM-DD)'),
            OpenApiParameter('company_id', int, description='Filter by company (superadmin only)'),
            OpenApiParameter('lang', str, description='Language for headers (default: ru)'),
        ],
        responses={200: OpenApiResponse(description='CSV file download')},
    )
    @action(detail=False, methods=['get'], url_path='export')
    def export(self, request):
        # Apply all standard filter backends via filter_queryset, but first
        # strip company_id for non-superadmin users — their queryset is
        # already scoped to their own company and adding a company_id from
        # another company would wrongly return zero rows.
        qs = self.get_queryset().order_by('-created_at')
        qs = self._export_filter_queryset(request, qs)

        today_str = timezone.localdate().strftime('%Y-%m-%d')
        filename = f'access_logs_{today_str}.csv'
        lang = get_lang(request)
        headers = [translate(key, lang) for key in self._EXPORT_HEADER_KEYS]

        def _iter_rows():
            # UTF-8 BOM so Excel recognises Cyrillic correctly
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(headers)
            yield '﻿' + buf.getvalue()

            for log in qs.iterator():
                gp = log.guest_pass
                if gp:
                    guest_name = gp.guest_name
                    guest_email = gp.guest_email
                    company_name = gp.company.name if gp.company else ''
                else:
                    guest_name = ''
                    guest_email = ''
                    company_name = ''

                invited_by = ''
                if gp and gp.created_by:
                    invited_by = gp.created_by.full_name

                validated_by = log.checked_by.full_name if log.checked_by else ''
                validated_at = (
                    log.created_at.strftime('%d.%m.%Y %H:%M') if log.created_at else ''
                )

                row_buf = io.StringIO()
                row_writer = csv.writer(row_buf)
                row_writer.writerow([
                    guest_name,
                    guest_email,
                    company_name,
                    invited_by,
                    validated_by,
                    validated_at,
                    log.method or '',
                ])
                yield row_buf.getvalue()

        response = StreamingHttpResponse(_iter_rows(), content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
