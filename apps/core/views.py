from datetime import datetime, time as dt_time, timedelta

import redis
from django.conf import settings
from django.db import connection
from django.db.models import Case, IntegerField, Q, When

from django.utils import timezone
from django.utils.dateparse import parse_date
from drf_spectacular.utils import extend_schema, OpenApiResponse, inline_serializer
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
import rest_framework.fields as fields

from apps.access.models import GuestPass
from apps.bookings.models import Booking, Resource
from apps.companies.models import Company
from apps.core.permissions import IsCompanyMember
from apps.crm.models import Task
from apps.hr.models import LeaveRequest
from apps.notifications.models import Notification
from apps.services.models import Announcement, Floor, ServiceRequest
from apps.services.utils import annotate_floor_occupancy
from apps.users.models import User


@extend_schema(
    tags=['System'],
    summary='Health check',
    responses={
        200: inline_serializer(
            name='HealthCheckResponse',
            fields={
                'status': fields.CharField(),
                'database': fields.CharField(),
                'redis': fields.CharField(),
                'deployment_marker': fields.CharField(),
                'environment': fields.CharField(),
            },
        ),
        503: OpenApiResponse(description='Service unavailable — dependency unreachable'),
    },
)
@api_view(['GET'])
@permission_classes([AllowAny])
def health_check(request):
    try:
        connection.ensure_connection()
        db_ok = True
    except Exception:
        db_ok = False

    try:
        redis_client = redis.from_url(settings.CELERY_BROKER_URL, socket_connect_timeout=2)
        redis_ok = redis_client.ping()
    except Exception:
        redis_ok = False

    healthy = db_ok and redis_ok
    return Response(
        {
            'status': 'healthy' if healthy else 'unhealthy',
            'database': 'connected' if db_ok else 'unavailable',
            'redis': 'connected' if redis_ok else 'unavailable',
            'deployment_marker': 'pipeline-verify-2026-04-06',
            'environment': 'alpha-test',
        },
        status=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
    )


@extend_schema(
    tags=['System'],
    summary='Ping',
    responses={
        200: inline_serializer(
            name='PingResponse',
            fields={'message': fields.CharField()},
        ),
    },
)
@api_view(['GET'])
@permission_classes([AllowAny])
def ping(request):
    return Response({'message': 'pong', 'version': 'beta-test'})


