from datetime import timedelta

from django.utils import timezone
from django.db.models import Count, Avg, F
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

from .serializers import SuperAdminDashboardSerializer, CompanyAdminDashboardSerializer, ResourceUsageSerializer


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
        200: CompanyAdminDashboardSerializer,
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

    from django.db.models import Sum
    storage_used = File.objects.filter(company=company).aggregate(total=Sum('file_size'))['total'] or 0

    data = {
        'employee_count': company.members.filter(is_active=True).count(),
        'active_employees_7d': company.members.filter(last_login__gte=week_ago).count(),
        'bookings_this_month': Booking.objects.filter(company=company, start_time__gte=month_start).count(),
        'storage_used_bytes': storage_used,
        'storage_limit_bytes': company.storage_limit_gb * 1024 * 1024 * 1024,
        'active_tasks': Task.objects.filter(
            column__board__company=company, is_deleted=False, is_archived=False,
        ).count(),
        'guest_visits_this_month': GuestPass.objects.filter(
            company=company, created_at__gte=month_start,
        ).count(),
    }
    return Response(CompanyAdminDashboardSerializer(data).data)


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
