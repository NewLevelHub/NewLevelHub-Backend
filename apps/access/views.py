from rest_framework import viewsets, status
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiResponse, inline_serializer
import rest_framework.fields as fields

from apps.core.permissions import IsSuperAdmin, IsCompanyAdmin
from apps.core.mixins import CompanyIsolationMixin, SetCompanyOnCreateMixin
from .models import GuestPass, AccessLog
from .serializers import (
    GuestPassSerializer, GuestPassCreateSerializer, GuestPassValidateSerializer, AccessLogSerializer,
)


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
    queryset = GuestPass.objects.all()
    http_method_names = ['get', 'post']
    filterset_fields = ['status']

    def get_serializer_class(self):
        if self.action == 'create':
            return GuestPassCreateSerializer
        return GuestPassSerializer

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
        # TODO: отправить QR-код повторно на email гостя
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

    # Notify the pass creator via email
    from apps.notifications.tasks import send_notification_email
    from django.utils import timezone as tz
    send_notification_email.delay(
        guest_pass.created_by.id,
        'guest_validated',
        {
            'subject': 'Гостевой пропуск подтверждён',
            'guest_name': guest_pass.guest_name,
            'visit_purpose': guest_pass.visit_purpose,
            'validated_at': tz.now().strftime('%Y-%m-%d %H:%M'),
            'action_url': '/access/',
        },
    )

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
