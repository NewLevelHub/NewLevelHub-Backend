"""
Notification utilities for NewLevelHub.

Usage from anywhere in the codebase::

    from apps.notifications.utils import create_notification

    create_notification(
        user=some_user,
        notification_type='booking_confirmed',
        title='Your booking is confirmed',
        message='Room A is booked for 10:00–11:00.',
        link='https://app.newlevelhub.com/bookings/42',
    )
"""

import hashlib
import logging

from django.conf import settings
from django.core.cache import cache
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone
from django.core import signing

from .models import Notification, NotificationPreference
from .serializers import NOTIFICATION_TYPE_FIELD_MAP

logger = logging.getLogger(__name__)

# All valid notification type keys (mirrors Notification.TYPE_CHOICES primary keys).
NOTIFICATION_TYPES = {
    'booking_confirmed',
    'booking_reminder',
    'booking_cancelled',
    'booking_completed',
    'task_assigned',
    'task_moved',
    'task_comment',
    'task_deadline',
    'task_deadline_soon',
    'task_deadline_overdue',
    'guest_validated',
    'guest_arrived',
    'guest_pass_expiring',
    'pass_expiring',
    'service_request_update',
    'service_accepted',
    'service_status',
    'service_completed',
    'announcement',
    'announcement_building',
    'announcement_company',
    'invitation',
    'invite_received',
    'leave_review',
    'leave_approved',
    'leave_rejected',
    'new_employee',
    'system',
}

# TTL (seconds) for the email deduplication cache key.  Emails with the same
# recipient + notification_type + title sent within this window are suppressed.
EMAIL_DEDUP_TTL = 300  # 5 minutes


def _is_dnd_active(pref):
    """Return True if Do-Not-Disturb is currently active for a preference record.

    Two DND mechanisms are supported:
      - ``do_not_disturb`` (simple boolean toggle): DND is on whenever True.
      - ``dnd_enabled`` / ``dnd_until`` (time-based): DND is on while
        ``dnd_enabled`` is True and ``dnd_until`` is either None (indefinite)
        or a future datetime.

    Either mechanism being active suppresses notifications.
    """
    if pref.do_not_disturb:
        return True
    if not pref.dnd_enabled:
        return False
    # DND is active if dnd_until is None (indefinite) OR has not passed yet.
    if pref.dnd_until is None:
        return True
    return pref.dnd_until > timezone.now()


def _in_app_allowed(pref, notification_type):
    """
    Return True if the in_app channel is enabled for *notification_type*.

    Types not present in NOTIFICATION_TYPE_FIELD_MAP (e.g. secondary types like
    'booking_completed', 'task_deadline_soon') fall back to True so they are
    never silently suppressed without an explicit user preference.
    """
    mapping = NOTIFICATION_TYPE_FIELD_MAP.get(notification_type)
    if mapping is None:
        return True
    in_app_field, _ = mapping
    return getattr(pref, in_app_field, True)


def _email_allowed(pref, notification_type):
    """
    Return True if the email channel is enabled for *notification_type*.

    Types not present in NOTIFICATION_TYPE_FIELD_MAP fall back to False so that
    unexpected types never spam users with email.
    """
    mapping = NOTIFICATION_TYPE_FIELD_MAP.get(notification_type)
    if mapping is None:
        return False
    _, email_field = mapping
    return getattr(pref, email_field, False)


def _build_unsubscribe_url(user):
    """Return a signed unsubscribe URL for *user*."""
    token = signing.dumps({'user_id': user.pk}, salt='notification-unsubscribe')
    frontend_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000')
    # Use a relative API URL so the frontend or email client can call it directly.
    base = getattr(settings, 'BACKEND_URL', frontend_url)
    return f"{base.rstrip('/')}/api/v1/notifications/unsubscribe/?token={token}"


