import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken


logger = logging.getLogger(__name__)
User = get_user_model()
_IDLE_CACHE_PREFIX = 'session_idle_expired:'

# Automatic background polling must not extend the session.
BACKGROUND_ACTIVITY_SUFFIXES = (
    '/notifications/unread-count/',
)


def _idle_cache_key(user_id) -> str:
    return f'{_IDLE_CACHE_PREFIX}{user_id}'


def is_idle_timeout_disabled() -> bool:
    return settings.IDLE_SESSION_TIMEOUT.total_seconds() <= 0


def is_idle_session_marked_expired(user_id) -> bool:
    return bool(cache.get(_idle_cache_key(user_id)))


def mark_idle_session_expired(user) -> None:
    """Persist idle expiry outside the request DB transaction (survives ATOMIC_REQUESTS rollback)."""
    timeout_seconds = max(int(settings.REFRESH_TOKEN_LIFETIME.total_seconds()), 60)
    cache.set(_idle_cache_key(user.pk), True, timeout=timeout_seconds)


def clear_idle_session_marker(user) -> None:
    cache.delete(_idle_cache_key(user.pk))


def is_idle_session_expired(user) -> bool:
    """Return True when the user's last activity exceeds the configured idle timeout."""
    if is_idle_timeout_disabled():
        return False
    if is_idle_session_marked_expired(user.pk):
        return True
    if user.last_login is None:
        return False
    return timezone.now() - user.last_login > settings.IDLE_SESSION_TIMEOUT


def _format_last_activity(timestamp):
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
        _format_last_activity(user.last_login),
        idle_minutes,
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


def should_track_activity(request) -> bool:
    path = getattr(request, 'path', '') or ''
    return not any(path.endswith(suffix) for suffix in BACKGROUND_ACTIVITY_SUFFIXES)


def touch_last_activity(user):
    """Record successful authenticated activity for idle-timeout tracking."""
    now = timezone.now()
    User.objects.filter(pk=user.pk).update(last_login=now)
    user.last_login = now
