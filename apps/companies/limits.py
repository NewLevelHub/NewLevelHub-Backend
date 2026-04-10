from django.db.models import Sum

from apps.notifications.models import Notification
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
    return company.files.aggregate(total=Sum('file_size'))['total'] or 0


def notify_company_admins_limit_thresholds(company, metric, current_value, limit_value):
    """
    Create system notifications for company admins when usage reaches 80% / 95%.
    """
    if limit_value <= 0:
        return

    usage_percent = (current_value / limit_value) * 100
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

    title = 'System limit warning'
    for threshold in thresholds:
        body = (
            f"{meta['label']} usage reached {threshold}% "
            f"({current_value}/{limit_value} {meta['unit']})."
        )
        for admin in admins:
            Notification.objects.get_or_create(
                user=admin,
                notification_type='announcement_company',
                title=title,
                body=body,
                defaults={
                    'url': f'/companies/{company.id}/limits',
                },
            )
