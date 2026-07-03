import csv
import io
import os
from datetime import datetime, timedelta, time

from django.http import HttpResponse
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

from django.utils import timezone
from django.db.models import Count, Avg, F, Sum, Q
from django.db.models.functions import TruncDate
from django.utils.dateparse import parse_date
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema, OpenApiResponse, OpenApiParameter
from drf_spectacular.types import OpenApiTypes

from apps.core.exceptions import raise_validation_error
from apps.core.i18n import get_lang, translate
from apps.core.permissions import IsSuperAdmin, IsCompanyAdmin
from apps.users.models import User
from apps.companies.models import Company
from apps.bookings.models import Booking, Resource
from apps.access.models import GuestPass, AccessLog
from apps.services.models import ServiceRequest
from apps.crm.models import Task
from apps.hr.models import LeaveRequest

from .serializers import (
    SuperAdminDashboardSerializer,
    ResourceUsageSerializer,
    CompanyAnalyticsSerializer,
)

_FONTS_DIR = os.path.join(os.path.dirname(__file__), 'fonts')


def _register_cyrillic_fonts():
    """Register DejaVu Sans TTF for Unicode/Cyrillic support in PDF export."""
    try:
        pdfmetrics.registerFont(TTFont('DejaVuSans', os.path.join(_FONTS_DIR, 'DejaVuSans.ttf')))
        pdfmetrics.registerFont(TTFont('DejaVuSans-Bold', os.path.join(_FONTS_DIR, 'DejaVuSans-Bold.ttf')))
    except Exception:
        pass  # fonts already registered on repeated imports


_register_cyrillic_fonts()

_CELL_STYLE = ParagraphStyle('PdfCell', fontName='DejaVuSans', fontSize=8, leading=10)
_CELL_HEADER_STYLE = ParagraphStyle('PdfCellHeader', fontName='DejaVuSans-Bold', fontSize=8, leading=10,
                                    textColor=colors.white)


def _pdf_cell(text, header=False):
    return Paragraph(str(text), _CELL_HEADER_STYLE if header else _CELL_STYLE)


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
        raise_validation_error(param_name, 'analytics.must_be_integer')


def _resolve_period_metadata(query_params):
    period = query_params.get('period') or '30d'
    if period not in PERIOD_CHOICES:
        raise_validation_error(
            'period',
            'analytics.invalid_period',
            {'choices': ', '.join(sorted(PERIOD_CHOICES))},
        )

    today = timezone.localdate()

    if period == 'custom':
        df_raw = query_params.get('date_from')
        dt_raw = query_params.get('date_to')
        if not df_raw or not dt_raw:
            raise ValidationError(
                {'detail': {'_i18n': True, 'key': 'analytics.custom_period_dates_required', 'params': {}}},
            )
        date_from = parse_date(df_raw)
        date_to = parse_date(dt_raw)
        if date_from is None:
            raise_validation_error('date_from', 'analytics.invalid_date')
        if date_to is None:
            raise_validation_error('date_to', 'analytics.invalid_date')
        if date_from > date_to:
            raise ValidationError(
                {'detail': {'_i18n': True, 'key': 'analytics.date_from_after_date_to', 'params': {}}}
            )
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


def _build_resource_utilization(bookings_in_period):
    rows = (
        bookings_in_period
        .annotate(day=TruncDate('start_time'))
        .values('day')
        .annotate(
            desk_bookings=Count('id', filter=Q(resource__resource_type='desk')),
            room_bookings=Count('id', filter=Q(resource__resource_type='meeting_room')),
            parking_bookings=Count('id', filter=Q(resource__resource_type='parking')),
            capsule_bookings=Count('id', filter=Q(resource__resource_type='capsule')),
        )
        .order_by('day')
    )
    return [
        {
            'date': row['day'],
            'desk_bookings': row['desk_bookings'],
            'room_bookings': row['room_bookings'],
            'parking_bookings': row['parking_bookings'],
            'capsule_bookings': row['capsule_bookings'],
        }
        for row in rows
    ]


