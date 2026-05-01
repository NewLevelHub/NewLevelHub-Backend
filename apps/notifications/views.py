from rest_framework import viewsets, mixins
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.shortcuts import redirect
from django.utils import timezone
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiParameter, OpenApiResponse

from apps.core.permissions import IsOwnerOrAdmin

from .models import Notification, NotificationPreference
from .serializers import (
    NotificationSerializer,
    NotificationPreferenceDictSerializer,
    DNDSerializer,
    DNDResponseSerializer,
    UnreadCountSerializer,
)

User = get_user_model()


@extend_schema_view(
    list=extend_schema(
        tags=['Notifications'],
        summary='List my notifications',
        description=(
            'Returns a paginated list of notifications for the authenticated user. '
            'Filter by `is_read` or `notification_type`. Ordered by `-created_at`.'
        ),
        responses={200: NotificationSerializer(many=True)},
    ),
    destroy=extend_schema(
        tags=['Notifications'],
        summary='Delete a notification',
        responses={
            204: OpenApiResponse(description='Deleted'),
            401: OpenApiResponse(description='Not authenticated'),
            404: OpenApiResponse(description='Not found'),
        },
    ),
)
class NotificationViewSet(
    mixins.ListModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """
    Notification endpoints for the authenticated user.

    - GET  /notifications/            — list (paginated, filterable)
    - GET  /notifications/unread-count/ — count of unread
    - POST /notifications/{id}/read/  — mark single as read
    - POST /notifications/read-all/   — mark all as read
    - DELETE /notifications/{id}/     — delete single
    """

    serializer_class = NotificationSerializer
    permission_classes = [IsOwnerOrAdmin]
    filterset_fields = ['is_read', 'notification_type']
    ordering = ['-created_at']

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
        if not notif.is_read:
            notif.is_read = True
            notif.read_at = timezone.now()
            notif.save(update_fields=['is_read', 'read_at'])
        return Response(NotificationSerializer(notif, context={'request': request}).data)

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
    description=(
        'GET returns the current DND status (`dnd_enabled`, `dnd_until`) plus a dict keyed by each '
        'notification type with `in_app` and `email` booleans. '
        'PATCH accepts a partial dict of notification-type keys — only the keys sent are updated. '
        'The `dnd_enabled` and `dnd_until` fields are read-only here; use '
        'POST /api/v1/notifications/do-not-disturb/ to change DND settings. '
        'The preference record is created with all defaults=true on first access.'
    ),
    request=NotificationPreferenceDictSerializer,
    responses={
        200: OpenApiResponse(
            description=(
                'Notification preferences. Top-level `dnd_enabled` (bool) and `dnd_until` '
                '(datetime|null) show current DND status. Remaining keys are notification types, '
                'each with `in_app` and `email` booleans.'
            )
        ),
        400: OpenApiResponse(description='Validation error'),
        401: OpenApiResponse(description='Not authenticated'),
    },
)
@api_view(['GET', 'PATCH'])
@permission_classes([IsAuthenticated])
def notification_preferences(request):
    prefs, _ = NotificationPreference.objects.get_or_create(user=request.user)
    if request.method == 'PATCH':
        serializer = NotificationPreferenceDictSerializer(prefs, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        prefs = serializer.save()
    return Response(NotificationPreferenceDictSerializer(prefs).data)


@extend_schema(
    tags=['Notifications'],
    summary='Set Do-Not-Disturb',
    description=(
        'Enables or disables Do-Not-Disturb mode. '
        'When DND is active, `create_notification` will skip creating in-app notifications. '
        '`until` is optional — omit or pass null for indefinite DND.'
    ),
    request=DNDSerializer,
    responses={
        200: DNDResponseSerializer,
        400: OpenApiResponse(description='Validation error'),
        401: OpenApiResponse(description='Not authenticated'),
    },
)
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def do_not_disturb(request):
    prefs, _ = NotificationPreference.objects.get_or_create(user=request.user)
    serializer = DNDSerializer(prefs, data=request.data)
    serializer.is_valid(raise_exception=True)
    prefs = serializer.save()
    return Response(DNDResponseSerializer({'dnd_enabled': prefs.dnd_enabled, 'dnd_until': prefs.dnd_until}).data)


@extend_schema(
    tags=['Notifications'],
    summary='Unsubscribe from email notifications',
    description=(
        'Disables all email notification preferences for the user identified by the signed token. '
        'The token is included in every notification email as an unsubscribe link. '
        'This endpoint is unauthenticated — it is designed to be called directly from an email link. '
        'On success, redirects to {FRONTEND_URL}/unsubscribe/success. '
        'On invalid or expired token, redirects to {FRONTEND_URL}/unsubscribe/invalid.'
    ),
    parameters=[
        OpenApiParameter(
            name='token',
            location=OpenApiParameter.QUERY,
            description='Signed token generated by Django signing (salt=notification-unsubscribe)',
            required=True,
            type=str,
        ),
    ],
    responses={
        302: OpenApiResponse(description='Redirect to frontend success or invalid page'),
    },
)
@api_view(['GET'])
@permission_classes([AllowAny])
def unsubscribe(request):
    """
    Disable all email notifications for the user identified by the signed token.

    BUG-3 fix: every notification email now includes a link pointing here.
    The token is produced by ``utils._build_unsubscribe_url`` using
    ``django.core.signing`` with salt='notification-unsubscribe'.

    On success redirects to {FRONTEND_URL}/unsubscribe/success.
    On invalid/expired token redirects to {FRONTEND_URL}/unsubscribe/invalid.
    """
    frontend_url = settings.FRONTEND_URL.rstrip('/')
    invalid_url = f'{frontend_url}/unsubscribe/invalid'
    success_url = f'{frontend_url}/unsubscribe/success'

    token = request.query_params.get('token')
    if not token:
        return redirect(invalid_url)

    try:
        data = signing.loads(token, salt='notification-unsubscribe', max_age=86400 * 30)  # 30 days
        user_id = data['user_id']
    except (signing.SignatureExpired, signing.BadSignature, KeyError, TypeError):
        return redirect(invalid_url)

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return redirect(invalid_url)

    prefs, _ = NotificationPreference.objects.get_or_create(user=user)

    # Disable every email preference field in NOTIFICATION_TYPE_FIELD_MAP.
    from .serializers import NOTIFICATION_TYPE_FIELD_MAP
    email_fields = [email_field for _, email_field in NOTIFICATION_TYPE_FIELD_MAP.values()]
    for field in email_fields:
        setattr(prefs, field, False)
    prefs.save(update_fields=email_fields)

    return redirect(success_url)
