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

from .models import Notification

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


def create_notification(user, notification_type, title, message, link=None):
    """
    Create and persist a new in-app notification for *user*.

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
    Notification
        The newly created (and saved) ``Notification`` instance.

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

    return Notification.objects.create(
        user=user,
        notification_type=notification_type,
        title=title,
        body=message,
        url=link or '',
    )