def _build_peak_hours(bookings_in_period):
    tz = timezone.get_current_timezone()
    aggregated = {}
    for start_time in bookings_in_period.values_list('start_time', flat=True):
        dt_local = timezone.localtime(start_time, tz)
        key = (dt_local.weekday(), dt_local.hour)
        aggregated[key] = aggregated.get(key, 0) + 1

    return [
        {
            'day_of_week': day_of_week,
            'hour': hour,
            'booking_count': count,
        }
        for (day_of_week, hour), count in sorted(aggregated.items())
    ]


def _build_new_registrations(user_qs):
    weeks = {}
    tz = timezone.get_current_timezone()
    for joined in user_qs.values_list('date_joined', flat=True):
        local_date = timezone.localtime(joined, tz).date()
        iso_year, iso_week, _ = local_date.isocalendar()
        week_key = f'{iso_year}-W{iso_week:02d}'
        weeks[week_key] = weeks.get(week_key, 0) + 1
    return [{'week': week, 'count': count} for week, count in sorted(weeks.items())]


def build_superadmin_dashboard_payload(request):
    """
    Same aggregated figures as JSON GET /analytics/superadmin/ (DEV-115).
    Used by the dashboard response and CSV export.
    """
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

    today = timezone.localdate()
    week_ago = timezone.now() - timedelta(days=7)
    day_start, day_end = _local_day_bounds(today)
    period_start, _ = _local_day_bounds(date_from)
    _, period_end = _local_day_bounds(date_to)

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
        status__in=('confirmed', 'completed'),
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

    bookings_in_period = Booking.objects.filter(
        status__in=('confirmed', 'completed'),
        start_time__gte=period_start,
        start_time__lte=period_end,
    )
    if company_id is not None:
        bookings_in_period = bookings_in_period.filter(company_id=company_id)
    if resource_type is not None:
        bookings_in_period = bookings_in_period.filter(resource__resource_type=resource_type)

    resource_utilization = _build_resource_utilization(bookings_in_period)
    peak_hours = _build_peak_hours(bookings_in_period)

    registrations_qs = User.objects.filter(
        date_joined__gte=period_start,
        date_joined__lte=period_end,
    )
    if company_id is not None:
        registrations_qs = registrations_qs.filter(company_id=company_id)
    new_registrations = _build_new_registrations(registrations_qs)

    sr_period_qs = ServiceRequest.objects.filter(
        created_at__gte=period_start,
        created_at__lte=period_end,
    )
    if company_id is not None:
        sr_period_qs = sr_period_qs.filter(company_id=company_id)
    service_requests_by_type = list(
        sr_period_qs.values('request_type').annotate(count=Count('id')).order_by('request_type'),
    )
    service_requests_by_type = [
        {'type': row['request_type'], 'count': row['count']}
        for row in service_requests_by_type
    ]

    top_resources_qs = list(
        bookings_in_period.values('resource_id', 'resource__name', 'resource__resource_type')
        .annotate(booking_count=Count('id'))
        .order_by('-booking_count', 'resource_id')[:5],
    )
    top_resources = [
        {
            'resource_id': row['resource_id'],
            'name': row['resource__name'],
            'resource_type': row['resource__resource_type'],
            'booking_count': row['booking_count'],
        }
        for row in top_resources_qs
    ]

    top_companies_qs = list(
        bookings_in_period
        .exclude(company_id__isnull=True)
        .values('company_id', 'company__name')
        .annotate(booking_count=Count('id'))
        .order_by('-booking_count', 'company_id')[:5],
    )
    top_companies = [
        {
            'company_id': row['company_id'],
            'company_name': row['company__name'],
            'booking_count': row['booking_count'],
        }
        for row in top_companies_qs
    ]

    period_days = (date_to - date_from).days + 1
    capacity = max(period_days, 1)
    low_utilization_qs = (
        bookings_in_period.values('resource_id', 'resource__name', 'resource__resource_type')
        .annotate(booking_count=Count('id'))
        .order_by('resource_id')
    )
    low_utilization = []
    for row in low_utilization_qs:
        utilization_percent = round((row['booking_count'] / capacity) * 100, 2)
        if utilization_percent < 20:
            low_utilization.append(
                {
                    'resource_id': row['resource_id'],
                    'name': row['resource__name'],
                    'resource_type': row['resource__resource_type'],
                    'booking_count': row['booking_count'],
                    'utilization_percent': utilization_percent,
                },
            )

    return {
        'period': period,
        'date_from': date_from,
        'date_to': date_to,
        'overview': overview,
        'resource_utilization': resource_utilization,
        'peak_hours': peak_hours,
        'new_registrations': new_registrations,
        'service_requests_by_type': service_requests_by_type,
        'top_resources': top_resources,
        'top_companies': top_companies,
        'low_utilization': low_utilization,
    }


