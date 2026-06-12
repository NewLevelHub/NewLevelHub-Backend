from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.users.session import (
    get_session_created_at_from_token,
    handle_absolute_session_expiry,
    handle_idle_session_expiry,
    is_absolute_session_expired,
    is_absolute_session_marked_expired,
    is_idle_session_expired,
)


class SessionIdleTimeout(AuthenticationFailed):
    """Raised when the user's session expired due to inactivity."""

    status_code = 401


class SessionAbsoluteTimeout(AuthenticationFailed):
    """Raised when the user's session exceeded the absolute lifetime limit."""

    status_code = 401


def active_user_authentication_rule(user):
    """
    Reject JWT authentication for inactive users.
    """
    return bool(user is not None and user.is_active)


class IdleAwareJWTAuthentication(JWTAuthentication):
    """
    JWT authentication that rejects sessions idle longer than IDLE_SESSION_TIMEOUT
    or older than ABSOLUTE_SESSION_TIMEOUT.
    """

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None

        user, validated_token = result

        if is_absolute_session_marked_expired(user.pk):
            session_created_at = get_session_created_at_from_token(validated_token)
            handle_absolute_session_expiry(user, session_created_at)
            raise SessionAbsoluteTimeout()

        session_created_at = get_session_created_at_from_token(validated_token)
        if is_absolute_session_expired(session_created_at):
            handle_absolute_session_expiry(user, session_created_at)
            raise SessionAbsoluteTimeout()

        if is_idle_session_expired(user):
            handle_idle_session_expiry(user)
            raise SessionIdleTimeout()

        return user, validated_token
