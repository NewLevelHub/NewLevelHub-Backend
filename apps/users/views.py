from datetime import timedelta

import logging
import uuid

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, parser_classes, throttle_classes
from rest_framework.exceptions import ValidationError
from rest_framework.generics import ListAPIView, RetrieveDestroyAPIView
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from django.core.cache import cache
from django.db.models import Count
from django.shortcuts import get_object_or_404
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from django.utils import timezone
from drf_spectacular.utils import (
    extend_schema,
    extend_schema_view,
    OpenApiParameter,
    OpenApiResponse,
    inline_serializer,
)
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
import rest_framework.fields as fields

from apps.core.error_codes import TOKEN_INVALID, TOKEN_EXPIRED, TOKEN_ALREADY_USED
from apps.core.exceptions import LocalizedError
from apps.core.i18n import translate, get_lang
from apps.core.pagination import StandardPagination
from apps.core.permissions import IsSuperAdmin
from apps.companies.invite_policy import existing_user_cannot_accept_invite_error, lookup_user_by_invite_email
from apps.companies.models import Invitation
from .filters import UserFilter
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
    UserDetailSerializer,
    _delete_file,
)
from .tasks import send_verification_email, create_email_verification_token
from .throttles import PasswordResetRateThrottle
from .jwt import (
    REFRESH_COOKIE_NAME,
    clear_refresh_cookie,
    issue_refresh_token,
    set_refresh_cookie,
)
from .session import touch_last_activity

logger = logging.getLogger(__name__)


def _get_tokens(user, remember_me=False):
    refresh = issue_refresh_token(user, remember_me=remember_me)
    return {'access': str(refresh.access_token), 'refresh': str(refresh)}


def _profile_response(user, request):
    """Return a UserProfileSerializer response with request context for avatar URL."""
    return Response(UserProfileSerializer(user, context={'request': request}).data)


def _blacklist_user_refresh_tokens(user):
    """Blacklist all outstanding refresh tokens for a user."""
    outstanding = OutstandingToken.objects.filter(user=user).exclude(
        blacklistedtoken__isnull=False
    )
    BlacklistedToken.objects.bulk_create(
        [BlacklistedToken(token=t) for t in outstanding],
        ignore_conflicts=True,
    )


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
    try:
        send_verification_email.delay(user.id, str(token.token))
    except Exception:
        logger.error(
            'register: failed to enqueue send_verification_email for user_id=%s — broker may be unreachable',
            user.id,
            exc_info=True,
        )
    tokens = _get_tokens(user, remember_me=False)
    response = Response(
        {'user': UserProfileSerializer(user, context={'request': request}).data, 'tokens': tokens},
        status=status.HTTP_201_CREATED,
    )
    set_refresh_cookie(response, tokens['refresh'], remember_me=False)
    return response