def build_company_analytics_data(user, date_from=None, date_to=None):
    """Same figures as JSON GET /analytics/company/ (DEV-117). Caller must ensure user.company is set.

    Args:
        user: the requesting user (must have user.company set).
        date_from: optional datetime.date — start of the reporting period (inclusive).
        date_to: optional datetime.date — end of the reporting period (inclusive).
        When both are None the period defaults to the last 30 days (consistent with
        _resolve_period_metadata's default of '30d').
    """
    company = user.company
    now = timezone.now()
    week_ago = now - timedelta(days=7)

    if date_from is not None and date_to is not None:
        period_start, _ = _local_day_bounds(date_from)
        _, period_end = _local_day_bounds(date_to)
    else:
        period_start = now - timedelta(days=30)
        period_end = now

    employees_qs = company.members.filter(is_active=True).exclude(role='superadmin')

    from apps.companies.limits import get_company_storage_used_bytes
    storage_used = get_company_storage_used_bytes(company)
    storage_limit_bytes = int(company.storage_limit_gb * 1024 * 1024 * 1024)

    tasks_qs = Task.objects.filter(
        column__board__company=company,
        column__board__is_archived=False,
        is_deleted=False,
        is_archived=False,
        created_at__gte=period_start,
        created_at__lte=period_end,
    )
    column_agg = list(
        tasks_qs.values(
            'column_id',
            'column__name',
            'column__position',
            'column__board_id',
            'column__board__name',
        ).annotate(count=Count('id')),
    )
    column_agg.sort(
        key=lambda r: (
            (r['column__board__name'] or '').lower(),
            r['column__position'] or 0,
            r['column_id'] or 0,
        ),
    )
    by_column = [
        {
            'column_id': row['column_id'],
            'name': row['column__name'] or '',
            'board_name': row['column__board__name'] or '',
            'count': row['count'],
        }
        for row in column_agg
    ]
    active_crm_tasks = {'total': tasks_qs.count(), 'by_column': by_column}

    employee_ids = list(employees_qs.values_list('id', flat=True))
    bookings_period = {
        item['user_id']: item['count']
        for item in Booking.objects.filter(
            company=company,
            start_time__gte=period_start,
            start_time__lte=period_end,
            user_id__in=employee_ids,
        ).values('user_id').annotate(count=Count('id'))
    }
    active_tasks_period = {}
    for row in tasks_qs.filter(assignee_id__in=employee_ids).values('assignee_id', 'column__name'):
        status_key = _normalize_column_status(row['column__name'])
        if status_key == 'done':
            continue
        assignee_id = row['assignee_id']
        if assignee_id is None:
            continue
        active_tasks_period[assignee_id] = active_tasks_period.get(assignee_id, 0) + 1
    employee_activity = [
        {
            'user': employee,
            'booking_count_30d': bookings_period.get(employee.id, 0),
            'task_count_active': active_tasks_period.get(employee.id, 0),
            'last_login': employee.last_login,
        }
        for employee in employees_qs.order_by('id')
    ]

    pending_leaves = [
        {
            'id': lr.id,
            'employee_name': lr.user.full_name,
            'leave_type': lr.leave_type,
            'start_date': lr.start_date,
            'end_date': lr.end_date,
            'created_at': lr.created_at,
        }
        for lr in LeaveRequest.objects.filter(
            company=company, status='pending',
        ).select_related('user').order_by('created_at')
    ]

    pending_guest_passes = [
        {
            'id': gp.id,
            'guest_name': gp.guest_name,
            'host_name': gp.created_by.full_name,
            'visit_date': gp.valid_from,
            'created_at': gp.created_at,
        }
        for gp in GuestPass.objects.filter(
            company=company, status='active',
        ).select_related('created_by').order_by('created_at')
    ]

    return {
        'total_employees': employees_qs.count(),
        'active_7d': employees_qs.filter(last_login__gte=week_ago).count(),
        'bookings_month': Booking.objects.filter(
            company=company,
            start_time__gte=period_start,
            start_time__lte=period_end,
        ).count(),
        'storage': {
            'used': storage_used,
            'limit': storage_limit_bytes,
        },
        'active_crm_tasks': active_crm_tasks,
        'guest_visits_month': AccessLog.objects.filter(
            is_entry=True,
            guest_pass__company=company,
            created_at__gte=period_start,
            created_at__lte=period_end,
        ).count(),
        'employee_activity': employee_activity,
        'pending_approvals': {
            'leaves': pending_leaves,
            'guest_passes': pending_guest_passes,
        },
    }


