from rest_framework import viewsets, status
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiResponse, inline_serializer
import rest_framework.fields as fields
from django.utils import timezone

from apps.core.permissions import IsSuperAdmin, IsCompanyAdmin, IsCompanyMember
from apps.core.mixins import CompanyIsolationMixin, SetCompanyOnCreateMixin
from .models import GuestPass, AccessLog
from .tasks import notify_pass_creator_on_entry
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
    queryset = GuestPass.objects.select_related('created_by', 'company').order_by('-created_at')
    http_method_names = ['get', 'post']
    filterset_fields = ['status']

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
        403: OpenApiResponse(description='Only superadmin and reception can validate'),
    },
)
@api_view(['POST'])
@permission_classes([IsSuperAdminOrReception])
def validate_qr(request):
    serializer = GuestPassValidateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        guest_pass = GuestPass.objects.get(qr_code=serializer.validated_data['qr_code'])
    except GuestPass.DoesNotExist:
        return Response({'valid': False, 'reason': 'not_found'})

    if guest_pass.valid_until < timezone.now():
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
