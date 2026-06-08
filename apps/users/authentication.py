from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.users.session import handle_idle_session_expiry, is_idle_session_expired


class SessionIdleTimeout(AuthenticationFailed):
    """Raised when the user's session expired due to inactivity."""

    status_code = 401


def active_user_authentication_rule(user):
    """
    Reject JWT authentication for inactive users.
    """
    return bool(user is not None and user.is_active)


class IdleAwareJWTAuthentication(JWTAuthentication):
    """
    JWT authentication that rejects sessions idle longer than IDLE_SESSION_TIMEOUT.
    """

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None

        user, validated_token = result
        if is_idle_session_expired(user):
            handle_idle_session_expiry(user)
            raise SessionIdleTimeout()

        return user, validated_token