def _http_csv_attachment(filename_stem, rows):
    buffer = io.StringIO()
    buffer.write('\ufeff')
    writer = csv.writer(buffer)
    for row in rows:
        writer.writerow(row)
    response = HttpResponse(buffer.getvalue(), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{filename_stem}.csv"'
    return response


def _http_pdf_attachment(filename_stem, pdf_bytes):
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename_stem}.pdf"'
    return response


def _pdf_table_style(header_bg=colors.HexColor('#2563EB')):
    return TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'DejaVuSans-Bold'),
        ('FONTNAME', (0, 1), (-1, -1), 'DejaVuSans'),
        ('FONTSIZE', (0, 0), (-1, 0), 8),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('BACKGROUND', (0, 0), (-1, 0), header_bg),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
        ('TOPPADDING', (0, 0), (-1, 0), 6),
        ('BACKGROUND', (0, 1), (-1, -1), colors.white),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F1F5F9')]),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 5),
        ('TOPPADDING', (0, 1), (-1, -1), 5),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#CBD5E1')),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ])


def _build_superadmin_pdf(payload, lang='ru'):
    t = lambda key, **params: translate(key, lang, **params)  # noqa: E731
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'PdfTitle',
        parent=styles['Heading1'],
        fontName='DejaVuSans-Bold',
        fontSize=16,
        textColor=colors.HexColor('#1E3A5F'),
        spaceAfter=4,
    )
    subtitle_style = ParagraphStyle(
        'PdfSubtitle',
        parent=styles['Normal'],
        fontName='DejaVuSans',
        fontSize=9,
        textColor=colors.HexColor('#64748B'),
        spaceAfter=16,
    )
    section_style = ParagraphStyle(
        'PdfSection',
        parent=styles['Heading2'],
        fontName='DejaVuSans-Bold',
        fontSize=11,
        textColor=colors.HexColor('#1E3A5F'),
        spaceBefore=12,
        spaceAfter=6,
    )

    period = payload['period']
    date_from = payload['date_from'].isoformat()
    date_to = payload['date_to'].isoformat()
    ov = payload['overview']
    generated_date = datetime.now().strftime('%Y-%m-%d %H:%M')

    story = [
        Paragraph(t('analytics.pdf.title_superadmin', period=period), title_style),
        Paragraph(t('analytics.pdf.generated', date=generated_date), subtitle_style),
        Paragraph(t('analytics.pdf.kpi_table'), section_style),
    ]

    kpi_headers = [
        _pdf_cell(t('analytics.csv.period'), header=True),
        _pdf_cell(t('analytics.csv.date_from'), header=True),
        _pdf_cell(t('analytics.csv.date_to'), header=True),
        _pdf_cell(t('analytics.csv.total_companies'), header=True),
        _pdf_cell(t('analytics.csv.active_companies'), header=True),
        _pdf_cell(t('analytics.csv.total_users'), header=True),
        _pdf_cell(t('analytics.csv.active_users_7d'), header=True),
        _pdf_cell(t('analytics.csv.bookings_today'), header=True),
        _pdf_cell(t('analytics.csv.guests_today'), header=True),
        _pdf_cell(t('analytics.csv.open_service_requests'), header=True),
    ]
    kpi_values = [
        _pdf_cell(period),
        _pdf_cell(date_from),
        _pdf_cell(date_to),
        _pdf_cell(ov['total_companies']),
        _pdf_cell(ov['active_companies']),
        _pdf_cell(ov['total_users']),
        _pdf_cell(ov['active_users_7d']),
        _pdf_cell(ov['bookings_today']),
        _pdf_cell(ov['guests_today']),
        _pdf_cell(ov['open_service_requests']),
    ]

    usable_width = A4[0] - 4 * cm
    col_count = len(kpi_headers)
    col_w = usable_width / col_count

    kpi_table = Table(
        [kpi_headers, kpi_values],
        colWidths=[col_w] * col_count,
        repeatRows=1,
    )
    kpi_table.setStyle(_pdf_table_style())
    story.append(kpi_table)

    doc.build(story)
    return buf.getvalue()