def _email_dedup_key(user, notification_type, title):
    """Return a cache key for email deduplication."""
    raw = f"notif_email:{user.pk}:{notification_type}:{title}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _send_notification_email(user, notification_type, title, body, url=''):
    """
    Render and send a notification email to *user*.

    BUG-2 fix: before sending, check a short-lived cache key.  If the same
    (user, notification_type, title) combination was already sent within
    EMAIL_DEDUP_TTL seconds, skip sending.

    BUG-3 fix: pass ``unsubscribe_url`` into the template context so every
    notification email contains a working unsubscribe link.
    """
    dedup_key = _email_dedup_key(user, notification_type, title)
    if cache.get(dedup_key):
        logger.debug(
            'Duplicate email suppressed for user=%s type=%s title=%r',
            user.pk, notification_type, title,
        )
        return

    unsubscribe_url = _build_unsubscribe_url(user)
    context = {
        'user': user,
        'title': title,
        'body': body,
        'url': url,
        'unsubscribe_url': unsubscribe_url,
    }
    html_body = render_to_string('emails/notifications/base_notification.html', context)

    from_email = settings.DEFAULT_FROM_EMAIL
    msg = EmailMultiAlternatives(
        subject=title,
        body=body,  # plain-text fallback
        from_email=from_email,
        to=[user.email],
    )
    msg.attach_alternative(html_body, 'text/html')
    try:
        msg.send()
        # Mark this combination as sent to prevent duplicates.
        cache.set(dedup_key, True, EMAIL_DEDUP_TTL)
    except Exception:
        logger.exception(
            'Failed to send notification email to user=%s type=%s',
            user.pk, notification_type,
        )


def should_notify(user, notification_type):
    """
    Return True if a notification of *notification_type* should be created for *user*.

    Checks:
      1. Do-Not-Disturb: if DND is currently active, return False.
      2. Per-type in_app preference: if disabled for this type, return False.

    This is a convenience predicate for call sites that cannot use
    ``create_notification`` directly (e.g. because they need ``get_or_create``
    semantics or already handle the ``Notification`` creation themselves).
    """
    pref, _ = NotificationPreference.objects.get_or_create(user=user)
    if _is_dnd_active(pref):
        logger.debug(
            'Notification suppressed by DND for user=%s type=%s',
            user.pk, notification_type,
        )
        return False
    if not _in_app_allowed(pref, notification_type):
        logger.debug(
            'Notification suppressed by in_app preference for user=%s type=%s',
            user.pk, notification_type,
        )
        return False
    return True


def create_notification(user, notification_type, title, message, link=None):
    """
    Create and persist a new in-app notification for *user*.

    Before creating the Notification record, this function checks:
      1. Do-Not-Disturb: if DND is active, skip creation and return None.
      2. Per-type in_app preference: if disabled for this type, skip and return None.

    Email delivery is handled separately by the ``send_notification_email`` Celery
    task in ``apps.notifications.tasks``, which applies preference checks,
    deduplication, and includes an unsubscribe link.

    Parameters
    ----------
    user : settings.AUTH_USER_MODEL instance
        The recipient of the notification.
    notification_type : str
        One of the keys in ``NOTIFICATION_TYPES``.  A ``ValueError`` is raised
        for unknown types so callers get an early, explicit failure rather than
        a silent DB constraint error.
    title : str
        Short heading shown in the notification bell / list.
    message : str
        Full notification body text.
    link : str | None
        Optional URL the user should be directed to when they click the
        notification (stored in the ``url`` column).

    Returns
    -------
    Notification | None
        The newly created (and saved) ``Notification`` instance, or None if
        the notification was suppressed by DND or user preferences.

    Raises
    ------
    ValueError
        If *notification_type* is not a recognised type key.
    """
    if notification_type not in NOTIFICATION_TYPES:
        raise ValueError(
            f"Unknown notification_type '{notification_type}'. "
            f"Valid values: {sorted(NOTIFICATION_TYPES)}"
        )

    # Fetch or create preference record (never 404 on first access).
    pref, _ = NotificationPreference.objects.get_or_create(user=user)

    # 1. DND check.
    if _is_dnd_active(pref):
        logger.debug(
            'Notification suppressed by DND for user=%s type=%s',
            user.pk, notification_type,
        )
        return None

    # 2. Per-type in_app preference check.
    if not _in_app_allowed(pref, notification_type):
        logger.debug(
            'Notification suppressed by in_app preference for user=%s type=%s',
            user.pk, notification_type,
        )
        return None

    # 3. Create in-app notification.
    notification = Notification.objects.create(
        user=user,
        notification_type=notification_type,
        title=title,
        body=message,
        url=link or '',
    )

    return notification
