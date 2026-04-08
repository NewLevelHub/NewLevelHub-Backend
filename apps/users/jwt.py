from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken
from rest_framework_simplejwt.views import TokenRefreshView


REFRESH_COOKIE_NAME = 'refresh_token'


def _refresh_lifetime(remember_me: bool):
    return settings.REMEMBER_ME_LIFETIME if remember_me else settings.REFRESH_TOKEN_LIFETIME


def _sync_outstanding_exp(refresh: RefreshToken):
    """
    Keep OutstandingToken aligned with JWT after custom set_exp().
    In some environments rotated refresh tokens may not have an OutstandingToken row yet.
    """
    jti = str(refresh['jti'])
    exp_ts = int(refresh['exp'])
    expires_at = timezone.datetime.fromtimestamp(exp_ts, tz=timezone.utc)
    token_str = str(refresh)
    user_id = refresh.get('user_id')

    if user_id is None:
        OutstandingToken.objects.filter(jti=jti).update(expires_at=expires_at, token=token_str)
        return

    obj, created = OutstandingToken.objects.get_or_create(
        jti=jti,
        defaults={
            'user_id': user_id,
            'token': token_str,
            'created_at': timezone.now(),
            'expires_at': expires_at,
        },
    )
    if not created:
        obj.expires_at = expires_at
        obj.token = token_str
        obj.save(update_fields=['expires_at', 'token'])


def issue_refresh_token(user, remember_me: bool = False) -> RefreshToken:
    refresh = RefreshToken.for_user(user)
    refresh['remember_me'] = bool(remember_me)
    refresh.set_exp(lifetime=_refresh_lifetime(bool(remember_me)))
    _sync_outstanding_exp(refresh)
    return refresh


def set_refresh_cookie(response: Response, refresh_token: str, remember_me: bool):
    max_age = int(_refresh_lifetime(remember_me).total_seconds())
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token,
        max_age=max_age,
        httponly=True,
        secure=not settings.DEBUG,
        samesite='Lax',
        path='/api/v1/auth/',
    )


def clear_refresh_cookie(response: Response):
    response.delete_cookie(
        REFRESH_COOKIE_NAME,
        path='/api/v1/auth/',
        samesite='Lax',
    )


class RememberMeTokenRefreshSerializer(TokenRefreshSerializer):
    def validate(self, attrs):
        try:
            incoming_refresh = RefreshToken(attrs['refresh'])
            remember_me = bool(incoming_refresh.get('remember_me', False))
            data = super().validate(attrs)
        except TokenError as exc:
            raise InvalidToken(str(exc)) from exc

        if 'refresh' in data:
            rotated_refresh = RefreshToken(data['refresh'])
            rotated_refresh['remember_me'] = remember_me
            rotated_refresh.set_exp(lifetime=_refresh_lifetime(remember_me))
            _sync_outstanding_exp(rotated_refresh)
            data['refresh'] = str(rotated_refresh)

        data['remember_me'] = remember_me
        return data


class CookieTokenRefreshView(TokenRefreshView):
    serializer_class = RememberMeTokenRefreshSerializer

    def post(self, request, *args, **kwargs):
        body_refresh = request.data.get('refresh')
        cookie_refresh = request.COOKIES.get(REFRESH_COOKIE_NAME)
        refresh = body_refresh or cookie_refresh

        if not refresh:
            return Response({'detail': 'Refresh token is required'}, status=status.HTTP_400_BAD_REQUEST)

        serializer = self.get_serializer(data={'refresh': refresh})
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        remember_me = bool(payload.pop('remember_me', False))

        response = Response(payload, status=status.HTTP_200_OK)
        if 'refresh' in payload:
            set_refresh_cookie(response, payload['refresh'], remember_me)
        return response