def _build_company_pdf(data, lang='ru'):
    t = lambda key, **params: translate(key, lang, **params)  # noqa: E731
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'PdfTitle',
        parent=styles['Heading1'],
        fontName='DejaVuSans-Bold',
        fontSize=16,
        textColor=colors.HexColor('#1E3A5F'),
        spaceAfter=4,
    )
    subtitle_style = ParagraphStyle(
        'PdfSubtitle',
        parent=styles['Normal'],
        fontName='DejaVuSans',
        fontSize=9,
        textColor=colors.HexColor('#64748B'),
        spaceAfter=16,
    )
    section_style = ParagraphStyle(
        'PdfSection',
        parent=styles['Heading2'],
        fontName='DejaVuSans-Bold',
        fontSize=11,
        textColor=colors.HexColor('#1E3A5F'),
        spaceBefore=12,
        spaceAfter=6,
    )

    act = data['active_crm_tasks']
    storage_used_gb = round(data['storage']['used'] / (1024 ** 3), 2)
    storage_limit_gb = round(data['storage']['limit'] / (1024 ** 3), 2)
    generated_date = datetime.now().strftime('%Y-%m-%d %H:%M')

    story = [
        Paragraph(t('analytics.pdf.title_company'), title_style),
        Paragraph(t('analytics.pdf.generated', date=generated_date), subtitle_style),
        Paragraph(t('analytics.pdf.summary'), section_style),
    ]

    summary_headers = [
        _pdf_cell(t('analytics.csv.total_employees'), header=True),
        _pdf_cell(t('analytics.csv.active_7d'), header=True),
        _pdf_cell(t('analytics.csv.bookings_month'), header=True),
        _pdf_cell('Storage (GB)', header=True),
        _pdf_cell(t('analytics.csv.crm_total'), header=True),
        _pdf_cell(t('analytics.csv.guest_visits_month'), header=True),
    ]
    summary_values = [
        _pdf_cell(data['total_employees']),
        _pdf_cell(data['active_7d']),
        _pdf_cell(data['bookings_month']),
        _pdf_cell(f'{storage_used_gb} / {storage_limit_gb}'),
        _pdf_cell(act['total']),
        _pdf_cell(data['guest_visits_month']),
    ]

    usable_width = A4[0] - 4 * cm
    col_count = len(summary_headers)
    col_w = usable_width / col_count

    summary_table = Table(
        [summary_headers, summary_values],
        colWidths=[col_w] * col_count,
        repeatRows=1,
    )
    summary_table.setStyle(_pdf_table_style())
    story.append(summary_table)
    story.append(Spacer(1, 0.5 * cm))

    story.append(Paragraph(t('analytics.pdf.employee_activity'), section_style))

    emp_headers = [
        _pdf_cell(t('analytics.csv.full_name'), header=True),
        _pdf_cell(t('analytics.csv.booking_count_30d'), header=True),
        _pdf_cell(t('analytics.csv.task_count_active'), header=True),
        _pdf_cell(t('analytics.csv.last_login'), header=True),
    ]
    emp_rows = [emp_headers]
    for emp in data['employee_activity']:
        last_login = emp['last_login'].strftime('%Y-%m-%d %H:%M') if emp['last_login'] else '—'
        emp_rows.append([
            _pdf_cell(emp['user'].full_name),
            _pdf_cell(emp['booking_count_30d']),
            _pdf_cell(emp['task_count_active']),
            _pdf_cell(last_login),
        ])

    emp_col_widths = [usable_width * 0.40, usable_width * 0.20, usable_width * 0.20, usable_width * 0.20]
    emp_table = Table(emp_rows, colWidths=emp_col_widths, repeatRows=1)
    emp_table.setStyle(_pdf_table_style())
    story.append(emp_table)

    doc.build(story)
    return buf.getvalue()


