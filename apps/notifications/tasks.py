from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string, TemplateDoesNotExist


# Maps notification_type → NotificationPreference email field name.
# Uses granular per-type fields where available; falls back to legacy group
# fields for types that do not yet have their own toggle (e.g. secondary
# booking/task variants that share a group preference).
_PREF_FIELD_MAP = {
    # Booking — granular per-type fields
    'booking_confirmed': 'booking_confirmed_email',
    'booking_reminder': 'booking_reminder_email',
    'booking_cancelled': 'booking_cancelled_email',
    'booking_completed': 'booking_email',          # no dedicated field yet → legacy group
    # Task — granular per-type fields
    'task_assigned': 'task_assigned_email',
    'task_moved': 'task_moved_email',
    'task_comment': 'task_comment_email',
    'task_deadline': 'task_deadline_email',
    'task_deadline_soon': 'task_deadline_email',   # shares deadline toggle
    'task_deadline_overdue': 'task_deadline_email',
    # Guest / Access — granular per-type fields
    'guest_validated': 'guest_validated_email',
    'guest_arrived': 'guest_pass_expiring_email',  # shares pass-expiring toggle
    'guest_pass_expiring': 'guest_pass_expiring_email',
    'pass_expiring': 'guest_pass_expiring_email',
    # Service — granular per-type field
    'service_request_update': 'service_request_update_email',
    'service_accepted': 'service_request_update_email',
    'service_status': 'service_request_update_email',
    'service_completed': 'service_request_update_email',
    # Announcement — granular per-type field
    'announcement': 'announcement_email',
    'announcement_building': 'announcement_email',
    'announcement_company': 'announcement_email',
    # Invitations / HR — granular per-type fields
    'invitation': 'invitation_email',
    'invite_received': 'invitation_email',
    'leave_review': 'leave_review_email',
    'leave_approved': 'leave_review_email',
    'leave_rejected': 'leave_review_email',
    'new_employee': 'hr_email',                    # no dedicated field yet → legacy group
    # System
    'system': None,
}

_DEFAULT_SUBJECTS = {
    'booking_confirmed': 'Ваше бронирование подтверждено',
    'booking_reminder': 'Напоминание о бронировании',
    'booking_cancelled': 'Бронирование отменено',
    'booking_completed': 'Бронирование завершено',
    'task_assigned': 'Вам назначена задача',
    'task_moved': 'Задача перемещена',
    'task_comment': 'Новый комментарий к задаче',
    'task_deadline': 'Дедлайн задачи',
    'task_deadline_soon': 'Скоро дедлайн задачи',
    'task_deadline_overdue': 'Просрочен дедлайн задачи',
    'guest_validated': 'Гостевой пропуск подтверждён',
    'guest_arrived': 'Гость прибыл',
    'guest_pass_expiring': 'Гостевой пропуск истекает',
    'pass_expiring': 'Пропуск истекает',
    'service_request_update': 'Обновление заявки на сервис',
    'service_accepted': 'Заявка принята',
    'service_status': 'Статус заявки обновлён',
    'service_completed': 'Заявка выполнена',
    'announcement': 'Новое объявление',
    'announcement_building': 'Объявление для здания',
    'announcement_company': 'Объявление компании',
    'invitation': 'Приглашение в компанию',
    'invite_received': 'Вы получили приглашение',
    'leave_review': 'Заявка на отпуск требует проверки',
    'leave_approved': 'Заявка на отпуск одобрена',
    'leave_rejected': 'Заявка на отпуск отклонена',
    'new_employee': 'Новый сотрудник присоединился',
    'system': 'Системное уведомление',
}


def _check_preference(user, notification_type):
    """
    Return True if the user has email enabled for this notification type.
    Returns True (allow) when no preference row exists (default-allow).
    Returns True unconditionally when notification_type maps to None (system/no pref key).
    """
    pref_field = _PREF_FIELD_MAP.get(notification_type)
    if pref_field is None:
        return True

    try:
        prefs = user.notification_preferences
    except Exception:
        return True

    return bool(getattr(prefs, pref_field, True))


def _check_dnd(user, notification_type):
    """
    Return True if DND is NOT active (i.e. notification should proceed).
    """
    try:
        prefs = user.notification_preferences
        return not prefs.do_not_disturb
    except Exception:
        return True


@shared_task
def send_notification_email(user_id, notification_type, context):
    """
    Send an HTML email notification to a single user.

    Parameters
    ----------
    user_id : int
        Primary key of the recipient user.
    notification_type : str
        One of the recognised notification type keys.
    context : dict
        Template context. Expected keys:
          - subject (str, optional) — overrides the default subject
          - user_first_name (str)
          - action_url (str)
          - Any additional type-specific keys.
    """
    from apps.users.models import User

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return

    if not _check_preference(user, notification_type):
        return

    platform_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000').rstrip('/')
    ctx = {
        'user_first_name': user.first_name,
        'platform_url': platform_url,
        'action_url': '',
    }
    ctx.update(context)

    subject = ctx.get('subject') or _DEFAULT_SUBJECTS.get(notification_type, 'Уведомление NewLevelHub')

    template_name = f'notifications/email/{notification_type}.html'
    fallback_template = 'notifications/email/base_notification.html'

    try:
        html_message = render_to_string(template_name, ctx)
    except TemplateDoesNotExist:
        html_message = render_to_string(fallback_template, ctx)

    send_mail(
        subject=subject,
        message='',
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        html_message=html_message,
        fail_silently=False,
    )


@shared_task
def send_bulk_email(announcement_id):
    """
    Send email notifications to all active company members for a given announcement.

    Skipped silently if announcement.notify_email is False or the announcement
    does not exist.
    """
    from apps.services.models import Announcement
    from apps.users.models import User

    try:
        announcement = Announcement.objects.select_related('company').get(pk=announcement_id)
    except Announcement.DoesNotExist:
        return

    if not announcement.notify_email:
        return

    if announcement.company_id is None:
        return

    recipients = User.objects.filter(
        company=announcement.company,
        is_active=True,
    )

    platform_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000').rstrip('/')

    for user in recipients:
        context = {
            'subject': announcement.title,
            'user_first_name': user.first_name,
            'action_url': '/announcements/',
            'platform_url': platform_url,
            'announcement_title': announcement.title,
            'announcement_body': announcement.body,
        }
        send_notification_email.delay(
            user.id,
            'announcement_company',
            context,
        )


@shared_task
def create_notification(user_id, notification_type, title, body='', url=''):
    """Universal task: create an in-app notification with preference checks."""
    from apps.users.models import User
    from apps.notifications.models import Notification

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return

    Notification.objects.create(
        user=user,
        notification_type=notification_type,
        title=title,
        body=body,
        url=url,
    )
