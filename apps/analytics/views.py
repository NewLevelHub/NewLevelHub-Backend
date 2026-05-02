from datetime import timedelta

from django.utils import timezone
from django.db.models import Count, Avg, F, Sum
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, OpenApiResponse

from apps.core.permissions import IsSuperAdmin, IsCompanyAdmin
from apps.users.models import User
from apps.companies.models import Company
from apps.bookings.models import Booking
from apps.access.models import GuestPass
from apps.services.models import ServiceRequest
from apps.crm.models import Task
from apps.storage.models import File

from .serializers import (
    SuperAdminDashboardSerializer,
    ResourceUsageSerializer,
    CompanyAnalyticsSerializer,
)


def _normalize_column_status(column_name):
    normalized = (column_name or '').strip().lower().replace('_', ' ').replace('-', ' ')
    normalized = ' '.join(normalized.split())
    compact = normalized.replace(' ', '')

    if compact in {'todo', 'backlog', 'new'} or normalized in {'to do', 'к выполнению'}:
        return 'todo'
    if compact in {'inprogress', 'progress', 'wip', 'doing'} or normalized in {'in progress', 'в работе'}:
        return 'in_progress'
    if compact in {'done', 'complete', 'completed'} or normalized in {'готово', 'сделано', 'завершено'}:
        return 'done'
    return None


@extend_schema(
    tags=['Analytics'],
    summary='Superadmin dashboard',
    responses={
        200: SuperAdminDashboardSerializer,
        401: OpenApiResponse(description='Not authenticated'),
        403: OpenApiResponse(description='Superadmin only'),
    },
)
@api_view(['GET'])
@permission_classes([IsSuperAdmin])
def superadmin_dashboard(request):
    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = now - timedelta(days=7)

    data = {
        'total_companies': Company.objects.count(),
        'active_companies': Company.objects.filter(is_active=True).count(),
        'total_users': User.objects.count(),
        'active_users_7d': User.objects.filter(last_login__gte=week_ago).count(),
        'bookings_today': Booking.objects.filter(start_time__gte=today_start, status='confirmed').count(),
        'guests_today': GuestPass.objects.filter(valid_from__date=now.date(), status='active').count(),
        'open_service_requests': ServiceRequest.objects.exclude(status='completed').count(),
    }
    return Response(SuperAdminDashboardSerializer(data).data)


@extend_schema(
    tags=['Analytics'],
    summary='Company admin dashboard',
    responses={
        200: CompanyAnalyticsSerializer,
        400: OpenApiResponse(description='No company assigned'),
        401: OpenApiResponse(description='Not authenticated'),
        403: OpenApiResponse(description='Company admin only'),
    },
)
@api_view(['GET'])
@permission_classes([IsCompanyAdmin])
def company_dashboard(request):
    user = request.user
    company = user.company
    if not company:
        return Response({'detail': 'No company'}, status=400)

    now = timezone.now()
    week_ago = now - timedelta(days=7)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    employees_qs = company.members.filter(is_active=True).exclude(role='superadmin')
    last_30_days = now - timedelta(days=30)

    storage_used = File.objects.filter(company=company).aggregate(total=Sum('file_size'))['total'] or 0
    storage_limit_bytes = int(company.storage_limit_gb * 1024 * 1024 * 1024)

    tasks_qs = Task.objects.filter(column__board__company=company, is_deleted=False, is_archived=False)
    active_crm_tasks = {'todo': 0, 'in_progress': 0, 'done': 0}
    for row in tasks_qs.values('column__name').annotate(count=Count('id')):
        status_key = _normalize_column_status(row['column__name'])
        if status_key:
            active_crm_tasks[status_key] += row['count']

    employee_ids = list(employees_qs.values_list('id', flat=True))
    bookings_30d = {
        item['user_id']: item['count']
        for item in Booking.objects.filter(
            company=company, start_time__gte=last_30_days, user_id__in=employee_ids,
        ).values('user_id').annotate(count=Count('id'))
    }
    active_tasks_30d = {}
    for row in tasks_qs.filter(assignee_id__in=employee_ids).values('assignee_id', 'column__name'):
        status_key = _normalize_column_status(row['column__name'])
        if status_key == 'done':
            continue
        assignee_id = row['assignee_id']
        if assignee_id is None:
            continue
        active_tasks_30d[assignee_id] = active_tasks_30d.get(assignee_id, 0) + 1
    employee_activity = [
        {
            'user_id': employee.id,
            'full_name': employee.full_name,
            'booking_count_30d': bookings_30d.get(employee.id, 0),
            'task_count_active': active_tasks_30d.get(employee.id, 0),
            'last_login': employee.last_login,
        }
        for employee in employees_qs.order_by('id')
    ]

    data = {
        'total_employees': employees_qs.count(),
        'active_7d': employees_qs.filter(last_login__gte=week_ago).count(),
        'bookings_month': Booking.objects.filter(company=company, start_time__gte=month_start).count(),
        'storage': {
            'used': storage_used,
            'limit': storage_limit_bytes,
        },
        'active_crm_tasks': active_crm_tasks,
        'guest_visits_month': GuestPass.objects.filter(company=company, created_at__gte=month_start).count(),
        'employee_activity': employee_activity,
    }
    return Response(CompanyAnalyticsSerializer(data).data)


@extend_schema(
    tags=['Analytics'],
    summary='Resource usage stats (superadmin)',
    responses={
        200: ResourceUsageSerializer(many=True),
        401: OpenApiResponse(description='Not authenticated'),
        403: OpenApiResponse(description='Superadmin only'),
    },
)
@api_view(['GET'])
@permission_classes([IsSuperAdmin])
def resource_usage(request):
    # TODO: фильтр по периоду через query params
    stats = (
        Booking.objects
        .values(resource_type=F('resource__resource_type'))
        .annotate(
            total_bookings=Count('id'),
            avg_duration_minutes=Avg(
                (F('end_time') - F('start_time')),
            ),
        )
    )
    # TODO: конвертировать avg_duration в минуты (сейчас timedelta)
    return Response(list(stats))