def _rows_superadmin_csv(payload, lang='ru'):
    ov = payload['overview']
    t = lambda key: translate(key, lang)  # noqa: E731
    headers = [
        t('analytics.csv.period'),
        t('analytics.csv.date_from'),
        t('analytics.csv.date_to'),
        t('analytics.csv.total_companies'),
        t('analytics.csv.active_companies'),
        t('analytics.csv.total_users'),
        t('analytics.csv.active_users_7d'),
        t('analytics.csv.bookings_today'),
        t('analytics.csv.guests_today'),
        t('analytics.csv.open_service_requests'),
    ]
    data_row = [
        payload['period'],
        payload['date_from'].isoformat(),
        payload['date_to'].isoformat(),
        ov['total_companies'],
        ov['active_companies'],
        ov['total_users'],
        ov['active_users_7d'],
        ov['bookings_today'],
        ov['guests_today'],
        ov['open_service_requests'],
    ]
    return [headers, data_row]


class _IgnoreDrfFormatQueryParamMixin:
    """DRF reserves ?format= for renderers; CSV export uses format=csv as a domain parameter."""

    def perform_content_negotiation(self, request, force=False):
        renderers = self.get_renderers()
        if renderers:
            return (renderers[0], renderers[0].media_type)
        return (JSONRenderer(), 'application/json')


def _rows_company_csv(data, lang='ru'):
    act = data['active_crm_tasks']
    t = lambda key: translate(key, lang)  # noqa: E731
    summary_header = [
        t('analytics.csv.total_employees'),
        t('analytics.csv.active_7d'),
        t('analytics.csv.bookings_month'),
        t('analytics.csv.storage_used'),
        t('analytics.csv.storage_limit'),
        t('analytics.csv.crm_total'),
        t('analytics.csv.guest_visits_month'),
    ]
    summary_row = [
        data['total_employees'],
        data['active_7d'],
        data['bookings_month'],
        data['storage']['used'],
        data['storage']['limit'],
        act['total'],
        data['guest_visits_month'],
    ]
    rows = [summary_header, summary_row, []]
    rows.append([
        t('analytics.csv.full_name'),
        t('analytics.csv.booking_count_30d'),
        t('analytics.csv.task_count_active'),
        t('analytics.csv.last_login'),
    ])
    for emp in data['employee_activity']:
        last_login = emp['last_login'].isoformat() if emp['last_login'] else ''
        rows.append([
            emp['user'].full_name,
            emp['booking_count_30d'],
            emp['task_count_active'],
            last_login,
        ])
    return rows


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
    payload = build_superadmin_dashboard_payload(request)
    return Response(SuperAdminDashboardSerializer(instance=payload).data)


