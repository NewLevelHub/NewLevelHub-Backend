from datetime import datetime, timedelta, time

from django.utils import timezone
from django.db.models import Count, Avg, F, Sum
from django.utils.dateparse import parse_date
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, OpenApiResponse, OpenApiParameter
from drf_spectacular.types import OpenApiTypes

from apps.core.permissions import IsSuperAdmin, IsCompanyAdmin
from apps.users.models import User
from apps.companies.models import Company
from apps.bookings.models import Booking, Resource
from apps.access.models import GuestPass, AccessLog
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

PERIOD_CHOICES = frozenset(('7d', '30d', '90d', 'custom'))
PERIOD_DAY_LENGTH = {'7d': 7, '30d': 30, '90d': 90}


def _parse_optional_int(param_name, raw):
    if raw is None or raw == '':
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValidationError({param_name: ['Must be a valid integer.']})


def _resolve_period_metadata(query_params):
    period = query_params.get('period') or '30d'
    if period not in PERIOD_CHOICES:
        raise ValidationError({'period': [f'Invalid period. Must be one of: {", ".join(sorted(PERIOD_CHOICES))}.']})

    today = timezone.localdate()

    if period == 'custom':
        df_raw = query_params.get('date_from')
        dt_raw = query_params.get('date_to')
        if not df_raw or not dt_raw:
            raise ValidationError(
                {
                    'detail': 'For period=custom, query parameters date_from and date_to (YYYY-MM-DD) are required.',
                },
            )
        date_from = parse_date(df_raw)
        date_to = parse_date(dt_raw)
        if date_from is None:
            raise ValidationError({'date_from': ['Enter a valid date (YYYY-MM-DD).']})
        if date_to is None:
            raise ValidationError({'date_to': ['Enter a valid date (YYYY-MM-DD).']})
        if date_from > date_to:
            raise ValidationError({'detail': 'date_from must be on or before date_to.'})
        return period, date_from, date_to

    span = PERIOD_DAY_LENGTH[period]
    date_to = today
    date_from = today - timedelta(days=span - 1)
    return period, date_from, date_to


def _local_day_bounds(target_date):
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(target_date, time.min), tz)
    end = timezone.make_aware(datetime.combine(target_date, time.max), tz)
    return start, end


@extend_schema(
    tags=['Analytics'],
    summary='Superadmin dashboard',
    parameters=[
        OpenApiParameter(
            name='period',
            type=str,
            location=OpenApiParameter.QUERY,
            description='Report window for metadata (date_from / date_to).',
            enum=['7d', '30d', '90d', 'custom'],
        ),
        OpenApiParameter(
            name='date_from',
            type=OpenApiTypes.DATE,
            location=OpenApiParameter.QUERY,
            description='Required when period=custom (inclusive, YYYY-MM-DD).',
        ),
        OpenApiParameter(
            name='date_to',
            type=OpenApiTypes.DATE,
            location=OpenApiParameter.QUERY,
            description='Required when period=custom (inclusive, YYYY-MM-DD).',
        ),
        OpenApiParameter(
            name='company_id',
            type=OpenApiTypes.INT,
            location=OpenApiParameter.QUERY,
            description='Optional: scope overview counters to this company (must exist).',
        ),
        OpenApiParameter(
            name='resource_type',
            type=str,
            location=OpenApiParameter.QUERY,
            description='Optional: filter bookings_today by Resource.resource_type.',
            enum=[c[0] for c in Resource.TYPE_CHOICES],
        ),
    ],
    responses={
        200: SuperAdminDashboardSerializer,
        400: OpenApiResponse(description='Validation error'),
        404: OpenApiResponse(description='Company not found'),
        401: OpenApiResponse(description='Not authenticated'),
        403: OpenApiResponse(description='Superadmin only'),
    },
)
@api_view(['GET'])
@permission_classes([IsSuperAdmin])
def superadmin_dashboard(request):
    qp = request.query_params
    period, date_from, date_to = _resolve_period_metadata(qp)

    company_id = _parse_optional_int('company_id', qp.get('company_id'))
    if company_id is not None:
        if not Company.objects.filter(pk=company_id).exists():
            raise NotFound('Company not found.')

    resource_type = qp.get('resource_type')
    if resource_type is not None and resource_type != '':
        valid_types = {c[0] for c in Resource.TYPE_CHOICES}
        if resource_type not in valid_types:
            raise ValidationError(
                {'resource_type': [f'Invalid resource_type. Must be one of: {", ".join(sorted(valid_types))}.']},
            )
    else:
        resource_type = None

    now = timezone.now()
    today = timezone.localdate()
    week_ago = now - timedelta(days=7)
    day_start, day_end = _local_day_bounds(today)

    if company_id is not None:
        company_qs = Company.objects.filter(pk=company_id)
        total_companies = company_qs.count()
        active_companies = company_qs.filter(is_active=True).count()
    else:
        total_companies = Company.objects.count()
        active_companies = Company.objects.filter(is_active=True).count()

    user_qs = User.objects.all()
    if company_id is not None:
        user_qs = user_qs.filter(company_id=company_id)
    total_users = user_qs.count()
    active_users_7d = user_qs.filter(last_login__gte=week_ago).count()

    bookings_qs = Booking.objects.filter(
        status='confirmed',
        start_time__gte=day_start,
        start_time__lte=day_end,
    )
    if company_id is not None:
        bookings_qs = bookings_qs.filter(company_id=company_id)
    if resource_type is not None:
        bookings_qs = bookings_qs.filter(resource__resource_type=resource_type)
    bookings_today = bookings_qs.count()

    guests_qs = AccessLog.objects.filter(
        is_entry=True,
        created_at__gte=day_start,
        created_at__lte=day_end,
    )
    if company_id is not None:
        guests_qs = guests_qs.filter(guest_pass__company_id=company_id)
    guests_today = guests_qs.count()

    sr_qs = ServiceRequest.objects.exclude(status='completed')
    if company_id is not None:
        sr_qs = sr_qs.filter(company_id=company_id)
    open_service_requests = sr_qs.count()

    overview = {
        'total_companies': total_companies,
        'active_companies': active_companies,
        'total_users': total_users,
        'active_users_7d': active_users_7d,
        'bookings_today': bookings_today,
        'guests_today': guests_today,
        'open_service_requests': open_service_requests,
    }

    payload = {
        'period': period,
        'date_from': date_from,
        'date_to': date_to,
        'overview': overview,
    }
    return Response(SuperAdminDashboardSerializer(instance=payload).data)


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
