import re
from datetime import timedelta

import django_filters
from django.db.models import Exists, OuterRef
from django.utils import timezone as tz_utils
from django.utils.dateparse import parse_datetime

from .models import Resource, Booking, ResourceBlock, BookingCancellationAudit
from .serializers import _EQUIPMENT_KEYS

# IsoDateTimeFilter skips calling the filter method entirely when the datetime
# string cannot be parsed (e.g. literal '+' decoded as space by QueryDict).
# Using CharFilter ensures the method is always called so we can do robust
# parsing with the space→'+' fixup ourselves.


class ResourceFilter(django_filters.FilterSet):
    """Каталог: type и resource_type — одно поле модели; equipment — ключи как в карточке переговорки."""

    type = django_filters.CharFilter(field_name='resource_type')
    resource_type = django_filters.CharFilter()
    floor = django_filters.NumberFilter()
    floor_id = django_filters.NumberFilter(field_name='floor_fk_id')
    capacity_min = django_filters.NumberFilter(field_name='capacity', lookup_expr='gte')
    capacity_max = django_filters.NumberFilter(field_name='capacity', lookup_expr='lte')
    equipment = django_filters.CharFilter(method='filter_equipment')
    has_projector = django_filters.BooleanFilter()
    has_tv = django_filters.BooleanFilter()
    has_video_conf = django_filters.BooleanFilter()
    is_active = django_filters.BooleanFilter()
    assigned_company = django_filters.NumberFilter(field_name='assigned_company_id')
    company_id = django_filters.NumberFilter(field_name='assigned_company_id')
    # Интервал свободности (datetime): без пересечений с подтверждёнными бронированиями и блокировками.
    # Имена параметров совпадают с полями модели по смыслу запроса, не с TimeField available_from.
    # CharFilter (not IsoDateTimeFilter) so the method is called even when the raw
    # string has a literal '+' decoded as space by Django's QueryDict — in that case
    # IsoDateTimeFilter silently skips the method call, bypassing the whole filter.
    available_from = django_filters.CharFilter(method='filter_free_interval')
    available_to = django_filters.CharFilter(method='filter_free_interval')

    class Meta:
        model = Resource
        # floor_id is declared explicitly above (field_name='floor_fk_id'), so it is
        # intentionally omitted here — listing a non-model-field name in Meta.fields
        # would cause django-filter to attempt auto-generation of a conflicting filter.
        fields = ['resource_type', 'floor', 'is_active', 'assigned_company', 'company_id']

    def filter_equipment(self, queryset, name, value):
        if value in (None, ''):
            return queryset
        raw = value if isinstance(value, (list, tuple)) else [value]
        tokens = []
        for part in raw:
            tokens.extend(str(part).replace(',', ' ').split())
        for token in tokens:
            key = token.strip().lower().replace('-', '_')
            field = _EQUIPMENT_KEYS.get(key)
            if field:
                queryset = queryset.filter(**{field: True})
        return queryset

    @staticmethod
    def _fix_tz_plus(raw):
        """
        QueryDict decodes a literal '+' in query strings as a space.
        ISO 8601 timezone offsets use '+', e.g. +05:00.  When the client
        sends '+' un-encoded (without %2B), Django sees ' 05:00' instead of
        '+05:00', and parse_datetime returns None.

        Heuristic: if the string ends with ' HH:MM' at the timezone position,
        restore it to '+HH:MM' so that parse_datetime can parse it correctly.
        """
        s = str(raw)
        # Replace the last occurrence of space followed by HH:MM at end of string
        # e.g. '2026-05-30T19:14:00 05:00' → '2026-05-30T19:14:00+05:00'
        return re.sub(r' (\d{2}:\d{2})$', r'+\1', s)

    def _parse_interval_datetimes(self):
        data = self.data
        raw_from = data.get('available_from') if data is not None else None
        raw_to = data.get('available_to') if data is not None else None
        if raw_from is None or raw_to is None or raw_from == '' or raw_to == '':
            return None, None
        if isinstance(raw_from, (list, tuple)):
            raw_from = raw_from[0]
        if isinstance(raw_to, (list, tuple)):
            raw_to = raw_to[0]
        if hasattr(raw_from, 'utcoffset'):
            dt_from = raw_from
        else:
            s = self._fix_tz_plus(raw_from)
            dt_from = parse_datetime(s)
        if hasattr(raw_to, 'utcoffset'):
            dt_to = raw_to
        else:
            s = self._fix_tz_plus(raw_to)
            dt_to = parse_datetime(s)
        return dt_from, dt_to

    def filter_free_interval(self, queryset, name, value):
        dt_from, dt_to = self._parse_interval_datetimes()
        if dt_from is None or dt_to is None:
            return queryset
        if dt_from >= dt_to:
            return queryset.none()

        # 1. Exclude by booking conflicts (existing logic).
        booking_overlap = Booking.objects.filter(
            resource_id=OuterRef('pk'),
            status='confirmed',
            start_time__lt=dt_to,
            end_time__gt=dt_from,
        )
        block_overlap = ResourceBlock.objects.filter(
            resource_id=OuterRef('pk'),
            start_time__lt=dt_to,
            end_time__gt=dt_from,
        )
        # Два вызова метода (по одному на параметр) — идемпотентный exclude(Exists(...)).
        queryset = queryset.exclude(Exists(booking_overlap)).exclude(Exists(block_overlap))

        # 2. Exclude by available_days.
        # Collect all weekdays (Mon=0, Sun=6) spanned by the requested range.
        requested_days = set()
        current = tz_utils.localtime(dt_from).date()
        end_date = tz_utils.localtime(dt_to).date()
        while current <= end_date:
            requested_days.add(current.weekday())
            current += timedelta(days=1)

        # available_days is a JSONField (Python list) — filter in Python.
        # An empty available_days list means "available every day" (no restriction).
        # Clear select_related to avoid conflict when combined with .only() deferred loading.
        available_ids = [
            r.id for r in queryset.select_related(None).only('id', 'available_days')
            if not r.available_days or requested_days.issubset(set(r.available_days))
        ]
        queryset = queryset.filter(id__in=available_ids)

        # 3. Exclude by available hours (single-day ranges only).
        # For multi-day ranges the hours check is skipped — a resource cannot
        # realistically be open 24 h, so we only enforce hours within one day.
        if tz_utils.localtime(dt_from).date() == tz_utils.localtime(dt_to).date():
            local_from = tz_utils.localtime(dt_from).time()
            local_to = tz_utils.localtime(dt_to).time()
            queryset = queryset.filter(
                available_from__lte=local_from,
                available_until__gte=local_to,
            )

        return queryset


