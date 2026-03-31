from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth import get_user_model, authenticate
from drf_spectacular.utils import extend_schema, OpenApiResponse
from .serializers import (
    UserRegistrationSerializer,
    UserLoginSerializer,
    UserSerializer,
    UserUpdateSerializer,
    UserManagementSerializer,
    PasswordResetRequestSerializer,
    MobileBiometricConfirmSerializer,
    UserRoleSerializer,
)

User = get_user_model()

ROLE_CAPABILITIES = {
    'supermentor': {
        'can_manage_users': True,
        'can_view_crm': True,
        'can_manage_booking': True,
        'can_access_iot': True,
    },
    'admin': {
        'can_manage_users': True,
        'can_view_crm': True,
        'can_manage_booking': True,
        'can_access_iot': True,
    },
    'tenant': {
        'can_manage_users': False,
        'can_view_crm': True,
        'can_manage_booking': True,
        'can_access_iot': False,
    },
    'employee': {
        'can_manage_users': False,
        'can_view_crm': True,
        'can_manage_booking': False,
        'can_access_iot': False,
    },
    'guest': {
        'can_manage_users': False,
        'can_view_crm': False,
        'can_manage_booking': False,
        'can_access_iot': False,
    },
}


def get_tokens_for_user(user):
    """Generate JWT tokens for user."""
    refresh = RefreshToken.for_user(user)
    return {
        'refresh': str(refresh),
        'access': str(refresh.access_token),
    }


@extend_schema(
    tags=['Authentication'],
    summary='User Registration',
    description='Регистрация нового пользователя с email и паролем',
    request=UserRegistrationSerializer,
    responses={
        201: OpenApiResponse(description='User created successfully'),
        400: OpenApiResponse(description='Validation error'),
    }
)
@api_view(['POST'])
@permission_classes([AllowAny])
def register_user(request):
    """
    POST /api/v1/auth/register/
    Регистрация нового пользователя.
    """
    serializer = UserRegistrationSerializer(data=request.data)
    if serializer.is_valid():
        user = serializer.save()
        tokens = get_tokens_for_user(user)
        return Response({
            'user': UserSerializer(user).data,
            'tokens': tokens,
            'message': 'User registered successfully'
        }, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    tags=['Authentication'],
    summary='User Login',
    description='Вход пользователя по email и паролю, выдача JWT токенов',
    request=UserLoginSerializer,
    responses={
        200: OpenApiResponse(description='Login successful'),
        401: OpenApiResponse(description='Invalid credentials'),
    }
)
@api_view(['POST'])
@permission_classes([AllowAny])
def login_user(request):
    """
    POST /api/v1/auth/login/
    Вход по email + пароль, возврат access + refresh токенов.
    """
    serializer = UserLoginSerializer(data=request.data)
    if serializer.is_valid():
        email = serializer.validated_data['email']
        password = serializer.validated_data['password']

        user = authenticate(request, username=email, password=password)

        if user is not None:
            if user.is_active:
                tokens = get_tokens_for_user(user)
                return Response({
                    'user': UserSerializer(user).data,
                    'tokens': tokens,
                    'message': 'Login successful'
                }, status=status.HTTP_200_OK)
            else:
                return Response({
                    'error': 'User account is disabled'
                }, status=status.HTTP_401_UNAUTHORIZED)
        else:
            return Response({
                'error': 'Invalid email or password'
            }, status=status.HTTP_401_UNAUTHORIZED)

    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    tags=['Authentication'],
    summary='Mobile Biometric Confirmation',
    description='Подтверждение биометрии на мобильном устройстве, выдача JWT токена',
    request=MobileBiometricConfirmSerializer,
    responses={
        200: OpenApiResponse(description='Biometric confirmation successful'),
        401: OpenApiResponse(description='Invalid biometric data'),
    }
)
@api_view(['POST'])
@permission_classes([AllowAny])
def mobile_biometric_confirm(request):
    """
    POST /api/v1/auth/mobile/confirm/
    Мобилка: подтверждение биометрии → выдача токена.
    """
    serializer = MobileBiometricConfirmSerializer(data=request.data)
    if serializer.is_valid():
        user_id = serializer.validated_data['user_id']
        _ = serializer.validated_data['biometric_token']

        try:
            user = User.objects.get(id=user_id, is_active=True)

            # TODO: Implement biometric validation logic
            # For now, we just check if user exists

            tokens = get_tokens_for_user(user)
            return Response({
                'user': UserSerializer(user).data,
                'tokens': tokens,
                'message': 'Biometric authentication successful'
            }, status=status.HTTP_200_OK)

        except User.DoesNotExist:
            return Response({
                'error': 'Invalid user or biometric data'
            }, status=status.HTTP_401_UNAUTHORIZED)

    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    tags=['Authentication'],
    summary='Get Current User',
    description='Получить профиль текущего авторизованного пользователя',
    responses={
        200: UserSerializer,
        401: OpenApiResponse(description='Unauthorized'),
    }
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_current_user(request):
    """
    GET /api/v1/auth/me/
    Профиль текущего авторизованного пользователя.
    """
    serializer = UserSerializer(request.user)
    return Response(serializer.data, status=status.HTTP_200_OK)


