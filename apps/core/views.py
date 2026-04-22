from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from django.db import connection
from django.utils.dateparse import parse_date
from drf_spectacular.utils import extend_schema, OpenApiResponse, inline_serializer
import rest_framework.fields as fields

from apps.core.permissions import IsCompanyMember
from apps.hr.models import LeaveRequest

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
