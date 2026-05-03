from datetime import datetime, time as dt_time

from django.db import connection
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
from apps.services.models import Announcement
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
                'deployment_marker': fields.CharField(),
                'environment': fields.CharField(),
            },
        ),
        503: OpenApiResponse(description='Service unavailable — database unreachable'),
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

    healthy = db_ok
    return Response(
        {
            'status': 'healthy' if healthy else 'unhealthy',
            'database': 'connected' if db_ok else 'unavailable',
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


def _superadmin_widgets():
    today_start, today_end = _local_day_bounds_today()

    bookings_today_qs = Booking.objects.filter(
        start_time__gte=today_start,
        start_time__lte=today_end,
        status='confirmed',
    )

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

    return {
        'total_companies': Company.objects.count(),
        'total_users': User.objects.filter(is_active=True).count(),
        'bookings_today': bookings_today_qs.count(),
        'recent_events': recent_events,
        'quick_actions': [
            'invite_user',
            'create_announcement',
            'manage_bookings',
            'view_analytics',
            'manage_companies',
        ],
    }


def _company_admin_widgets(user):
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
        .filter(company=company)
        .order_by('-is_pinned', '-created_at')[:5]
    )

    pending_leaves = LeaveRequest.objects.filter(company=company, status='pending').count()
    active_guest_passes = GuestPass.objects.filter(company=company, status='active').count()

    return {
        'employee_count': employee_count,
        'active_tasks': active_tasks,
        'bookings_today': bookings_today,
        'announcement_feed': [_serialize_announcement(a) for a in announcements],
        'pending_approvals': {
            'leaves': pending_leaves,
            'guest_passes': active_guest_passes,
        },
    }


def _employee_widgets(user):
    today_start, today_end = _local_day_bounds_today()
    today = timezone.localdate()

    my_tasks_today = Task.objects.filter(
        assignee=user,
        is_archived=False,
        is_deleted=False,
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
        .filter(company=user.company)
        .order_by('-is_pinned', '-created_at')[:5]
    )

    unread_count = Notification.objects.filter(user=user, is_read=False).count()

    return {
        'my_tasks_today': my_tasks_today,
        'my_bookings_today': my_bookings_today,
        'announcement_feed': [_serialize_announcement(a) for a in announcements],
        'unread_notifications_count': unread_count,
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
