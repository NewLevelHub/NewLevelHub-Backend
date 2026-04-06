import uuid as _uuid

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.generics import ListAPIView, RetrieveUpdateAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse, inline_serializer
import rest_framework.fields as fields

from apps.core.permissions import IsSuperAdmin
from .models import User, EmailVerificationToken
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
from .throttles import EmailResendThrottle


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

    from .tasks import send_verification_email
    send_verification_email.delay(user.id)

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
    parameters=[
        OpenApiParameter(name='token', type=str, location=OpenApiParameter.QUERY, required=True),
    ],
    responses={
        200: OpenApiResponse(description='Email verified successfully'),
        400: OpenApiResponse(description='Token already used or expired'),
        404: OpenApiResponse(description='Token not found'),
    },
)
@api_view(['GET'])
@permission_classes([AllowAny])
def verify_email(request):
    raw_token = request.query_params.get('token')
    if not raw_token:
        return Response({'detail': 'Token is required'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        _uuid.UUID(str(raw_token))
    except (ValueError, AttributeError):
        return Response({'detail': 'Invalid token format'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        token = EmailVerificationToken.objects.select_related('user').get(token=raw_token)
    except EmailVerificationToken.DoesNotExist:
        return Response({'detail': 'Token not found'}, status=status.HTTP_404_NOT_FOUND)

    if token.is_used:
        return Response({'detail': 'Token already used'}, status=status.HTTP_400_BAD_REQUEST)

    if token.is_expired:
        return Response({'detail': 'Token expired'}, status=status.HTTP_400_BAD_REQUEST)

    token.is_used = True
    token.save(update_fields=['is_used'])

    token.user.is_email_verified = True
    token.user.save(update_fields=['is_email_verified'])

    return Response({'detail': 'Email verified'})


@extend_schema(
    tags=['Auth'],
    summary='Resend verification email',
    request=None,
    responses={
        200: OpenApiResponse(description='Verification email sent'),
        400: OpenApiResponse(description='Email already verified'),
        401: OpenApiResponse(description='Not authenticated'),
        429: OpenApiResponse(description='Rate limit exceeded'),
    },
)
@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([EmailResendThrottle])
def resend_verification_email(request):
    user = request.user
    if user.is_email_verified:
        return Response({'detail': 'Email already verified'}, status=status.HTTP_400_BAD_REQUEST)

    from .tasks import send_verification_email
    send_verification_email.delay(user.id)

    return Response({'detail': 'Verification email sent'})


@extend_schema(
    tags=['Auth'],
    summary='Request password reset',
    request=PasswordResetRequestSerializer,
    responses={
        200: OpenApiResponse(description='Reset link sent if account exists'),
        400: OpenApiResponse(description='Validation error'),
    },
)
@api_view(['POST'])
@permission_classes([AllowAny])
def password_reset_request(request):
    serializer = PasswordResetRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    # TODO: создать PasswordResetToken, отправить email
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
    # TODO: валидация токена, смена пароля
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