@extend_schema(
    tags=['Analytics'],
    summary='Company admin dashboard',
    parameters=[
        OpenApiParameter(
            name='period',
            type=str,
            location=OpenApiParameter.QUERY,
            description='Период: 7d, 30d, 90d или custom. По умолчанию: последние 30 дней.',
            enum=['7d', '30d', '90d', 'custom'],
        ),
        OpenApiParameter(
            name='date_from',
            type=OpenApiTypes.DATE,
            location=OpenApiParameter.QUERY,
            description='Начало периода (YYYY-MM-DD). Обязательно при period=custom.',
        ),
        OpenApiParameter(
            name='date_to',
            type=OpenApiTypes.DATE,
            location=OpenApiParameter.QUERY,
            description='Конец периода (YYYY-MM-DD). Обязательно при period=custom.',
        ),
    ],
    responses={
        200: CompanyAnalyticsSerializer,
        400: OpenApiResponse(description='No company assigned or invalid period params'),
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

    period, date_from, date_to = _resolve_period_metadata(request.query_params)
    data = build_company_analytics_data(user, date_from=date_from, date_to=date_to)
    return Response(CompanyAnalyticsSerializer(data).data)


@extend_schema(
    tags=['Analytics'],
    summary='Export superadmin analytics as CSV or PDF',
    parameters=[
        OpenApiParameter(
            name='format',
            type=str,
            location=OpenApiParameter.QUERY,
            description='Export format: csv or pdf.',
            enum=['csv', 'pdf'],
            required=True,
        ),
        OpenApiParameter(
            name='period',
            type=str,
            location=OpenApiParameter.QUERY,
            description='Same as GET /analytics/superadmin/.',
            enum=['7d', '30d', '90d', 'custom'],
        ),
        OpenApiParameter(
            name='date_from',
            type=OpenApiTypes.DATE,
            location=OpenApiParameter.QUERY,
            description='Required when period=custom.',
        ),
        OpenApiParameter(
            name='date_to',
            type=OpenApiTypes.DATE,
            location=OpenApiParameter.QUERY,
            description='Required when period=custom.',
        ),
        OpenApiParameter(
            name='company_id',
            type=OpenApiTypes.INT,
            location=OpenApiParameter.QUERY,
            description='Optional company scope (same as dashboard).',
        ),
        OpenApiParameter(
            name='resource_type',
            type=str,
            location=OpenApiParameter.QUERY,
            description='Optional bookings_today filter (same as dashboard).',
            enum=[c[0] for c in Resource.TYPE_CHOICES],
        ),
    ],
    responses={
        200: OpenApiResponse(description='CSV (UTF-8 with BOM) or PDF, Content-Disposition: attachment'),
        400: OpenApiResponse(description='Validation error'),
        404: OpenApiResponse(description='Company not found'),
        401: OpenApiResponse(description='Not authenticated'),
        403: OpenApiResponse(description='Superadmin only'),
    },
)
class SuperadminExportView(_IgnoreDrfFormatQueryParamMixin, APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        fmt = request.query_params.get('format')
        if fmt not in ('csv', 'pdf'):
            raise ValidationError({'format': ['Invalid or missing format. Use format=csv or format=pdf.']})
        payload = build_superadmin_dashboard_payload(request)
        stem = f'analytics-superadmin-{payload["period"]}'
        if fmt == 'pdf':
            pdf_bytes = _build_superadmin_pdf(payload, get_lang(request))
            return _http_pdf_attachment(stem, pdf_bytes)
        return _http_csv_attachment(stem, _rows_superadmin_csv(payload, get_lang(request)))


@extend_schema(
    tags=['Analytics'],
    summary='Export company analytics as CSV or PDF',
    parameters=[
        OpenApiParameter(
            name='format',
            type=str,
            location=OpenApiParameter.QUERY,
            description='Export format: csv or pdf.',
            enum=['csv', 'pdf'],
            required=True,
        ),
        OpenApiParameter(
            name='period',
            type=str,
            location=OpenApiParameter.QUERY,
            description='Период: 7d, 30d, 90d или custom. По умолчанию: последние 30 дней.',
            enum=['7d', '30d', '90d', 'custom'],
        ),
        OpenApiParameter(
            name='date_from',
            type=OpenApiTypes.DATE,
            location=OpenApiParameter.QUERY,
            description='Начало периода (YYYY-MM-DD). Обязательно при period=custom.',
        ),
        OpenApiParameter(
            name='date_to',
            type=OpenApiTypes.DATE,
            location=OpenApiParameter.QUERY,
            description='Конец периода (YYYY-MM-DD). Обязательно при period=custom.',
        ),
    ],
    responses={
        200: OpenApiResponse(description='CSV (UTF-8 with BOM) or PDF, Content-Disposition: attachment'),
        400: OpenApiResponse(description='No company assigned or invalid format/period params'),
        401: OpenApiResponse(description='Not authenticated'),
        403: OpenApiResponse(description='Company admin only'),
    },
)
class CompanyExportView(_IgnoreDrfFormatQueryParamMixin, APIView):
    permission_classes = [IsCompanyAdmin]

    def get(self, request):
        fmt = request.query_params.get('format')
        if fmt not in ('csv', 'pdf'):
            raise ValidationError({'format': ['Invalid or missing format. Use format=csv or format=pdf.']})
        user = request.user
        if not user.company:
            return Response({'detail': 'No company'}, status=400)
        period, date_from, date_to = _resolve_period_metadata(request.query_params)
        data = build_company_analytics_data(user, date_from=date_from, date_to=date_to)
        safe_slug = ''.join(c if c.isalnum() else '-' for c in user.company.name.lower()) or 'company'
        stem = f'analytics-company-{safe_slug}-{period}'
        if fmt == 'pdf':
            pdf_bytes = _build_company_pdf(data, get_lang(request))
            return _http_pdf_attachment(stem, pdf_bytes)
        return _http_csv_attachment(stem, _rows_company_csv(data, get_lang(request)))


@extend_schema(
    tags=['Analytics'],
    summary='Per-resource usage stats (superadmin)',
    parameters=[
        OpenApiParameter(
            name='period',
            type=str,
            location=OpenApiParameter.QUERY,
            description='Reporting window: 7d, 30d, 90d, or custom.',
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
            name='resource_id',
            type=OpenApiTypes.INT,
            location=OpenApiParameter.QUERY,
            description='Optional: limit to a single resource.',
        ),
        OpenApiParameter(
            name='floor',
            type=OpenApiTypes.INT,
            location=OpenApiParameter.QUERY,
            description='Optional: filter resources by floor.',
        ),
        OpenApiParameter(
            name='company_id',
            type=OpenApiTypes.INT,
            location=OpenApiParameter.QUERY,
            description='Optional: only count bookings owned by this company.',
        ),
    ],
    responses={
        200: ResourceUsageSerializer,
        400: OpenApiResponse(description='Validation error'),
        404: OpenApiResponse(description='Company or resource not found'),
        401: OpenApiResponse(description='Not authenticated'),
        403: OpenApiResponse(description='Superadmin only'),
    },
)
@api_view(['GET'])
@permission_classes([IsSuperAdmin])
def resource_usage(request):
    qp = request.query_params
    period, date_from, date_to = _resolve_period_metadata(qp)
    period_start, _ = _local_day_bounds(date_from)
    _, period_end = _local_day_bounds(date_to)

    resource_id = _parse_optional_int('resource_id', qp.get('resource_id'))
    floor = _parse_optional_int('floor', qp.get('floor'))
    company_id = _parse_optional_int('company_id', qp.get('company_id'))

    if company_id is not None and not Company.objects.filter(pk=company_id).exists():
        raise NotFound()
    if resource_id is not None and not Resource.objects.filter(pk=resource_id).exists():
        raise NotFound()

    bookings_qs = Booking.objects.filter(
        status__in=('confirmed', 'completed'),
        start_time__gte=period_start,
        start_time__lte=period_end,
    )
    if resource_id is not None:
        bookings_qs = bookings_qs.filter(resource_id=resource_id)
    if floor is not None:
        bookings_qs = bookings_qs.filter(resource__floor=floor)
    if company_id is not None:
        bookings_qs = bookings_qs.filter(company_id=company_id)

    stats = (
        bookings_qs
        .values(
            'resource_id',
            'resource__name',
            'resource__resource_type',
            'resource__floor',
        )
        .annotate(
            total_bookings=Count('id'),
            avg_duration=Avg(F('end_time') - F('start_time')),
            total_duration=Sum(F('end_time') - F('start_time')),
        )
        .order_by('-total_bookings', 'resource_id')
    )

    tz = timezone.get_current_timezone()
    peak_by_resource = {}
    for res_id, start_time in bookings_qs.values_list('resource_id', 'start_time'):
        hour = timezone.localtime(start_time, tz).hour
        buckets = peak_by_resource.setdefault(res_id, {})
        buckets[hour] = buckets.get(hour, 0) + 1

    results = []
    for row in stats:
        avg_duration = row['avg_duration']
        total_duration = row['total_duration']
        avg_minutes = round(avg_duration.total_seconds() / 60, 2) if avg_duration else 0.0
        total_minutes = round(total_duration.total_seconds() / 60, 2) if total_duration else 0.0
        buckets = peak_by_resource.get(row['resource_id']) or {}
        if buckets:
            peak_hour, peak_count = max(buckets.items(), key=lambda kv: (kv[1], -kv[0]))
        else:
            peak_hour, peak_count = None, 0
        results.append({
            'resource_id': row['resource_id'],
            'resource_name': row['resource__name'],
            'resource_type': row['resource__resource_type'],
            'floor': row['resource__floor'],
            'total_bookings': row['total_bookings'],
            'avg_duration_minutes': avg_minutes,
            'total_booked_minutes': total_minutes,
            'peak_hour': peak_hour,
            'peak_hour_bookings': peak_count,
        })

    payload = {
        'period': period,
        'date_from': date_from,
        'date_to': date_to,
        'results': results,
    }
    return Response(ResourceUsageSerializer(instance=payload).data)
