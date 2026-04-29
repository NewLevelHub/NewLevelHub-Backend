import logging

from django.core.cache import cache
from rest_framework import viewsets, status
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiResponse, inline_serializer
import rest_framework.fields as fields
from django_filters.rest_framework import DjangoFilterBackend

from apps.core.permissions import IsSuperAdmin, IsCompanyAdmin, IsCompanyMember
from apps.core.mixins import CompanyIsolationMixin, SetCompanyOnCreateMixin
from .filters import GuestPassFilter
from .models import GuestPass, AccessLog
from .serializers import (
    GuestPassSerializer, GuestPassCreateSerializer, GuestPassValidateSerializer, AccessLogSerializer,
)

logger = logging.getLogger(__name__)


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
    filter_backends = [DjangoFilterBackend]
    filterset_class = GuestPassFilter

    def get_permissions(self):
        if self.action in ('list', 'retrieve', 'create', 'revoke', 'resend'):
            return [IsCompanyMember()]
        return [permission() for permission in self.permission_classes]

    def get_serializer_class(self):
        if self.action == 'create':
            return GuestPassCreateSerializer
        return GuestPassSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if user.role == 'superadmin':
            return queryset
        if user.role == 'company_admin':
            if not user.company_id:
                return queryset.none()
            return queryset.filter(company_id=user.company_id)
        return queryset.filter(created_by=user)

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
            401: OpenApiResponse(description='Not authenticated'),
            404: OpenApiResponse(description='Not found'),
        },
    )
    @action(detail=True, methods=['post'], url_path='revoke')
    def revoke(self, request, pk=None):
        guest_pass = self.get_object()
        if guest_pass.status in ('used', 'expired'):
            return Response(
                {'detail': f'Cannot revoke pass with status "{guest_pass.status}"'},
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
        throttle_key = f'guest_pass_resend:{guest_pass.id}'
        resend_count = cache.get(throttle_key, 0)
        if resend_count >= 3:
            return Response({'detail': 'Too many requests'}, status=status.HTTP_429_TOO_MANY_REQUESTS)

        if resend_count == 0:
            cache.set(throttle_key, 1, timeout=3600)
        else:
            cache.incr(throttle_key)

        from .tasks import send_guest_pass_email_now

        try:
            send_guest_pass_email_now(guest_pass.id)
        except Exception:
            logger.exception('Failed to resend pass email for pass_id=%s', guest_pass.id)
            return Response({'detail': 'Failed to send QR email'}, status=status.HTTP_502_BAD_GATEWAY)
        return Response({'detail': 'QR code resent'})


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
                'visit_purpose': fields.CharField(),
                'created_by': fields.CharField(),
            },
        ),
        400: OpenApiResponse(description='Invalid or missing QR code'),
        401: OpenApiResponse(description='Not authenticated'),
        404: OpenApiResponse(description='Pass not found'),
    },
)
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def validate_qr(request):
    serializer = GuestPassValidateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        guest_pass = GuestPass.objects.get(qr_code=serializer.validated_data['qr_code'])
    except GuestPass.DoesNotExist:
        return Response({'valid': False, 'reason': 'Pass not found'}, status=status.HTTP_404_NOT_FOUND)

    if not guest_pass.is_valid:
        return Response({'valid': False, 'reason': f'Pass status: {guest_pass.status}'})

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
    # TODO: уведомить создателя пропуска
    return Response({
        'valid': True,
        'guest_name': guest_pass.guest_name,
        'visit_purpose': guest_pass.visit_purpose,
        'created_by': guest_pass.created_by.full_name,
    })


@extend_schema_view(
    list=extend_schema(
        tags=['Access'],
        summary='List access logs',
        responses={200: AccessLogSerializer(many=True), 403: OpenApiResponse(description='Superadmin only')},
    ),
    create=extend_schema(
        tags=['Access'],
        summary='Create manual access log entry',
        request=AccessLogSerializer,
        responses={
            201: AccessLogSerializer,
            400: OpenApiResponse(description='Validation error'),
            403: OpenApiResponse(description='Superadmin only'),
        },
    ),
)
class AccessLogViewSet(viewsets.ModelViewSet):
    serializer_class = AccessLogSerializer
    permission_classes = [IsSuperAdmin]
    http_method_names = ['get', 'post']
    queryset = AccessLog.objects.all()
    filterset_fields = ['method', 'is_entry']