@extend_schema(
    tags=['Authentication'],
    summary='Get Current User Permissions',
    description='Получить роль и capability-флаги текущего авторизованного пользователя',
    responses={
        200: OpenApiResponse(
            description='Permissions for current user',
            response={
                'type': 'object',
                'properties': {
                    'role': {'type': 'string', 'example': 'admin'},
                    'capabilities': {
                        'type': 'object',
                        'properties': {
                            'can_manage_users': {'type': 'boolean', 'example': True},
                            'can_view_crm': {'type': 'boolean', 'example': True},
                            'can_manage_booking': {'type': 'boolean', 'example': True},
                            'can_access_iot': {'type': 'boolean', 'example': True},
                        },
                    },
                },
            },
        ),
        401: OpenApiResponse(description='Unauthorized'),
    },
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_current_user_permissions(request):
    """
    GET /api/v1/auth/me/permissions/
    Роль и capability-флаги текущего пользователя для фронтенда.
    """
    role = request.user.role
    capabilities = ROLE_CAPABILITIES.get(role, ROLE_CAPABILITIES['guest'])

    return Response(
        {
            'role': role,
            'capabilities': capabilities,
        },
        status=status.HTTP_200_OK,
    )


@extend_schema(
    tags=['Authentication'],
    summary='Logout',
    description='Выход из системы (инвалидация refresh токена)',
    responses={
        200: OpenApiResponse(description='Logout successful'),
        400: OpenApiResponse(description='Invalid token'),
    }
)
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def logout_user(request):
    """
    POST /api/v1/auth/logout/
    Инвалидация refresh-токена (blacklist).
    """
    try:
        refresh_token = request.data.get('refresh')
        if refresh_token:
            token = RefreshToken(refresh_token)
            token.blacklist()
            return Response({
                'message': 'Logout successful'
            }, status=status.HTTP_200_OK)
        else:
            return Response({
                'error': 'Refresh token is required'
            }, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    tags=['Authentication'],
    summary='Password Reset Request',
    description='Запрос сброса пароля (отправка email/SMS с кодом)',
    request=PasswordResetRequestSerializer,
    responses={
        200: OpenApiResponse(description='Reset email sent'),
        400: OpenApiResponse(description='Invalid email'),
    }
)
@api_view(['POST'])
@permission_classes([AllowAny])
def password_reset_request(request):
    """
    POST /api/v1/auth/password/reset/
    Запрос сброса пароля (email или SMS).
    """
    serializer = PasswordResetRequestSerializer(data=request.data)
    if serializer.is_valid():
        email = serializer.validated_data['email']

        try:
            User.objects.get(email=email)
            # TODO: Implement password reset email/SMS logic

            return Response({
                'message': 'Password reset instructions sent to your email'
            }, status=status.HTTP_200_OK)

        except User.DoesNotExist:
            # Security: Don't reveal if email exists
            return Response({
                'message': 'If the email exists, reset instructions will be sent'
            }, status=status.HTTP_200_OK)

    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    tags=['Authentication'],
    summary='Get User Roles',
    description='Получить справочный список ролей пользователей',
    responses={
        200: UserRoleSerializer(many=True),
    }
)
@api_view(['GET'])
@permission_classes([AllowAny])
def list_user_roles(request):
    """
    GET /api/v1/auth/roles/
    Справочный список ролей пользователей.
    """
    serializer = UserRoleSerializer(User.get_role_definitions(), many=True)
    return Response(serializer.data, status=status.HTTP_200_OK)


