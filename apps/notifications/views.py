from rest_framework import viewsets
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.utils import timezone
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiResponse

from .models import Notification, NotificationPreference
from .serializers import NotificationSerializer, NotificationPreferenceSerializer, UnreadCountSerializer


@extend_schema_view(
    list=extend_schema(
        tags=['Notifications'],
        summary='List notifications',
        responses={200: NotificationSerializer(many=True)},
    ),
    retrieve=extend_schema(
        tags=['Notifications'],
        summary='Get notification',
        responses={200: NotificationSerializer, 404: OpenApiResponse(description='Not found')},
    ),
)
class NotificationViewSet(viewsets.ModelViewSet):
    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'patch', 'delete']
    filterset_fields = ['is_read', 'notification_type']

    def get_queryset(self):
        return Notification.objects.filter(user=self.request.user)

    @extend_schema(
        tags=['Notifications'],
        summary='Mark notification as read',
        request=None,
        responses={
            200: NotificationSerializer,
            401: OpenApiResponse(description='Not authenticated'),
            404: OpenApiResponse(description='Not found'),
        },
    )
    @action(detail=True, methods=['post'], url_path='read')
    def mark_read(self, request, pk=None):
        notif = self.get_object()
        notif.is_read = True
        notif.read_at = timezone.now()
        notif.save(update_fields=['is_read', 'read_at'])
        return Response(NotificationSerializer(notif).data)

    @extend_schema(
        tags=['Notifications'],
        summary='Mark all notifications as read',
        request=None,
        responses={
            200: OpenApiResponse(description='All marked as read'),
            401: OpenApiResponse(description='Not authenticated'),
        },
    )
    @action(detail=False, methods=['post'], url_path='read-all')
    def mark_all_read(self, request):
        Notification.objects.filter(user=request.user, is_read=False).update(
            is_read=True, read_at=timezone.now(),
        )
        return Response({'detail': 'All marked as read'})

    @extend_schema(
        tags=['Notifications'],
        summary='Unread notifications count',
        responses={
            200: UnreadCountSerializer,
            401: OpenApiResponse(description='Not authenticated'),
        },
    )
    @action(detail=False, methods=['get'], url_path='unread-count')
    def unread_count(self, request):
        count = Notification.objects.filter(user=request.user, is_read=False).count()
        return Response(UnreadCountSerializer({'count': count}).data)


@extend_schema(
    tags=['Notifications'],
    summary='Get or update notification preferences',
    request=NotificationPreferenceSerializer,
    responses={
        200: NotificationPreferenceSerializer,
        400: OpenApiResponse(description='Validation error'),
        401: OpenApiResponse(description='Not authenticated'),
    },
)
@api_view(['GET', 'PATCH'])
@permission_classes([IsAuthenticated])
def notification_preferences(request):
    prefs, _ = NotificationPreference.objects.get_or_create(user=request.user)
    if request.method == 'PATCH':
        serializer = NotificationPreferenceSerializer(prefs, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
    return Response(NotificationPreferenceSerializer(prefs).data)