@extend_schema(
    tags=['Auth'],
    summary='Get invitation details by token',
    request=None,
    parameters=[
        OpenApiParameter(
            name='token',
            type=str,
            location=OpenApiParameter.QUERY,
            required=True,
            description='Invitation token (UUID)',
        ),
    ],
    responses={
        200: OpenApiResponse(description='Returns invitation company_name, email and role'),
        400: OpenApiResponse(
            description='Invalid/expired/used token, or invite email already registered',
        ),
    },
    methods=['GET'],
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
    methods=['POST'],
)
@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
def register_by_invite(request):
    lang = get_lang(request)
    if request.method == 'GET':
        token = request.query_params.get('token')
        if not token:
            return Response({'detail': translate('users.token_required', lang)}, status=status.HTTP_400_BAD_REQUEST)
        try:
            token = uuid.UUID(str(token))
        except (TypeError, ValueError):
            return Response(
                {'detail': translate('users.invite_invalid_or_expired', lang)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        invitation = Invitation.objects.select_related('company').filter(token=token).first()
        if not invitation or invitation.is_used or invitation.is_expired:
            return Response(
                {'detail': translate('users.invite_invalid_or_expired', lang)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        existing = lookup_user_by_invite_email(invitation.email)
        is_guest_upgrade = False
        if existing:
            err = existing_user_cannot_accept_invite_error(existing, invitation)
            if err:
                raise ValidationError({'email': err})
            is_guest_upgrade = (existing.role == 'guest')

        return Response(
            {
                'company_name': invitation.company.name if invitation.company_id else None,
                'email': invitation.email,
                'role': invitation.role,
                'is_guest_upgrade': is_guest_upgrade,
            }
        )

    serializer = InviteRegistrationSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    user = serializer.save()
    token = create_email_verification_token(user)
    try:
        send_verification_email.delay(user.id, str(token.token))
    except Exception:
        logger.warning(
            'register_by_invite: failed to enqueue send_verification_email for user_id=%s — broker may be unreachable',
            user.id,
        )
    return Response(
        {'detail': translate('users.registration_success', lang)},
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
    remember_me = serializer.validated_data.get('remember_me', False)
    tokens = _get_tokens(user, remember_me=remember_me)
    touch_last_activity(user)
    response = Response({'user': UserProfileSerializer(user).data, 'tokens': tokens})
    set_refresh_cookie(response, tokens['refresh'], remember_me=remember_me)
    return response
    return Response(
        {'user': UserProfileSerializer(user, context={'request': request}).data, 'tokens': _get_tokens(user)}
    )


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
    },
)
@api_view(['POST'])
@permission_classes([AllowAny])
def logout(request):
    lang = get_lang(request)
    refresh_token = request.data.get('refresh') or request.COOKIES.get(REFRESH_COOKIE_NAME)
    if not refresh_token:
        return Response({'detail': 'Refresh-токен обязателен'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        token = RefreshToken(refresh_token)
        token.blacklist()
    except Exception:
        return Response({'detail': translate('auth.token_invalid', lang)}, status=status.HTTP_400_BAD_REQUEST)
    response = Response({'detail': translate('users.logout_success', lang)})
    clear_refresh_cookie(response)
    return response


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
    lang = get_lang(request)
    serializer = EmailVerifySerializer(data=request.query_params)
    serializer.is_valid(raise_exception=True)
    verification_token = get_object_or_404(
        EmailVerificationToken.objects.select_related('user'),
        token=serializer.validated_data['token'],
    )

    if verification_token.is_used:
        return Response({'detail': translate('auth.token_already_used', lang)}, status=status.HTTP_400_BAD_REQUEST)
    if verification_token.is_expired:
        return Response({'detail': translate('auth.token_expired', lang)}, status=status.HTTP_400_BAD_REQUEST)

    user = verification_token.user
    user.is_email_verified = True
    user.save(update_fields=['is_email_verified'])
    verification_token.is_used = True
    verification_token.save(update_fields=['is_used'])
    return Response({'detail': 'Email подтверждён'})


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
    lang = get_lang(request)
    if request.user.is_email_verified:
        return Response({'detail': 'Email уже подтверждён'}, status=status.HTTP_403_FORBIDDEN)

    throttle_key = f'email_resend:{request.user.id}'
    resend_count = cache.get(throttle_key, 0)
    if resend_count >= 3:
        return Response(
            {'detail': translate('users.too_many_requests', lang)},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    if resend_count == 0:
        cache.set(throttle_key, 1, timeout=600)
    else:
        cache.incr(throttle_key)

    token = create_email_verification_token(request.user, invalidate_existing=True)
    try:
        send_verification_email.delay(request.user.id, str(token.token))
    except Exception:
        logger.error(
            'resend_email_verification: failed to enqueue send_verification_email '
            'for user_id=%s — broker may be unreachable',
            request.user.id,
            exc_info=True,
        )
    return Response({'detail': translate('users.email_confirmation_sent', lang)})


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

    lang = get_lang(request)
    try:
        user = User.objects.get(email=email, is_active=True)
    except User.DoesNotExist:
        # Anti-enumeration: always return 200 regardless of whether the account exists.
        return Response({'detail': translate('users.password_reset_email_sent', lang)})

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

    return Response({'detail': translate('users.password_reset_email_sent', lang)})


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
    lang = get_lang(request)
    serializer = PasswordResetConfirmSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    token_value = serializer.validated_data['token']
    new_password = serializer.validated_data['new_password']

    try:
        reset_token = PasswordResetToken.objects.select_related('user').get(token=token_value)
    except PasswordResetToken.DoesNotExist:
        raise LocalizedError(code=TOKEN_INVALID, i18n_key='auth.token_invalid')

    user = reset_token.user

    if not user.is_active:
        raise LocalizedError(code=TOKEN_INVALID, i18n_key='auth.token_invalid')

    if reset_token.is_used:
        raise LocalizedError(code=TOKEN_ALREADY_USED, i18n_key='auth.token_already_used')

    if reset_token.is_expired:
        raise LocalizedError(code=TOKEN_EXPIRED, i18n_key='auth.token_expired')

    # Atomic update to prevent race condition: only proceeds if the token is still unused.
    updated = PasswordResetToken.objects.filter(token=token_value, is_used=False).update(is_used=True)
    if updated == 0:
        raise LocalizedError(code=TOKEN_ALREADY_USED, i18n_key='auth.token_already_used')

    user.set_password(new_password)
    user.save(update_fields=['password'])

    _blacklist_user_refresh_tokens(user)

    return Response({'detail': translate('users.password_reset_success', lang)})


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
    return _profile_response(request.user, request)


@extend_schema(
    tags=['Users'],
    summary='Update current user profile (multipart/form-data)',
    description=(
        'Updates writable profile fields. '
        'Send as multipart/form-data when uploading an avatar. '
        'Allowed avatar formats: JPEG, PNG, WebP; max 5 MB. '
        'Avatar is resized to 400×400 px server-side. '
        'Fields email, role, and company are ignored even if supplied.'
    ),
    request={'multipart/form-data': UserProfileUpdateSerializer},
    responses={
        200: UserProfileSerializer,
        400: OpenApiResponse(description='Validation error (bad file type, size, etc.)'),
        401: OpenApiResponse(description='Not authenticated'),
    },
)
@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser, JSONParser])
def update_profile(request):
    serializer = UserProfileUpdateSerializer(request.user, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    # Re-fetch to ensure avatar field reflects the saved path.
    request.user.refresh_from_db()
    return _profile_response(request.user, request)


@extend_schema(
    tags=['Users'],
    summary='Delete current user avatar',
    description='Removes the avatar file from storage and sets avatar=null on the user profile.',
    responses={
        200: UserProfileSerializer,
        401: OpenApiResponse(description='Not authenticated'),
    },
)
@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_avatar(request):
    user = request.user
    _delete_file(user.avatar)
    user.avatar = None
    user.save(update_fields=['avatar'])
    return _profile_response(user, request)


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
    parameters=[
        OpenApiParameter(name='role', type=str,
                         description='Filter by role (superadmin, company_admin, employee, guest)'),
        OpenApiParameter(name='company_id', type=int, description='Filter by company ID'),
        OpenApiParameter(name='is_active', type=bool, description='Filter by active status'),
        OpenApiParameter(name='search', type=str, description='Search by email, first_name, last_name'),
        OpenApiParameter(name='ordering', type=str, description='Order by date_joined or last_login'),
    ],
    responses={
        200: UserListSerializer(many=True),
        401: OpenApiResponse(description='Not authenticated'),
        403: OpenApiResponse(description='Superadmin only'),
    },
)
class UserListView(ListAPIView):
    queryset = User.objects.select_related('company').all()
    serializer_class = UserListSerializer
    permission_classes = [IsSuperAdmin]
    pagination_class = StandardPagination
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = UserFilter
    search_fields = ['email', 'first_name', 'last_name']
    ordering_fields = ['date_joined', 'last_login']
    ordering = ['-date_joined']


@extend_schema(
    tags=['Users'],
    summary='Impersonate user (superadmin)',
    description=(
        'Issues JWT tokens on behalf of the target user for support/debug. '
        'The returned access token contains an `impersonated_by` claim with '
        'the superadmin\'s ID for audit purposes. Cannot impersonate self, '
        'another superadmin, or an inactive user.'
    ),
    request=None,
    responses={
        200: OpenApiResponse(description='Returns access, refresh, and target user data.'),
        400: OpenApiResponse(description='Cannot impersonate self, another superadmin, or inactive user.'),
        401: OpenApiResponse(description='Not authenticated'),
        403: OpenApiResponse(description='Superadmin only'),
        404: OpenApiResponse(description='User not found'),
    },
)
@api_view(['POST'])
@permission_classes([IsSuperAdmin])
def impersonate_user(request, id):
    target = get_object_or_404(User, pk=id)

    if target.id == request.user.id:
        return Response(
            {'detail': 'Cannot impersonate yourself'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if target.role == 'superadmin':
        return Response(
            {'detail': 'Cannot impersonate another superadmin'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not target.is_active:
        return Response(
            {'detail': 'Cannot impersonate an inactive user'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    refresh = RefreshToken.for_user(target)
    access = refresh.access_token
    access['impersonated_by'] = request.user.id

    logger.info(
        'impersonation: superadmin_id=%s impersonating user_id=%s (%s)',
        request.user.id,
        target.id,
        target.email,
    )

    return Response({
        'access': str(access),
        'refresh': str(refresh),
        'user': UserListSerializer(target).data,
    })


@extend_schema_view(
    get=extend_schema(
        tags=['Users'],
        summary='Get user detail by id (superadmin)',
        responses={
            200: UserDetailSerializer,
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Superadmin only'),
            404: OpenApiResponse(description='User not found'),
        },
    ),
    delete=extend_schema(
        tags=['Users'],
        summary='Delete user by id (superadmin)',
        description='Permanently deletes the user. Cannot delete self or another superadmin.',
        responses={
            204: OpenApiResponse(description='User deleted'),
            400: OpenApiResponse(description='Cannot delete self or another superadmin'),
            401: OpenApiResponse(description='Not authenticated'),
            403: OpenApiResponse(description='Superadmin only'),
            404: OpenApiResponse(description='User not found'),
        },
    ),
)
class UserDetailView(RetrieveDestroyAPIView):
    serializer_class = UserDetailSerializer
    permission_classes = [IsSuperAdmin]
    lookup_field = 'pk'

    def get_queryset(self):
        return User.objects.select_related('company').annotate(
            bookings_count=Count('bookings', distinct=True),
            tasks_count=Count('assigned_tasks', distinct=True),
        )

    def perform_destroy(self, instance):
        if instance.id == self.request.user.id:
            raise ValidationError({'detail': 'Cannot delete yourself'})
        if instance.role == 'superadmin':
            raise ValidationError({'detail': 'Cannot delete another superadmin'})
        _blacklist_user_refresh_tokens(instance)
        instance.delete()


@extend_schema(
    tags=['Users'],
    summary='Block user by id (superadmin)',
    description='Sets is_active=false and invalidates all user refresh sessions.',
    responses={
        200: OpenApiResponse(description='User blocked and sessions invalidated'),
        401: OpenApiResponse(description='Not authenticated'),
        403: OpenApiResponse(description='Superadmin only'),
        404: OpenApiResponse(description='User not found'),
    },
)
@api_view(['POST'])
@permission_classes([IsSuperAdmin])
def block_user(request, pk):
    user = get_object_or_404(User, pk=pk)
    user.is_active = False
    user.save(update_fields=['is_active'])
    _blacklist_user_refresh_tokens(user)
    return Response({'detail': 'User blocked'})


@extend_schema(
    tags=['Users'],
    summary='Unblock user by id (superadmin)',
    description='Sets is_active=true so the user can authenticate again.',
    responses={
        200: OpenApiResponse(description='User unblocked'),
        401: OpenApiResponse(description='Not authenticated'),
        403: OpenApiResponse(description='Superadmin only'),
        404: OpenApiResponse(description='User not found'),
    },
)
@api_view(['POST'])
@permission_classes([IsSuperAdmin])
def unblock_user(request, pk):
    user = get_object_or_404(User, pk=pk)
    user.is_active = True
    user.save(update_fields=['is_active'])
    return Response({'detail': 'User unblocked'})
