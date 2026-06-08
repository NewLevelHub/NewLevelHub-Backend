from django.db.models import Q, Sum

from apps.notifications.models import Notification
from apps.notifications.utils import should_notify
from apps.users.models import User


LIMIT_METRIC_META = {
    'employees': {
        'label': 'Employee',
        'unit': 'employees',
    },
    'boards': {
        'label': 'Board',
        'unit': 'boards',
    },
    'storage': {
        'label': 'Storage',
        'unit': 'GB',
    },
}


def get_company_storage_used_bytes(company):
    """Return total storage used by the company in bytes.

    Includes both:
    - Storage app files (apps.storage.File) linked to this company.
    - Direct-upload CRM task attachments (apps.crm.TaskAttachment with no
      storage_file link) for tasks belonging to this company's boards.
    """
    from apps.crm.models import TaskAttachment

    from apps.storage.models import File

    # All storage files belonging to the company:
    # - company-scoped files (file.company = company)
    # - personal files of company employees (file.company IS NULL, file.owner.company = company)
    storage_files_bytes = (
        File.objects.filter(is_deleted=False)
        .filter(
            Q(company=company)
            | Q(company__isnull=True, owner__company=company)
        )
        .aggregate(total=Sum('file_size'))['total'] or 0
    )

    # Only count direct-upload attachments (storage_file is None).
    # Mode B attachments are already counted via the Storage file above.
    crm_attachments_bytes = (
        TaskAttachment.objects.filter(
            task__column__board__company=company,
            storage_file__isnull=True,
            file__isnull=False,
        ).aggregate(total=Sum('file_size'))['total'] or 0
    )

    return storage_files_bytes + crm_attachments_bytes


def get_guest_storage_used_bytes(user):
    """Return total personal storage used by a guest user in bytes."""
    from apps.storage.models import File

    return (
        File.objects.filter(owner=user, company__isnull=True, is_deleted=False)
        .aggregate(total=Sum('file_size'))['total'] or 0
    )


def notify_company_admins_limit_thresholds(company, metric, current_value, limit_value):
    """
    Create system notifications for company admins when usage reaches 80% / 95%.
    """
    if limit_value <= 0:
        return

    usage_percent = (float(current_value) / float(limit_value)) * 100
    if usage_percent < 80:
        return

    meta = LIMIT_METRIC_META[metric]
    thresholds = [threshold for threshold in (80, 95) if usage_percent >= threshold]
    if not thresholds:
        return

    admins = User.objects.filter(
        company=company,
        role='company_admin',
        is_active=True,
    )
    if not admins.exists():
        return

    for threshold in thresholds:
        title = f'System limit warning: {threshold}%'
        body = (
            f"Your {meta['label'].lower()} usage has reached {threshold}% "
            f"({current_value}/{limit_value} {meta['unit']}). "
            f"Please free up space or upgrade your plan."
        )
        for admin in admins:
            if not should_notify(admin, 'announcement_company'):
                continue
            Notification.objects.get_or_create(
                user=admin,
                notification_type='announcement_company',
                title=title,
                is_read=False,
                defaults={
                    'body': body,
                    'url': f'/companies/{company.id}',
                },
            )