def _can_manage_users(user):
    return bool(user and user.is_authenticated and user.role in ('admin', 'supermentor'))


@extend_schema(
    tags=['Users'],
    summary='List users',
    description='Получить список пользователей (admin/supermentor only)',
    responses={200: UserManagementSerializer(many=True), 403: OpenApiResponse(description='Forbidden')},
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_users(request):
    if not _can_manage_users(request.user):
        return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)
    queryset = User.objects.all().order_by('-created_at')
    serializer = UserManagementSerializer(queryset, many=True)
    return Response(serializer.data, status=status.HTTP_200_OK)


@extend_schema(
    tags=['Users'],
    summary='Create user',
    description='Создать пользователя (admin/supermentor only)',
    request=UserManagementSerializer,
    responses={201: UserManagementSerializer, 400: OpenApiResponse(description='Validation error'), 403: OpenApiResponse(description='Forbidden')},
)
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_user(request):
    if not _can_manage_users(request.user):
        return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)
    serializer = UserManagementSerializer(data=request.data)
    if serializer.is_valid():
        user = serializer.save()
        return Response(UserManagementSerializer(user).data, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    tags=['Users'],
    summary='Get user by id',
    description='Получить пользователя по id (admin/supermentor only)',
    responses={200: UserManagementSerializer, 403: OpenApiResponse(description='Forbidden'), 404: OpenApiResponse(description='Not found')},
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_user_by_id(request, user_id: int):
    if not _can_manage_users(request.user):
        return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)
    return Response(UserManagementSerializer(user).data, status=status.HTTP_200_OK)


@extend_schema(
    tags=['Users'],
    summary='Update user by id',
    description='Обновить пользователя по id (admin/supermentor only)',
    request=UserManagementSerializer,
    responses={200: UserManagementSerializer, 400: OpenApiResponse(description='Validation error'), 403: OpenApiResponse(description='Forbidden'), 404: OpenApiResponse(description='Not found')},
)
@api_view(['PATCH', 'PUT'])
@permission_classes([IsAuthenticated])
def update_user_by_id(request, user_id: int):
    if not _can_manage_users(request.user):
        return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)
    serializer = UserManagementSerializer(user, data=request.data, partial=request.method == 'PATCH')
    if serializer.is_valid():
        updated = serializer.save()
        return Response(UserManagementSerializer(updated).data, status=status.HTTP_200_OK)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    tags=['Users'],
    summary='Delete user by id',
    description='Удалить пользователя по id (admin/supermentor only)',
    responses={204: OpenApiResponse(description='Deleted'), 403: OpenApiResponse(description='Forbidden'), 404: OpenApiResponse(description='Not found')},
)
@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_user_by_id(request, user_id: int):
    if not _can_manage_users(request.user):
        return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)
    user.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(
    tags=['Profiles'],
    summary='Get my profile',
    description='Получить профиль текущего пользователя',
    responses={200: UserSerializer},
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_my_profile(request):
    return Response(UserSerializer(request.user).data, status=status.HTTP_200_OK)


@extend_schema(
    tags=['Profiles'],
    summary='Update my profile',
    description='Обновить профиль текущего пользователя',
    request=UserUpdateSerializer,
    responses={200: UserSerializer, 400: OpenApiResponse(description='Validation error')},
)
@api_view(['PATCH', 'PUT'])
@permission_classes([IsAuthenticated])
def update_my_profile(request):
    serializer = UserUpdateSerializer(
        request.user,
        data=request.data,
        partial=request.method == 'PATCH',
    )
    if serializer.is_valid():
        user = serializer.save()
        return Response(UserSerializer(user).data, status=status.HTTP_200_OK)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