class BookingCancellationAuditFilter(django_filters.FilterSet):
    booking = django_filters.NumberFilter(field_name='booking_id')
    cancelled_by = django_filters.NumberFilter(field_name='cancelled_by_id')
    cancelled_at_after = django_filters.DateTimeFilter(field_name='cancelled_at', lookup_expr='gte')
    cancelled_at_before = django_filters.DateTimeFilter(field_name='cancelled_at', lookup_expr='lte')

    class Meta:
        model = BookingCancellationAudit
        fields = ['booking', 'cancelled_by', 'cancelled_at_after', 'cancelled_at_before']


class _CharInFilter(django_filters.BaseInFilter, django_filters.CharFilter):
    pass


class BookingFilter(django_filters.FilterSet):
    status = django_filters.CharFilter()
    status_in = _CharInFilter(field_name='status', lookup_expr='in')
    resource_type = django_filters.CharFilter(field_name='resource__resource_type')
    date_from = django_filters.DateTimeFilter(field_name='start_time', lookup_expr='gte')
    date_to = django_filters.DateTimeFilter(field_name='end_time', lookup_expr='lte')
    company_id = django_filters.NumberFilter(field_name='company_id')
    user_id = django_filters.NumberFilter(field_name='user_id')
    resource_id = django_filters.NumberFilter(field_name='resource_id')
    recurring_booking_id = django_filters.NumberFilter(field_name='recurring_booking_id')
    # Backward-compatible aliases for older clients.
    company = django_filters.NumberFilter(field_name='company_id')
    user = django_filters.NumberFilter(field_name='user_id')
    resource = django_filters.NumberFilter(field_name='resource_id')

    class Meta:
        model = Booking
        fields = [
            'status',
            'status_in',
            'resource_type',
            'date_from',
            'date_to',
            'company_id',
            'user_id',
            'resource_id',
            'recurring_booking_id',
            'company',
            'user',
            'resource',
        ]
