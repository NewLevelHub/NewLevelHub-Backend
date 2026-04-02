from rest_framework import viewsets, status
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, extend_schema_view

from apps.core.permissions import IsSuperAdmin
from .models import GuestPass, AccessLog
from .serializers import (
    GuestPassSerializer, GuestPassCreateSerializer, GuestPassValidateSerializer, AccessLogSerializer,
)


@extend_schema_view(
    list=extend_schema(tags=['Access'], summary='List guest passes'),
    retrieve=extend_schema(tags=['Access'], summary='Get guest pass details'),
    create=extend_schema(tags=['Access'], summary='Create guest pass'),
)
class GuestPassViewSet(viewsets.ModelViewSet):
    serializer_class = GuestPassSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post']
    filterset_fields = ['status']

    def get_queryset(self):
        user = self.request.user
        if user.role == 'superadmin':
            return GuestPass.objects.all()
        if user.role == 'company_admin' and user.company_id:
            return GuestPass.objects.filter(company=user.company)
        return GuestPass.objects.filter(created_by=user)

    def get_serializer_class(self):
        if self.action == 'create':
            return GuestPassCreateSerializer
        return GuestPassSerializer

    @extend_schema(tags=['Access'], summary='Revoke guest pass')
    @action(detail=True, methods=['post'], url_path='revoke')
    def revoke(self, request, pk=None):
        guest_pass = self.get_object()
        guest_pass.status = 'revoked'
        guest_pass.save(update_fields=['status'])
        return Response({'detail': 'Pass revoked'})

    @extend_schema(tags=['Access'], summary='Resend QR to guest email')
    @action(detail=True, methods=['post'], url_path='resend')
    def resend(self, request, pk=None):
        # TODO: отправить QR-код повторно на email гостя
        return Response({'detail': 'QR code resent'})


@extend_schema(tags=['Access'], summary='Validate QR code (reception desk)')
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
    list=extend_schema(tags=['Access'], summary='List access logs'),
    create=extend_schema(tags=['Access'], summary='Create manual access log entry'),
)
class AccessLogViewSet(viewsets.ModelViewSet):
    serializer_class = AccessLogSerializer
    permission_classes = [IsSuperAdmin]
    http_method_names = ['get', 'post']
    queryset = AccessLog.objects.all()
    filterset_fields = ['method', 'is_entry']
