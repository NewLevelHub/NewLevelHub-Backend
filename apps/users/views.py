from datetime import timedelta

import logging

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.generics import ListAPIView, RetrieveUpdateAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from django.core.cache import cache
from django.shortcuts import get_object_or_404
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from django.utils import timezone
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse, inline_serializer
import rest_framework.fields as fields

from apps.core.permissions import IsSuperAdmin
from .models import EmailVerificationToken, PasswordResetToken, User
from .serializers import (
    UserRegistrationSerializer,
    InviteRegistrationSerializer,
    LoginSerializer,
    UserProfileSerializer,
    UserProfileUpdateSerializer,
    ChangePasswordSerializer,
    PasswordResetRequestSerializer,
    PasswordResetConfirmSerializer,
    EmailVerifySerializer,
    UserListSerializer,
)
from .throttles import PasswordResetRateThrottle

logger = logging.getLogger(__name__)
from .tasks import send_verification_email, create_email_verification_token


def _get_tokens(user):
    refresh = RefreshToken.for_user(user)
    return {'access': str(refresh.access_token), 'refresh': str(refresh)}


# ── Auth ──────────────────────────────────────────────────────────────

@extend_schema(
    tags=['Auth'],
    summary='Register guest account',
    request=UserRegistrationSerializer,
    responses={
        201: OpenApiResponse(
            response=UserProfileSerializer,
            description='User created. Returns profile and JWT tokens.',
        ),
        400: OpenApiResponse(description='Validation error'),
    },
)
@api_view(['POST'])
@permission_classes([AllowAny])
def register(request):
    serializer = UserRegistrationSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    user = serializer.save()
    token = create_email_verification_token(user)
    send_verification_email.delay(user.id, str(token.token))
    return Response(
        {'user': UserProfileSerializer(user).data, 'tokens': _get_tokens(user)},
        status=status.HTTP_201_CREATED,
    )


@extend_schema(
    tags=['Auth'],
    summary='Register via invite link',
    request=InviteRegistrationSerializer,
    responses={
        201: OpenApiResponse(
            response=UserProfileSerializer,
            description='User registered via invite. Returns profile and JWT tokens.',
        ),
        400: OpenApiResponse(description='Validation error or invalid invite token'),
    },
)
@api_view(['POST'])
@permission_classes([AllowAny])
def register_by_invite(request):
    serializer = InviteRegistrationSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    user = serializer.save()
    # TODO: привязка к компании из инвайта, email верификация
    return Response(
        {'user': UserProfileSerializer(user).data, 'tokens': _get_tokens(user)},
        status=status.HTTP_201_CREATED,
    )


@extend_schema(
    tags=['Auth'],
    summary='Login',
    request=LoginSerializer,
    responses={
        200: OpenApiResponse(
            response=UserProfileSerializer,
            description='Login successful. Returns profile and JWT tokens (access + refresh).',
        ),
        400: OpenApiResponse(description='Invalid credentials or inactive account'),
    },
)
@api_view(['POST'])
@permission_classes([AllowAny])
def login(request):
    serializer = LoginSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    user = serializer.validated_data['user']
    return Response({'user': UserProfileSerializer(user).data, 'tokens': _get_tokens(user)})


