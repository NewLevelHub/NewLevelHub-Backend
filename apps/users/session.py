import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken


logger = logging.getLogger(__name__)
User = get_user_model()
_IDLE_CACHE_PREFIX = 'session_idle_expired:'
_ABSOLUTE_CACHE_PREFIX = 'session_absolute_expired:'
SESSION_CREATED_AT_CLAIM = 'session_created_at'
ABSOLUTE_TIMEOUT_REASON = 'ABSOLUTE_TIMEOUT'

# Automatic background polling must not extend the session.
BACKGROUND_ACTIVITY_SUFFIXES = (
    '/notifications/unread-count/',
)


def _idle_cache_key(user_id) -> str:
    return f'{_IDLE_CACHE_PREFIX}{user_id}'


def _absolute_cache_key(user_id) -> str:
    return f'{_ABSOLUTE_CACHE_PREFIX}{user_id}'


def is_idle_timeout_disabled() -> bool:
    return settings.IDLE_SESSION_TIMEOUT.total_seconds() <= 0


def is_absolute_timeout_disabled() -> bool:
    return settings.ABSOLUTE_SESSION_TIMEOUT.total_seconds() <= 0


def is_idle_session_marked_expired(user_id) -> bool:
    return bool(cache.get(_idle_cache_key(user_id)))


def is_absolute_session_marked_expired(user_id) -> bool:
    return bool(cache.get(_absolute_cache_key(user_id)))


def mark_idle_session_expired(user) -> None:
    """Persist idle expiry outside the request DB transaction (survives ATOMIC_REQUESTS rollback)."""
    timeout_seconds = max(int(settings.REFRESH_TOKEN_LIFETIME.total_seconds()), 60)
    cache.set(_idle_cache_key(user.pk), True, timeout=timeout_seconds)


def mark_absolute_session_expired(user) -> None:
    """Persist absolute expiry outside the request DB transaction (survives ATOMIC_REQUESTS rollback)."""
    timeout_seconds = max(int(settings.REFRESH_TOKEN_LIFETIME.total_seconds()), 60)
    cache.set(_absolute_cache_key(user.pk), True, timeout=timeout_seconds)


def clear_idle_session_marker(user) -> None:
    cache.delete(_idle_cache_key(user.pk))


def clear_absolute_session_marker(user) -> None:
    cache.delete(_absolute_cache_key(user.pk))


def clear_session_markers(user) -> None:
    clear_idle_session_marker(user)
    clear_absolute_session_marker(user)


def parse_session_created_at(raw_value):
    """Convert a JWT session_created_at claim to an aware datetime."""
    if raw_value is None:
        return None
    try:
        timestamp = int(raw_value)
    except (TypeError, ValueError):
        return None
    return timezone.datetime.fromtimestamp(timestamp, tz=timezone.utc)


def get_session_created_at_from_token(token):
    if token is None:
        return None
    getter = getattr(token, 'get', None)
    if getter is None:
        return None
    return parse_session_created_at(getter(SESSION_CREATED_AT_CLAIM))


def is_absolute_session_expired(session_created_at) -> bool:
    """Return True when the session start exceeds the configured absolute timeout."""
    if is_absolute_timeout_disabled():
        return False
    if session_created_at is None:
        return False
    return timezone.now() - session_created_at > settings.ABSOLUTE_SESSION_TIMEOUT


def is_idle_session_expired(user) -> bool:
    """Return True when the user's last activity exceeds the configured idle timeout."""
    if is_idle_timeout_disabled():
        return False
    if is_idle_session_marked_expired(user.pk):
        return True
    if user.last_login is None:
        return False
    return timezone.now() - user.last_login > settings.IDLE_SESSION_TIMEOUT


def _format_timestamp(timestamp):
    if timestamp is None:
        return 'N/A'
    return timezone.localtime(timestamp).strftime('%Y-%m-%d %H:%M:%S')


def log_idle_session_expiry(user):
    idle_duration = timezone.now() - user.last_login if user.last_login else None
    idle_minutes = int(idle_duration.total_seconds() // 60) if idle_duration else 0
    logger.info(
        'User session expired due to inactivity.\n'
        'User ID: %s\n'
        'Last activity: %s\n'
        'Idle duration: %s minutes',
        user.pk,
        _format_timestamp(user.last_login),
        idle_minutes,
    )


def log_absolute_session_expiry(user, session_created_at):
    now = timezone.now()
    session_duration = now - session_created_at if session_created_at else None
    duration_minutes = (
        int(session_duration.total_seconds() // 60) if session_duration else 0
    )
    logger.info(
        'User session expired due to absolute timeout.\n'
        'User ID: %s\n'
        'session_created_at: %s\n'
        'current_time: %s\n'
        'session_duration: %s minutes\n'
        'reason: %s',
        user.pk,
        _format_timestamp(session_created_at),
        _format_timestamp(now),
        duration_minutes,
        ABSOLUTE_TIMEOUT_REASON,
    )


def blacklist_user_refresh_tokens(user):
    """Blacklist all outstanding refresh tokens for a user."""
    outstanding = OutstandingToken.objects.filter(user=user).exclude(
        blacklistedtoken__isnull=False
    )
    BlacklistedToken.objects.bulk_create(
        [BlacklistedToken(token=t) for t in outstanding],
        ignore_conflicts=True,
    )


def handle_idle_session_expiry(user):
    log_idle_session_expiry(user)
    mark_idle_session_expired(user)
    blacklist_user_refresh_tokens(user)


def handle_absolute_session_expiry(user, session_created_at):
    log_absolute_session_expiry(user, session_created_at)
    mark_absolute_session_expired(user)
    blacklist_user_refresh_tokens(user)


def should_track_activity(request) -> bool:
    path = getattr(request, 'path', '') or ''
    return not any(path.endswith(suffix) for suffix in BACKGROUND_ACTIVITY_SUFFIXES)


def touch_last_activity(user):
    """Record successful authenticated activity for idle-timeout tracking."""
    now = timezone.now()
    User.objects.filter(pk=user.pk).update(last_login=now)
    user.last_login = now


def stamp_session_created_at(refresh_token, session_created_at=None):
    """Set session_created_at on refresh and derived access tokens."""
    created_at = session_created_at or timezone.now()
    timestamp = int(created_at.timestamp())
    refresh_token[SESSION_CREATED_AT_CLAIM] = timestamp
    access_token = refresh_token.access_token
    access_token[SESSION_CREATED_AT_CLAIM] = timestamp
    return access_token
