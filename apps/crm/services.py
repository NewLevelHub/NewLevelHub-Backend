from apps.core.error_codes import CRM_WIP_LIMIT_EXCEEDED
from apps.core.exceptions import LocalizedError

from .models import Task


def check_wip_limit(column, count=1, exclude_task_pk=None):
    """
    Raises ValidationError with code 'wip_limit_exceeded' if adding `count`
    active tasks to `column` would exceed its wip_limit.
    wip_limit == 0 means no limit — check is skipped.
    exclude_task_pk: exclude a specific task from the active count
    (used when moving/unarchiving a task already in the column).
    """
    if column.wip_limit == 0:
        return
    qs = Task.objects.filter(column=column, is_archived=False)
    if exclude_task_pk is not None:
        qs = qs.exclude(pk=exclude_task_pk)
    current = qs.count()
    if current + count > column.wip_limit:
        raise LocalizedError(
            code=CRM_WIP_LIMIT_EXCEEDED,
            i18n_key='crm.wip_limit_exceeded',
            params={'wip_limit': column.wip_limit},
        )