@extend_schema(
    tags=['Auth'],
    summary='Logout (blacklist refresh token)',
    request=inline_serializer(
        name='LogoutRequest',
        fields={'refresh': fields.CharField()},
    ),
    responses={
        200: OpenApiResponse(description='Logged out successfully'),
        400: OpenApiResponse(description='Missing or invalid refresh token'),
        401: OpenApiResponse(description='Not authenticated'),
    },
)
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def logout(request):
    refresh_token = request.data.get('refresh')
    if not refresh_token:
        return Response({'detail': 'Refresh token is required'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        token = RefreshToken(refresh_token)
        token.blacklist()
    except Exception:
        return Response({'detail': 'Invalid token'}, status=status.HTTP_400_BAD_REQUEST)
    return Response({'detail': 'Logged out'})


@extend_schema(
    tags=['Auth'],
    summary='Verify email',
    request=None,
    parameters=[
        OpenApiParameter(
            name='token',
            type=str,
            location=OpenApiParameter.QUERY,
            required=True,
            description='Email verification token (UUID)',
        ),
    ],
    responses={
        200: OpenApiResponse(description='Email verified successfully'),
        400: OpenApiResponse(description='Invalid or expired token'),
        404: OpenApiResponse(description='Token not found'),
    },
)
@api_view(['GET'])
@permission_classes([AllowAny])
def verify_email(request):
    serializer = EmailVerifySerializer(data=request.query_params)
    serializer.is_valid(raise_exception=True)
    verification_token = get_object_or_404(
        EmailVerificationToken.objects.select_related('user'),
        token=serializer.validated_data['token'],
    )

    if verification_token.is_used:
        return Response({'detail': 'Token already used'}, status=status.HTTP_400_BAD_REQUEST)
    if verification_token.is_expired:
        return Response({'detail': 'Token expired'}, status=status.HTTP_400_BAD_REQUEST)

    user = verification_token.user
    user.is_email_verified = True
    user.save(update_fields=['is_email_verified'])
    verification_token.is_used = True
    verification_token.save(update_fields=['is_used'])
    return Response({'detail': 'Email verified'})


@extend_schema(
    tags=['Auth'],
    summary='Resend verification email',
    request=None,
    responses={
        200: OpenApiResponse(description='Verification email sent'),
        403: OpenApiResponse(description='Email already verified'),
        429: OpenApiResponse(description='Rate limit exceeded'),
    },
)
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def resend_verification_email(request):
    if request.user.is_email_verified:
        return Response({'detail': 'Email already verified'}, status=status.HTTP_403_FORBIDDEN)

    throttle_key = f'email_resend:{request.user.id}'
    resend_count = cache.get(throttle_key, 0)
    if resend_count >= 3:
        return Response({'detail': 'Too many requests'}, status=status.HTTP_429_TOO_MANY_REQUESTS)

    if resend_count == 0:
        cache.set(throttle_key, 1, timeout=600)
    else:
        cache.incr(throttle_key)

    token = create_email_verification_token(request.user, invalidate_existing=True)
    send_verification_email.delay(request.user.id, str(token.token))
    return Response({'detail': 'Verification email sent'})


@extend_schema(
    tags=['Auth'],
    summary='Request password reset',
    request=PasswordResetRequestSerializer,
    responses={
        200: OpenApiResponse(description='Reset link sent if account exists'),
        400: OpenApiResponse(description='Validation error'),
        429: OpenApiResponse(description='Too many requests'),
    },
)
@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([PasswordResetRateThrottle])
def password_reset_request(request):
    from .tasks import send_password_reset_email

    serializer = PasswordResetRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    email = serializer.validated_data['email']

    try:
        user = User.objects.get(email=email, is_active=True)
    except User.DoesNotExist:
        # Anti-enumeration: always return 200 regardless of whether the account exists.
        return Response({'detail': 'If an account exists, a reset link has been sent'})

    token = PasswordResetToken.objects.create(
        user=user,
        expires_at=timezone.now() + timedelta(hours=1),
    )

    try:
        send_password_reset_email.delay(token.id)
        logger.info(
            'password_reset_request: enqueued send_password_reset_email '
            'for token_id=%s user=%s',
            token.id,
            user.email,
        )
    except Exception:
        # Broker unavailable (e.g. Redis not reachable). The token is already
        # persisted; log the failure and continue so the HTTP response is still
        # 200. A monitoring alert or dead-letter queue should handle retries.
        logger.error(
            'password_reset_request: failed to enqueue send_password_reset_email '
            'for token_id=%s — broker may be unreachable',
            token.id,
            exc_info=True,
        )

    return Response({'detail': 'If an account exists, a reset link has been sent'})


@extend_schema(
    tags=['Auth'],
    summary='Confirm password reset',
    request=PasswordResetConfirmSerializer,
    responses={
        200: OpenApiResponse(description='Password has been reset'),
        400: OpenApiResponse(description='Invalid or expired token'),
    },
)
@api_view(['POST'])
@permission_classes([AllowAny])
def password_reset_confirm(request):
    serializer = PasswordResetConfirmSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    token_value = serializer.validated_data['token']
    new_password = serializer.validated_data['new_password']

    try:
        reset_token = PasswordResetToken.objects.select_related('user').get(token=token_value)
    except PasswordResetToken.DoesNotExist:
        return Response({'detail': 'Invalid token'}, status=status.HTTP_400_BAD_REQUEST)

    user = reset_token.user

    if not user.is_active:
        return Response({'detail': 'Invalid token'}, status=status.HTTP_400_BAD_REQUEST)

    if reset_token.is_used:
        return Response({'detail': 'Token already used'}, status=status.HTTP_400_BAD_REQUEST)

    if reset_token.is_expired:
        return Response({'detail': 'Token expired'}, status=status.HTTP_400_BAD_REQUEST)

    # Atomic update to prevent race condition: only proceeds if the token is still unused.
    updated = PasswordResetToken.objects.filter(token=token_value, is_used=False).update(is_used=True)
    if updated == 0:
        return Response({'detail': 'Token already used'}, status=status.HTTP_400_BAD_REQUEST)

    user.set_password(new_password)
    user.save(update_fields=['password'])

    # Bulk-blacklist all outstanding refresh tokens for this user without N+1 queries.
    outstanding = OutstandingToken.objects.filter(user=user).exclude(
        blacklistedtoken__isnull=False
    )
    BlacklistedToken.objects.bulk_create(
        [BlacklistedToken(token=t) for t in outstanding],
        ignore_conflicts=True,
    )

    return Response({'detail': 'Password has been reset'})


# ── Profile ───────────────────────────────────────────────────────────

@extend_schema(
    tags=['Users'],
    summary='Get current user profile',
    responses={
        200: UserProfileSerializer,
        401: OpenApiResponse(description='Not authenticated'),
    },
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def me(request):
    return Response(UserProfileSerializer(request.user).data)


@extend_schema(
    tags=['Users'],
    summary='Update current user profile',
    request=UserProfileUpdateSerializer,
    responses={
        200: UserProfileSerializer,
        400: OpenApiResponse(description='Validation error'),
        401: OpenApiResponse(description='Not authenticated'),
    },
)
@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def update_profile(request):
    serializer = UserProfileUpdateSerializer(request.user, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(UserProfileSerializer(request.user).data)


@extend_schema(
    tags=['Users'],
    summary='Change password',
    request=ChangePasswordSerializer,
    responses={
        200: OpenApiResponse(description='Password changed successfully'),
        400: OpenApiResponse(description='Current password incorrect or validation error'),
        401: OpenApiResponse(description='Not authenticated'),
    },
)
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def change_password(request):
    serializer = ChangePasswordSerializer(data=request.data, context={'request': request})
    serializer.is_valid(raise_exception=True)
    request.user.set_password(serializer.validated_data['new_password'])
    request.user.save(update_fields=['password'])
    return Response({'detail': 'Password changed'})


# ── Admin: user management ────────────────────────────────────────────

@extend_schema(
    tags=['Users'],
    summary='List all users (superadmin)',
    responses={
        200: UserListSerializer(many=True),
        401: OpenApiResponse(description='Not authenticated'),
        403: OpenApiResponse(description='Superadmin only'),
    },
)
class UserListView(ListAPIView):
    queryset = User.objects.all()
    serializer_class = UserListSerializer
    permission_classes = [IsSuperAdmin]
    filterset_fields = ['role', 'is_active', 'company']
    search_fields = ['email', 'first_name', 'last_name']
    ordering_fields = ['date_joined', 'last_login', 'email']


@extend_schema(
    tags=['Users'],
    summary='Get / update user by id (superadmin)',
    responses={
        200: UserProfileSerializer,
        401: OpenApiResponse(description='Not authenticated'),
        403: OpenApiResponse(description='Superadmin only'),
        404: OpenApiResponse(description='User not found'),
    },
)
class UserDetailView(RetrieveUpdateAPIView):
    queryset = User.objects.all()
    serializer_class = UserProfileSerializer
    permission_classes = [IsSuperAdmin]
    lookup_field = 'pk'
