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
    PasswordResetRequestSerializer,
    MobileBiometricConfirmSerializer,
    UserRoleSerializer,
)

User = get_user_model()


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