@extend_schema(
    tags=['Calendar'],
    summary='Team calendar events',
    responses={
        200: inline_serializer(
            name='CalendarEventResponse',
            fields={
                'id': fields.CharField(),
                'event_type': fields.CharField(),
                'source_id': fields.IntegerField(),
                'title': fields.CharField(),
                'start': fields.DateField(),
                'end': fields.DateField(),
                'all_day': fields.BooleanField(),
                'status': fields.CharField(),
                'user_id': fields.IntegerField(),
                'user_name': fields.CharField(),
                'leave_type': fields.CharField(),
                'comment': fields.CharField(),
            },
            many=True,
        ),
        400: OpenApiResponse(description='Validation error'),
        401: OpenApiResponse(description='Not authenticated'),
        403: OpenApiResponse(description='Company members only'),
    },
)
@api_view(['GET'])
@permission_classes([IsCompanyMember])
def calendar_events(request):
    queryset = LeaveRequest.objects.filter(status='approved').select_related('user')
    if request.user.role != 'superadmin':
        queryset = queryset.filter(company_id=request.user.company_id)

    date_from = request.query_params.get('date_from')
    if date_from:
        parsed_from = parse_date(date_from)
        if parsed_from is None:
            return Response({'date_from': 'Invalid date format. Use YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)
        queryset = queryset.filter(end_date__gte=parsed_from)

    date_to = request.query_params.get('date_to')
    if date_to:
        parsed_to = parse_date(date_to)
        if parsed_to is None:
            return Response({'date_to': 'Invalid date format. Use YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)
        queryset = queryset.filter(start_date__lte=parsed_to)

    events = []
    for leave in queryset.order_by('start_date', 'id'):
        events.append({
            'id': f'leave-{leave.id}',
            'event_type': 'leave',
            'source_id': leave.id,
            'title': f'Отсутствие: {leave.user.full_name}',
            'start': leave.start_date,
            'end': leave.end_date,
            'all_day': True,
            'status': leave.status,
            'user_id': leave.user_id,
            'user_name': leave.user.full_name,
            'leave_type': leave.leave_type,
            'comment': leave.comment,
        })
    return Response(events)


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard helpers
# ─────────────────────────────────────────────────────────────────────────────

def _local_day_bounds_today():
    tz = timezone.get_current_timezone()
    today = timezone.localdate()
    start = timezone.make_aware(datetime.combine(today, dt_time.min), tz)
    end = timezone.make_aware(datetime.combine(today, dt_time.max), tz)
    return start, end


def _local_day_bounds_offset(days):
    """Return (start, end) UTC-aware datetimes for localdate() + days offset."""
    tz = timezone.get_current_timezone()
    target_date = timezone.localdate() + timedelta(days=days)
    start = timezone.make_aware(datetime.combine(target_date, dt_time.min), tz)
    end = timezone.make_aware(datetime.combine(target_date, dt_time.max), tz)
    return start, end


def _serialize_booking_recent(b):
    return {
        'id': b.id,
        'resource_name': b.resource.name,
        'user_name': b.user.full_name,
        'company_name': b.company.name if b.company else None,
        'start_time': b.start_time,
        'end_time': b.end_time,
        'status': b.status,
    }


def _serialize_user(user):
    return {
        'id': user.id,
        'full_name': user.full_name,
        'avatar': user.avatar.url if user.avatar else None,
    }


def _serialize_announcement(ann):
    return {
        'id': ann.id,
        'title': ann.title,
        'body': ann.body,
        'category': ann.category,
        'scope': ann.scope,
        'created_at': ann.created_at,
    }


def _floor_load():
    """
    Return per-floor occupancy stats for the superadmin dashboard.

    Uses annotate_floor_occupancy() — the same function used by FloorViewSet —
    so the dashboard floor_load and the floor list API occupancy_pct are always
    identical, both based on the Resource.floor_fk FK relationship.
    """
    floors = annotate_floor_occupancy(Floor.objects.order_by('number'))
    return [
        {
            'floor_id': floor.id,
            'floor_number': floor.number,
            'floor_name': floor.name or f'Этаж {floor.number}',
            'total': floor.total_resources,
            'occupied': floor.booked_now,
            'occupancy_pct': floor.occupancy_pct,
        }
        for floor in floors
    ]


def _superadmin_widgets():
    now = timezone.now()
    today_start, today_end = _local_day_bounds_today()
    today_local = timezone.localdate()

    bookings_today_qs = Booking.objects.filter(
        start_time__gte=today_start,
        start_time__lte=today_end,
        status='confirmed',
    )
    bookings_today_count = bookings_today_qs.count()

    week_ago_start, week_ago_end = _local_day_bounds_offset(days=-7)
    bookings_same_weekday_7d_ago_count = Booking.objects.filter(
        start_time__gte=week_ago_start,
        start_time__lte=week_ago_end,
        status='confirmed',
    ).count()
    bookings_week_delta = bookings_today_count - bookings_same_weekday_7d_ago_count

    total_resources = Resource.objects.filter(is_active=True).count()
    occupied_resources = Booking.objects.filter(
        start_time__lte=now,
        end_time__gte=now,
        status__in=['confirmed', 'checked_in'],
    ).values('resource').distinct().count()
    space_load_pct = round(occupied_resources / total_resources * 100) if total_resources > 0 else 0

    open_service_requests = ServiceRequest.objects.exclude(status='completed').count()
    service_requests_closed_today = ServiceRequest.objects.filter(
        status='completed',
        updated_at__date=today_local,
    ).count()

    new_companies_last_7d = Company.objects.filter(
        created_at__gte=now - timedelta(days=7),
    ).count()

    recent_bookings = (
        Booking.objects
        .select_related('user', 'resource')
        .order_by('-created_at')[:10]
    )
    recent_events = [
        {
            'event_type': 'booking',
            'id': b.id,
            'title': f'{b.resource.name} — {b.user.full_name}',
            'start_time': b.start_time,
            'status': b.status,
        }
        for b in recent_bookings
    ]

    bookings_recent_qs = (
        Booking.objects
        .filter(start_time__gte=today_start, start_time__lte=today_end)
        .select_related('user', 'resource', 'company')
        .order_by('start_time')[:5]
    )
    bookings_recent = [_serialize_booking_recent(b) for b in bookings_recent_qs]

    announcements_recent_qs = (
        Announcement.objects
        .filter(company__isnull=True)
        .order_by('-created_at')[:3]
    )
    announcements_recent = [
        {
            'id': a.id,
            'title': a.title,
            'text': a.body,
            'created_at': a.created_at,
        }
        for a in announcements_recent_qs
    ]

    return {
        'total_companies': Company.objects.count(),
        'total_users': User.objects.filter(is_active=True).count(),
        'bookings_today': bookings_today_count,
        'recent_events': recent_events,
        'quick_actions': [
            'invite_user',
            'create_announcement',
            'manage_bookings',
            'view_analytics',
            'manage_companies',
        ],
        'bookings_recent': bookings_recent,
        'announcements_recent': announcements_recent,
        'bookings_week_delta': bookings_week_delta,
        'space_load_pct': space_load_pct,
        'open_service_requests': open_service_requests,
        'service_requests_closed_today': service_requests_closed_today,
        'new_companies_last_7d': new_companies_last_7d,
        'floor_load': _floor_load(),
    }


def _initials(user):
    parts = [user.first_name[:1], user.last_name[:1]]
    return ''.join(p for p in parts if p).upper()


def _my_tasks_for_user(user, now, limit=5):
    today = timezone.localdate()
    return list(
        Task.objects
        .select_related('column__board')
        .filter(
            assignee=user,
            is_archived=False,
            is_deleted=False,
        )
        .filter(Q(deadline__isnull=True) | Q(deadline__date=today))
        .order_by(
            Case(When(deadline__isnull=True, then=1), default=0, output_field=IntegerField()),
            'deadline',
        )[:limit]
    )


def _company_admin_widgets(user):
    now = timezone.now()
    today_start, today_end = _local_day_bounds_today()
    company = user.company

    employee_count = User.objects.filter(company=company, is_active=True).count()

    active_tasks = Task.objects.filter(
        column__board__company=company,
        is_archived=False,
        is_deleted=False,
    ).count()

    bookings_today = Booking.objects.filter(
        company=company,
        start_time__gte=today_start,
        start_time__lte=today_end,
        status='confirmed',
    ).count()

    announcements = (
        Announcement.objects
        .filter(Q(company=company) | Q(company__isnull=True))
        .order_by('-is_pinned', '-created_at')[:5]
    )

    pending_leaves_qs = (
        LeaveRequest.objects
        .select_related('user')
        .filter(company=company, status='pending')
        .order_by('created_at')
    )
    pending_leaves = [
        {
            'id': lr.id,
            'employee': _serialize_user(lr.user),
            'leave_type': lr.leave_type,
            'start_date': lr.start_date,
            'end_date': lr.end_date,
            'created_at': lr.created_at,
        }
        for lr in pending_leaves_qs
    ]

    active_guest_passes_qs = (
        GuestPass.objects
        .select_related('created_by')
        .filter(company=company, status='active')
        .order_by('created_at')
    )
    active_guest_passes = [
        {
            'id': gp.id,
            'guest_name': gp.guest_name,
            'guest_email': gp.guest_email,
            'host': _serialize_user(gp.created_by),
            'visit_date': gp.valid_from,
            'valid_from': gp.valid_from,
            'valid_until': gp.valid_until,
            'created_at': gp.created_at,
        }
        for gp in active_guest_passes_qs
    ]

    bookings_recent_qs = (
        Booking.objects
        .filter(company=company, start_time__gte=today_start, start_time__lte=today_end)
        .select_related('user', 'resource', 'company')
        .order_by('start_time')[:5]
    )
    bookings_recent = [_serialize_booking_recent(b) for b in bookings_recent_qs]

    busy_ids = Booking.objects.filter(
        company=company,
        status='confirmed',
        start_time__lte=now,
        end_time__gte=now,
    ).values_list('resource_id', flat=True)
    free_resources_now = (
        Resource.objects
        .filter(is_active=True)
        .exclude(id__in=busy_ids)
        .count()
    )

    team_bookings_qs = (
        Booking.objects
        .select_related('user', 'resource')
        .filter(company=company, start_time__gte=today_start, start_time__lte=today_end)
        .order_by('start_time')[:10]
    )
    team_bookings_today = [
        {
            'user_full_name': b.user.full_name,
            'user_initials': _initials(b.user),
            'resource_name': b.resource.name,
            'start_time': b.start_time,
            'end_time': b.end_time,
            'status': b.status,
        }
        for b in team_bookings_qs
    ]

    today = timezone.localdate()
    my_tasks_qs = _my_tasks_for_user(user, now)
    my_tasks = [
        {
            'id': t.id,
            'title': t.title,
            'board_name': t.column.board.name,
            'due_date': t.deadline.date().isoformat() if t.deadline else None,
            'priority': t.priority,
            'is_overdue': t.deadline.date() < today if t.deadline else False,
        }
        for t in my_tasks_qs
    ]

    return {
        'employee_count': employee_count,
        'active_tasks': active_tasks,
        'bookings_today': bookings_today,
        'announcement_feed': [_serialize_announcement(a) for a in announcements],
        'pending_approvals': {
            'leaves': pending_leaves,
            'guest_passes': active_guest_passes,
        },
        'bookings_recent': bookings_recent,
        'free_resources_now': free_resources_now,
        'team_bookings_today': team_bookings_today,
        'my_tasks': my_tasks,
    }


def _employee_widgets(user):
    now = timezone.now()
    today_start, today_end = _local_day_bounds_today()
    today = timezone.localdate()

    my_tasks_today = Task.objects.filter(
        assignee=user,
        is_archived=False,
        is_deleted=False,
        column__board__is_archived=False,
        deadline__date=today,
    ).count()

    my_bookings_today = Booking.objects.filter(
        user=user,
        start_time__gte=today_start,
        start_time__lte=today_end,
        status='confirmed',
    ).count()

    announcements = (
        Announcement.objects
        .filter(Q(company=user.company) | Q(company__isnull=True))
        .order_by('-is_pinned', '-created_at')[:5]
    )

    unread_count = Notification.objects.filter(user=user, is_read=False).count()

    bookings_recent_qs = (
        Booking.objects
        .filter(user=user, start_time__gte=today_start, start_time__lte=today_end)
        .select_related('user', 'resource', 'company')
        .order_by('start_time')[:5]
    )
    bookings_recent = [_serialize_booking_recent(b) for b in bookings_recent_qs]

    upcoming_qs = (
        Booking.objects
        .select_related('resource')
        .filter(user=user, status='confirmed', start_time__gte=today_start, start_time__lte=today_end)
        .order_by('start_time')[:5]
    )
    my_upcoming_bookings = [
        {
            'id': b.id,
            'resource_name': b.resource.name,
            'resource_type': b.resource.resource_type,
            'resource_capacity': b.resource.capacity,
            'resource_row': None,
            'start_time': b.start_time,
            'end_time': b.end_time,
            'is_all_day': False,
            'status': b.status,
        }
        for b in upcoming_qs
    ]

    my_tasks_qs = _my_tasks_for_user(user, now)
    my_tasks_list = [
        {
            'id': t.id,
            'title': t.title,
            'board_name': t.column.board.name,
            'due_date': t.deadline.date().isoformat() if t.deadline else None,
            'priority': t.priority,
            'is_overdue': t.deadline.date() < today if t.deadline else False,
        }
        for t in my_tasks_qs
    ]

    my_tasks_boards_count = len({t['board_name'] for t in my_tasks_list})

    return {
        'my_tasks_today': my_tasks_today,
        'my_bookings_today': my_bookings_today,
        'announcement_feed': [_serialize_announcement(a) for a in announcements],
        'unread_notifications_count': unread_count,
        'bookings_recent': bookings_recent,
        'my_upcoming_bookings': my_upcoming_bookings,
        'my_tasks': my_tasks_list,
        'my_tasks_boards_count': my_tasks_boards_count,
    }


def _guest_widgets(user):
    today_start, today_end = _local_day_bounds_today()

    my_bookings_today = Booking.objects.filter(
        user=user,
        start_time__gte=today_start,
        start_time__lte=today_end,
        status='confirmed',
    ).count()

    available_desks = Resource.objects.filter(resource_type='desk', is_active=True).count()
    available_rooms = Resource.objects.filter(resource_type='meeting_room', is_active=True).count()

    bc_announcements = (
        Announcement.objects
        .filter(company__isnull=True)
        .order_by('-is_pinned', '-created_at')[:5]
    )

    return {
        'my_bookings_today': my_bookings_today,
        'quick_booking': {
            'available_desks': available_desks,
            'available_rooms': available_rooms,
        },
        'bc_announcements': [_serialize_announcement(a) for a in bc_announcements],
    }


@extend_schema(
    tags=['Dashboard'],
    summary='Role-based dashboard',
    responses={
        200: OpenApiResponse(description='Dashboard widgets for the current user role'),
        401: OpenApiResponse(description='Not authenticated'),
    },
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def dashboard(request):
    user = request.user
    role = user.role

    base = {
        'role': role,
        'user': _serialize_user(user),
    }

    if role == 'superadmin':
        base.update(_superadmin_widgets())
    elif role == 'company_admin':
        base.update(_company_admin_widgets(user))
    elif role == 'employee':
        base.update(_employee_widgets(user))
    else:
        base.update(_guest_widgets(user))

    return Response(base)
