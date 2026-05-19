import django_filters
from django.db.models import Exists, OuterRef
from django.utils.dateparse import parse_datetime

from .models import Resource, Booking, ResourceBlock
from .serializers import _EQUIPMENT_KEYS


class ResourceFilter(django_filters.FilterSet):
    """Каталог: type и resource_type — одно поле модели; equipment — ключи как в карточке переговорки."""

    type = django_filters.CharFilter(field_name='resource_type')
    resource_type = django_filters.CharFilter()
    floor = django_filters.NumberFilter()
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
    available_from = django_filters.IsoDateTimeFilter(method='filter_free_interval')
    available_to = django_filters.IsoDateTimeFilter(method='filter_free_interval')

    class Meta:
        model = Resource
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
        dt_from = raw_from if hasattr(raw_from, 'utcoffset') else parse_datetime(str(raw_from))
        dt_to = raw_to if hasattr(raw_to, 'utcoffset') else parse_datetime(str(raw_to))
        return dt_from, dt_to

    def filter_free_interval(self, queryset, name, value):
        dt_from, dt_to = self._parse_interval_datetimes()
        if dt_from is None or dt_to is None:
            return queryset
        if dt_from >= dt_to:
            return queryset.none()

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
        return queryset.exclude(Exists(booking_overlap)).exclude(Exists(block_overlap))


class BookingFilter(django_filters.FilterSet):
    status = django_filters.CharFilter()
    resource_type = django_filters.CharFilter(field_name='resource__resource_type')
    date_from = django_filters.DateTimeFilter(field_name='start_time', lookup_expr='gte')
    date_to = django_filters.DateTimeFilter(field_name='end_time', lookup_expr='lte')
    company_id = django_filters.NumberFilter(field_name='company_id')
    user_id = django_filters.NumberFilter(field_name='user_id')
    resource_id = django_filters.NumberFilter(field_name='resource_id')
    # Backward-compatible aliases for older clients.
    company = django_filters.NumberFilter(field_name='company_id')
    user = django_filters.NumberFilter(field_name='user_id')
    resource = django_filters.NumberFilter(field_name='resource_id')

    class Meta:
        model = Booking
        fields = [
            'status',
            'resource_type',
            'date_from',
            'date_to',
            'company_id',
            'user_id',
            'resource_id',
            'company',
            'user',
            'resource',
        ]
